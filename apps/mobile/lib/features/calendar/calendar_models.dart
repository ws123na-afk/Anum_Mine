import '../../data/json_read.dart';

/// Kinds and replies as the API names them (docs/calendar.md).
const eventKinds = ['meeting', 'call', 'review', 'reminder', 'focus'];
const attendeeResponses = ['invited', 'accepted', 'tentative', 'declined'];

/// Repeat frequencies and weekdays of the RFC 5545 subset the API stores
/// (docs/calendar.md#recurrence).
const repeatFrequencies = ['daily', 'weekly', 'monthly', 'yearly'];
const weekdayCodes = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];

/// Minutes-before choices for reminders.
const reminderPresets = [0, 5, 10, 15, 30, 60, 120, 1440, 2880, 10080];
const maxReminders = 5;

/// Which occurrences of a series an edit or a deletion applies to.
enum EditScope { thisOne, following, all }

extension EditScopeApi on EditScope {
  String get api => switch (this) {
        EditScope.thisOne => 'this',
        EditScope.following => 'following',
        EditScope.all => 'all',
      };
}

/// The repeat rule of a series (docs/calendar.md#recurrence).
class Recurrence {
  const Recurrence({
    required this.freq,
    this.interval = 1,
    this.byWeekday = const [],
    this.byMonthday,
    this.bySetpos,
    this.until,
    this.count,
  });

  final String freq;
  final int interval;
  final List<String> byWeekday;
  final int? byMonthday, bySetpos, count;

  /// The last day an occurrence may start on, as YYYY-MM-DD.
  final String? until;

  Recurrence copyWith({
    String? freq,
    int? interval,
    List<String>? byWeekday,
    int? byMonthday,
    int? bySetpos,
    String? until,
    int? count,
    bool clearEnd = false,
    bool clearMonthly = false,
  }) =>
      Recurrence(
        freq: freq ?? this.freq,
        interval: interval ?? this.interval,
        byWeekday: byWeekday ?? this.byWeekday,
        byMonthday: clearMonthly ? null : byMonthday ?? this.byMonthday,
        bySetpos: clearMonthly ? null : bySetpos ?? this.bySetpos,
        until: clearEnd ? null : until ?? this.until,
        count: clearEnd ? null : count ?? this.count,
      );

  Map<String, Object?> toJson() => {
        'freq': freq,
        'interval': interval,
        'by_weekday': byWeekday,
        if (byMonthday != null) 'by_monthday': byMonthday,
        if (bySetpos != null) 'by_setpos': bySetpos,
        if (until != null) 'until': until,
        if (count != null) 'count': count,
      };

  factory Recurrence.fromJson(JsonReader j) => Recurrence(
        freq: j.string('freq'),
        interval: j.optInt('interval') ?? 1,
        byWeekday: j.optStrings('by_weekday') ?? const [],
        byMonthday: j.optInt('by_monthday'),
        bySetpos: j.optInt('by_setpos'),
        until: j.optString('until'),
        count: j.optInt('count'),
      );

  @override
  bool operator ==(Object other) =>
      other is Recurrence && other.toJson().toString() == toJson().toString();

  @override
  int get hashCode => toJson().toString().hashCode;
}

class Attendee {
  const Attendee({
    this.id,
    required this.name,
    this.email,
    this.role,
    this.response = 'invited',
  });

  /// Null for an attendee added on this device and not saved yet.
  final String? id;
  final String name;
  final String? email, role;
  final String response;

  /// `SK` for Sajid Kamboh; one letter for one word.
  String get initials {
    final words = name.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty);
    final letters = [
      for (final w in words.take(2)) String.fromCharCode(w.runes.first)
    ];
    return letters.join().toUpperCase();
  }

  Map<String, Object?> toJson() => {
        if (id != null) 'id': id,
        'name': name,
        if (email != null && email!.isNotEmpty) 'email': email,
        if (role != null && role!.isNotEmpty) 'role': role,
        'response': response,
      };

  factory Attendee.fromJson(JsonReader j) => Attendee(
        id: j.optString('id'),
        name: j.string('name'),
        email: j.optString('email'),
        role: j.optString('role'),
        response: j.optString('response') ?? 'invited',
      );
}

class CalendarEvent {
  const CalendarEvent({
    required this.id,
    required this.title,
    required this.kind,
    required this.status,
    required this.startsAt,
    required this.endsAt,
    this.description,
    this.location,
    this.conferenceUrl,
    this.phone,
    this.attendees = const [],
    this.canManage = false,
    this.connectionId,
    this.syncState,
    this.invitationsPending = false,
    this.allDay = false,
    this.timezone,
    this.recurrence,
    this.reminders = const [],
    this.occurrenceStart,
    this.occurrenceKey,
    this.isRecurring = false,
    this.overridden = false,
    this.overrideId,
    this.startDate,
    this.endDate,
  });

