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

  factory VoiceSession.fromJson(JsonMap value) {
    final r = JsonReader(value);
    return VoiceSession(
      id: r.string('id'),
      locale: r.string('locale'),
      status: r.string('status'),
      assistantName: r.optString('assistant_name') ?? 'Anum',
    );
  }

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

  factory WorkspaceFacts.fromJson(JsonMap? value, [String path = '']) {
    final r = JsonReader(value ?? const {}, path);
    return WorkspaceFacts(
      tasksTotal: r.optInt('tasks_total') ?? 0,
      running: r.optInt('running') ?? 0,
      waitingApproval: r.optInt('waiting_approval') ?? 0,
      pendingApprovals: r.optInt('pending_approvals') ?? 0,
    );
  }

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
    final r = JsonReader(value);
    final segment = r.optObject('assistant_segment');
    final proposal = r.optString('proposed_task')?.trim();
    final workspace = r.optObject('workspace');
    return VoiceAskResult(
      intent: r.optString('intent') ?? 'question',
      riskTier: _tier(value['risk_tier']),
      reply: r.optString('reply') ?? '',
      proposedTask: proposal == null || proposal.isEmpty ? null : proposal,
      workspace: WorkspaceFacts.fromJson(workspace?.json, r.at('workspace')),
      assistantSegmentId: segment?.optString('id') ??
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
