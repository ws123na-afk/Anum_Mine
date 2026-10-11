// First run after sign-in (docs/mobile.md#first-run): choosing a workspace when
// the token names none, and offering "skip" on the model step only when a
// model already answers.
import 'dart:async';
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/auth_screens.dart';
import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:anum_mobile/features/auth/workspace_selection.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

String _jwt(Map<String, Object?> claims) {
  String part(Object value) =>
      base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${part({'alg': 'RS256', 'typ': 'JWT'})}.${part(claims)}.signature';
}

/// Keycloak without the `default_workspace_id` attribute: no workspace claim.
class _Authenticator implements OidcAuthenticator {
  _Authenticator({this.workspaceClaim});
  final String? workspaceClaim;

  @override
  OidcConfig get config =>
      const OidcConfig(issuer: 'http://localhost:8080/realms/anum');

  @override
  Future<OidcTokens> signIn() async => OidcTokens(
        accessToken: _jwt({
          'sub': 'user_first',
          'tenant_id': 'tenant_first',
          if (workspaceClaim != null) 'workspace_id': workspaceClaim,
        }),
        refreshToken: 'refresh-example',
        idToken: 'id-example',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      );

  @override
  Future<OidcTokens> refresh(String refreshToken) => signIn();

  @override
  Future<void> endSession({String? idToken}) async {}
}

Map<String, Object?> _membershipRow(String id, String? name, String role) => {
      'tenant_id': 'tenant_first',
      'workspace_id': id,
      'workspace_name': name,
      'role': role,
      'status': 'active',
    };

class _Api implements ApiTransport {
  final requests = <ApiRequest>[];

  /// Rows of `GET /api/v1/me/workspace-memberships`.
  List<Map<String, Object?>> directory = [];
  bool directoryFails = false;

  /// Where `/workspace-memberships/current` reports an active membership.
  Set<String> memberOf = {'workspace_ops', 'workspace_finance'};

  bool complete = true;
  bool modelConfigured = false;
  String role = 'owner';

  /// The server's `configured-model` routing target.
  String serverProvider = 'mock';
  String serverModel = 'anum-mock-planner';
  int routingFailures = 0;
  Completer<void>? routingGate;

  Iterable<String> get paths => requests.map((r) => r.uri.path);

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    final workspace = request.headers['x-workspace-id'];
    if (path == '/api/v1/me/workspace-memberships') {
      if (directoryFails) {
        return const ApiResponse(
            statusCode: 503, body: {'detail': 'Directory unavailable'});
      }
      return ApiResponse(statusCode: 200, body: {'data': directory});
    }
    if (path == '/api/v1/workspace-memberships/current') {
      if (!memberOf.contains(workspace)) {
        return const ApiResponse(
            statusCode: 403,
            body: {'detail': 'Active workspace membership required'});
      }
      return ApiResponse(statusCode: 200, body: {
        'tenant_id': 'tenant_first',
        'workspace_id': workspace,
        'user_id': 'user_first',
        'role': role,
        'active': true,
      });
    }
    if (workspace == null || workspace.isEmpty) {
      return const ApiResponse(statusCode: 400, body: {
        'detail': 'Select a workspace with the x-workspace-id header'
      });
    }
    if (path == '/api/v1/onboarding') {
      if (request.method == 'PUT') complete = true;
      return ApiResponse(statusCode: 200, body: _status(workspace));
    }
    if (path == '/api/v1/model-config' && request.method == 'PUT') {
      final body = request.body!;
      modelConfigured = true;
      return ApiResponse(statusCode: 200, body: {
        ...body,
        'base_url': body['base_url'] ?? 'https://api.anthropic.com',
        'credential_configured': body['api_key'] != null,
        'credential_hint': '...mple',
        'updated_at': '2026-10-11T10:00:00Z',
      }..remove('api_key'));
    }
    if (path == '/api/v1/model-config/test') {
      return const ApiResponse(statusCode: 200, body: {
        'provider': 'anthropic',
        'model': 'claude-sonnet-5-5',
        'models': ['claude-sonnet-5-5', 'claude-haiku-5-5'],
        'latency_ms': 12,
        'status': 'connected',
      });
    }
    if (path == '/api/v1/routing/targets') {
      await routingGate?.future;
      if (routingFailures > 0) {
        routingFailures--;
        return const ApiResponse(
            statusCode: 503, body: {'detail': 'Service unavailable'});
      }
      return ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'id': 'configured-model',
            'region': 'local',
            'provider': serverProvider,
            'model': serverModel,
            'status': serverProvider == 'mock' ? 'healthy' : 'unverified',
            'cost_per_1k_tokens': 0.0,
            'latency_ms': 1,
          },
        ]
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }

  Map<String, Object?> _status(String workspace) => {
        'complete': complete,
        'model_configured': modelConfigured,
        if (complete) ...{
          'tenant': {'id': 'tenant_first', 'name': 'First'},
          'workspace': {
            'id': workspace,
            'tenant_id': 'tenant_first',
            'name': 'Workspace $workspace',
          },
          'membership': {
            'tenant_id': 'tenant_first',
            'workspace_id': workspace,
            'user_id': 'user_first',
            'role': role,
            'active': true,
          },
        },
      };
}

