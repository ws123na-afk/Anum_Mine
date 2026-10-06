enum LoadPhase { initial, loading, ready, empty, error, offline }

enum WorkStatus {
  created,
  queued,
  running,
  waitingApproval,
  completed,
  failed,
  cancelled
}

class WorkspaceTask {
  const WorkspaceTask(
      {required this.id,
      required this.title,
      required this.prompt,
      required this.status,
      required this.createdAt,
      required this.updatedAt,
      this.run});
  final String id, title, prompt;
  final WorkStatus status;
  final DateTime createdAt, updatedAt;
  final WorkspaceRun? run;
}

class WorkspaceRun {
  const WorkspaceRun(
      {required this.id,
      required this.status,
      required this.steps,
      this.result});
  final String id;
  final WorkStatus status;
  final List<RunStep> steps;
  final String? result;
}

class RunStep {
  const RunStep(
      {required this.id,
      required this.type,
      required this.summary,
      required this.createdAt,
      this.metadata = const {}});
  final String id, type, summary;
  final DateTime createdAt;

  /// Step details from the API (ids, sources, policy outcome); never
  /// retrieved text. Empty when the API sent none.
  final Map<String, Object?> metadata;
}

class WorkspaceApproval {
  const WorkspaceApproval(
      {required this.id,
      required this.taskId,
      required this.action,
      required this.reason,
      required this.risk,
      required this.status,
      required this.createdAt,
      this.arguments = const {},
      this.payloadHash,
      this.expiresAt,
      this.decidedAt,
      this.decidedBy,
      this.decisionReason,
      this.requestedBy,
      this.target,
      this.requiredApprovals = 1,
      this.approvers = const []});

  /// [action] is the exact tool the agent will call and [arguments] the exact
  /// arguments it will send (secret-looking values arrive as `[REDACTED]`).
  final String id, taskId, action, reason, risk, status;
  final DateTime createdAt;
  final Map<String, Object?> arguments;

  /// SHA-256 of the canonical tool call. Approving sends back this value, so
  /// the agent executes only what was displayed (docs/approvals-and-risk.md).
  final String? payloadHash;
  final DateTime? expiresAt, decidedAt;

  /// User id of whoever approved or rejected, and the optional reason given.
  final String? decidedBy, decisionReason;

  /// User whose run proposed the call; under the workspace two-person rule
  /// they cannot approve it themselves.
  final String? requestedBy;

  /// Configured integration host the tool will contact (host only), or null
  /// for internal tools.
  final String? target;

  /// Distinct approvals the organization approval rules require (approval
  /// chains, docs/approvals-and-risk.md); 1 without a chain.
  final int requiredApprovals;

  /// Who has approved so far, oldest first.
  final List<ApprovalApprover> approvers;

  /// "1 of 3 approvals" for an approval that needs several people, else null.
  String? get progressLabel {
    if (requiredApprovals <= 1 && approvers.length <= 1) return null;
    final total = requiredApprovals > approvers.length
        ? requiredApprovals
        : approvers.length;
    return '${approvers.length} of $total approvals';
  }

  /// Whether [userId] already approved; the API accepts one approval per person.
  bool approvedBy(String? userId) =>
      userId != null && approvers.any((a) => a.userId == userId);

  /// A pending approval past [expiresAt] reads as expired.
  String effectiveStatus([DateTime? now]) => status == 'pending' &&
          expiresAt != null &&
          !expiresAt!.isAfter(now ?? DateTime.now())
      ? 'expired'
      : status;

  /// Approve is offered only while pending, unexpired and bound to a hash.
  bool canApprove([DateTime? now]) =>
      effectiveStatus(now) == 'pending' && payloadHash != null;
}

/// One recorded approve decision of an approval.
class ApprovalApprover {
  const ApprovalApprover(
      {required this.userId, required this.approvedAt, this.reason});
  final String userId;
  final DateTime approvedAt;
  final String? reason;
}

/// Names joined for a sentence: "a", "a and b", "a, b and c".
String joinNames(List<String> names) {
  if (names.length <= 1) return names.isEmpty ? '' : names.first;
  return '${names.sublist(0, names.length - 1).join(', ')} and ${names.last}';
}

/// Longest decision reason the API accepts.
const approvalReasonMaxChars = 500;

