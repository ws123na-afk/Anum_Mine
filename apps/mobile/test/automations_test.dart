// Automations (docs/automation.md): draft rules, the API contract, and the
// screens' states, permissions and layouts at phone to desktop widths.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/automations/automation_models.dart';
import 'package:anum_mobile/features/automations/automation_repository.dart';
import 'package:anum_mobile/features/automations/automations_controller.dart';
import 'package:anum_mobile/features/workspace/automations_screen.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.utc(2026, 10, 11, 8);

AutomationWorkflow _workflow(String id, String name,
        {String status = 'active', String? createdBy = 'user_me'}) =>
    AutomationWorkflow(
      id: id,
      name: name,
      description: 'Summarises the week for the team',
      status: status,
      version: 2,
      overlapPolicy: 'skip',
      steps: const [
        AutomationStepDef(
            id: 'make',
            name: 'Create digest',
            action: 'task.create',
            input: {'title': 'Digest', 'prompt': 'Summarise'}),
      ],
      updatedAt: _now,
      createdBy: createdBy,
      lastRunAt: _now.subtract(const Duration(hours: 2)),
      lastRunStatus: 'failed',
      nextRunAt: _now.add(const Duration(hours: 1)),
      runCount: 1,
      scheduleCount: 1,
    );

class FakeAutomations implements AutomationRepository {
  FakeAutomations(
      {this.role = 'owner', List<AutomationWorkflow>? items, this.failList = 0})
      : items = items ?? [];
  String role;
  List<AutomationWorkflow> items;
  int failList;
  final calls = <String>[];

  @override
  Future<({String? userId, String? role})> viewer() async =>
      (userId: 'user_me', role: role);
  @override
  Future<AutomationPage<AutomationWorkflow>> workflows(
      {String query = '',
      String status = '',
      int limit = 20,
      int offset = 0}) async {
    calls.add('list:$query:$status:$offset');
    if (failList > 0) {
      failList--;
      throw const ApiException(503, 'Automation store unavailable');
    }
    final matching = items
        .where((w) =>
            w.name.toLowerCase().contains(query.toLowerCase()) &&
            (status.isEmpty || w.status == status))
        .toList();
    final page = matching.skip(offset).take(limit).toList();
    return AutomationPage(page, hasMore: offset + limit < matching.length);
  }

  @override
  Future<AutomationWorkflow> workflow(String id) async =>
      items.firstWhere((w) => w.id == id);
  @override
  Future<AutomationWorkflow> createWorkflow(Map<String, Object?> body) async {
    calls.add('create');
    final w = _workflow('wf_new', body['name'] as String);
    items = [w, ...items];
    return w;
  }

  @override
  Future<AutomationWorkflow> updateWorkflow(
      String id, Map<String, Object?> body) async {
    calls.add('update');
    return items.firstWhere((w) => w.id == id);
  }

  @override
  Future<AutomationWorkflow> setWorkflowEnabled(String id, bool enabled) async {
    calls.add('enabled:$enabled');
    return items.firstWhere((w) => w.id == id);
  }

  @override
  Future<void> deleteWorkflow(String id) async => calls.add('delete');
  @override
  Future<List<AutomationScheduleItem>> schedules(String workflowId) async => [
        AutomationScheduleItem(
            id: 's1',
            workflowId: workflowId,
            name: 'Weekday mornings',
            cron: '0 9 * * 1-5',
            description: 'At 09:00, Monday through Friday',
            timezone: 'Asia/Riyadh',
            enabled: true,
            nextRunAt: _now.add(const Duration(hours: 1)),
            lastRunAt: _now.subtract(const Duration(days: 1)),
            lastOutcome: 'skipped_overlap',
            createdBy: 'user_me'),
      ];
  @override
  Future<SchedulePreview> preview(String cron, String timezone) async {
    if (cron.startsWith('0 25')) {
      throw const ApiException(422, 'Request validation failed',
          details: ['Hour field: 25 is outside 0-23.']);
    }
    return SchedulePreview(
        description: 'At 09:00, Monday through Friday',
        timezone: timezone,
        nextRuns: [_now.add(const Duration(hours: 1))]);
  }

