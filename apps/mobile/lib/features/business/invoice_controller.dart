import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'business_controller.dart';
import 'business_models.dart';
import 'invoice_models.dart';
import 'invoice_repository.dart';

/// State of the Invoices and Clients sections of Business
/// (docs/business.md#invoices): the per-currency summary, the paged, filtered
/// invoice list, the billing profile and the client accounts. An invoice's own
/// screen talks to [repository] and calls [refresh] after each change so the
/// list and the tiles stay current.
class InvoicesController extends ChangeNotifier {
  InvoicesController(this.repository) {
    invoices = PagedList(
      (offset) => repository.list(filter, offset: offset, limit: pageSize),
    );
    clients = PagedList(
      (offset) => repository.accounts(
          query: clientQuery, offset: offset, limit: pageSize),
    );
  }

  final InvoicesRepository repository;
  static const pageSize = 20;

  /// Who the caller is; set from the Business setup once it is loaded.
  BusinessPermissions permissions =
      const BusinessPermissions(write: false, manage: false, decide: false);

  BusinessLoad state = BusinessLoad.idle;
  String? error;
  InvoiceSummary? summary;
  InvoiceFilter filter = const InvoiceFilter();
  late final PagedList<InvoiceListItem> invoices;

  BillingProfile? profile;
  BusinessLoad profileState = BusinessLoad.idle;
  String? profileError;

  String? clientQuery;
  late final PagedList<ClientAccount> clients;

  bool get canWrite => permissions.write;
  bool get canManage => permissions.manage;

  static BusinessLoad stateOf(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? BusinessLoad.denied
          : BusinessLoad.error;
  static String messageOf(Object error) =>
      error is ApiException ? error.readable : '$error';

  /// The tiles and the first page.
  Future<void> load() async {
    state = BusinessLoad.loading;
    error = null;
    notifyListeners();
    await Future.wait([_loadSummary(), _firstPage()]);
    // The list decides the section's state; the tiles are a convenience
    // unless the whole section is refused.
    state = invoices.state == BusinessLoad.ready
        ? BusinessLoad.ready
        : invoices.state;
    error = invoices.error;
    notifyListeners();
  }

  Future<void> _loadSummary() async {
    try {
      summary = await repository.summary();
    } catch (_) {
      summary = null;
    }
  }

  Future<void> _firstPage() => _fill(invoices);

  Future<void> _fill<T>(PagedList<T> list) async {
    list.state = BusinessLoad.loading;
    list.error = null;
    try {
      final page = await list.fetch(0);
      list.items
        ..clear()
        ..addAll(page.items);
      list.total = page.total;
      list.state = BusinessLoad.ready;
    } catch (e) {
      list.state = stateOf(e);
      list.error = messageOf(e);
    }
  }

  Future<void> first() async {
    invoices.state = BusinessLoad.loading;
    notifyListeners();
    await _fill(invoices);
    state = invoices.state;
    error = invoices.error;
    notifyListeners();
  }

  Future<void> more() async {
    if (invoices.loadingMore || !invoices.hasMore) return;
    invoices.loadingMore = true;
    invoices.error = null;
    notifyListeners();
    try {
      final page = await invoices.fetch(invoices.items.length);
      invoices.items.addAll(page.items);
      invoices.total = page.total;
    } catch (e) {
      invoices.error = messageOf(e);
    } finally {
      invoices.loadingMore = false;
      notifyListeners();
    }
  }

  Future<void> applyFilter(InvoiceFilter next) async {
    filter = next;
    await first();
  }

  Future<void> search(String text) => applyFilter(filter.copyWith(
        query: () => text.trim().isEmpty ? null : text.trim(),
      ));

  /// After a change on an invoice: tiles and list again, keeping the filters.
  Future<void> refresh() async {
    await Future.wait([_loadSummary(), _firstPage()]);
    state = invoices.state;
    error = invoices.error;
    notifyListeners();
  }

  Future<void> loadProfile() async {
    profileState = BusinessLoad.loading;
    profileError = null;
    notifyListeners();
    try {
      profile = await repository.billingProfile();
      profileState = BusinessLoad.ready;
    } catch (e) {
      profileState = stateOf(e);
      profileError = messageOf(e);
    }
    notifyListeners();
  }

  Future<void> saveProfile(BillingProfile next) async {
    profile = await repository.saveBillingProfile(next);
    notifyListeners();
  }

  Future<void> firstClients() async {
    clients.state = BusinessLoad.loading;
    notifyListeners();
    await _fill(clients);
    notifyListeners();
  }

  Future<void> searchClients(String text) {
    clientQuery = text.trim().isEmpty ? null : text.trim();
    return firstClients();
  }

  Future<void> moreClients() async {
    if (clients.loadingMore || !clients.hasMore) return;
    clients.loadingMore = true;
    clients.error = null;
    notifyListeners();
    try {
      final page = await clients.fetch(clients.items.length);
      clients.items.addAll(page.items);
      clients.total = page.total;
    } catch (e) {
      clients.error = messageOf(e);
    } finally {
      clients.loadingMore = false;
      notifyListeners();
    }
  }

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    summary = null;
    profile = null;
    profileState = BusinessLoad.idle;
    filter = const InvoiceFilter();
    clientQuery = null;
    for (final list in [invoices, clients]) {
      list.items.clear();
      list.total = 0;
      list.state = BusinessLoad.idle;
    }
    state = BusinessLoad.idle;
    notifyListeners();
  }
}
