import 'workspace_models.dart';

/// Server-side task list filters (`GET /api/v1/tasks`, docs/tasks.md).
class TaskListQuery {
  const TaskListQuery(
      {this.statuses = const {},
      this.search = '',
      this.archived = false,
      this.mine = false});
  final Set<WorkStatus> statuses;
  final String search;
  final bool archived;

  /// Only tasks the signed-in person created (`created_by=me`).
  final bool mine;

  bool get isDefault =>
      statuses.isEmpty && search.trim().isEmpty && !archived && !mine;

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
      if (mine) 'created_by=me',
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
      other.mine == mine &&
      other.statuses.length == statuses.length &&
      other.statuses.containsAll(statuses);

  @override
  int get hashCode =>
      Object.hash(search, archived, mine, Object.hashAllUnordered(statuses));
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

  /// Creates a task with its title, priority and due date, and runs it at
  /// once when [TaskDraft.runNow] is set.
  Future<WorkspaceTask> createPlannedTask(TaskDraft draft);

  /// Changes the priority or due date (`PUT /tasks/{id}`); [clearDue]
  /// removes the due date.
  Future<WorkspaceTask> planTask(String taskId,
      {TaskPriority? priority, DateTime? dueAt, bool clearDue = false});
  Future<TaskSummaryCounts> taskSummary(TaskListQuery query);

  /// Starts a task that was created without running (`POST /tasks/{id}/run`).
  Future<WorkspaceTask> runTask(String taskId);
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

/// A new task as the create sheet collects it.
class TaskDraft {
  const TaskDraft(
      {required this.title,
      required this.prompt,
      this.priority = TaskPriority.medium,
      this.dueAt,
      this.runNow = true});
  final String title, prompt;
  final TaskPriority priority;

  /// Local time; sent as UTC.
  final DateTime? dueAt;
  final bool runNow;

  /// The title the API accepts: trimmed, or the start of the prompt.
  String get effectiveTitle {
    final t = title.trim();
    if (t.isNotEmpty) return t.length > 160 ? t.substring(0, 160) : t;
    final p = prompt.trim().replaceAll(RegExp(r'\s+'), ' ');
    return p.length > 80 ? '${p.substring(0, 77)}...' : p;
  }

  Map<String, Object?> body() => {
        'title': effectiveTitle,
        'prompt': prompt.trim(),
        'priority': taskPriorityWire(priority),
        if (dueAt != null) 'due_at': dueAt!.toUtc().toIso8601String(),
      };
}

String taskPriorityWire(TaskPriority priority) => switch (priority) {
      TaskPriority.low => 'low',
      TaskPriority.medium => 'medium',
      TaskPriority.high => 'high',
    };

/// The `PUT /tasks/{id}` body for a planning change.
Map<String, Object?> taskPlanBody(
        {TaskPriority? priority, DateTime? dueAt, bool clearDue = false}) =>
    {
      if (priority != null) 'priority': taskPriorityWire(priority),
      if (clearDue)
        'due_at': null
      else if (dueAt != null)
        'due_at': dueAt.toUtc().toIso8601String(),
    };

/// Which day a due date falls on, relative to [now]: drives the due label.
enum DueDay { overdue, today, tomorrow, later }

DueDay dueDay(DateTime due, {DateTime? now}) {
  final n = (now ?? DateTime.now()).toLocal();
  final d = due.toLocal();
  final today = DateTime(n.year, n.month, n.day);
  final day = DateTime(d.year, d.month, d.day);
  final diff = day.difference(today).inDays;
  if (d.isBefore(n) && diff <= 0) return DueDay.overdue;
  if (diff == 0) return DueDay.today;
  if (diff == 1) return DueDay.tomorrow;
  return DueDay.later;
}
