// Skills and services on Resources (docs/skills.md, docs/tools-and-integrations.md,
// docs/mobile.md): enable, disable, uninstall with confirmation, a refusal that says who
// can act, and an integration's last error with how to fix it.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/resources_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _skill = WorkspaceSkill(
    id: 'sv_1',
    skillId: 'acme.notes',
    name: 'Release notes',
    version: '1.0.0',
    description: 'Drafts release notes',
    risk: 'low',
    tools: ['anum.respond'],
    installed: true,
    installedVersion: '1.0.0',
    enabled: true);

class _Repository implements WorkspaceRepository {
  _Repository({this.refuse = false});
  final bool refuse;
  final calls = <String>[];
  bool enabled = true;
  bool installed = true;

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
        tasks: const [],
        approvals: const [],
        automations: const [],
        files: const [],
        memories: const [],
        skills: [
          if (installed)
            WorkspaceSkill(
                id: _skill.id,
                skillId: _skill.skillId,
                name: _skill.name,
                version: _skill.version,
                description: _skill.description,
                risk: _skill.risk,
                tools: _skill.tools,
                installed: true,
                installedVersion: '1.0.0',
                enabled: enabled)
          else
            const WorkspaceSkill(
                id: 'sv_1',
                skillId: 'acme.notes',
                name: 'Release notes',
                version: '1.0.0',
                description: 'Drafts release notes',
                risk: 'low',
                tools: ['anum.respond'],
                installed: false),
        ],
        integrations: [
          WorkspaceIntegration(
              id: 'valkey',
              name: 'Valkey',
              kind: 'cache',
              status: 'degraded',
              endpoint: 'redis://valkey:6379/0',
              detail: 'Health check failed: ConnectionRefusedError',
              remediation: 'Check that Valkey at ANUM_VALKEY_URL is running.',
              lastError: 'Health check failed: TimeoutError',
              lastErrorAt: DateTime.utc(2026, 10, 1)),
        ],
      );

  void _guard() {
    if (refuse) {
      throw const ApiException(403, 'Workspace permission denied',
          code: 'forbidden');
    }
  }

  @override
  Future<void> setSkillEnabled(String skillId, bool value) async {
    _guard();
    calls.add('enable:$skillId:$value');
    enabled = value;
  }

  @override
  Future<void> uninstallSkill(String skillId) async {
    _guard();
    calls.add('uninstall:$skillId');
    installed = false;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

Future<_Repository> _pump(WidgetTester tester, int tab,
    {bool refuse = false, double width = 390, double textScale = 1}) async {
  tester.view.physicalSize = Size(width, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final repository = _Repository(refuse: refuse);
  final controller = WorkspaceController(repository);
  await controller.load();
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.light(),
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: Scaffold(
        body: ResourcesScreen(controller: controller, initialTab: tab)),
  ));
  await tester.pumpAndSettle();
  return repository;
}

void main() {
  testWidgets('an owner disables, enables and uninstalls a skill',
      (tester) async {
    final repository = await _pump(tester, ResourcesTab.skills);
    expect(find.text('Installed'), findsOneWidget);
    await tester.tap(find.text('Disable'));
    await tester.pumpAndSettle();
    expect(find.text('Disabled'), findsOneWidget);
    await tester.tap(find.text('Enable'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Uninstall'));
    await tester.pumpAndSettle();
    expect(find.text('Uninstall Release notes?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Uninstall'));
    await tester.pumpAndSettle();
    expect(repository.calls, [
      'enable:acme.notes:false',
      'enable:acme.notes:true',
      'uninstall:acme.notes'
    ]);
    expect(find.widgetWithText(FilledButton, 'Install'), findsOneWidget);
  });

  testWidgets('a refused change says who can make it, the screen stays usable',
      (tester) async {
    await _pump(tester, ResourcesTab.skills, refuse: true);
    await tester.tap(find.text('Disable'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Only workspace owners can install'),
        findsOneWidget);
    expect(find.text('Installed'), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('skills fit at $width with 200% text', (tester) async {
      await _pump(tester, ResourcesTab.skills, width: width, textScale: 2);
      expect(find.text('Release notes'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('services show the last error and how to fix it at $width',
        (tester) async {
      await _pump(tester, ResourcesTab.services, width: width, textScale: 2);
      expect(
          find.textContaining('How to fix: Check that Valkey'), findsOneWidget);
      expect(
          find.textContaining('Last error: Health check failed: TimeoutError'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  test('the API repository maps installs and sends skill changes', () async {
    final requests = <ApiRequest>[];
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession(
        accessToken: 'token',
        tokenType: 'bearer',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
        context: const TenantContext(
            tenantId: 't', workspaceId: 'w', userId: 'u', roles: ['owner'])));
    final repository = ApiWorkspaceRepository(
        AnumApiClient(
            baseUri: Uri.parse('http://localhost:8000'),
            transport: _Transport(requests),
            sessions: sessions),
        fileTransfer: _NoFiles());
    final snapshot = await repository.loadWorkspace();
    final skills = {for (final s in snapshot.skills) s.version: s};
    expect(skills['2.0.0']!.installed, isFalse);
    expect(skills['2.0.0']!.installedVersion, '1.0.0');
    expect(skills['1.0.0']!.installed, isTrue);
    expect(skills['1.0.0']!.enabled, isFalse);
    expect(snapshot.integrations.single.remediation, 'Check it.');
    expect(snapshot.integrations.single.lastErrorAt, isNotNull);
    await repository.setSkillEnabled('acme.notes', true);
    expect(requests.last.method, 'PATCH');
    expect(requests.last.uri.path, '/api/v1/skills/installations/acme.notes');
    expect(requests.last.body, {'enabled': true});
    await repository.uninstallSkill('acme.notes');
    expect(requests.last.method, 'DELETE');
  });
}

class _Transport implements ApiTransport {
  _Transport(this.requests);
  final List<ApiRequest> requests;
  static const _time = '2026-10-01T10:00:00Z';
  Map<String, Object?> _version(String v) => {
        'id': 'sv_$v',
        'skill_id': 'acme.notes',
        'version': v,
        'name': 'Notes',
        'description': 'Writes',
        'required_tools': <String>[],
        'risk_level': 'low',
        'created_at': _time
      };

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final lists = <String, List<Object?>>{
      '/api/v1/skills/versions': [_version('1.0.0'), _version('2.0.0')],
      '/api/v1/skills/installations': [
        {
          'id': 'si',
          'skill_id': 'acme.notes',
          'version': '1.0.0',
          'approved_tools': <String>[],
          'enabled': false,
          'installed_at': _time
        }
      ],
      '/api/v1/integrations': [
        {
          'id': 'valkey',
          'name': 'Valkey',
          'kind': 'cache',
          'status': 'connected',
          'endpoint': 'redis://valkey',
          'detail': 'Health check passed.',
          'latency_ms': 2,
          'remediation': 'Check it.',
          'last_error': 'Health check failed: X',
          'last_error_at': _time
        }
      ],
    };
    if (request.method != 'GET') {
      return const ApiResponse(statusCode: 200, body: <String, Object?>{});
    }
    return ApiResponse(
        statusCode: 200, body: {'data': lists[request.uri.path] ?? const []});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}
