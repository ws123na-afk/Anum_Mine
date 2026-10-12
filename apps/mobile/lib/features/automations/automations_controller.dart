import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'automation_models.dart';
import 'automation_repository.dart';

enum AutomationLoad { loading, ready, error }

/// A sentence for a failed automation request: field problems of a 422, the
/// API's own refusal for 403/404/409, or how to recover from no connection.
String automationErrorText(Object error) {
  if (error is ApiException) {
    if (error.statusCode == 0) {
      return 'Could not reach ANUM. Check your connection and try again.';
    }
    if (error.statusCode == 401) return 'Your sign-in expired. Sign in again.';
    return error.readable;
  }
  return error.toString();
}

/// The Automations list (search, status filter, "Show more" paging) and the
/// caller's role. Detail screens call the repository through [run] so every
/// failure becomes a readable message.
class AutomationsController extends ChangeNotifier {
  AutomationsController(this.repository, {this.pageSize = 20});
  final AutomationRepository repository;
  final int pageSize;

  AutomationLoad phase = AutomationLoad.loading;
  String? error;
  int? errorStatus;
  List<AutomationWorkflow> workflows = const [];
  bool hasMore = false, loadingMore = false, started = false;
  String query = '', status = '';
  String? role, userId;
  Timer? _debounce;
  int _generation = 0;

  bool get canManage => canManageAutomations(role);
  bool canEdit(String? createdBy) => canEditAutomation(role, userId, createdBy);
  bool get filtered => query.trim().isNotEmpty || status.isNotEmpty;

  Future<void> start() async {
    if (started) return;
    started = true;
    unawaited(repository.viewer().then((v) {
      role = v.role;
      userId = v.userId;
      notifyListeners();
    }, onError: (_) {}));
    await refresh();
  }

  /// Every schedule of the workspace by workflow, for the list's schedule
  /// line ("Every day at 08:00"). Loaded with the list; a failure here only
  /// leaves the line out.
  Map<String, List<AutomationScheduleItem>> schedulesByWorkflow = const {};

  /// Workflows whose switch is being changed; their switch is disabled.
  final Set<String> toggling = {};

  /// The schedule line of [workflowId]: its first enabled schedule (or its
  /// first one), or null when it has none.
  AutomationScheduleItem? scheduleOf(String workflowId) {
    final items = schedulesByWorkflow[workflowId];
    if (items == null || items.isEmpty) return null;
    return items.firstWhere((s) => s.enabled, orElse: () => items.first);
  }

  Future<void> _loadSchedules() async {
    try {
      // No workflow id: every schedule the caller may read (up to 500).
      final items = await repository.schedules('');
      final grouped = <String, List<AutomationScheduleItem>>{};
      for (final item in items) {
        grouped.putIfAbsent(item.workflowId, () => []).add(item);
      }
      schedulesByWorkflow = grouped;
    } catch (_) {
      // The rows still show the next run time from the workflow itself.
    }
  }

  /// Pauses or resumes [workflow] (its schedules stop or start firing).
  /// Returns null on success or the message to show; the switch stays where
  /// the server left it.
  Future<String?> setEnabled(AutomationWorkflow workflow, bool enabled) async {
    if (toggling.contains(workflow.id)) return null;
    toggling.add(workflow.id);
    notifyListeners();
    try {
      final updated = await repository.setWorkflowEnabled(workflow.id, enabled);
      replace(updated);
      return null;
    } catch (e) {
      return automationErrorText(e);
    } finally {
      toggling.remove(workflow.id);
      notifyListeners();
    }
  }

  Future<void> refresh() async {
    final generation = ++_generation;
    if (workflows.isEmpty) phase = AutomationLoad.loading;
    notifyListeners();
    unawaited(_loadSchedules().then((_) {
      if (generation == _generation) notifyListeners();
    }));
    try {
      final page = await repository.workflows(
          query: query, status: status, limit: pageSize, offset: 0);
      if (generation != _generation) return;
      workflows = page.items;
      hasMore = page.hasMore;
      phase = AutomationLoad.ready;
      error = null;
    } catch (e) {
      if (generation != _generation) return;
      phase = AutomationLoad.error;
      error = automationErrorText(e);
      errorStatus = e is ApiException ? e.statusCode : null;
    }
    notifyListeners();
  }

