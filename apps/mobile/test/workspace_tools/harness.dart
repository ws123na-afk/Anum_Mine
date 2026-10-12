import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const widths = [320.0, 390.0, 768.0, 1024.0, 1440.0];

/// Pumps [screen] in the dark theme with both catalogs, at [width] by
/// [height], with [scale] text, in [locale]; named routes push a page that
/// shows the name (so tests can assert where a tap went).
Future<List<String>> pumpScreen(
  WidgetTester tester,
  Widget screen, {
  double width = 390,
  double height = 900,
  double scale = 1,
  Locale locale = const Locale('en'),
}) async {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  final opened = <String>[];
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates,
    ],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          textScaler: TextScaler.linear(scale), disableAnimations: true),
      child: child!,
    ),
    onGenerateRoute: (settings) {
      opened.add(settings.name ?? '');
      return MaterialPageRoute<void>(
        settings: settings,
        builder: (_) => Scaffold(body: Text('route ${settings.name}')),
      );
    },
    home: KeyedSubtree(key: UniqueKey(), child: screen),
  ));
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  return opened;
}

/// Runs the layout checks at every width, at 200 percent text and in Arabic.
Future<void> checkLayouts(
    WidgetTester tester, Widget Function() build, Finder ready) async {
  for (final width in widths) {
    await pumpScreen(tester, build(), width: width);
    await tester.pump(const Duration(milliseconds: 50));
    expect(ready, findsWidgets, reason: 'at $width');
    expect(tester.takeException(), isNull, reason: 'overflow at $width');
  }
  await pumpScreen(tester, build(), width: 320, scale: 2);
  await tester.pump(const Duration(milliseconds: 50));
  expect(tester.takeException(), isNull, reason: 'overflow at 200% text');
  await pumpScreen(tester, build(), locale: const Locale('ar'));
  await tester.pump(const Duration(milliseconds: 50));
  expect(tester.takeException(), isNull, reason: 'overflow in Arabic');
  final scaffold = find.byType(Scaffold).first;
  expect(Directionality.of(tester.element(scaffold)), TextDirection.rtl);
  await pumpScreen(tester, build(), width: 390);
  await tester.pump(const Duration(milliseconds: 50));
  await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
  await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
}
