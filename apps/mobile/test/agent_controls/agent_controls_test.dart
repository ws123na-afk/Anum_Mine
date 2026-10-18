// Agent controls (docs/agent-controls.md): parsing, the controller against a
// fake API, and the screen: an owner turns the emergency stop on with a reason
// and caps an agent; members see who can; every state from 320 px at 200
// percent text to 1440 px.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/agent_controls/agent_controls_data.dart';
import 'package:anum_mobile/features/agent_controls/agent_controls_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeControls implements AgentControlsRepository {
  FakeControls({this.owner = true, this.orgAdmin = true, this.agents = 2});
  bool owner, orgAdmin, fail = false;
  int agents;
  bool engaged = false;
  String? reason;
  final stops = <(bool, String)>[];
  final savedCaps = <String, double?>{};

  EmergencyStopState _state([int cancelled = 0]) =>
      EmergencyStopState.fromJson(JsonReader({
        'emergency_stop': {
          'engaged': engaged,
          'reason': reason,
          'changed_by': reason == null ? null : 'user_local',
          'changed_by_you': true,
          'changed_at': reason == null ? null : '2026-10-18T10:00:00Z',
        },
        'can_engage': owner,
        'can_release': owner && orgAdmin,
        'cancelled_tasks': cancelled,
      }));

  @override
  Future<EmergencyStopState> status() async {
    if (fail) throw const ApiException(502, 'The API did not answer');
    return _state();
  }

  @override
  Future<EmergencyStopState> setStop(
      {required bool engaged, required String reason}) async {
    stops.add((engaged, reason));
    this.engaged = engaged;
    this.reason = reason;
    return _state(engaged ? 2 : 0);
  }

  Map<String, Object?> _cap(String id, String name, double cost) => {
        'agent_id': id,
        'name': name,
        'status': 'active',
        'sharing': id == 'agent_fleet' ? 'private' : 'workspace',
        'monthly_cost_limit_usd': savedCaps.containsKey(id)
            ? savedCaps[id]
            : (id == 'agent_outreach' ? 2.0 : null),
        'usage': {'estimated_cost_usd': cost, 'calls': 12},
        'exceeded': false,
      };

  @override
  Future<AgentCapsPage> caps(
      {String query = '', int offset = 0, int limit = 20}) async {
    if (!owner) throw const ApiException(403, 'Only owners');
    final all = [
      _cap('agent_outreach', 'Outreach desk', 1.8),
      _cap('agent_fleet', 'Fleet desk', 0.4),
    ].take(agents).where((x) =>
        query.isEmpty ||
        '${x['name']}'.toLowerCase().contains(query.toLowerCase()));
    final items = all.toList();
    return AgentCapsPage.fromJson(JsonReader({
      'period_start': '2026-10-01',
      'resets_on': '2026-11-01',
      'can_manage': true,
      'total': items.length,
      'items': items,
    }));
  }

  @override
  Future<AgentCapItem> setCap(String agentId, double? limitUsd) async {
    savedCaps[agentId] = limitUsd;
    return AgentCapItem.fromJson(JsonReader(_cap(agentId, 'Fleet desk', 0.4)));
  }
}

