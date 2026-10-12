// Home (docs/home.md): parsing, the controller's states, and the main paths
// of Home and ANUM Proactive, from a 320 px phone at 200 percent text to
// 1440 px, in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/home/home_controller.dart';
import 'package:anum_mobile/features/home/home_models.dart';
import 'package:anum_mobile/features/home/home_repository.dart';
import 'package:anum_mobile/features/home/home_screen.dart';
import 'package:anum_mobile/features/home/insights_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime(2026, 10, 7, 9, 41);

Map<String, Object?> _summary({bool empty = false, String? name = 'Shahzeb'}) =>
    {
      'generated_at': _now.toUtc().toIso8601String(),
      'profile': {'display_name': name},
      'focus': empty
          ? []
          : [
              {
                'id': 'approvals',
                'source': 'approvals',
                'kind': 'approvals_pending',
                'title': '2 approvals waiting',
                'count': 2,
                'severity': 'high',
                'target': {'feature': 'approvals'},
                'params': {'count': 2},
              },
              {
                'id': 'task:task_1',
                'source': 'tasks',
                'kind': 'task_in_progress',
                'title': 'PMV tyre report review',
                'progress': 0.75,
                'severity': 'low',
                'target': {'feature': 'tasks', 'id': 'task_1'},
                'params': {'status': 'running', 'phase': 'executing'},
              },
            ],
      'counts': {'approvals': 2, 'tasks': 1},
      'progress': {'completed': 3, 'open': 1},
      'activity': empty
          ? []
          : [
              {
                'id': 'task:task_2',
                'kind': 'task',
                'title': 'Tyre report analysis',
                'status': 'completed',
                'at': _now
                    .subtract(const Duration(minutes: 10))
                    .toUtc()
                    .toIso8601String(),
                'target': {'feature': 'tasks', 'id': 'task_2'},
              },
            ],
      'sources': [
        {'name': 'approvals', 'status': 'ok'},
        {'name': 'email', 'status': 'unavailable', 'detail': 'x'},
        {'name': 'meetings', 'status': 'not_installed'},
      ],
    };

Map<String, Object?> _insight(String key,
        {String kind = 'task_failed', String type = 'command'}) =>
    {
      'key': key,
      'source': 'tasks',
      'kind': kind,
      'severity': 'high',
      'title': 'A task failed',
      'finding': '“Quotation” failed and was not retried.',
      'question': 'Shall I analyze what went wrong?',
      'params': {'title': 'Quotation'},
      'action': {
        'type': type,
        'verb': type == 'command' ? 'analyze' : 'review',
        'target': {'feature': 'tasks', 'id': 'task_9'},
        'command': type == 'command' ? 'Analyze the failed task.' : null,
      },
      'state': 'new',
    };

class FakeHomeRepository implements HomeRepository {
  int failSummary = 0;
  bool failPins = false;
  bool failAddPin = false;
  bool empty = false;
  bool canEdit = true;
  final pinList = <HomePin>[
    const HomePin(
        id: 'pin_1',
        kind: PinKind.link,
        label: 'TyrePulse',
        target: 'https://tyrepulse.example',
        icon: 'tyre'),
    const HomePin(
        id: 'pin_2',
        kind: PinKind.workspace,
        label: 'PMV Operations',
        target: 'workspace_pmv'),
  ];
  final commandList = <QuickCommand>[
    const QuickCommand(
        id: 'builtin:check_email',
        mode: CommandMode.work,
        label: 'Check my emails',
        prompt: 'Check my emails, find today\'s urgent items and summarize '
            'them.',
        key: 'check_email',
        builtIn: true),
    const QuickCommand(
        id: 'cmd_1',
        mode: CommandMode.custom,
        label: 'Tyre stock',
        prompt: 'Check tyre stock on Noon'),
  ];
  var insightItems = [
    _insight('tasks:failed:task_9'),
    _insight('approvals:a1', kind: 'approval_waiting', type: 'open'),
  ];
  final dismissed = <String>[];
  final snoozed = <String>[];
  final reorders = <List<String>>[];
  String? savedName;

