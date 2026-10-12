/// ANUM Command models (docs/command.md): the plan preview, a command's live
/// view, next steps, suggestions and quick commands. Read only through
/// [JsonReader].
library;

import '../../data/json_read.dart';

enum ApprovalNeed { none, required, blocked }

ApprovalNeed _need(String? value) => switch (value) {
      'required' => ApprovalNeed.required,
      'blocked' => ApprovalNeed.blocked,
      _ => ApprovalNeed.none,
    };

/// Derived on the server from the command's tasks.
enum CommandStatus {
  ready,
  running,
  waitingApproval,
  completed,
  partial,
  failed,
  cancelled;

  static CommandStatus parse(String? value) => switch (value) {
        'running' => running,
        'waiting_approval' => waitingApproval,
        'completed' => completed,
        'partial' => partial,
        'failed' => failed,
        'cancelled' => cancelled,
        _ => ready,
      };

  /// Nothing runs any more and nothing waits for a person.
  bool get finished =>
      this == completed ||
      this == partial ||
      this == failed ||
      this == cancelled;

  bool get active => this == running || this == waitingApproval;
}

enum QuickCategory {
  work,
  personal,
  custom;

  static QuickCategory parse(String? value) => switch (value) {
        'work' => work,
        'personal' => personal,
        _ => custom,
      };
}

class CommandAgent {
  const CommandAgent({required this.id, required this.name});
  factory CommandAgent.fromJson(JsonReader j) =>
      CommandAgent(id: j.string('id'), name: j.string('name'));

  /// email, whatsapp, research, action, calendar, tyre, writer or general.
  final String id;
  final String name;
}

class PlanStep {
  const PlanStep({
    required this.position,
    required this.title,
    required this.instruction,
    required this.agent,
    required this.skills,
    required this.tool,
    required this.riskLevel,
    required this.approval,
    required this.approvalReason,
    required this.connected,
    this.note,
  });

  factory PlanStep.fromJson(JsonReader j) => PlanStep(
        position: j.integer('position'),
        title: j.string('title'),
        instruction: j.string('instruction'),
        agent: CommandAgent.fromJson(j.object('agent')),
        skills: j.objects('skills', (item) => item.string('name')),
        tool: j.optObject('tool')?.string('name'),
        riskLevel: j.string('risk_level'),
        approval: _need(j.string('approval')),
        approvalReason: j.optString('approval_reason') ?? '',
        connected: j.optBool('connected') ?? true,
        note: j.optString('note'),
      );

  final int position;
  final String title;
  final String instruction;
  final CommandAgent agent;
  final List<String> skills;
  final String? tool;
  final String riskLevel;
  final ApprovalNeed approval;
  final String approvalReason;
  final bool connected;
  final String? note;

  /// The same step with an edited instruction (planned again on Start).
  PlanStep withInstruction(String value) => PlanStep(
        position: position,
        title: value,
        instruction: value,
        agent: agent,
        skills: skills,
        tool: tool,
        riskLevel: riskLevel,
        approval: approval,
        approvalReason: approvalReason,
        connected: connected,
        note: note,
      );
}

class CommandPlan {
  const CommandPlan({
    required this.text,
    required this.language,
    required this.steps,
    required this.agents,
  });

  factory CommandPlan.fromJson(JsonReader j) => CommandPlan(
        text: j.string('text'),
        language: j.optString('language') ?? 'en',
        steps: j.objects('steps', PlanStep.fromJson),
        agents: j.objects('agents', CommandAgent.fromJson),
      );

  final String text;
  final String language;
  final List<PlanStep> steps;
  final List<CommandAgent> agents;
}

/// A pending or decided approval of one of the command's tasks.
class CommandApproval {
  const CommandApproval({
    required this.id,
    required this.taskId,
    required this.action,
    required this.status,
    required this.reason,
    required this.riskLevel,
    this.payloadHash,
    this.arguments = const {},
  });

