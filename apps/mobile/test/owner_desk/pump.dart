// Shared harness of the owner desk widget tests: a screen in a MaterialApp
// with the app's theme, both languages and a chosen size and text scale.
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

/// The sizes every owner desk screen is checked at: 320 to 1024 px wide,
/// text at 200 percent, both languages.
const deskSizes = [
  (Size(320, 640), 2.0, Locale('en')),
  (Size(390, 844), 2.0, Locale('ar')),
  (Size(768, 1024), 2.0, Locale('en')),
  (Size(1024, 768), 2.0, Locale('ar')),
];

Future<void> pumpDesk(WidgetTester tester, Widget screen,
    {Size size = const Size(1024, 3000),
    double textScale = 1,
    Locale locale = const Locale('en'),
    bool settle = true}) async {
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
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: screen,
  ));
  if (settle) await tester.pumpAndSettle();
}

/// Scrolls [finder] into view and taps it.
Future<void> tapDesk(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder.first);
  await tester.pumpAndSettle();
  await tester.tap(finder.first);
  await tester.pumpAndSettle();
}