class _Harness {
  _Harness({String? workspaceClaim, WorkspacePreferenceStore? preferences})
      : preferences = preferences ?? MemoryWorkspacePreferenceStore() {
    final api = AnumApiClient(
        baseUri: Uri.parse('http://127.0.0.1:8000'),
        transport: transport,
        sessions: sessions);
    repository = AuthRepository(
        api: api,
        sessions: sessions,
        oidc: _Authenticator(workspaceClaim: workspaceClaim),
        workspacePreferences: this.preferences);
    controller = AuthController(repository);
  }

  final transport = _Api();
  final sessions = MemorySessionStore();
  final WorkspacePreferenceStore preferences;
  late final AuthRepository repository;
  late final AuthController controller;

  Future<String?> remembered() =>
      preferences.read(tenantId: 'tenant_first', userId: 'user_first');

  Future<String?> sessionWorkspace() async =>
      (await sessions.read())?.context.workspaceId;
}

Future<void> _pumpFlow(WidgetTester tester, AuthController controller) async {
  tester.view.physicalSize = const Size(1170, 2532);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(), home: AuthFlow(controller: controller)));
  await tester.pumpAndSettle();
}

void main() {
  group('workspace selection without a workspace claim', () {
    test('a token that names a workspace never asks the directory', () async {
      final h = _Harness(workspaceClaim: 'workspace_ops');
      await h.controller.signInWithOidc();
      expect(h.transport.paths,
          isNot(contains(endsWith('/me/workspace-memberships'))));
      expect(h.controller.phase, AuthPhase.modelSetup);
    });

    test('exactly one membership is selected without asking', () async {
      final h = _Harness();
      h.transport.directory = [_membershipRow('workspace_ops', 'Ops', 'owner')];
      h.transport.modelConfigured = true;
      await h.controller.signInWithOidc();

      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.sessionWorkspace(), 'workspace_ops');
      expect(h.controller.workspaceName, 'Workspace workspace_ops');
      // The directory was read without a workspace header.
      final directoryCall = h.transport.requests
          .firstWhere((r) => r.uri.path.endsWith('/me/workspace-memberships'));
      expect(directoryCall.headers.containsKey('x-workspace-id'), isFalse);
      expect(
          h.transport.requests.last.headers['x-workspace-id'], 'workspace_ops');
    });

    test('several memberships show the picker; the choice is remembered',
        () async {
      final h = _Harness();
      h.transport.directory = [
        _membershipRow('workspace_finance', 'Finance', 'viewer'),
        _membershipRow('workspace_ops', 'Ops', 'owner'),
      ];
      h.transport.modelConfigured = true;
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.workspacePicker);
      expect(h.controller.workspaceChoices.map((m) => m.workspaceId),
          ['workspace_finance', 'workspace_ops']);
      expect(await h.sessionWorkspace(), isEmpty);

      await h.controller.chooseWorkspace('workspace_finance');
      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.sessionWorkspace(), 'workspace_finance');
      expect(await h.remembered(), 'workspace_finance');

      // Signing out and in again opens the remembered workspace directly.
      await h.controller.signOut();
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.sessionWorkspace(), 'workspace_finance');
    });

    test('a remembered workspace that is no longer listed asks again',
        () async {
      final h = _Harness();
      await h.preferences.write(
          tenantId: 'tenant_first',
          userId: 'user_first',
          workspaceId: 'workspace_gone');
      h.transport.directory = [
        _membershipRow('workspace_finance', 'Finance', 'viewer'),
        _membershipRow('workspace_ops', 'Ops', 'owner'),
      ];
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.workspacePicker);
    });

    test('a refused choice explains it and returns to the picker', () async {
      final h = _Harness();
      h.transport.directory = [
        _membershipRow('workspace_finance', 'Finance', 'viewer'),
        _membershipRow('workspace_ops', 'Ops', 'owner'),
      ];
      await h.controller.signInWithOidc();
      // Deactivated after the list was loaded.
      h.transport.memberOf = {'workspace_ops'};
      await h.controller.chooseWorkspace('workspace_finance');
      expect(h.controller.phase, AuthPhase.error);
      expect(h.controller.issue, AuthIssue.permission);
      expect(await h.remembered(), isNull);
      h.controller.retry();
      expect(h.controller.phase, AuthPhase.workspacePicker);
      expect(h.controller.workspaceChoices, hasLength(2));
    });

    test('no membership explains it; checking again continues once added',
        () async {
      final h = _Harness();
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.noWorkspace);

      h.transport.directory = [_membershipRow('workspace_ops', 'Ops', 'owner')];
      h.transport.modelConfigured = true;
      await h.controller.checkWorkspacesAgain();
      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.sessionWorkspace(), 'workspace_ops');
    });

    test('a directory failure is an error with a retry, not a dead end',
        () async {
      final h = _Harness()..transport.directoryFails = true;
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.error);
      expect(h.controller.message, 'Directory unavailable');
      h.controller.retry();
      expect(h.controller.phase, AuthPhase.signedOut);
    });

    test('a restored session without a workspace resolves it too', () async {
      final h = _Harness();
      h.transport.directory = [_membershipRow('workspace_ops', 'Ops', 'owner')];
      h.transport.modelConfigured = true;
      await h.repository.signInWithOidc();
      expect(await h.sessionWorkspace(), isEmpty);
      await h.controller.restore();
      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.sessionWorkspace(), 'workspace_ops');
    });

    testWidgets('the picker lists names and roles and opens the choice',
        (tester) async {
      final h = _Harness();
      h.transport.directory = [
        _membershipRow('workspace_finance', 'Finance', 'viewer'),
        _membershipRow('workspace_ops', null, 'owner'),
      ];
      h.transport.modelConfigured = true;
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      expect(find.text('Where do you want to work?'), findsOneWidget);
      expect(find.text('Finance'), findsOneWidget);
      expect(
          find.text('workspace_finance · Your role: viewer'), findsOneWidget);
      expect(find.text('workspace_ops'), findsOneWidget);
      expect(find.text('Your role: owner'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('choose-workspace_finance')));
      await tester.pumpAndSettle();
      expect(h.controller.phase, AuthPhase.ready);
      expect(await h.remembered(), 'workspace_finance');
    });

    testWidgets('no workspace says why and what to do', (tester) async {
      final h = _Harness();
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      expect(find.text('You are not in a workspace'), findsOneWidget);
      expect(find.textContaining('Ask a workspace owner to invite you'),
          findsOneWidget);
      expect(find.text('Check again'), findsOneWidget);
      expect(find.text('Sign in with another account'), findsOneWidget);
    });

    test('the secure store keeps one choice per tenant and user', () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      FlutterSecureStorage.setMockInitialValues({});
      final store = SecureWorkspacePreferenceStore();
      await store.write(
          tenantId: 'tenant_first', userId: 'user_first', workspaceId: 'w_one');
      await store.write(
          tenantId: 'tenant_first', userId: 'user_other', workspaceId: 'w_two');
      expect(await store.read(tenantId: 'tenant_first', userId: 'user_first'),
          'w_one');
      expect(await store.read(tenantId: 'tenant_first', userId: 'user_other'),
          'w_two');
      expect(
          await store.read(tenantId: 'tenant_x', userId: 'user_first'), isNull);
    });
  });

  group('model step skip', () {
    Future<_Harness> atModelStep({String role = 'owner'}) async {
      final h = _Harness(workspaceClaim: 'workspace_ops');
      h.transport.role = role;
      return h;
    }

    test('a server that runs only the mock planner offers no skip', () async {
      final h = await atModelStep();
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.modelSetup);
      await pumpEventQueue();
      expect(h.controller.checkingModel, isFalse);
      expect(h.controller.modelAvailability?.canSkip, isFalse);
      h.controller.useServerModel();
      expect(h.controller.phase, AuthPhase.modelSetup);
    });

    test('a server model can be skipped to', () async {
      final h = await atModelStep();
      h.transport
        ..serverProvider = 'openai_compatible'
        ..serverModel = 'gpt-example';
      await h.controller.signInWithOidc();
      await pumpEventQueue();
      expect(h.controller.modelAvailability?.serverModel, 'gpt-example');
      h.controller.useServerModel();
      expect(h.controller.phase, AuthPhase.ready);
    });

    test('a new workspace that already has a model skips the step', () async {
      final h = _Harness(workspaceClaim: 'workspace_ops');
      h.transport.complete = false;
      await h.controller.signInWithOidc();
      expect(h.controller.phase, AuthPhase.onboarding);
      h.transport.modelConfigured = true;
      await h.controller.createWorkspace('First', 'Ops');
      expect(h.controller.phase, AuthPhase.ready);
    });

    testWidgets('owner without any model: no skip button, the reason instead',
        (tester) async {
      final h = await atModelStep();
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      expect(find.text('Test, save, and continue'), findsOneWidget);
      expect(find.textContaining('Skip and use'), findsNothing);
      expect(find.textContaining('Skipping is not available'), findsOneWidget);
    });

    testWidgets('owner with a server model sees the skip button',
        (tester) async {
      final h = await atModelStep();
      h.transport
        ..serverProvider = 'openai_compatible'
        ..serverModel = 'gpt-example';
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      final skip = find.text("Skip and use the server's model (gpt-example)");
      await tester.ensureVisible(skip);
      await tester.tap(skip);
      await tester.pumpAndSettle();
      expect(h.controller.phase, AuthPhase.ready);
    });

    testWidgets('the check shows progress, then a failure with a retry',
        (tester) async {
      final h = await atModelStep();
      h.transport
        ..routingGate = Completer<void>()
        ..routingFailures = 1
        ..serverProvider = 'ollama'
        ..serverModel = 'llama3.2';
      await h.controller.signInWithOidc();
      tester.view.physicalSize = const Size(1170, 2532);
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
          theme: AnumTheme.dark(), home: AuthFlow(controller: h.controller)));
      await tester.pump();
      expect(find.text('Checking whether the server already has a model...'),
          findsOneWidget);
      expect(find.textContaining('Skip and use'), findsNothing);

      h.transport.routingGate!.complete();
      await tester.pumpAndSettle();
      expect(
          find.textContaining(
              'Could not check whether the server already has a model: '
              'Service unavailable'),
          findsOneWidget);

      await tester.ensureVisible(find.text('Check again'));
      await tester.tap(find.text('Check again'));
      await tester.pumpAndSettle();
      expect(find.text("Skip and use the server's model (llama3.2)"),
          findsOneWidget);
    });

    testWidgets('a member cannot connect a model and is told who can',
        (tester) async {
      final h = await atModelStep(role: 'member');
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      expect(find.text('Test, save, and continue'), findsNothing);
      expect(find.textContaining('Ask a workspace owner to connect one'),
          findsOneWidget);
      expect(find.textContaining('Skip and use'), findsNothing);

      // An owner connects one meanwhile: checking again offers to continue.
      h.transport.modelConfigured = true;
      await tester.tap(find.text('Check again'));
      await tester.pumpAndSettle();
      await tester.tap(find.text("Continue with this workspace's model"));
      await tester.pumpAndSettle();
      expect(h.controller.phase, AuthPhase.ready);
    });

    testWidgets('an owner connects Claude with Haiku and Sonnet by default',
        (tester) async {
      final h = await atModelStep();
      await h.controller.signInWithOidc();
      await _pumpFlow(tester, h.controller);

      await tester.tap(find.byType(DropdownButtonFormField<String>).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Anthropic (Claude)').last);
      await tester.pumpAndSettle();

      expect(find.text('Anthropic API key'), findsOneWidget);
      expect(find.text('Base URL (optional)'), findsOneWidget);
      Finder field(String text) => find.byWidgetPredicate(
          (w) => w is EditableText && w.controller.text == text);
      expect(field('claude-haiku-5-5'), findsOneWidget);
      expect(field('claude-sonnet-5-5'), findsOneWidget);
      expect(find.text('Low (quickest)'), findsOneWidget);

      // The key is required for Claude.
      final save = find.text('Test, save, and continue');
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(find.text('This field is required'), findsOneWidget);
      expect(h.transport.paths, isNot(contains('/api/v1/model-config')));

      await tester.enterText(
          find.widgetWithText(TextFormField, 'Anthropic API key'),
          'sk-ant-example');
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();

      final put = h.transport.requests.firstWhere(
          (r) => r.uri.path == '/api/v1/model-config' && r.method == 'PUT');
      expect(put.body, {
        'provider': 'anthropic',
        'model': 'claude-sonnet-5-5',
        'base_url': 'https://api.anthropic.com',
        'api_key': 'sk-ant-example',
        'fast_model': 'claude-haiku-5-5',
        'capable_model': 'claude-sonnet-5-5',
        'fast_effort': 'low',
        'capable_effort': null,
      });
      expect(h.transport.paths, contains('/api/v1/model-config/test'));
      expect(h.controller.phase, AuthPhase.ready);
    });
  });

  group('layout', () {
    for (final (size, scale) in <(Size, double)>[
      (const Size(360, 640), 1),
      (const Size(360, 800), 2),
      (const Size(768, 1024), 1),
      (const Size(1024, 768), 1.3),
    ]) {
      testWidgets(
          'first-run screens fit ${size.width.toInt()}x'
          '${size.height.toInt()} at ${scale}x text', (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        Future<void> show(AuthController controller) async {
          await tester.pumpWidget(MaterialApp(
              theme: AnumTheme.light(),
              builder: (context, content) => MediaQuery(
                  data: MediaQuery.of(context)
                      .copyWith(textScaler: TextScaler.linear(scale)),
                  child: content!),
              home: AuthFlow(controller: controller)));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        }

        final picker = _Harness();
        picker.transport.directory = [
          _membershipRow(
              'workspace_finance', 'Finance and accounting', 'viewer'),
          _membershipRow('workspace_ops', null, 'owner'),
        ];
        await picker.controller.signInWithOidc();
        await show(picker.controller);
        expect(find.text('Where do you want to work?'), findsOneWidget);

        final none = _Harness();
        await none.controller.signInWithOidc();
        await show(none.controller);
        expect(find.text('You are not in a workspace'), findsOneWidget);

        final member = _Harness(workspaceClaim: 'workspace_ops');
        member.transport.role = 'viewer';
        await member.controller.signInWithOidc();
        await show(member.controller);
        expect(find.text('Check again'), findsOneWidget);
      });
    }
  });
}
