import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'calendar_models.dart';

/// The workspace calendar over the API (docs/calendar.md). Viewers read;
/// members and owners add events and change their own; owners change any.
abstract interface class CalendarRepository {
  Future<EventPage> events(DateTime start, DateTime end);

  /// One event; for a series, [occurrenceStart] opens that occurrence.
  Future<CalendarEvent> event(String id, {DateTime? occurrenceStart});
  Future<CalendarEvent> create(EventDraft draft);
  Future<CalendarEvent> update(String id, Map<String, Object?> changes);
  Future<CalendarEvent> respond(String id, String attendeeId, String response);

  /// Deletes an event, or with a scope one occurrence of a series or this
  /// and the following ones (docs/calendar.md#recurrence).
  Future<void> delete(String id, {EditScope? scope, DateTime? occurrenceStart});
  Future<List<NextStep>> nextSteps();

  /// Starts a suggested step as a task; returns its id.
  Future<String> createTask(String title, String prompt);

  /// The connected Google or Microsoft calendars and what this person may do.
  Future<CalendarSyncStatus> sync();

  /// One sync pass now (owners); returns the connection as it is afterwards.
  Future<SyncConnection> syncNow(String connectionId);

  /// Asks for the event's invitations. Nothing is sent: an owner approves the
  /// exact event and list first. Returns the addresses the request names.
  Future<List<String>> requestInvitations(String eventId);
}

class ApiCalendarRepository implements CalendarRepository {
  const ApiCalendarRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/calendar';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<EventPage> events(DateTime start, DateTime end) async {
    final value = JsonReader(await api.request(
      'GET',
      Uri(path: '$_base/events', queryParameters: {
        'start': start.toUtc().toIso8601String(),
        'end': end.toUtc().toIso8601String(),
      }).toString(),
    ));
    return EventPage(value.objects('items', CalendarEvent.fromJson),
        truncated: value.optBool('truncated') ?? false,
        canWrite: value.optBool('can_write') ?? false);
  }

  @override
  Future<CalendarEvent> event(String id, {DateTime? occurrenceStart}) async {
    final query = occurrenceStart == null
        ? ''
        : '?occurrence_start=${Uri.encodeQueryComponent(occurrenceStart.toUtc().toIso8601String())}';
    return CalendarEvent.fromJson(
        JsonReader(await api.request('GET', '$_base/events/${_id(id)}$query')));
  }

  @override
  Future<CalendarEvent> create(EventDraft draft) async =>
      CalendarEvent.fromJson(JsonReader(
          await api.request('POST', '$_base/events', body: draft.toJson())));

  @override
  Future<CalendarEvent> update(String id, Map<String, Object?> changes) async =>
      CalendarEvent.fromJson(JsonReader(await api
          .request('PATCH', '$_base/events/${_id(id)}', body: changes)));

  @override
  Future<CalendarEvent> respond(
          String id, String attendeeId, String response) async =>
      CalendarEvent.fromJson(JsonReader(await api.request('PUT',
          '$_base/events/${_id(id)}/attendees/${_id(attendeeId)}/response',
          body: {'response': response})));

  @override
  Future<void> delete(String id,
      {EditScope? scope, DateTime? occurrenceStart}) async {
    final query = scope == null || scope == EditScope.all
        ? ''
        : '?scope=${scope.api}&occurrence_start=${Uri.encodeQueryComponent((occurrenceStart ?? DateTime.now()).toUtc().toIso8601String())}';
    await api.request('DELETE', '$_base/events/${_id(id)}$query');
  }

  @override
  Future<List<NextStep>> nextSteps() async =>
      JsonReader(await api.request('GET', '$_base/next-steps'))
          .objects('items', NextStep.fromJson);

  @override
  Future<CalendarSyncStatus> sync() async {
    final value = JsonReader(await api.request('GET', '$_base/sync'));
    return CalendarSyncStatus(
        value.objects('connections', SyncConnection.fromJson),
        canManage: value.optBool('can_manage') ?? false);
  }

  @override
  Future<SyncConnection> syncNow(String connectionId) async =>
      SyncConnection.fromJson(JsonReader(await api.request(
              'POST', '$_base/sync/connections/${_id(connectionId)}/sync'))
          .object('connection'));

  @override
  Future<List<String>> requestInvitations(String eventId) async =>
      JsonReader(await api.request(
              'POST', '$_base/events/${_id(eventId)}/invitations'))
          .strings('attendees');

  @override
  Future<String> createTask(String title, String prompt) async =>
      JsonReader(await api.request('POST', '/api/v1/tasks',
              body: {'title': title, 'prompt': prompt}))
          .string('id');
}
