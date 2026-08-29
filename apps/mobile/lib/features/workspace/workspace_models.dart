enum LoadPhase { initial, loading, ready, empty, error, offline }
enum WorkStatus { created, queued, running, waitingApproval, completed, failed, cancelled }

class WorkspaceTask {
  const WorkspaceTask({required this.id, required this.title, required this.prompt, required this.status, required this.createdAt, required this.updatedAt, this.run});
  final String id, title, prompt;
  final WorkStatus status;
  final DateTime createdAt, updatedAt;
  final WorkspaceRun? run;
}

class WorkspaceRun {
  const WorkspaceRun({required this.id, required this.status, required this.steps, this.result});
  final String id;
  final WorkStatus status;
  final List<RunStep> steps;
  final String? result;
}

class RunStep {
  const RunStep({required this.id, required this.type, required this.summary, required this.createdAt});
  final String id, type, summary;
  final DateTime createdAt;
}

class WorkspaceApproval {
  const WorkspaceApproval({required this.id, required this.taskId, required this.action, required this.reason, required this.risk, required this.status, required this.createdAt});
  final String id, taskId, action, reason, risk, status;
  final DateTime createdAt;
}

class WorkspaceAutomation {
  const WorkspaceAutomation({required this.id, required this.name, required this.status, required this.updatedAt, this.currentStep = 0, this.stepCount = 0});
  final String id, name, status;
  final DateTime updatedAt;
  final int currentStep, stepCount;
}

class WorkspaceFile {
  const WorkspaceFile({required this.id, required this.name, required this.contentType, required this.sizeBytes, required this.createdAt});
  final String id, name, contentType;
  final int sizeBytes;
  final DateTime createdAt;
}

class WorkspaceMemory {
  const WorkspaceMemory({required this.id, required this.taskId, required this.content, required this.sourceType, required this.createdAt});
  final String id, taskId, content, sourceType;
  final DateTime createdAt;
}

class WorkspaceSnapshot {
  const WorkspaceSnapshot({required this.tasks, required this.approvals, required this.automations, required this.files, required this.memories});
  final List<WorkspaceTask> tasks;
  final List<WorkspaceApproval> approvals;
  final List<WorkspaceAutomation> automations;
  final List<WorkspaceFile> files;
  final List<WorkspaceMemory> memories;
}

