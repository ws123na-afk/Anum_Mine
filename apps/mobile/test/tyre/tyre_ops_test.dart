import 'dart:async';

// Tyre plan, reports and schedules on the phone (docs/tyre-planning.md):
// sync now, the purchase plan, generating, reading, deleting and sending
// reports, report schedules, with the server's own role answers.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/tyre/tyre_controller.dart';
import 'package:anum_mobile/features/tyre/tyre_ops.dart';
import 'package:anum_mobile/features/tyre/tyre_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'tyre_test.dart' show FakeTyres;

Map<String, Object?> reportJson(int i, {bool full = false}) => {
      'id': 'trep_$i',
      'kind': 'fleet_health',
      'title': 'Fleet tyre health',
      'period_from': null,
      'period_to': null,
      'created_at': '2026-10-18T08:00:00Z',
      'source': i.isEven ? 'schedule' : 'manual',
      'deliveries': <Object?>[],
      if (!full)
        'highlights': [
          {'label': 'Vehicles', 'value': 6}
        ],
      if (full)
        'content': {
          'highlights': [
            {'label': 'Vehicles', 'value': 6},
            {'label': 'Average tread (mm)', 'value': 10.75},
          ],
          'tables': [
            {
              'title': 'Lowest tread',
              'columns': ['Tyre', 'Vehicle', 'Tread mm'],
              'rows': [
                for (var n = 0; n < 30; n++) ['SN-$n', 'PLATE-$n', 2.5 + n / 10]
              ],
            }
          ],
          'notes': ['Tread is the latest reading.'],
        },
    };

class FakeOps implements TyreOpsRepository {
  FakeOps({
    this.reportCount = 25,
    this.planRows = 60,
    this.channels = const ['email'],
    this.failPlan = false,
    this.denyPlan = false,
  });
  final int reportCount, planRows;
  final List<String> channels;
  final bool failPlan, denyPlan;
  final calls = <String>[];
  final bodies = <Map<String, Object?>>[];
  String? refuse;
  var schedulesList = <Map<String, Object?>>[
    {
      'id': 'tsch_1',
      'kind': 'fleet_health',
      'title': 'Fleet tyre health',
      'cadence': 'weekly',
      'description': 'Every Sunday at 07:00',
      'enabled': true,
      'next_run_at': '2026-10-25T04:00:00Z',
    }
  ];

  void need() {
    if (refuse != null) throw ApiException(409, refuse!);
  }

  @override
  Future<int> sync() async {
    calls.add('sync');
    need();
    return 2;
  }

  @override
  Future<PurchasePlan> plan(int months) async {
    calls.add('plan $months');
    if (denyPlan) throw const ApiException(403, 'No tyre access.');
    if (failPlan) throw const ApiException(503, 'The data source is down.');
    return PurchasePlan.fromJson(JsonReader.of({
      'currency': 'SAR',
      'total_tyres': planRows * 2,
      'total_cost': planRows * 2 * 900.0,
      'unpriced_rows': 1,
      'rows': [
        for (var i = 0; i < planRows; i++)
          {
            'month': '2026-11',
            'size': '315/80R22.5',
            'brand': 'Bridgestone $i',
            'due': 3,
            'safety': 1,
            'from_stock': 2,
            'to_buy': 2,
            'unit_price': 900.0,
            'estimated_cost': 1800.0,
          }
      ],
    }));
  }

  @override
  Future<OpsPage<TyreReportItem>> reports(
      {String? kind, required int offset, int limit = 20}) async {
    calls.add('reports $offset');
    final count = (reportCount - offset).clamp(0, limit);
    return OpsPage([
      for (var i = 0; i < count; i++)
        TyreReportItem.fromJson(JsonReader.of(reportJson(offset + i)))
    ], reportCount);
  }

  @override
  Future<TyreReportItem> report(String id) async {
    calls.add('report $id');
    return TyreReportItem.fromJson(JsonReader.of(reportJson(0, full: true)));
  }

  @override
  Future<TyreReportItem> generate(
      String kind, String? periodFrom, String? periodTo) async {
    need();
    calls.add('generate $kind $periodFrom $periodTo');
    return TyreReportItem.fromJson(JsonReader.of(reportJson(0, full: true)));
  }

