import 'workspace_models.dart';

/// Server-side task list filters (`GET /api/v1/tasks`, docs/tasks.md).
class TaskListQuery {
  const TaskListQuery(
      {this.statuses = const {}, this.search = '', this.archived = false});
  final Set<WorkStatus> statuses;
  final String search;
  final bool archived;

  bool get isDefault => statuses.isEmpty && search.trim().isEmpty && !archived;

  /// The query string (no `?`) for the list (with `cursor`/`limit`) or the
  /// summary (`withStatus: false`).
  String queryString({String? cursor, int? limit, bool withStatus = true}) {
    final parts = <String>[
      if (limit != null) 'limit=$limit',
      if (cursor != null) 'cursor=${Uri.encodeQueryComponent(cursor)}',
      if (withStatus)
        for (final s in statuses) 'status=${workStatusWire(s)}',
      if (search.trim().isNotEmpty)
        'q=${Uri.encodeQueryComponent(_clip(search.trim()))}',
      if (archived) 'archived=true',
    ];
    return parts.join('&');
  }

  static String _clip(String value) =>
      value.length > 200 ? value.substring(0, 200) : value;

  @override
  bool operator ==(Object other) =>
      other is TaskListQuery &&
      other.search == search &&
      other.archived == archived &&
      other.statuses.length == statuses.length &&
      other.statuses.containsAll(statuses);

  @override
  int get hashCode =>
      Object.hash(search, archived, Object.hashAllUnordered(statuses));
}

String workStatusWire(WorkStatus status) => switch (status) {
      WorkStatus.created => 'created',
      WorkStatus.queued => 'queued',
      WorkStatus.running => 'running',
      WorkStatus.waitingApproval => 'waiting_approval',
      WorkStatus.completed => 'completed',
      WorkStatus.failed => 'failed',
      WorkStatus.cancelled => 'cancelled',
    };

/// `GET /api/v1/tasks/summary`: counts per status for the other filters.
class TaskSummaryCounts {
  const TaskSummaryCounts(
      {required this.total, required this.byStatus, required this.archived});
  final int total, archived;
  final Map<WorkStatus, int> byStatus;

  int count(Iterable<WorkStatus> statuses) =>
      statuses.fold(0, (sum, s) => sum + (byStatus[s] ?? 0));
}

const taskPageSize = 50;
const runPageSize = 20;

/// Task management beyond the workspace snapshot: filtered paging, the
/// summary, rename, archive, retry, the run history and the run-log export.
/// Implemented by the API repository; screens hide these features when the
/// repository does not offer them.
abstract interface class TaskManagementRepository {
  Future<List<WorkspaceTask>> listTasks(TaskListQuery query, {String? cursor});
  Future<TaskSummaryCounts> taskSummary(TaskListQuery query);
  Future<WorkspaceTask> renameTask(String taskId, String title);
  Future<WorkspaceTask> archiveTask(String taskId);
  Future<WorkspaceTask> unarchiveTask(String taskId);
  Future<WorkspaceTask> retryTask(String taskId);
  Future<List<WorkspaceRun>> listRuns(String taskId, {String? cursor});

  /// The task's run log (`GET /tasks/{id}/export?format=json`), as
  /// indented JSON text ready to copy or share.
  Future<String> exportRunLog(String taskId);
}

/// Which actions the task's state allows (the API still checks the role).
class TaskActionSet {
  const TaskActionSet(
      {required this.retry,
      required this.cancel,
      required this.rename,
      required this.archive,
      required this.unarchive});
  final bool retry, cancel, rename, archive, unarchive;

  factory TaskActionSet.of(WorkspaceTask task) {
    final archived = task.archivedAt != null;
    final terminal = {
      WorkStatus.completed,
      WorkStatus.failed,
      WorkStatus.cancelled
    }.contains(task.status);
    return TaskActionSet(
      retry: !archived &&
          (task.status == WorkStatus.failed ||
              task.status == WorkStatus.cancelled),
      cancel: !archived && !terminal,
      rename: !archived,
      archive: !archived && (terminal || task.status == WorkStatus.created),
      unarchive: archived,
    );
  }
}

/// "Run 3 (latest)": runs are listed newest first and numbered from the oldest.
String runLabel(int index, int total) =>
    index == 0 ? 'Run ${total - index} (latest)' : 'Run ${total - index}';
