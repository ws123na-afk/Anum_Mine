// The Organization (governance) screen: tenant-wide policy packs and routing
// targets are editable only for an organization administrator, whatever the
// workspace role (docs/governance-and-scale.md#who-may-change-what). The
// screen follows the API's `can_manage_organization_policy`, never the role.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/governance/governance.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _orgAdminRequired =
    'Organization administrator role required: this setting applies to every workspace in the organization';

class FakeGovernance implements GovernanceRepository {
  FakeGovernance({required this.canManage, this.writesForbidden = false});
  final bool canManage, writesForbidden;
  final calls = <String>[];
  bool active = true;

  @override
  Future<GovernanceSnapshot> load() async => GovernanceSnapshot(
        summary: GovernanceSummary(
            policyPacks: 1,
            activePolicyPacks: active ? 1 : 0,
            roleTemplates: 0,
            approvalRules: 0,
            canManageOrganizationPolicy: canManage),
        policies: [
          PolicyPack(
              id: 'policy_1',
              name: 'Organization baseline',
              version: 1,
              active: active,
              rules: const ['integration.write: require_approval'],
              ruleItems: const [
                PackRule(
                    action: 'integration.write',
                    effect: 'require_approval',
                    conditions: {'risk': 'high'})
              ])
        ],
        marketplace: const [],
        targets: const [
          RoutingTarget(
              id: 'configured-model',
              region: 'local',
              provider: 'ollama',
              model: 'llama3.2',
              status: 'healthy',
              latencyMs: 1,
              cost: 0)
        ],
        operations: const EnterpriseOperations(
            activeRegions: 1,
            healthyTargets: 1,
            degradedTargets: 0,
            installedPackages: 0,
            failoverReady: false),
      );

  Future<T> _write<T>(String call, T Function() result) async {
    calls.add(call);
    if (writesForbidden) throw const ApiException(403, _orgAdminRequired);
    return result();
  }

  @override
  Future<PolicyPack> setPolicyActive(String id, {required bool active}) =>
      _write('setPolicyActive', () {
        this.active = active;
        return PolicyPack(
            id: id,
            name: 'Organization baseline',
            version: 1,
            active: active,
            rules: const []);
      });
  @override
  Future<PolicyPack> publishPolicyPack(Map<String, Object?> body) =>
      _write('publish', () {
        published.add(body);
        return PolicyPack(
            id: 'p2',
            name: body['name']! as String,
            version: 2,
            active: true,
            rules: const []);
      });
  final published = <Map<String, Object?>>[];
  @override
  Future<void> deletePolicyPack(String id) => _write('delete $id', () {});
  @override
  Future<PolicyPack> createBaselinePolicy(String name) => _write(
      'baseline',
      () => PolicyPack(
          id: 'p', name: name, version: 1, active: true, rules: const []));
  @override
  Future<RoutingTarget> updateTarget(RoutingTarget target) =>
      _write('updateTarget', () => target);
  @override
  Future<RoutingTarget> probeTarget(String id) => _write(
      'probeTarget',
      () => RoutingTarget(
          id: id,
          region: 'local',
          provider: 'ollama',
          model: 'llama3.2',
          status: 'healthy',
          latencyMs: 1,
          cost: 0,
          lastCheckedAt: DateTime.utc(2026, 11, 10, 10),
          measuredLatencyMs: 240));
  @override
  Future<void> createRoleTemplate(String name, List<String> permissions) =>
      _write('roleTemplate', () {});
  @override
  Future<void> createApprovalRule(String name, String pattern,
          {int minimumApprovers = 1,
          List<String> requiredRoles = const ['owner']}) =>
      _write('approvalRule', () {});
  @override
  Future<void> updateMemoryGovernance(
          {required int retentionDays,
          required bool allowPermanent,
          required bool requireProvenance,
          List<String> sourceTypes = const []}) =>
      _write('memoryGovernance', () {});
  @override
  Future<void> upsertMarketplacePackage(MarketplaceItem item) =>
      _write('upsertPackage', () {});
  @override
  Future<void> deleteMarketplacePackage(String id) =>
      _write('deletePackage', () {});
  @override
  Future<void> installPackage(String id) => _write('install', () {});
  @override
  Future<void> uninstallPackage(String id) => _write('uninstall', () {});
  @override
  Future<void> exportAudit(String format) async {}
}

Widget _app(GovernanceController controller,
        [GovernanceSection section = GovernanceSection.policies]) =>
    MaterialApp(
        theme: AnumTheme.dark(),
        home:
            GovernanceScreen(controller: controller, initialSection: section));

void main() {
  testWidgets(
      'a workspace owner who is not an organization administrator reads policy packs',
      (tester) async {
    final repo = FakeGovernance(canManage: false);
    await tester.pumpWidget(_app(GovernanceController(repo)));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('organization-read-only')), findsOneWidget);
    expect(find.textContaining('Owning this workspace is not enough'),
        findsOneWidget);
    expect(find.text('Organization baseline'), findsOneWidget);
    expect(find.byKey(const Key('pack-new')), findsNothing);
    expect(find.byKey(const Key('pack-new-version-Organization baseline')),
        findsNothing);
    expect(repo.calls, isEmpty);
  });

  testWidgets('routing targets are read only for the same caller',
      (tester) async {
    final repo = FakeGovernance(canManage: false);
    await tester.pumpWidget(
        _app(GovernanceController(repo), GovernanceSection.routing));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('organization-read-only')), findsOneWidget);
    expect(find.text('ollama · llama3.2'), findsOneWidget);
    expect(find.byTooltip('Target actions'), findsNothing);
    // A typed status counts for nothing until a probe ran.
    expect(find.text('Not yet probed'), findsOneWidget);
    expect(
        find.text(
            'Health is measured by a probe, never typed. Organization administrators probe targets and take them out of routing.'),
        findsOneWidget);
  });

  testWidgets('an organization administrator archives a policy pack',
      (tester) async {
    final repo = FakeGovernance(canManage: true);
    await tester.pumpWidget(_app(GovernanceController(repo)));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('organization-read-only')), findsNothing);
    await tester.tap(find.byKey(const Key('pack-versions-Organization baseline')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('pack-toggle-policy_1')));
    await tester.pumpAndSettle();
    expect(repo.calls, ['setPolicyActive']);
    expect(find.text('Organization baseline v1 archived.'), findsOneWidget);
    expect(find.text('No active version'), findsOneWidget);
  });

  testWidgets('a refused change keeps the data and shows the API answer',
      (tester) async {
    final repo = FakeGovernance(canManage: true, writesForbidden: true);
    await tester.pumpWidget(_app(GovernanceController(repo)));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('pack-versions-Organization baseline')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('pack-toggle-policy_1')));
    await tester.pumpAndSettle();
    expect(find.text('Not changed: $_orgAdminRequired'), findsOneWidget);
    expect(find.text('Organization baseline'), findsOneWidget);
  });
}
