// The sign-in screens driven by GET /api/v1/auth/sign-in-options and the
// pending-account phase (docs/identity.md#google-and-apple-sign-in): only the
// offered providers, Google on iOS only together with Apple (Guideline 4.8),
// Create account only with a privacy policy, and an account without a tenant.
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/auth_screens.dart';
import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:anum_mobile/features/auth/sign_in_options.dart';
import 'package:anum_mobile/features/auth/welcome_screens.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

String _jwt(Map<String, Object?> claims) {
  String part(Object value) =>
      base64Url.encode(utf8.encode(jsonEncode(value))).replaceAll('=', '');
  return '${part({'alg': 'RS256'})}.${part(claims)}.signature';
}

class _Keycloak implements OidcAuthenticator {
  /// Claims of the next token; a test assigns a tenant by changing them.
  Map<String, Object?> claims = {
    'sub': 'google-user-1',
    'email': 'new.person@example.com',
  };
  final requests = <OidcSignInOptions>[];
  int refreshes = 0;
  int endSessions = 0;
  bool rejectRefresh = false;

  @override
  OidcConfig get config =>
      const OidcConfig(issuer: 'https://auth.example.com/realms/anum');

  OidcTokens _tokens() => OidcTokens(
      accessToken: _jwt(claims),
      refreshToken: 'refresh-example',
      idToken: 'id-example',
      expiresAt: DateTime.now().toUtc().add(const Duration(minutes: 5)));

  @override
  Future<OidcTokens> signIn(
      [OidcSignInOptions options = const OidcSignInOptions()]) async {
    requests.add(options);
    return _tokens();
  }

  @override
  Future<OidcTokens> refresh(String refreshToken) async {
    refreshes++;
    if (rejectRefresh) throw const OidcRefreshRejected();
    return _tokens();
  }

  @override
  Future<void> endSession({String? idToken}) async => endSessions++;
}

class _Api implements ApiTransport {
  _Api(this.options);
  Object options;
  final paths = <String>[];
  final authorizations = <String?>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    paths.add(request.uri.path);
    authorizations.add(request.headers['authorization']);
    if (request.uri.path == '/api/v1/auth/sign-in-options') {
      if (options == 'fail') {
        return const ApiResponse(statusCode: 503, body: {
          'error': {'code': 'service_unavailable', 'message': 'API restarting'}
        });
      }
      return ApiResponse(
          statusCode: 200, body: options as Map<String, Object?>);
    }
    if (request.uri.path == '/api/v1/onboarding') {
      return const ApiResponse(statusCode: 200, body: {
        'complete': true,
        'model_configured': true,
        'tenant': {'id': 'tenant_acme', 'name': 'Acme'},
        'workspace': {
          'id': 'workspace_sales',
          'tenant_id': 'tenant_acme',
          'name': 'Sales'
        },
        'membership': {
          'tenant_id': 'tenant_acme',
          'workspace_id': 'workspace_sales',
          'user_id': 'google-user-1',
          'role': 'member',
          'active': true,
        },
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

const _everything = {
  'identity_providers': [
    {'id': 'google', 'label': 'Google'},
    {'id': 'apple', 'label': 'Apple'},
  ],
  'self_sign_up': true,
  'privacy_policy_url': 'https://example.com/privacy',
};

const _assigned = {
  'sub': 'google-user-1',
  'tenant_id': 'tenant_acme',
  'workspace_id': 'workspace_sales',
};

class _Harness {
  _Harness([Object options = _everything]) : transport = _Api(options) {
    final api = AnumApiClient(
        baseUri: Uri.parse('https://api.example.com'),
        transport: transport,
        sessions: sessions);
    repository = AuthRepository(api: api, sessions: sessions, oidc: keycloak);
    controller = AuthController(repository)..phase = AuthPhase.signedOut;
  }

  final keycloak = _Keycloak();
  final sessions = MemorySessionStore();
  final _Api transport;
  late final AuthRepository repository;
  late final AuthController controller;
}

Future<void> _pump(WidgetTester tester, _Harness h,
    {double width = 390,
    double height = 844,
    double scale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, app) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(scale)),
        child: app!),
    home: AuthFlow(key: ObjectKey(h), controller: h.controller),
  ));
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  // The sign-in frame is a lazy ListView: scroll until the target is built.
  if (finder.evaluate().isEmpty) {
    await tester.scrollUntilVisible(finder, 200,
        scrollable: find.byType(Scrollable).first);
  }
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  group('sign-in options', () {
    test('parses known providers in order and drops the rest', () {
      final options = SignInOptions.fromJson({
        'identity_providers': [
          {'id': 'apple'},
          {'id': 'facebook'},
          {'id': 'google'},
        ],
        'self_sign_up': true,
        'privacy_policy_url': 'javascript:alert(1)',
      });
      expect(options.providers, ['google', 'apple']);
      expect(options.selfSignUp, isTrue);
      expect(options.privacyPolicyUrl, isNull);
      expect(options.canCreateAccount(isIOS: false), isFalse);
    });

    test('iOS never offers Google without Apple (Guideline 4.8)', () {
      final googleOnly = SignInOptions.fromJson({
        'identity_providers': [
          {'id': 'google'}
        ],
        'privacy_policy_url': 'https://example.com/privacy',
      });
      expect(googleOnly.visibleProviders(isIOS: false), ['google']);
      expect(googleOnly.visibleProviders(isIOS: true), isEmpty);
      final both = SignInOptions.fromJson(_everything);
      expect(both.visibleProviders(isIOS: true), ['google', 'apple']);
    });

    test('the options call carries no token', () async {
      final h = _Harness();
      await h.controller.loadSignInOptions();
      expect(h.transport.paths, ['/api/v1/auth/sign-in-options']);
      expect(h.transport.authorizations.single, isNull);
    });
  });