Future<void> pump(WidgetTester tester, AgentControlsController controller,
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
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: AgentControlsScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('reasons and caps are checked like the API', () {
    expect(parseStopReason('  Runaway   agent ').value, 'Runaway agent');
    expect(parseStopReason('a').error, isNotNull);
    expect(parseStopReason('x' * 301).error, isNotNull);
    expect(parseCapUsd('').ok, isTrue);
    expect(parseCapUsd('').value, isNull);
    expect(parseCapUsd(r'$1,250.456').value, 1250.46);
    expect(parseCapUsd('-1').ok, isFalse);
    expect(parseCapUsd('ten').ok, isFalse);
    final item = AgentCapItem.fromJson(JsonReader({
      'agent_id': 'a',
      'name': 'A',
      'monthly_cost_limit_usd': 2.0,
      'usage': {'estimated_cost_usd': 3.0, 'calls': 1},
      'exceeded': true,
    }));
    expect(item.fraction, 1);
    expect(item.percent, 150);
  });

  testWidgets('an owner turns the stop on with a reason and caps an agent',
      (tester) async {
    final fake = FakeControls();
    await pump(tester, AgentControlsController(fake),
        size: const Size(1024, 1400));
    expect(find.text('Off: running'), findsOneWidget);

    // A reason is required before anything is sent.
    await tester.tap(find.byKey(const Key('stop-toggle')));
    await tester.pumpAndSettle();
    expect(find.text('Say why in at least 3 characters.'), findsOneWidget);
    expect(fake.stops, isEmpty);

    await tester.enterText(find.byKey(const Key('stop-reason')),
        'Outreach agent sends duplicates');
    await tester.tap(find.byKey(const Key('stop-toggle')));
    await tester.pumpAndSettle();
    expect(find.text('Stop all agents and sends?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('confirm-stop-change')));
    await tester.pumpAndSettle();
    expect(fake.stops, [(true, 'Outreach agent sends duplicates')]);
    expect(find.text('On: paused'), findsOneWidget);
    expect(
        find.textContaining('2 running tasks were cancelled'), findsOneWidget);
    expect(
        find.text('Reason: Outreach agent sends duplicates'), findsOneWidget);

    // Caps: near the cap, private agent, a new cap saved.
    expect(find.text('Near the cap'), findsOneWidget);
    expect(find.text('Private'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('cap-input-agent_fleet')));
    await tester.enterText(
        find.byKey(const Key('cap-input-agent_fleet')), 'ten');
    await tester.tap(find.byKey(const Key('cap-save-agent_fleet')));
    await tester.pumpAndSettle();
    expect(
        find.text('Enter an amount in US dollars, 0 or more.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('cap-input-agent_fleet')), '5');
    await tester.tap(find.byKey(const Key('cap-save-agent_fleet')));
    await tester.pumpAndSettle();
    expect(fake.savedCaps, {'agent_fleet': 5.0});
    expect(find.text('Saved the cap for Fleet desk.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('an owner who is not the organization owner cannot resume',
      (tester) async {
    final fake = FakeControls(orgAdmin: false)
      ..engaged = true
      ..reason = 'Runaway agent';
    await pump(tester, AgentControlsController(fake));
    expect(find.byKey(const Key('stop-read-only')), findsOneWidget);
    expect(find.textContaining('Only the organization owner'), findsWidgets);
    expect(find.byKey(const Key('stop-toggle')), findsNothing);
  });

  testWidgets('members, empty, no match and error states', (tester) async {
    final fake = FakeControls(owner: false, orgAdmin: false);
    final controller = AgentControlsController(fake);
    await pump(tester, controller, size: const Size(1024, 2400));
    expect(
        find.textContaining(
            'Only a workspace owner or the organization administrator can turn the stop on'),
        findsOneWidget);
    expect(find.text('Agent caps are for owners'), findsOneWidget);

    fake
      ..owner = true
      ..agents = 0;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('No agents yet'), findsOneWidget);

    fake.agents = 2;
    await controller.search('zzz');
    await tester.pumpAndSettle();
    expect(find.text('No agent matches'), findsOneWidget);
    await tester.ensureVisible(find.text('Clear search'));
    await tester.tap(find.text('Clear search'));
    await tester.pumpAndSettle();
    expect(controller.query, '');
    expect(find.text('Outreach desk'), findsOneWidget);

    fake.fail = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('Agent controls could not be loaded'), findsOneWidget);
    fake.fail = false;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Off: running'), findsOneWidget);
  });

  for (final (size, scale, locale) in [
    (const Size(320, 640), 2.0, const Locale('en')),
    (const Size(390, 844), 1.0, const Locale('ar')),
    (const Size(1440, 900), 1.0, const Locale('en')),
  ]) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      await pump(tester, AgentControlsController(FakeControls()),
          size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull);
    });
  }
}
