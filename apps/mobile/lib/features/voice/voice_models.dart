import '../../data/api_models.dart';

enum VoiceRetention { session, thirtyDays, permanent }

extension VoiceRetentionValue on VoiceRetention {
  String get apiValue => switch (this) {
        VoiceRetention.session => 'session',
        VoiceRetention.thirtyDays => '30_days',
        VoiceRetention.permanent => 'permanent',
      };

  String get label => switch (this) {
        VoiceRetention.session => 'Delete when the conversation ends',
        VoiceRetention.thirtyDays => 'Keep for 30 days',
        VoiceRetention.permanent => 'Keep until deleted',
      };
}

/// What the assistant is doing right now. Drives the orb and the labels.
enum VoiceState { idle, listening, thinking, speaking }

/// Server-side risk tier of a spoken request.
enum VoiceRiskTier { read, confirm, visualOnly }

VoiceRiskTier _tier(Object? value) => switch (value) {
      'confirm' => VoiceRiskTier.confirm,
      'visual_only' => VoiceRiskTier.visualOnly,
      _ => VoiceRiskTier.read,
    };

class VoiceSession {
  const VoiceSession({
    required this.id,
    required this.locale,
    required this.status,
    this.assistantName = 'Anum',
  });

  factory VoiceSession.fromJson(JsonMap value) => VoiceSession(
        id: value['id']! as String,
        locale: value['locale']! as String,
        status: value['status']! as String,
        assistantName: (value['assistant_name'] as String?) ?? 'Anum',
      );

  final String id;
  final String locale;
  final String status;
  final String assistantName;
}

class VoiceSegment {
  const VoiceSegment({required this.id, required this.text});
  final String id;
  final String text;
}

class VoiceCommand {
  const VoiceCommand(
      {required this.taskId, required this.title, required this.status});
  final String taskId;
  final String title;
  final String status;
}

class WorkspaceFacts {
  const WorkspaceFacts({
    this.tasksTotal = 0,
    this.running = 0,
    this.waitingApproval = 0,
    this.pendingApprovals = 0,
  });

  factory WorkspaceFacts.fromJson(JsonMap? value) => WorkspaceFacts(
        tasksTotal: (value?['tasks_total'] as num?)?.toInt() ?? 0,
        running: (value?['running'] as num?)?.toInt() ?? 0,
        waitingApproval: (value?['waiting_approval'] as num?)?.toInt() ?? 0,
        pendingApprovals: (value?['pending_approvals'] as num?)?.toInt() ?? 0,
      );

  final int tasksTotal, running, waitingApproval, pendingApprovals;
}

/// Response of `POST /voice/sessions/{id}/ask`. Read-only on the server: it
/// can answer, or propose a task the user must confirm on screen.
class VoiceAskResult {
  const VoiceAskResult({
    required this.intent,
    required this.riskTier,
    required this.reply,
    required this.workspace,
    required this.assistantSegmentId,
    this.proposedTask,
  });

  factory VoiceAskResult.fromJson(JsonMap value) {
    final segment = value['assistant_segment'] as JsonMap?;
    final proposal = (value['proposed_task'] as String?)?.trim();
    return VoiceAskResult(
      intent: (value['intent'] as String?) ?? 'question',
      riskTier: _tier(value['risk_tier']),
      reply: (value['reply'] as String?) ?? '',
      proposedTask: proposal == null || proposal.isEmpty ? null : proposal,
      workspace: WorkspaceFacts.fromJson(value['workspace'] as JsonMap?),
      assistantSegmentId: (segment?['id'] as String?) ??
          'assistant_${DateTime.now().microsecondsSinceEpoch}',
    );
  }

  final String intent;
  final VoiceRiskTier riskTier;
  final String reply;
  final String? proposedTask;
  final WorkspaceFacts workspace;
  final String assistantSegmentId;
}

enum VoiceSpeaker { you, assistant }

enum TurnResolution { created, dismissed }

/// One bubble in the conversation.
class VoiceTurn {
  const VoiceTurn({
    required this.id,
    required this.speaker,
    required this.text,
    this.result,
    this.sessionId,
    this.resolution,
    this.busy = false,
    this.createdTitle,
  });

  final String id;
  final VoiceSpeaker speaker;
  final String text;
  final VoiceAskResult? result;
  final String? sessionId;
  final TurnResolution? resolution;
  final bool busy;
  final String? createdTitle;

  /// The assistant proposed a task and is waiting for the user's tap.
  bool get awaitingConfirmation =>
      result?.riskTier == VoiceRiskTier.confirm &&
      result?.proposedTask != null &&
      resolution == null;

  VoiceTurn copyWith(
          {TurnResolution? resolution, bool? busy, String? createdTitle}) =>
      VoiceTurn(
        id: id,
        speaker: speaker,
        text: text,
        result: result,
        sessionId: sessionId,
        resolution: resolution ?? this.resolution,
        busy: busy ?? this.busy,
        createdTitle: createdTitle ?? this.createdTitle,
      );
}
