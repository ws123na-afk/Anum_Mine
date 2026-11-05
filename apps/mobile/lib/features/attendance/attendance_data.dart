import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';

/// Work attendance and daily reports (docs/attendance.md#api), the parts the
/// phone shows. Every record is the caller's own.

class AttendanceEvent {
  const AttendanceEvent({
    required this.id,
    required this.kind,
    required this.occurredAt,
    required this.source,
    required this.status,
    this.siteId,
    this.quality = 'ok',
  });
  factory AttendanceEvent.fromJson(JsonReader j) => AttendanceEvent(
        id: j.string('id'),
        kind: j.string('kind'),
        occurredAt: j.string('occurred_at'),
        siteId: j.optString('site_id'),
        source: j.optString('source') ?? 'web',
        quality: j.optString('quality') ?? 'ok',
        status: j.optString('status') ?? 'active',
      );
  final String id, kind, occurredAt, source, quality, status;
  final String? siteId;
}

class AttendanceDay {
  const AttendanceDay({
    required this.day,
    required this.workedSeconds,
    required this.presenceSeconds,
    required this.remoteSeconds,
    required this.breakSeconds,
    required this.openSeconds,
    this.firstArrival,
    this.lastDeparture,
    this.overtimeSeconds,
    this.flags = const [],
  });
  factory AttendanceDay.fromJson(JsonReader j) => AttendanceDay(
        day: j.string('day'),
        workedSeconds: j.optInt('worked_seconds') ?? 0,
        presenceSeconds: j.optInt('presence_seconds') ?? 0,
        remoteSeconds: j.optInt('remote_seconds') ?? 0,
        breakSeconds: j.optInt('break_seconds') ?? 0,
        openSeconds: j.optInt('open_seconds') ?? 0,
        overtimeSeconds: j.optInt('overtime_seconds'),
        firstArrival: j.optString('first_arrival'),
        lastDeparture: j.optString('last_departure'),
        flags: j.optStrings('flags') ?? const [],
      );
  final String day;
  final int workedSeconds, presenceSeconds, remoteSeconds, breakSeconds;
  final int openSeconds;
  final int? overtimeSeconds;
  final String? firstArrival, lastDeparture;
  final List<String> flags;
}

class AttendanceToday {
  const AttendanceToday({
    required this.day,
    required this.atSite,
    required this.onBreak,
    required this.remote,
    required this.events,
    required this.trackingPaused,
    this.siteId,
    this.since,
    this.totals,
  });
  factory AttendanceToday.fromJson(JsonReader j) {
    final status = j.object('status');
    return AttendanceToday(
      day: j.string('day'),
      atSite: status.optBool('at_site') ?? false,
      siteId: status.optString('site_id'),
      onBreak: status.optBool('on_break') ?? false,
      remote: status.optBool('remote') ?? false,
      since: status.optString('since'),
      totals: j.optObject('totals') == null
          ? null
          : AttendanceDay.fromJson(j.object('totals')),
      events: j.optObjects('events', AttendanceEvent.fromJson),
      trackingPaused: j.optBool('tracking_paused') ?? false,
    );
  }
  final String day;
  final bool atSite, onBreak, remote, trackingPaused;
  final String? siteId, since;
  final AttendanceDay? totals;
  final List<AttendanceEvent> events;

  bool get working => atSite || remote;
}

class AttendanceSite {
  const AttendanceSite(
      {required this.id, required this.name, required this.kind, required this.archived});
  factory AttendanceSite.fromJson(JsonReader j) => AttendanceSite(
      id: j.string('id'),
      name: j.string('name'),
      kind: j.optString('kind') ?? 'site',
      archived: j.optBool('archived') ?? false);
  final String id, name, kind;
  final bool archived;
}

class AttendanceCorrection {
  const AttendanceCorrection({
    required this.id,
    required this.action,
    required this.reason,
    required this.createdAt,
    this.beforeKind,
    this.beforeAt,
    this.afterKind,
    this.afterAt,
  });
  factory AttendanceCorrection.fromJson(JsonReader j) {
    final before = j.optObject('before');
    final after = j.optObject('after');
    return AttendanceCorrection(
      id: j.string('id'),
      action: j.string('action'),
      reason: j.string('reason'),
      createdAt: j.string('created_at'),
      beforeKind: before?.optString('kind'),
      beforeAt: before?.optString('occurred_at'),
      afterKind: after?.optString('kind'),
      afterAt: after?.optString('occurred_at'),
    );
  }
  final String id, action, reason, createdAt;
  final String? beforeKind, beforeAt, afterKind, afterAt;
}

