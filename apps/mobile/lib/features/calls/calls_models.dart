import '../../data/json_read.dart';

/// Phone calls (docs/calls.md): the API's shapes and the dialpad's pure
/// rules, kept free of Flutter so they are tested on their own.
const callsPageSize = 25;
const logOutcomes = ['answered', 'no_answer', 'busy', 'voicemail'];

class AiConsent {
  const AiConsent(
      {required this.grantedAt,
      required this.recordedBy,
      required this.method});
  factory AiConsent.fromJson(JsonReader j) => AiConsent(
        grantedAt: j.date('granted_at'),
        recordedBy: j.string('recorded_by'),
        method: j.string('method'),
      );
  final DateTime grantedAt;
  final String recordedBy, method;
}

class CallContact {
  const CallContact({
    required this.id,
    required this.name,
    required this.phone,
    this.company,
    this.email,
    this.notes,
    this.favourite = false,
    this.source = 'manual',
    this.language = 'auto',
    this.consent,
    this.optedOut = false,
    this.canEdit = false,
  });
  factory CallContact.fromJson(JsonReader j) {
    final consent = j.optObject('ai_consent');
    return CallContact(
      id: j.string('id'),
      name: j.string('name'),
      phone: j.string('phone'),
      company: j.optString('company'),
      email: j.optString('email'),
      notes: j.optString('notes'),
      favourite: j.optBool('favourite') ?? false,
      source: j.optString('source') ?? 'manual',
      language: j.optString('language') ?? 'auto',
      consent: consent == null ? null : AiConsent.fromJson(consent),
      optedOut: j.optBool('opted_out') ?? false,
      canEdit: j.optBool('can_edit') ?? false,
    );
  }
  final String id, name, phone, source;

  /// The language the contact speaks: `en`, `ar` or `auto`.
  final String language;
  final String? company, email, notes;
  final bool favourite, optedOut, canEdit;
  final AiConsent? consent;
}

class CallFollowUp {
  const CallFollowUp({required this.text, this.dueDate, this.taskId});
  factory CallFollowUp.fromJson(JsonReader j) => CallFollowUp(
        text: j.string('text'),
        dueDate: j.optString('due_date'),
        taskId: j.optString('task_id'),
      );
  final String text;
  final String? dueDate, taskId;
}

class CallEntry {
  const CallEntry({
    required this.id,
    required this.direction,
    required this.kind,
    required this.status,
    required this.startedAt,
    this.contactId,
    this.contactName,
    this.phone,
    this.peerUserId,
    this.outcome,
    this.durationSeconds,
    this.notes,
    this.summary,
    this.followUp,
    this.model,
    this.transcript,
    this.canEdit = false,
  });
  factory CallEntry.fromJson(JsonReader j) {
    final follow = j.optObject('follow_up');
    return CallEntry(
      id: j.string('id'),
      direction: j.string('direction'),
      kind: j.string('kind'),
      status: j.string('status'),
      startedAt: j.date('started_at'),
      contactId: j.optString('contact_id'),
      contactName: j.optString('contact_name'),
      phone: j.optString('phone'),
      peerUserId: j.optString('peer_user_id'),
      outcome: j.optString('outcome'),
      durationSeconds: j.optInt('duration_seconds'),
      notes: j.optString('notes'),
      summary: j.optString('summary'),
      followUp: follow == null ? null : CallFollowUp.fromJson(follow),
      model: j.optObject('summary_provenance')?.optString('model'),
      transcript: j.optString('transcript'),
      canEdit: j.optBool('can_edit') ?? false,
    );
  }
  final String id, direction, kind, status;
  final DateTime startedAt;
  final String? contactId,
      contactName,
      phone,
      peerUserId,
      outcome,
      notes,
      summary,
      model,
      transcript;
  final int? durationSeconds;
  final CallFollowUp? followUp;
  final bool canEdit;

  String get who => contactName ?? phone ?? peerUserId ?? '';
  bool get live =>
      status == 'dialing' || status == 'ringing' || status == 'in_progress';
}

class CallsSetup {
  const CallsSetup({
    required this.canLog,
    required this.canManage,
    required this.canCreateTasks,
    required this.modelReady,
    required this.providerConnected,
    this.providerProblem,
    this.browserCalls = false,
    this.recording = false,
    this.aiCalls = false,
    this.aiProblem,
    this.costPerMinuteCents,
  });
  factory CallsSetup.fromJson(JsonReader j) {
    final caps = j.optObject('capabilities');
    return CallsSetup(
      canLog: j.optBool('can_log') ?? false,
      canManage: j.optBool('can_manage') ?? false,
      canCreateTasks: j.optBool('can_create_tasks') ?? false,
      modelReady: j.optBool('model_ready') ?? false,
      providerConnected: j.optBool('provider_connected') ?? false,
      providerProblem: caps?.optString('problem'),
      browserCalls: caps?.optBool('browser_calls') ?? false,
      recording: caps?.optBool('recording') ?? false,
      aiCalls: caps?.optBool('ai_calls') ?? false,
      aiProblem: caps?.optString('ai_problem'),
      costPerMinuteCents: caps?.optInt('cost_per_minute_cents'),
    );
  }
  final bool canLog, canManage, canCreateTasks, modelReady, providerConnected;

