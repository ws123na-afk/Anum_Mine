import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('English and Arabic catalogs cover primary navigation', () {
    final english = AnumLocalizations(Locale('en'));
    final arabic = AnumLocalizations(Locale('ar'));
    for (final key in ['workspace', 'tasks', 'voice', 'approvals', 'automation', 'resources', 'settings', 'refresh']) {
      expect(english.t(key), isNot(key));
      expect(arabic.t(key), isNot(key));
      expect(arabic.t(key), isNot(english.t(key)));
    }
    expect(arabic.isArabic, isTrue);
  });

  testWidgets('Arabic locale establishes RTL direction', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      locale: Locale('ar'),
      supportedLocales: [Locale('en'), Locale('ar')],
      localizationsDelegates: [AnumLocalizations.delegate, ...GlobalMaterialLocalizations.delegates],
      home: Text('المهام'),
    ));
    await tester.pumpAndSettle();
    expect(Directionality.of(tester.element(find.text('المهام'))), TextDirection.rtl);
  });
}