  final String id, title, kind, status;
  final String? description, location, conferenceUrl, phone;
  final DateTime startsAt, endsAt;
  final List<Attendee> attendees;
  final bool canManage;

  /// Repeating, all-day events and reminders (docs/calendar.md#recurrence).
  /// An item of a series is one occurrence: [occurrenceStart] is its
  /// original start (what scoped edits and deletions name), [overridden]
  /// marks one changed on its own.
  final bool allDay, isRecurring, overridden;
  final String? timezone, occurrenceKey, overrideId, startDate, endDate;
  final Recurrence? recurrence;
  final List<int> reminders;
  final DateTime? occurrenceStart;

  /// Tells one shown occurrence from another.
  String get key => occurrenceKey ?? id;

  /// Whole days an all-day event covers.
  int get allDayLength {
    final first = startDate, last = endDate;
    if (first == null || last == null) return 1;
    final a = DateTime.parse(first), b = DateTime.parse(last);
    return b.difference(a).inDays + 1;
  }

  /// Calendar sync (docs/calendar.md#sync): the connected Google or Microsoft
  /// calendar this event mirrors, and `synced`, `pending_push` or `local`.
  final String? connectionId, syncState;

  /// Attendees with an email address have no approved invitation for the event
  /// as it is now.
  final bool invitationsPending;

  /// `synced`, `waiting` (to be sent) or `local` (only in ANUM).
  String get syncKind => connectionId == null
      ? 'local'
      : syncState == 'synced'
          ? 'synced'
          : syncState == 'pending_push'
              ? 'waiting'
              : 'local';

  /// Attendees an invitation would reach.
  List<String> get invitees => [
        for (final a in attendees)
          if (a.email != null && a.email!.isNotEmpty) a.email!
      ];

  bool get cancelled => status == 'cancelled';

  /// A meeting link to join, only https.
  Uri? get joinLink {
    final url = conferenceUrl;
    if (url == null) return null;
    final uri = Uri.tryParse(url);
    return uri != null && uri.scheme == 'https' ? uri : null;
  }

  Uri? get callLink {
    final number = phone?.replaceAll(RegExp(r'[^0-9+]'), '');
    return number == null || number.isEmpty
        ? null
        : Uri(scheme: 'tel', path: number);
  }

  int count(String response) =>
      attendees.where((a) => a.response == response).length;

  factory CalendarEvent.fromJson(JsonReader j) => CalendarEvent(
        id: j.string('id'),
        title: j.string('title'),
        kind: j.optString('kind') ?? 'meeting',
        status: j.optString('status') ?? 'scheduled',
        startsAt: j.date('starts_at'),
        endsAt: j.date('ends_at'),
        description: j.optString('description'),
        location: j.optString('location'),
        conferenceUrl: j.optString('conference_url'),
        phone: j.optString('phone'),
        attendees: j.optObjects('attendees', Attendee.fromJson),
        canManage: j.optBool('can_manage') ?? false,
        connectionId: j.optString('connection_id'),
        syncState: j.optString('sync_state'),
        invitationsPending: j.optBool('invitations_pending') ?? false,
        allDay: j.optBool('all_day') ?? false,
        timezone: j.optString('timezone'),
        recurrence: j.optObject('recurrence') == null
            ? null
            : Recurrence.fromJson(j.optObject('recurrence')!),
        reminders: [
          for (final value in j.optList('reminders') ?? const <Object?>[])
            if (value is num) value.toInt()
        ],
        occurrenceStart: j.optString('occurrence_start') == null
            ? null
            : j.date('occurrence_start'),
        occurrenceKey: j.optString('occurrence_key'),
        isRecurring: j.optBool('is_recurring') ?? false,
        overridden: j.optBool('overridden') ?? false,
        overrideId: j.optString('override_id'),
        startDate: j.optString('start_date'),
        endDate: j.optString('end_date'),
      );
}

/// One connected Google or Microsoft calendar (docs/calendar.md#sync).
class SyncConnection {
  const SyncConnection({
    required this.id,
    required this.provider,
    required this.providerName,
    required this.email,
    required this.status,
    this.lastSyncAt,
    this.lastError,
    this.eventsSynced = 0,
  });

  final String id, provider, providerName, email, status;
  final DateTime? lastSyncAt;
  final String? lastError;
  final int eventsSynced;

  bool get healthy => status == 'connected';
  bool get needsReconnect => status == 'reauth_required';

  factory SyncConnection.fromJson(JsonReader j) => SyncConnection(
        id: j.string('id'),
        provider: j.string('provider'),
        providerName: j.optString('provider_name') ?? j.string('provider'),
        email: j.string('email'),
        status: j.optString('status') ?? 'connected',
        lastSyncAt:
            j.optString('last_sync_at') == null ? null : j.date('last_sync_at'),
        lastError: j.optString('last_error'),
        eventsSynced: j.optInt('events_synced') ?? 0,
      );
}

