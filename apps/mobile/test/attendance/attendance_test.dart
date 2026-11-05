// Attendance on the phone (docs/attendance.md#mobile) against a fake API
// with the API's JSON shapes (packages/contracts/src/attendance.ts): today's
// status and check in and out with an idempotency key, the week history and
// its paging, writing and editing a daily report with its version, a
// correction with a reason, and every state from 320 px Arabic at 200
// percent to a tablet.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/attendance/attendance_controller.dart';
import 'package:anum_mobile/features/attendance/attendance_data.dart';
import 'package:anum_mobile/features/attendance/attendance_screen.dart';
import 'package:anum_mobile/features/attendance/attendance_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> eventJson(String id, String kind, String at) => {
      'id': id,
      'kind': kind,
      'occurred_at': at,
      'received_at': at,
      'time_zone': 'Asia/Dubai',
      'site_id': kind == 'arrive' ? 'asite_1' : null,
      'source': 'web',
      'quality': 'ok',
      'status': 'active',
      'note': null,
    };

Map<String, Object?> dayJson(String day,
        {int worked = 0, List<String> flags = const []}) =>
    {
      'day': day,
      'time_zone': 'Asia/Dubai',
      'presence_seconds': worked,
      'remote_seconds': 0,
      'break_seconds': 0,
      'worked_seconds': worked,
      'verified_seconds': 0,
      'estimated_seconds': 0,
      'open_seconds': 0,
      'scheduled_seconds': 28800,
      'overtime_seconds': null,
      'first_arrival': worked > 0 ? '${day}T05:00:00Z' : null,
      'last_departure': worked > 0 ? '${day}T13:00:00Z' : null,
      'site_ids': <Object?>[],
      'flags': flags,
      'segments': <Object?>[],
      'reduced': false,
    };

Map<String, Object?> reportJson(String day, {int version = 1}) => {
      'id': 'arep_$day',
      'day': day,
      'project': 'Warehouse move',
      'tasks_done': ['Packed shelves'],
      'tasks_pending': ['Label boxes'],
      'site_visits': [
        {'site_id': null, 'name': 'Jebel Ali', 'note': null}
      ],
      'issues': null,
      'time_spent_minutes': 240,
      'priority': 'normal',
      'follow_up_on': null,
      'notes': null,
      'file_ids': <Object?>[],
      'drafted_with_ai': false,
      'version': version,
      'created_at': '${day}T14:00:00Z',
      'updated_at': '${day}T14:00:00Z',
    };

class AttendanceApi {
  AttendanceApi({this.empty = false});
  bool empty;
  bool checkedIn = false;
  final events = <Map<String, Object?>>[];
  final reports = <String, Map<String, Object?>>{};
  final corrections = <Map<String, Object?>>[];
  late final api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    const base = '/api/v1/attendance';
    if (r.method == 'GET' && path == '$base/today') {
      return ok({
        'day': '2026-11-05',
        'time_zone': 'Asia/Dubai',
        'status': {
          'at_site': checkedIn,
          'site_id': checkedIn ? 'asite_1' : null,
          'on_break': false,
          'remote': false,
          'since': checkedIn ? '2026-11-05T05:00:00Z' : null,
        },
        'totals': checkedIn ? dayJson('2026-11-05', worked: 3600) : null,
        'events': events,
        'tracking_paused': false,
        'location_consent': false,
        'profile_saved': true,
      });
    }
    if (r.method == 'GET' && path == '$base/sites') {
      return okList([
        {
          'id': 'asite_1',
          'name': 'Head office',
          'kind': 'workplace',
          'address_label': null,
          'radius_m': 150,
          'archived': false,
          'created_at': '2026-11-01T08:00:00Z',
          'updated_at': '2026-11-01T08:00:00Z',
        }
      ]);
    }
    if (r.method == 'POST' && path == '$base/check') {
      final kind = r.body!['kind']! as String;
      checkedIn = kind == 'arrive';
      final event =
          eventJson('aev_${events.length + 1}', kind, '2026-11-05T05:00:00Z');
      events.add(event);
      return ok({'event': event, 'duplicate': false}, 201);
    }
    if (r.method == 'GET' && path == '$base/days') {
      final from = DateTime.parse(r.uri.queryParameters['from']!);
      return okList([
        for (var i = 0; i < 7; i++)
          dayJson(
              from.add(Duration(days: i)).toIso8601String().substring(0, 10),
              worked: empty ? 0 : (i == 2 ? 28800 : 0),
              flags: !empty && i == 2 ? ['missing_checkout'] : const []),
      ]);
    }
    if (r.method == 'GET' && path == '$base/daily-reports') {
      return ok({'items': reports.values.toList(), 'total': reports.length});
    }
    if (path.startsWith('$base/daily-reports/')) {
      final day = path.split('/').last;
      if (r.method == 'GET') {
        final found = reports[day];
        return found == null
            ? refuse(404, 'No daily report for that day yet.')
            : ok(found);
      }
      if (r.method == 'PUT') {
        final current = reports[day];
        if (current != null &&
            r.body!['expected_version'] != current['version']) {
          return refuse(409, 'This report changed in another tab. Reload it.');
        }
        final saved = {
          ...reportJson(day, version: ((current?['version'] as int?) ?? 0) + 1),
          'project': r.body!['project'],
          'tasks_done': r.body!['tasks_done'],
          'tasks_pending': r.body!['tasks_pending'],
          'site_visits': r.body!['site_visits'],
        };
        reports[day] = saved;
        return ok(saved);
      }
      if (r.method == 'DELETE') {
        reports.remove(day);
        return const ApiResponse(statusCode: 204);
      }
    }
    if (r.method == 'GET' && path == '$base/corrections') {
      return ok({'items': corrections, 'total': corrections.length});
    }
    if (r.method == 'POST' && path == '$base/corrections') {
      final correction = {
        'id': 'acor_${corrections.length + 1}',
        'action': r.body!['action'],
        'event_id': r.body!['event_id'],
        'replacement_event_id': 'aev_9',
        'before': null,
        'after': {
          'kind': r.body!['kind'],
          'occurred_at': r.body!['occurred_at'],
          'site_id': null
        },
        'reason': r.body!['reason'],
        'created_at': '2026-11-05T09:00:00Z',
      };
      corrections.insert(0, correction);
      return ok(correction, 201);
    }
    return null;
  }
}