  Future<void> loadMore() async {
    if (!hasMore || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.workflows(
          query: query,
          status: status,
          limit: pageSize,
          offset: workflows.length);
      workflows = [...workflows, ...page.items];
      hasMore = page.hasMore;
    } catch (e) {
      error = automationErrorText(e);
    }
    loadingMore = false;
    notifyListeners();
  }

  void search(String value) {
    query = value;
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), refresh);
  }

  void filter(String value) {
    status = value;
    refresh();
  }

  void clearFilters() {
    query = '';
    status = '';
    refresh();
  }

  /// Replace one workflow in the list after a change made elsewhere.
  void replace(AutomationWorkflow workflow) {
    workflows = [for (final w in workflows) w.id == workflow.id ? workflow : w];
    notifyListeners();
  }

  /// Runs a mutation; returns null on success or the message to show.
  Future<String?> run(Future<void> Function() action,
      {bool reload = true}) async {
    try {
      await action();
      if (reload) unawaited(refresh());
      return null;
    } catch (e) {
      return automationErrorText(e);
    }
  }

  static String idempotencyKey() {
    final random = Random.secure();
    return List.generate(
            16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
        .join();
  }

  /// Forget everything (sign-out): the next person loads their own list and role.
  void reset() {
    _debounce?.cancel();
    _generation++;
    started = false;
    phase = AutomationLoad.loading;
    workflows = const [];
    schedulesByWorkflow = const {};
    hasMore = false;
    query = '';
    status = '';
    role = null;
    userId = null;
    error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    super.dispose();
  }
}

/// Used when the workspace repository is not the API one (tests and previews):
/// the screen shows its error state with this message instead of dead lists.
class UnavailableAutomationRepository implements AutomationRepository {
  const UnavailableAutomationRepository();
  static const _error = ApiException(0, 'Automations are not available here.');
  @override
  Future<({String? userId, String? role})> viewer() async =>
      (userId: null, role: null);
  @override
  Future<AutomationPage<AutomationWorkflow>> workflows(
          {String query = '',
          String status = '',
          int limit = 20,
          int offset = 0}) =>
      Future.error(_error);
  @override
  Future<AutomationWorkflow> workflow(String id) => Future.error(_error);
  @override
  Future<AutomationWorkflow> createWorkflow(Map<String, Object?> body) =>
      Future.error(_error);
  @override
  Future<AutomationWorkflow> updateWorkflow(
          String id, Map<String, Object?> body) =>
      Future.error(_error);
  @override
  Future<AutomationWorkflow> setWorkflowEnabled(String id, bool enabled) =>
      Future.error(_error);
  @override
  Future<void> deleteWorkflow(String id) => Future.error(_error);
  @override
  Future<List<AutomationScheduleItem>> schedules(String workflowId) =>
      Future.error(_error);
  @override
  Future<SchedulePreview> preview(String cron, String timezone) =>
      Future.error(_error);
  @override
  Future<AutomationScheduleItem> createSchedule(
          {required String workflowId,
          required String name,
          required String cron,
          required String timezone}) =>
      Future.error(_error);
  @override
  Future<AutomationScheduleItem> updateSchedule(String id,
          {String? name, String? cron, String? timezone}) =>
      Future.error(_error);
  @override
  Future<AutomationScheduleItem> setScheduleEnabled(String id, bool enabled) =>
      Future.error(_error);
  @override
  Future<void> deleteSchedule(String id) => Future.error(_error);
  @override
  Future<AutomationPage<AutomationRunItem>> runs(String workflowId,
          {String status = '', int limit = 10, int offset = 0}) =>
      Future.error(_error);
  @override
  Future<AutomationRunItem> start(String workflowId, String idempotencyKey) =>
      Future.error(_error);
  @override
  Future<AutomationRunItem> transition(String runId, String action) =>
      Future.error(_error);
}
