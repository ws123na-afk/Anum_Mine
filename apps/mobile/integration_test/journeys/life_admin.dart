import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/calendar/calendar_connections.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../test/attendance/attendance_test.dart' as attendance;
import '../../test/purchases/purchases_test.dart' as purchases;
import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// The life admin gaps closed on the phone (docs/audits/2026-11-12-product-review.md):
/// one journey each, through the whole app and its real HTTP transport.
///
/// * Calendar: connect Google from the phone (the sign-in opens in the
///   browser, the app finishes the request when it comes back), then choose
///   another calendar to sync (docs/calendar.md#connecting-on-the-phone).
/// * Purchases: edit a purchase, attach a receipt from the vault, move a
///   return and record its refund in cash (docs/purchases.md#mobile).
/// * Attendance: write a daily report and submit it after the review
///   (docs/attendance.md#mobile).
/// * Tyre planning: send a report by email; it waits for an approval
///   (docs/tyre-planning.md).

/// Answers [patterns] from a widget-test fake that speaks [ApiRequest] and
/// [ApiResponse] (the same JSON as the API).
void _serve(FakeAnumApi api, Map<String, List<String>> patterns,
    ApiResponse? Function(ApiRequest request) route) {
  FakeResponse answer(FakeRequest r) {
    final body = r.json;
    final response = route(ApiRequest(
        method: r.method,
        uri: r.uri,
        headers: r.headers,
        body: body is Map<String, Object?> ? body : null));
    if (response == null) {
      return FakeResponse.error(404, 'not_found', 'No fixture for $r');
    }
    final json = response.body;
    if (response.statusCode == 204 || json == null) {
      return FakeResponse.empty(status: response.statusCode);
    }
    // A list answer is a JSON array on the wire.
    if (json.length == 1 && json['data'] is List) {
      return FakeResponse.json(json['data'], status: response.statusCode);
    }
    return FakeResponse.json(json, status: response.statusCode);
  }

  for (final MapEntry(key: method, value: paths) in patterns.entries) {
    for (final path in paths) {
      api.on(method, path, answer);
    }
  }
}

Future<void> _tapKey(WidgetTester tester, String key,
    {FakeAnumApi? api}) async {
  await waitFor(tester, find.byKey(Key(key)), api: api);
  await tapOn(tester, find.byKey(Key(key)));
}

Future<void> _type(WidgetTester tester, String key, String text) async {
  final field = find.byKey(Key(key));
  await tester.ensureVisible(field);
  await tester.pump();
  await tester.enterText(field, text);
  FocusManager.instance.primaryFocus?.unfocus();
  await pumpFor(tester, const Duration(milliseconds: 200));
}

class _CalendarState {
  bool returned = false;
  bool connected = false;
  final chosen = <String>[];

  Map<String, Object?> get connection => {
        'id': 'cal_1',
        'provider': 'google',
        'provider_name': 'Google Calendar',
        'email': 'owner@example.com',
        'display_name': 'Owner',
        'connected_by': Fx.userId,
        'status': 'connected',
        'fix': '',
        'last_sync_at': Fx.iso(const Duration(minutes: -1)),
        'last_sync_status': 'ok',
        'last_error': null,
        'last_summary': {'created': 3},
        'events_synced': 3,
        'calendars': calendars,
        'created_at': Fx.iso(const Duration(minutes: -1)),
      };

  List<Map<String, Object?>> get calendars => [
        {
          'id': 'owner@example.com',
          'name': 'Owner',
          'primary': true,
          'selected': true,
          'can_edit': true,
          'color': '#3366cc'
        },
        {
          'id': 'team@group.calendar.google.com',
          'name': 'Team',
          'primary': false,
          'selected': chosen.contains('team@group.calendar.google.com'),
          'can_edit': true,
          'color': '#0b8043'
        },
      ];

