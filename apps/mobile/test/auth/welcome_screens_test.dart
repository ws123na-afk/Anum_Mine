// ANUM 360 splash, onboarding, Login and Create account (mockups 01-04). With
// Keycloak the app never collects a password: each button starts the OIDC
// code flow with hints (login_hint, kc_idp_hint, prompt=create).
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/auth_screens.dart';
import 'package:anum_mobile/features/auth/intro.dart';
import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:anum_mobile/features/auth/sign_in_options.dart';
import 'package:anum_mobile/features/auth/welcome_screens.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class _Transport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) async =>
      const ApiResponse(statusCode: 200, body: {'data': <Object?>[]});
}

/// Records the sign-in requests instead of opening a browser.
class _Controller extends AuthController {
  _Controller({this.oidc = true})
      : super(AuthRepository(
            api: AnumApiClient(
                baseUri: Uri.parse('https://anum.test'),
                transport: _Transport(),
                sessions: MemorySessionStore()),
            sessions: MemorySessionStore())) {
    phase = AuthPhase.signedOut;
    // What GET /api/v1/auth/sign-in-options reports; social_sign_in_test.dart
    // covers the screens with less offered.
    signInOptions = SignInOptions(
        providers: const ['google', 'apple'],
        selfSignUp: true,
        privacyPolicyUrl: Uri.parse('https://example.com/privacy'));
  }
  final bool oidc;
  final requests = <OidcSignInOptions>[];
  @override
  bool get oidcEnabled => oidc;
  @override
  Uri? get passwordResetUri =>
      const OidcConfig(issuer: 'https://id.example.com/realms/anum')
          .passwordResetUri;
  @override
  Future<void> signInWithOidc(
      [OidcSignInOptions options = const OidcSignInOptions()]) async {
    requests.add(options);
  }
}

Future<void> _pump(WidgetTester tester, Widget child,
    {double width = 390,
    double height = 844,
    Locale locale = const Locale('en'),
    double scale = 1}) async {
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
    home: child,
  ));
  await tester.pump();
}

