import 'workspace_models.dart';

abstract interface class WorkspaceRepository {
  Future<WorkspaceSnapshot> loadWorkspace();
  Future<WorkspaceTask> createAndRunTask(String prompt);
  Future<WorkspaceTask> loadTask(String taskId);
  Future<WorkspaceTask> cancelTask(String taskId);
  Future<WorkspaceTask> resumeTask(String taskId);
  Future<WorkspaceApproval> decideApproval(String approvalId, {required bool approve});
  Future<WorkspaceAutomation> startAutomation(String automationId);
  Future<AutomationDefinition> createAutomation({required String name, required String description, required String action});
  Future<AutomationSchedule> createSchedule({required String workflowId, required String name, required String cron, required String timezone});
  Future<WorkspaceAutomation> transitionAutomation(String runId, String action);
  Future<WorkspaceFile> uploadFile(String path);
  Future<void> downloadFile(WorkspaceFile file);
  Future<void> deleteFile(String fileId);
  Future<WorkspaceMemory> createMemory({required String taskId, required String content});
  Future<void> deleteMemory(String memoryId);
  Future<void> installSkill(WorkspaceSkill skill);
}

class WorkspaceOfflineException implements Exception {
  const WorkspaceOfflineException([this.message = 'The workspace is offline.']);
  final String message;
  @override String toString() => message;
}