  factory CommandApproval.fromJson(JsonReader j) => CommandApproval(
        id: j.string('id'),
        taskId: j.string('task_id'),
        action: j.string('action'),
        status: j.string('status'),
        reason: j.optString('reason') ?? '',
        riskLevel: j.optString('risk_level') ?? 'high',
        payloadHash: j.optString('payload_hash'),
        arguments: j.optMap('arguments') ?? const {},
      );

  final String id;
  final String taskId;
  final String action;
  final String status;
  final String reason;
  final String riskLevel;
  final String? payloadHash;
  final Map<String, Object?> arguments;

  bool get pending => status == 'pending';
}

class CommandStepView {
  const CommandStepView({
    required this.position,
    required this.title,
    required this.instruction,
    required this.agent,
    required this.tool,
    required this.riskLevel,
    required this.approval,
    required this.taskId,
    required this.status,
    required this.progress,
    this.result,
    this.startedAt,
    this.finishedAt,
    this.approvals = const [],
  });

  factory CommandStepView.fromJson(JsonReader j) => CommandStepView(
        position: j.integer('position'),
        title: j.string('title'),
        instruction: j.string('instruction'),
        agent: CommandAgent.fromJson(j.object('agent')),
        tool: j.optString('tool'),
        riskLevel: j.string('risk_level'),
        approval: _need(j.string('approval')),
        taskId: j.string('task_id'),
        status: j.string('status'),
        progress: j.number('progress').clamp(0, 1).toDouble(),
        result: j.optString('result'),
        startedAt: j.optDate('started_at'),
        finishedAt: j.optDate('finished_at'),
        approvals: j.optObjects('approvals', CommandApproval.fromJson),
      );

  final int position;
  final String title;
  final String instruction;
  final CommandAgent agent;
  final String? tool;
  final String riskLevel;
  final ApprovalNeed approval;
  final String taskId;

  /// The task's status (created, queued, running, waiting_approval,
  /// completed, failed, cancelled) or `missing`.
  final String status;
  final double progress;
  final String? result;
  final DateTime? startedAt;
  final DateTime? finishedAt;
  final List<CommandApproval> approvals;

  bool get terminal =>
      const {'completed', 'failed', 'cancelled', 'missing'}.contains(status);
}

class TimelineEntry {
  const TimelineEntry({
    required this.id,
    required this.at,
    required this.type,
    required this.taskId,
    required this.position,
    required this.agent,
    required this.title,
  });

  factory TimelineEntry.fromJson(JsonReader j) => TimelineEntry(
        id: j.string('id'),
        at: j.date('at'),
        type: j.string('type'),
        taskId: j.string('task_id'),
        position: j.integer('position'),
        agent: CommandAgent.fromJson(j.object('agent')),
        title: j.string('title'),
      );

  final String id;
  final DateTime at;
  final String type;
  final String taskId;
  final int position;
  final CommandAgent agent;
  final String title;
}

class CommandFile {
  const CommandFile({
    required this.kind,
    required this.name,
    required this.path,
    this.contentType,
  });

  factory CommandFile.fromJson(JsonReader j) => CommandFile(
        kind: j.string('kind'),
        name: j.string('name'),
        path: j.string('path'),
        contentType: j.optString('content_type'),
      );

  /// `attachment` or `run_log`.
  final String kind;
  final String name;

  /// API path to download with the session's headers.
  final String path;
  final String? contentType;
}

class CommandSummary {
  const CommandSummary({
    required this.id,
    required this.text,
    required this.status,
    required this.progress,
    required this.stepCount,
    required this.completedSteps,
    required this.pendingApprovals,
    required this.agents,
    required this.createdAt,
  });

  factory CommandSummary.fromJson(JsonReader j) => CommandSummary(
        id: j.string('id'),
        text: j.string('text'),
        status: CommandStatus.parse(j.string('status')),
        progress: j.number('progress').clamp(0, 1).toDouble(),
        stepCount: j.integer('step_count'),
        completedSteps: j.integer('completed_steps'),
        pendingApprovals: j.integer('pending_approvals'),
        agents: j.objects('agents', CommandAgent.fromJson),
        createdAt: j.date('created_at'),
      );

