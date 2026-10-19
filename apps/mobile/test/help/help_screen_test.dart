import 'dart:io';

import 'package:anum_mobile/features/help/help_library.dart';
import 'package:anum_mobile/features/help/help_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

/// The bundled articles, read from the asset copies (docs/help.md).
List<HelpArticle> _bundled() => [
      for (final file in Directory('assets/help').listSync().whereType<File>())
        if (file.path.endsWith('.md'))
          parseHelpArticle(file.path, file.readAsStringSync())!,
    ];

Widget _app(Widget home, {Locale locale = const Locale('en')}) => MaterialApp(
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: home,
    );

void main() {
  testWidgets('searches the articles and opens one', (tester) async {
    final articles = _bundled();
    await tester.pumpWidget(_app(HelpScreen(loader: () async => articles)));
    await tester.pumpAndSettle();
    expect(find.text('Help'), findsOneWidget);
    expect(find.text('${articles.length ~/ 2} articles'), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('help-search')), 'approve reject');
    await tester.pumpAndSettle();
    expect(find.text('Approvals'), findsWidgets);
    expect(find.text('Calendar'), findsNothing);

    await tester.tap(find.widgetWithText(ListTile, 'Approvals').first);
    await tester.pumpAndSettle();
    expect(find.text('Approve or reject'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Full documentation'), 300);
    expect(find.text('Full documentation'), findsOneWidget);
  });

  testWidgets('says when nothing matches', (tester) async {
    final articles = _bundled();
    await tester.pumpWidget(_app(HelpScreen(loader: () async => articles)));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('help-search')), 'zzzz');
    await tester.pumpAndSettle();
    expect(find.textContaining('No article matches "zzzz"'), findsOneWidget);
    expect(find.text('Full documentation'), findsOneWidget);
  });

  testWidgets('shows the Arabic articles in Arabic', (tester) async {
    final articles = _bundled();
    await tester.pumpWidget(_app(HelpScreen(loader: () async => articles),
        locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('المساعدة'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('help-search')), 'الموافقات');
    await tester.pumpAndSettle();
    expect(find.widgetWithText(ListTile, 'الموافقات'), findsWidgets);
  });

  testWidgets('explains a failed load', (tester) async {
    await tester.pumpWidget(
        _app(HelpScreen(loader: () async => throw StateError('missing'))));
    await tester.pumpAndSettle();
    expect(
        find.textContaining("Couldn't open the help articles"), findsOneWidget);
  });

  test('parses links into the docs and refuses traversal', () {
    expect(helpDocsHref('../tasks.md'), '${helpDocsBaseUrl}tasks.md');
    expect(helpDocsHref('../../secret'), isNull);
    expect(parseHelpArticle('x.md', '# no frontmatter'), isNull);
  });
}
