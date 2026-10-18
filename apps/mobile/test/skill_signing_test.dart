// Skill signing on Resources > Skills, read only (docs/skills.md#signing): the signer and
// verification status from the API, an install refused for an unsigned version, and an
// installed version flagged after its key was revoked.
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

const _time = '2026-10-18T10:00:00Z';

Map<String, Object?> _version(
        String skillId, String name, Map<String, Object?> verification) =>
    {
      'id': 'sv_$skillId',
      'skill_id': skillId,
      'version': '1.0.0',
      'name': name,
      'description': '$name for the team',
      'instructions': 'Do it well.',
      'required_tools': const <String>[],
      'risk_level': 'low',
      'created_by': 'admin',
      'created_at': _time,
      'verification': verification,
    };

Map<String, Object?> _check(String status,
        {bool installable = false,
        String detail = 'Not signed.',
        String? signer,
        String? keySource}) =>
    {
      'status': status,
      'installable': installable,
      'digest': 'ab' * 32,
      'key_id':
          signer == null || keySource == 'first_party' ? null : 'acme-2026',
      'signer': signer,
      'key_source': keySource,
      'fingerprint':
          signer == null || keySource == 'first_party' ? null : 'abcd:ef01',
      'signed_at': null,
      'detail': detail,
    };

class _Transport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final lists = <String, List<Object?>>{
      '/api/v1/skills/versions': [
        _version(
            'acme.notes',
            'Notes',
            _check('verified',
                installable: true, signer: 'Acme Labs', keySource: 'tenant')),
        _version('acme.draft', 'Draft helper',
            _check('unsigned', detail: 'This version is not signed.')),
        _version(
            'acme.legacy',
            'Legacy',
            _check('revoked',
                signer: 'Acme Labs',
                detail:
                    'Signed with key acme-2026, which an organization administrator revoked: Laptop lost.')),
        _version(
            'anum.brief',
            'Daily brief',
            _check('verified',
                installable: true,
                signer: 'ANUM (shipped with this release)',
                keySource: 'first_party')),
      ],
      '/api/v1/skills/installations': [
        {
          'id': 'si_1',
          'skill_id': 'acme.legacy',
          'version': '1.0.0',
          'approved_tools': const <String>[],
          'enabled': false,
          'disabled_reason':
              'Turned off automatically: the signing key acme-2026 was revoked (Laptop lost).',
          'installed_by': 'owner',
          'installed_at': _time,
        }
      ],
    };
    return ApiResponse(
        statusCode: 200,
        body: {'data': lists[request.uri.path] ?? const <Object?>[]});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

Future<WorkspaceRepository> _repository() async {
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
  return ApiWorkspaceRepository(
      AnumApiClient(
          baseUri: Uri.parse('http://localhost:8000'),
          transport: _Transport(),
          sessions: sessions),
      fileTransfer: _NoFiles());
}

void main() {
  test('the repository reads each version\'s signature check', () async {
    final snapshot = await (await _repository()).loadWorkspace();
    final byId = {for (final s in snapshot.skills) s.skillId: s};
    expect(byId['acme.notes']!.signature!.label, 'Signed by Acme Labs');
    expect(byId['acme.notes']!.installable, isTrue);
    expect(byId['acme.draft']!.signature!.label, 'Unsigned');
    expect(byId['acme.draft']!.installable, isFalse);
    expect(byId['acme.legacy']!.signature!.label, 'Key revoked');
    expect(byId['anum.brief']!.signature!.label, 'First-party');
  });

  testWidgets(
      'skills show the signer, block an unsigned install and flag a revoked one',
      (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final controller = WorkspaceController(await _repository());
    await tester.runAsync(controller.load);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.light(),
      home: Scaffold(
          body: ResourcesScreen(
              controller: controller, initialTab: ResourcesTab.skills)),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Signed by Acme Labs'), findsOneWidget);
    expect(find.text('Key acme-2026, fingerprint abcd:ef01'), findsOneWidget);
    expect(find.text('First-party'), findsOneWidget);
    expect(find.text('Unsigned'), findsOneWidget);
    expect(find.text('This version is not signed.'), findsOneWidget);
    expect(find.text('Key revoked'), findsOneWidget);
    expect(
        find.text(
            'Flagged: Signed with key acme-2026, which an organization administrator revoked: Laptop lost.'),
        findsOneWidget);
    // Turned off automatically when the key was revoked: the reason is shown.
    expect(find.text('Disabled'), findsOneWidget);
    expect(
        find.text(
            'Turned off automatically: the signing key acme-2026 was revoked (Laptop lost).'),
        findsOneWidget);

    // Every not-installed tile has an Install button; only the unsigned one is disabled.
    final installs = tester
        .widgetList<FilledButton>(find.widgetWithText(FilledButton, 'Install'))
        .toList();
    expect(installs.length, 3);
    expect(installs.where((b) => b.onPressed == null).length, 1);
    expect(tester.takeException(), isNull);
  });
}
