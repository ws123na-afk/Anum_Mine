// Sign-in, first run, one-time code, password recovery and the workspace
// switcher speak the user's language (docs/mobile.md#first-run): every label
// comes from the catalog, Arabic pages run right to left, and nothing
// overflows on a phone or a tablet.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/account_screens.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/auth_screens.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/auth_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class _Transport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest request) async =>
      const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'tenant_id': 'tenant_a',
            'workspace_id': 'workspace_ops',
            'workspace_name': 'Ops',
            'role': 'owner',
            'status': 'active',
          },
        ],
      });
}

AuthRepository _repository([SessionStore? store]) {
  final sessions = store ?? MemorySessionStore();
  return AuthRepository(
      api: AnumApiClient(
          baseUri: Uri.parse('https://anum.test'),
          transport: _Transport(),
          sessions: sessions),
      sessions: sessions);
}

/// Signed in to workspace_ops, so the switcher can list memberships.
Future<AuthRepository> _signedIn() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_a',
          workspaceId: 'workspace_ops',
          userId: 'user_a',
          roles: ['owner'])));
  return _repository(sessions);
}

OnboardingStatus _onboarding(String role) => OnboardingStatus(
    complete: true,
    modelConfigured: false,
    workspace: const WorkspaceSummary(
        id: 'workspace_ops', tenantId: 'tenant_a', name: 'Ops'),
    membership: MembershipSummary(
        tenantId: 'tenant_a',
        workspaceId: 'workspace_ops',
        userId: 'user_a',
        role: role,
        active: true));

Future<void> _pump(WidgetTester tester, Widget home,
    {required Size size, Locale locale = const Locale('ar')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(disableAnimations: true),
        child: child!),
    home: home,
  ));
  await tester.pumpAndSettle();
}

void _expectRtl(WidgetTester tester, Finder inside) {
  expect(Directionality.of(tester.element(inside)), TextDirection.rtl);
}

String _ar(String key) => authAr[key]!;

