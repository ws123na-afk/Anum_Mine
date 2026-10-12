// Automations (mockup 11): the schedule line, the switch that really pauses
// and resumes, the Paused tab, templates that fill the editor and suggest a
// schedule, Custom Automation, and layouts in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/automations/automation_models.dart';
import 'package:anum_mobile/features/automations/automation_repository.dart';
import 'package:anum_mobile/features/automations/automations_controller.dart';
import 'package:anum_mobile/features/workspace/automations_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'lists_harness.dart';

final _now = DateTime.utc(2026, 10, 12, 8);

AutomationWorkflow _wf(String id, String name,
        {String status = 'active', String createdBy = 'user_me'}) =>
    AutomationWorkflow(
        id: id,
        name: name,
        description: '',
        status: status,
        version: 1,
        overlapPolicy: 'skip',
        steps: const [
          AutomationStepDef(
              id: 's', name: 'Report', action: 'task.create', input: {}),
        ],
        updatedAt: _now,
        createdBy: createdBy);

class _Fake implements AutomationRepository {
  _Fake(this.items, {this.role = 'owner'});
  List<AutomationWorkflow> items;
  String role;
  final calls = <String>[];
  String? refuseToggle;
  Map<String, Object?>? created;

  @override
  Future<({String? userId, String? role})> viewer() async =>
      (userId: 'user_me', role: role);

  @override
  Future<AutomationPage<AutomationWorkflow>> workflows(
      {String query = '',
      String status = '',
      int limit = 20,
      int offset = 0}) async {
    calls.add('list:$status');
    return AutomationPage(
        items.where((w) => status.isEmpty || w.status == status).toList(),
        hasMore: false);
  }

  @override
  Future<List<AutomationScheduleItem>> schedules(String workflowId) async {
    calls.add('schedules:$workflowId');
    return [
      AutomationScheduleItem(
          id: 'sch_1',
          workflowId: 'wf_1',
          name: 'Mornings',
          cron: '0 8 * * *',
          description: 'Every day at 08:00',
          timezone: 'Asia/Riyadh',
          enabled: true),
    ];
  }

  @override
  Future<AutomationWorkflow> setWorkflowEnabled(String id, bool enabled) async {
    calls.add('enabled:$id:$enabled');
    if (refuseToggle case final message?) throw ApiException(403, message);
    final i = items.indexWhere((w) => w.id == id);
    return items[i] =
        _wf(id, items[i].name, status: enabled ? 'active' : 'disabled');
  }

  @override
  Future<AutomationWorkflow> createWorkflow(Map<String, Object?> body) async {
    created = body;
    final w = _wf('wf_new', body['name']! as String);
    items = [w, ...items];
    return w;
  }

  @override
  Future<AutomationWorkflow> workflow(String id) async =>
      items.firstWhere((w) => w.id == id);

  @override
  Future<AutomationPage<AutomationRunItem>> runs(String workflowId,
          {String status = '', int limit = 10, int offset = 0}) async =>
      const AutomationPage([], hasMore: false);

  @override
  Future<SchedulePreview> preview(String cron, String timezone) async =>
      SchedulePreview(
          description: 'Described $cron', timezone: timezone, nextRuns: []);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

void main() {
  testWidgets('rows show the schedule; the switch pauses and resumes',
      (tester) async {
    final fake =
        _Fake([_wf('wf_1', 'Daily Tyre Report'), _wf('wf_2', 'Digest')]);
    await pumpList(
        tester, AutomationsScreen(automations: AutomationsController(fake)));
    expect(fake.calls, contains('schedules:'));
    expect(find.text('Every day at 08:00'), findsOneWidget);
    expect(find.text('Runs when you start it'), findsOneWidget);
    await tester.tap(find.byKey(const Key('auto-switch-wf_1')));
    await tester.pumpAndSettle();
    expect(fake.calls, contains('enabled:wf_1:false'));
    expect(find.text('Paused'), findsWidgets);
    expect(
        tester.widget<Switch>(find.byKey(const Key('auto-switch-wf_1'))).value,
        isFalse);
    await tester.ensureVisible(find.byKey(const Key('auto-tab-paused')));
    await tester.tap(find.byKey(const Key('auto-tab-paused')));
    await tester.pumpAndSettle();
    expect(fake.calls.last, 'list:disabled');
    expect(find.text('Digest'), findsNothing);
    await expectTapTargets(tester);
  });

  testWidgets('a refused switch says why and stays where the server is',
      (tester) async {
    final fake = _Fake([_wf('wf_1', 'Report')])
      ..refuseToggle = 'Only the creator or an owner can change this.';
    await pumpList(
        tester, AutomationsScreen(automations: AutomationsController(fake)));
    await tester.tap(find.byKey(const Key('auto-switch-wf_1')));
    await tester.pumpAndSettle();
    expect(find.text('Only the creator or an owner can change this.'),
        findsOneWidget);
    expect(
        tester.widget<Switch>(find.byKey(const Key('auto-switch-wf_1'))).value,
        isTrue);
  });

  testWidgets('a template fills the editor and offers its schedule',
      (tester) async {
    final fake = _Fake([]);
    await pumpList(
        tester, AutomationsScreen(automations: AutomationsController(fake)),
        height: 2000);
    await tester.ensureVisible(find.byKey(const Key('auto-tab-templates')));
    await tester.tap(find.byKey(const Key('auto-tab-templates')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('auto-template-daily')));
    await tester.pumpAndSettle();
    expect(find.text('Daily business report'), findsWidgets);
    final submit = find.widgetWithText(FilledButton, 'Create automation');
    await tester.ensureVisible(submit);
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(fake.created!['name'], 'Daily business report');
    final steps = fake.created!['steps']! as List<Object?>;
    expect((steps.single! as Map)['action'], 'task.create');
    // The detail opens with the suggested schedule, not yet saved.
    expect(find.text('Every day at 08:00'), findsWidgets);
    expect(find.text('0 8 * * *'), findsOneWidget);
  });

  testWidgets('viewers see templates locked and no switches', (tester) async {
    final fake =
        _Fake([_wf('wf_1', 'Report', createdBy: 'other')], role: 'viewer');
    await pumpList(
        tester, AutomationsScreen(automations: AutomationsController(fake)));
    expect(find.byType(Switch), findsNothing);
    await tester.ensureVisible(find.byKey(const Key('auto-tab-templates')));
    await tester.tap(find.byKey(const Key('auto-tab-templates')));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.lock_outline), findsWidgets);
  });

  for (final width in listWidths) {
    testWidgets('lays out at $width wide, 200% text, English and Arabic',
        (tester) async {
      final fake = _Fake([
        _wf('wf_1', 'A long automation name that wraps on a small phone'),
        _wf('wf_2', 'Paused one', status: 'disabled'),
      ]);
      for (final locale in ['en', 'ar']) {
        await pumpList(
            tester, AutomationsScreen(automations: AutomationsController(fake)),
            width: width, text: 2, locale: locale);
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.byKey(const Key('auto-tab-templates')));
        await tester.tap(find.byKey(const Key('auto-tab-templates')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      }
      expect(find.text('تقرير العمل اليومي'), findsOneWidget);
    });
  }
}
