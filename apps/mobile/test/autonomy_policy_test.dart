// Autonomy policy on the phone (docs/autonomy.md): quiet hours, daily caps,
// blocked lists, rules and the organization guardrails, against a fake
// repository that answers like the API, at the gate sizes and 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/admin/admin_controller.dart'
    show AdminPhase;
import 'package:anum_mobile/features/autonomy/autonomy_controller.dart';
import 'package:anum_mobile/features/autonomy/autonomy_models.dart';
import 'package:anum_mobile/features/autonomy/autonomy_policy_draft.dart';
import 'package:anum_mobile/features/autonomy/autonomy_policy_screens.dart';
import 'package:anum_mobile/features/autonomy/autonomy_repository.dart';
import 'package:anum_mobile/features/autonomy/autonomy_screens.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/autonomy_policy_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const _above = "Above the organization's guardrails: the daily cap for email "
    'can be at most 50. Lower these values, or ask an organization '
    'administrator to change the guardrails.';

Map<String, Object?> _rule(String id, String tool,
        {String? template, String level = 'ask_always'}) =>
    {
      'id': id,
      'tool': tool,
      'template': template,
      'category': 'email',
      'level': level,
      'source': 'manual',
    };

class _Fake implements AutonomyRepository {
  _Fake({
    this.owner = true,
    this.canTighten = true,
    this.admin = false,
    this.rulesForbidden = false,
    this.guardrailsForbidden = false,
    this.failRulesOnce = false,
    int ruleCount = 2,
  }) {
    rows = [
      for (var i = 0; i < ruleCount; i++)
        _rule('r$i', 'email.tool$i', template: i.isOdd ? 'tpl$i' : null),
    ];
  }
  final bool owner, canTighten, admin, rulesForbidden, guardrailsForbidden;
  bool failRulesOnce;
  final calls = <String>[];
  Map<String, Object?>? saved;
  Map<String, Object?>? savedGuardrails;
  String? refuseSave, refuseRule;
  late List<Map<String, Object?>> rows;
  Map<String, Object?> guardrailJson = {
    'spending_limit': 1000,
    'max_auto_recipients': 8,
    'category_ceilings': {'commerce': 'ask_always'},
    'daily_caps': {'email': 50},
    'quiet_hours': {'start': '23:00', 'end': '06:00', 'timezone': 'UTC'},
    'blocked_recipients': ['blocked@example.com'],
    'blocked_domains': <String>[],
  };
  Map<String, Object?> settings = {
    'category_levels': {'email': 'act_and_notify'},
    'amount_threshold': 0,
    'recipients_threshold': 5,
    'price_change_percent_threshold': 2.5,
    'daily_caps': {'email': 20},
    'quiet_hours': null,
    'blocked_recipients': ['old@example.com'],
    'blocked_domains': ['competitor.example'],
    'approved_templates': ['order_status'],
    'proposal_after': 3,
    'future_field': 'kept',
    'updated_by': 'owner_a',
    'updated_at': '2026-10-11T10:00:00Z',
  };

  Map<String, Object?> get _overview => {
        'settings': settings,
        'guardrails': guardrailJson,
        'categories': [
          for (final c in autonomyCategories)
            {
              'category': c,
              'level': 'act_silently',
              'ceiling': null,
              'effective': 'act_silently',
            },
        ],
        'pending_proposals': 0,
        'can_manage_workspace': owner,
        'can_manage_guardrails': admin,
        'can_tighten': canTighten,
      };

  @override
  Future<AutonomyOverview> overview() async =>
      AutonomyOverview.fromJson(JsonReader(_overview));

  @override
  Future<AutonomyOverview> save(Map<String, Object?> body) async {
    calls.add('save');
    if (refuseSave != null) throw ApiException(422, refuseSave!);
    saved = body;
    settings = {...body};
    return overview();
  }

  @override
  Future<List<AutonomyRule>> rules() async {
    calls.add('rules');
    if (failRulesOnce) {
      failRulesOnce = false;
      throw const ApiException(0, 'Could not reach ANUM.');
    }
    if (rulesForbidden) {
      throw const ApiException(
          403, 'Your role cannot read the autonomy settings.');
    }
    return [for (final r in rows) AutonomyRule.fromJson(JsonReader(r))];
  }