  group('login screen', () {
    testWidgets('offered providers get buttons; Google passes the hint',
        (tester) async {
      final h = _Harness();
      h.keycloak.claims = _assigned;
      await _pump(tester, h);
      expect(find.byKey(const Key('auth-google')), findsOneWidget);
      expect(find.byKey(const Key('auth-apple')), findsOneWidget);
      await _tap(tester, find.byKey(const Key('auth-google')));
      expect(h.keycloak.requests.single.identityProvider,
          OidcIdentityProvider.google);
      expect(h.controller.phase, AuthPhase.ready);
      expect((await h.sessions.read())!.context.tenantId, 'tenant_acme');
    });

    testWidgets('a provider the server does not offer has no button',
        (tester) async {
      final h = _Harness({
        'identity_providers': [
          {'id': 'apple'}
        ],
        'self_sign_up': false,
        'privacy_policy_url': null,
      });
      await _pump(tester, h);
      expect(find.byKey(const Key('auth-apple')), findsOneWidget);
      expect(find.byKey(const Key('auth-google')), findsNothing);
      // No privacy policy: no Create account.
      expect(find.byKey(const Key('auth-to-register')), findsNothing);
    });

    testWidgets('on iOS Google is hidden when Apple is not offered',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
      try {
        final h = _Harness({
          'identity_providers': [
            {'id': 'google'}
          ],
          'self_sign_up': true,
          'privacy_policy_url': 'https://example.com/privacy',
        });
        await _pump(tester, h);
        expect(find.byKey(const Key('auth-google')), findsNothing);
        expect(find.byKey(const Key('auth-primary')), findsOneWidget);
      } finally {
        debugDefaultTargetPlatformOverride = null;
      }
    });

    testWidgets('options that fail to load leave the email sign-in',
        (tester) async {
      final h = _Harness('fail');
      await _pump(tester, h);
      expect(find.textContaining('(API restarting)'), findsOneWidget);
      expect(find.byKey(const Key('auth-google')), findsNothing);
      expect(find.byKey(const Key('auth-primary')), findsOneWidget);
    });
  });

  group('create account', () {
    testWidgets('privacy policy from the server, accepted first',
        (tester) async {
      final h = _Harness();
      final opened = <Uri>[];
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
          theme: AnumTheme.dark(),
          localizationsDelegates: const [
            AnumLocalizations.delegate,
            ...GlobalMaterialLocalizations.delegates
          ],
          home: Scaffold(
              body: ListenableBuilder(
                  listenable: h.controller,
                  builder: (_, __) => OidcSignInView(
                      controller: h.controller,
                      startWithRegistration: true,
                      privacyPolicyUrl: '',
                      openUrl: (uri) async {
                        opened.add(uri);
                        return true;
                      })))));
      await tester.pumpAndSettle();
      await _tap(tester, find.byKey(const Key('auth-privacy-link')));
      expect(opened.single, Uri.parse('https://example.com/privacy'));
      await _tap(tester, find.byKey(const Key('auth-apple')));
      expect(h.keycloak.requests, isEmpty, reason: 'terms not accepted yet');
      await _tap(tester, find.byKey(const Key('auth-terms')));
      await _tap(tester, find.byKey(const Key('auth-apple')));
      expect(h.keycloak.requests.single.register, isTrue);
      expect(h.keycloak.requests.single.identityProvider,
          OidcIdentityProvider.apple);
    });

    testWidgets('without self sign-up only the providers create accounts',
        (tester) async {
      final h = _Harness({
        'identity_providers': [
          {'id': 'google'},
          {'id': 'apple'}
        ],
        'self_sign_up': false,
        'privacy_policy_url': 'https://example.com/privacy',
      });
      await _pump(tester, h);
      await _tap(tester, find.byKey(const Key('auth-to-register')));
      expect(find.byKey(const Key('auth-primary')), findsNothing);
      expect(find.byKey(const Key('auth-email')), findsNothing);
      expect(find.text('Create your account with Google or Apple.'),
          findsOneWidget);
    });
  });

  group('pending account', () {
    testWidgets('waits, checks again, then opens once a tenant is assigned',
        (tester) async {
      final h = _Harness();
      await _pump(tester, h);
      await _tap(tester, find.byKey(const Key('auth-apple')));
      expect(h.controller.phase, AuthPhase.accountPending);
      expect(find.text('Your account is waiting for access'), findsOneWidget);
      expect(find.text('new.person@example.com'), findsOneWidget);
      expect(find.text('google-user-1'), findsOneWidget);
      // Nothing is stored and nothing tenant-scoped is called.
      expect(await h.sessions.read(), isNull);
      expect(h.transport.paths, ['/api/v1/auth/sign-in-options']);

      await _tap(tester, find.byKey(const Key('pending-check')));
      expect(find.textContaining('Still waiting'), findsOneWidget);

      h.keycloak.claims = _assigned;
      await _tap(tester, find.byKey(const Key('pending-check')));
      expect(h.keycloak.refreshes, 2);
      expect(h.controller.phase, AuthPhase.ready);
      expect((await h.sessions.read())!.context.tenantId, 'tenant_acme');
    });

    testWidgets('sign out ends the Keycloak session and stores nothing',
        (tester) async {
      final h = _Harness();
      await _pump(tester, h);
      await _tap(tester, find.byKey(const Key('auth-google')));
      await _tap(tester, find.byKey(const Key('pending-sign-out')));
      expect(h.keycloak.endSessions, 1);
      expect(h.controller.phase, AuthPhase.signedOut);
      expect(await h.sessions.read(), isNull);
    });

    testWidgets('a rejected refresh returns to sign-in', (tester) async {
      final h = _Harness();
      await _pump(tester, h);
      await _tap(tester, find.byKey(const Key('auth-google')));
      h.keycloak.rejectRefresh = true;
      await _tap(tester, find.byKey(const Key('pending-check')));
      expect(h.controller.phase, AuthPhase.signedOut);
    });

    testWidgets('the pending screen is in Arabic too', (tester) async {
      final h = _Harness();
      await _pump(tester, h, locale: const Locale('ar'));
      await _tap(tester, find.byKey(const Key('auth-google')));
      expect(find.text('حسابك بانتظار منح الصلاحية'), findsOneWidget);
      expect(find.text('تحقق مجددًا'), findsOneWidget);
    });
  });

  group('layout', () {
    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final scale in [1.0, 2.0]) {
        testWidgets('pending and create account fit $width wide, text x$scale',
            (tester) async {
          final h = _Harness();
          // Tall enough to keep every control on screen at 200% text; the
          // check is that nothing overflows sideways.
          await _pump(tester, h, width: width, height: 1600, scale: scale);
          await _tap(tester, find.byKey(const Key('auth-to-register')));
          expect(tester.takeException(), isNull);
          final login = _Harness();
          await _pump(tester, login, width: width, height: 1600, scale: scale);
          await _tap(tester, find.byKey(const Key('auth-google')));
          expect(login.controller.phase, AuthPhase.accountPending);
          expect(tester.takeException(), isNull);
        });
      }
    }
  });
}
