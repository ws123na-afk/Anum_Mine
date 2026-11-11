// Policy packs on the Organization screen (docs/governance-and-scale.md#policy-packs):
// grouping, search, the version diff and the editor's checks; publishing a
// new pack from the baseline and a new version, comparing two versions,
// deleting an archived one; and layouts from 320 to 1024 wide at 2x text in
// English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/governance/governance.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'lists/lists_harness.dart';

PolicyPack _pack(String id, String name, int version, bool active,
        List<PackRule> rules) =>
    PolicyPack(
        id: id,
        name: name,
        version: version,
        active: active,
        rules: [for (final r in rules) '${r.action}: ${r.effect}'],
        ruleItems: rules,
        createdAt: DateTime.utc(2026, 11, version));

const _v1 = [
  PackRule(action: 'payment.*', effect: 'require_approval'),
  PackRule(action: 'memory.delete', effect: 'deny'),
];
const _v2 = [
  PackRule(
      action: 'payment.*',
      effect: 'require_approval',
      conditions: {'risk': 'high'}),
  PackRule(action: 'email.send', effect: 'allow'),
];

class _Repo implements GovernanceRepository {
  _Repo({this.canManage = true});
  final bool canManage;
  final calls = <String>[];
  final published = <JsonMap>[];
  List<PolicyPack> packs = [
    _pack('p1', 'Baseline', 1, false, _v1),
    _pack('p2', 'Baseline', 2, true, _v2),
    _pack('p3', 'Finance', 1, true, _v1),
  ];
  Object? refuse;

  @override
  Future<GovernanceSnapshot> load() async => GovernanceSnapshot(
      summary: GovernanceSummary(
          policyPacks: packs.length,
          activePolicyPacks: packs.where((p) => p.active).length,
          roleTemplates: 0,
          approvalRules: 0,
          canManageOrganizationPolicy: canManage),
      policies: packs,
      marketplace: const [],
      targets: const [],
      operations: const EnterpriseOperations(
          activeRegions: 0,
          healthyTargets: 0,
          degradedTargets: 0,
          installedPackages: 0,
          failoverReady: false));

  @override
  Future<PolicyPack> publishPolicyPack(JsonMap body) async {
    calls.add('publish ${body['name']}');
    if (refuse != null) throw refuse!;
    published.add(body);
    final same = packs.where((p) => p.name == body['name']).toList();
    final version = same.isEmpty
        ? 1
        : same.map((p) => p.version).reduce((a, b) => a > b ? a : b) + 1;
    final pack =
        _pack('p_new', body['name']! as String, version, true, const []);
    packs = [
      for (final p in packs)
        p.name == pack.name
            ? _pack(p.id, p.name, p.version, false, p.ruleItems)
            : p,
      pack
    ];
    return pack;
  }

  @override
  Future<void> deletePolicyPack(String id) async {
    calls.add('delete $id');
    if (refuse != null) throw refuse!;
    packs = packs.where((p) => p.id != id).toList();
  }

