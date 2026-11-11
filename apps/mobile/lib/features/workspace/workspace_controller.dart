import '../../data/readable_error.dart';
import 'package:flutter/foundation.dart';
import '../../data/api_client.dart';
import '../files/file_versions_repository.dart';
import '../meetings/meetings_controller.dart';
import '../meetings/meetings_repository.dart';
import 'files_controller.dart';
import 'files_repository.dart';
import 'task_management.dart';
import 'workspace_models.dart';
import 'workspace_repository.dart';

class WorkspaceController extends ChangeNotifier {
  WorkspaceController(this.repository,
      {FilesRepository? files,
      FileVersionsRepository? fileVersions,
      MeetingsRepository? meetings}) {
    if (files != null) {
      filesController =
          FilesController(files, onChanged: load, versions: fileVersions);
    }
    if (meetings != null) meetingsController = MeetingsController(meetings);
  }

  /// Calls & Meetings (docs/meetings.md#mobile). Null when the app wires no
  /// meetings API.
  MeetingsController? meetingsController;
  final WorkspaceRepository repository;

  /// The Files tab (docs/files.md#mobile): paged list, uploads with progress,
  /// previews and per-role actions. Null when the app wires no file API.
  FilesController? filesController;
  LoadPhase phase = LoadPhase.initial;
  WorkspaceSnapshot? snapshot;
  String? message;
  bool mutating = false;

  /// Set when a run is refused with 402: the API's sentence names the reset
  /// date. Not an error state: the workspace stays usable.
  String? budgetMessage;

  void dismissBudget() {
    budgetMessage = null;
    notifyListeners();
  }

  List<WorkspaceTask> get tasks => snapshot?.tasks ?? const [];
  List<WorkspaceApproval> get approvals => snapshot?.approvals ?? const [];
  List<WorkspaceApproval> get pendingApprovals => approvals
      .where((x) => x.effectiveStatus() == 'pending')
      .toList(growable: false);
  List<WorkspaceAutomation> get automations =>
      snapshot?.automations ?? const [];
  List<WorkspaceFile> get files => snapshot?.files ?? const [];

  /// The loaded memories, newest page first then the older pages, with
  /// edits made here applied.
  List<WorkspaceMemory> get memories {
    final seen = <String>{};
    return [
      for (final m in [...?snapshot?.memories, ..._olderMemories])
        if (seen.add(m.id)) _editedMemories[m.id] ?? m
    ];
  }

  final List<WorkspaceMemory> _olderMemories = [];
  final Map<String, WorkspaceMemory> _editedMemories = {};

  /// Older memories wait beyond the loaded ones ("Load older memories").
  bool get hasOlderMemories => _olderMemories.isEmpty
      ? (snapshot?.memories.length ?? 0) >= memoryFirstPage
      : _moreOlder;
  bool _moreOlder = false;
  bool loadingOlderMemories = false;
  String? olderMemoriesError;

  /// The signed-in person's roles, so the Memory tab offers an owner the
  /// edit of a shared note (set by the app with [currentUserId]).
  List<String> currentRoles = const [];
  bool get isOwner => currentRoles.contains('owner');

  /// Loads the next older page (docs/memory.md#workspace-memories). One
  /// extra memory is asked for to know whether another page follows.
  Future<void> loadOlderMemories() async {
    if (loadingOlderMemories || !hasOlderMemories) return;
    loadingOlderMemories = true;
    olderMemoriesError = null;
    notifyListeners();
    final epoch = _epoch;
    try {
      final offset = (snapshot?.memories.length ?? 0) + _olderMemories.length;
      final page = await repository.memoryPage(
          offset: offset, limit: memoryOlderPage + 1);
      if (epoch != _epoch) return;
      // Oldest first: the first of an over-full page belongs to the next one.
      final more = page.length > memoryOlderPage;
      final kept = more ? page.sublist(1) : page;
      _olderMemories.addAll(kept.reversed);
      _moreOlder = more;
    } on Object catch (error) {
      if (epoch != _epoch) return;
      olderMemoriesError = readableError(error);
    } finally {
      if (epoch == _epoch) {
        loadingOlderMemories = false;
        notifyListeners();
      }
    }
  }

  /// Adds a memory for the whole workspace; null when saved, else the API's
  /// sentence (memory governance may refuse its source type).
  Future<String?> addWorkspaceMemory(String content, {int? keepDays}) async {
    try {
      await repository.createWorkspaceMemory(content, keepDays: keepDays);
      await load();
      return null;
    } on Object catch (error) {
      return readableError(error);
    }
  }

  /// Changes a memory's text; null when saved, else why not.
  Future<String?> editMemory(WorkspaceMemory memory, String content) async {
    try {
      final updated = await repository.updateMemory(memory.id, content);
      _editedMemories[memory.id] = updated;
      notifyListeners();
      return null;
    } on ApiException catch (error) {
      return error.isPermissionDenied ? 'denied' : error.readable;
    } on Object catch (error) {
      return readableError(error);
    }
  }