  void install(FakeAnumApi api) {
    api.get(
        '/api/v1/calendar/events',
        (_) => FakeResponse.json({
              'items': <Object?>[],
              'start': Fx.iso(),
              'end': Fx.iso(),
              'truncated': false,
              'can_write': true
            }));
    api.get('/api/v1/calendar/next-steps',
        (_) => const FakeResponse.json({'items': <Object?>[]}));
    api.get(
        '/api/v1/calendar/sync',
        (_) => FakeResponse.json({
              'providers': [
                {
                  'provider': 'google',
                  'name': 'Google Calendar',
                  'configured': true,
                  'missing': <String>[],
                  'scopes': <String>[]
                },
                {
                  'provider': 'microsoft',
                  'name': 'Microsoft 365 Calendar',
                  'configured': false,
                  'missing': ['ANUM_CALENDAR_MICROSOFT_CLIENT_ID'],
                  'scopes': <String>[]
                },
              ],
              'redirect_uri':
                  'https://api.example.com/api/v1/calendar/sync/oauth/callback',
              'can_manage': true,
              'connections': [if (connected) connection],
            }));
    api.post('/api/v1/calendar/sync/oauth/{provider}/start', (r) {
      expect(r.body, {'client': 'phone'});
      return FakeResponse.json({
        'authorization_url':
            'https://accounts.google.com/o/oauth2/v2/auth?state=example',
        'expires_at': Fx.iso(const Duration(minutes: 15)),
        'request': 'coc_phone',
      });
    });
    api.post('/api/v1/calendar/sync/oauth/complete', (r) {
      expect(r.body, {'connection': 'coc_phone'});
      if (!returned) {
        return FakeResponse.error(409, 'conflict',
            "The provider has not sent you back yet. Finish the sign-in in the provider's window.");
      }
      connected = true;
      return FakeResponse.json(connection, status: 201);
    });
    api.get(
        '/api/v1/calendar/sync/connections/{id}/calendars',
        (_) => FakeResponse.json(
            {'connection': 'cal_1', 'calendars': calendars, 'max_extra': 10}));
    api.put('/api/v1/calendar/sync/connections/{id}/calendars', (r) {
      chosen
        ..clear()
        ..addAll(
            [for (final id in r.body['selected']! as List<Object?>) '$id']);
      return FakeResponse.json(
          {'connection': 'cal_1', 'calendars': calendars, 'max_extra': 10});
    });
  }
}

Map<String, Object?> _tyreReport(List<Map<String, Object?>> deliveries) => {
      'id': 'trep_1',
      'kind': 'fleet_health',
      'title': 'Fleet tyre health',
      'period_from': '2026-11-01',
      'period_to': '2026-11-12',
      'data_as_of': Fx.iso(),
      'created_by': Fx.userId,
      'created_at': Fx.iso(const Duration(hours: -1)),
      'source': 'manual',
      'schedule_id': null,
      'deliveries': deliveries,
      'highlights': [
        {'label': 'Tyres fitted', 'value': 24}
      ],
      'content': {
        'highlights': [
          {'label': 'Tyres fitted', 'value': 24}
        ],
        'tables': <Object?>[],
        'notes': <Object?>[],
        'currency': 'AED',
      },
    };

void _installTyre(FakeAnumApi api, List<Map<String, Object?>> deliveries) {
  const base = '/api/v1/tyre';
  api.get(
      '$base/setup',
      (_) => FakeResponse.json({
            'connection': {'kind': 'supabase_rest', 'label': 'TyrePulse'},
            'sync': {'synced_at': Fx.iso(const Duration(hours: -2))},
            'can_manage': true,
            'can_operate': true,
            'delivery_channels': ['email', 'whatsapp'],
          }));
  api.get(
      '$base/dashboard',
      (_) => FakeResponse.json({
            'synced_at': Fx.iso(const Duration(hours: -2)),
            'summary': {
              'vehicles': 6, 'tyres_fitted': 24, 'average_tread_mm': 10.7,
              'below_minimum': 1, //
              'in_warning_band': 1, 'pressure_compliant_pct': 91.3,
              'overdue_inspections': 1,
              'due_next_30_days': 2, 'due_next_90_days': 2,
              'cost_per_1000_km': 33.5,
              'currency': 'AED', 'min_tread_mm': 3,
            },
            'alert_counts': {'critical': 0, 'warning': 1},
            'due_soon': <Object?>[],
          }));
  api.get(
      '$base/forecasts',
      (_) => const FakeResponse.json(
          {'items': <Object?>[], 'total': 0, 'offset': 0, 'limit': 25}));
  api.get(
      '$base/alerts',
      (_) => const FakeResponse.json(
          {'items': <Object?>[], 'total': 0, 'offset': 0, 'limit': 25}));
  api.get(
      '$base/purchase-plan',
      (_) => const FakeResponse.json(
          {'months': 3, 'lines': <Object?>[], 'currency': 'AED'}));
  api.get(
      '$base/report-schedules', (_) => const FakeResponse.json(<Object?>[]));
  api.get(
      '$base/reports',
      (_) => FakeResponse.json({
            'items': [_tyreReport(deliveries)],
            'total': 1,
            'limit': 10,
            'offset': 0
          }));
  api.get(
      '$base/reports/{id}', (_) => FakeResponse.json(_tyreReport(deliveries)));
  api.get(
      '$base/delivery-channels',
      (_) => const FakeResponse.json({
            'channels': ['email', 'whatsapp']
          }));
  api.post('$base/reports/{id}/deliver', (r) {
    deliveries.add({
      ...r.body,
      'status': 'waiting_approval',
      'approval_id': 'approval_tyre'
    });
    return FakeResponse.json({
      'status': 'waiting_approval',
      'approval_id': 'approval_tyre',
      'payload_hash': 'ab' * 32,
      'task_id': 'task_tyre',
      'message':
          'Sending needs an approval: approve it in Approvals and it will be sent.',
      'report': _tyreReport(deliveries),
    });
  });
}