class AttendanceReport {
  const AttendanceReport({
    required this.day,
    required this.version,
    required this.priority,
    this.project,
    this.tasksDone = const [],
    this.tasksPending = const [],
    this.issues,
    this.timeSpentMinutes,
    this.followUpOn,
    this.notes,
    this.fileIds = const [],
    this.siteVisits = const [],
    this.draftedWithAi = false,
  });
  factory AttendanceReport.fromJson(JsonReader j) => AttendanceReport(
        day: j.string('day'),
        version: j.integer('version'),
        project: j.optString('project'),
        tasksDone: j.optStrings('tasks_done') ?? const [],
        tasksPending: j.optStrings('tasks_pending') ?? const [],
        issues: j.optString('issues'),
        timeSpentMinutes: j.optInt('time_spent_minutes'),
        priority: j.optString('priority') ?? 'normal',
        followUpOn: j.optString('follow_up_on'),
        notes: j.optString('notes'),
        fileIds: j.optStrings('file_ids') ?? const [],
        siteVisits: [
          for (final visit in j.optList('site_visits') ?? const <Object?>[])
            if (visit is Map) jsonObject(visit, 'site_visits[]'),
        ],
        draftedWithAi: j.optBool('drafted_with_ai') ?? false,
      );
  final String day, priority;
  final int version;
  final String? project, issues, followUpOn, notes;
  final int? timeSpentMinutes;
  final List<String> tasksDone, tasksPending, fileIds;

  /// Kept as sent so an edit on the phone never drops them.
  final List<Map<String, Object?>> siteVisits;
  final bool draftedWithAi;
}

abstract interface class AttendanceRepository {
  Future<AttendanceToday> today();
  Future<List<AttendanceSite>> sites();
  Future<AttendanceEvent> check(String kind,
      {required String idempotencyKey, String? siteId});
  Future<List<AttendanceDay>> days(String from, String to);
  Future<RecordsChunk<AttendanceCorrection>> corrections({String? token});
  Future<AttendanceCorrection> correct(Map<String, Object?> body);
  Future<RecordsChunk<AttendanceReport>> reports(
      {String query, String? token});

  /// The report of [day], or null when none was written yet.
  Future<AttendanceReport?> report(String day);
  Future<AttendanceReport> saveReport(String day, Map<String, Object?> body);
  Future<void> deleteReport(String day);
}

class ApiAttendanceRepository implements AttendanceRepository {
  const ApiAttendanceRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/attendance';
  static const pageSize = 20;

  @override
  Future<AttendanceToday> today() async => AttendanceToday.fromJson(
      JsonReader(await api.request('GET', '$_base/today')));

  @override
  Future<List<AttendanceSite>> sites() async => recordsListBody(
      await api.request('GET', '$_base/sites'), AttendanceSite.fromJson);

  @override
  Future<AttendanceEvent> check(String kind,
          {required String idempotencyKey, String? siteId}) async =>
      AttendanceEvent.fromJson(JsonReader(await api.request(
              'POST', '$_base/check',
              body: {
                'kind': kind,
                'site_id': siteId,
                'idempotency_key': idempotencyKey
              }))
          .object('event'));

  @override
  Future<List<AttendanceDay>> days(String from, String to) async =>
      recordsListBody(
          await api.request(
              'GET', '$_base/days${recordsQuery({'from': from, 'to': to})}'),
          AttendanceDay.fromJson);

  @override
  Future<RecordsChunk<AttendanceCorrection>> corrections(
      {String? token}) async {
    final offset = int.tryParse(token ?? '') ?? 0;
    return RecordsChunk.offsetPage(
        JsonReader(await api.request('GET',
            '$_base/corrections${recordsQuery({'limit': pageSize, 'offset': offset})}')),
        AttendanceCorrection.fromJson,
        offset: offset);
  }

  @override
  Future<AttendanceCorrection> correct(Map<String, Object?> body) async =>
      AttendanceCorrection.fromJson(JsonReader(
          await api.request('POST', '$_base/corrections', body: body)));

  @override
  Future<RecordsChunk<AttendanceReport>> reports(
      {String query = '', String? token}) async {
    final offset = int.tryParse(token ?? '') ?? 0;
    return RecordsChunk.offsetPage(
        JsonReader(await api.request(
            'GET',
            '$_base/daily-reports${recordsQuery({
                  'q': query,
                  'limit': pageSize,
                  'offset': offset
                })}')),
        AttendanceReport.fromJson,
        offset: offset);
  }

  @override
  Future<AttendanceReport?> report(String day) async {
    try {
      return AttendanceReport.fromJson(JsonReader(await api.request(
          'GET', '$_base/daily-reports/${Uri.encodeComponent(day)}')));
    } on ApiException catch (error) {
      if (error.statusCode == 404) return null;
      rethrow;
    }
  }

  @override
  Future<AttendanceReport> saveReport(
          String day, Map<String, Object?> body) async =>
      AttendanceReport.fromJson(JsonReader(await api.request(
          'PUT', '$_base/daily-reports/${Uri.encodeComponent(day)}',
          body: body)));

  @override
  Future<void> deleteReport(String day) => api.request(
      'DELETE', '$_base/daily-reports/${Uri.encodeComponent(day)}');
}
