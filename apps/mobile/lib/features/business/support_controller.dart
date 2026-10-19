import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'business_controller.dart';
import 'support_models.dart';
import 'support_repository.dart';

/// State of the Support section of Business (docs/business.md#support-tickets):
/// the policy and permissions, the summary counts and the paged, filtered
/// ticket list. A ticket's own screen talks to [repository] and calls
/// [refresh] after each change so the list stays current.
class SupportController extends ChangeNotifier {
  SupportController(this.repository) {
    tickets = PagedList(
      (offset) => repository.tickets(filter, offset: offset, limit: pageSize),
    );
  }

  final SupportRepository repository;
  static const pageSize = 20;

  BusinessLoad state = BusinessLoad.idle;
  String? error;
  SupportSetup? setup;
  SupportSummary? summary;
  List<SupportAssignee> assignees = const [];
  TicketFilter filter = const TicketFilter();
  late final PagedList<SupportTicket> tickets;

  bool get canWrite => setup?.canWrite ?? false;
  bool get canManage => setup?.canManage ?? false;
  String? get myId => assignees.where((item) => item.you).firstOrNull?.userId;

  static BusinessLoad stateOf(Object error) =>
      error is ApiException && error.isPermissionDenied
          ? BusinessLoad.denied
          : BusinessLoad.error;
  static String messageOf(Object error) =>
      error is ApiException ? error.readable : '$error';

  /// Setup, who can be assigned, the counts and the first page.
  Future<void> load() async {
    state = BusinessLoad.loading;
    error = null;
    notifyListeners();
    try {
      final results = await Future.wait<Object>([
        repository.setup(),
        repository.assignees(),
      ]);
      setup = results[0] as SupportSetup;
      assignees = results[1] as List<SupportAssignee>;
      state = BusinessLoad.ready;
    } catch (e) {
      state = stateOf(e);
      error = messageOf(e);
      notifyListeners();
      return;
    }
    notifyListeners();
    await Future.wait([loadSummary(), first()]);
  }

  Future<void> loadSummary() async {
    try {
      summary = await repository.summary();
    } catch (_) {
      // The counts are a convenience; the list carries its own error.
      summary = null;
    }
    notifyListeners();
  }

  Future<void> first() async {
    tickets.state = BusinessLoad.loading;
    tickets.error = null;
    notifyListeners();
    try {
      final page = await tickets.fetch(0);
      tickets.items
        ..clear()
        ..addAll(page.items);
      tickets.total = page.total;
      tickets.state = BusinessLoad.ready;
    } catch (e) {
      tickets.state = stateOf(e);
      tickets.error = messageOf(e);
    }
    notifyListeners();
  }

  Future<void> more() async {
    if (tickets.loadingMore || !tickets.hasMore) return;
    tickets.loadingMore = true;
    tickets.error = null;
    notifyListeners();
    try {
      final page = await tickets.fetch(tickets.items.length);
      tickets.items.addAll(page.items);
      tickets.total = page.total;
    } catch (e) {
      tickets.error = messageOf(e);
    } finally {
      tickets.loadingMore = false;
      notifyListeners();
    }
  }

  Future<void> applyFilter(TicketFilter next) async {
    filter = next;
    await first();
  }

  Future<void> search(String text) => applyFilter(filter.copyWith(
        query: () => text.trim().isEmpty ? null : text.trim(),
      ));

  /// After a change on a ticket: counts and list again, keeping the filters.
  Future<void> refresh() => Future.wait([loadSummary(), first()]);

  Future<TicketDetail> create(Map<String, Object?> fields) async {
    final detail = await repository.create(fields);
    await refresh();
    return detail;
  }

  /// Forget everything (sign-out or workspace switch).
  void reset() {
    setup = null;
    summary = null;
    assignees = const [];
    filter = const TicketFilter();
    tickets.items.clear();
    tickets.total = 0;
    tickets.state = BusinessLoad.idle;
    state = BusinessLoad.idle;
    notifyListeners();
  }
}