  @override
  Future<AutonomyRule> addRule(
      {required String tool, String? template, required String level}) async {
    calls.add('addRule:$tool:${template ?? ''}:$level');
    if (refuseRule != null) throw ApiException(422, refuseRule!);
    final row =
        _rule('r_new${rows.length}', tool, template: template, level: level);
    rows = [...rows, row];
    return AutonomyRule.fromJson(JsonReader(row));
  }

  @override
  Future<void> deleteRule(String id) async {
    calls.add('deleteRule:$id');
    rows = [
      for (final r in rows)
        if (r['id'] != id) r,
    ];
  }

  @override
  Future<OrgGuardrails> guardrails() async {
    calls.add('guardrails');
    if (guardrailsForbidden) {
      throw const ApiException(
          403, 'Your role cannot read the organization settings.');
    }
    return OrgGuardrails.fromJson(JsonReader(guardrailJson));
  }

  @override
  Future<OrgGuardrails> saveGuardrails(Map<String, Object?> body) async {
    calls.add('saveGuardrails');
    savedGuardrails = body;
    guardrailJson = {...body};
    return OrgGuardrails.fromJson(JsonReader(body));
  }

  // The feed side is not used here.
  @override
  Future<ActionPage> actions(
          {String? category,
          String? query,
          String? cursor,
          int limit = 25}) async =>
      const ActionPage([], null);
  @override
  Future<AutonomousAction> undo(AutonomousAction action) =>
      throw UnimplementedError();
  @override
  Future<void> tighten(String actionId) async {}
  @override
  Future<List<AutonomyProposal>> proposals() async => const [];
  @override
  Future<void> decide(String proposalId, {required bool accept}) async {}
}

Future<void> _pump(WidgetTester tester, Widget child,
    {Size size = const Size(390, 844),
    double scale = 1,
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
      ...GlobalMaterialLocalizations.delegates,
    ],
    builder: (context, content) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(scale)),
        child: content!),
    home: child,
  ));
  await tester.pumpAndSettle();
}

Future<void> _type(WidgetTester tester, String key, String text) async {
  final finder = find.byKey(Key(key));
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.enterText(finder, text);
  await tester.pump();
}