  @override
  Future<void> deleteReport(String id) async {
    need();
    calls.add('delete $id');
  }

  @override
  Future<DeliveryResult> deliver(
      String id, String channel, String to, String format) async {
    need();
    calls.add('deliver $channel $to $format');
    return const DeliveryResult('waiting_approval',
        'Sending needs an approval: approve it in Approvals and it will be sent.');
  }

  @override
  Future<List<String>> deliveryChannels() async => channels;

  @override
  Future<List<ReportSchedule>> schedules() async => [
        for (final s in schedulesList) ReportSchedule.fromJson(JsonReader.of(s))
      ];

  @override
  Future<ReportSchedule> createSchedule(Map<String, Object?> fields) async {
    need();
    calls.add('createSchedule');
    bodies.add(fields);
    return ReportSchedule.fromJson(JsonReader.of(schedulesList.first));
  }

  @override
  Future<void> deleteSchedule(String id) async {
    need();
    calls.add('deleteSchedule $id');
    schedulesList = [];
  }
}

Future<void> phone(WidgetTester tester, {double width = 390}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = Size(width, 844);
  addTearDown(tester.view.reset);
}

Future<void> tap(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key(key)));
  await tester.pumpAndSettle();
}

Future<void> enter(WidgetTester tester, String key, String text) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(Key(key)), text);
}

Future<void> open(
  WidgetTester tester,
  FakeOps ops, {
  bool operate = true,
  String? tab,
  Locale locale = const Locale('en'),
  VoidCallback? onApprovals,
  bool withOps = true,
}) async {
  final c = TyreController(FakeTyres(canOperate: operate),
      ops: withOps ? TyreOps(ops) : null);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates,
    ],
    home: TyreScreen(
        key: UniqueKey(), controller: c, onOpenApprovals: onApprovals),
  ));
  await tester.pumpAndSettle();
  if (tab != null) {
    await tester.ensureVisible(find.text(tab));
    await tester.pumpAndSettle();
    await tester.tap(find.text(tab));
    await tester.pumpAndSettle();
  }
}

