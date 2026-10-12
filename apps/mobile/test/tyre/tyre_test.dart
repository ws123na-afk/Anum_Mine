// Tyre planning on the phone (docs/tyre-planning.md): dashboard, tyres due by
// month, alerts, every state, Arabic, and layouts from 320 to 1440 wide at
// 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/push/push_models.dart';
import 'package:anum_mobile/features/tyre/tyre_controller.dart';
import 'package:anum_mobile/features/tyre/tyre_models.dart';
import 'package:anum_mobile/features/tyre/tyre_repository.dart';
import 'package:anum_mobile/features/tyre/tyre_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, Object?> _forecast(int n, {String state = 'projected'}) => {
      'tyre_id': 't$n',
      'serial': 'fake-sn-${n.toString().padLeft(4, '0')}',
      'size': '315/80r22.5',
      'vehicle_label': 'fake-plate-01',
      'position': 'P${n % 4 + 1}',
      'state': state,
      'confidence': 'medium',
      'current_tread_mm': 6.8,
      'wear_mm_per_30_days': 2.4,
      'projected_removal': '2026-10-28',
    };

Map<String, Object?> _alert(String id, String severity) => {
      'id': id,
      'kind': 'low_tread',
      'severity': severity,
      'status': 'open',
      'subject_label': 'fake-sn-0103 on fake-plate-01',
      'message': 'Tread 2.8 mm is at or below the 3.0 mm minimum: remove now.',
    };

class FakeTyres implements TyreRepository {
  FakeTyres(
      {this.synced = true,
      this.canOperate = true,
      this.fail,
      this.dueCount = 30});
  bool synced, canOperate;
  ApiException? fail;
  int dueCount;
  final requests = <String>[];
  final alertItems = [_alert('a1', 'critical'), _alert('a2', 'warning')];

  @override
  Future<TyreAccess> access() async {
    requests.add('access');
    if (fail != null) throw fail!;
    return TyreAccess.fromJson(JsonReader({
      'connection': {'kind': 'supabase_postgres'},
      'sync': synced ? {'synced_at': '2026-10-01T08:00:00Z'} : null,
      'can_operate': canOperate,
    }));
  }

  @override
  Future<TyreDashboard> dashboard() async => TyreDashboard.fromJson(JsonReader({
        'summary': {
          'vehicles': 6,
          'tyres_fitted': 24,
          'average_tread_mm': 10.75,
          'below_minimum': 1,
          'in_warning_band': 1,
          'pressure_compliant_pct': 91.3,
          'overdue_inspections': 1,
          'due_next_30_days': 2,
          'due_next_90_days': 2,
          'cost_per_1000_km': 33.58,
          'currency': 'SAR',
          'min_tread_mm': 3.0,
        },
        'synced_at': '2026-10-01T08:00:00Z',
        'alert_counts': {'critical': 2, 'warning': 1},
        'due_soon': [_forecast(1, state: 'due'), _forecast(2)],
      }));

  @override
  Future<TyrePage<TyreForecast>> due(
      {required String month,
      String? query,
      int offset = 0,
      int limit = 25}) async {
    requests.add('due $month ${query ?? ''} $offset');
    final all = [
      for (var i = 1; i <= dueCount; i++)
        if (query == null || query.isEmpty || '$i' == query) _forecast(i)
    ];
    return TyrePage(
        items: all
            .skip(offset)
            .take(limit)
            .map((item) => TyreForecast.fromJson(JsonReader(item)))
            .toList(),
        total: all.length,
        offset: offset);
  }

  @override
  Future<TyrePage<TyreAlert>> alerts({int offset = 0, int limit = 25}) async =>
      TyrePage(
          items: alertItems
              .map((item) => TyreAlert.fromJson(JsonReader(item)))
              .toList(),
          total: alertItems.length,
          offset: 0);

  @override
  Future<TyreAlert> acknowledge(String id) async {
    requests.add('ack $id');
    return TyreAlert.fromJson(
        JsonReader(alertItems.firstWhere((a) => a['id'] == id))).acknowledged();
  }
}

Widget _app(TyreController controller,
        {Locale locale = const Locale('en'),
        int tab = 0,
        double textScale = 1}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: child!),
      home: TyreScreen(
          key: ObjectKey(controller), controller: controller, initialTab: tab),
    );