  List<AutomationDefinition> get workflowDefinitions =>
      snapshot?.workflowDefinitions ?? const [];
  List<AutomationSchedule> get schedules => snapshot?.schedules ?? const [];
  List<WorkspaceSkill> get skills => snapshot?.skills ?? const [];
  List<WorkspaceIntegration> get integrations =>
      snapshot?.integrations ?? const [];

  /// Changes on [reset], so an answer that was on its way for the previous
  /// person is dropped instead of shown to the next one.
  int _epoch = 0;

  Future<void> load() async {
    final epoch = _epoch;
    phase = LoadPhase.loading;
    message = null;
    notifyListeners();
    try {
      final loaded = await repository.loadWorkspace();
      if (epoch != _epoch) return;
      snapshot = _withRunApprovals(loaded);
      // A fresh first page: older pages loaded before would be offset wrongly.
      _olderMemories.clear();
      _editedMemories.clear();
      _moreOlder = false;
      olderMemoriesError = null;
      phase = _isEmpty(snapshot!) ? LoadPhase.empty : LoadPhase.ready;
    } on WorkspaceOfflineException catch (error) {
      if (epoch != _epoch) return;
      phase = LoadPhase.offline;
      message = error.message;
    } catch (error) {
      if (epoch != _epoch) return;
      phase = LoadPhase.error;
      message = readableError(error);
    }
    notifyListeners();
  }

  /// Forgets everything (sign-out or an ended session): the next person to
  /// sign in loads their own workspace and never sees this one.
  void reset() {
    _epoch++;
    phase = LoadPhase.initial;
    snapshot = null;
    message = null;
    mutating = false;
    budgetMessage = null;
    taskRefusal = null;
    skillNotice = null;
    currentUserId = null;
    currentRoles = const [];
    _olderMemories.clear();
    _editedMemories.clear();
    _moreOlder = false;
    olderMemoriesError = null;
    _runApprovals.clear();
    decisionRefusals.clear();
    filesController?.reset();
    meetingsController?.reset();
    notifyListeners();
  }

  Future<WorkspaceTask?> createTask(String prompt) =>
      _mutate(() => _keepRunApproval(repository.createAndRunTask(prompt)));
  Future<WorkspaceTask> loadTask(String id) => repository.loadTask(id);
  Future<WorkspaceTask?> cancelTask(String id) {
    taskRefusal = null;
    return _mutate(() => repository.cancelTask(id),
        onRefused: (message) => taskRefusal = message);
  }

  Future<WorkspaceTask?> resumeTask(String id) {
    taskRefusal = null;
    return _mutate(() => _keepRunApproval(repository.resumeTask(id)),
        onRefused: (message) => taskRefusal = message);
  }

  /// Filtered paging, retry, rename, archive, run history and export, when
  /// the repository offers them (the API repository does; docs/tasks.md).
  TaskManagementRepository? get taskManagement {
    final value = repository;
    return value is TaskManagementRepository
        ? value as TaskManagementRepository
        : null;
  }

  /// The API's refusal of the last task action (a 403 names who may act, a
  /// 409 why the state does not allow it); shown on the task's screen.
  String? taskRefusal;

  /// Creates a task with a priority and due date (and runs it when asked).
  /// Without task management only the prompt can be sent, and it runs.
  Future<WorkspaceTask?> createPlannedTask(TaskDraft draft) {
    final m = taskManagement;
    if (m == null) return createTask(draft.prompt.trim());
    taskRefusal = null;
    return _mutate(() => _keepRunApproval(m.createPlannedTask(draft)),
        onRefused: (message) => taskRefusal = message);
  }

  /// Changes a task's priority or due date (creator or owner).
  Future<WorkspaceTask?> planTask(String id,
          {TaskPriority? priority, DateTime? dueAt, bool clearDue = false}) =>
      _taskAction((m) =>
          m.planTask(id, priority: priority, dueAt: dueAt, clearDue: clearDue));

  Future<WorkspaceTask?> runTask(String id) =>
      _taskAction((m) => _keepRunApproval(m.runTask(id)));
  Future<WorkspaceTask?> retryTask(String id) =>
      _taskAction((m) => _keepRunApproval(m.retryTask(id)));
  Future<WorkspaceTask?> renameTask(String id, String title) =>
      _taskAction((m) => m.renameTask(id, title));
  Future<WorkspaceTask?> archiveTask(String id) =>
      _taskAction((m) => m.archiveTask(id));
  Future<WorkspaceTask?> unarchiveTask(String id) =>
      _taskAction((m) => m.unarchiveTask(id));

  Future<WorkspaceTask?> _taskAction(
      Future<WorkspaceTask> Function(TaskManagementRepository m) action) {
    final m = taskManagement;
    if (m == null) return Future.value(null);
    taskRefusal = null;
    return _mutate(() => action(m),
        onRefused: (message) => taskRefusal = message);
  }

  /// Approvals a run response created that the approvals list has not
  /// returned yet, by id.
  final Map<String, WorkspaceApproval> _runApprovals = {};