  @override
  Future<HomeSummary> summary() async {
    if (failSummary != 0) {
      throw ApiException(
          failSummary,
          failSummary == 403
              ? 'Active workspace membership required'
              : 'Bad gateway');
    }
    return HomeSummary.fromJson(
        JsonReader(_summary(empty: empty, name: savedName ?? 'Shahzeb')));
  }

  @override
  Future<String?> setDisplayName(String? name) async => savedName = name;

  @override
  Future<List<HomePin>> pins() async {
    if (failPins) throw const ApiException(500, 'Pins are unavailable');
    return empty ? [] : List.of(pinList);
  }

  @override
  Future<HomePin> addPin(
      {required PinKind kind,
      required String label,
      required String target,
      String? icon}) async {
    if (failAddPin) throw const ApiException(409, 'This is already pinned.');
    final pin = HomePin(
        id: 'pin_${pinList.length + 1}',
        kind: kind,
        label: label,
        target: target,
        icon: icon);
    pinList.add(pin);
    return pin;
  }

  @override
  Future<HomePin> updatePin(String id, {String? label, String? target}) async {
    final i = pinList.indexWhere((p) => p.id == id);
    pinList[i] = HomePin(
        id: id,
        kind: pinList[i].kind,
        label: label ?? pinList[i].label,
        target: target ?? pinList[i].target);
    return pinList[i];
  }

  @override
  Future<void> removePin(String id) async =>
      pinList.removeWhere((p) => p.id == id);

  @override
  Future<List<HomePin>> reorderPins(List<String> ids) async {
    reorders.add(ids);
    return [for (final id in ids) pinList.firstWhere((p) => p.id == id)];
  }

  @override
  Future<List<PinnableWorkspace>> workspaces() async =>
      const [PinnableWorkspace('workspace_pmv', 'PMV Operations')];

  @override
  Future<CommandList> commands() async =>
      CommandList(canEdit ? List.of(commandList) : const [], canEdit);

  @override
  Future<QuickCommand> addCommand(
      {required CommandMode mode,
      required String label,
      required String prompt}) async {
    final c = QuickCommand(
        id: 'cmd_${commandList.length + 1}',
        mode: mode,
        label: label,
        prompt: prompt);
    commandList.add(c);
    return c;
  }

  @override
  Future<QuickCommand> updateCommand(String id,
          {CommandMode? mode, String? label, String? prompt}) async =>
      commandList.firstWhere((c) => c.id == id);

  @override
  Future<void> removeCommand(String id) async =>
      commandList.removeWhere((c) => c.id == id);

  @override
  Future<InsightList> insights({bool includeHidden = false}) async =>
      InsightList.fromJson(JsonReader({
        'items': empty ? [] : insightItems,
        'hidden': dismissed.length + snoozed.length,
        'sources': [],
      }));

  @override
  Future<Insight> dismiss(String key) async {
    dismissed.add(key);
    insightItems = insightItems.where((i) => i['key'] != key).toList();
    return Insight.fromJson(
        JsonReader({..._insight(key), 'state': 'dismissed'}));
  }

  @override
  Future<Insight> snooze(String key, {int hours = 24}) async {
    snoozed.add(key);
    insightItems = insightItems.where((i) => i['key'] != key).toList();
    return Insight.fromJson(JsonReader({
      ..._insight(key),
      'state': 'snoozed',
      'snoozed_until':
          _now.add(Duration(hours: hours)).toUtc().toIso8601String(),
    }));
  }

  @override
  Future<void> restore(String key) async {}
}

class Calls {
  final commands = <String?>[];
  final opened = <HomeTarget>[];
  final links = <Uri>[];
  final workspaces = <String>[];
  var voice = 0;
}

Widget _app(Widget home,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
    MaterialApp(
      key: UniqueKey(),
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
              disableAnimations: true,
              textScaler: TextScaler.linear(textScale)),
          child: child!),
      home: Scaffold(body: home),
    );

