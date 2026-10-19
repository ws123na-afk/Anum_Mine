import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'workspace_models.dart';

/// Paged approvals (docs/approvals-and-risk.md#paging-filters-and-search): the
/// filter, one keyset page of the list, the counts, and the controller the
/// Approvals screen listens to.
enum ApprovalFilter {
  all('all'),
  pending('pending'),
  decided('decided');

  const ApprovalFilter(this.wire);

  /// The value of the `status` query parameter.
  final String wire;
}

const approvalsPageSize = 25;
const approvalsQueryMaxChars = 200;

class ApprovalsPage {
  const ApprovalsPage({required this.items, this.nextCursor});
  final List<WorkspaceApproval> items;
  final String? nextCursor;
}

/// How many approvals are in each state now (expiry applied by the API).
class ApprovalSummary {
  const ApprovalSummary(
      {this.pending = 0,
      this.approved = 0,
      this.rejected = 0,
      this.expired = 0});
  final int pending, approved, rejected, expired;
  int get decided => approved + rejected + expired;
  int get total => pending + decided;

  /// How many approvals the [filter] covers, for "N of M shown".
  int totalFor(ApprovalFilter filter) => switch (filter) {
        ApprovalFilter.pending => pending,
        ApprovalFilter.decided => decided,
        ApprovalFilter.all => total,
      };
}

/// A source of approval pages: the API, or the loaded snapshot.
abstract interface class ApprovalsPagingRepository {
  Future<ApprovalsPage> loadApprovalsPage(
      {required ApprovalFilter filter,
      String query = '',
      String? cursor,
      int limit = approvalsPageSize});
  Future<ApprovalSummary> loadApprovalSummary();
}

/// The search text as sent: trimmed and capped at what the API accepts.
String approvalSearchText(String value) {
  final text = value.trim();
  return text.length > approvalsQueryMaxChars
      ? text.substring(0, approvalsQueryMaxChars)
      : text;
}

/// Pending approvals first, then newest first (the API's order).
int compareApprovals(WorkspaceApproval a, WorkspaceApproval b,
    {DateTime? now}) {
  final pendingA = a.effectiveStatus(now) == 'pending';
  final pendingB = b.effectiveStatus(now) == 'pending';
  if (pendingA != pendingB) return pendingA ? -1 : 1;
  final byDate = b.createdAt.compareTo(a.createdAt);
  return byDate != 0 ? byDate : b.id.compareTo(a.id);
}

/// Pages over approvals already in memory, with the same order, filter and
/// search as the API. Used when the repository has no paging route.
class LocalApprovalsPaging implements ApprovalsPagingRepository {
  const LocalApprovalsPaging(this.source);
  final List<WorkspaceApproval> Function() source;

  @override
  Future<ApprovalsPage> loadApprovalsPage(
      {required ApprovalFilter filter,
      String query = '',
      String? cursor,
      int limit = approvalsPageSize}) async {
    final needle = query.trim().toLowerCase();
    final rows = source().where((a) {
      final pending = a.effectiveStatus() == 'pending';
      if (filter == ApprovalFilter.pending && !pending) return false;
      if (filter == ApprovalFilter.decided && pending) return false;
      if (needle.isEmpty) return true;
      return [a.id, a.action, a.reason, a.target ?? '']
          .any((field) => field.toLowerCase().contains(needle));
    }).toList()
      ..sort(compareApprovals);
    final start = cursor == null ? 0 : int.tryParse(cursor) ?? 0;
    final end = start + limit;
    return ApprovalsPage(
        items: rows.skip(start).take(limit).toList(),
        nextCursor: end < rows.length ? '$end' : null);
  }

  @override
  Future<ApprovalSummary> loadApprovalSummary() async {
    var pending = 0, approved = 0, rejected = 0, expired = 0;
    for (final a in source()) {
      switch (a.effectiveStatus()) {
        case 'pending':
          pending++;
        case 'approved':
          approved++;
        case 'rejected':
          rejected++;
        default:
          expired++;
      }
    }
    return ApprovalSummary(
        pending: pending,
        approved: approved,
        rejected: rejected,
        expired: expired);
  }
}

enum ApprovalsPhase { loading, ready, denied, error }

/// The state behind the Approvals screen: the loaded pages for the current
/// filter and search, the counts, and paging. A stale answer (the filter
/// changed while it was in flight) is dropped.
class ApprovalsListController extends ChangeNotifier {
  ApprovalsListController(this.repository);
  final ApprovalsPagingRepository repository;

  ApprovalsPhase phase = ApprovalsPhase.loading;
  ApprovalFilter filter = ApprovalFilter.all;
  String query = '';
  List<WorkspaceApproval> items = const [];
  String? nextCursor;
  ApprovalSummary? summary;
  String? error, pagingError;
  bool paging = false;
  int _generation = 0;
  bool _disposed = false;

  bool get hasMore => nextCursor != null;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  /// Loads the first page. [quiet] keeps the list on screen while it reloads
  /// and ignores a failure (a background refresh after an event or decision).
  Future<void> load({bool quiet = false}) async {
    final mine = ++_generation;
    if (!quiet) {
      phase = ApprovalsPhase.loading;
      error = null;
      _notify();
    }
    try {
      final results = await Future.wait<Object?>([
        repository.loadApprovalsPage(filter: filter, query: query),
        repository
            .loadApprovalSummary()
            .then<ApprovalSummary?>((s) => s, onError: (Object _) => null),
      ]);
      if (mine != _generation) return;
      final page = results[0]! as ApprovalsPage;
      items = page.items;
      nextCursor = page.nextCursor;
      summary = results[1] as ApprovalSummary?;
      pagingError = null;
      phase = ApprovalsPhase.ready;
    } on ApiException catch (e) {
      if (mine != _generation) return;
      if (quiet && phase == ApprovalsPhase.ready) return;
      phase =
          e.isPermissionDenied ? ApprovalsPhase.denied : ApprovalsPhase.error;
      error = e.message;
    } catch (e) {
      if (mine != _generation) return;
      if (quiet && phase == ApprovalsPhase.ready) return;
      phase = ApprovalsPhase.error;
      error = '$e';
    }
    _notify();
  }

  Future<void> setFilter(ApprovalFilter value) {
    if (value == filter) return Future.value();
    filter = value;
    return load();
  }

  Future<void> setQuery(String value) {
    final text = approvalSearchText(value);
    if (text == query) return Future.value();
    query = text;
    return load();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || paging) return;
    final mine = _generation;
    paging = true;
    pagingError = null;
    _notify();
    try {
      final page = await repository.loadApprovalsPage(
          filter: filter, query: query, cursor: cursor);
      if (mine != _generation) return;
      final seen = items.map((a) => a.id).toSet();
      items = [...items, ...page.items.where((a) => !seen.contains(a.id))];
      nextCursor = page.nextCursor;
    } on ApiException catch (e) {
      if (mine == _generation) pagingError = e.message;
    } catch (e) {
      if (mine == _generation) pagingError = '$e';
    } finally {
      paging = false;
    }
    _notify();
  }
}
