import 'dart:ui' show Locale;

import 'package:intl/intl.dart';

/// The one money formatter for the app: the currency code, then the amount
/// with thousands separators and two decimals, as the web shows it
/// (`SAR 2,500.00`, `SAR -1,234.50`). An unknown amount is `-`.
///
/// Numbers go through intl's [NumberFormat] for [locale] (the app's locale
/// when given, else intl's current locale). Arabic keeps Western digits and
/// separators, like the web's `ar-u-nu-latn` (docs/web-localization.md), so
/// the same figure reads the same in both languages.
String formatMoney(num? amount, String? currency, {Locale? locale}) {
  if (amount == null || amount.isNaN || amount.isInfinite) return '-';
  final number = NumberFormat.decimalPatternDigits(
          locale: _numberLocale(locale), decimalDigits: 2)
      .format(amount);
  final code = currency?.trim() ?? '';
  return code.isEmpty ? number : '$code $number';
}

/// [formatMoney] for an amount the API sends as a decimal string
/// (`"-1234.5"`): the whole part is grouped for the locale and the decimals
/// are kept exactly as sent, so no precision is lost to floating point.
/// Text that is not a plain decimal number is `-`.
String formatMoneyText(String value, String? currency, {Locale? locale}) {
  final match = RegExp(r'^(-?)(\d+)(?:\.(\d+))?$').firstMatch(value.trim());
  if (match == null) return '-';
  final digits = match.group(2)!;
  final format = NumberFormat.decimalPattern(_numberLocale(locale));
  final whole = BigInt.tryParse(digits);
  final grouped =
      whole != null && whole.isValidInt ? format.format(whole.toInt()) : digits;
  final fraction = match.group(3);
  final decimal = format.symbols.DECIMAL_SEP;
  final number = fraction == null || fraction.isEmpty
      ? grouped
      : '$grouped$decimal$fraction';
  final signed =
      match.group(1)!.isEmpty ? number : '${format.symbols.MINUS_SIGN}$number';
  final code = currency?.trim() ?? '';
  return code.isEmpty ? signed : '$code $signed';
}

/// The intl locale for numbers: Arabic always uses `ar`, which intl writes
/// with Western digits (regional Arabic locales such as `ar_EG` would switch
/// to Arabic-Indic digits); anything intl does not know falls back to `en`.
String _numberLocale(Locale? locale) {
  final tag = locale == null
      ? Intl.getCurrentLocale()
      : locale.toLanguageTag().replaceAll('-', '_');
  final language = tag.split('_').first.toLowerCase();
  if (language == 'ar') return 'ar';
  return NumberFormat.localeExists(tag)
      ? tag
      : NumberFormat.localeExists(language)
          ? language
          : 'en';
}