/// What sync looks like to this person: the connections, and whether they
/// may sync or change them (owners).
class CalendarSyncStatus {
  const CalendarSyncStatus(this.connections, {this.canManage = false});
  final List<SyncConnection> connections;
  final bool canManage;

  /// New events go to the oldest connection that works.
  SyncConnection? get target {
    for (final c in connections) {
      if (c.healthy) return c;
    }
    return null;
  }

  SyncConnection? get troubled {
    for (final c in connections) {
      if (!c.healthy) return c;
    }
    return null;
  }
}

class EventPage {
  const EventPage(this.items, {this.truncated = false, this.canWrite = false});
  final List<CalendarEvent> items;
  final bool truncated, canWrite;
}

/// What a suggested next step does: start a task, or open a screen.
class NextStep {
  const NextStep({
    required this.id,
    required this.title,
    required this.detail,
    required this.icon,
    required this.kind,
    this.taskTitle,
    this.taskPrompt,
    this.destination,
  });

  final String id, title, detail, icon, kind;
  final String? taskTitle, taskPrompt, destination;

  bool get isTask => kind == 'task';

  factory NextStep.fromJson(JsonReader j) {
    final action = j.object('action');
    return NextStep(
      id: j.string('id'),
      title: j.string('title'),
      detail: j.optString('detail') ?? '',
      icon: j.optString('icon') ?? 'agenda',
      kind: action.string('kind'),
      taskTitle: action.optString('title'),
      taskPrompt: action.optString('prompt'),
      destination: action.optString('destination'),
    );
  }
}

/// The fields of the add/edit form.
class EventDraft {
  EventDraft({
    required this.title,
    required this.kind,
    required this.startsAt,
    required this.endsAt,
    this.description,
    this.location,
    this.conferenceUrl,
    this.phone,
    this.attendees = const [],
    this.allDay = false,
    this.timezone,
    this.recurrence,
    this.reminders = const [],
  });

  String title, kind;
  DateTime startsAt, endsAt;
  String? description, location, conferenceUrl, phone;
  List<Attendee> attendees;

  /// Repeating, all-day and reminders (docs/calendar.md#recurrence). For an
  /// all-day event [startsAt] and [endsAt] are local days (the end inclusive).
  bool allDay;
  String? timezone;
  Recurrence? recurrence;
  List<int> reminders;

  factory EventDraft.from(CalendarEvent e) => EventDraft(
        title: e.title,
        kind: e.kind,
        startsAt: e.allDay && e.startDate != null
            ? DateTime.parse(e.startDate!)
            : e.startsAt.toLocal(),
        endsAt: e.allDay && e.endDate != null
            ? DateTime.parse(e.endDate!)
            : e.endsAt.toLocal(),
        description: e.description,
        location: e.location,
        conferenceUrl: e.conferenceUrl,
        phone: e.phone,
        attendees: e.attendees,
        allDay: e.allDay,
        timezone: e.timezone,
        recurrence: e.recurrence,
        reminders: [...e.reminders],
      );

  static String? _blank(String? value) =>
      value == null || value.trim().isEmpty ? null : value.trim();

  /// The API body. [scope] and [occurrenceStart] address one occurrence of a
  /// series; one occurrence cannot carry a rule of its own.
  Map<String, Object?> toJson({EditScope? scope, DateTime? occurrenceStart}) =>
      {
        'title': title.trim(),
        'kind': kind,
        'all_day': allDay,
        if (allDay) 'start_date': dayText(startsAt),
        if (allDay) 'end_date': dayText(endsAt),
        'starts_at': allDay
            ? DateTime(startsAt.year, startsAt.month, startsAt.day)
                .toUtc()
                .toIso8601String()
            : startsAt.toUtc().toIso8601String(),
        'ends_at': allDay
            ? DateTime(endsAt.year, endsAt.month, endsAt.day, 23, 59)
                .toUtc()
                .toIso8601String()
            : endsAt.toUtc().toIso8601String(),
        if (timezone != null) 'timezone': timezone,
        if (scope != EditScope.thisOne) 'recurrence': recurrence?.toJson(),
        'reminders': ([...reminders.toSet()]..sort()),
        'description': _blank(description),
        'location': _blank(location),
        'conference_url': _blank(conferenceUrl),
        'phone': _blank(phone),
        'attendees': [for (final a in attendees) a.toJson()],
        if (scope != null) 'scope': scope.api,
        if (occurrenceStart != null)
          'occurrence_start': occurrenceStart.toUtc().toIso8601String(),
      };
}

/// A local day as YYYY-MM-DD (the API's all-day dates).
String dayText(DateTime value) =>
    '${value.year.toString().padLeft(4, '0')}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

DateTime dayOf(DateTime value) {
  final l = value.toLocal();
  return DateTime(l.year, l.month, l.day);
}