Future<Calls> pumpHome(WidgetTester tester, HomeController controller,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en'),
    Set<String>? features}) async {
  final calls = Calls();
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(_app(
    HomeScreen(
      key: ObjectKey(controller),
      controller: controller,
      onCommand: calls.commands.add,
      onVoice: () => calls.voice++,
      onOpen: calls.opened.add,
      onSwitchWorkspace: calls.workspaces.add,
      availableFeatures: features,
      onOpenLink: (uri) async {
        calls.links.add(uri);
        return true;
      },
      clock: () => _now,
    ),
    locale: locale,
    textScale: textScale,
  ));
  await tester.pumpAndSettle();
  return calls;
}

Future<void> _tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  group('models', () {
    test('parse the summary, insights, pins and commands', () {
      final summary = HomeSummary.fromJson(JsonReader(_summary()));
      expect(summary.displayName, 'Shahzeb');
      expect(summary.focus.first.target, const HomeTarget('approvals'));
      expect(summary.focus.last.progress, 0.75);
      expect(summary.progress!.fraction, 0.75);
      expect(summary.failedSources.map((s) => s.name), ['email']);
      expect(summary.highPriority, 1);
      final insight = Insight.fromJson(JsonReader(_insight('tasks:x')));
      expect(insight.action.isCommand, isTrue);
      expect(insight.hidden, isFalse);
      final pin = HomePin.fromJson(JsonReader({
        'id': 'p',
        'kind': 'feature',
        'label': 'Reports',
        'target': 'reports',
      }));
      expect(pin.kind, PinKind.feature);
      final commands = CommandList.fromJson(JsonReader({
        'items': [
          {
            'id': 'builtin:plan_day',
            'mode': 'personal',
            'label': 'Plan my day',
            'prompt': 'Plan',
            'key': 'plan_day',
            'built_in': true
          }
        ],
        'can_edit': true,
      }));
      expect(commands.items.single.mode, CommandMode.personal);
    });

    test('a missing field or a wrong type fails with the field path', () {
      expect(
          () => HomeSummary.fromJson(JsonReader({'focus': []})),
          throwsA(isA<JsonShapeException>()
              .having((e) => e.path, 'path', 'generated_at')));
      expect(
          () => HomePin.fromJson(JsonReader(
              {'id': 'p', 'kind': 'rocket', 'label': 'x', 'target': 'y'})),
          throwsA(isA<JsonShapeException>()));
      expect(
          () => Insight.fromJson(
              JsonReader({..._insight('k'), 'action': 'open'})),
          throwsA(isA<JsonShapeException>()
              .having((e) => e.path, 'path', 'action')));
    });
  });

  group('controller', () {
    test(
        'loads everything; 403 is the permission state; errors keep the '
        'message', () async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await controller.load();
      expect(controller.phase, HomePhase.ready);
      expect(controller.pins, hasLength(2));
      expect(controller.openInsights, 2);
      expect(controller.modeCommands.single.key, 'check_email');
      controller.selectMode(CommandMode.custom);
      expect(controller.modeCommands.single.label, 'Tyre stock');

      repo.failSummary = 403;
      final denied = HomeController(repo);
      await denied.load();
      expect(denied.phase, HomePhase.denied);
      repo.failSummary = 502;
      final failed = HomeController(repo);
      await failed.load();
      expect(failed.phase, HomePhase.error);
      expect(failed.error, 'Bad gateway');
    });

    test('pins: add, move, rename, remove; a failure is said once', () async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await controller.load();
      expect(
          await controller.addPin(
              kind: PinKind.feature, label: 'Reports', target: 'reports'),
          isTrue);
      expect(controller.pins.last.label, 'Reports');
      await controller.movePin(controller.pins.last, -1);
      expect(repo.reorders.single, ['pin_1', 'pin_3', 'pin_2']);
      expect(await controller.movePin(controller.pins.first, -1), isFalse);
      await controller.renamePin(controller.pins.first, 'Tyre Pulse');
      expect(controller.pins.first.label, 'Tyre Pulse');
      await controller.removePin(controller.pins.first);
      expect(controller.pins.map((p) => p.id), ['pin_3', 'pin_2']);

      repo.failAddPin = true;
      expect(
          await controller.addPin(
              kind: PinKind.feature, label: 'Tasks', target: 'tasks'),
          isFalse);
      expect(controller.lastError, 'This is already pinned.');
      expect(controller.takeNotice(), 'This is already pinned.');
      expect(controller.takeNotice(), isNull);
    });

    test('a pins failure leaves the rest of Home working', () async {
      final repo = FakeHomeRepository()..failPins = true;
      final controller = HomeController(repo);
      await controller.load();
      expect(controller.phase, HomePhase.ready);
      expect(controller.pinsError, 'Pins are unavailable');
    });

    test('insights: dismiss and snooze hide the card and count it', () async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await controller.loadInsights();
      await controller.dismiss(controller.insights.first);
      expect(repo.dismissed, ['tasks:failed:task_9']);
      expect(controller.insights.map((i) => i.key), ['approvals:a1']);
      await controller.snooze(controller.insights.first);
      expect(repo.snoozed, ['approvals:a1']);
      expect(controller.insights, isEmpty);
      expect(controller.hiddenInsights, 2);
    });
  });

  group('Home screen', () {
    testWidgets('greets by name, asks, speaks and opens features',
        (tester) async {
      final controller = HomeController(FakeHomeRepository());
      final calls = await pumpHome(tester, controller);
      expect(find.text('Good morning'), findsOneWidget);
      expect(find.text('Shahzeb 👋'), findsOneWidget);
      expect(find.textContaining('2026'), findsOneWidget);

      await tester.enterText(find.byType(TextField), 'Find high cost vehicles');
      await tester.testTextInput.receiveAction(TextInputAction.send);
      await tester.pumpAndSettle();
      expect(calls.commands, ['Find high cost vehicles']);

      await tester.tap(find.bySemanticsLabel('Speak to ANUM'));
      await tester.pumpAndSettle();
      expect(calls.voice, 1);

      await tester.tap(find.text('Check my emails'));
      await tester.pumpAndSettle();
      expect(calls.commands.last,
          'Check my emails, find today\'s urgent items and summarize them.');
      await tester.tap(find.text('Custom'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Tyre stock'));
      await tester.pumpAndSettle();
      expect(calls.commands.last, 'Check tyre stock on Noon');

      await _tapVisible(tester, find.bySemanticsLabel('ANUM Command'));
      expect(calls.commands.last, isNull);
      await _tapVisible(tester, find.bySemanticsLabel(RegExp(r'^Tasks')));
      expect(calls.opened.last, const HomeTarget('tasks'));
      await _tapVisible(
          tester, find.bySemanticsLabel(RegExp('Your AI agent is ready')));
      expect(calls.voice, 2);
    });

    testWidgets('focus, progress, activity and pins open the right thing',
        (tester) async {
      final controller = HomeController(FakeHomeRepository());
      final calls = await pumpHome(tester, controller);
      await tester.ensureVisible(find.text("Today's Focus"));
      expect(find.text('Approvals waiting: 2'), findsOneWidget);
      expect(find.text('High priority: 1'), findsOneWidget);
      expect(find.text('3 of 4 tasks done today'), findsOneWidget);
      expect(find.textContaining('Some parts could not load: email'),
          findsOneWidget);
      await _tapVisible(tester, find.text('Approvals waiting: 2'));
      expect(calls.opened.last, const HomeTarget('approvals'));
      await _tapVisible(tester, find.text('PMV tyre report review'));
      expect(calls.opened.last, const HomeTarget('tasks', 'task_1'));
      expect(find.textContaining('Running · 75%'), findsOneWidget);

      await _tapVisible(tester, find.text('Tyre report analysis'));
      expect(calls.opened.last, const HomeTarget('tasks', 'task_2'));
      expect(find.text('10 min ago'), findsOneWidget);

      await _tapVisible(tester, find.bySemanticsLabel('TyrePulse'));
      expect(calls.links.single, Uri.parse('https://tyrepulse.example'));
      await _tapVisible(tester, find.bySemanticsLabel('PMV Operations'));
      expect(calls.workspaces.single, 'workspace_pmv');
    });

    testWidgets('pin a web app through the sheet, then manage pins',
        (tester) async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await pumpHome(tester, controller);
      await _tapVisible(tester, find.bySemanticsLabel('Pin an app'));
      expect(find.text('Web app'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Name'), 'VoiceQuote');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Address (https://)'),
          'http://voicequote.example');
      await _tapVisible(tester, find.text('Save'));
      expect(find.textContaining('Use an https:// address'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Address (https://)'),
          'https://voicequote.example');
      await _tapVisible(tester, find.text('Save'));
      expect(repo.pinList.last.label, 'VoiceQuote');
      expect(find.bySemanticsLabel('VoiceQuote'), findsOneWidget);

      await _tapVisible(tester, find.text('Manage').last);
      await tester.tap(find.byTooltip('Move VoiceQuote up'));
      await tester.pumpAndSettle();
      expect(repo.reorders.single, ['pin_1', 'pin_3', 'pin_2']);
      await tester.tap(find.byTooltip('Remove VoiceQuote'));
      await tester.pumpAndSettle();
      expect(repo.pinList.map((p) => p.label), ['TyrePulse', 'PMV Operations']);
    });

    testWidgets('a failed pin save is said inside the sheet', (tester) async {
      final repo = FakeHomeRepository()..failAddPin = true;
      await pumpHome(tester, HomeController(repo));
      await _tapVisible(tester, find.bySemanticsLabel('Pin an app'));
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Name'), 'TyrePulse');
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Address (https://)'),
          'https://tyrepulse.example');
      await _tapVisible(tester, find.text('Save'));
      expect(find.text('This is already pinned.'), findsWidgets);
      expect(find.text('Pin an app'), findsWidgets); // the sheet stays open
    });

    testWidgets('empty, error and permission states say the next step',
        (tester) async {
      final repo = FakeHomeRepository()..empty = true;
      final calls = await pumpHome(tester, HomeController(repo));
      expect(find.text('Nothing needs you right now'), findsOneWidget);
      expect(find.text('No activity yet'), findsOneWidget);
      expect(find.text('Pin the apps and workspaces you open most.'),
          findsOneWidget);
      await _tapVisible(tester, find.text('Ask ANUM').first);
      expect(calls.commands.single, isNull);

      final failing = FakeHomeRepository()..failSummary = 502;
      await pumpHome(tester, HomeController(failing));
      expect(find.text('Home could not load'), findsOneWidget);
      failing.failSummary = 0;
      await _tapVisible(tester, find.text('Try again'));
      expect(find.text('Approvals waiting: 2'), findsOneWidget);

      final denied = FakeHomeRepository()..failSummary = 403;
      await pumpHome(tester, HomeController(denied));
      expect(find.text('No access to this workspace'), findsOneWidget);
    });

    testWidgets(
        'viewers see why there are no quick commands; tiles the shell '
        'cannot open are hidden', (tester) async {
      final repo = FakeHomeRepository()..canEdit = false;
      await pumpHome(tester, HomeController(repo), features: {'tasks', 'chat'});
      expect(
          find.textContaining('Viewers cannot run commands'), findsOneWidget);
      expect(find.text('Add command'), findsNothing);
      expect(find.bySemanticsLabel('Calendar'), findsNothing);
      expect(find.bySemanticsLabel('AI Chat'), findsOneWidget);
    });

    testWidgets('the name can be added from Home', (tester) async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await pumpHome(tester, controller);
      await tester.tap(find.byTooltip('Change the name ANUM greets you by'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'Shahzeb Rahman');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      expect(repo.savedName, 'Shahzeb Rahman');
      expect(find.text('Shahzeb Rahman 👋'), findsOneWidget);
    });

    testWidgets('meets the tap target and label guidelines', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpHome(tester, HomeController(FakeHomeRepository()));
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      handle.dispose();
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      testWidgets('lays out at $width px with 200% text, English and Arabic',
          (tester) async {
        for (final locale in const [Locale('en'), Locale('ar')]) {
          final controller = HomeController(FakeHomeRepository());
          await pumpHome(tester, controller,
              size: Size(width, 900), textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale.languageCode == 'ar') {
            expect(find.text('صباح الخير'), findsOneWidget);
            expect(Directionality.of(tester.element(find.text('صباح الخير'))),
                TextDirection.rtl);
            expect(find.text('موافقات بانتظارك: 2'), findsOneWidget);
          }
        }
      });
    }
  });

  group('ANUM Proactive', () {
    Future<Calls> pumpInsights(WidgetTester tester, HomeController controller,
        {Size size = const Size(390, 844),
        double textScale = 1,
        Locale locale = const Locale('en')}) async {
      final calls = Calls();
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(
        Builder(
          builder: (context) => Center(
            child: TextButton(
              onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                      builder: (_) => ProactiveInsightsScreen(
                          controller: controller,
                          onOpen: calls.opened.add,
                          onCommand: calls.commands.add))),
              child: const Text('open'),
            ),
          ),
        ),
        locale: locale,
        textScale: textScale,
      ));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      return calls;
    }

    testWidgets('finding, question, primary action, Dismiss and Later',
        (tester) async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      final calls = await pumpInsights(tester, controller);
      expect(find.text('ANUM noticed a few things'), findsOneWidget);
      expect(
          find.text('“Quotation” failed and was not retried.'), findsOneWidget);
      expect(find.text('Shall I analyze what went wrong?'), findsOneWidget);
      expect(find.text('An approval is waiting'), findsOneWidget);

      await tester.tap(find.text('Dismiss').first);
      await tester.pumpAndSettle();
      expect(repo.dismissed, ['tasks:failed:task_9']);
      expect(find.text('A task failed'), findsNothing);
      expect(find.text('Hidden: 1'), findsOneWidget);

      await tester.tap(find.text('Review'));
      await tester.pumpAndSettle();
      expect(calls.opened.single, const HomeTarget('tasks', 'task_9'));
      expect(find.text('open'), findsOneWidget); // popped back
    });

    testWidgets('a command action hands the request to ANUM Command',
        (tester) async {
      final controller = HomeController(FakeHomeRepository());
      final calls = await pumpInsights(tester, controller);
      await tester.tap(find.text('Analyze'));
      await tester.pumpAndSettle();
      expect(calls.commands.single, 'Analyze the failed task.');
    });

    testWidgets('Later snoozes; the empty state offers a refresh',
        (tester) async {
      final repo = FakeHomeRepository();
      final controller = HomeController(repo);
      await pumpInsights(tester, controller);
      await tester.tap(find.text('Later').first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Later').first);
      await tester.pumpAndSettle();
      expect(repo.snoozed, hasLength(2));
      expect(find.text('Nothing needs your attention'), findsOneWidget);
      expect(find.text('Refresh'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      testWidgets('lays out at $width px with 200% text, English and Arabic',
          (tester) async {
        for (final locale in const [Locale('en'), Locale('ar')]) {
          await pumpInsights(tester, HomeController(FakeHomeRepository()),
              size: Size(width, 900), textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale.languageCode == 'ar') {
            expect(find.text('فشلت مهمة'), findsOneWidget);
          }
        }
      });
    }

    testWidgets('meets the tap target and label guidelines', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpInsights(tester, HomeController(FakeHomeRepository()));
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      handle.dispose();
    });
  });
}