  final String id;
  final String text;
  final CommandStatus status;
  final double progress;
  final int stepCount;
  final int completedSteps;
  final int pendingApprovals;
  final List<CommandAgent> agents;
  final DateTime createdAt;
}

class CommandView extends CommandSummary {
  const CommandView({
    required super.id,
    required super.text,
    required super.status,
    required super.progress,
    required super.stepCount,
    required super.completedSteps,
    required super.pendingApprovals,
    required super.agents,
    required super.createdAt,
    required this.locale,
    required this.steps,
    required this.pending,
    required this.timeline,
    required this.files,
    this.durationSeconds,
  });

  factory CommandView.fromJson(JsonReader j) {
    final summary = CommandSummary.fromJson(j);
    return CommandView(
      id: summary.id,
      text: summary.text,
      status: summary.status,
      progress: summary.progress,
      stepCount: summary.stepCount,
      completedSteps: summary.completedSteps,
      pendingApprovals: summary.pendingApprovals,
      agents: summary.agents,
      createdAt: summary.createdAt,
      locale: j.optString('locale') ?? 'en',
      steps: j.objects('steps', CommandStepView.fromJson),
      pending: j.optObjects('pending', CommandApproval.fromJson),
      timeline: j.optObjects('timeline', TimelineEntry.fromJson),
      files: j.optObjects('files', CommandFile.fromJson),
      durationSeconds: j.optNumber('duration_seconds'),
    );
  }

  final String locale;
  final List<CommandStepView> steps;
  final List<CommandApproval> pending;
  final List<TimelineEntry> timeline;
  final List<CommandFile> files;
  final double? durationSeconds;

  Set<String> get taskIds => {for (final step in steps) step.taskId};

  /// Plain-text summary (copied by Share).
  String plainSummary() {
    final lines = <String>[text, ''];
    for (final step in steps) {
      lines.add('${step.position}. ${step.title} (${step.agent.name}): '
          '${step.result?.trim().isNotEmpty == true ? step.result!.trim() : step.status}');
    }
    return lines.join('\n');
  }
}

class NextStep {
  const NextStep(
      {required this.id, required this.kind, required this.label, this.text});

  factory NextStep.fromJson(JsonReader j) => NextStep(
        id: j.string('id'),
        kind: j.string('kind'),
        label: j.string('label'),
        text: j.optString('text'),
      );

  final String id;

  /// `command`, `review_approvals` or `save_quick_command`.
  final String kind;
  final String label;
  final String? text;
}

class CommandSuggestion {
  const CommandSuggestion({
    required this.kind,
    required this.text,
    required this.title,
    this.quickCommandId,
  });

  factory CommandSuggestion.fromJson(JsonReader j) => CommandSuggestion(
        kind: j.string('kind'),
        text: j.string('text'),
        title: j.string('title'),
        quickCommandId: j.optString('quick_command_id'),
      );

  /// `quick`, `recent` or `starter`.
  final String kind;
  final String text;
  final String title;
  final String? quickCommandId;
}

class QuickCommand {
  const QuickCommand({
    required this.id,
    required this.title,
    required this.text,
    required this.category,
    required this.position,
    required this.useCount,
  });

  factory QuickCommand.fromJson(JsonReader j) => QuickCommand(
        id: j.string('id'),
        title: j.string('title'),
        text: j.string('text'),
        category: QuickCategory.parse(j.string('category')),
        position: j.integer('position'),
        useCount: j.optInt('use_count') ?? 0,
      );

  final String id;
  final String title;
  final String text;
  final QuickCategory category;
  final int position;
  final int useCount;
}

class CommandAttachment {
  const CommandAttachment({required this.fileId, required this.name});
  final String fileId;
  final String name;
}
