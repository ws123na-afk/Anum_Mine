import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:flutter_test/flutter_test.dart';

String jwt(Map<String, Object?> claims) {
  String part(Object value) =>
      base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${part({'alg': 'RS256', 'typ': 'JWT'})}.${part(claims)}.signature';
}

final accessClaims = <String, Object?>{
  'sub': 'user-dev',
  'tenant_id': 'tenant_local',
  'workspace_id': 'workspace_foundation',
  'realm_access': {
    'roles': ['Owner', 'offline_access', 'uma_authorization']
  },
};

class FakeAuthenticator implements OidcAuthenticator {
  FakeAuthenticator({this.config = const OidcConfig(issuer: issuer)});

  static const issuer = 'http://localhost:8080/realms/anum';

  @override
  final OidcConfig config;
  int signIns = 0;
  final List<String> refreshes = [];
  final List<String?> endSessions = [];
  Object? signInError;
  Object? refreshError;
  int expiresInSeconds = 300;
  String? rotatedRefreshToken = 'refresh-2';

  @override
  Future<OidcTokens> signIn() async {
    signIns++;
    if (signInError != null) throw signInError!;
    return OidcTokens(
      accessToken: jwt(accessClaims),
      refreshToken: 'refresh-1',
      idToken: 'id-1',
      expiresAt:
          DateTime.now().toUtc().add(Duration(seconds: expiresInSeconds)),
    );
  }

  @override
  Future<OidcTokens> refresh(String refreshToken) async {
    refreshes.add(refreshToken);
    await Future<void>.delayed(Duration.zero);
    if (refreshError != null) throw refreshError!;
    return OidcTokens(
      accessToken: jwt({...accessClaims, 'workspace_id': 'workspace_other'}),
      refreshToken: rotatedRefreshToken,
      expiresAt: DateTime.now().toUtc().add(const Duration(minutes: 5)),
    );
  }

  @override
  Future<void> endSession({String? idToken}) async => endSessions.add(idToken);
}

class RecordingTransport implements ApiTransport {
  final List<ApiRequest> requests = [];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    if (request.uri.path.endsWith('/onboarding')) {
      return const ApiResponse(statusCode: 200, body: {
        'complete': true,
        'model_configured': true,
        'tenant': {'id': 'tenant_local', 'name': 'Local'},
        'workspace': {
          'id': 'workspace_foundation',
          'tenant_id': 'tenant_local',
          'name': 'Foundation',
        },
        'membership': {
          'tenant_id': 'tenant_local',
          'workspace_id': 'workspace_foundation',
          'user_id': 'user-dev',
          'role': 'owner',
          'active': true,
        },
      });
    }
    return const ApiResponse(statusCode: 200, body: {});
  }
}

LocalSession oidcSession({required DateTime expiresAt}) => LocalSession(
      accessToken: jwt(accessClaims),
      tokenType: 'Bearer',
      expiresAt: expiresAt,
      source: SessionSource.oidc,
      refreshToken: 'refresh-1',
      idToken: 'id-1',
      context: const TenantContext(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_selected',
          userId: 'user-dev',
          roles: ['owner']),
    );

