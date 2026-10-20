// The premium visual identity (docs/design/anum360.md#theme): AA contrast for
// every text and status colour on every surface in both themes, one accent,
// no gradient text, hairline cards, short motion that honours reduced motion,
// and the shared states (skeleton loading, empty, error) at 320 to 1440 px,
// 200% text and in Arabic.
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/anum_components.dart';
import 'package:anum_mobile/src/widgets/depth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

double _contrast(Color a, Color b) {
  final la = a.computeLuminance(), lb = b.computeLuminance();
  final hi = la > lb ? la : lb, lo = la > lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

/// A translucent tint as it is painted on [surface].
Color _tint(Color color, Color surface) =>
    Color.alphaBlend(color.withValues(alpha: 0.12), surface);

void main() {
  for (final (name, p) in [
    ('dark', AnumPalette.dark),
    ('light', AnumPalette.light),
  ]) {
    group('$name palette', () {
      final surfaces = [p.background, p.surface1, p.surface2, p.surface3];

      test('text meets AA on every surface', () {
        for (final s in surfaces) {
          expect(_contrast(p.text, s), greaterThanOrEqualTo(7));
          expect(_contrast(p.muted, s), greaterThanOrEqualTo(4.5));
          expect(_contrast(p.faint, s), greaterThanOrEqualTo(4.5));
          expect(_contrast(p.violet, s), greaterThanOrEqualTo(4.5));
        }
        expect(_contrast(p.violet, p.accentSoft), greaterThanOrEqualTo(4.5));
        expect(_contrast(p.text, p.accentSoft), greaterThanOrEqualTo(7));
      });

      test('status colours meet AA on surfaces and on their own tint', () {
        for (final status in [p.ok, p.warn, p.stop]) {
          for (final s in surfaces) {
            expect(_contrast(status, s), greaterThanOrEqualTo(4.5));
          }
          expect(_contrast(status, _tint(status, p.surface1)),
              greaterThanOrEqualTo(4.5));
        }
      });

      test('one accent: white text on it, every accent alias the same', () {
        expect(_contrast(p.onAccent, p.accentStart), greaterThanOrEqualTo(4.5));
        expect(p.accentStart, p.accentEnd);
        expect(p.sky, p.violet);
        expect(p.rose, p.violet);
        expect(p.accentGradient.colors.toSet(), hasLength(1));
      });

      test('the theme uses the palette and solid accent controls', () {
        final theme = name == 'dark' ? AnumTheme.dark() : AnumTheme.light();
        expect(theme.extension<AnumPalette>(), p);
        expect(theme.colorScheme.primary, p.accentStart);
        expect(theme.colorScheme.onPrimary, p.onAccent);
        expect(theme.scaffoldBackgroundColor, p.background);
        expect(theme.textTheme.bodyMedium?.fontFamily, AnumTheme.fontFamily);
        expect(theme.textTheme.bodyMedium?.fontFamilyFallback,
            contains('Noto Sans Arabic'));
      });
    });
  }

  test('motion is 150 to 200 ms', () {
    for (final d in [AnumMotion.quick, AnumMotion.standard, AnumMotion.page]) {
      expect(d.inMilliseconds, inInclusiveRange(150, 200));
    }
  });

  Future<void> pump(WidgetTester tester, Widget child,
      {double width = 390,
      double scale = 1,
      bool reduceMotion = false,
      TextDirection direction = TextDirection.ltr,
      ThemeData? theme}) async {
    tester.view.physicalSize = Size(width, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: theme ?? AnumTheme.light(),
      builder: (context, c) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
              disableAnimations: reduceMotion,
              textScaler: TextScaler.linear(scale)),
          child: Directionality(textDirection: direction, child: c!)),
      home: Scaffold(body: AnumPage(children: [child])),
    ));
  }

  testWidgets('a surface is a hairline card with soft depth, no gradient',
      (tester) async {
    await pump(tester, const AnumSurface(child: Text('Card')));
    final p = AnumPalette.light;
    final material = tester.widget<Material>(find
        .ancestor(of: find.text('Card'), matching: find.byType(Material))
        .first);
    expect(material.color, p.surface1);
    final shape = material.shape! as RoundedRectangleBorder;
    expect(shape.side.color, p.line);
    expect(shape.side.width, 1);
    expect(find.byType(ShaderMask), findsNothing);
  });

  testWidgets('loading shows a skeleton and says what is loading',
      (tester) async {
    await pump(
        tester,
        const AnumFeedback(
            kind: AnumFeedbackKind.loading, message: 'Loading leads'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.byType(AnumSkeleton), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('Loading leads'), findsOneWidget);
    expect(find.bySemanticsLabel('Loading leads'), findsOneWidget);
  });

  testWidgets('the skeleton stands still with reduced motion', (tester) async {
    await pump(tester, const AnumSkeleton(), reduceMotion: true);
    expect(tester.hasRunningAnimations, isFalse);
    expect(find.byType(ShaderMask), findsNothing);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('states lay out at $width px, 200% text, Arabic and dark',
        (tester) async {
      for (final direction in TextDirection.values) {
        await pump(
            tester,
            Column(children: [
              const AnumFeedback(
                  kind: AnumFeedbackKind.error,
                  message: 'The leads could not load. Check the connection.'),
              const AnumEmpty(
                  title: 'No leads yet',
                  message: 'Add a lead to start your pipeline.'),
              AnumMetricGrid(children: [
                for (var i = 0; i < 5; i++)
                  AnumMetric(
                      label: 'Figure $i',
                      value: 'SAR 12,500.00',
                      icon: Icons.insights_outlined),
              ]),
              const AnumPill(label: 'Approval required', tone: AnumTone.warn),
            ]),
            width: width,
            scale: 2,
            direction: direction,
            theme: AnumTheme.dark());
        await tester.pump();
        expect(tester.takeException(), isNull);
      }
    });
  }
}