TyreController _controller(FakeTyres fake) =>
    TyreController(fake, clock: () => DateTime(2026, 10, 11));

void main() {
  testWidgets('dashboard shows the fleet numbers and what is due',
      (tester) async {
    await tester.pumpWidget(_app(_controller(FakeTyres())));
    await tester.pumpAndSettle();
    expect(find.text('24'), findsOneWidget);
    expect(find.text('10.8 mm'), findsOneWidget);
    expect(find.text('91.3%'), findsOneWidget);
    expect(find.text('33.58'), findsOneWidget);
    expect(find.textContaining('Due now'), findsOneWidget);
    expect(find.text('fake-sn-0001'), findsOneWidget);
  });

  testWidgets('due list pages, searches and moves between months',
      (tester) async {
    final fake = FakeTyres();
    await tester.pumpWidget(_app(_controller(fake), tab: 1));
    await tester.pumpAndSettle();
    expect(find.text('30 tyres due'), findsOneWidget);
    await tester.ensureVisible(find.text('Show more'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Show more'));
    await tester.pumpAndSettle();
    expect(fake.requests, contains('due 2026-10  25'));
    await tester.enterText(find.byType(TextField), '7');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(find.text('1 tyres due'), findsOneWidget);
    await tester.tap(find.byTooltip('Next month'));
    await tester.pumpAndSettle();
    expect(fake.requests.last, startsWith('due 2026-11'));
    fake.dueCount = 0;
    await tester.tap(find.byTooltip('Next month'));
    await tester.pumpAndSettle();
    expect(find.text('Nothing due'), findsOneWidget);
  });

  testWidgets('members acknowledge alerts; viewers are told who can',
      (tester) async {
    final fake = FakeTyres();
    await tester.pumpWidget(_app(_controller(fake), tab: 2));
    await tester.pumpAndSettle();
    expect(find.text('Critical'), findsOneWidget);
    await tester.tap(find.text('Acknowledge').first);
    await tester.pumpAndSettle();
    expect(fake.requests, contains('ack a1'));
    expect(find.text('Acknowledged'), findsOneWidget);

    final viewer = FakeTyres(canOperate: false);
    await tester.pumpWidget(_app(_controller(viewer), tab: 2));
    await tester.pumpAndSettle();
    expect(find.text('Members and owners can acknowledge alerts.'),
        findsOneWidget);
    expect(find.text('Acknowledge'), findsNothing);
  });

  testWidgets('not synced, failing and denied each say what to do',
      (tester) async {
    await tester.pumpWidget(_app(_controller(FakeTyres(synced: false))));
    await tester.pumpAndSettle();
    expect(find.textContaining('not synced yet'), findsOneWidget);

    final failing =
        FakeTyres(fail: const ApiException(503, 'Database unavailable'));
    final controller = _controller(failing);
    await tester.pumpWidget(_app(controller));
    await tester.pumpAndSettle();
    expect(find.textContaining('Database unavailable'), findsOneWidget);
    failing.fail = null;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('24'), findsOneWidget);

    await tester.pumpWidget(_app(_controller(FakeTyres(
        fail: const ApiException(403, 'Workspace permission denied')))));
    await tester.pumpAndSettle();
    expect(find.text('Not available to your role'), findsOneWidget);
  });

  testWidgets('Arabic reads right to left', (tester) async {
    await tester
        .pumpWidget(_app(_controller(FakeTyres()), locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('تخطيط الإطارات'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('تخطيط الإطارات'))),
        TextDirection.rtl);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('lays out at $width wide with 200% text', (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      for (final tab in [0, 1, 2]) {
        await tester
            .pumpWidget(_app(_controller(FakeTyres()), tab: tab, textScale: 2));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: 'tab $tab at $width');
      }
    });
  }

  test('a tyre alert push opens the alerts screen', () {
    final destination = PushDestination.fromData(
        {'anum_kind': 'tyre_alerts', 'workspace_id': 'workspace_test'});
    expect(destination?.kind, PushDestinationKind.tyreAlerts);
    expect(destination?.route, '/tyres/alerts');
  });
}