/// A typed reason as sent: trimmed, blank means none.
String? normalizeReason(String? reason) {
  final trimmed = (reason ?? '').replaceAll('\r\n', '\n').trim();
  if (trimmed.isEmpty) return null;
  return trimmed.length > approvalReasonMaxChars
      ? trimmed.substring(0, approvalReasonMaxChars)
      : trimmed;
}

/// The decision body: the displayed payload hash and the optional reason.
Map<String, Object?> decisionBody(WorkspaceApproval approval, String? reason) {
  final normalized = normalizeReason(reason);
  return {
    if (approval.payloadHash != null) 'payload_hash': approval.payloadHash,
    if (normalized != null) 'reason': normalized,
  };
}

/// One readable line of an approval's arguments: a dotted key and its value.
class ApprovalArgument {
  const ApprovalArgument(this.key, this.value);
  final String key, value;
  bool get redacted => value == '[REDACTED]';
}

/// Flattens nested arguments into sorted `key.path` / value rows.
List<ApprovalArgument> approvalArguments(Map<String, Object?> arguments) {
  final rows = <ApprovalArgument>[];
  void visit(String key, Object? value) {
    if (value is Map) {
      if (value.isEmpty) rows.add(ApprovalArgument(key, '{}'));
      final keys = value.keys.map((k) => '$k').toList()..sort();
      for (final child in keys) {
        visit('$key.$child', value[child]);
      }
    } else if (value is List) {
      if (value.isEmpty) rows.add(ApprovalArgument(key, '[]'));
      for (var i = 0; i < value.length; i++) {
        visit('$key[$i]', value[i]);
      }
    } else {
      rows.add(ApprovalArgument(key, value == null ? 'null' : '$value'));
    }
  }

  final keys = arguments.keys.toList()..sort();
  for (final key in keys) {
    visit(key, arguments[key]);
  }
  return rows;
}

class WorkspaceAutomation {
  const WorkspaceAutomation(
      {required this.id,
      required this.workflowId,
      required this.name,
      required this.status,
      required this.updatedAt,
      this.currentStep = 0,
      this.stepCount = 0});
  final String id, workflowId, name, status;
  final DateTime updatedAt;
  final int currentStep, stepCount;
}

class AutomationDefinition {
  const AutomationDefinition(
      {required this.id,
      required this.name,
      required this.description,
      required this.status,
      required this.steps,
      required this.updatedAt});
  final String id, name, description, status;
  final List<String> steps;
  final DateTime updatedAt;
}

class AutomationSchedule {
  const AutomationSchedule(
      {required this.id,
      required this.workflowId,
      required this.name,
      required this.cron,
      required this.timezone,
      required this.enabled});
  final String id, workflowId, name, cron, timezone;
  final bool enabled;
}

class WorkspaceSkill {
  const WorkspaceSkill(
      {required this.id,
      required this.skillId,
      required this.name,
      required this.version,
      required this.description,
      required this.risk,
      required this.tools,
      required this.installed});
  final String id, skillId, name, version, description, risk;
  final List<String> tools;
  final bool installed;
}

class WorkspaceIntegration {
  const WorkspaceIntegration(
      {required this.id,
      required this.name,
      required this.kind,
      required this.status,
      required this.endpoint,
      required this.detail,
      this.latencyMs});
  final String id, name, kind, status, endpoint, detail;
  final int? latencyMs;
}

class WorkspaceFile {
  const WorkspaceFile(
      {required this.id,
      required this.name,
      required this.contentType,
      required this.sizeBytes,
      required this.createdAt});
  final String id, name, contentType;
  final int sizeBytes;
  final DateTime createdAt;
}

class WorkspaceMemory {
  const WorkspaceMemory(
      {required this.id,
      required this.taskId,
      required this.content,
      required this.sourceType,
      required this.createdAt});
  final String id, taskId, content, sourceType;
  final DateTime createdAt;
}

class WorkspaceSnapshot {
  const WorkspaceSnapshot(
      {required this.tasks,
      required this.approvals,
      required this.automations,
      required this.files,
      required this.memories,
      this.workflowDefinitions = const [],
      this.schedules = const [],
      this.skills = const [],
      this.integrations = const []});
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
