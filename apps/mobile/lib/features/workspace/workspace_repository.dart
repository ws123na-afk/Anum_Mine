import 'workspace_models.dart';

abstract interface class WorkspaceRepository {
  Future<WorkspaceSnapshot> loadWorkspace();
  Future<WorkspaceTask> createAndRunTask(String prompt);
  Future<WorkspaceTask> loadTask(String taskId);
  Future<WorkspaceTask> cancelTask(String taskId);
  Future<WorkspaceTask> resumeTask(String taskId);
  Future<WorkspaceApproval> decideApproval(WorkspaceApproval approval,
      {required bool approve, String? reason});
  // Automation changes go through AutomationRepository (features/automations).
  Future<WorkspaceFile> uploadFile(String path);
  Future<void> downloadFile(WorkspaceFile file);
  Future<void> deleteFile(String fileId);
  Future<WorkspaceMemory> createMemory(
      {required String taskId, required String content});
  Future<void> deleteMemory(String memoryId);

  /// Older memories, [limit] after the newest [offset] (oldest first).
  Future<List<WorkspaceMemory>> memoryPage(
      {required int offset, required int limit});

  /// A memory for the whole workspace, attached to no task; kept until
  /// someone deletes it, or [keepDays] days.
  Future<WorkspaceMemory> createWorkspaceMemory(String content,
      {int? keepDays});

  /// New text for a memory (its author, or an owner for a shared note).
  Future<WorkspaceMemory> updateMemory(String memoryId, String content);
  Future<void> installSkill(WorkspaceSkill skill);
  Future<void> setSkillEnabled(String skillId, bool enabled);
  Future<void> uninstallSkill(String skillId);
}

class WorkspaceOfflineException implements Exception {
  const WorkspaceOfflineException([this.message = 'The workspace is offline.']);
  final String message;
  @override
  String toString() => message;
}
