import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/command/command.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import '../voice/voice_fakes.dart';
import 'command_fakes.dart';

const _sizes = <Size>[
  Size(320, 640),
  Size(390, 844),
  Size(768, 1024),
  Size(1024, 768),
  Size(1440, 900),
];

Widget _app(Widget child, {String locale = 'en', double scale = 1}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      locale: Locale(locale),
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates,
      ],
      home: Builder(
        builder: (context) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
              disableAnimations: true, textScaler: TextScaler.linear(scale)),
          child: child,
        ),
      ),
    );

Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 10; i++) {
    await tester.pump(const Duration(milliseconds: 50));
  }
}

void main() {
  late FakeCommandRepository repository;
  late FakeCommandTransfer transfer;
  late CommandController controller;

  setUp(() {
    repository = FakeCommandRepository();
    transfer = FakeCommandTransfer();
    controller = CommandController(
        repository: repository, speech: FakeSpeech(), transfer: transfer);
  });

  Future<void> pump(WidgetTester tester, Size size,
      {String locale = 'en', double scale = 1, Widget? screen}) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(_app(
        screen ?? CommandScreen(controller: controller),
        locale: locale,
        scale: scale));
    await _settle(tester);
  }

  Future<void> finish(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  }

  Future<void> reachResults() async {
    repository.views = [
      view('waiting_approval', [
        stepViewJson(1, 'email', 'completed', result: 'Found 12 urgent emails'),
        stepViewJson(2, 'action', 'waiting_approval',
            approvals: [approvalJson('approval_1', 'task_2')]),
      ]),
    ];
    await controller.submit('Check my emails');
    await controller.start();
  }

  for (final size in _sizes) {
    testWidgets('every stage lays out at ${size.width.toInt()} wide',
        (tester) async {
      await pump(tester, size);
      expect(find.text('ANUM Command'), findsOneWidget);
      expect(find.text('PMV report'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));

      await controller.submit('Check my emails');
      await _settle(tester);
      expect(find.text('Plan and agent selection'), findsOneWidget);
      expect(find.text('Needs your approval'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));

      await reachResults();
      await _settle(tester);
      expect(find.text('Results ready'), findsOneWidget);
      expect(find.text('Approve all'), findsWidgets);
      expect(tester.takeException(), isNull);
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await finish(tester);
    });
  }

  testWidgets('200% text and Arabic right-to-left do not clip', (tester) async {
    await pump(tester, const Size(390, 844), locale: 'ar', scale: 2);
    expect(find.text('أوامر ANUM'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('أوامر ANUM'))),
        TextDirection.rtl);
    expect(tester.takeException(), isNull);
    await controller.submit('تحقق من بريدي');
    await _settle(tester);
    expect(find.text('الخطة واختيار الوكلاء'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await reachResults();
    await _settle(tester);
    expect(find.text('النتائج جاهزة'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await finish(tester);
  });

  testWidgets('type a command, confirm the plan, approve all', (tester) async {
    await pump(tester, const Size(390, 844));
    await tester.tap(find.byTooltip('Type a command'));
    await _settle(tester);
    await tester.enterText(find.byType(TextField), 'Check my emails');
    await tester.tap(find.byTooltip('Plan this'));
    await _settle(tester);
    expect(repository.previews, ['Check my emails']);
    expect(find.text('Here is what I understood'), findsOneWidget);

    // Remove the second step, then start.
    final remove = find.widgetWithIcon(IconButton, Icons.delete_outline).last;
    await tester.ensureVisible(remove);
    await _settle(tester);
    await tester.tap(remove);
    await _settle(tester);
    expect(controller.steps, hasLength(1));
    repository.views = [
      view('waiting_approval', [
        stepViewJson(1, 'action', 'waiting_approval',
            approvals: [approvalJson('approval_1', 'task_1')]),
      ]),
    ];
    final start = find.text('Start execution');
    await tester.ensureVisible(start);
    await tester.tap(start);
    await _settle(tester);
    expect(repository.creates.single['steps'], ['Check my emails']);
    expect(find.text('Results ready'), findsOneWidget);

    final approve = find.text('Approve all').first;
    await tester.ensureVisible(approve);
    await tester.tap(approve);
    await _settle(tester);
    expect(find.textContaining('Payload hash'), findsOneWidget);
    await tester.tap(find.text('Approve'));
    await _settle(tester);
    expect(repository.approvals, ['approval_1']);
    await finish(tester);
  });

  testWidgets('a tyre step without a connection links to Tyre planning',
      (tester) async {
    repository.plan = CommandPlan.fromJson(JsonReader(planJson([
      planStepJson(1, 'tyre', 'Check the PMV tyre report',
          tool: 'tyre_fleet_summary',
          connected: false,
          note: 'Connect TyrePulse under Tyre planning first.'),
    ])));
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final opened = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates,
      ],
      onGenerateRoute: (settings) {
        opened.add(settings.name ?? '');
        return MaterialPageRoute<void>(builder: (_) => const SizedBox());
      },
      home: MediaQuery(
        data: const MediaQueryData(disableAnimations: true),
        child: CommandScreen(controller: controller),
      ),
    ));
    await controller.submit('Check the PMV tyre report');
    await _settle(tester);
    expect(find.text('Not connected'), findsOneWidget);
    final connect = find.text('Connect TyrePulse under Tyre planning');
    await tester.ensureVisible(connect);
    await _settle(tester);
    await tester.tap(connect);
    await _settle(tester);
    expect(opened, ['/tyres']);
    await finish(tester);
  });

  testWidgets('suggestion chips start a command with their source',
      (tester) async {
    await pump(tester, const Size(390, 844));
    await tester.tap(find.text('PMV report'));
    await _settle(tester);
    expect(repository.previews, ['Check PMV tyre report']);
    expect(controller.source, 'quick_command');
    expect(controller.quickCommandId, 'quick_1');
    await finish(tester);
  });

  testWidgets('a planning permission error says who can act', (tester) async {
    repository.previewError = const ApiException(403, 'Forbidden');
    await pump(tester, const Size(390, 844));
    await controller.submit('Check my emails');
    await _settle(tester);
    expect(find.textContaining('workspace owner'), findsOneWidget);
    await finish(tester);
  });

  testWidgets('quick commands: empty, add, run', (tester) async {
    await pump(tester, const Size(390, 844),
        screen: QuickCommandsScreen(controller: controller));
    expect(find.text('No Work commands yet'), findsOneWidget);
    await tester.tap(find.text('Add quick command').first);
    await _settle(tester);
    await tester.enterText(find.widgetWithText(TextField, 'Name'), 'Mail');
    await tester.enterText(
        find.widgetWithText(TextField, 'Command'), 'Check my emails');
    await tester.tap(find.text('Save'));
    await _settle(tester);
    expect(find.text('Mail'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
    await tester.tap(find.text('Mail'));
    await _settle(tester);
    expect(repository.previews, ['Check my emails']);
    await finish(tester);
  });

  test('every Command string has Arabic', () {
    for (final key in CommandStrings.keys) {
      expect(CommandStrings.hasArabic(key), isTrue, reason: key);
    }
    expect(const CommandStrings(true).t('title'),
        isNot(CommandStrings.english.t('title')));
  });
}
