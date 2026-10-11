// Agents (docs/agents.md): the strings in both languages, the API calls the
// repository makes, the drafts it sends, and the screens (list, detail tabs,
// editor) in every state, from a 320 px phone at 200 percent text to 1440x900
// and in Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/agents/agents_controller.dart';
import 'package:anum_mobile/features/agents/agents_models.dart';
import 'package:anum_mobile/features/agents/agents_repository.dart';
import 'package:anum_mobile/features/agents/agents_screen.dart';
import 'package:anum_mobile/features/agents/agents_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const _now = '2026-10-11T09:00:00Z';

JsonMap agentJson(String id, String name,
        {String category = 'work',
        String status = 'active',
        bool canEdit = true,
        bool canRun = true,
        String sharing = 'private'}) =>
    {
      'id': id,
      'name': name,
      'icon': 'truck',
      'color': '#0f766e',
      'category': category,
      'department': category == 'work' ? 'Logistics' : null,
      'description': '$name helps the team.',
      'instructions': 'Keep it short.',
      'model_route': 'capable',
      'allowed_skills': <String>[],
      'allowed_tools': ['web_search'],
      'autonomy': {'read': 'auto', 'write': 'ask', 'external': 'ask'},
      'knowledge_mode': 'none',
      'knowledge_sources': <Object?>[],
      'sharing': sharing,
      'status': status,
      'owner_user_id': 'user_test',
      'version': 1,
      'can_edit': canEdit,
      'can_run': canRun,
      'updated_at': _now,
      'unavailable_tools': <String>[],
      'unavailable_skills': <String>[],
    };

final JsonMap catalogJson = {
  'tools': [
    {
      'name': 'anum.respond',
      'description': 'Text',
      'category': 'read',
      'always_on': true,
      'available_to_you': true
    },
    {
      'name': 'web_search',
      'description': 'Search',
      'category': 'read',
      'always_on': false,
      'available_to_you': true
    },
    {
      'name': 'fleet.reset',
      'description': 'Reset',
      'category': 'write',
      'always_on': false,
      'available_to_you': false,
      'reason': 'Needs the owner role, which you do not have.'
    },
  ],
  'skills': [
    {
      'id': 'anum.web-search',
      'name': 'Web search',
      'description': 'Looks things up.',
      'source': 'builtin',
      'required_tools': ['web_search']
    },
  ],
  'templates': [
    {
      'id': 'fleet',
      'icon': 'truck',
      'color': '#0f766e',
      'category': 'work',
      'instructions': 'Plan tyre maintenance.',
      'model_route': 'capable',
      'allowed_tools': <String>[],
      'allowed_skills': <String>[],
      'knowledge_mode': 'workspace',
      'missing_capabilities': ['Tyre-planning tools (TyrePulse connector)']
    },
  ],
  'icons': ['bot', 'truck'],
  'autonomy_floor': {'read': 'auto', 'write': 'ask', 'external': 'ask'},
};

class FakeAgentsRepository implements AgentsRepository {
  FakeAgentsRepository({List<JsonMap>? agents, this.failList = false})
      : agents = agents ??
            [
              agentJson('agent_fleet', 'Fleet desk', sharing: 'workspace'),
              agentJson('agent_shop', 'Groceries', category: 'personal'),
            ];
  List<JsonMap> agents;
  bool failList;
  final stored = <AgentMemory>[
    AgentMemory(
        id: 'm1',
        content: 'Truck 12 runs on Michelin tyres.',
        source: 'manual',
        createdAt: DateTime.utc(2026, 10, 1)),
  ];
  final created = <Map<String, Object?>>[];
  final updates = <Map<String, Object?>>[];
  final runs = <String>[];

  @override
  Future<AgentCatalog> catalog() async =>
      AgentCatalog.fromReader(JsonReader(catalogJson));