  @override
  Future<AutomationScheduleItem> createSchedule(
          {required String workflowId,
          required String name,
          required String cron,
          required String timezone}) async =>
      (await schedules(workflowId)).first;
  @override
  Future<AutomationScheduleItem> updateSchedule(String id,
          {String? name, String? cron, String? timezone}) async =>
      (await schedules('wf')).first;
  @override
  Future<AutomationScheduleItem> setScheduleEnabled(
          String id, bool enabled) async =>
      (await schedules('wf')).first;
  @override
  Future<void> deleteSchedule(String id) async {}
  @override
  Future<AutomationPage<AutomationRunItem>> runs(String workflowId,
          {String status = '', int limit = 10, int offset = 0}) async =>
      AutomationPage([
        AutomationRunItem(
          id: 'automation_run_1',
          workflowId: workflowId,
          status: 'failed',
          trigger: 'schedule',
          error: 'Upstream service returned 503',
          createdAt: _now,
          updatedAt: _now,
          createdBy: 'user_me',
          workflowVersion: 2,
          steps: const [
            AutomationRunStep(
                id: 'make',
                name: 'Create digest',
                action: 'task.create',
                status: 'completed',
                attempt: 1,
                output: {'task_id': 'task_1', 'title': 'Digest'}),
          ],
        ),
      ], hasMore: false);
  @override
  Future<AutomationRunItem> start(String workflowId, String key) async {
    calls.add('start:${key.isNotEmpty}');
    return (await runs(workflowId)).items.first;
  }

  @override
  Future<AutomationRunItem> transition(String runId, String action) async {
    calls.add('transition:$action');
    return (await runs('wf')).items.first;
  }
}

Widget _app(Widget child, {double width = 390, double textScale = 1}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      home: MediaQuery(
        data: MediaQueryData(
            size: Size(width, 900), textScaler: TextScaler.linear(textScale)),
        child: Scaffold(body: child),
      ),
    );

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  ApiResponse next = const ApiResponse(statusCode: 200, body: {'data': []});
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    return next;
  }
}

Future<AnumApiClient> _client(_Transport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 't',
          workspaceId: 'w',
          userId: 'user_me',
          roles: ['owner'])));
  return AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions);
}

