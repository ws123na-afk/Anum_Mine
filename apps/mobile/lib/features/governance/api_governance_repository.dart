import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'governance_models.dart';
import 'governance_repository.dart';

abstract interface class AuditExporter {
  Future<void> export(String format);
}

class ApiGovernanceRepository implements GovernanceRepository {
  const ApiGovernanceRepository(this.api, {required this.auditExporter});
  final AnumApiClient api;
  final AuditExporter auditExporter;
  Future<List<JsonReader>> _one(String p) async =>
      [JsonReader(await api.request('GET', p))];
  Future<List<JsonReader>> _list(String p) async =>
      JsonReader(await api.request('GET', p)).optObjects('data', (x) => x);

  @override
  Future<GovernanceSnapshot> load() async {
    try {
      // Future.wait (not a record's `.wait`) so an ApiException reaches the
      // offline mapping below unwrapped. Single objects come back as
      // one-element lists, keeping every result the same type.
      final v = await Future.wait([
        _one('/api/v1/organization/governance'),
        _list('/api/v1/policy-packs'),
        _list('/api/v1/marketplace/packages'),
        _list('/api/v1/marketplace/installs'),
        _list('/api/v1/routing/targets'),
        _one('/api/v1/enterprise/operations')
      ]);
      final installs = v[3];
      return GovernanceSnapshot(
          summary: _summary(v[0].single),
          policies: v[1].map(_policy).toList(),
          marketplace: v[2].map((x) => _market(x, installs)).toList(),
          targets: v[4].map(_target).toList(),
          operations: _ops(v[5].single));
    } on ApiException catch (e) {
      if (e.statusCode == 0 || e.statusCode >= 500) {
        throw GovernanceOfflineException(e.message);
      }
      rethrow;
    }
  }

  @override
  Future<PolicyPack> createBaselinePolicy(String name) async => _policy(
          JsonReader(await api.request('POST', '/api/v1/policy-packs', body: {
        'name': name,
        'description': 'Mobile-managed organization baseline',
        'rules': [
          {
            'action': 'integration.write',
            'effect': 'require_approval',
            'conditions': {'risk': 'high'}
          }
        ]
      })));
  @override
  Future<PolicyPack> setPolicyActive(String id, {required bool active}) async =>
      _policy(JsonReader(await api.request('POST',
          '/api/v1/policy-packs/$id/${active ? 'activate' : 'archive'}')));
  @override
  Future<void> createRoleTemplate(String name, List<String> permissions) async {
    await api.request('POST', '/api/v1/role-templates',
        body: {'name': name, 'permissions': permissions});
  }

  @override
  Future<void> createApprovalRule(String name, String pattern,
      {int minimumApprovers = 1,
      List<String> requiredRoles = const ['owner']}) async {
    await api.request('POST', '/api/v1/organization/approval-rules', body: {
      'name': name,
      'action_pattern': pattern,
      'minimum_approvers': minimumApprovers,
      'required_roles': requiredRoles
    });
  }

  @override
  Future<void> updateMemoryGovernance(
      {required int retentionDays,
      required bool allowPermanent,
      required bool requireProvenance,
      List<String> sourceTypes = const []}) async {
    await api.request('PUT', '/api/v1/organization/memory-governance', body: {
      'default_retention_days': retentionDays,
      'allow_permanent_retention': allowPermanent,
      'require_provenance': requireProvenance,
      'allowed_source_types': sourceTypes
    });
  }

  @override
  Future<void> upsertMarketplacePackage(MarketplaceItem item) async {
    await api.request('PUT', '/api/v1/marketplace/packages/${item.id}', body: {
      'id': item.id,
      'name': item.name,
      'kind': item.kind,
      'version': item.version,
      'publisher': item.publisher,
      'verified': item.verified,
      'permissions': item.permissions,
      'regions': item.regions
    });
  }

  @override
  Future<void> deleteMarketplacePackage(String id) async {
    await api.request('DELETE', '/api/v1/marketplace/packages/$id');
  }

  @override
  Future<void> installPackage(String id) async {
    await api.request('POST', '/api/v1/marketplace/packages/$id/install',
        body: <String, Object?>{});
  }

  @override
  Future<void> uninstallPackage(String id) async {
    await api.request('DELETE', '/api/v1/marketplace/packages/$id/install');
  }

  @override
  Future<RoutingTarget> updateTarget(RoutingTarget t) async =>
      _target(JsonReader(
          await api.request('PUT', '/api/v1/routing/targets/${t.id}', body: {
        'id': t.id,
        'region': t.region,
        'provider': t.provider,
        'model': t.model,
        'status': t.status,
        'modalities': t.modalities,
        'sensitivity': t.sensitivity,
        'cost_per_1k_tokens': t.cost,
        'latency_ms': t.latencyMs
      })));
  @override
  Future<void> exportAudit(String format) => auditExporter.export(format);
  GovernanceSummary _summary(JsonReader j) => GovernanceSummary(
      policyPacks: j.integer('policy_packs'),
      activePolicyPacks: j.integer('active_policy_packs'),
      roleTemplates: j.integer('role_templates'),
      approvalRules: j.integer('approval_rules'));
  PolicyPack _policy(JsonReader j) => PolicyPack(
      id: j.string('id'),
      name: j.string('name'),
      version: j.integer('version'),
      active: j.boolean('active'),
      rules: j.optObjects('rules', (x) => '${x['action']}: ${x['effect']}'));
  MarketplaceItem _market(JsonReader j, List<JsonReader> i) => MarketplaceItem(
      id: j.string('id'),
      name: j.string('name'),
      kind: j.string('kind'),
      version: j.string('version'),
      publisher: j.optString('publisher') ?? 'ANUM',
      regions: j.optStrings('regions') ?? const ['global'],
      verified: j.boolean('verified'),
      permissions: j.optStrings('permissions') ?? const [],
      installed: i.any((x) => x['package_id'] == j['id']));
  RoutingTarget _target(JsonReader j) => RoutingTarget(
      id: j.string('id'),
      region: j.string('region'),
      provider: j.string('provider'),
      model: j.string('model'),
      status: j.string('status'),
      latencyMs: j.integer('latency_ms'),
      cost: j.number('cost_per_1k_tokens'),
      modalities: j.optStrings('modalities') ?? const ['text'],
      sensitivity: j.optStrings('sensitivity') ?? const ['standard']);
  EnterpriseOperations _ops(JsonReader j) => EnterpriseOperations(
      activeRegions: j.integer('active_regions'),
      healthyTargets: j.integer('healthy_targets'),
      degradedTargets: j.integer('degraded_targets'),
      installedPackages: j.integer('installed_packages'),
      failoverReady: j.boolean('failover_ready'));
}