void lifeAdminJourneys() => group('Life admin', () {
      testWidgets(
          'calendar: connect Google from the phone, finish on return, '
          'choose another calendar', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final state = _CalendarState()..install(api);
        final opened = <Uri>[];
        final previous = calendarOpenUrl;
        calendarOpenUrl = (uri) async {
          opened.add(uri);
          return true;
        };
        addTearDown(() => calendarOpenUrl = previous);
        await launchApp(tester, api);
        await signIn(tester, api);
        await openFromMenu(tester, api, 'Calendar');
        await _tapKey(tester, 'calsync-connections', api: api);
        await _tapKey(tester, 'calconn-connect-google', api: api);
        expect(opened.single.host, 'accounts.google.com');

        // Back too early: it keeps waiting. Then the browser hands back.
        tester.binding
            .handleAppLifecycleStateChanged(AppLifecycleState.resumed);
        await pumpFor(tester);
        expect(find.byKey(const Key('calconn-progress')), findsOneWidget);
        state.returned = true;
        tester.binding
            .handleAppLifecycleStateChanged(AppLifecycleState.inactive);
        tester.binding
            .handleAppLifecycleStateChanged(AppLifecycleState.resumed);
        await waitFor(tester, find.byKey(const Key('calconn-cal_1')), api: api);

        await _tapKey(tester, 'calconn-choose-cal_1', api: api);
        await waitFor(tester, find.text('Team'), api: api);
        await tapOn(tester, find.text('Team'));
        await _tapKey(tester, 'calchoice-save', api: api);
        await waitFor(tester, find.text('Also synced, read only: Team'),
            api: api);
        expect(state.chosen, ['team@group.calendar.google.com']);
      });

      testWidgets(
          'purchases: edit, attach a receipt, move a return and record '
          'its refund in cash', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final fake = purchases.PurchasesApi()
          ..returns.add(purchases.returnJson('pret_1'));
        _serve(
            api,
            {
              'GET': [
                '/api/v1/purchases', '/api/v1/purchases/{id}',
                '/api/v1/purchases/{id}/events', //
                '/api/v1/finance/accounts',
              ],
              'PATCH': ['/api/v1/purchases/{id}'],
              'POST': [
                '/api/v1/purchases/returns/{id}/status',
                '/api/v1/purchases/returns/{id}/refunds/cash'
              ],
            },
            fake.route);
        api.get(
            '/api/v1/documents',
            (_) => FakeResponse.json({
                  'items': [
                    {
                      'id': 'doc_noon',
                      'title': 'Noon receipt',
                      'category': 'receipt'
                    }
                  ],
                  'next_cursor': null,
                }));
        await launchApp(tester, api);
        await signIn(tester, api);
        await openFromMenu(tester, api, 'Purchases');
        await _tapKey(tester, 'purchase-pur_1', api: api);

        await _tapKey(tester, 'purchase-edit', api: api);
        await _type(tester, 'field-order_ref', 'NOON-2002');
        await _tapKey(tester, 'form-submit', api: api);
        await waitFor(tester, find.text('NOON-2002'), api: api);

        await _tapKey(tester, 'purchase-receipt', api: api);
        await _tapKey(tester, 'receipt-doc_noon', api: api);
        await waitFor(tester, find.text('Attached from the document vault'),
            api: api);
        expect(
            api
                .sent('PATCH', '/api/v1/purchases/pur_1')
                .last
                .body['document_id'],
            'doc_noon');

        await _tapKey(tester, 'return-actions-pret_1', api: api);
        await _tapKey(tester, 'action-move:approved', api: api);
        await waitFor(tester, find.text('Damaged · Approved'), api: api);
        await _tapKey(tester, 'return-actions-pret_1', api: api);
        await _tapKey(tester, 'action-cash', api: api);
        await _tapKey(tester, 'form-submit', api: api);
        await waitUntil(
            tester,
            'the cash refund is recorded',
            () => api
                .sent('POST', '/api/v1/purchases/returns/pret_1/refunds/cash')
                .isNotEmpty,
            api: api);
        final cash = api
            .sent('POST', '/api/v1/purchases/returns/pret_1/refunds/cash')
            .single;
        expect(cash.body['account_id'], 'acct_cash');
        expect(
            cash.headers['idempotency-key'] ?? cash.headers['Idempotency-Key'],
            startsWith('cashref'));
      });

      testWidgets(
          'attendance: write a daily report and submit it after the review',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final fake = attendance.AttendanceApi();
        _serve(
            api,
            {
              'GET': [
                '/api/v1/attendance/today', '/api/v1/attendance/sites',
                '/api/v1/attendance/days', //
                '/api/v1/attendance/daily-reports',
                '/api/v1/attendance/daily-reports/{day}',
                '/api/v1/attendance/corrections',
              ],
              'PUT': ['/api/v1/attendance/daily-reports/{day}'],
            },
            fake.route);
        await launchApp(tester, api);
        await signIn(tester, api);
        await openFromMenu(tester, api, 'Attendance');
        await _tapKey(tester, 'attendance-write-today', api: api);
        await _type(tester, 'field-tasks_done', 'Packed shelves');
        await _type(tester, 'field-issues', 'Forklift was late');
        await _tapKey(tester, 'report-submit', api: api);
        await waitFor(tester, find.textContaining('Done: Packed shelves'),
            api: api);
        await _tapKey(tester, 'records-confirm', api: api);
        await waitUntil(tester, 'the report is submitted',
            () => api.log.any((r) => r.method == 'PUT'),
            api: api);
        final sent = api.log.lastWhere((r) => r.method == 'PUT');
        expect(sent.body['submit'], isTrue);
        expect(sent.body['tasks_done'], ['Packed shelves']);
        expect(sent.body['issues'], 'Forklift was late');
      });

      testWidgets(
          'tyre planning: send a report by email; it waits for an approval',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final deliveries = <Map<String, Object?>>[];
        _installTyre(api, deliveries);
        await launchApp(tester, api);
        await signIn(tester, api);
        await waitFor(tester, find.text('Tyre planning'), api: api);
        await tapOn(tester, find.text('Tyre planning').first);
        await waitFor(tester, find.text('Reports'), api: api);
        await tapOn(tester, find.text('Reports'));
        await _tapKey(tester, 'tyre-report-trep_1', api: api);
        await _tapKey(tester, 'tyre-report-deliver', api: api);
        await waitFor(tester, find.byKey(const Key('tyre-deliver-to')),
            api: api);
        await tester.enterText(
            find.byKey(const Key('tyre-deliver-to')), 'fleet@example.com');
        await pumpFor(tester);
        await _tapKey(tester, 'tyre-deliver-send', api: api);
        await waitFor(tester, find.byKey(const Key('tyre-delivered')),
            api: api);
        final sent =
            api.sent('POST', '/api/v1/tyre/reports/trep_1/deliver').single;
        expect(sent.body,
            {'channel': 'email', 'to': 'fleet@example.com', 'format': 'pdf'});
      });
    });