  /// Shows the approval a run just created at once, with the progress the
  /// run response carries (`required_approvals`, `approvers`), before the
  /// approvals list reloads. Once the list returns that approval its entry
  /// wins: the list always reports chain progress (docs/approvals-and-risk.md).
  Future<WorkspaceTask> _keepRunApproval(Future<WorkspaceTask> run) async {
    final task = await run;
    final approval = task.approval;
    if (approval != null) {
      _runApprovals[approval.id] = approval;
      final current = snapshot;
      if (current != null) {
        snapshot = _withRunApprovals(current);
        notifyListeners();
      }
    }
    return task;
  }

  WorkspaceSnapshot _withRunApprovals(WorkspaceSnapshot value) {
    final listed = {for (final approval in value.approvals) approval.id};
    _runApprovals.removeWhere((id, _) => listed.contains(id));
    if (_runApprovals.isEmpty) return value;
    return WorkspaceSnapshot(
      tasks: value.tasks,
      approvals: [..._runApprovals.values, ...value.approvals],
      automations: value.automations,
      files: value.files,
      memories: value.memories,
      workflowDefinitions: value.workflowDefinitions,
      schedules: value.schedules,
      skills: value.skills,
      integrations: value.integrations,
    );
  }

  /// The pending approval of [task]: the listed one, else the one its run
  /// response carried.
  WorkspaceApproval? pendingApprovalFor(WorkspaceTask task) {
    for (final approval in pendingApprovals) {
      if (approval.taskId == task.id) return approval;
    }
    final fromRun = task.approval;
    return fromRun != null && fromRun.effectiveStatus() == 'pending'
        ? fromRun
        : null;
  }

  /// The API's refusal of a decision, by approval id: a 403 such as the
  /// two-person rule refusing to let you approve your own high-risk task, or a
  /// 409 such as a second approval by the same person in an approval chain.
  /// Shown on that approval's card; the screen stays usable.
  final Map<String, String> decisionRefusals = {};

  /// The signed-in user, so an approval chain they already approved offers no
  /// second approval (set by the app once signed in; null until then).
  String? currentUserId;

  /// Called after the person decided an approval; the app offers phone push
  /// after the first one (docs/notifications.md).
  VoidCallback? onApprovalDecided;

  Future<void> decide(WorkspaceApproval approval,
      {required bool approve, String? reason}) async {
    decisionRefusals.remove(approval.id);
    var decided = false;
    await _mutate(() async {
      final result = await repository.decideApproval(approval,
          approve: approve, reason: reason);
      decided = true;
      return result;
    }, onRefused: (message) => decisionRefusals[approval.id] = message);
    if (decided) onApprovalDecided?.call();
  }

  Future<void> uploadFile(String path) async {
    await _mutate(() => repository.uploadFile(path));
  }

  Future<void> deleteFile(String id) async {
    await _mutate(() => repository.deleteFile(id));
  }

  Future<void> addMemory(String taskId, String content) async {
    await _mutate(
        () => repository.createMemory(taskId: taskId, content: content));
  }

  Future<void> deleteMemory(String id) async {
    await _mutate(() => repository.deleteMemory(id));
  }

  /// Why the last skill change was refused (403/409), in words that say who can.
  String? skillNotice;

  static const skillOwnersOnly =
      'Only workspace owners can install, enable, disable or uninstall skills. Ask an owner of this workspace.';

  void _skillRefused(String message) =>
      skillNotice = 'Not allowed: $message $skillOwnersOnly';

  Future<void> installSkill(WorkspaceSkill skill) async {
    skillNotice = null;
    await _mutate(() => repository.installSkill(skill),
        onRefused: _skillRefused);
  }

  Future<void> setSkillEnabled(WorkspaceSkill skill, bool enabled) async {
    skillNotice = null;
    await _mutate(() => repository.setSkillEnabled(skill.skillId, enabled),
        onRefused: _skillRefused);
  }

  Future<void> uninstallSkill(WorkspaceSkill skill) async {
    skillNotice = null;
    await _mutate(() => repository.uninstallSkill(skill.skillId),
        onRefused: _skillRefused);
  }

  Future<T?> _mutate<T>(Future<T> Function() operation,
      {void Function(String message)? onRefused}) async {
    mutating = true;
    message = null;
    budgetMessage = null;
    notifyListeners();
    try {
      final result = await operation();
      await load();
      return result;
    } on ApiException catch (error) {
      if (onRefused != null &&
          (error.isPermissionDenied || error.statusCode == 409)) {
        onRefused(error.message);
        await load();
      } else if (error.isBudgetExceeded) {
        budgetMessage = error.message;
        // The task was created before its run was refused: show it.
        await load();
      } else {
        message = error.readable;
        phase = LoadPhase.error;
      }
    } on WorkspaceOfflineException catch (error) {
      phase = LoadPhase.offline;
      message = error.message;
    } catch (error) {
      message = readableError(error);
      phase = LoadPhase.error;
    } finally {
      mutating = false;
      notifyListeners();
    }
    return null;
  }

  bool _isEmpty(WorkspaceSnapshot value) =>
      value.tasks.isEmpty &&
      value.approvals.isEmpty &&
      value.automations.isEmpty &&
      value.files.isEmpty &&
      value.memories.isEmpty;
}
