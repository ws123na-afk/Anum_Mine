// Agent controls (docs/agent-controls.md): parsing, the controller against a
// fake API, and the screen: an owner turns the emergency stop on with a reason,
// sees what it holds, and caps an agent by cost and the default assistant by
// tokens; members see who can; English and Arabic from 320 px at 200 percent
// text to 1440 px.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/agent_controls/agent_controls_data.dart';
import 'package:anum_mobile/features/agent_controls/agent_controls_screen.dart';
import 'package:anum_mobile/src/localization/agent_controls_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeControls implements AgentControlsRepository {
  FakeControls({this.owner = true, this.orgAdmin = true, this.agents = 3});
  bool owner, orgAdmin, fail = false;
  int agents;
  int heldTotal = 23;
  bool engaged = false;
  String? reason;
  final stops = <(bool, String)>[];
  final savedCaps = <String, (double?, int?)>{};
  final heldOffsets = <int>[];

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

  Map<String, Object?> _cap(String id, String name, double cost) {
    final saved = savedCaps[id];
    return {
      'agent_id': id,
      'name': name,
      'is_default': id == 'assistant',
      'status': 'active',
      'sharing': id == 'agent_fleet' ? 'private' : 'workspace',
      'monthly_cost_limit_usd':
          saved != null ? saved.$1 : (id == 'agent_outreach' ? 2.0 : null),
      'monthly_token_limit': saved?.$2,
      'usage': {
        'estimated_cost_usd': cost,
        'calls': 12,
        'input_tokens': 600,
        'output_tokens': 150
      },
      'total_tokens': 750,
      'exceeded': false,
    };
  }

  @override
  Future<AgentCapsPage> caps(
      {String query = '', int offset = 0, int limit = 20}) async {
    if (!owner) throw const ApiException(403, 'Only owners');
    final items = [
      _cap('assistant', 'ANUM assistant', 0.2),
      _cap('agent_outreach', 'Outreach desk', 1.8),
      _cap('agent_fleet', 'Fleet desk', 0.4),
    ]
        .take(agents)
        .where((x) =>
            query.isEmpty ||
            '${x['name']}'.toLowerCase().contains(query.toLowerCase()))
        .toList();
    return AgentCapsPage.fromJson(JsonReader({
      'period_start': '2026-10-01',
      'resets_on': '2026-11-01',
      'can_manage': true,
      'total': items.length,
      'items': items,
    }));
  }

  @override
  Future<AgentCapItem> setCap(String agentId, double? limitUsd,
      {int? tokenLimit}) async {
    savedCaps[agentId] = (limitUsd, tokenLimit);
    final name = agentId == 'assistant' ? 'ANUM assistant' : 'Fleet desk';
    return AgentCapItem.fromJson(JsonReader(_cap(agentId, name, 0.4)));
  }

  @override
  Future<HeldPage> held({int offset = 0, int limit = 20}) async {
    heldOffsets.add(offset);
    final items = [
      for (var i = offset; i < heldTotal && i < offset + limit; i++)
        {
          'kind': i == 0 ? 'mail' : 'approval',
          'id': 'held_$i',
          'title': i == 0 ? 'October invoices' : 'external.action $i',
          'detail': i == 0 ? '3 recipients' : 'api.example.com',
          'at': '2026-10-18T10:00:00Z',
        }
    ];
    return HeldPage.fromJson(
        JsonReader({'engaged': engaged, 'total': heldTotal, 'items': items}));
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
    expect(parseStopReason('a').error, 'ac.reasonShort');
    expect(parseStopReason('x' * 301).error, 'ac.reasonLong');
    expect(parseCapUsd('').value, isNull);
    expect(parseCapUsd(r'$1,250.456').value, 1250.46);
    expect(parseCapUsd('-1').ok, isFalse);
    expect(parseTokenCap('1,000').value, 1000);
    expect(parseTokenCap('1.5').ok, isFalse);
    expect(parseTokenCap('').ok, isTrue);
    final item = AgentCapItem.fromJson(JsonReader({
      'agent_id': 'a',
      'name': 'A',
      'monthly_cost_limit_usd': 2.0,
      'monthly_token_limit': 100,
      'usage': {'estimated_cost_usd': 3.0, 'calls': 1},
      'total_tokens': 50,
      'exceeded': true,
    }));
    expect(item.fraction, 1);
    expect(item.percent, 150);
    expect(item.tokenPercent, 50);
    expect(item.worstPercent, 150);
  });

  test('English and Arabic agent controls strings match', () {
    final names = RegExp(r'\{(\w+)\}');
    Set<String> of(String text) =>
        names.allMatches(text).map((match) => match.group(1)!).toSet();
    for (final key in agentControlsEn.keys) {
      final base =
          key.replaceAll(RegExp(r'\.(zero|one|two|few|many|other)$'), '');
      final arabic = agentControlsAr[key] ?? agentControlsAr['$base.other'];
      expect(arabic, isNotNull, reason: key);
      if (agentControlsAr.containsKey(key) &&
          !key.contains('.engaged.') &&
          !key.contains('.calls.')) {
        expect(of(agentControlsAr[key]!), of(agentControlsEn[key]!),
            reason: key);
      }
    }
    for (final value in agentControlsAr.values) {
      expect(RegExp('[؀-ۿ]').hasMatch(value), isTrue, reason: value);
    }
  });

  testWidgets('an owner stops agents, sees what is held and caps agents',
      (tester) async {
    final fake = FakeControls();
    await pump(tester, AgentControlsController(fake),
        size: const Size(1024, 3200));
    expect(find.text('Off: running'), findsOneWidget);
    expect(find.byKey(const Key('held')), findsNothing);

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
        find.text('The emergency stop is on. 2 running tasks were cancelled.'),
        findsOneWidget);

    // What the stop holds, paged.
    expect(find.text('Held while the stop is on'), findsOneWidget);
    expect(find.text('October invoices'), findsOneWidget);
    expect(find.text('Scheduled email'), findsOneWidget);
    expect(find.text('1 to 20 of 23'), findsOneWidget);
    await tester.tap(find.descendant(
        of: find.byKey(const Key('held')), matching: find.text('Next')));
    await tester.pumpAndSettle();
    expect(fake.heldOffsets.last, 20);
    expect(find.text('21 to 23 of 23'), findsOneWidget);

    // Caps: the default assistant by tokens, an agent by cost.
    expect(find.text('Default assistant'), findsOneWidget);
    expect(find.text('Near the cap'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('token-input-assistant')), '1.5');
    await tester.tap(find.byKey(const Key('cap-save-assistant')));
    await tester.pumpAndSettle();
    expect(find.text('Enter a whole number of tokens, 0 or more.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('token-input-assistant')), '200000');
    await tester.tap(find.byKey(const Key('cap-save-assistant')));
    await tester.pumpAndSettle();
    expect(fake.savedCaps['assistant'], (null, 200000));
    expect(find.text('Saved the cap for ANUM assistant.'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('cap-input-agent_fleet')), '5');
    await tester.tap(find.byKey(const Key('cap-save-agent_fleet')));
    await tester.pumpAndSettle();
    expect(fake.savedCaps['agent_fleet'], (5.0, null));
    expect(tester.takeException(), isNull);
  });

  testWidgets('an owner who is not the organization owner cannot resume',
      (tester) async {
    final fake = FakeControls(orgAdmin: false)
      ..engaged = true
      ..reason = 'Runaway agent';
    await pump(tester, AgentControlsController(fake),
        size: const Size(1024, 2400));
    expect(find.byKey(const Key('stop-read-only')), findsOneWidget);
    expect(find.byKey(const Key('stop-toggle')), findsNothing);
    expect(find.byKey(const Key('held')), findsOneWidget);
  });

  testWidgets('members, empty, no match, nothing held and error states',
      (tester) async {
    final fake = FakeControls(owner: false, orgAdmin: false);
    final controller = AgentControlsController(fake);
    await pump(tester, controller, size: const Size(1024, 2400));
    expect(
        find.text(
            'Only a workspace owner or the organization administrator can turn the stop on.'),
        findsOneWidget);
    expect(find.text('Agent caps are for owners'), findsOneWidget);

    fake
      ..owner = true
      ..agents = 0
      ..engaged = true
      ..reason = 'Runaway'
      ..heldTotal = 0;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('No agents yet'), findsOneWidget);
    expect(find.text('Nothing is held'), findsOneWidget);

    fake.agents = 3;
    await controller.search('zzz');
    await tester.pumpAndSettle();
    expect(find.text('No agent matches'), findsOneWidget);
    await tester.tap(find.text('Clear search'));
    await tester.pumpAndSettle();
    expect(find.text('Outreach desk'), findsOneWidget);

    fake.fail = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('Agent controls could not be loaded'), findsOneWidget);
    fake.fail = false;
    await tester.tap(find.text('Retry').first);
    await tester.pumpAndSettle();
    expect(find.text('On: paused'), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left', (tester) async {
    final fake = FakeControls()
      ..engaged = true
      ..reason = 'Runaway';
    await pump(tester, AgentControlsController(fake),
        size: const Size(1024, 2400), locale: const Locale('ar'));
    expect(find.text('الإيقاف الطارئ'), findsOneWidget);
    expect(find.text('محتجز أثناء الإيقاف'), findsOneWidget);
    expect(find.text('المساعد الافتراضي'), findsOneWidget);
    expect(Directionality.of(tester.element(find.byType(AgentControlsScreen))),
        TextDirection.rtl);
  });

  for (final (size, scale, locale) in [
    (const Size(320, 640), 2.0, const Locale('en')),
    (const Size(390, 844), 1.0, const Locale('ar')),
    (const Size(1440, 900), 1.0, const Locale('en')),
  ]) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      final fake = FakeControls()
        ..engaged = true
        ..reason = 'Runaway';
      await pump(tester, AgentControlsController(fake),
          size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull);
    });
  }
}
