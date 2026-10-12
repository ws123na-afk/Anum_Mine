// Automations that do real work (docs/automation.md): trigger and email-step
// rules, waiting step words, the triggers card and its editor (states,
// permissions, the secret shown once, take over), Arabic, and layouts from
// 320 to 1440 wide at 100% and 200% text.
import 'package:anum_mobile/features/automations/automation_models.dart';
import 'package:anum_mobile/features/automations/automation_repository.dart';
import 'package:anum_mobile/features/automations/automation_triggers_card.dart';
import 'package:anum_mobile/features/automations/automations_controller.dart';
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeTriggers
    implements AutomationRepository, AutomationTriggerRepository {
  FakeTriggers({this.role = 'owner', this.fail = false});
  String role;
  bool fail;
  final calls = <String>[];
  final items = <AutomationTriggerItem>[];

  @override
  Future<({String? userId, String? role})> viewer() async =>
      (userId: 'user_me', role: role);

  @override
  Future<List<TriggerEventInfo>> triggerEvents() async => const [
        TriggerEventInfo(
            type: 'email.received',
            label: 'Email received',
            description: 'A new message arrived in a connected mailbox.',
            fields: ['from', 'subject']),
      ];

  @override
  Future<List<AutomationTriggerItem>> triggers(String workflowId) async {
    if (fail) throw const ApiException(503, 'Automation store unavailable');
    return List.of(items);
  }

  AutomationTriggerItem _item(String id, Map<String, Object?> body,
          {String createdBy = 'user_me'}) =>
      AutomationTriggerItem(
          id: id,
          workflowId: 'wf',
          name: body['name'] as String,
          kind: body['kind'] as String? ?? 'event',
          enabled: true,
          eventType: body['event_type'] as String?,
          conditions: [
            for (final c in (body['conditions'] as List? ?? const []))
              Map<String, Object?>.from(c as Map)
          ],
          secretHint: body['kind'] == 'webhook' ? 'k3y4' : null,
          webhookPath: body['kind'] == 'webhook'
              ? '/api/v1/automation/hooks/t/w/$id'
              : null,
          createdBy: createdBy);

  @override
  Future<({AutomationTriggerItem trigger, String? secret})> createTrigger(
      String workflowId, Map<String, Object?> body) async {
    calls.add('create:${body['kind']}:${body['conditions']}');
    final item = _item('trigger_${items.length + 1}', body);
    items.add(item);
    return (
      trigger: item,
      secret: item.webhook ? 'whsec_example_shown_once' : null
    );
  }

  void seed(String name,
          {String createdBy = 'user_me', String kind = 'event'}) =>
      items.add(_item('trigger_${items.length + 1}',
          {'name': name, 'kind': kind, 'event_type': 'email.received'},
          createdBy: createdBy));

  @override
  Future<AutomationTriggerItem> updateTrigger(
          String id, Map<String, Object?> body) async =>
      items.firstWhere((t) => t.id == id);
  @override
  Future<AutomationTriggerItem> setTriggerEnabled(
      String id, bool enabled) async {
    calls.add('enabled:$enabled');
    return items.firstWhere((t) => t.id == id);
  }

  @override
  Future<({AutomationTriggerItem trigger, String? secret})> rotateTriggerSecret(
          String id) async =>
      (trigger: items.firstWhere((t) => t.id == id), secret: 'whsec_rotated');
  @override
  Future<AutomationTriggerItem> takeOverTrigger(String id) async {
    calls.add('takeOver:$id');
    return items.firstWhere((t) => t.id == id);
  }

  @override
  Future<void> deleteTrigger(String id) async => calls.add('delete:$id');
  @override
  Future<AutomationScheduleItem> takeOverSchedule(String id) =>
      throw UnimplementedError();

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Widget _app(Widget child,
        {double width = 390,
        double textScale = 1,
        Locale locale = const Locale('en')}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: MediaQuery(
        data: MediaQueryData(
            size: Size(width, 900), textScaler: TextScaler.linear(textScale)),
        child: Scaffold(body: SingleChildScrollView(child: child)),
      ),
    );

Future<AutomationsController> _controller(FakeTriggers fake) async {
  final controller = AutomationsController(fake);
  final viewer = await fake.viewer();
  controller.role = viewer.role;
  controller.userId = viewer.userId;
  return controller;
}

Future<void> _tap(WidgetTester tester, String text) async {
  final finder = find.text(text).last;
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
}

void main() {
  group('rules', () {
    test('email and notify steps build their input; retries are checked', () {
      final mail = StepDraft(
          id: 'm',
          name: 'Mail',
          action: 'email.send',
          accountId: 'acct',
          to: 'a@example.com; {{trigger.from}}',
          body: ' Shipped ',
          maxAttempts: 2,
          retryDelay: 30);
      final push = StepDraft(id: 'n', name: 'N', action: 'notify', push: true);
      final check = checkWorkflowDraft(
          name: 'Flow',
          description: '',
          overlapPolicy: 'allow',
          steps: [mail, push]);
      expect(check.ok, isTrue);
      final steps = check.body!['steps'] as List;
      expect((steps.first as Map)['input'], {
        'account_id': 'acct',
        'to': ['a@example.com', '{{trigger.from}}'],
        'subject': '',
        'body': 'Shipped'
      });
      expect((steps.first as Map)['retry_delay_seconds'], 30);
      expect((steps.last as Map)['input'], {
        'channels': ['in_app', 'push']
      });
      final bad = checkWorkflowDraft(
          name: 'Flow',
          description: '',
          overlapPolicy: 'allow',
          steps: [
            StepDraft(id: 'm', name: 'M', action: 'email.send', retryDelay: 1)
          ]);
      expect(bad.errors.keys,
          containsAll(['0.accountId', '0.to', '0.body', '0.retryDelay']));
      final roundTrip = StepDraft.fromStep(const AutomationStepDef(
          id: 't',
          name: 'T',
          action: 'task.create',
          input: {'title': 'x', 'prompt': 'y', 'run': false}));
      expect(roundTrip.runTask, isFalse);
      expect(roundTrip.input()['run'], false);
    });

    test('trigger drafts and waiting words', () {
      final check = checkTriggerDraft(
          name: ' Noon ',
          kind: 'event',
          eventType: 'email.received',
          events: const ['email.received'],
          conditions: [
            TriggerCondition(
                field: 'from', operator: 'contains', value: 'noon.com')
          ],
          rateLimit: 30,
          replayWindow: 300);
      expect(check.body!['conditions'], [
        {'field': 'from', 'operator': 'contains', 'value': 'noon.com'}
      ]);
      final bad = checkTriggerDraft(
          name: '',
          kind: 'webhook',
          eventType: '',
          events: const [],
          conditions: [TriggerCondition(field: 'bad field')],
          rateLimit: 0,
          replayWindow: 5);
      expect(bad.errors.keys,
          containsAll(['name', 'rate', 'replay', '0.field', '0.value']));
      const step = AutomationRunStep(
          id: 's',
          name: 'S',
          action: 'email.send',
          status: 'running',
          waitingFor: 'approval',
          output: {'task_id': 'task_2'});
      expect(waitingLabel(step), contains('Waiting for approval'));
      expect(step.taskId, 'task_2');
      expect(canTakeOver('owner', 'me', 'them'), isTrue);
      expect(canTakeOver('member', 'me', 'them'), isFalse);
      expect(canTakeOver('owner', 'me', 'me'), isFalse);
    });
  });

  group('triggers card', () {
    testWidgets('empty, add an event trigger with a condition, then a webhook',
        (tester) async {
      final fake = FakeTriggers();
      final controller = await _controller(fake);
      await tester.pumpWidget(_app(AutomationTriggersCard(
          controller: controller, repository: fake, workflowId: 'wf')));
      await tester.pumpAndSettle();
      expect(find.text('No triggers'), findsOneWidget);

      await _tap(tester, 'Add event trigger');
      await tester.pumpAndSettle();
      await _tap(tester, 'Add trigger');
      await tester.pumpAndSettle();
      expect(find.text('Give the trigger a name.'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextField, 'Trigger name'), 'Noon mail');
      await _tap(tester, 'Add condition');
      await tester.pumpAndSettle();
      await tester.enterText(
          find.widgetWithText(TextFormField, 'Value'), 'noon.com');
      await _tap(tester, 'Add trigger');
      await tester.pumpAndSettle();
      expect(fake.calls.first, startsWith('create:event'));
      expect(find.text('Noon mail'), findsOneWidget);

      await _tap(tester, 'Add webhook');
      await tester.pumpAndSettle();
      await tester.enterText(
          find.widgetWithText(TextField, 'Trigger name'), 'Store');
      await _tap(tester, 'Add trigger');
      await tester.pumpAndSettle();
      expect(find.text('whsec_example_shown_once'), findsOneWidget);
      await _tap(tester, 'I saved the secret');
      await tester.pumpAndSettle();
      expect(find.text('whsec_example_shown_once'), findsNothing);
      expect(find.textContaining('secret ends in k3y4'), findsOneWidget);
    });

    testWidgets(
        'an owner takes over a member\'s trigger; viewers have no controls',
        (tester) async {
      final fake = FakeTriggers()..seed('Member mail', createdBy: 'member_m');
      final controller = await _controller(fake);
      await tester.pumpWidget(_app(AutomationTriggersCard(
          controller: controller, repository: fake, workflowId: 'wf')));
      await tester.pumpAndSettle();
      await _tap(tester, 'Take over');
      await tester.pumpAndSettle();
      await _tap(tester, 'Yes');
      await tester.pumpAndSettle();
      expect(fake.calls, contains('takeOver:trigger_1'));

      final viewer = FakeTriggers(role: 'viewer')..seed('Mail');
      final readOnly = await _controller(viewer);
      await tester.pumpWidget(_app(AutomationTriggersCard(
          controller: readOnly, repository: viewer, workflowId: 'wf2')));
      await tester.pumpAndSettle();
      expect(find.text('Add event trigger'), findsNothing);
      expect(find.text('Turn off'), findsNothing);
      expect(find.text('Mail'), findsOneWidget);
    });

    testWidgets('a failed load says so and retries', (tester) async {
      final fake = FakeTriggers(fail: true);
      final controller = await _controller(fake);
      await tester.pumpWidget(_app(AutomationTriggersCard(
          controller: controller, repository: fake, workflowId: 'wf')));
      await tester.pumpAndSettle();
      expect(find.text('Could not load the triggers'), findsOneWidget);
      fake.fail = false;
      await _tap(tester, 'Try again');
      await tester.pumpAndSettle();
      expect(find.text('No triggers'), findsOneWidget);
    });

    testWidgets('Arabic reads right to left', (tester) async {
      final fake = FakeTriggers()..seed('Mail');
      final controller = await _controller(fake);
      await tester.pumpWidget(_app(
          AutomationTriggersCard(
              controller: controller, repository: fake, workflowId: 'wf'),
          locale: const Locale('ar')));
      await tester.pumpAndSettle();
      expect(find.text('المُشغِّلات'), findsOneWidget);
      expect(find.textContaining('وصل بريد إلكتروني'), findsOneWidget);
      expect(Directionality.of(tester.element(find.text('المُشغِّلات'))),
          TextDirection.rtl);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in [1.0, 2.0]) {
        testWidgets('fits at $width wide, ${scale}x text', (tester) async {
          tester.view.physicalSize = Size(width, 900);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.reset);
          final fake = FakeTriggers()
            ..seed('A long trigger name for Noon and Amazon order emails',
                createdBy: 'member_m')
            ..seed('Store', kind: 'webhook');
          final controller = await _controller(fake);
          await tester.pumpWidget(_app(
              AutomationTriggersCard(
                  controller: controller, repository: fake, workflowId: 'wf'),
              width: width,
              textScale: scale));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await _tap(tester, 'Add event trigger');
          await tester.pumpAndSettle();
          await _tap(tester, 'Add condition');
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
