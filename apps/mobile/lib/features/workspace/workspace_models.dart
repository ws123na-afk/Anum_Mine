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
  const WorkspaceAutomation({required this.id, required this.workflowId, required this.name, required this.status, required this.updatedAt, this.currentStep = 0, this.stepCount = 0});
  final String id, workflowId, name, status;
  final DateTime updatedAt;
  final int currentStep, stepCount;
}

class AutomationDefinition {
  const AutomationDefinition({required this.id, required this.name, required this.description, required this.status, required this.steps, required this.updatedAt});
  final String id, name, description, status;
  final List<String> steps;
  final DateTime updatedAt;
}

class AutomationSchedule {
  const AutomationSchedule({required this.id, required this.workflowId, required this.name, required this.cron, required this.timezone, required this.enabled});
  final String id, workflowId, name, cron, timezone;
  final bool enabled;
}

class WorkspaceSkill {
  const WorkspaceSkill({required this.id, required this.skillId, required this.name, required this.version, required this.description, required this.risk, required this.tools, required this.installed});
  final String id, skillId, name, version, description, risk;
  final List<String> tools;
  final bool installed;
}

class WorkspaceIntegration {
  const WorkspaceIntegration({required this.id, required this.name, required this.kind, required this.status, required this.endpoint, required this.detail, this.latencyMs});
  final String id, name, kind, status, endpoint, detail;
  final int? latencyMs;
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
  const WorkspaceSnapshot({required this.tasks, required this.approvals, required this.automations, required this.files, required this.memories, this.workflowDefinitions = const [], this.schedules = const [], this.skills = const [], this.integrations = const []});
  final List<WorkspaceTask> tasks;
  final List<WorkspaceApproval> approvals;
  final List<WorkspaceAutomation> automations;
  final List<WorkspaceFile> files;
  final List<WorkspaceMemory> memories;
  final List<AutomationDefinition> workflowDefinitions;
  final List<AutomationSchedule> schedules;
  final List<WorkspaceSkill> skills;
  final List<WorkspaceIntegration> integrations;
}