  @override
  Future<AgentPage> list(
      {String? category,
      String query = '',
      String? cursor,
      int limit = 20}) async {
    if (failList) {
      throw const ApiException(503, 'The database is not reachable.');
    }
    final rows = agents
        .where((a) =>
            query.isEmpty ||
            '${a['name']} ${a['description']}'
                .toLowerCase()
                .contains(query.toLowerCase()))
        .toList();
    final counts = {
      'all': rows.length,
      for (final c in agentCategories)
        c: rows.where((a) => a['category'] == c).length,
    };
    return AgentPage.fromReader(JsonReader({
      'items': rows
          .where((a) => category == null || a['category'] == category)
          .toList(),
      'next_cursor': null,
      'counts': counts,
    }));
  }

  @override
  Future<AgentProfile> get(String id) async {
    final found = agents.where((a) => a['id'] == id);
    if (found.isEmpty) throw const ApiException(404, 'Agent not found.');
    return AgentProfile.fromReader(JsonReader(found.first));
  }

  @override
  Future<AgentProfile> create(Map<String, Object?> body) async {
    created.add(body);
    final json = {...agentJson('agent_new', '${body['name']}'), ...body};
    agents = [json, ...agents];
    return AgentProfile.fromReader(JsonReader(json));
  }

  @override
  Future<AgentProfile> update(String id, Map<String, Object?> body) async {
    updates.add(body);
    final index = agents.indexWhere((a) => a['id'] == id);
    final next = {...agents[index], ...body}..['version'] =
        (agents[index]['version']! as int) + 1;
    agents[index] = next;
    return AgentProfile.fromReader(JsonReader(next));
  }

  @override
  Future<void> delete(String id) async =>
      agents.removeWhere((a) => a['id'] == id);

  @override
  Future<AgentMemoryPage> memories(String id, {String query = ''}) async =>
      AgentMemoryPage(items: List.of(stored), total: stored.length);

  @override
  Future<AgentMemory> addMemory(String id, String content) async {
    final memory = AgentMemory(
        id: 'm${stored.length + 2}',
        content: content,
        source: 'manual',
        createdAt: DateTime.utc(2026));
    stored.insert(0, memory);
    return memory;
  }

  @override
  Future<void> deleteMemory(String id, String memoryId) async =>
      stored.removeWhere((m) => m.id == memoryId);

  @override
  Future<List<AgentTask>> recentTasks(String id) async => [
        for (final prompt in runs)
          AgentTask(
              id: 't',
              title: prompt,
              status: 'completed',
              createdAt: DateTime.utc(2026))
      ];

  @override
  Future<AgentRunOutcome> runTask(String id, String prompt,
      {String? title}) async {
    runs.add(prompt);
    return AgentRunOutcome(
        taskTitle: prompt,
        status: 'completed',
        waitingApproval: false,
        result: 'Rotate the front tyres on Tuesday.');
  }
}

Future<void> pump(WidgetTester tester, Widget child,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(textScale)),
      child: child!,
    ),
    home: child,
  ));
  await tester.pumpAndSettle();
}

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path == '/api/v1/tasks' && request.method == 'POST') {
      return const ApiResponse(
          statusCode: 201, body: {'id': 'task_1', 'title': 'x'});
    }
    if (path == '/api/v1/tasks/task_1/run') {
      return const ApiResponse(statusCode: 200, body: {
        'task': {'id': 'task_1', 'title': 'Plan', 'status': 'waiting_approval'},
        'run': {'id': 'run_1', 'status': 'waiting_approval'},
        'approval': {'id': 'approval_1'},
      });
    }
    return const ApiResponse(
        statusCode: 200,
        body: {'items': <Object?>[], 'counts': <String, Object?>{}});
  }
}