void main() {
  group('token mapping', () {
    test('claims become the tenant context', () {
      final session = sessionFromOidcTokens(OidcTokens(
          accessToken: jwt(accessClaims),
          refreshToken: 'r',
          idToken: 'i',
          expiresAt: DateTime.utc(2030)));
      expect(session.isOidc, isTrue);
      expect(session.context.tenantId, 'tenant_local');
      expect(session.context.workspaceId, 'workspace_foundation');
      expect(session.context.userId, 'user-dev');
      expect(session.context.roles, ['owner']);
      expect(session.refreshToken, 'r');
      expect(
          LocalSession.fromJson(session.toJson()).toJson(), session.toJson());
    });

    test('a token without tenant_id is refused', () {
      expect(
          () => sessionFromOidcTokens(OidcTokens(
              accessToken: jwt({'sub': 'x'}), expiresAt: DateTime.utc(2030))),
          throwsA(isA<ApiException>()
              .having((e) => e.statusCode, 'statusCode', 403)));
    });

    test('the configured workspace fills in a missing claim', () {
      final session = sessionFromOidcTokens(
          OidcTokens(
              accessToken: jwt({'sub': 'x', 'tenant_id': 'tenant_local'}),
              expiresAt: DateTime.utc(2030)),
          defaultWorkspaceId: 'workspace_default');
      expect(session.context.workspaceId, 'workspace_default');
    });

    test('older stored sessions read as local sessions', () {
      final json = oidcSession(expiresAt: DateTime.utc(2030)).toJson()
        ..remove('source')
        ..remove('refresh_token')
        ..remove('id_token');
      expect(LocalSession.fromJson(json).isOidc, isFalse);
    });

    test('garbage tokens decode to no claims', () {
      expect(decodeJwtClaims('nope'), isEmpty);
      expect(decodeJwtClaims('a.###.c'), isEmpty);
    });
  });

  group('refreshing session store', () {
    test('refreshes near expiry, keeps the selected workspace and ID token',
        () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator();
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      await inner.write(oidcSession(
          expiresAt: DateTime.now().toUtc().add(const Duration(seconds: 30))));

      final results = await Future.wait([store.read(), store.read()]);
      expect(authenticator.refreshes, ['refresh-1'],
          reason: 'concurrent reads share one refresh');
      final session = results.first!;
      expect(session.refreshToken, 'refresh-2');
      expect(session.idToken, 'id-1');
      expect(session.context.workspaceId, 'workspace_selected');
      expect(session.isExpired, isFalse);
      expect((await inner.read())!.refreshToken, 'refresh-2');
    });

    test('a refresh without rotation keeps the current refresh token',
        () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator()..rotatedRefreshToken = null;
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      await inner.write(oidcSession(
          expiresAt:
              DateTime.now().toUtc().subtract(const Duration(hours: 1))));
      expect((await store.read())!.refreshToken, 'refresh-1');
    });

    test('a fresh token is returned without a refresh', () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator();
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      await inner.write(oidcSession(
          expiresAt: DateTime.now().toUtc().add(const Duration(minutes: 10))));
      await store.read();
      expect(authenticator.refreshes, isEmpty);
    });

    test('a rejected refresh token ends the session', () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator()
        ..refreshError = const OidcRefreshRejected();
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      await inner.write(oidcSession(
          expiresAt:
              DateTime.now().toUtc().subtract(const Duration(minutes: 1))));
      expect(await store.read(), isNull);
      expect(await inner.read(), isNull);
    });

    test('an offline refresh keeps the stored session for the next attempt',
        () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator()
        ..refreshError = Exception('network unreachable');
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      await inner.write(oidcSession(
          expiresAt:
              DateTime.now().toUtc().subtract(const Duration(minutes: 1))));
      await expectLater(store.read(), throwsException);
      expect((await inner.read())?.refreshToken, 'refresh-1');
    });

    test('local sessions pass through untouched', () async {
      final inner = MemorySessionStore();
      final authenticator = FakeAuthenticator();
      final store =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      final local = LocalSession(
          accessToken: 'anum_local_x',
          tokenType: 'bearer',
          expiresAt: DateTime.now().toUtc().add(const Duration(seconds: 5)),
          context: const TenantContext(
              tenantId: 't_1', workspaceId: 'w_1', userId: 'u_1', roles: []));
      await inner.write(local);
      expect((await store.read())!.accessToken, 'anum_local_x');
      expect(authenticator.refreshes, isEmpty);
    });
  });

  group('repository and controller', () {
    late MemorySessionStore inner;
    late FakeAuthenticator authenticator;
    late RecordingTransport transport;
    late AuthRepository repository;

    setUp(() {
      inner = MemorySessionStore();
      authenticator = FakeAuthenticator();
      transport = RecordingTransport();
      final sessions =
          OidcSessionStore(inner: inner, authenticator: authenticator);
      final api = AnumApiClient(
          baseUri: Uri.parse('http://127.0.0.1:8000'),
          transport: transport,
          sessions: sessions);
      repository =
          AuthRepository(api: api, sessions: sessions, oidc: authenticator);
    });

    test('sign-in stores the tokens and API calls carry bearer + workspace',
        () async {
      final session = await repository.signInWithOidc();
      expect((await inner.read())!.accessToken, session.accessToken);
      await repository.onboardingStatus();
      final headers = transport.requests.single.headers;
      expect(headers['authorization'], 'Bearer ${session.accessToken}');
      expect(headers['x-workspace-id'], 'workspace_foundation');
      expect(headers.containsKey('x-user-id'), isFalse);
    });

    test('switching workspace changes x-workspace-id without the local API',
        () async {
      await repository.signInWithOidc();
      await repository.switchWorkspace('workspace_two');
      expect(transport.requests, isEmpty);
      await repository.onboardingStatus();
      expect(
          transport.requests.single.headers['x-workspace-id'], 'workspace_two');
    });

    test('sign-out ends the provider session and forgets the tokens', () async {
      await repository.signInWithOidc();
      await repository.signOut();
      expect(authenticator.endSessions, ['id-1']);
      expect(await inner.read(), isNull);
      expect(
          transport.requests.where((r) => r.uri.path.contains('/auth/local/')),
          isEmpty);
    });

    test('sign-out still clears tokens when the provider logout fails',
        () async {
      final failing = _FailingLogout();
      final sessions = MemorySessionStore();
      final repo = AuthRepository(
          api: AnumApiClient(
              baseUri: Uri.parse('http://127.0.0.1:8000'),
              transport: transport,
              sessions: sessions),
          sessions: sessions,
          oidc: failing);
      await repo.signInWithOidc();
      await repo.signOut();
      expect(await sessions.read(), isNull);
    });

    test('controller signs in through OIDC and reaches the workspace',
        () async {
      final controller = AuthController(repository);
      expect(controller.oidcEnabled, isTrue);
      await controller.signInWithOidc();
      expect(controller.phase, AuthPhase.ready);
      expect(controller.workspaceName, 'Foundation');
    });

    test('cancelling the browser returns to sign-in without an error',
        () async {
      authenticator.signInError = const OidcCancelled();
      final controller = AuthController(repository);
      await controller.signInWithOidc();
      expect(controller.phase, AuthPhase.signedOut);
      expect(controller.message, isNull);
    });

    test('without an issuer the local sign-in stays in place', () {
      final sessions = MemorySessionStore();
      final local = AuthRepository(
          api: AnumApiClient(
              baseUri: Uri.parse('http://127.0.0.1:8000'),
              transport: transport,
              sessions: sessions),
          sessions: sessions);
      expect(AuthController(local).oidcEnabled, isFalse);
      expect(local.signInWithOidc, throwsStateError);
    });
  });
}

class _FailingLogout extends FakeAuthenticator {
  @override
  Future<void> endSession({String? idToken}) async =>
      throw Exception('provider unreachable');
}
