import 'workspace_models.dart';

abstract interface class WorkspaceRepository {
  Future<WorkspaceSnapshot> loadWorkspace();
  Future<WorkspaceTask> createAndRunTask(String prompt);
  Future<WorkspaceTask> loadTask(String taskId);
  Future<WorkspaceTask> cancelTask(String taskId);
  Future<WorkspaceTask> resumeTask(String taskId);
  Future<WorkspaceApproval> decideApproval(String approvalId, {required bool approve});
  Future<WorkspaceAutomation> startAutomation(String automationId);
  Future<WorkspaceAutomation> transitionAutomation(String runId, String action);
  Future<WorkspaceFile> uploadFile(String path);
  Future<void> downloadFile(WorkspaceFile file);
  Future<void> deleteFile(String fileId);
  Future<WorkspaceMemory> createMemory({required String taskId, required String content});
  Future<void> deleteMemory(String memoryId);
}

class WorkspaceOfflineException implements Exception {
  const WorkspaceOfflineException([this.message = 'The workspace is offline.']);
  final String message;
  @override String toString() => message;
}

