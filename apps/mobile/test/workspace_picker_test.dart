// Settings › Switch workspace (docs/identity.md, My workspaces): the picker
// lists the caller's memberships from GET /api/v1/me/workspace-memberships and
// switches only after the API accepts the membership in the chosen workspace.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/account_screens.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _Transport implements ApiTransport {
  _Transport({this.listFails = false});
  final bool listFails;
  final requests = <ApiRequest>[];

  /// Where the local session switch succeeds (the API checks the membership).
  final memberOf = {'workspace_test', 'workspace_finance'};

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path == '/api/v1/me/workspace-memberships') {
      if (listFails) return const ApiResponse(statusCode: 503, body: {});
      return const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'tenant_id': 'tenant_test',
            'workspace_id': 'workspace_finance',
            'workspace_name': 'Finance',
            'role': 'viewer',
            'status': 'active'
          },
          {
            'tenant_id': 'tenant_test',
            'workspace_id': 'workspace_legal',
            'workspace_name': 'Legal',
            'role': 'member',
            'status': 'active'
          },
          {
            'tenant_id': 'tenant_test',
            'workspace_id': 'workspace_test',
            'workspace_name': 'Operations',
            'role': 'owner',
            'status': 'active'
          },
        ]
      });
    }
    if (path == '/api/v1/auth/local/workspace/switch') {
      final target = (request.body?['workspace_id'] as String?) ?? '';
      if (!memberOf.contains(target)) {
        return const ApiResponse(statusCode: 403, body: {
          'detail': 'Active workspace membership required',
        });
      }
      return ApiResponse(statusCode: 200, body: {
        'access_token': 'anum_local_example',
        'token_type': 'bearer',
        'expires_at': DateTime.now()
            .toUtc()
            .add(const Duration(hours: 1))
            .toIso8601String(),
        'context': {
          'tenant_id': 'tenant_test',
          'workspace_id': target,
          'user_id': 'user_test',
          'roles': ['viewer'],
        },
      });
    }
    return const ApiResponse(statusCode: 404, body: {});
  }
}

Future<(AuthRepository, _Transport, MemorySessionStore)> _repository(
    {bool listFails = false}) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'anum_local_example',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_test',
          roles: ['owner'])));
  final transport = _Transport(listFails: listFails);
  final api = AnumApiClient(
      baseUri: Uri.parse('http://127.0.0.1:8000'),
      transport: transport,
      sessions: sessions);
  return (AuthRepository(api: api, sessions: sessions), transport, sessions);
}

Future<void> _open(WidgetTester tester, AuthRepository repository,
    {Future<void> Function()? onSwitched}) async {
  tester.view.physicalSize = const Size(1200, 3200);
  tester.view.devicePixelRatio = 2;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    home: Builder(
      builder: (context) => Scaffold(
        body: Center(
          child: FilledButton(
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => WorkspaceSwitcherScreen(
                    repository: repository,
                    currentWorkspaceId: 'workspace_test',
                    role: 'owner',
                    onSwitched: onSwitched),
              ),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    ),
  ));
  await tester.tap(find.text('Open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('lists every membership with name and role, current marked',
      (tester) async {
    final (repository, transport, _) = await _repository();
    await _open(tester, repository);

    expect(
        transport.requests.first.uri.path, '/api/v1/me/workspace-memberships');
    expect(find.text('Finance'), findsOneWidget);
    expect(find.text('workspace_finance · Your role: viewer'), findsOneWidget);
    expect(find.text('Legal'), findsOneWidget);
    expect(find.text('Operations'), findsOneWidget);
    expect(find.text('Current workspace · workspace_test · Your role: owner'),
        findsOneWidget);
    // The current workspace is in the list, so it is not shown twice.
    expect(find.text('Current workspace · owner'), findsNothing);
  });

  testWidgets('tapping a workspace switches after the API accepts it',
      (tester) async {
    final (repository, transport, sessions) = await _repository();
    var reloaded = 0;
    await _open(tester, repository, onSwitched: () async => reloaded++);

    await tester.tap(find.text('Finance'));
    await tester.pumpAndSettle();

    final switchCall = transport.requests.last;
    expect(switchCall.uri.path, '/api/v1/auth/local/workspace/switch');
    expect(switchCall.body, {'workspace_id': 'workspace_finance'});
    expect((await sessions.read())!.context.workspaceId, 'workspace_finance');
    expect(reloaded, 1);
    expect(find.byType(WorkspaceSwitcherScreen), findsNothing);
  });

  testWidgets('a refused switch keeps the current workspace and explains why',
      (tester) async {
    final (repository, _, sessions) = await _repository();
    await _open(tester, repository);

    await tester.tap(find.text('Legal'));
    await tester.pumpAndSettle();

    expect(
        find.textContaining(
            'You are not an active member of “workspace_legal”'),
        findsOneWidget);
    expect((await sessions.read())!.context.workspaceId, 'workspace_test');
    expect(find.byType(WorkspaceSwitcherScreen), findsOneWidget);
  });

  testWidgets('if the list cannot be loaded, a typed ID still works',
      (tester) async {
    final (repository, transport, sessions) =
        await _repository(listFails: true);
    await _open(tester, repository);

    expect(
        find.text('Your workspace list could not be loaded'), findsOneWidget);
    expect(find.text('Current workspace · owner'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'workspace_finance');
    await tester.tap(find.text('Switch workspace').last);
    await tester.pumpAndSettle();
    expect(transport.requests.last.body, {'workspace_id': 'workspace_finance'});
    expect((await sessions.read())!.context.workspaceId, 'workspace_finance');
  });
}
