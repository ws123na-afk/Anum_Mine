// An agent's Skills tab (docs/agents.md#skills): the pure helpers ported from
// apps/web/src/lib/agents.ts, the strings in both languages, and the panel in
// every state (loadout, allow a tool, remove, add from the picker, stale and
// forbidden edits, read-only, search, Arabic, 320 px at 200 percent text).
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/agents/agent_skills_models.dart';
import 'package:anum_mobile/features/agents/agent_skills_panel.dart';
import 'package:anum_mobile/features/agents/agents_controller.dart';
import 'package:anum_mobile/features/agents/agents_models.dart';
import 'package:anum_mobile/features/agents/agents_screen.dart';
import 'package:anum_mobile/src/localization/agent_skills_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/depth.dart' show AnumTone;
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'agents_test.dart' show FakeAgentsRepository, agentJson, catalogJson;

JsonMap toolJson(String name,
        {bool registered = true, bool allowed = false, bool? granted}) =>
    {
      'name': name,
      'registered': registered,
      'allowed': allowed,
      'granted': granted,
    };

JsonMap skillJson(String id, String name,
        {String source = 'installed',
        String state = 'ready',
        String? reason,
        String? version = '1.2.0',
        String? latest,
        String? risk = 'low',
        String activation = 'always',
        List<String> triggers = const [],
        List<JsonMap> tools = const [],
        String description = ''}) =>
    {
      'id': id,
      'name': name,
      'description': description,
      'source': source,
      'version': version,
      'latest_version': latest,
      'risk_level': risk,
      'state': state,
      'reason': reason,
      'activation': activation,
      'triggers': triggers,
      'loads_instructions': source == 'installed',
      'upgrade_available': latest != null,
      'tools': tools,
    };

AgentSkillState state(JsonMap json) =>
    AgentSkillState.fromReader(JsonReader(json));

AgentCatalog catalogWith(List<JsonMap> skills, [List<JsonMap>? tools]) =>
    AgentCatalog.fromReader(JsonReader({
      ...catalogJson,
      'skills': skills,
      if (tools != null) 'tools': tools,
    }));

JsonMap catalogSkill(String id, String name,
        {String source = 'builtin',
        List<String> tools = const [],
        String description = ''}) =>
    {
      'id': id,
      'name': name,
      'description': description,
      'source': source,
      'required_tools': tools,
    };

AgentProfile agentWith(
        {List<String> skills = const [],
        List<String> tools = const [],
        int version = 3}) =>
    AgentProfile.fromReader(JsonReader({
      ...agentJson('agent_fleet', 'Fleet desk'),
      'allowed_skills': skills,
      'allowed_tools': tools,
      'version': version,
    }));

class _Host extends StatefulWidget {
  const _Host(this.repository, this.agent, {this.catalog});
  final FakeAgentsRepository repository;
  final AgentProfile agent;
  final AgentCatalog? catalog;
  @override
  State<_Host> createState() => _HostState();
}

class _HostState extends State<_Host> {
  late AgentProfile agent = widget.agent;
  @override
  Widget build(BuildContext context) => Scaffold(
        body: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: AgentSkillsPanel(
            agent: agent,
            repository: widget.repository,
            catalog: widget.catalog,
            onAgentChanged: (next) => setState(() => agent = next),
            onStale: () async {
              final fresh = await widget.repository.get(agent.id);
              if (mounted) setState(() => agent = fresh);
            },
          ),
        ),
      );
}

Future<void> pumpPanel(WidgetTester tester, FakeAgentsRepository repository,
    {AgentProfile? agent,
    AgentCatalog? catalog,
    Size size = const Size(390, 2400),
    double textScale = 1,
    Locale locale = const Locale('en'),
    bool settle = true}) async {
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
    home: _Host(
        repository,
        agent ??
            AgentProfile.fromReader(JsonReader({...repository.agents.first})),
        catalog: catalog),
  ));
  if (settle) {
    await tester.pumpAndSettle();
  } else {
    await tester.pump();
  }
}

