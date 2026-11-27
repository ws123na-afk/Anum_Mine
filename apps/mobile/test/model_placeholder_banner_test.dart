// "AI answers are placeholders" on the phone (docs/model-gateway.md#placeholder-answers):
// the banner shows only while the model is the placeholder, says whose setting
// it is, opens model setup, and reads right to left in Arabic.
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/model_placeholder_banner.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> _pump(WidgetTester tester,
    {required bool? placeholder,
    bool workspace = false,
    VoidCallback? onSetUp,
    Locale locale = const Locale('en'),
    Size size = const Size(390, 844)}) async {
  tester.view.physicalSize = size * 3;
  tester.view.devicePixelRatio = 3;
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
    home: Scaffold(
      body: Column(children: [
        AnumModelPlaceholderBanner(
            placeholder: placeholder,
            workspaceSource: workspace,
            onSetUp: onSetUp ?? () {}),
        const Expanded(child: Placeholder()),
      ]),
    ),
  ));
  await tester.pumpAndSettle();
}

const _banner = ValueKey('model-placeholder-banner');

void main() {
  testWidgets('the server placeholder: what it means and Set up model',
      (tester) async {
    var opened = 0;
    await _pump(tester, placeholder: true, onSetUp: () => opened++);
    expect(find.byKey(_banner), findsOneWidget);
    expect(
        find.text(
            'AI answers are placeholders: the server has no AI model set up.'),
        findsOneWidget);
    expect(
        find.textContaining('Everything else in ANUM works'), findsOneWidget);
    await tester.tap(find.text('Set up model'));
    expect(opened, 1);
  });

  testWidgets('a workspace set to the placeholder says so', (tester) async {
    await _pump(tester, placeholder: true, workspace: true);
    expect(find.textContaining('this workspace is set to the placeholder'),
        findsOneWidget);
  });

  testWidgets('a real model or an unknown status shows nothing',
      (tester) async {
    await _pump(tester, placeholder: false);
    expect(find.byKey(_banner), findsNothing);
    await _pump(tester, placeholder: null);
    expect(find.byKey(_banner), findsNothing);
  });

  testWidgets('Arabic, right to left', (tester) async {
    await _pump(tester, placeholder: true, locale: const Locale('ar'));
    expect(find.text('إعداد النموذج'), findsOneWidget);
    expect(Directionality.of(tester.element(find.byKey(_banner))),
        TextDirection.rtl);
  });

  for (final size in const [Size(360, 640), Size(390, 844), Size(1024, 768)]) {
    testWidgets('fits ${size.width.toInt()}x${size.height.toInt()} at 2x text',
        (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = 2;
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      await _pump(tester, placeholder: true, size: size);
      expect(tester.takeException(), isNull);
    });
  }

  test('model status reads the API answer', () {
    final status = ModelStatus.fromJson(
        {'placeholder': true, 'provider': 'mock', 'source': 'workspace'});
    expect(status.placeholder, isTrue);
    expect(status.source, 'workspace');
  });
}
