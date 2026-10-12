import '../../data/json_read.dart';

/// Calls & Meetings (docs/meetings.md): the API's shapes.
const meetingsPageSize = 20;
const transcriptPageSize = 100;

class MeetingActionItem {
  const MeetingActionItem({
    required this.id,
    required this.text,
    this.owner,
    this.dueDate,
    this.done = false,
    this.taskId,
  });
  factory MeetingActionItem.fromJson(JsonReader j) => MeetingActionItem(
        id: j.string('id'),
        text: j.string('text'),
        owner: j.optString('owner'),
        dueDate: j.optString('due_date'),
        done: j.optBool('done') ?? false,
        taskId: j.optString('task_id'),
      );
  final String id, text;
  final String? owner, dueDate, taskId;
  final bool done;
}

class MeetingFollowUp {
  const MeetingFollowUp({
    this.to = const [],
    this.subject = '',
    this.body = '',
    this.edited = false,
    this.sendTaskId,
    this.approvalId,
    this.sentFrom,
  });
  factory MeetingFollowUp.fromJson(JsonReader j) => MeetingFollowUp(
        to: j.optStrings('to') ?? const [],
        subject: j.optString('subject') ?? '',
        body: j.optString('body') ?? '',
        edited: j.optBool('edited') ?? false,
        sendTaskId: j.optString('send_task_id'),
        approvalId: j.optString('approval_id'),
        sentFrom: j.optString('sent_from'),
      );
  final List<String> to;
  final String subject, body;
  final bool edited;
  final String? sendTaskId, approvalId, sentFrom;
}

class Meeting {
  const Meeting({
    required this.id,
    required this.title,
    required this.source,
    required this.status,
    required this.language,
    required this.createdBy,
    required this.createdAt,
    required this.updatedAt,
    this.occurredAt,
    this.durationSeconds,
    this.agentSkillId,
    this.segmentCount = 0,
    this.errorCode,
    this.error,
    this.summary,
    this.decisions = const [],
    this.actionItems = const [],
    this.followUp,
    this.model,
    this.truncated = false,
  });

  factory Meeting.fromJson(JsonReader j) {
    final provenance = j.optObject('provenance');
    final followUp = j.optObject('follow_up');
    return Meeting(
      id: j.string('id'),
      title: j.string('title'),
      source: j.string('source'),
      status: j.string('status'),
      language: j.optString('language') ?? 'auto',
      createdBy: j.string('created_by'),
      createdAt: j.date('created_at'),
      updatedAt: j.date('updated_at'),
      occurredAt: j.optDate('occurred_at'),
      durationSeconds: j.optInt('duration_seconds'),
      agentSkillId: j.optString('agent_skill_id'),
      segmentCount: j.optInt('segment_count') ?? 0,
      errorCode: j.optString('error_code'),
      error: j.optString('error'),
      summary: j.optString('summary'),
      decisions: j.optStrings('decisions') ?? const [],
      actionItems: j.optObjects('action_items', MeetingActionItem.fromJson),
      followUp: followUp == null ? null : MeetingFollowUp.fromJson(followUp),
      model: provenance?.optString('model'),
      truncated: provenance?.optBool('truncated') ?? false,
    );
  }

  final String id, title, source, status, language, createdBy;
  final DateTime createdAt, updatedAt;
  final DateTime? occurredAt;
  final int? durationSeconds;
  final String? agentSkillId, errorCode, error, summary, model;
  final int segmentCount;
  final List<String> decisions;
  final List<MeetingActionItem> actionItems;
  final MeetingFollowUp? followUp;
  final bool truncated;

  bool get isWorking =>
      status == 'queued' || status == 'transcribing' || status == 'analyzing';
  bool get isArabic => language == 'ar';
}

class MeetingPage {
  const MeetingPage(this.items, this.total);
  final List<Meeting> items;
  final int total;
}

class TranscriptLine {
  const TranscriptLine({
    required this.id,
    required this.text,
    this.speaker,
    this.startMs,
    required this.seq,
  });
  factory TranscriptLine.fromJson(JsonReader j) => TranscriptLine(
        id: j.string('id'),
        seq: j.integer('seq'),
        text: j.string('text'),
        speaker: j.optString('speaker'),
        startMs: j.optInt('start_ms'),
      );
  final String id, text;
  final String? speaker;
  final int? startMs;
  final int seq;
}

class TranscriptPage {
  const TranscriptPage(this.lines, this.total, this.speakers);
  final List<TranscriptLine> lines;
  final int total;
  final List<String> speakers;
}