void main() {
  testWidgets('without operations the screen keeps its three tabs',
      (tester) async {
    await phone(tester);
    await open(tester, FakeOps(), withOps: false);
    expect(find.text('Plan'), findsNothing);
    expect(find.byKey(const Key('tyre-sync')), findsNothing);
  });

  testWidgets('an operator syncs now; a viewer has no button', (tester) async {
    await phone(tester);
    final ops = FakeOps();
    await open(tester, ops);
    await tap(tester, 'tyre-sync');
    expect(ops.calls, contains('sync'));
    expect(find.text('Synced. 2 new alert(s).'), findsOneWidget);
    await open(tester, FakeOps(), operate: false);
    expect(find.byKey(const Key('tyre-sync')), findsNothing);
  });

  group('purchase plan', () {
    testWidgets('shows totals, pages locally and changes the window',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'Plan');
      expect(ops.calls, contains('plan 6'));
      expect(find.text('Show more (25 of 60)'), findsOneWidget);
      await tap(tester, 'tyre-plan-more');
      expect(find.text('Show more (50 of 60)'), findsOneWidget);
      await tap(tester, 'tyre-plan-12');
      expect(ops.calls.last, 'plan 12');
    });

    testWidgets('says when there is nothing, and when it cannot load',
        (tester) async {
      await phone(tester);
      await open(tester, FakeOps(planRows: 0), tab: 'Plan');
      expect(find.text('Nothing to buy'), findsOneWidget);
      await open(tester, FakeOps(failPlan: true), tab: 'Plan');
      expect(find.textContaining('The data source is down.'), findsOneWidget);
      await open(tester, FakeOps(denyPlan: true), tab: 'Plan');
      expect(find.byIcon(Icons.lock_outline), findsWidgets);
    });
  });

  group('reports', () {
    testWidgets('lists and pages; a viewer cannot generate', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'Reports', operate: false);
      expect(find.byKey(const Key('tyre-report-new')), findsNothing);
      expect(find.text('Load more'), findsNothing);
      await tap(tester, 'tyre-reports-more');
      expect(ops.calls, contains('reports 20'));
    });

    testWidgets('an empty list tells operators what to do', (tester) async {
      await phone(tester);
      await open(tester, FakeOps(reportCount: 0), tab: 'Reports');
      expect(find.text('No reports yet'), findsOneWidget);
      await open(tester, FakeOps(reportCount: 0),
          tab: 'Reports', operate: false);
      expect(find.text('Owners and members generate reports.'), findsOneWidget);
    });

    testWidgets('generating checks the period and opens the report',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'Reports');
      await tap(tester, 'tyre-report-new');
      await enter(tester, 'tyre-report-from', '1 Oct');
      await tap(tester, 'tyre-save');
      expect(find.text('Use the form 2026-12-31.'), findsOneWidget);
      await enter(tester, 'tyre-report-from', '2026-10-01');
      await tap(tester, 'tyre-save');
      expect(find.textContaining('Give both dates'), findsOneWidget);
      await enter(tester, 'tyre-report-to', '2026-10-31');
      await tap(tester, 'tyre-save');
      expect(
          ops.calls, contains('generate fleet_health 2026-10-01 2026-10-31'));
      // The new report is open: highlights, a capped table and a note.
      expect(find.text('Average tread (mm)'), findsOneWidget);
      expect(find.text('Showing 25 of 30 rows.'), findsOneWidget);
      expect(find.text('Tread is the latest reading.'), findsOneWidget);
    });

    testWidgets('a server refusal stays on the form', (tester) async {
      await phone(tester);
      final ops = FakeOps()..refuse = 'Sync the tyre data first.';
      await open(tester, ops, tab: 'Reports');
      await tap(tester, 'tyre-report-new');
      await tap(tester, 'tyre-save');
      expect(find.byKey(const Key('tyre-form-error')), findsOneWidget);
      expect(find.text('Sync the tyre data first.'), findsOneWidget);
    });

    testWidgets('deleting asks first and shows a refusal', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'Reports');
      await tester.tap(find.byKey(const Key('tyre-report-trep_0')));
      await tester.pumpAndSettle();
      ops.refuse = 'Not allowed right now.';
      await tap(tester, 'tyre-report-delete');
      await tap(tester, 'tyre-confirm');
      expect(find.byKey(const Key('tyre-report-error')), findsOneWidget);
      ops.refuse = null;
      await tap(tester, 'tyre-report-delete');
      await tap(tester, 'tyre-confirm');
      expect(ops.calls, contains('delete trep_0'));
    });

    testWidgets('sending needs a recipient and waits for approval',
        (tester) async {
      await phone(tester);
      final ops = FakeOps();
      var approvals = 0;
      await open(tester, ops, tab: 'Reports', onApprovals: () => approvals++);
      await tester.tap(find.byKey(const Key('tyre-report-trep_0')));
      await tester.pumpAndSettle();
      await tap(tester, 'tyre-report-deliver');
      await tap(tester, 'tyre-deliver-send');
      expect(
          find.text('Enter an email address or phone number.'), findsOneWidget);
      await enter(tester, 'tyre-deliver-to', 'fleet@example.com');
      await tap(tester, 'tyre-deliver-send');
      expect(ops.calls, contains('deliver email fleet@example.com pdf'));
      expect(find.byKey(const Key('tyre-delivered')), findsOneWidget);
      await tester.tap(find.text('Open Approvals'));
      expect(approvals, 1);
    });

    testWidgets('without a connected channel it says so', (tester) async {
      await phone(tester);
      await open(tester, FakeOps(channels: const []), tab: 'Reports');
      await tester.tap(find.byKey(const Key('tyre-report-trep_0')));
      await tester.pumpAndSettle();
      await tap(tester, 'tyre-report-deliver');
      expect(find.byKey(const Key('tyre-no-channels')), findsOneWidget);
      expect(find.byKey(const Key('tyre-deliver-send')), findsNothing);
    });

    testWidgets('a viewer reads a report but cannot send or delete it',
        (tester) async {
      await phone(tester);
      await open(tester, FakeOps(), tab: 'Reports', operate: false);
      await tester.tap(find.byKey(const Key('tyre-report-trep_0')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('tyre-report-deliver')), findsNothing);
      expect(find.byKey(const Key('tyre-report-delete')), findsNothing);
    });
  });

  group('schedules', () {
    testWidgets('an operator adds and deletes one', (tester) async {
      await phone(tester);
      final ops = FakeOps();
      await open(tester, ops, tab: 'Reports');
      expect(find.text('Every Sunday at 07:00'), findsOneWidget);
      await tap(tester, 'tyre-schedule-new');
      await enter(tester, 'tyre-schedule-time', '25:00');
      await tap(tester, 'tyre-save');
      expect(find.text('Use the form 07:00.'), findsOneWidget);
      await enter(tester, 'tyre-schedule-time', '08:30');
      await tap(tester, 'tyre-save');
      expect(ops.bodies.single, {
        'kind': 'fleet_health',
        'cadence': 'weekly',
        'weekday': 0,
        'time': '08:30',
      });
      await tap(tester, 'tyre-schedule-delete-tsch_1');
      await tap(tester, 'tyre-confirm');
      expect(ops.calls, contains('deleteSchedule tsch_1'));
      expect(find.text('No schedules yet.'), findsOneWidget);
    });

    testWidgets('a viewer sees schedules without controls', (tester) async {
      await phone(tester);
      await open(tester, FakeOps(), tab: 'Reports', operate: false);
      expect(find.text('Every Sunday at 07:00'), findsOneWidget);
      expect(find.byKey(const Key('tyre-schedule-new')), findsNothing);
      expect(
          find.byKey(const Key('tyre-schedule-delete-tsch_1')), findsNothing);
    });
  });

  for (final width in [320.0, 390.0]) {
    testWidgets('arabic plan, reports and report fit at $width',
        (tester) async {
      await phone(tester, width: width);
      await open(tester, FakeOps(), tab: 'الخطة', locale: const Locale('ar'));
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.text('التقارير'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('التقارير'));
      await tester.pumpAndSettle();
      expect(find.text('إنشاء تقرير'), findsWidgets);
      expect(tester.takeException(), isNull);
      await tester.tap(find.byKey(const Key('tyre-report-trep_0')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tap(tester, 'tyre-report-deliver');
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('a scheduled report carries the Scheduled label', (tester) async {
    await phone(tester);
    await open(tester, FakeOps(), tab: 'Reports');
    expect(find.textContaining('Scheduled'), findsWidgets);
  });

  test('an older plan response never replaces a newer horizon', () async {
    final repo = SlowPlan();
    final ops = TyreOps(repo);
    final older = ops.setMonths(3);
    final newer = ops.setMonths(12);
    repo.second.complete(PurchasePlan.fromJson(JsonReader.of(
        {'currency': 'SAR', 'total_tyres': 12, 'rows': <Object?>[]})));
    await newer;
    repo.first.complete(PurchasePlan.fromJson(JsonReader.of(
        {'currency': 'SAR', 'total_tyres': 3, 'rows': <Object?>[]})));
    await older;
    expect(ops.months, 12);
    expect(ops.plan!.totalTyres, 12);
  });

  testWidgets('reopening the plan and syncing reload it', (tester) async {
    await phone(tester);
    final ops = FakeOps();
    await open(tester, ops, tab: 'Plan');
    final before = ops.calls.where((x) => x.startsWith('plan')).length;
    await tap(tester, 'tyre-sync');
    expect(ops.calls.where((x) => x.startsWith('plan')).length, before + 1);
    await open(tester, ops, tab: 'Plan');
    expect(ops.calls.where((x) => x.startsWith('plan')).length, before + 2);
  });
}

class SlowPlan extends FakeOps {
  final first = Completer<PurchasePlan>();
  final second = Completer<PurchasePlan>();
  var n = 0;

  @override
  Future<PurchasePlan> plan(int months) => (n++ == 0 ? first : second).future;
}
