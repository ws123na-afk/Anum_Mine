import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// The bundled fonts and their SIL Open Font License texts (assets/fonts).
const bundledFontLicenses = {
  'Inter': 'assets/fonts/OFL-Inter.txt',
  'Noto Sans Arabic': 'assets/fonts/OFL-NotoSansArabic.txt',
};

/// Adds the bundled fonts' licences to the app's licence page
/// (Profile, About, "View licenses"), as the Open Font License requires when
/// the fonts ship.
/// The texts load lazily, only when the page is opened.
void registerFontLicenses({AssetBundle? bundle}) {
  LicenseRegistry.addLicense(() async* {
    final assets = bundle ?? rootBundle;
    for (final entry in bundledFontLicenses.entries) {
      yield LicenseEntryWithLineBreaks(
          [entry.key], await assets.loadString(entry.value));
    }
  });
}
