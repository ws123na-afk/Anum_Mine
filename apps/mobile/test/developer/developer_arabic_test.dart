// Developer mode in Arabic (docs/mobile.md, Localization): the list, the
// request detail with its merge approval, and Projects read in Arabic, right
// to left, at the gate widths and at 200 percent text.
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:anum_mobile/features/developer/developer_projects_screen.dart';
import 'package:anum_mobile/features/developer/developer_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

Future<void> _pump(WidgetTester tester, Widget home,
    {Size size = const Size(390, 844), double scale = 1}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: const Locale('ar'),
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates,
    ],
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(
          disableAnimations: true, textScaler: TextScaler.linear(scale)),
      child: child!,
    ),
    home: home,
  ));
  await tester.pumpAndSettle();
}

Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(finder, 200,
      scrollable: find
          .descendant(
              of: find.byType(ListView).last, matching: find.byType(Scrollable))
          .first);
  await tester.pumpAndSettle();
}

List<Map<String, Object?>> _requests() => [
      requestJson('dev_3',
          text: 'Add a dark/light toggle to Settings',
          status: 'waiting_approval',
          pr: 42,
          prTitle: 'Dark mode toggle in Settings',
          createdAt: '2026-10-11T10:00:00Z'),
      requestJson('dev_2',
          text: 'Show pending approvals on the Tasks page',
          status: 'ci_failed',
          pr: 41,
          createdAt: '2026-10-10T10:00:00Z'),
    ];

void main() {
  test('Arabic plural categories follow CLDR', () {
    expect(
        [0, 1, 2, 3, 10, 11, 99, 100, 102, 103]
            .map(AnumLocalizations.arabicPluralForm),
        [
          'zero',
          'one',
          'two',
          'few',
          'few',
          'many',
          'many',
          'other',
          'other',
          'few'
        ]);
    const ar = AnumLocalizations(Locale('ar'));
    const en = AnumLocalizations.english;
    expect(ar.p('dev.projectsReady', 2, {'ready': 1}), 'مشروعان، 1 جاهز');
    expect(ar.p('dev.projectsReady', 11, {'ready': 3}), '11 مشروعًا، 3 جاهز');
    expect(en.p('dev.projectsReady', 1, {'ready': 1}), '1 project, 1 ready');
    expect(en.p('dev.projectsReady', 4, {'ready': 2}), '4 projects, 2 ready');
  });

  test('labels and checks in Arabic; English stays the default', () {
    const ar = AnumLocalizations(Locale('ar'));
    const checks =
        DevChecks(total: 3, passed: 1, failed: 1, pending: 1, failing: ['Web']);
    expect(checksLabel(checks),
        '1 of 3 checks passed, 1 running, 1 failing (Web)');
    expect(checksLabel(checks, ar),
        'نجح 1 من 3 فحوص، 1 قيد التشغيل، 1 يفشل (Web)');
    expect(projectStateLabel(ProjectState.attention, ar), 'يحتاج إلى انتباه');
    expect(projectStateLabel(ProjectState.attention), 'Needs attention');
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('the Developer list reads in Arabic at ${width.toInt()} wide',
        (tester) async {
      final api = FakeDeveloperApi(requests: _requests());
      final c = await buildDeveloper(api);
      await _pump(tester, Scaffold(body: DeveloperScreen(controller: c)),
          size: Size(width, 900), scale: width == 320 ? 2 : 1);
      expect(find.text('ابنِ تطبيقاتك'), findsOneWidget);
      expect(Directionality.of(tester.element(find.text('ابنِ تطبيقاتك'))),
          TextDirection.rtl);
      expect(find.text('طلب تغيير'), findsOneWidget);
      expect(find.text('بانتظار الموافقة'), findsWidgets);
      expect(find.text('CI يفشل'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
    });
  }

  testWidgets('detail and merge approval in Arabic; the hash stays as sent',
      (tester) async {
    final api = FakeDeveloperApi(requests: _requests());
    final c = await buildDeveloper(api);
    await _pump(tester, Scaffold(body: DeveloperScreen(controller: c)));
    final tile = find.byKey(const Key('developer-request-dev_3'));
    await _scrollTo(tester, tile);
    await tester.tap(tile);
    await tester.pumpAndSettle();
    expect(find.text('طلب المطوّر'), findsOneWidget);
    expect(find.text('نجح 3 من 3 فحوص'), findsOneWidget);
    final semantics = tester.ensureSemantics();
    expect(find.bySemanticsLabel('Merged: لم يبدأ'), findsOneWidget);
    semantics.dispose();

    final approve = find.byKey(const Key('developer-approve'));
    await _scrollTo(tester, approve);
    expect(find.text('الموافقة والدمج'), findsOneWidget);
    await tester.tap(approve);
    await tester.pumpAndSettle();
    expect(find.text('الموافقة على الدمج'), findsWidgets);
    expect(find.text(exampleHash), findsOneWidget);
    expect(
        Directionality.of(
            tester.element(find.byKey(const Key('developer-payload-hash')))),
        TextDirection.ltr);
    final confirm = find.byKey(const Key('developer-confirm-merge'));
    await _scrollTo(tester, confirm);
    await tester.tap(confirm);
    await tester.pumpAndSettle();
    expect(api.calls('POST', '/approve').single.body,
        {'payload_hash': exampleHash});
    expect(find.text('دُمج طلب الدمج #42.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Projects read in Arabic at 200% text', (tester) async {
    final api = FakeDeveloperApi(requests: _requests());
    final c = await buildDeveloper(api);
    await c.load();
    await _pump(tester, DeveloperProjectsScreen(controller: c), scale: 2);
    expect(find.text('المشاريع'), findsWidgets);
    expect(find.text('ما يحتاجه كل مستودع'), findsOneWidget);
    expect(find.text('اختبار الاتصال'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