void main() {
  test('every English auth string has an Arabic one, and back', () {
    expect(authAr.keys.toSet(), authEn.keys.toSet());
    for (final entry in authAr.entries) {
      expect(entry.value.trim(), isNotEmpty, reason: entry.key);
      // Placeholders survive translation.
      final names = RegExp(r'\{(\w+)\}');
      expect(names.allMatches(entry.value).map((m) => m[1]).toSet(),
          names.allMatches(authEn[entry.key]!).map((m) => m[1]).toSet(),
          reason: entry.key);
    }
  });

  for (final size in const [Size(390, 844), Size(768, 1024)]) {
    final at = '${size.width.toInt()}x${size.height.toInt()}';

    testWidgets('local sign-in in Arabic at $at', (tester) async {
      final controller = AuthController(_repository());
      await _pump(tester, SignInScreen(controller: controller), size: size);
      expect(find.text(_ar('auth.signIn.title')), findsOneWidget);
      expect(find.text(_ar('auth.signIn.organizationId')), findsOneWidget);
      expect(find.text(_ar('auth.signIn.otp')), findsOneWidget);
      expect(find.text('Sign in to ANUM'), findsNothing);
      _expectRtl(tester, find.text(_ar('auth.signIn.title')));
      expect(tester.takeException(), isNull);

      // Validation messages are Arabic too.
      await tester.enterText(find.byType(TextFormField).first, 'x');
      await tester.tap(find.text(_ar('auth.continue')));
      await tester.pumpAndSettle();
      expect(find.text(_ar('auth.signIn.minLength')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a second person is asked for an invitation in Arabic at $at',
        (tester) async {
      final controller = AuthController(_repository())
        ..onboarding = const OnboardingStatus(
            complete: true,
            modelConfigured: false,
            workspace: WorkspaceSummary(
                id: 'workspace_ops', tenantId: 'tenant_a', name: 'Ops'));
      await _pump(tester, NoWorkspaceScreen(controller: controller),
          size: size);
      expect(find.text(_ar('auth.none.title')), findsOneWidget);
      expect(
          find.text(AnumLocalizations(const Locale('ar'))
              .f('auth.none.askExisting', {'name': 'Ops'})),
          findsOneWidget);
      expect(find.text(_ar('auth.checkAgain')), findsOneWidget);
      _expectRtl(tester, find.text(_ar('auth.none.title')));
      expect(tester.takeException(), isNull);
    });

    testWidgets('workspace setup in Arabic at $at', (tester) async {
      final controller = AuthController(_repository());
      await _pump(tester, WorkspaceSetupScreen(controller: controller),
          size: size);
      expect(find.text(_ar('auth.setup.title')), findsOneWidget);
      await tester.tap(find.text(_ar('auth.setup.create')));
      await tester.pumpAndSettle();
      expect(find.text(_ar('auth.required')), findsNWidgets(2));
      expect(tester.takeException(), isNull);
    });

    testWidgets('the model step without any model, owner, in Arabic at $at',
        (tester) async {
      final controller = AuthController(_repository())
        ..onboarding = _onboarding('owner')
        ..modelAvailability =
            const ModelAvailability(workspaceConfigured: false);
      await _pump(tester, ModelConnectionScreen(controller: controller),
          size: size);
      expect(find.text(_ar('auth.model.title')), findsOneWidget);
      expect(find.text(_ar('auth.model.save')), findsOneWidget);
      await tester.scrollUntilVisible(
          find.text(_ar('auth.model.continueWithout')), 200,
          scrollable: find
              .descendant(
                  of: find.byType(ListView), matching: find.byType(Scrollable))
              .first);
      expect(find.text(_ar('auth.model.noneOwner')), findsOneWidget);
      expect(find.text('Continue without a model'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('the model step for a member, check failed, in Arabic at $at',
        (tester) async {
      final controller = AuthController(_repository())
        ..onboarding = _onboarding('member')
        ..modelCheckError = 'timeout';
      await _pump(tester, ModelConnectionScreen(controller: controller),
          size: size);
      expect(find.text(_ar('auth.model.memberSubtitle')), findsOneWidget);
      expect(
          find.text(AnumLocalizations(const Locale('ar'))
              .f('auth.model.checkFailed', {'reason': 'timeout'})),
          findsOneWidget);
      expect(find.text(_ar('auth.model.continueWithout')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('one-time code and recovery in Arabic at $at', (tester) async {
      final controller = AuthController(_repository());
      await _pump(
          tester,
          OtpVerificationScreen(
              controller: controller, challengeId: 'challenge_a'),
          size: size);
      expect(find.text(_ar('auth.otp.title')), findsOneWidget);
      expect(find.text(_ar('auth.otp.verify')), findsOneWidget);
      _expectRtl(tester, find.text(_ar('auth.otp.title')));
      expect(tester.takeException(), isNull);

      await _pump(
          tester,
          PasswordRecoveryScreen(
              controller: controller,
              tenantId: 'tenant_a',
              workspaceId: 'workspace_ops',
              userId: 'user_a'),
          size: size);
      expect(find.text(_ar('auth.recover.title')), findsOneWidget);
      expect(find.text(_ar('auth.recover.enterpriseText')), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('the workspace switcher in Arabic at $at', (tester) async {
      final repository = await _signedIn();
      await _pump(
          tester,
          WorkspaceSwitcherScreen(
              repository: repository,
              currentWorkspaceId: 'workspace_ops',
              role: 'owner'),
          size: size);
      expect(find.text(_ar('auth.switch.title')), findsWidgets);
      // The role is shown in Arabic, the workspace id as it is.
      expect(find.text('Ops'), findsOneWidget);
      expect(
          find.text('${_ar('auth.switch.current')} · workspace_ops · '
              'دورك: مالك'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('a fixed controller message is translated on the failure page',
      (tester) async {
    final controller = AuthController(_repository());
    await controller.signIn('x', 'workspace_ops', 'user_a');
    expect(controller.phase, AuthPhase.error);
    await _pump(tester, AuthFlow(controller: controller),
        size: const Size(390, 844));
    expect(find.text(_ar('auth.failure.validation')), findsOneWidget);
    expect(find.text(_ar('auth.msg.invalidIds')), findsOneWidget);
    expect(find.text(_ar('auth.tryAgain')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('English keeps its wording and the role word', (tester) async {
    final repository = await _signedIn();
    await _pump(
        tester,
        WorkspaceSwitcherScreen(
            repository: repository,
            currentWorkspaceId: 'workspace_ops',
            role: 'owner'),
        size: const Size(390, 844),
        locale: const Locale('en'));
    expect(find.text('Current workspace · workspace_ops · Your role: owner'),
        findsOneWidget);
    expect(find.text('Not listed?'), findsOneWidget);
  });
}