void main() {
  testWidgets('Login: email hint, Google, Apple; no password field',
      (tester) async {
    final controller = _Controller();
    await _pump(tester, SignInScreen(controller: controller));
    expect(find.text('Welcome Back'), findsOneWidget);
    expect(find.byWidgetPredicate((w) => w is TextField && w.obscureText),
        findsNothing,
        reason: 'the password is typed on Keycloak, never in the app');

    await tester.enterText(find.byKey(const Key('auth-email')), 'not an email');
    await tester.tap(find.byKey(const Key('auth-primary')));
    await tester.pump();
    expect(find.textContaining('Enter an email address'), findsOneWidget);
    expect(controller.requests, isEmpty);

    await tester.enterText(
        find.byKey(const Key('auth-email')), ' person@example.com ');
    await tester.tap(find.byKey(const Key('auth-primary')));
    await tester.pump();
    expect(controller.requests.single.trimmedLoginHint, 'person@example.com');
    expect(controller.requests.single.register, isFalse);

    await tester.ensureVisible(find.byKey(const Key('auth-google')));
    await tester.tap(find.byKey(const Key('auth-google')));
    await tester.ensureVisible(find.byKey(const Key('auth-apple')));
    await tester.tap(find.byKey(const Key('auth-apple')));
    await tester.pump();
    expect(
        controller.requests[1].additionalParameters, {'kc_idp_hint': 'google'});
    expect(controller.requests[1].loginHint, isNull);
    expect(
        controller.requests[2].additionalParameters, {'kc_idp_hint': 'apple'});
  });

  testWidgets('Login: the Mobile tab sends the number as the login hint',
      (tester) async {
    final controller = _Controller();
    await _pump(tester, SignInScreen(controller: controller));
    await tester.tap(find.text('Mobile'));
    await tester.pump();
    await tester.enterText(
        find.byKey(const Key('auth-mobile')), '+966 50 000 0000');
    await tester.tap(find.byKey(const Key('auth-primary')));
    await tester.pump();
    expect(controller.requests.single.trimmedLoginHint, '+966500000000');
  });

  testWidgets('Forgot password opens Keycloak reset; a failure says so',
      (tester) async {
    final opened = <Uri>[];
    var succeed = true;
    await _pump(
        tester,
        OidcSignInView(
            controller: _Controller(),
            openUrl: (uri) async {
              opened.add(uri);
              return succeed;
            }));
    await tester.tap(find.byKey(const Key('auth-forgot')));
    await tester.pump();
    expect(opened.single.path, '/realms/anum/login-actions/reset-credentials');
    expect(opened.single.queryParameters['client_id'], 'anum-flutter');
    succeed = false;
    await tester.tap(find.byKey(const Key('auth-forgot')));
    await tester.pump();
    expect(find.textContaining("Couldn't open the password reset page"),
        findsOneWidget);
  });

  testWidgets('Create account needs the terms, then opens registration',
      (tester) async {
    final controller = _Controller();
    await _pump(tester, SignInScreen(controller: controller));
    await tester.ensureVisible(find.byKey(const Key('auth-to-register')));
    await tester.tap(find.byKey(const Key('auth-to-register')));
    await tester.pump();
    expect(find.text('Create Your Account'), findsOneWidget);
    // Keycloak's sign-up page lists the realm's password rules live.
    expect(find.text('At least 8 characters'), findsNothing);
    expect(find.textContaining('shows the password rules as you type'),
        findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('auth-email')), 'new@example.com');
    await tester.tap(find.byKey(const Key('auth-primary')));
    await tester.pump();
    expect(controller.requests, isEmpty);
    expect(find.text('Accept the terms to continue.'), findsOneWidget);

    await tester.tap(find.byKey(const Key('auth-terms')));
    await tester.tap(find.byKey(const Key('auth-primary')));
    await tester.pump();
    final sent = controller.requests.single;
    expect(sent.register, isTrue);
    expect(sent.promptValues, ['create']);
    expect(sent.trimmedLoginHint, 'new@example.com');
  });

  testWidgets('local development builds keep the local form, no social',
      (tester) async {
    await _pump(tester, SignInScreen(controller: _Controller(oidc: false)));
    expect(find.text('Organization ID'), findsOneWidget);
    expect(find.byKey(const Key('auth-google')), findsNothing);
    expect(find.byKey(const Key('auth-to-register')), findsNothing);
  });

  testWidgets('onboarding: skippable, Get Started opens Create account',
      (tester) async {
    final controller = _Controller();
    final intro = IntroController(store: MemoryIntroStore());
    await intro.restore();
    await _pump(tester, AuthFlow(controller: controller, intro: intro));
    expect(find.text('Welcome to ANUM 360'), findsOneWidget);
    for (var i = 0; i < 3; i++) {
      await tester.tap(find.byKey(const Key('onboarding-next')));
      await tester.pumpAndSettle();
    }
    expect(find.text('Secure and private'), findsOneWidget);
    await tester.tap(find.byKey(const Key('onboarding-start')));
    await tester.pumpAndSettle();
    expect(find.text('Create Your Account'), findsOneWidget);
    expect(intro.show, isFalse);

    final skipped = IntroController(store: MemoryIntroStore());
    await skipped.restore();
    await _pump(tester, AuthFlow(controller: _Controller(), intro: skipped));
    await tester.tap(find.byKey(const Key('onboarding-skip')));
    await tester.pumpAndSettle();
    expect(find.text('Welcome Back'), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('sign-in screens fit at $width px, 200% text and Arabic',
        (tester) async {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        await _pump(tester, SignInScreen(controller: _Controller()),
            width: width, height: 900, locale: locale, scale: 2);
        expect(tester.takeException(), isNull);
        await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
        await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
        final intro = IntroController(store: MemoryIntroStore());
        await intro.restore();
        await _pump(tester, AuthFlow(controller: _Controller(), intro: intro),
            width: width, height: 900, locale: locale, scale: 2);
        expect(tester.takeException(), isNull);
        await _pump(tester, const SplashScreen(),
            width: width, height: 900, locale: locale);
        expect(tester.takeException(), isNull);
      }
      expect(find.text('نظام تشغيلك بالذكاء الاصطناعي'), findsOneWidget);
    });
  }
}
