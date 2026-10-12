// Profile and settings (ANUM 360 mockup 13): the profile card, the plan
// (free, nothing to buy: there is no billing API), and each row opening the
// real place in Settings; language and theme apply at once.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/profile_screen.dart';
import 'package:anum_mobile/features/settings/settings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/theme/theme_controller.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class _Transport implements ApiTransport {
  _Transport({this.status = 200});
  final int status;
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    final path = request.uri.path;
    if (status != 200) {
      return ApiResponse(statusCode: status, body: {'detail': 'Not allowed'});
    }
    if (path.endsWith('/notification-preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'task_completed': true,
        'approval_required': true,
        'run_failed': true,
        'automation_failed': true,
        'email_enabled': false,
        'desktop_enabled': true,
      });
    }
    if (path.endsWith('/me/preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'remember_conversations': true,
        'updated_at': null,
        'conversation_memory': 'on'
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<SettingsController> _settings({int status = 200}) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_test',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'example_owner',
      'roles': ['owner'],
    },
  }));
  final api = AnumApiClient(
      baseUri: Uri.parse('http://127.0.0.1:8000'),
      transport: _Transport(status: status),
      sessions: sessions);
  return SettingsController(AuthRepository(api: api, sessions: sessions));
}

Future<void> _pump(WidgetTester tester, Widget child, LocaleController locale,
    {double width = 390, double scale = 1}) async {
  tester.view.physicalSize = Size(width, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(ListenableBuilder(
    listenable: locale,
    builder: (context, _) => MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale.locale ?? const Locale('en'),
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
    ),
  ));
  for (var i = 0; i < 5; i++) {
    await tester.runAsync(() => Future<void>.delayed(Duration.zero));
    await tester.pump(const Duration(milliseconds: 50));
  }
}

/// Locale changes without touching shared preferences.
class _Locale extends LocaleController {
  @override
  Future<void> select(String code) async {
    locale = Locale(code);
    notifyListeners();
  }
}

void main() {
  testWidgets('profile card, free plan and every row', (tester) async {
    final settings = await _settings();
    final theme = ThemeController(store: MemoryThemeStore());
    final locale = _Locale();
    await _pump(
        tester,
        ProfileScreen(
            settings: settings,
            locale: locale,
            theme: theme,
            workspaceName: 'Operations'),
        locale);
    expect(find.text('example_owner'), findsWidgets);
    expect(find.text('Owner'), findsOneWidget);
    expect(find.text('Free plan'), findsWidgets);
    for (final row in [
      'My Profile',
      'Subscription',
      'Security',
      'Notifications',
      'Language',
      'Theme',
      'Data & Privacy',
      'Help & Support',
      'About ANUM 360',
    ]) {
      expect(find.text(row), findsOneWidget, reason: row);
    }
    await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
    await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));

    // Subscription: truthful, nothing to buy.
    await tester.tap(find.text('Subscription'));
    await tester.pumpAndSettle();
    expect(find.textContaining('nothing to buy or pay'), findsOneWidget);
    expect(find.byType(FilledButton), findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();

    // Theme applies at once and is kept.
    await tester.tap(find.text('Theme'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Light').last);
    await tester.pumpAndSettle();
    expect(theme.mode, ThemeMode.light);

    // Security opens Settings at its section.
    await tester.ensureVisible(find.text('Security'));
    await tester.tap(find.text('Security'));
    await tester.pumpAndSettle();
    expect(find.text('Sign out'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();

    // Language switches the app to Arabic.
    await tester.ensureVisible(find.text('Language'));
    await tester.tap(find.text('Language'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Arabic'));
    await tester.pumpAndSettle();
    expect(find.text('الملف الشخصي والإعدادات'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a refusal explains who can grant access', (tester) async {
    final settings = await _settings(status: 403);
    await _pump(
        tester,
        ProfileScreen(
            settings: settings,
            locale: _Locale(),
            theme: ThemeController(store: MemoryThemeStore())),
        _Locale());
    expect(find.textContaining('A workspace owner can grant access'),
        findsOneWidget);
  });

  for (final width in [320.0, 768.0, 1440.0]) {
    testWidgets('fits at $width px with 200% text', (tester) async {
      final settings = await _settings();
      await _pump(
          tester,
          ProfileScreen(
              settings: settings,
              locale: _Locale(),
              theme: ThemeController(store: MemoryThemeStore())),
          _Locale(),
          width: width,
          scale: 2);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Help lists real actions only', (tester) async {
    var asked = 0;
    await _pump(
        tester, HelpScreen(onAskAnum: () => asked++, webAppUrl: ''), _Locale());
    expect(find.text('Open ANUM on the web'), findsNothing);
    await tester.tap(find.text('Ask ANUM'));
    await tester.pumpAndSettle();
    expect(asked, 1);
  });
}