/// A fleet agent with a ready built-in skill and an installed skill that
/// needs `web_search` allowed.
FakeAgentsRepository fleetRepository({
  List<String> skills = const ['anum.web-search', 'acme.tyres'],
  List<String> tools = const [],
  bool canEdit = true,
}) {
  final repository = FakeAgentsRepository(agents: [
    {
      ...agentJson('agent_fleet', 'Fleet desk', canEdit: canEdit),
      'allowed_skills': skills,
      'allowed_tools': tools,
      'version': 3,
    },
  ]);
  repository.skillItems = {
    'anum.web-search': skillJson('anum.web-search', 'Web search',
        source: 'builtin',
        version: null,
        activation: 'on_request',
        triggers: ['search', 'look up'],
        description: 'Looks things up.',
        tools: [toolJson('anum.respond', allowed: true)]),
    'acme.tyres': skillJson('acme.tyres', 'Tyre planner',
        state: 'needs_tools',
        latest: '1.3.0',
        risk: 'medium',
        reason: 'The API sentence.',
        tools: [toolJson('web_search', granted: true)]),
  };
  return repository;
}

void main() {
  group('helpers', () {
    final ready = state(skillJson('b.ready', 'beta', tools: []));
    final alpha = state(skillJson('a.needs', 'Alpha', state: 'needs_tools'));
    final zed = state(skillJson('z.ready', 'Zed'));

    test('sortLoadout puts ready skills first, then by name', () {
      expect(sortLoadout([alpha, zed, ready]).map((s) => s.id),
          ['b.ready', 'z.ready', 'a.needs']);
    });

    test('skillStateTone', () {
      expect(skillStateTone('ready'), AnumTone.ok);
      expect(skillStateTone('needs_tools'), AnumTone.warn);
      expect(skillStateTone('disabled'), AnumTone.warn);
      expect(skillStateTone('blocked'), AnumTone.stop);
      expect(skillStateTone('unavailable'), AnumTone.stop);
      expect(skillStateTone('not_installed'), AnumTone.neutral);
    });

    test('allowableMissingTools: registered, not allowed, not withheld, usable',
        () {
      final catalog = catalogWith([], [
        {
          'name': 'web_search',
          'description': '',
          'category': 'read',
          'always_on': false,
          'available_to_you': true
        },
        {
          'name': 'owner_only',
          'description': '',
          'category': 'write',
          'always_on': false,
          'available_to_you': false
        },
        {
          'name': 'anum.respond',
          'description': '',
          'category': 'read',
          'always_on': true,
          'available_to_you': true
        },
      ]);
      final item = state(skillJson('x', 'X', state: 'needs_tools', tools: [
        toolJson('web_search'),
        toolJson('owner_only'),
        toolJson('anum.respond'),
        toolJson('withheld', granted: false),
        toolJson('gone', registered: false),
        toolJson('already', allowed: true),
      ]));
      expect(allowableMissingTools(item, catalog), ['web_search']);
      expect(allowableMissingTools(item, null), isEmpty);
    });

    test('attach adds a built-in skill with its usable tools, sorted', () {
      final catalog = catalogWith([
        catalogSkill('anum.web-search', 'Web search',
            tools: ['web_search', 'anum.respond', 'fleet.reset']),
        catalogSkill('acme.tyres', 'Tyres',
            source: 'installed', tools: ['web_search']),
      ]);
      final agent = agentWith(skills: ['z.one'], tools: ['b.tool'], version: 7);
      expect(attachSkillChanges(agent, catalog.skills.first, catalog), {
        'allowed_skills': ['anum.web-search', 'z.one'],
        'allowed_tools': ['b.tool', 'web_search'],
        'version': 7,
      });
      // An installed skill never adds tools.
      expect(attachSkillChanges(agent, catalog.skills.last, catalog), {
        'allowed_skills': ['acme.tyres', 'z.one'],
        'allowed_tools': ['b.tool'],
        'version': 7,
      });
    });

    test('detach keeps the tools; allow merges and sorts', () {
      final agent = agentWith(skills: ['a', 'b'], tools: ['t2'], version: 4);
      expect(detachSkillChanges(agent, 'a'), {
        'allowed_skills': ['b'],
        'version': 4,
      });
      expect(allowToolsChanges(agent, ['t3', 't1', 't2']), {
        'allowed_tools': ['t1', 't2', 't3'],
        'version': 4,
      });
    });

    test(
        'addableSkills hides loaded ones, searches every word, installed first',
        () {
      final catalog = catalogWith([
        catalogSkill('anum.web-search', 'Web search',
            tools: ['web_search'], description: 'Looks things up.'),
        catalogSkill('acme.tyres', 'Tyre planner',
            source: 'installed', tools: ['tyres_due']),
        catalogSkill('anum.email', 'Email', tools: ['search_email']),
      ]);
      expect(addableSkills(catalog, ['anum.email'], '').map((s) => s.id),
          ['acme.tyres', 'anum.web-search']);
      expect(addableSkills(catalog, [], 'SEARCH').map((s) => s.id),
          ['anum.email', 'anum.web-search']);
      expect(addableSkills(catalog, [], 'search looks').map((s) => s.id),
          ['anum.web-search']);
      expect(addableSkills(catalog, [], 'tyres_due').map((s) => s.id),
          ['acme.tyres']);
      expect(addableSkills(null, [], ''), isEmpty);
    });

    test('filterLoadout searches name, id, description and tool names', () {
      final items = [
        state(skillJson('acme.tyres', 'Tyre planner',
            description: 'Plans rotations.', tools: [toolJson('tyres_due')])),
        state(skillJson('anum.email', 'Email')),
      ];
      expect(filterLoadout(items, '').length, 2);
      expect(filterLoadout(items, 'tyres_due').single.id, 'acme.tyres');
      expect(filterLoadout(items, 'rotations planner').single.id, 'acme.tyres');
      expect(filterLoadout(items, 'ANUM.email').single.id, 'anum.email');
      expect(filterLoadout(items, 'nothing'), isEmpty);
    });

    test('skillReasonKey names the tools and falls back to the API sentence',
        () {
      expect(skillReasonKey(state(skillJson('x', 'X', state: 'disabled')))!.key,
          'agentSkills.reason.disabled');
      final ungranted = state(skillJson('x', 'X', state: 'needs_tools', tools: [
        toolJson('a', allowed: true),
        toolJson('b', granted: false)
      ]));
      expect(skillReasonKey(ungranted)!.key, 'agentSkills.reason.notGranted');
      expect(skillReasonKey(ungranted)!.tools, 'b');
      final notAllowed = state(skillJson('x', 'X',
          state: 'needs_tools', tools: [toolJson('a'), toolJson('c')]));
      expect(skillReasonKey(notAllowed)!.key, 'agentSkills.reason.notAllowed');
      expect(skillReasonKey(notAllowed)!.tools, 'a, c');
      final missing = state(skillJson('x', 'X',
          state: 'unavailable', tools: [toolJson('m', registered: false)]));
      expect(skillReasonKey(missing)!.key, 'agentSkills.reason.missingTool');
      expect(
          skillReasonKey(state(skillJson('x', 'X',
                  state: 'unavailable', source: 'unknown')))!
              .key,
          'agentSkills.reason.unknown');
      expect(skillReasonKey(state(skillJson('x', 'X', state: 'unavailable'))),
          isNull);
    });

    test('the loadout reads the API shape', () {
      final loadout = AgentSkillLoadout.fromReader(JsonReader({
        'agent_id': 'a',
        'agent_version': 5,
        'can_edit': false,
        'items': [
          skillJson('x', 'X', tools: [toolJson('t', allowed: true)])
        ],
        'ready': 1,
        'attention': 0,
      }));
      expect(loadout.canEdit, isFalse);
      expect(loadout.items.single.tools.single.ok, isTrue);
      expect(loadout.items.single.version, '1.2.0');
    });
  });

  test('both languages have the same keys and placeholders, no plural forms',
      () {
    expect(agentSkillsAr.keys.toSet(), agentSkillsEn.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in agentSkillsEn.keys) {
      Set<String> names(String text) =>
          placeholder.allMatches(text).map((m) => m.group(1)!).toSet();
      expect(names(agentSkillsAr[key]!), names(agentSkillsEn[key]!),
          reason: key);
      expect(key.endsWith('.one') || key.endsWith('.other'), isFalse);
      for (final text in [agentSkillsEn[key]!, agentSkillsAr[key]!]) {
        expect(text.runes.any((r) => r == 0x2014 || r == 0x2013), isFalse,
            reason: key);
      }
    }
  });

  group('panel', () {
    testWidgets(
        'shows the loadout: state, source, version, risk, how it loads, tools and reason',
        (tester) async {
      final repository = fleetRepository();
      await pumpPanel(tester, repository);
      expect(find.text('Skills this agent loads'), findsOneWidget);
      expect(find.text('Ready: 1. Needs attention: 1.'), findsOneWidget);
      expect(find.text('Web search'), findsOneWidget);
      expect(find.text('Tyre planner'), findsOneWidget);
      expect(find.text('Ready'), findsOneWidget);
      expect(find.text('Needs tools'), findsOneWidget);
      expect(find.textContaining('Built in'), findsOneWidget);
      expect(find.textContaining('Installed, Version 1.2.0, Risk medium'),
          findsOneWidget);
      expect(find.text('Joins a task when you ask about: search, look up'),
          findsOneWidget);
      expect(
          find.text(
              'Loads its instructions into every task and chat with this agent.'),
          findsOneWidget);
      // The API's own sentence is used when no case names the tools; here the
      // missing tool is named in the interface language.
      expect(find.text('Allow web_search on this agent so the skill can run.'),
          findsOneWidget);
      expect(
          find.text(
              'Version 1.3.0 is available. A workspace owner upgrades it in Skills.'),
          findsOneWidget);
      expect(find.text('Allow web_search'), findsOneWidget);
      // Ready skills sort before the ones that need attention.
      expect(tester.getTopLeft(find.text('Web search')).dy,
          lessThan(tester.getTopLeft(find.text('Tyre planner')).dy));
    });

    testWidgets('Allow a missing tool sends the tool and the version read',
        (tester) async {
      final repository = fleetRepository();
      await pumpPanel(tester, repository);
      await tester.tap(find.text('Allow web_search'));
      await tester.pumpAndSettle();
      expect(repository.updates.single, {
        'allowed_tools': ['web_search'],
        'version': 3,
      });
      expect(find.text('Allowed web_search on this agent.'), findsOneWidget);
      expect(find.text('Allow web_search'), findsNothing);
      expect(find.text('Needs tools'), findsNothing);
      expect(find.text('Ready: 2. Needs attention: 0.'), findsOneWidget);
    });

    testWidgets('Remove detaches the skill and keeps the tools',
        (tester) async {
      final repository = fleetRepository(tools: ['web_search']);
      await pumpPanel(tester, repository);
      expect(find.bySemanticsLabel('Remove Tyre planner from this agent'),
          findsOneWidget);
      await tester.tap(find.text('Remove').first);
      await tester.pumpAndSettle();
      expect(repository.updates.single, {
        'allowed_skills': ['anum.web-search'],
        'version': 3,
      });
      expect(find.text('Tyre planner'), findsNothing);
      expect(
          find.text(
              'Tyre planner removed. Tasks that already started keep it.'),
          findsOneWidget);
      expect(repository.agents.single['allowed_tools'], ['web_search']);
    });

    testWidgets('the picker adds a built-in skill with the tool it needs',
        (tester) async {
      final repository = fleetRepository(skills: const []);
      await pumpPanel(tester, repository);
      expect(find.text('No skills loaded'), findsOneWidget);
      expect(
          find.text(
              'Without skills it plans with the built-in runtime only. Add a skill to give it reviewed instructions and the tools they need.'),
          findsOneWidget);
      await tester.tap(find.text('Add a skill').first);
      await tester.pumpAndSettle();
      expect(find.text('Also allows the tool: web_search'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextField, 'Search available skills'), 'nope');
      await tester.pumpAndSettle();
      expect(find.text('No available skill matches “nope”.'), findsOneWidget);
      await tester.enterText(
          find.widgetWithText(TextField, 'Search available skills'), 'web');
      await tester.pumpAndSettle();
      expect(find.bySemanticsLabel('Add Web search'), findsOneWidget);
      await tester.tap(find.text('Add'));
      await tester.pumpAndSettle();
      expect(repository.updates.single, {
        'allowed_skills': ['anum.web-search'],
        'allowed_tools': ['web_search'],
        'version': 3,
      });
      expect(find.text('Web search added. The next task and chat load it.'),
          findsOneWidget);
      // Loaded now, so it is no longer offered; the picker says so.
      expect(find.text('This agent already loads every available skill.'),
          findsOneWidget);
      await tester.tap(find.text('Done'));
      await tester.pumpAndSettle();
      expect(find.text('Also allows the tool: web_search'), findsNothing);
      expect(find.text('Ready: 1. Needs attention: 0.'), findsOneWidget);
    });

    testWidgets('a stale edit says so, reloads and offers the change again',
        (tester) async {
      final repository = fleetRepository();
      repository.updateFailure = const ApiException(409,
          'Someone changed this agent since you opened it. Reload and apply your change again.');
      await pumpPanel(tester, repository);
      final calls = repository.skillsCalls;
      await tester.tap(find.text('Allow web_search'));
      await tester.pumpAndSettle();
      expect(
          find.textContaining(
              'Someone changed this agent since you opened it.'),
          findsOneWidget);
      expect(find.textContaining('The page was reloaded'), findsOneWidget);
      expect(repository.skillsCalls, greaterThan(calls));
      expect(find.text('Allow web_search'), findsOneWidget);
    });

    testWidgets('a forbidden edit shows the API sentence', (tester) async {
      final repository = fleetRepository();
      repository.updateFailure = const ApiException(
          403, 'Only the workspace owner can change a shared agent.');
      await pumpPanel(tester, repository);
      await tester.tap(find.text('Allow web_search'));
      await tester.pumpAndSettle();
      expect(find.text('Only the workspace owner can change a shared agent.'),
          findsOneWidget);
    });

    testWidgets('another failure names what failed', (tester) async {
      final repository = fleetRepository();
      repository.updateFailure =
          const ApiException(422, 'Unknown tool: web_search.');
      await pumpPanel(tester, repository);
      await tester.tap(find.text('Allow web_search'));
      await tester.pumpAndSettle();
      expect(find.text('The skills did not change: Unknown tool: web_search.'),
          findsOneWidget);
    });

    testWidgets('someone who cannot edit reads but cannot change',
        (tester) async {
      final repository = fleetRepository(canEdit: false);
      await pumpPanel(tester, repository,
          agent: AgentProfile.fromReader(
              JsonReader({...repository.agents.single})));
      expect(
          find.text(
              'Only this agent’s creator, or a workspace owner for a shared agent, can change its skills.'),
          findsOneWidget);
      expect(find.text('Tyre planner'), findsOneWidget);
      expect(find.text('Add a skill'), findsNothing);
      expect(find.text('Remove'), findsNothing);
      expect(find.text('Allow web_search'), findsNothing);
    });

    testWidgets('read-only empty state points at who can add', (tester) async {
      final repository = fleetRepository(skills: const [], canEdit: false);
      await pumpPanel(tester, repository,
          agent: AgentProfile.fromReader(
              JsonReader({...repository.agents.single})));
      expect(
          find.text(
              'Without skills it plans with the built-in runtime only. Its creator, or a workspace owner, can add skills.'),
          findsOneWidget);
      expect(find.text('Add a skill'), findsNothing);
    });

    testWidgets('loading, then error with a retry that works', (tester) async {
      final repository = fleetRepository();
      repository.skillsFailure =
          const ApiException(403, 'You need the task:read permission.');
      await pumpPanel(tester, repository);
      expect(
          find.text(
              'Could not load this agent’s skills: You need the task:read permission. Check that the API is reachable, then try again.'),
          findsOneWidget);
      expect(find.text('Tyre planner'), findsNothing);
      repository.skillsFailure = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Tyre planner'), findsOneWidget);
      expect(find.textContaining('Could not load'), findsNothing);
    });

    testWidgets('shows a loading line while the loadout is read',
        (tester) async {
      final repository = fleetRepository();
      final gate = Completer<void>();
      final gated = _Gated(repository, gate.future);
      await pumpPanel(tester, gated, settle: false);
      expect(find.text('Loading the skills this agent loads'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      gate.complete();
      await tester.pumpAndSettle();
      expect(find.text('Tyre planner'), findsOneWidget);
    });

    testWidgets('every state has a label and its fix text', (tester) async {
      final repository = fleetRepository(skills: [
        'a.ready',
        'b.disabled',
        'c.missing',
        'd.blocked',
        'e.gone',
        'f.ghost',
      ]);
      repository.skillItems = {
        'a.ready': skillJson('a.ready', 'A ready'),
        'b.disabled': skillJson('b.disabled', 'B off', state: 'disabled'),
        'c.missing': skillJson('c.missing', 'C new', state: 'not_installed'),
        'd.blocked': skillJson('d.blocked', 'D bad', state: 'blocked'),
        'e.gone': skillJson('e.gone', 'E gone',
            state: 'unavailable',
            tools: [toolJson('tool_x', registered: false)]),
        'f.ghost': skillJson('f.ghost', 'F ghost',
            state: 'unavailable', source: 'unknown', version: null, risk: null),
      };
      await pumpPanel(tester, repository, size: const Size(390, 3000));
      for (final label in [
        'Ready',
        'Turned off',
        'Not installed',
        'Blocked',
        'Unavailable',
      ]) {
        expect(find.text(label), findsWidgets, reason: label);
      }
      expect(find.textContaining('turned this skill off'), findsOneWidget);
      expect(find.textContaining('It is not installed in this workspace'),
          findsOneWidget);
      expect(find.textContaining('marked blocked'), findsOneWidget);
      expect(find.textContaining('does not have tool_x'), findsOneWidget);
      expect(
          find.textContaining('No skill with this id exists'), findsOneWidget);
      expect(find.text('Unknown skill'), findsOneWidget);
    });

    testWidgets('a long loadout gets a search', (tester) async {
      final ids = [for (var i = 1; i <= 7; i++) 'acme.skill$i'];
      final repository = fleetRepository(skills: ids);
      repository.skillItems = {
        for (final id in ids)
          id: skillJson(id, 'Skill ${id.substring(id.length - 1)}'),
      };
      await pumpPanel(tester, repository, size: const Size(390, 3000));
      final field = find.widgetWithText(TextField, 'Search loaded skills');
      expect(field, findsOneWidget);
      await tester.enterText(field, 'skill3');
      await tester.pumpAndSettle();
      expect(find.text('Skill 3'), findsOneWidget);
      expect(find.text('Skill 4'), findsNothing);
      await tester.enterText(field, 'zzz');
      await tester.pumpAndSettle();
      expect(find.text('No loaded skill matches “zzz”.'), findsOneWidget);
    });

    testWidgets('a short loadout has no search', (tester) async {
      await pumpPanel(tester, fleetRepository());
      expect(
          find.widgetWithText(TextField, 'Search loaded skills'), findsNothing);
    });

    testWidgets('a catalog that fails to load can be retried in the picker',
        (tester) async {
      final repository = fleetRepository(skills: const []);
      final failing = _CatalogFailing(repository);
      await pumpPanel(tester, failing);
      await tester.tap(find.text('Add a skill').first);
      await tester.pumpAndSettle();
      expect(find.text('Could not load the available skills: Catalog is down.'),
          findsOneWidget);
      failing.fail = false;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Web search'), findsOneWidget);
    });

    testWidgets('an empty catalog tells an owner installs skills',
        (tester) async {
      final repository = fleetRepository(skills: const []);
      await pumpPanel(tester, repository, catalog: catalogWith([]));
      await tester.tap(find.text('Add a skill').first);
      await tester.pumpAndSettle();
      expect(
          find.text(
              'No skills are available in this workspace yet. A workspace owner installs them in Skills.'),
          findsOneWidget);
    });

    testWidgets('Arabic reads right to left', (tester) async {
      final repository = fleetRepository();
      await pumpPanel(tester, repository, locale: const Locale('ar'));
      expect(find.text('المهارات التي يحمّلها هذا الوكيل'), findsOneWidget);
      expect(find.text('جاهزة'), findsOneWidget);
      expect(find.text('تحتاج إلى أدوات'), findsOneWidget);
      expect(find.text('إزالة'), findsWidgets);
      expect(Directionality.of(tester.element(find.text('Tyre planner'))),
          TextDirection.rtl);
      await tester.tap(find.text('السماح بـ web_search'));
      await tester.pumpAndSettle();
      expect(find.text('سُمح بالأداة web_search لهذا الوكيل.'), findsOneWidget);
    });
  });

  group('layouts', () {
    for (final size in const [
      Size(320, 640),
      Size(390, 844),
      Size(768, 1024),
      Size(1440, 900)
    ]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        testWidgets(
            '${locale.languageCode} ${size.width.toInt()}x${size.height.toInt()} at 200% text: loadout and picker',
            (tester) async {
          final repository = fleetRepository(tools: []);
          await pumpPanel(tester, repository,
              size: size, textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
          expect(find.byType(AgentSkillsPanel), findsOneWidget);
          await tester.ensureVisible(find.byIcon(Icons.add).first);
          await tester.tap(find.byIcon(Icons.add).first);
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          final box = tester.getRect(find.byType(AgentSkillsPanel));
          expect(box.right, lessThanOrEqualTo(size.width + 0.5));
          expect(box.left, greaterThanOrEqualTo(-0.5));
        });
      }
    }
  });

  testWidgets('the agent page Skills tab shows the loadout', (tester) async {
    final repository = fleetRepository();
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      home: AgentDetailScreen(
          controller: AgentDetailController(repository, 'agent_fleet')),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(Tab, 'Skills'));
    await tester.pumpAndSettle();
    expect(find.text('Tyre planner'), findsOneWidget);
    await tester.tap(find.text('Allow web_search'));
    await tester.pumpAndSettle();
    // The page's own agent took the saved version.
    await tester.tap(find.text('Remove').last);
    await tester.pumpAndSettle();
    expect(repository.updates.map((u) => u['version']), [3, 4]);
  });
}

class _Gated extends FakeAgentsRepository {
  _Gated(FakeAgentsRepository source, this.gate)
      : super(agents: source.agents) {
    skillItems = source.skillItems;
  }
  final Future<void> gate;
  @override
  Future<AgentSkillLoadout> skills(String id) async {
    await gate;
    return super.skills(id);
  }
}

class _CatalogFailing extends FakeAgentsRepository {
  _CatalogFailing(FakeAgentsRepository source) : super(agents: source.agents) {
    skillItems = source.skillItems;
  }
  bool fail = true;
  @override
  Future<AgentCatalog> catalog() async {
    if (fail) throw const ApiException(503, 'Catalog is down.');
    return super.catalog();
  }
}