  /// Why calls through the provider are off (null when they work).
  final String? providerProblem;

  /// What the server reports as ready (`capabilities` of GET /setup).
  final bool browserCalls, recording, aiCalls;

  /// Why AI calls are off (null when they can be asked for).
  final String? aiProblem;
  final int? costPerMinuteCents;
}

/// The workspace's voice provider as an owner sees it (GET /provider,
/// call:manage). Secrets are never returned, only a short hint.
class ProviderStatus {
  const ProviderStatus({
    required this.connected,
    this.provider = 'twilio',
    this.fromNumber,
    this.credentialHint,
    this.businessName,
    this.recordingEnabled = false,
    this.transcriptionEnabled = false,
    this.aiCallsEnabled = false,
    this.inboundAiEnabled = false,
    this.aiMaxSeconds = 180,
    this.aiMaxCostCents = 100,
    this.costPerMinuteCents = 5,
    this.lastCheckStatus,
    this.lastCheckDetail,
    this.lastCheckedAt,
    this.webhooksReady = false,
    this.problem,
  });
  factory ProviderStatus.fromJson(JsonReader j) => ProviderStatus(
        connected: j.optBool('connected') ?? false,
        provider: j.optString('provider') ?? 'twilio',
        fromNumber: j.optString('from_number'),
        credentialHint: j.optString('credential_hint'),
        businessName: j.optString('business_name'),
        recordingEnabled: j.optBool('recording_enabled') ?? false,
        transcriptionEnabled: j.optBool('transcription_enabled') ?? false,
        aiCallsEnabled: j.optBool('ai_calls_enabled') ?? false,
        inboundAiEnabled: j.optBool('inbound_ai_enabled') ?? false,
        aiMaxSeconds: j.optInt('ai_max_seconds') ?? 180,
        aiMaxCostCents: j.optInt('ai_max_cost_cents') ?? 100,
        costPerMinuteCents: j.optInt('cost_per_minute_cents') ?? 5,
        lastCheckStatus: j.optString('last_check_status'),
        lastCheckDetail: j.optString('last_check_detail'),
        lastCheckedAt: j.optDate('last_checked_at'),
        webhooksReady: j.optBool('webhooks_ready') ?? false,
        problem: j.optString('problem'),
      );
  final bool connected,
      recordingEnabled,
      transcriptionEnabled,
      aiCallsEnabled,
      inboundAiEnabled,
      webhooksReady;
  final String provider;
  final String? fromNumber,
      credentialHint,
      businessName,
      lastCheckStatus,
      lastCheckDetail,
      problem;
  final int aiMaxSeconds, aiMaxCostCents, costPerMinuteCents;
  final DateTime? lastCheckedAt;
}

/// The statuses of an AI call request (docs/calls.md#ai-calls).
const aiCallStatuses = [
  'pending_approval',
  'calling',
  'completed',
  'failed',
  'cancelled',
];

/// An AI call request. Nothing is dialled until a workspace owner approves
/// the exact number, purpose and script (bound to their payload hash).
class AiCallRequest {
  const AiCallRequest({
    required this.id,
    required this.requestedBy,
    required this.contactId,
    required this.contactName,
    required this.phoneMasked,
    required this.purpose,
    required this.script,
    required this.language,
    required this.maxSeconds,
    required this.maxCostCents,
    required this.status,
    required this.createdAt,
    this.approvalId,
    this.approvalStatus,
    this.taskId,
    this.callId,
    this.error,
  });
  factory AiCallRequest.fromJson(JsonReader j) => AiCallRequest(
        id: j.string('id'),
        requestedBy: j.string('requested_by'),
        contactId: j.string('contact_id'),
        contactName: j.string('contact_name'),
        phoneMasked: j.optString('phone_masked') ?? '',
        purpose: j.string('purpose'),
        script: j.optString('script') ?? '',
        language: j.optString('language') ?? 'en',
        maxSeconds: j.optInt('max_seconds') ?? 0,
        maxCostCents: j.optInt('max_cost_cents') ?? 0,
        status: j.string('status'),
        createdAt: j.date('created_at'),
        approvalId: j.optString('approval_id'),
        approvalStatus: j.optObject('approval')?.optString('status'),
        taskId: j.optString('task_id'),
        callId: j.optString('call_id'),
        error: j.optString('error'),
      );
  final String id,
      requestedBy,
      contactId,
      contactName,
      phoneMasked,
      purpose,
      script,
      language,
      status;
  final int maxSeconds, maxCostCents;
  final DateTime createdAt;
  final String? approvalId, approvalStatus, taskId, callId, error;

  bool get pending => status == 'pending_approval';
}

/// The AI call limits the server enforces (AiCallCreate in
/// services/api/anum_api/calls/api.py), checked first so the form explains them.
const aiPurposeMin = 5, aiPurposeMax = 500;
const aiScriptMin = 10, aiScriptMax = 4000;
const aiSecondsMin = 30, aiSecondsMax = 900, aiSecondsDefault = 120;