class MeetingConnector {
  const MeetingConnector({
    required this.provider,
    required this.available,
    required this.connected,
    this.problem,
    this.accountLabel,
  });
  factory MeetingConnector.fromJson(JsonReader j) => MeetingConnector(
        provider: j.string('provider'),
        available: j.optBool('available') ?? false,
        connected: j.optBool('connected') ?? false,
        problem: j.optString('problem'),
        accountLabel: j.optString('account_label'),
      );
  final String provider;
  final bool available, connected;
  final String? problem, accountLabel;
  String get name => provider == 'zoom' ? 'Zoom' : 'Microsoft Teams';
}

/// A connected mailbox the follow-up can be sent from (docs/email.md).
class EmailAccountChoice {
  const EmailAccountChoice({required this.id, required this.address});
  factory EmailAccountChoice.fromJson(JsonReader j) =>
      EmailAccountChoice(id: j.string('id'), address: j.string('address'));
  final String id, address;
}

class MeetingSetup {
  const MeetingSetup({
    required this.transcriptionConfigured,
    this.transcriptionProblem,
    required this.recordingExtensions,
    required this.maxRecordingBytes,
    required this.transcriptExtensions,
    required this.maxTranscriptBytes,
    required this.connectors,
    required this.modelReady,
    this.emailAccounts = const [],
    required this.canCreate,
    required this.canManageAll,
  });
  factory MeetingSetup.fromJson(JsonReader j) {
    final t = j.object('transcription');
    return MeetingSetup(
      transcriptionConfigured: t.optBool('configured') ?? false,
      transcriptionProblem: t.optString('problem'),
      recordingExtensions: t.optStrings('accepted_extensions') ?? const [],
      maxRecordingBytes: t.optInt('max_recording_bytes') ?? 0,
      transcriptExtensions: j.optStrings('transcript_extensions') ??
          const ['.vtt', '.srt', '.txt', '.docx'],
      maxTranscriptBytes: j.optInt('max_transcript_bytes') ?? 0,
      connectors: j.optObjects('connectors', MeetingConnector.fromJson),
      modelReady: j.optBool('model_ready') ?? false,
      emailAccounts:
          j.optObjects('email_accounts', EmailAccountChoice.fromJson),
      canCreate: j.optBool('can_create') ?? false,
      canManageAll: j.optBool('can_manage_all') ?? false,
    );
  }
  final bool transcriptionConfigured, modelReady, canCreate, canManageAll;
  final String? transcriptionProblem;

  /// Mailboxes the caller may send the follow-up from (email module).
  final List<EmailAccountChoice> emailAccounts;
  final List<String> recordingExtensions, transcriptExtensions;
  final int maxRecordingBytes, maxTranscriptBytes;
  final List<MeetingConnector> connectors;
}

class ExternalMeeting {
  const ExternalMeeting({
    required this.externalId,
    required this.title,
    this.startedAt,
    this.durationMinutes,
    this.importedMeetingId,
  });
  factory ExternalMeeting.fromJson(JsonReader j) => ExternalMeeting(
        externalId: j.string('external_id'),
        title: j.string('title'),
        startedAt: j.optDate('started_at'),
        durationMinutes: j.optInt('duration_minutes'),
        importedMeetingId: j.optString('imported_meeting_id'),
      );
  final String externalId, title;
  final DateTime? startedAt;
  final int? durationMinutes;
  final String? importedMeetingId;
}

/// "01:02" or "1:02:03" for a transcript line; empty when unknown.
String transcriptClock(int? ms) {
  if (ms == null || ms < 0) return '';
  final total = ms ~/ 1000;
  final h = total ~/ 3600, m = (total % 3600) ~/ 60, s = total % 60;
  String two(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '${two(m)}:${two(s)}';
}

/// The meeting list's query string.
String meetingsListPath({
  String query = '',
  String status = '',
  int offset = 0,
  int limit = meetingsPageSize,
}) {
  final params = <String, String>{
    'limit': '$limit',
    'offset': '$offset',
    if (query.trim().isNotEmpty) 'q': query.trim(),
    if (status.isNotEmpty) 'status': status,
  };
  return Uri(path: '/api/v1/meetings', queryParameters: params).toString();
}

/// Problems with a picked file before it is sent, or null.
String? meetingUploadProblem({
  required String name,
  required int size,
  required bool recording,
  required MeetingSetup setup,
}) {
  final dot = name.lastIndexOf('.');
  final ext = dot >= 0 ? name.substring(dot).toLowerCase() : '';
  if (size <= 0) return 'empty';
  final allowed =
      recording ? setup.recordingExtensions : setup.transcriptExtensions;
  if (!allowed.contains(ext)) return 'type';
  final limit = recording ? setup.maxRecordingBytes : setup.maxTranscriptBytes;
  if (limit > 0 && size > limit) return 'size';
  return null;
}
