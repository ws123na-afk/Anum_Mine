import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/governance/api_governance_repository.dart';
import 'package:anum_mobile/features/governance/governance_models.dart';
import 'package:flutter_test/flutter_test.dart';

class Exporter implements AuditExporter {
  String? format;
  @override
  Future<void> export(String value) async {
    format = value;
  }
}

class GovernanceTransport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    final p = r.uri.path;
    if (p.endsWith('/organization/governance')) {
      return const ApiResponse(statusCode: 200, body: {
        'tenant_id': 'tenant_test',
        'policy_packs': 0,
        'active_policy_packs': 0,
        'role_templates': 0,
        'approval_rules': 0,
        'memory_governance_configured': false
      });
    }
    if (p.endsWith('/enterprise/operations')) {
      return const ApiResponse(statusCode: 200, body: {
        'tenant_id': 'tenant_test',
        'active_regions': 1,
        'healthy_targets': 1,
        'degraded_targets': 0,
        'installed_packages': 0,
        'failover_ready': true,
        'generated_at': '2026-08-30T10:00:00Z'
      });
    }
    if (p.contains('/policy-packs/policy_1/')) {
      return const ApiResponse(statusCode: 200, body: {
        'id': 'policy_1',
        'name': 'Baseline',
        'version': 1,
        'active': true,
        'rules': []
      });
    }
    if (p.endsWith('/routing/targets/target_1')) {
      return ApiResponse(statusCode: 200, body: r.body);
    }
    if (p.endsWith('/routing/targets/target_1/probe')) {
      return const ApiResponse(statusCode: 200, body: {
        'id': 'target_1',
        'region': 'us-east',
        'provider': 'openai',
        'model': 'gpt',
        'status': 'healthy',
        'latency_ms': 40,
        'cost_per_1k_tokens': 0.01,
        'enabled': true,
        'last_checked_at': '2026-11-10T10:00:00Z',
        'measured_latency_ms': 240,
        'last_error': null
      });
    }
    return const ApiResponse(statusCode: 200, body: {'data': []});
  }
}

void main() {
  test('governance maps operations, policy, marketplace and routing mutations',
      () async {
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession(
        accessToken: 'token',
        tokenType: 'bearer',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
        context: const TenantContext(
            tenantId: 'tenant_test',
            workspaceId: 'workspace_test',
            userId: 'user_test',
            roles: ['owner'])));
    final transport = GovernanceTransport();
    final exporter = Exporter();
    final repo = ApiGovernanceRepository(
        AnumApiClient(
            baseUri: Uri.parse('http://localhost:8000'),
            transport: transport,
            sessions: sessions),
        auditExporter: exporter);
    final snapshot = await repo.load();
    expect(snapshot.operations.failoverReady, isTrue);
    // No `can_manage_organization_policy` in the summary: read only.
    expect(snapshot.summary.canManageOrganizationPolicy, isFalse);
    await repo.setPolicyActive('policy_1', active: true);
    expect(transport.requests.last.uri.path,
        '/api/v1/policy-packs/policy_1/activate');
    await repo.createRoleTemplate('Operator', ['task.read']);
    expect(transport.requests.last.uri.path, '/api/v1/role-templates');
    await repo.createApprovalRule('External', 'integration.*');
    expect(transport.requests.last.uri.path,
        '/api/v1/organization/approval-rules');
    await repo.updateMemoryGovernance(
        retentionDays: 30, allowPermanent: false, requireProvenance: true);
    expect(transport.requests.last.uri.path,
        '/api/v1/organization/memory-governance');
    await repo.installPackage('package_1');
    expect(transport.requests.last.uri.path,
        '/api/v1/marketplace/packages/package_1/install');
    await repo.uninstallPackage('package_1');
    expect(transport.requests.last.method, 'DELETE');
    await repo.updateTarget(const RoutingTarget(
        id: 'target_1',
        region: 'us-east',
        provider: 'openai',
        model: 'gpt',
        status: 'offline',
        latencyMs: 40,
        cost: .01,
        modalities: ['text'],
        sensitivity: ['standard']));
    final body = transport.requests.last.body!;
    expect(body['id'], 'target_1');
    expect(body['modalities'], isA<List<Object?>>());
    expect(body['enabled'], true);
    final probed = await repo.probeTarget('target_1');
    expect(transport.requests.last.method, 'POST');
    expect(transport.requests.last.uri.path,
        '/api/v1/routing/targets/target_1/probe');
    expect(probed.probed, isTrue);
    expect(probed.measuredLatencyMs, 240);
    await repo.exportAudit('csv');
    expect(exporter.format, 'csv');
  });
}
