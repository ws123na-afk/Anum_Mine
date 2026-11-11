// The owner desk catalogs (lib/src/localization/owner_desk_strings.dart):
// every English key has an Arabic twin with the same placeholders, the
// Arabic is written in Arabic, and no sentence holds an em or en dash.
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/owner_desk_strings.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('English and Arabic owner desk strings match', () {
    final names = RegExp(r'\{(\w+)\}');
    Set<String> of(String text) =>
        names.allMatches(text).map((m) => m.group(1)!).toSet();
    expect(ownerDeskAr.keys.toSet(), ownerDeskEn.keys.toSet());
    for (final key in ownerDeskEn.keys) {
      expect(of(ownerDeskAr[key]!), of(ownerDeskEn[key]!), reason: key);
    }
    final arabic = RegExp('[؀-ۿ]');
    // Codes and brand words that read the same in both languages.
    const same = {
      'executive.grants.row',
      'selling.ready.provider.stripe',
    };
    for (final entry in ownerDeskAr.entries) {
      if (same.contains(entry.key)) continue;
      expect(arabic.hasMatch(entry.value), isTrue, reason: entry.key);
    }
    for (final text in [...ownerDeskEn.values, ...ownerDeskAr.values]) {
      expect(text.contains('–') || text.contains('—'), isFalse, reason: text);
    }
  });

  test('the main catalog finds the owner desk keys in both languages', () {
    const en = AnumLocalizations(Locale('en'));
    const ar = AnumLocalizations(Locale('ar'));
    for (final key in [
      'nav.executive',
      'selling.ready.title',
      'od.ib.title',
      'od.ab.title',
      'ab.status_running'
    ]) {
      expect(en.t(key), isNot(key));
      expect(ar.t(key), isNot(en.t(key)), reason: key);
    }
  });
}
