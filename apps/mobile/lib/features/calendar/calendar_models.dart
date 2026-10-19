import '../../data/json_read.dart';

/// Kinds and replies as the API names them (docs/calendar.md).
const eventKinds = ['meeting', 'call', 'review', 'reminder', 'focus'];
const attendeeResponses = ['invited', 'accepted', 'tentative', 'declined'];

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
  });

  final String id, title, kind, status;
  final String? description, location, conferenceUrl, phone;
  final DateTime startsAt, endsAt;
  final List<Attendee> attendees;
  final bool canManage;

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
  });

  String title, kind;
  DateTime startsAt, endsAt;
  String? description, location, conferenceUrl, phone;
  List<Attendee> attendees;

  factory EventDraft.from(CalendarEvent e) => EventDraft(
        title: e.title,
        kind: e.kind,
        startsAt: e.startsAt.toLocal(),
        endsAt: e.endsAt.toLocal(),
        description: e.description,
        location: e.location,
        conferenceUrl: e.conferenceUrl,
        phone: e.phone,
        attendees: e.attendees,
      );

  static String? _blank(String? value) =>
      value == null || value.trim().isEmpty ? null : value.trim();

  Map<String, Object?> toJson() => {
        'title': title.trim(),
        'kind': kind,
        'starts_at': startsAt.toUtc().toIso8601String(),
        'ends_at': endsAt.toUtc().toIso8601String(),
        'description': _blank(description),
        'location': _blank(location),
        'conference_url': _blank(conferenceUrl),
        'phone': _blank(phone),
        'attendees': [for (final a in attendees) a.toJson()],
      };
}

DateTime dayOf(DateTime value) {
  final l = value.toLocal();
  return DateTime(l.year, l.month, l.day);
}
