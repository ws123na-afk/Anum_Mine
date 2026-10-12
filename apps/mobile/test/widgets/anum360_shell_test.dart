// ANUM 360 design system: tokens, the mark, the orb, gradient buttons, the
// bottom navigation with the orb, the Menu drawer and the greeting header.
import 'dart:convert';

import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/auth/intro.dart';
import 'package:anum_mobile/features/settings/account_identity.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/theme/theme_controller.dart';
import 'package:anum_mobile/src/widgets/brand.dart';
import 'package:anum_mobile/src/widgets/greeting_header.dart';
import 'package:anum_mobile/src/widgets/navigation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

double _contrast(Color a, Color b) {
  final la = a.computeLuminance(), lb = b.computeLuminance();
  final hi = la > lb ? la : lb, lo = la > lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

Future<void> _pump(WidgetTester tester, Widget child,
    {double width = 390,
    double height = 844,
    Locale locale = const Locale('en'),
    double scale = 1,
    ThemeData? theme}) async {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: theme ?? AnumTheme.dark(),
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

class _Shell extends StatefulWidget {
  const _Shell();
  @override
  State<_Shell> createState() => _ShellState();
}

class _ShellState extends State<_Shell> {
  final key = GlobalKey<ScaffoldState>();
  int? selected = 0;
  int orbs = 0;
  @override
  Widget build(BuildContext context) => Scaffold(
        key: key,
        drawer: AnumMenuDrawer(
          semanticLabel: 'Main menu',
          sections: [
            AnumMenuSection(entries: [
              AnumMenuEntry(
                  key: const Key('entry-home'),
                  icon: Icons.home_outlined,
                  label: 'Home',
                  selected: true,
                  onTap: () => key.currentState!.closeDrawer()),
              AnumMenuEntry(
                  icon: Icons.approval_outlined,
                  label: 'Approvals',
                  badge: 3,
                  onTap: () {}),
            ]),
          ],
          footer: const AnumPlanCard(title: 'Free plan', subtitle: 'Ops'),
        ),
        body: ListView(padding: const EdgeInsets.all(16), children: [
          AnumGreetingHeader(
            name: 'Shahzeb Rahman',
            notificationCount: 3,
            onNotifications: () {},
            onProfile: () {},
            now: DateTime(2026, 10, 7, 9),
          ),
          const SizedBox(height: 16),
          AnumGradientButton(label: 'Sign In', onPressed: () {}),
          const SizedBox(height: 16),
          const AnumGradientButton(label: 'Disabled', onPressed: null),
        ]),
        bottomNavigationBar: AnumBottomNav(
          selected: selected,
          orbLabel: 'ANUM Command',
          onOrb: () => setState(() => orbs++),
          onSelected: (i) => i == 3
              ? key.currentState!.openDrawer()
              : setState(() => selected = i),
          items: const [
            AnumNavItem(
                key: Key('nav-home'),
                icon: Icons.home_outlined,
                selectedIcon: Icons.home,
                label: 'Home'),
            AnumNavItem(
                key: Key('nav-chat'),
                icon: Icons.chat_bubble_outline,
                selectedIcon: Icons.chat_bubble,
                label: 'Chat'),
            AnumNavItem(
                key: Key('nav-tasks'),
                icon: Icons.task_alt_outlined,
                selectedIcon: Icons.task_alt,
                label: 'Tasks'),
            AnumNavItem(
                key: Key('nav-menu'),
                icon: Icons.menu,
                selectedIcon: Icons.menu_open,
                label: 'Menu',
                badge: 3),
          ],
        ),
      );
}

void main() {
  test('accent gradient keeps white text at AA contrast in both palettes', () {
    for (final p in [AnumPalette.dark, AnumPalette.light]) {
      expect(_contrast(p.onAccent, p.accentStart), greaterThanOrEqualTo(4.5));
      expect(_contrast(p.onAccent, p.accentEnd), greaterThanOrEqualTo(4.5));
      expect(_contrast(p.text, p.background), greaterThanOrEqualTo(7));
      expect(
          _contrast(p.text, p.glass.withAlpha(255)), greaterThanOrEqualTo(7));
    }
    expect(AnumPalette.dark.background.computeLuminance(), lessThan(0.01),
        reason: 'midnight navy');
    final scheme = AnumTheme.light().extension<AnumPalette>()!;
    expect(scheme, AnumPalette.light);
  });

  test('the orb waveform is symmetric, tallest in the middle, never empty', () {
    for (final phase in [0.0, 0.3, 0.77]) {
      final bars = AnumOrbPainter.bars(phase, 0.6);
      expect(bars, hasLength(15));
      expect(bars.every((h) => h >= 0.08 && h <= 1), isTrue);
      expect(bars[7], greaterThan(bars.first));
    }
    final quiet = AnumOrbPainter.bars(0.2, 0);
    final loud = AnumOrbPainter.bars(0.2, 1);
    expect(loud[7], greaterThan(quiet[7]));
  });

  test('initials and greeting keys', () {
    expect(AnumAvatar.initials('Shahzeb Rahman'), 'SR');
    expect(AnumAvatar.initials('owner'), 'O');
    expect(AnumAvatar.initials('name.surname@example.com'), 'NS');
    expect(AnumAvatar.initials('  '), '?');
    expect(greetingKey(DateTime(2026, 1, 1, 8)), 'shellGreetingMorning');
    expect(greetingKey(DateTime(2026, 1, 1, 13)), 'shellGreetingAfternoon');
    expect(greetingKey(DateTime(2026, 1, 1, 21)), 'shellGreetingEvening');
  });

  test('account identity reads the ID token name, else the user id', () {
    String jwt(Map<String, Object?> claims) =>
        'h.${base64Url.encode(utf8.encode(jsonEncode(claims))).replaceAll('=', '')}.s';
    LocalSession session({String? idToken}) => LocalSession(
        accessToken: jwt({'sub': 'user_1'}),
        tokenType: 'Bearer',
        expiresAt: DateTime.utc(2030),
        idToken: idToken,
        context: const TenantContext(
            tenantId: 'tenant',
            workspaceId: 'ops',
            userId: 'user_1',
            roles: ['member', 'owner']));
    final named = AccountIdentity.fromSession(session(
        idToken:
            jwt({'name': 'Example Person', 'email': 'person@example.com'})));
    expect(named.displayName, 'Example Person');
    expect(named.email, 'person@example.com');
    expect(named.role, 'owner');
    final given = AccountIdentity.fromSession(
        session(idToken: jwt({'given_name': 'Example', 'family_name': 'Two'})));
    expect(given.displayName, 'Example Two');
    final bare = AccountIdentity.fromSession(session());
    expect(bare.displayName, 'user_1');
    expect(bare.email, isNull);
  });

  test('theme choice persists; dark is the default', () async {
    final store = MemoryThemeStore();
    final theme = ThemeController(store: store);
    expect(theme.mode, ThemeMode.dark);
    await theme.select(ThemeMode.light);
    expect(store.value, 'light');
    final restored = ThemeController(store: store);
    await restored.restore();
    expect(restored.mode, ThemeMode.light);
    expect(ThemeController.parse('nonsense'), isNull);
  });

  test('onboarding shows once and can ask for registration', () async {
    final store = MemoryIntroStore();
    final intro = IntroController(store: store);
    expect(intro.show, isFalse, reason: 'hidden until restored');
    await intro.restore();
    expect(intro.show, isTrue);
    await intro.finish(register: true);
    expect(intro.show, isFalse);
    expect(store.value, isTrue);
    expect(intro.takeRegistration(), isTrue);
    expect(intro.takeRegistration(), isFalse);
    final again = IntroController(store: store);
    await again.restore();
    expect(again.show, isFalse);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('shell parts fit at $width px', (tester) async {
      await _pump(tester, const _Shell(), width: width);
      expect(tester.takeException(), isNull);
      expect(find.text('Chat'), findsOneWidget);
      expect(find.byType(AnumOrb), findsOneWidget);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  }

  testWidgets('200% text: labels hide instead of clipping, nothing overflows',
      (tester) async {
    await _pump(tester, const _Shell(), width: 320, scale: 2);
    expect(tester.takeException(), isNull);
    expect(find.bySemanticsLabel('Chat'), findsOneWidget);
    await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
  });

  testWidgets('Arabic RTL: Home sits on the right, orb in the middle',
      (tester) async {
    await _pump(tester, const _Shell(), locale: const Locale('ar'));
    expect(tester.takeException(), isNull);
    final home = tester.getCenter(find.byKey(const Key('nav-home')));
    final menu = tester.getCenter(find.byKey(const Key('nav-menu')));
    final orb = tester.getCenter(find.byKey(const Key('nav-orb')));
    expect(home.dx, greaterThan(orb.dx));
    expect(menu.dx, lessThan(orb.dx));
    expect(find.text('صباح الخير'), findsOneWidget);
  });

  testWidgets('selection, orb and Menu drawer work; state is not colour only',
      (tester) async {
    await _pump(tester, const _Shell(), theme: AnumTheme.light());
    final handle = tester.ensureSemantics();
    expect(
        tester.getSemantics(find.byKey(const Key('nav-home'))),
        matchesSemantics(
            isButton: true,
            isSelected: true,
            hasSelectedState: true,
            hasTapAction: true,
            label: 'Home'));
    await tester.tap(find.byKey(const Key('nav-tasks')));
    await tester.pump();
    expect(
        tester.getSemantics(find.byKey(const Key('nav-tasks'))),
        matchesSemantics(
            isButton: true,
            isSelected: true,
            hasSelectedState: true,
            hasTapAction: true,
            label: 'Tasks'));
    await tester.tap(find.byTooltip('ANUM Command'));
    await tester.pump();
    expect(tester.state<_ShellState>(find.byType(_Shell)).orbs, 1);

    await tester.tap(find.byKey(const Key('nav-menu')));
    await tester.pumpAndSettle();
    expect(find.byType(AnumMenuDrawer), findsOneWidget);
    expect(find.text('Free plan'), findsOneWidget);
    expect(find.text('3'), findsWidgets);
    await tester.tap(find.byKey(const Key('entry-home')));
    await tester.pumpAndSettle();
    expect(find.byType(AnumMenuDrawer), findsNothing);
    handle.dispose();
  });

  testWidgets('the orb animates only when active and motion is allowed',
      (tester) async {
    tester.view.physicalSize = const Size(400, 400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    Widget app(bool reduce, bool active) => MediaQuery(
        data: MediaQueryData(disableAnimations: reduce),
        child: Directionality(
            textDirection: TextDirection.ltr,
            child: Theme(
                data: AnumTheme.dark(),
                child: Center(child: AnumOrb(active: active)))));
    await tester.pumpWidget(app(false, true));
    expect(tester.hasRunningAnimations, isTrue);
    await tester.pumpWidget(app(true, true));
    await tester.pump();
    expect(tester.hasRunningAnimations, isFalse);
    await tester.pumpWidget(app(false, false));
    await tester.pump();
    expect(tester.hasRunningAnimations, isFalse);
    expect(tester.takeException(), isNull);
  });

  testWidgets('gradient button: disabled and busy states', (tester) async {
    var taps = 0;
    await _pump(
        tester,
        Scaffold(
            body: Column(children: [
          AnumGradientButton(label: 'Go', onPressed: () => taps++),
          AnumGradientButton(
              label: 'Busy', busy: true, onPressed: () => taps++),
        ])));
    await tester.tap(find.text('Go'));
    await tester.tap(find.text('Busy'));
    expect(taps, 1);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}
