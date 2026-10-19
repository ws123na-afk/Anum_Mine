import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'portal_models.dart';
import 'portal_repository.dart';

enum PortalLoad { idle, loading, ready, error }

/// One list of the portal: offers, change orders, invoices or requests.
class PortalSection<T> {
  PortalLoad state = PortalLoad.idle;
  List<T> items = <T>[];
  String? error;

  void clear() {
    items = <T>[];
    state = PortalLoad.idle;
    error = null;
  }
}

/// State of a customer's portal session (docs/business-portal.md). The link's
/// token lives only in [repository]; [leave] forgets it. A link the server
/// refuses (turned off, expired, unknown) puts the whole session in
/// [linkProblem] with the API's own sentence: nothing is retried, because a
/// refused link never starts working again.
class PortalController extends ChangeNotifier {
  PortalController(this.repository);
  final PortalRepository repository;

  PortalLoad state = PortalLoad.idle;
  PortalMe? me;
  String? error;

  /// The API's sentence when the link itself was refused (401), else null.
  String? linkProblem;
  bool left = false;

  final offers = PortalSection<PortalOfferSummary>();
  final changes = PortalSection<PortalChangeSummary>();
  final invoices = PortalSection<PortalInvoiceSummary>();
  final tickets = PortalSection<PortalTicketSummary>();

  bool get canDecide => me?.canDecide ?? false;

  static String messageOf(Object error) =>
      error is ApiException ? error.readable : '$error';

  /// Records a 401 as a refused link. Returns true when it was one.
  bool noteError(Object error) {
    if (error is ApiException && error.statusCode == 401) {
      linkProblem = error.message;
      notifyListeners();
      return true;
    }
    return false;
  }

  Future<void> open() async {
    state = PortalLoad.loading;
    error = null;
    notifyListeners();
    try {
      me = await repository.me();
      state = PortalLoad.ready;
    } catch (e) {
      state = PortalLoad.error;
      error = messageOf(e);
      noteError(e);
    }
    notifyListeners();
  }

  /// The counts again, quietly (after a decision or a new request).
  Future<void> refreshCounts() async {
    try {
      me = await repository.me();
    } catch (e) {
      noteError(e);
    }
    notifyListeners();
  }

  Future<void> _fill<T>(
      PortalSection<T> section, Future<List<T>> Function() fetch) async {
    section.state = PortalLoad.loading;
    section.error = null;
    notifyListeners();
    try {
      section.items = await fetch();
      section.state = PortalLoad.ready;
    } catch (e) {
      section.state = PortalLoad.error;
      section.error = messageOf(e);
      noteError(e);
    }
    notifyListeners();
  }

  Future<void> loadOffers() => _fill(offers, repository.offers);
  Future<void> loadChanges() => _fill(changes, repository.changeOrders);
  Future<void> loadInvoices() => _fill(invoices, repository.invoices);
  Future<void> loadTickets() => _fill(tickets, repository.tickets);

  /// Approves or declines the exact offer shown. Throws the API's refusal
  /// (409: it changed, 403: read-only link or paused business).
  Future<PortalOffer> decideOffer(PortalOffer offer, String decision,
      String signerName, String note) async {
    final next = await repository.decideOffer(offer.id,
        decision: decision,
        contentHash: offer.contentHash,
        signerName: signerName,
        note: note);
    await Future.wait([loadOffers(), refreshCounts()]);
    return next;
  }

  Future<PortalChange> decideChange(PortalChange change, String decision,
      String signerName, String note) async {
    final next = await repository.decideChangeOrder(change.id,
        decision: decision,
        contentHash: change.contentHash,
        signerName: signerName,
        note: note);
    await Future.wait([loadChanges(), refreshCounts()]);
    return next;
  }

  Future<PortalTicket> openTicket({
    required String title,
    required String description,
    required String severity,
    String? projectId,
  }) async {
    final next = await repository.openTicket(
        title: title,
        description: description,
        severity: severity,
        projectId: projectId);
    await Future.wait([loadTickets(), refreshCounts()]);
    return next;
  }

  /// Forget the token and everything read with it.
  void leave({bool notify = true}) {
    if (left) return;
    left = true;
    repository.close();
    me = null;
    offers.clear();
    changes.clear();
    invoices.clear();
    tickets.clear();
    if (notify) notifyListeners();
  }

  @override
  void dispose() {
    repository.close();
    super.dispose();
  }
}
