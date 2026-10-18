import '../../data/json_read.dart';

/// Phone calls (docs/calls.md): the API's shapes and the dialpad's pure
/// rules, kept free of Flutter so they are tested on their own.
const callsPageSize = 25;
const logOutcomes = ['answered', 'no_answer', 'busy', 'voicemail'];

class AiConsent {
  const AiConsent(
      {required this.grantedAt, required this.recordedBy, required this.method});
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
      consent: consent == null ? null : AiConsent.fromJson(consent),
      optedOut: j.optBool('opted_out') ?? false,
      canEdit: j.optBool('can_edit') ?? false,
    );
  }
  final String id, name, phone, source;
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
    );
  }
  final bool canLog, canManage, canCreateTasks, modelReady, providerConnected;
  final String? providerProblem;
}

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
  final minutes = RegExp(r'^(\d{1,3})\s*m(in)?$', caseSensitive: false)
      .firstMatch(value);
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
