// Bundled fonts (docs/mobile.md#fonts, owner decision 12 Oct 2026): the
// theme's families are real assets with their licences, and the licences
// reach the licence page.
import 'dart:io';

import 'package:anum_mobile/src/font_licenses.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('every weight the theme uses is a bundled font file', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    for (final family in ['Inter', 'Noto Sans Arabic']) {
      expect(pubspec, contains('- family: $family'), reason: family);
    }
    for (final file in ['Inter', 'NotoSansArabic']) {
      for (final weight in [400, 500, 600, 700, 800]) {
        final path = 'assets/fonts/$file-$weight.ttf';
        expect(pubspec, contains(path));
        expect(File(path).lengthSync(), greaterThan(50 * 1024), reason: path);
      }
    }
    expect(AnumTheme.fontFamily, 'Inter');
    expect(AnumTheme.fontFamilyFallback.first, 'Noto Sans Arabic');
  });

  test('the fonts load and their OFL licences are registered', () async {
    final loader = FontLoader('Inter')
      ..addFont(rootBundle.load('assets/fonts/Inter-400.ttf'));
    await loader.load();

    registerFontLicenses();
    final entries = await LicenseRegistry.licenses.toList();
    for (final name in bundledFontLicenses.keys) {
      final entry = entries.firstWhere((e) => e.packages.contains(name));
      final text = entry.paragraphs.map((p) => p.text).join('\n');
      expect(text, contains('SIL Open Font License'), reason: name);
    }
  });
}