  @override
  Future<PolicyPack> setPolicyActive(String id, {required bool active}) async {
    calls.add('active $id $active');
    return packs.firstWhere((p) => p.id == id);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<GovernanceController> _pump(WidgetTester tester, _Repo repo,
    {double width = 390, double text = 1, String locale = 'en'}) async {
  final c = GovernanceController(repo);
  await tester.pumpWidget(const SizedBox());
  await pumpList(tester, GovernanceScreen(controller: c),
      width: width, height: 2400, text: text, locale: locale, scaffold: false);
  return c;
}

void main() {
  group('models', () {
    test('groups by name, newest first, with the active version', () {
      final groups = packGroups(_Repo().packs);
      expect(groups.map((g) => g.name), ['Baseline', 'Finance']);
      expect(groups.first.versions.map((p) => p.version), [2, 1]);
      expect(groups.first.active!.id, 'p2');
      expect(filterPacks(_Repo().packs, 'email').single.id, 'p2');
      expect(filterPacks(_Repo().packs, 'DENY').map((p) => p.id), ['p1', 'p3']);
    });

    test('the diff marks added, removed, changed and unchanged rules', () {
      final changes = diffRules(_v1, _v2);
      expect({
        for (final c in changes) c.action: c.kind
      }, {
        'payment.*': RuleChangeKind.changed,
        'email.send': RuleChangeKind.added,
        'memory.delete': RuleChangeKind.removed,
      });
      expect(diffRules(_v1, _v1).every((c) => c.kind == RuleChangeKind.same),
          isTrue);
    });

    test('the editor body is checked like the web', () {
      expect(packBody('', '', [RuleDraft(action: 'x')]).error,
          'ad.gov.editor.err.name');
      expect(packBody('A', '', []).error, 'ad.gov.editor.err.noRules');
      final bad = packBody('A', '', [RuleDraft(action: 'x'), RuleDraft()]);
      expect((bad.error, bad.row), ('ad.gov.editor.err.action', 2));
      final json =
          packBody('A', '', [RuleDraft(action: 'x', conditions: '[1, 2]')]);
      expect((json.error, json.row), ('ad.gov.editor.err.conditions', 1));
      final ok = packBody(' Ops ', ' d ',
          [RuleDraft(action: 'x.*', effect: 'deny', conditions: '{"a": 1}')]);
      expect(ok.body, {
        'name': 'Ops',
        'description': 'd',
        'rules': [
          {
            'action': 'x.*',
            'effect': 'deny',
            'conditions': {'a': 1}
          }
        ],
      });
    });

    test('the API repository publishes and deletes', () async {
      final transport = ScriptedTransport((r) => ApiResponse(
          statusCode: r.method == 'DELETE' ? 204 : 201,
          body: r.method == 'DELETE'
              ? null
              : {
                  'id': 'p9',
                  'name': 'Ops',
                  'description': 'd',
                  'version': 3,
                  'active': true,
                  'rules': [
                    {
                      'action': 'x',
                      'effect': 'deny',
                      'conditions': {'risk': 'high'}
                    }
                  ],
                  'created_at': '2026-11-11T09:00:00Z',
                }));
      final repo = ApiGovernanceRepository(await scriptedClient(transport),
          auditExporter: _NoExport());
      final pack = await repo.publishPolicyPack({'name': 'Ops'});
      expect(transport.requests.last.uri.path, '/api/v1/policy-packs');
      expect(pack.ruleItems.single.conditions, {'risk': 'high'});
      expect(pack.description, 'd');
      await repo.deletePolicyPack('p9');
      expect(transport.requests.last.method, 'DELETE');
      expect(transport.requests.last.uri.path, '/api/v1/policy-packs/p9');
    });
  });

  group('screen', () {
    testWidgets('a new pack from the baseline, then a new version',
        (tester) async {
      final repo = _Repo();
      await _pump(tester, repo);
      await tester.tap(find.byKey(const Key('pack-new')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('editor-publish')));
      await tester.pumpAndSettle();
      expect(find.text('Name the policy pack.'), findsOneWidget);
      await tester.tap(find.byKey(const Key('editor-start')));
      await tester.pumpAndSettle();
      await tester.tap(
          find.text('Organization baseline (approval-first defaults)').last);
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('editor-rule-3')), findsOneWidget);
      // Move the last rule up and remove the first.
      await tester.ensureVisible(find.byTooltip('Move rule 3 up'));
      await tester.tap(find.byTooltip('Move rule 3 up'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('editor-remove-1')));
      await tester.tap(find.byKey(const Key('editor-remove-1')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('editor-publish')));
      await tester.tap(find.byKey(const Key('editor-publish')));
      await tester.pumpAndSettle();
      final body = repo.published.single;
      expect(body['name'], 'Organization baseline');
      expect([for (final r in body['rules']! as List) (r as Map)['action']],
          ['memory.delete', 'payment.*']);
      expect(find.text('Organization baseline v1 published and active.'),
          findsOneWidget);

      await tester.tap(find.byKey(const Key('pack-new-version-Baseline')));
      await tester.pumpAndSettle();
      expect(find.text('New version of Baseline'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('editor-conditions-1')), 'not json');
      await tester.ensureVisible(find.byKey(const Key('editor-publish')));
      await tester.tap(find.byKey(const Key('editor-publish')));
      await tester.pumpAndSettle();
      expect(find.textContaining('Rule 1: conditions must be a JSON object'),
          findsOneWidget);
      await tester.enterText(find.byKey(const Key('editor-conditions-1')), '');
      await tester.ensureVisible(find.byKey(const Key('editor-publish')));
      await tester.tap(find.byKey(const Key('editor-publish')));
      await tester.pumpAndSettle();
      expect(repo.published.last['name'], 'Baseline');
      expect(find.text('Baseline v3 published and active.'), findsOneWidget);
    });

    testWidgets('compare two versions and delete an archived one',
        (tester) async {
      final repo = _Repo();
      await _pump(tester, repo);
      await tester.tap(find.byKey(const Key('pack-compare-Baseline')));
      await tester.pumpAndSettle();
      expect(find.text('Compare Baseline'), findsOneWidget);
      expect(find.text('Added'), findsOneWidget);
      expect(find.text('Removed'), findsOneWidget);
      expect(find.text('Changed'), findsOneWidget);
      expect(find.textContaining('Before: payment.*'), findsOneWidget);
      Navigator.of(tester.element(find.text('Compare Baseline'))).pop();
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('pack-versions-Baseline')));
      await tester.pumpAndSettle();
      // The active version cannot be deleted; the archived one can.
      expect(find.byKey(const Key('pack-delete-p2')), findsNothing);
      await tester.tap(find.byKey(const Key('pack-delete-p1')));
      await tester.pumpAndSettle();
      expect(find.text('Delete Baseline v1?'), findsOneWidget);
      await tester.tap(find.byKey(const Key('pack-delete-confirm')));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('delete p1'));
      expect(find.text('Baseline v1 deleted.'), findsOneWidget);
      expect(find.byKey(const Key('pack-compare-Baseline')), findsNothing);
    });

    testWidgets('search narrows the groups; readers get no editor',
        (tester) async {
      final repo = _Repo(canManage: false);
      await _pump(tester, repo);
      expect(find.byKey(const Key('pack-new')), findsNothing);
      await tester.enterText(find.byKey(const Key('packs-search')), 'email');
      await tester.pumpAndSettle();
      expect(find.text('Packs shown: 1 of 1'), findsOneWidget);
      expect(find.byKey(const Key('pack-group-Finance')), findsNothing);
      await tester.enterText(find.byKey(const Key('packs-search')), 'zzz');
      await tester.pumpAndSettle();
      expect(find.text('No policy pack matches "zzz".'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0]) {
      testWidgets('packs, editor and compare fit at $width wide, 2x',
          (tester) async {
        for (final locale in ['en', 'ar']) {
          final c = await _pump(tester, _Repo(),
              width: width, text: 2, locale: locale);
          expect(tester.takeException(), isNull);
          await tester.ensureVisible(find.byKey(const Key('pack-new')));
          await tester.tap(find.byKey(const Key('pack-new')));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          if (locale == 'ar') {
            expect(find.text('حزمة سياسات جديدة'), findsWidgets);
          }
          Navigator.of(tester.element(find.byKey(const Key('editor-publish'))))
              .pop();
          await tester.pumpAndSettle();
          await tester
              .ensureVisible(find.byKey(const Key('pack-compare-Baseline')));
          await tester.tap(find.byKey(const Key('pack-compare-Baseline')));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await tester.pumpWidget(const SizedBox());
          c.dispose();
        }
      });
    }
  });
}

class _NoExport implements AuditExporter {
  @override
  Future<void> export(String format) async {}
}