void main() {
  test('both languages have the same keys and placeholders', () {
    expect(AgentsText.ar.keys.toSet(), AgentsText.en.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in AgentsText.en.keys) {
      Set<String> names(String text) =>
          placeholder.allMatches(text).map((m) => m.group(1)!).toSet();
      expect(names(AgentsText.ar[key]!), names(AgentsText.en[key]!),
          reason: key);
    }
  });

  test('the repository filters, and runs tasks as the agent', () async {
    final transport = _Transport();
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession.fromJson({
      'access_token': 'token',
      'token_type': 'bearer',
      'expires_at': DateTime.now()
          .toUtc()
          .add(const Duration(hours: 1))
          .toIso8601String(),
      'context': {
        'tenant_id': 't',
        'workspace_id': 'w',
        'user_id': 'u',
        'roles': ['owner']
      },
    }));
    final repository = ApiAgentsRepository(AnumApiClient(
        baseUri: Uri.parse('http://localhost:8000'),
        transport: transport,
        sessions: sessions));
    await repository.list(category: 'work', query: ' tyre ');
    expect(transport.requests.last.uri.query, 'limit=20&category=work&q=tyre');
    final outcome = await repository.runTask('agent_1', 'Plan next week');
    expect(transport.requests[1].body, {
      'title': 'Plan next week',
      'prompt': 'Plan next week',
      'agent_id': 'agent_1'
    });
    expect(outcome.waitingApproval, isTrue);
  });

  test('a draft sends only changes, and a built-in skill brings its tool', () {
    final agent = AgentProfile.fromReader(JsonReader(agentJson('a', 'Fleet')));
    final draft = AgentDraft.fromAgent(agent)..name = 'Fleet planner';
    expect(draft.changesFrom(agent), {'name': 'Fleet planner', 'version': 1});
    final catalog = AgentCatalog.fromReader(JsonReader(catalogJson));
    final blank = AgentDraft()
      ..toggleSkill(catalog.skills.first, true, catalog);
    expect(blank.allowedTools, ['web_search']);
    expect(AgentDraft().problem(), 'errNameRequired');
    expect(autonomyLevelsFor('external'), ['off', 'ask']);
  });

  testWidgets('the list filters by category and opens an agent with its tabs',
      (tester) async {
    final repository = FakeAgentsRepository();
    await pump(
        tester,
        AgentsScreen(
            key: UniqueKey(), controller: AgentsController(repository)));
    expect(find.text('Fleet desk'), findsOneWidget);
    expect(find.text('Groceries'), findsOneWidget);
    await tester.tap(find.text('Personal 1'));
    await tester.pumpAndSettle();
    expect(find.text('Fleet desk'), findsNothing);
    await tester.tap(find.text('All 2'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Fleet desk'));
    await tester.pumpAndSettle();
    for (final label in [
      'Chat',
      'Call',
      'Edit',
      'Overview',
      'Skills',
      'Memory',
      'Settings'
    ]) {
      expect(find.text(label), findsWidgets, reason: label);
    }
    await tester.enterText(
        find.widgetWithText(TextField, 'What should Fleet desk do?'),
        'Plan rotations');
    await tester.tap(find.text('Run task'));
    await tester.pumpAndSettle();
    expect(find.text('Rotate the front tyres on Tuesday.'), findsOneWidget);
    expect(repository.runs, ['Plan rotations']);
    await tester.tap(find.text('Memory'));
    await tester.pumpAndSettle();
    expect(find.text('Truck 12 runs on Michelin tyres.'), findsOneWidget);
    await tester.enterText(
        find.widgetWithText(TextField, 'Add a memory'), 'Depot in Jeddah');
    await tester.tap(find.text('Save memory'));
    await tester.pumpAndSettle();
    expect(find.text('Depot in Jeddah'), findsOneWidget);
  });

  testWidgets('empty, no-match and error states offer the next step',
      (tester) async {
    final empty = FakeAgentsRepository(agents: []);
    await pump(tester,
        AgentsScreen(key: UniqueKey(), controller: AgentsController(empty)));
    expect(find.text('No agents yet'), findsOneWidget);
    final failing = FakeAgentsRepository(failList: true);
    await pump(tester,
        AgentsScreen(key: UniqueKey(), controller: AgentsController(failing)));
    expect(find.text("Couldn't load agents"), findsOneWidget);
    expect(find.text('The database is not reachable.'), findsOneWidget);
    failing.failList = false;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Fleet desk'), findsOneWidget);
  });

  testWidgets('create from a template through the editor', (tester) async {
    final repository = FakeAgentsRepository();
    await pump(
        tester,
        AgentsScreen(
            key: UniqueKey(), controller: AgentsController(repository)));
    await tester.tap(find.text('Create agent'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Fleet Agent'));
    await tester.pumpAndSettle();
    expect(
        find.text(
            'Not connected yet: Tyre-planning tools (TyrePulse connector)'),
        findsOneWidget);
    final submit = find.widgetWithText(FilledButton, 'Create agent');
    await tester.ensureVisible(submit);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(repository.created.single, containsPair('template_id', 'fleet'));
    expect(repository.created.single, containsPair('name', 'Fleet Agent'));
    expect(
        repository.created.single, containsPair('knowledge_mode', 'workspace'));
  });

  testWidgets('a viewer reads a shared agent but cannot run or change it',
      (tester) async {
    final repository = FakeAgentsRepository(agents: [
      agentJson('agent_fleet', 'Fleet desk', canEdit: false, canRun: false)
    ]);
    await pump(
        tester,
        AgentDetailScreen(
            controller: AgentDetailController(repository, 'agent_fleet')));
    expect(find.text('Edit'), findsNothing);
    expect(find.text('Viewers cannot run tasks. Ask a member or owner.'),
        findsOneWidget);
    expect(find.textContaining('Only its creator'), findsOneWidget);
  });

  testWidgets('a deleted or hidden agent says so', (tester) async {
    await pump(
        tester,
        AgentDetailScreen(
            controller:
                AgentDetailController(FakeAgentsRepository(), 'agent_gone')));
    expect(find.text('Agent not found'), findsOneWidget);
  });

  testWidgets('Arabic', (tester) async {
    await pump(
        tester,
        AgentsScreen(
            key: UniqueKey(),
            controller: AgentsController(FakeAgentsRepository())),
        locale: const Locale('ar'));
    expect(find.text('الوكلاء'), findsOneWidget);
    expect(find.text('العمل 1'), findsOneWidget);
    expect(find.text('إنشاء وكيل'), findsOneWidget);
  });

  testWidgets(
      'Call opens the voice call screen; the call summary is under More',
      (tester) async {
    final called = <String>[];
    await pump(
        tester,
        AgentDetailScreen(
            key: UniqueKey(),
            controller:
                AgentDetailController(FakeAgentsRepository(), 'agent_fleet'),
            callOpener: (context, agent) async => called.add(agent.id)));
    await tester.tap(find.text('Call'));
    await tester.pumpAndSettle();
    expect(called, ['agent_fleet']);
    await tester.tap(find.byTooltip('More'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Call summary').last);
    await tester.pumpAndSettle();
    expect(find.text('Call notes or transcript'), findsOneWidget);
  });

  for (final size in const [
    Size(320, 640),
    Size(390, 844),
    Size(768, 1024),
    Size(1024, 768),
    Size(1440, 900)
  ]) {
    for (final scale in const [1.0, 2.0]) {
      testWidgets(
          'layout ${size.width.toInt()}x${size.height.toInt()} at ${scale}x text',
          (tester) async {
        final repository = FakeAgentsRepository();
        await pump(
            tester,
            AgentsScreen(
                key: UniqueKey(), controller: AgentsController(repository)),
            size: size,
            textScale: scale);
        expect(tester.takeException(), isNull);
        await pump(
            tester,
            AgentDetailScreen(
                controller: AgentDetailController(repository, 'agent_fleet')),
            size: size,
            textScale: scale);
        expect(tester.takeException(), isNull);
        final catalog = AgentCatalog.fromReader(JsonReader(catalogJson));
        await pump(
            tester,
            AgentEditorScreen(
                catalog: catalog, onSave: (d) => repository.create(d.toJson())),
            size: size,
            textScale: scale);
        expect(tester.takeException(), isNull);
      });
    }
  }
}
