// A session that ends on its own moves the app to sign-in
// (docs/mobile.md#session-end).
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:flutter_test/flutter_test.dart';

import 'oidc_session_test.dart'
    show FakeAuthenticator, RecordingTransport, oidcSession;

LocalSession localSession(DateTime expiresAt) => LocalSession(
      accessToken: 'anum_local_example',
      tokenType: 'bearer',
      expiresAt: expiresAt,
      context: const TenantContext(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_foundation',
          userId: 'user_owner',
          roles: ['owner']),
    );

DateTime inFuture([Duration by = const Duration(hours: 1)]) =>
    DateTime.now().toUtc().add(by);
DateTime inPast() =>
    DateTime.now().toUtc().subtract(const Duration(minutes: 1));

class _Silent implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) =>
      Completer<ApiResponse>().future;
}

void main() {
  group('WatchedSessionStore', () {
    test('reports a working session that is gone, once', () async {
      var ended = 0;
      final inner = MemorySessionStore();
      final store = WatchedSessionStore(inner: inner, onEnded: () => ended++);
      await store.write(localSession(inFuture()));
      expect(await store.read(), isNotNull);
      expect(ended, 0);
      await inner.clear();
      expect(await store.read(), isNull);
      expect(await store.read(), isNull);
      expect(ended, 1);
    });

    test('reports a session that expired while in use', () async {
      var ended = 0;
      final inner = MemorySessionStore();
      final store = WatchedSessionStore(inner: inner, onEnded: () => ended++);
      await inner.write(localSession(inFuture()));
      await store.read();
      await inner.write(localSession(inPast()));
      expect((await store.read())?.isExpired, isTrue);
      expect(ended, 1);
    });

    test('a cold start without a session and a sign-out are not reported',
        () async {
      var ended = 0;
      final inner = MemorySessionStore();
      final store = WatchedSessionStore(inner: inner, onEnded: () => ended++);
      expect(await store.read(), isNull);
      await inner.write(localSession(inPast()));
      await store.read();
      await store.write(localSession(inFuture()));
      await store.clear();
      expect(await store.read(), isNull);
      expect(ended, 0);
    });
  });

  test('sign-out does not wait for a server that never answers', () async {
    final sessions = MemorySessionStore();
    await sessions.write(localSession(inFuture()));
    final controller = AuthController(AuthRepository(
        api: AnumApiClient(
            baseUri: Uri.parse('http://127.0.0.1:8000'),
            transport: _Silent(),
            sessions: sessions),
        sessions: sessions,
        signOutTimeout: const Duration(milliseconds: 30)));
    await controller.signOut().timeout(const Duration(seconds: 5));
    expect(controller.phase, AuthPhase.signedOut);
    expect(await sessions.read(), isNull);
  });

  group('AuthController', () {
    late RecordingTransport transport;
    late FakeAuthenticator authenticator;
    late MemorySessionStore inner;
    late AuthController controller;
    late AnumApiClient api;

    setUp(() {
      transport = RecordingTransport();
      authenticator = FakeAuthenticator();
      inner = MemorySessionStore();
      late final AuthController built;
      final sessions = WatchedSessionStore(
        inner: OidcSessionStore(inner: inner, authenticator: authenticator),
        onEnded: () => built.sessionExpired(),
      );
      api = AnumApiClient(
          baseUri: Uri.parse('http://127.0.0.1:8000'),
          transport: transport,
          sessions: sessions);
      controller = built = AuthController(
          AuthRepository(api: api, sessions: sessions, oidc: authenticator));
    });

    test('a refresh the provider rejects mid-use signs the app out', () async {
      await controller.signInWithOidc();
      expect(controller.phase, AuthPhase.ready);
      var notified = 0;
      controller.addListener(() => notified++);

      // The access token ran out and Keycloak refuses the refresh token.
      await inner.write(oidcSession(expiresAt: inPast()));
      authenticator.refreshError = const OidcRefreshRejected();
      await expectLater(
          api.request('GET', '/api/v1/tasks'),
          throwsA(
              isA<ApiException>().having((e) => e.statusCode, 'status', 401)));

      expect(controller.phase, AuthPhase.signedOut);
      expect(controller.sessionEnded, isTrue);
      expect(controller.message, 'Your session ended. Sign in again.');
      expect(controller.workspaceName, isNull);
      expect(notified, 1);
      expect(await inner.read(), isNull);
    });

    test('an offline refresh keeps the person signed in', () async {
      await controller.signInWithOidc();
      await inner.write(oidcSession(expiresAt: inPast()));
      authenticator.refreshError = Exception('network unreachable');
      await expectLater(api.request('GET', '/api/v1/tasks'), throwsException);
      expect(controller.phase, AuthPhase.ready);
      expect(controller.sessionEnded, isFalse);
    });

    test('signing in again clears the notice', () async {
      await controller.signInWithOidc();
      await inner.write(oidcSession(expiresAt: inPast()));
      authenticator.refreshError = const OidcRefreshRejected();
      await expectLater(
          api.request('GET', '/api/v1/tasks'), throwsA(isA<ApiException>()));
      authenticator.refreshError = null;
      await controller.signInWithOidc();
      expect(controller.phase, AuthPhase.ready);
      expect(controller.sessionEnded, isFalse);
    });

    test('a deliberate sign-out shows no "session ended" notice', () async {
      await controller.signInWithOidc();
      await controller.signOut();
      expect(controller.phase, AuthPhase.signedOut);
      expect(controller.sessionEnded, isFalse);
      expect(controller.message, isNull);
    });

    test('Settings signs out through the app, not the repository alone',
        () async {
      await controller.signInWithOidc();
      final settings = SettingsController(controller.repository)
        ..onSignOut = controller.signOut;
      await settings.signOut();
      expect(controller.phase, AuthPhase.signedOut);
      expect(await inner.read(), isNull);
    });
  });
}
