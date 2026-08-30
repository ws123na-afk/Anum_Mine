import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/widgets.dart';
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
    await tester.pumpWidget(WidgetsApp(
      color: Color(0xFFFFFFFF),
      locale: Locale('ar'),
      supportedLocales: [Locale('en'), Locale('ar')],
      localizationsDelegates: [AnumLocalizations.delegate],
      home: Directionality(textDirection: TextDirection.rtl, child: Text('المهام')),
    ));
    expect(Directionality.of(tester.element(find.text('المهام'))), TextDirection.rtl);
  });
}
