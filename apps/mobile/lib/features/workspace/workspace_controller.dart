import 'package:flutter/foundation.dart';
import '../../data/api_client.dart';
import 'workspace_models.dart';
import 'workspace_repository.dart';

class WorkspaceController extends ChangeNotifier {
  WorkspaceController(this.repository);
  final WorkspaceRepository repository;
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
  List<WorkspaceMemory> get memories => snapshot?.memories ?? const [];
  List<AutomationDefinition> get workflowDefinitions =>
      snapshot?.workflowDefinitions ?? const [];
  List<AutomationSchedule> get schedules => snapshot?.schedules ?? const [];
  List<WorkspaceSkill> get skills => snapshot?.skills ?? const [];
  List<WorkspaceIntegration> get integrations =>
      snapshot?.integrations ?? const [];

  Future<void> load() async {
    phase = LoadPhase.loading;
    message = null;
    notifyListeners();
    try {
      snapshot = _withRunApprovals(await repository.loadWorkspace());
      phase = _isEmpty(snapshot!) ? LoadPhase.empty : LoadPhase.ready;
    } on WorkspaceOfflineException catch (error) {
      phase = LoadPhase.offline;
      message = error.message;
    } catch (error) {
      phase = LoadPhase.error;
      message = error.toString();
    }
    notifyListeners();
  }

  Future<WorkspaceTask?> createTask(String prompt) =>
      _mutate(() => _keepRunApproval(repository.createAndRunTask(prompt)));
  Future<WorkspaceTask> loadTask(String id) => repository.loadTask(id);
  Future<WorkspaceTask?> cancelTask(String id) =>
      _mutate(() => repository.cancelTask(id));
  Future<WorkspaceTask?> resumeTask(String id) =>
      _mutate(() => _keepRunApproval(repository.resumeTask(id)));

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

  Future<void> decide(WorkspaceApproval approval,
      {required bool approve, String? reason}) async {
    decisionRefusals.remove(approval.id);
    await _mutate(
        () => repository.decideApproval(approval,
            approve: approve, reason: reason),
        onRefused: (message) => decisionRefusals[approval.id] = message);
  }

  Future<void> startAutomation(String id) async {
    await _mutate(() => repository.startAutomation(id));
  }

  Future<void> createAutomation(
      String name, String description, String action) async {
    await _mutate(() => repository.createAutomation(
        name: name, description: description, action: action));
  }

  Future<void> createSchedule(
      String workflowId, String name, String cron, String timezone) async {
    await _mutate(() => repository.createSchedule(
        workflowId: workflowId, name: name, cron: cron, timezone: timezone));
  }

  Future<void> transitionAutomation(String id, String action) async {
    await _mutate(() => repository.transitionAutomation(id, action));
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

  Future<void> installSkill(WorkspaceSkill skill) async {
    await _mutate(() => repository.installSkill(skill));
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
        message = error.toString();
        phase = LoadPhase.error;
      }
    } on WorkspaceOfflineException catch (error) {
      phase = LoadPhase.offline;
      message = error.message;
    } catch (error) {
      message = error.toString();
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