Future<void> pumpAttendance(WidgetTester tester, AttendanceApi fake,
    {Size size = phone,
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  final controller = AttendanceController(
      ApiAttendanceRepository(fake.api.client()),
      now: () => DateTime(2026, 11, 5, 9));
  addTearDown(controller.dispose);
  await pumpModule(tester, AttendanceScreen(controller: controller),
      size: size, textScale: textScale, locale: locale);
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(AttendanceStrings.catalogsMatch, isTrue);
    expect(const AttendanceStrings(false).hours(27000), '7 h 30 min');
  });

  group('repository', () {
    test('check sends kind, site and idempotency key', () async {
      final fake = AttendanceApi();
      final repo = ApiAttendanceRepository(fake.api.client());
      final event = await repo.check('arrive',
          idempotencyKey: 'att_example_key', siteId: 'asite_1');
      expect(event.kind, 'arrive');
      expect(fake.api.requests.last.body, {
        'kind': 'arrive',
        'site_id': 'asite_1',
        'idempotency_key': 'att_example_key'
      });
    });

    test('a day without a report reads as none; lists unwrap', () async {
      final fake = AttendanceApi();
      final repo = ApiAttendanceRepository(fake.api.client());
      expect(await repo.report('2026-11-05'), isNull);
      expect((await repo.sites()).single.name, 'Head office');
      final days = await repo.days('2026-10-30', '2026-11-05');
      expect(days, hasLength(7));
      expect(days[2].flags, ['missing_checkout']);
    });

    test('corrections page by offset even though the API omits it', () async {
      final fake = AttendanceApi();
      for (var i = 0; i < 3; i++) {
        fake.corrections.add({
          'id': 'acor_$i',
          'action': 'add',
          'event_id': null,
          'replacement_event_id': null,
          'before': null,
          'after': null,
          'reason': 'Forgot',
          'created_at': '2026-11-05T09:00:00Z',
        });
      }
      final page = await ApiAttendanceRepository(fake.api.client())
          .corrections(token: '2');
      expect(fake.api.requests.last.uri.queryParameters['offset'], '2');
      expect(page.next, isNull);
    });

    test('other refusals keep their status', () async {
      final fake = AttendanceApi();
      fake.api.failWith = 409;
      fake.api.failMessage =
          'That record was already changed. Refresh to see the current one.';
      await expectLater(
          ApiAttendanceRepository(fake.api.client()).report('2026-11-05'),
          throwsA(
              isA<ApiException>().having((e) => e.statusCode, 'status', 409)));
    });
  });

  group('screen', () {
    testWidgets('check in, then the status and today\'s record',
        (tester) async {
      final fake = AttendanceApi();
      await pumpAttendance(tester, fake);
      expect(find.text('Not checked in'), findsOneWidget);
      await tester.tap(find.byKey(const Key('attendance-site')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Head office').last);
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('check-arrive')));
      final sent =
          fake.api.sent('POST', '/api/v1/attendance/check').single.body!;
      expect(sent['kind'], 'arrive');
      expect(sent['site_id'], 'asite_1');
      expect(sent['idempotency_key'], startsWith('att'));
      expect(find.textContaining('Checked in at Head office'), findsOneWidget);
      expect(find.byKey(const Key('check-leave')), findsOneWidget);
      expect(find.byKey(const Key('event-aev_1')), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('writes a daily report, then edits it with its version',
        (tester) async {
      final fake = AttendanceApi();
      await pumpAttendance(tester, fake);
      await tapVisible(tester, find.byKey(const Key('attendance-write-today')));
      await tester.enterText(
          find.byKey(const Key('field-project')), 'Warehouse move');
      await tester.enterText(find.byKey(const Key('field-tasks_done')),
          'Packed shelves\n\nMoved desks');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      var body = fake.api
          .sent('PUT', '/api/v1/attendance/daily-reports/2026-11-05')
          .single
          .body!;
      expect(body['tasks_done'], ['Packed shelves', 'Moved desks']);
      expect(body['expected_version'], isNull);

      await tapVisible(tester, find.byKey(const Key('tab-reports')));
      await tapVisible(tester, find.byKey(const Key('report-2026-11-05')));
      await tester.enterText(
          find.byKey(const Key('field-project')), 'Warehouse move B');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      body = fake.api
          .sent('PUT', '/api/v1/attendance/daily-reports/2026-11-05')
          .last
          .body!;
      expect(body['expected_version'], 1);
      expect(body['project'], 'Warehouse move B');
      expectClean(tester);
    });

    testWidgets('history by week, then the earlier week', (tester) async {
      final fake = AttendanceApi();
      await pumpAttendance(tester, fake);
      await tapVisible(tester, find.byKey(const Key('tab-history')));
      expect(find.text('No check out'), findsOneWidget);
      expect(find.byKey(const Key('week-later')), findsNothing);
      await tapVisible(tester, find.byKey(const Key('week-earlier')));
      final query = fake.api
          .sent('GET', '/api/v1/attendance/days')
          .last
          .uri
          .queryParameters;
      expect(query, {'from': '2026-10-23', 'to': '2026-10-29'});
      expect(find.byKey(const Key('week-later')), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('adds a missing record as a correction with a reason',
        (tester) async {
      final fake = AttendanceApi();
      await pumpAttendance(tester, fake);
      await tapVisible(tester, find.byKey(const Key('tab-corrections')));
      expect(find.byKey(const Key('corrections-empty')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('attendance-add-missing')));
      await tester.enterText(find.byKey(const Key('field-time')), '08:15');
      await tester.enterText(
          find.byKey(const Key('field-reason')), 'Forgot to check in');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final body =
          fake.api.sent('POST', '/api/v1/attendance/corrections').single.body!;
      expect(body['action'], 'add');
      expect(body['kind'], 'arrive');
      expect(body['reason'], 'Forgot to check in');
      expect(DateTime.parse(body['occurred_at']! as String).isUtc, isTrue);
      expect(find.text('Reason: Forgot to check in'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('empty history offers to add a missing record', (tester) async {
      await pumpAttendance(tester, AttendanceApi(empty: true));
      await tapVisible(tester, find.byKey(const Key('tab-history')));
      expect(find.text('No records in these days.'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('tab-reports')));
      expect(find.byKey(const Key('reports-empty')), findsOneWidget);
    });

    testWidgets('a failed load says what failed', (tester) async {
      final fake = AttendanceApi();
      fake.api.failWith = 500;
      fake.api.failMessage = 'Attendance could not be read.';
      await pumpAttendance(tester, fake);
      expect(find.byKey(const Key('records-error')), findsOneWidget);
    });

    testWidgets('a viewer is told who can use attendance', (tester) async {
      final fake = AttendanceApi();
      fake.api.failWith = 403;
      fake.api.failMessage =
          'Attendance is for workspace owners and members. Ask a workspace owner to change your role from viewer to member.';
      await pumpAttendance(tester, fake);
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
    });

    testWidgets('Arabic, 320 px, 200 percent text, then a tablet',
        (tester) async {
      final fake = AttendanceApi()
        ..reports['2026-11-04'] = reportJson('2026-11-04');
      await pumpAttendance(tester, fake,
          size: narrow, textScale: 2, locale: const Locale('ar'));
      expect(find.text('الحضور'), findsWidgets);
      expectClean(tester);
      for (final tab in ['history', 'reports', 'corrections']) {
        await tapVisible(tester, find.byKey(Key('tab-$tab')));
        expectClean(tester);
      }
      await pumpAttendance(tester, AttendanceApi(), size: tablet);
      expectClean(tester);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}