Future<void> _tap(WidgetTester tester, String key) async {
  final finder = find.byKey(Key(key));
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  group('strings', () {
    test('every English key has an Arabic one and no dashes', () {
      expect(autonomyPolicyAr.keys.toSet(), autonomyPolicyEn.keys.toSet());
      for (final e in [
        ...autonomyPolicyEn.entries,
        ...autonomyPolicyAr.entries
      ]) {
        expect(e.value, isNot(contains(String.fromCharCode(0x2014))),
            reason: e.key);
        expect(e.value, isNot(contains(String.fromCharCode(0x2013))),
            reason: e.key);
        expect(e.key, isNot(matches(r'\.(one|other|two|few|many|zero)$')));
      }
    });
  });

  group('models', () {
    test('the overview exposes the new settings and keeps unknown fields', () {
      final o = AutonomyOverview.fromJson(JsonReader(_Fake()._overview));
      expect(o.priceChangePercentThreshold, 2.5);
      expect(o.dailyCaps, {'email': 20});
      expect(o.blockedRecipients, ['old@example.com']);
      expect(o.blockedDomains, ['competitor.example']);
      expect(o.quietHours, isNull);
      expect(o.canManageGuardrails, isFalse);
      final body = o.body(
        levels: {'email': 'ask_always'},
        recipientsThreshold: 5,
        amountThreshold: 0,
        approvedTemplates: const [],
        quietHours: const QuietHours(start: '22:00', end: '07:00'),
        dailyCaps: {'email': 10},
      );
      expect(body['future_field'], 'kept');
      expect(body['quiet_hours'],
          {'start': '22:00', 'end': '07:00', 'timezone': 'UTC'});
      expect(body['daily_caps'], {'email': 10});
      expect(body.containsKey('updated_by'), isFalse);
      final off = o.body(
          levels: const {},
          recipientsThreshold: 5,
          amountThreshold: 0,
          approvedTemplates: const [],
          clearQuietHours: true);
      expect(off['quiet_hours'], isNull);
    });

    test('recipients and domains are normalized like the server', () {
      expect(normalizeRecipient('  Name@Example.COM '), 'name@example.com');
      expect(normalizeRecipient('+966 (50) 123-4567'), '+966501234567');
      expect(plausibleRecipient('+966501234567'), isTrue);
      expect(plausibleRecipient('not an address'), isFalse);
      expect(plausibleRecipient('a@b'), isFalse);
      expect(normalizeDomain(' @Example.com '), 'example.com');
      expect(plausibleDomain('example.com'), isTrue);
      expect(plausibleDomain('http://example.com'), isFalse);
      expect(validHhmm('22:00'), isTrue);
      expect(validHhmm('24:00'), isFalse);
      expect(plausibleZone('Asia/Riyadh'), isTrue);
      expect(plausibleZone('not a zone'), isFalse);
      expect(validCap(100000), isTrue);
      expect(validCap(100001), isFalse);
    });
  });

  group('settings controller', () {
    test('saves quiet hours, caps and blocked lists, keeping other fields',
        () async {
      final repo = _Fake();
      final c = AutonomySettingsController(repo);
      await c.load();
      c.policy.quietOn = true;
      c.policy.quietStart = '21:30';
      c.policy.quietEnd = '06:00';
      c.policy.quietZone = 'Asia/Riyadh';
      c.policy.caps.add(CapRow('Whatsapp', '7'));
      expect(c.policy.addRecipient(' New@Example.com ', c.l), isNull);
      expect(c.policy.addDomain('@Spam.example', c.l), isNull);
      c.policy.removeRecipient('old@example.com');
      c.priceChange = '5';
      c.proposalAfter = '4';
      expect(await c.save(), isTrue);
      final body = repo.saved!;
      expect(body['quiet_hours'],
          {'start': '21:30', 'end': '06:00', 'timezone': 'Asia/Riyadh'});
      expect(body['daily_caps'], {'email': 20, 'whatsapp': 7});
      expect(body['blocked_recipients'], ['new@example.com']);
      expect(body['blocked_domains'], ['competitor.example', 'spam.example']);
      expect(body['price_change_percent_threshold'], 5);
      expect(body['proposal_after'], 4);
      expect(body['future_field'], 'kept');
      expect(c.notice, startsWith('Autonomy saved.'));
    });

    test('turning quiet hours off sends null; bad values never reach the API',
        () async {
      final repo = _Fake();
      repo.settings['quiet_hours'] = {
        'start': '22:00',
        'end': '07:00',
        'timezone': 'UTC'
      };
      final c = AutonomySettingsController(repo);
      await c.load();
      expect(c.policy.quietOn, isTrue);
      c.policy.quietStart = '25:00';
      expect(await c.save(), isFalse);
      expect(c.notice, contains('HH:MM'));
      expect(repo.calls, isNot(contains('save')));
      c.policy.quietStart = '22:00';
      c.policy.quietZone = 'Not A Zone!';
      expect(c.problem, contains('IANA'));
      c.policy.quietOn = false;
      c.policy.caps.add(CapRow('', '3'));
      expect(c.problem, contains('area or tool name'));
      c.policy.caps.last.key = 'email';
      expect(c.problem, contains('listed twice'));
      c.policy.caps.last.key = 'files';
      c.policy.caps.last.value = '100001';
      expect(c.problem, contains('0 to 100000'));
      c.policy.caps.last.value = '0';
      c.priceChange = '1001';
      expect(c.problem, contains('0 to 1000'));
      c.priceChange = '2.5';
      c.proposalAfter = '1';
      expect(c.problem, contains('2 to 50'));
      c.proposalAfter = '3';
      expect(await c.save(), isTrue);
      expect(repo.saved!['quiet_hours'], isNull);
    });

    test('the lists reject bad and repeated entries', () {
      final c = AutonomySettingsController(_Fake());
      expect(c.policy.addRecipient('nope', c.l), contains('email address'));
      expect(c.policy.addDomain('http://x.com/path', c.l), contains('domain'));
      expect(c.policy.addDomain('a.example', c.l), isNull);
      expect(c.policy.addDomain('A.example', c.l), contains('already'));
    });

    test('the 422 above the guardrails is shown as the API sent it', () async {
      final repo = _Fake()..refuseSave = _above;
      final c = AutonomySettingsController(repo);
      await c.load();
      c.policy.caps.first.value = '60';
      expect(await c.save(), isFalse);
      expect(c.notice, _above);
      expect(c.noticeFailed, isTrue);
      expect(c.readOnly, isFalse);
    });
  });

  group('rules controller', () {
    test('lists, adds, replaces and removes; the refusal is verbatim',
        () async {
      final repo = _Fake();
      final c = AutonomyRulesController(repo);
      await c.load();
      expect(c.rules, hasLength(2));
      expect(c.canManage, isTrue);
      repo.refuseRule = 'Acting silently is only for low-risk tools; choose '
          'act and notify.';
      expect(
          await c.add(tool: 'email.send', template: '', level: 'act_silently'),
          isFalse);
      expect(c.addError, repo.refuseRule);
      repo.refuseRule = null;
      expect(
          await c.add(
              tool: ' email.send ', template: ' tpl ', level: 'act_and_notify'),
          isTrue);
      expect(repo.calls, contains('addRule:email.send:tpl:act_and_notify'));
      expect(c.rules.first.tool, 'email.send');
      expect(c.notice, contains('email.send'));
      await c.remove(c.rules.first);
      expect(repo.calls.last, startsWith('deleteRule:r_new'));
      expect(c.rules, hasLength(2));
      expect(
          await c.add(tool: '  ', template: '', level: 'ask_always'), isFalse);
      expect(c.addError, 'Enter the tool name.');
    });

    test('a forbidden read keeps the API text; search and paging past 20',
        () async {
      final denied = AutonomyRulesController(_Fake(rulesForbidden: true));
      await denied.load();
      expect(denied.phase, AdminPhase.forbidden);
      expect(denied.loadMessage, contains('cannot read the autonomy'));

      final c = AutonomyRulesController(_Fake(ruleCount: 45));
      await c.load();
      expect(c.searchable, isTrue);
      expect(c.shown, hasLength(20));
      c.showMore();
      expect(c.shown, hasLength(40));
      c.search('tool44');
      expect(c.matching.single.tool, 'email.tool44');
    });
  });

  group('guardrails controller', () {
    test('an administrator edits and saves the floors', () async {
      final repo = _Fake(admin: true);
      final c = GuardrailsController(repo);
      await c.load();
      expect(c.readOnly, isFalse);
      expect(c.spending, '1000');
      c.spending = '2000';
      c.setCeiling('email', 'act_and_notify');
      c.setCeiling('commerce', null);
      c.policy.quietOn = false;
      c.policy.addDomain('bad.example', c.l);
      expect(await c.save(), isTrue);
      final body = repo.savedGuardrails!;
      expect(body['spending_limit'], 2000);
      expect(body['category_ceilings'], {'email': 'act_and_notify'});
      expect(body['quiet_hours'], isNull);
      expect(body['blocked_domains'], ['bad.example']);
      expect(body['max_auto_amount'], isNull);
      expect(c.notice, startsWith('Guardrails saved.'));
    });

    test('members cannot edit; bad numbers are refused before sending',
        () async {
      final member = GuardrailsController(_Fake());
      await member.load();
      expect(member.readOnly, isTrue);
      expect(await member.save(), isFalse);

      final repo = _Fake(admin: true);
      final c = GuardrailsController(repo);
      await c.load();
      c.maxRecipients = '10001';
      expect(await c.save(), isFalse);
      c.maxRecipients = '8';
      c.maxPrice = '-1';
      expect(c.problem, contains('Highest price change threshold'));
      expect(repo.calls, isNot(contains('saveGuardrails')));
    });
  });

  group('settings screen', () {
    testWidgets('an owner sets quiet hours, a cap and blocked entries',
        (tester) async {
      final repo = _Fake();
      await _pump(tester,
          AutonomySettingsScreen(controller: AutonomySettingsController(repo)));
      expect(find.textContaining('are edited on the web'), findsNothing);
      await _tap(tester, 'quiet-on');
      await _type(tester, 'quiet-start', '21:00');
      await _type(tester, 'quiet-end', '05:30');
      await _type(tester, 'quiet-zone', 'Asia/Riyadh');
      await _tap(tester, 'cap-add');
      await _type(tester, 'cap-key-1', 'files');
      await _type(tester, 'cap-value-1', '9');
      await _type(tester, 'blocked-domain-input', 'Spam.Example');
      await _tap(tester, 'blocked-domain-add');
      await _type(tester, 'blocked-recipient-input', 'nope');
      await _tap(tester, 'blocked-recipient-add');
      expect(find.textContaining('Enter an email address'), findsOneWidget);
      await _type(tester, 'blocked-recipient-input', '+966 50 123 4567');
      await _tap(tester, 'blocked-recipient-add');
      await _tap(tester, 'autonomy-save');
      final body = repo.saved!;
      expect(body['quiet_hours'],
          {'start': '21:00', 'end': '05:30', 'timezone': 'Asia/Riyadh'});
      expect(body['daily_caps'], {'email': 20, 'files': 9});
      expect(body['blocked_domains'], ['competitor.example', 'spam.example']);
      expect(body['blocked_recipients'], ['old@example.com', '+966501234567']);
      expect(body['future_field'], 'kept');
      expect(find.byKey(const Key('autonomy-notice')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('removing a cap and a blocked entry, then a refused save',
        (tester) async {
      final repo = _Fake()..refuseSave = _above;
      await _pump(tester,
          AutonomySettingsScreen(controller: AutonomySettingsController(repo)));
      await _tap(tester, 'cap-remove-0');
      expect(find.byKey(const Key('caps-empty')), findsOneWidget);
      await tester.ensureVisible(find.byTooltip('Remove old@example.com'));
      await tester.tap(find.byTooltip('Remove old@example.com'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('blocked-recipient-empty')), findsOneWidget);
      await _tap(tester, 'autonomy-save');
      expect(find.text(_above), findsOneWidget);
      expect(find.byKey(const Key('autonomy-save')), findsOneWidget);
    });

    testWidgets('members see the values read-only, with no way to edit',
        (tester) async {
      final repo = _Fake(owner: false);
      await _pump(tester,
          AutonomySettingsScreen(controller: AutonomySettingsController(repo)),
          size: const Size(320, 700), scale: 2);
      expect(find.byKey(const Key('autonomy-owner-required')), findsOneWidget);
      expect(find.byKey(const Key('autonomy-save')), findsNothing);
      expect(find.byKey(const Key('cap-add')), findsNothing);
      expect(find.byKey(const Key('blocked-domain-input')), findsNothing);
      expect(find.byKey(const Key('open-rules')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('rules and guardrails open from the settings screen',
        (tester) async {
      final repo = _Fake(admin: true);
      await _pump(tester,
          AutonomySettingsScreen(controller: AutonomySettingsController(repo)));
      await _tap(tester, 'open-rules');
      expect(find.text('Rules for exact patterns'), findsOneWidget);
      expect(find.byKey(const Key('rule-r0')), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await _tap(tester, 'open-guardrails');
      expect(find.text('Floors every workspace keeps'), findsOneWidget);
      expect(find.byKey(const Key('guardrails-save')), findsOneWidget);
    });

    testWidgets('a load failure offers a retry that works', (tester) async {
      final repo = _Fake();
      final c = AutonomySettingsController(_Failing(repo));
      await _pump(tester, AutonomySettingsScreen(controller: c));
      expect(find.text('Retry'), findsOneWidget);
      (c.repository as _Failing).fail = false;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('autonomy-save')), findsOneWidget);
    });
  });

  group('rules screen', () {
    testWidgets('lists, adds with the refusal shown, and deletes',
        (tester) async {
      final repo = _Fake();
      final c = AutonomyRulesController(repo);
      await _pump(tester, AutonomyRulesScreen(controller: c));
      expect(find.text('email.tool0'), findsOneWidget);
      expect(find.text('email.tool1 with template tpl1'), findsOneWidget);
      repo.refuseRule = 'No tool named "nope" is available in this deployment.';
      await _type(tester, 'rule-tool', 'nope');
      await _tap(tester, 'rule-add');
      expect(find.text(repo.refuseRule!), findsOneWidget);
      repo.refuseRule = null;
      await _type(tester, 'rule-tool', 'email.send');
      await _type(tester, 'rule-template', 'order_status');
      await tester.ensureVisible(find.byKey(const Key('rule-level')));
      await tester.tap(find.byKey(const Key('rule-level')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Act and notify').last);
      await tester.pumpAndSettle();
      await _tap(tester, 'rule-add');
      expect(repo.calls,
          contains('addRule:email.send:order_status:act_and_notify'));
      expect(find.byKey(const Key('rules-notice')), findsOneWidget);
      await _tap(tester, 'rule-delete-r0');
      expect(repo.calls, contains('deleteRule:r0'));
      expect(find.text('email.tool0'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty state offers the next action; members only tighten',
        (tester) async {
      final repo = _Fake(ruleCount: 0, owner: false);
      await _pump(tester,
          AutonomyRulesScreen(controller: AutonomyRulesController(repo)));
      expect(find.text('No rules yet'), findsOneWidget);
      expect(find.byKey(const Key('rules-owner-required')), findsOneWidget);
      expect(find.textContaining('always ask'), findsWidgets);
      await _tap(tester, 'rule-level');
      expect(find.text('Act silently'), findsNothing);
    });

    testWidgets('someone who cannot add sees what is in force, no form',
        (tester) async {
      final repo = _Fake(owner: false, canTighten: false);
      await _pump(tester,
          AutonomyRulesScreen(controller: AutonomyRulesController(repo)));
      expect(find.byKey(const Key('rule-tool')), findsNothing);
      expect(find.byKey(const Key('rule-delete-r0')), findsNothing);
      expect(find.text('email.tool0'), findsOneWidget);
    });

    testWidgets('forbidden shows the API text; error retries', (tester) async {
      await _pump(
          tester,
          AutonomyRulesScreen(
              controller:
                  AutonomyRulesController(_Fake(rulesForbidden: true))));
      expect(find.byKey(const Key('rules-forbidden')), findsOneWidget);
      expect(find.textContaining('Your role cannot read the autonomy'),
          findsOneWidget);

      final repo = _Fake(failRulesOnce: true);
      await _pump(
          tester,
          AutonomyRulesScreen(
              key: UniqueKey(), controller: AutonomyRulesController(repo)));
      expect(find.byKey(const Key('rules-failed')), findsOneWidget);
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('email.tool0'), findsOneWidget);
    });

    testWidgets('long lists get search and Show more', (tester) async {
      final repo = _Fake(ruleCount: 45);
      await _pump(tester,
          AutonomyRulesScreen(controller: AutonomyRulesController(repo)));
      expect(find.byKey(const Key('rules-search')), findsOneWidget);
      expect(find.byKey(const Key('rule-r19')), findsOneWidget);
      expect(find.byKey(const Key('rule-r20')), findsNothing);
      await _tap(tester, 'rules-more');
      expect(find.byKey(const Key('rule-r20')), findsOneWidget);
      await tester.enterText(find.byKey(const Key('rules-search')), 'tool44');
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('rule-r44')), findsOneWidget);
      expect(find.byKey(const Key('rule-r1')), findsNothing);
      await tester.enterText(find.byKey(const Key('rules-search')), 'zzz');
      await tester.pumpAndSettle();
      expect(find.textContaining('matches "zzz"'), findsOneWidget);
    });
  });

  group('guardrails screen', () {
    testWidgets('an administrator edits and saves', (tester) async {
      final repo = _Fake(admin: true);
      await _pump(tester,
          AutonomyGuardrailsScreen(controller: GuardrailsController(repo)));
      expect(find.byKey(const Key('guardrails-admin-required')), findsNothing);
      await _type(tester, 'g-spending', '1500');
      await _type(tester, 'g-max-price', '10');
      await _type(tester, 'g-domain-input', 'bad.example');
      await _tap(tester, 'g-domain-add');
      await _tap(tester, 'guardrails-save');
      expect(repo.savedGuardrails!['spending_limit'], 1500);
      expect(repo.savedGuardrails!['max_auto_price_change_percent'], 10);
      expect(repo.savedGuardrails!['blocked_domains'], ['bad.example']);
      expect(find.byKey(const Key('guardrails-notice')), findsOneWidget);
    });

    testWidgets('a member sees the floors read-only with the reason',
        (tester) async {
      final repo = _Fake();
      await _pump(tester,
          AutonomyGuardrailsScreen(controller: GuardrailsController(repo)),
          size: const Size(320, 700), scale: 2);
      expect(
          find.byKey(const Key('guardrails-admin-required')), findsOneWidget);
      expect(find.byKey(const Key('guardrails-save')), findsNothing);
      expect(find.text('blocked@example.com'), findsOneWidget);
      expect(find.byKey(const Key('g-domain-input')), findsNothing);
      final spending =
          tester.widget<TextFormField>(find.byKey(const Key('g-spending')));
      expect(spending.enabled, isFalse);
      expect(tester.takeException(), isNull);
    });

    testWidgets('forbidden read and failure states', (tester) async {
      await _pump(
          tester,
          AutonomyGuardrailsScreen(
              controller:
                  GuardrailsController(_Fake(guardrailsForbidden: true))));
      expect(find.byKey(const Key('guardrails-forbidden')), findsOneWidget);
      expect(find.textContaining('cannot read the organization settings'),
          findsOneWidget);
    });
  });

  group('layout', () {
    for (final width in [320.0, 390.0, 768.0, 1440.0]) {
      for (final arabic in [false, true]) {
        testWidgets(
            'settings, rules and guardrails at ${width.toInt()} wide, 200% text'
            '${arabic ? ', Arabic' : ''}', (tester) async {
          final repo = _Fake(admin: true, ruleCount: 30);
          final locale = Locale(arabic ? 'ar' : 'en');
          final size = Size(width, 900);
          final settings = AutonomySettingsController(repo);
          await _pump(tester, AutonomySettingsScreen(controller: settings),
              size: size, scale: 2, locale: locale);
          await _tap(tester, 'cap-add');
          await _type(tester, 'blocked-domain-input', 'x');
          await _tap(tester, 'blocked-domain-add');
          expect(tester.takeException(), isNull);
          await _pump(tester,
              AutonomyRulesScreen(controller: AutonomyRulesController(repo)),
              size: size, scale: 2, locale: locale);
          expect(find.byKey(const Key('rules-search')), findsOneWidget);
          expect(tester.takeException(), isNull);
          await _pump(tester,
              AutonomyGuardrailsScreen(controller: GuardrailsController(repo)),
              size: size, scale: 2, locale: locale);
          expect(find.byKey(const Key('guardrails-save')), findsOneWidget);
          expect(tester.takeException(), isNull);
          if (arabic) {
            expect(
                Directionality.of(
                    tester.element(find.byKey(const Key('guardrails-save')))),
                TextDirection.rtl);
            expect(find.text('الحدود'), findsOneWidget);
          }
        });
      }
    }

    testWidgets('Arabic notices and tap targets', (tester) async {
      final repo = _Fake();
      final c = AutonomyRulesController(repo);
      await _pump(tester, AutonomyRulesScreen(controller: c),
          locale: const Locale('ar'));
      expect(find.text('قواعد لأنماط محددة'), findsOneWidget);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}

/// Fails the first overview read, like a dropped connection.
class _Failing extends _Fake {
  _Failing(_Fake inner) : super(owner: inner.owner);
  bool fail = true;
  @override
  Future<AutonomyOverview> overview() async {
    if (fail) throw const ApiException(0, 'Could not reach ANUM.');
    return super.overview();
  }
}
