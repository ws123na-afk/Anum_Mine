import 'package:flutter/foundation.dart';
import 'workspace_models.dart';
import 'workspace_repository.dart';

class WorkspaceController extends ChangeNotifier {
  WorkspaceController(this.repository);
  final WorkspaceRepository repository;
  LoadPhase phase = LoadPhase.initial;
  WorkspaceSnapshot? snapshot;
  String? message;
  bool mutating = false;

  List<WorkspaceTask> get tasks => snapshot?.tasks ?? const [];
  List<WorkspaceApproval> get approvals => snapshot?.approvals ?? const [];
  List<WorkspaceApproval> get pendingApprovals => approvals.where((x) => x.status == 'pending').toList(growable: false);
  List<WorkspaceAutomation> get automations => snapshot?.automations ?? const [];
  List<WorkspaceFile> get files => snapshot?.files ?? const [];
  List<WorkspaceMemory> get memories => snapshot?.memories ?? const [];
  List<AutomationDefinition> get workflowDefinitions => snapshot?.workflowDefinitions ?? const [];
  List<AutomationSchedule> get schedules => snapshot?.schedules ?? const [];
  List<WorkspaceSkill> get skills => snapshot?.skills ?? const [];
  List<WorkspaceIntegration> get integrations => snapshot?.integrations ?? const [];

  Future<void> load() async {
    phase = LoadPhase.loading; message = null; notifyListeners();
    try { snapshot = await repository.loadWorkspace(); phase = _isEmpty(snapshot!) ? LoadPhase.empty : LoadPhase.ready; }
    on WorkspaceOfflineException catch (error) { phase = LoadPhase.offline; message = error.message; }
    catch (error) { phase = LoadPhase.error; message = error.toString(); }
    notifyListeners();
  }

  Future<WorkspaceTask?> createTask(String prompt) => _mutate(() => repository.createAndRunTask(prompt));
  Future<WorkspaceTask> loadTask(String id) => repository.loadTask(id);
  Future<WorkspaceTask?> cancelTask(String id) => _mutate(() => repository.cancelTask(id));
  Future<WorkspaceTask?> resumeTask(String id) => _mutate(() => repository.resumeTask(id));
  Future<void> decide(String id, {required bool approve}) async { await _mutate(() => repository.decideApproval(id, approve: approve)); }
  Future<void> startAutomation(String id) async { await _mutate(() => repository.startAutomation(id)); }
  Future<void> createAutomation(String name, String description, String action) async { await _mutate(() => repository.createAutomation(name: name, description: description, action: action)); }
  Future<void> createSchedule(String workflowId, String name, String cron, String timezone) async { await _mutate(() => repository.createSchedule(workflowId: workflowId, name: name, cron: cron, timezone: timezone)); }
  Future<void> transitionAutomation(String id, String action) async { await _mutate(() => repository.transitionAutomation(id, action)); }
  Future<void> uploadFile(String path) async { await _mutate(() => repository.uploadFile(path)); }
  Future<void> deleteFile(String id) async { await _mutate(() => repository.deleteFile(id)); }
  Future<void> addMemory(String taskId, String content) async { await _mutate(() => repository.createMemory(taskId: taskId, content: content)); }
  Future<void> deleteMemory(String id) async { await _mutate(() => repository.deleteMemory(id)); }
  Future<void> installSkill(WorkspaceSkill skill) async { await _mutate(() => repository.installSkill(skill)); }

  Future<T?> _mutate<T>(Future<T> Function() operation) async {
    mutating = true; message = null; notifyListeners();
    try { final result = await operation(); await load(); return result; }
    on WorkspaceOfflineException catch (error) { phase = LoadPhase.offline; message = error.message; }
    catch (error) { message = error.toString(); phase = LoadPhase.error; }
    finally { mutating = false; notifyListeners(); }
    return null;
  }

  bool _isEmpty(WorkspaceSnapshot value) => value.tasks.isEmpty && value.approvals.isEmpty && value.automations.isEmpty && value.files.isEmpty && value.memories.isEmpty;
}