void main() {
  group('drafts', () {
    test('a task step needs a title and a prompt; ids are unique', () {
      final a = StepDraft(id: 'x', name: '', action: 'task.create');
      final b = StepDraft(id: 'x', name: 'B', action: 'task.execute');
      final check = checkWorkflowDraft(
          name: ' ', description: '', overlapPolicy: 'allow', steps: [a, b]);
      expect(check.ok, isFalse);
      expect(check.errors['name'], 'Give the automation a name.');
      expect(check.errors['0.title'], contains('needs a title'));
      expect(check.errors['0.prompt'], contains('prompt'));
      expect(check.errors['1.id'], contains('already used'));
      expect(check.errors['1.action'], contains('no longer supported'));
    });

    test('a valid draft builds the API body and keeps unknown input keys', () {
      final draft = StepDraft.fromStep(const AutomationStepDef(
          id: 'make',
          name: 'Make',
          action: 'task.create',
          input: {'title': 'T', 'prompt': 'P', 'priority': 'high'}));
      final check = checkWorkflowDraft(
          name: ' Digest ',
          description: '',
          overlapPolicy: 'skip',
          steps: [
            draft,
            newStepDraft([draft], 'pause')
          ]);
      expect(check.ok, isTrue);
      expect(check.body!['name'], 'Digest');
      expect(check.body!['overlap_policy'], 'skip');
      final steps = check.body!['steps'] as List;
      expect((steps.first as Map)['input'],
          {'priority': 'high', 'title': 'T', 'prompt': 'P'});
      expect((steps.last as Map)['id'], 'step-2');
    });

    test('labels, cron shape and permissions', () {
      expect(cronShapeError('0 9 * * 1-5'), isNull);
      expect(cronShapeError('0 9 * *'), contains('this one has 4'));
      expect(
          outcomeLabel('skipped_overlap'), 'Skipped: a run was still active');
      expect(statusLabel('disabled'), 'Paused');
      expect(relativeWhen(_now.add(const Duration(minutes: 20)), now: _now),
          'in 20 min');
      expect(relativeWhen(_now.subtract(const Duration(hours: 3)), now: _now),
          '3 h ago');
      expect(canManageAutomations('viewer'), isFalse);
      expect(canEditAutomation('member', 'me', 'someone'), isFalse);
      expect(canEditAutomation('member', 'me', 'me'), isTrue);
      expect(canEditAutomation('owner', 'me', 'someone'), isTrue);
    });
  });

  group('API contract', () {
    test('lists ask for one more than a page and encode filters', () async {
      final transport = _Transport();
      final repository = ApiAutomationRepository(await _client(transport));
      transport.next = ApiResponse(statusCode: 200, body: {
        'data': [
          for (var i = 0; i < 3; i++)
            {
              'id': 'wf_$i',
              'name': 'W $i',
              'steps': <Object?>[],
              'updated_at': '2026-10-11T08:00:00Z'
            }
        ]
      });
      final page = await repository.workflows(
          query: 'weekly report', status: 'active', limit: 2, offset: 4);
      expect(page.items.length, 2);
      expect(page.hasMore, isTrue);
      final uri = transport.requests.last.uri;
      expect(uri.path, '/api/v1/automation/workflows');
      expect(uri.queryParameters, {
        'query': 'weekly report',
        'status': 'active',
        'limit': '3',
        'offset': '4'
      });
    });

    test('run now sends an idempotency key; 422 details read as sentences',
        () async {
      final transport = _Transport();
      final repository = ApiAutomationRepository(await _client(transport));
      transport.next = const ApiResponse(statusCode: 201, body: {
        'id': 'r',
        'workflow_id': 'wf',
        'status': 'completed',
        'steps': [],
      });
      await repository.start('wf', 'key-1');
      expect(transport.requests.last.headers['Idempotency-Key'], 'key-1');
      expect(transport.requests.last.uri.path,
          '/api/v1/automation/workflows/wf/runs');

      transport.next = const ApiResponse(statusCode: 422, body: {
        'error': {
          'code': 'validation_error',
          'message': 'Request validation failed',
          'details': [
            {
              'location': 'body.cron',
              'message': 'Value error, Hour field: 25 is outside 0-23.'
            }
          ]
        }
      });
      await expectLater(
          repository.preview('0 25 * * *', 'UTC'),
          throwsA(isA<ApiException>().having((e) => automationErrorText(e),
              'readable', 'Hour field: 25 is outside 0-23.')));
    });
  });

  group('screens', () {
    testWidgets('lists automations and opens one with schedules and runs',
        (tester) async {
      final repo = FakeAutomations(items: [_workflow('wf_1', 'Weekly digest')]);
      final controller = AutomationsController(repo);
      await tester.pumpWidget(_app(AutomationsScreen(automations: controller)));
      await tester.pumpAndSettle();
      expect(find.text('Weekly digest'), findsOneWidget);
      await tester.tap(find.text('Weekly digest'));
      await tester.pumpAndSettle();
      expect(find.text('Weekday mornings'), findsOneWidget);
      expect(find.textContaining('Skipped: a run was still active'),
          findsOneWidget);
      await tester.scrollUntilVisible(find.text('Run history'), 200);
      expect(
          find.textContaining('Upstream service returned 503'), findsOneWidget);
      await tester.tap(find.text('Run now'));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('start:true'));
    });

    testWidgets('empty list offers to create; validation explains the fix',
        (tester) async {
      final repo = FakeAutomations();
      await tester.pumpWidget(
          _app(AutomationsScreen(automations: AutomationsController(repo))));
      await tester.pumpAndSettle();
      expect(find.text('No automations yet'), findsOneWidget);
      await tester.tap(find.byKey(const Key('auto-custom')));
      await tester.pumpAndSettle();
      final submit = find.widgetWithText(FilledButton, 'Create automation');
      await tester.ensureVisible(submit);
      await tester.pumpAndSettle();
      await tester.tap(submit);
      await tester.pumpAndSettle();
      expect(find.text('Give the automation a name.'), findsWidgets);
      expect(find.text('Step 1: the task needs a title.'), findsOneWidget);
      expect(repo.calls, isNot(contains('create')));
    });

    testWidgets('viewers get an explanation instead of controls',
        (tester) async {
      final repo = FakeAutomations(
          role: 'viewer', items: [_workflow('wf_1', 'Weekly digest')]);
      await tester.pumpWidget(
          _app(AutomationsScreen(automations: AutomationsController(repo))));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('auto-viewer-hint')), findsOneWidget);
      expect(find.byKey(const Key('auto-custom')), findsNothing);
      expect(find.byTooltip('New automation'), findsNothing);
      // Not their automation to change: no switch, a status pill instead.
      expect(find.byKey(const Key('auto-switch-wf_1')), findsNothing);
      await tester.tap(find.text('Weekly digest'));
      await tester.pumpAndSettle();
      expect(find.text('Run now'), findsNothing);
    });

    testWidgets('members see who can change an automation they did not create',
        (tester) async {
      final repo = FakeAutomations(
          role: 'member',
          items: [_workflow('wf_1', 'Owner report', createdBy: 'owner_x')]);
      await tester.pumpWidget(
          _app(AutomationsScreen(automations: AutomationsController(repo))));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Owner report'));
      await tester.pumpAndSettle();
      expect(find.text('Run now'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, 'Edit'), findsNothing);
      expect(find.text('Only owner_x or a workspace owner can change this.'),
          findsOneWidget);
    });

    testWidgets('a failed load says what failed and retries', (tester) async {
      final repo =
          FakeAutomations(failList: 1, items: [_workflow('wf_1', 'Digest')]);
      await tester.pumpWidget(
          _app(AutomationsScreen(automations: AutomationsController(repo))));
      await tester.pumpAndSettle();
      expect(find.text('Automation store unavailable'), findsOneWidget);
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Digest'), findsOneWidget);
    });

    testWidgets('long lists page with Show more', (tester) async {
      final repo = FakeAutomations(items: [
        for (var i = 0; i < 25; i++) _workflow('wf_$i', 'Automation $i')
      ]);
      await tester.pumpWidget(
          _app(AutomationsScreen(automations: AutomationsController(repo))));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('auto-load-more')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('auto-load-more')));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('list:::20'));
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in [1.0, 2.0]) {
        testWidgets('lays out at ${width.toInt()} px, text x$scale',
            (tester) async {
          tester.view.physicalSize = Size(width, 900);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.reset);
          final repo = FakeAutomations(items: [
            _workflow(
                'wf_1', 'A long automation name that has to wrap on phones')
          ]);
          await tester.pumpWidget(_app(
              AutomationsScreen(automations: AutomationsController(repo)),
              width: width,
              textScale: scale));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          final row = find.textContaining('A long automation name').first;
          await tester.ensureVisible(row);
          await tester.pumpAndSettle();
          await tester.tap(row);
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          // The schedule sheet and the editor at the same size.
          final add = find.widgetWithText(TextButton, 'Add');
          await tester.scrollUntilVisible(add, 150,
              scrollable: find.byType(Scrollable).first);
          await tester.pumpAndSettle();
          await tester.tap(add);
          await tester.pumpAndSettle();
          expect(find.textContaining('At 09:00, Monday through Friday'),
              findsWidgets);
          expect(tester.takeException(), isNull);
          await tester.tapAt(const Offset(5, 5));
          await tester.pumpAndSettle();
          final edit = find.widgetWithText(OutlinedButton, 'Edit');
          await tester.scrollUntilVisible(edit, -150,
              scrollable: find.byType(Scrollable).first);
          await tester.pumpAndSettle();
          await tester.tap(edit);
          await tester.pumpAndSettle();
          expect(find.text('Edit automation'), findsOneWidget);
          expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