/// Why an AI call cannot be asked for [contact] now, as a catalog key, or
/// null. The same order as the web's `aiCallBlocker`
/// (apps/web/src/lib/calls.ts); the server checks all of it again.
String? aiCallBlocker(CallContact contact, CallsSetup? setup) {
  if (setup == null || !setup.canLog) return 'calls.ai.blocked.role';
  if (contact.optedOut) return 'calls.ai.blocked.optedOut';
  if (contact.consent == null) return 'calls.ai.blocked.noConsent';
  if (!setup.aiCalls) return 'calls.ai.blocked.off';
  return null;
}

/// Contacts an AI call may be asked for: recorded consent, not opted out.
List<CallContact> consentingContacts(Iterable<CallContact> contacts) => [
      for (final c in contacts)
        if (c.consent != null && !c.optedOut) c,
    ];

/// The most a call of [seconds] can cost at [centsPerMinute] per started
/// minute, as `$0.10` (USD cents, as the web shows it).
String aiCostText(int seconds, int? centsPerMinute) {
  final minutes = seconds <= 0 ? 0 : (seconds + 59) ~/ 60;
  final cents = minutes * (centsPerMinute ?? 0);
  return '\$${(cents / 100).toStringAsFixed(2)}';
}

/// A typed call length clamped to the API's range (30 to 900 seconds).
int clampAiSeconds(int? value) =>
    (value ?? aiSecondsMin).clamp(aiSecondsMin, aiSecondsMax);

class CallsPage<T> {
  const CallsPage(this.items, this.total);
  final List<T> items;
  final int total;
}

/// A contact the person picked from the phone's own address book (only this
/// one contact is read; nothing else leaves the phone).
class DeviceContact {
  const DeviceContact({required this.name, required this.phone});
  final String name, phone;
}

// -- the dialpad -------------------------------------------------------------

const _eastern = '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹';

/// Arabic-Indic digits as Western digits (the API reads both).
String westernDigits(String text) => text.split('').map((ch) {
      final index = _eastern.indexOf(ch);
      return index == -1 ? ch : '${index % 10}';
    }).join();

/// What a phone dials: `+` first only, digits, `*` and `#`.
String dialable(String text) {
  final kept = westernDigits(text).replaceAll(RegExp(r'[^0-9+*#]'), '');
  if (kept.isEmpty) return kept;
  return kept[0] + kept.substring(1).replaceAll('+', '');
}

bool isDialable(String text) {
  final value = dialable(text);
  return RegExp(r'^\+?[0-9*#]{3,20}$').hasMatch(value) &&
      value.replaceAll(RegExp(r'[^0-9]'), '').length >= 3;
}

/// One dialpad key: digits, `*`, `#`, `+` only at the start, at most 20.
String pressKey(String current, String key) {
  if (current.length >= 20) return current;
  if (key == '+') return current.isEmpty ? '+' : current;
  if (!RegExp(r'^[0-9*#]$').hasMatch(key)) return current;
  return current + key;
}

/// A long press of 0 types `+` on an empty number, as phone dialers do.
String longPressZero(String current) =>
    current.isEmpty ? '+' : pressKey(current, '0');

String backspace(String current) =>
    current.isEmpty ? current : current.substring(0, current.length - 1);

String pasteNumber(String current, String pasted) {
  final value = dialable(current + pasted);
  return value.length > 20 ? value.substring(0, 20) : value;
}

/// The `tel:` URI for a number, or null when it cannot be dialled.
Uri? telUri(String text) => isDialable(text)
    ? Uri(scheme: 'tel', path: dialable(text).replaceAll('#', '%23'))
    : null;

/// `1:05`, `1:02:03`; empty for no duration.
String durationText(int? seconds) {
  if (seconds == null || seconds < 0) return '';
  final h = seconds ~/ 3600, m = (seconds % 3600) ~/ 60, s = seconds % 60;
  String two(int v) => v.toString().padLeft(2, '0');
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '$m:${two(s)}';
}

/// `2:30`, `150` or `3m` as seconds, or null.
int? parseDuration(String text) {
  final value = westernDigits(text).trim();
  if (value.isEmpty) return null;
  final clock = RegExp(r'^(\d{1,3}):([0-5]?\d)$').firstMatch(value);
  if (clock != null) {
    return int.parse(clock.group(1)!) * 60 + int.parse(clock.group(2)!);
  }
  final minutes =
      RegExp(r'^(\d{1,3})\s*m(in)?$', caseSensitive: false).firstMatch(value);
  if (minutes != null) return int.parse(minutes.group(1)!) * 60;
  if (RegExp(r'^\d{1,5}$').hasMatch(value)) {
    final seconds = int.parse(value);
    return seconds <= 86400 ? seconds : null;
  }
  return null;
}

/// Seconds away at the dialer, rounded and capped at a day.
int elapsedSeconds(DateTime start, DateTime end) {
  final seconds = (end.difference(start).inMilliseconds / 1000).round();
  return seconds.clamp(0, 86400);
}
