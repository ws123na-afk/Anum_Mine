// The shared money formatter (lib/src/localization/money_format.dart):
// thousands separators and two decimals like the web, in English and Arabic.
import 'package:anum_mobile/features/business/business_screen.dart' show money;
import 'package:anum_mobile/features/commerce/commerce_models.dart' as commerce;
import 'package:anum_mobile/features/finance/finance_models.dart';
import 'package:anum_mobile/src/localization/money_format.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

const _en = Locale('en');
const _ar = Locale('ar');
const _lrm = '‎';

void main() {
  group('formatMoney', () {
    test('groups thousands and keeps two decimals', () {
      expect(formatMoney(4000, 'SAR', locale: _en), 'SAR 4,000.00');
      expect(formatMoney(2500.5, 'SAR', locale: _en), 'SAR 2,500.50');
      expect(formatMoney(1234567.891, 'AED', locale: _en), 'AED 1,234,567.89');
      expect(formatMoney(0, 'SAR', locale: _en), 'SAR 0.00');
      expect(formatMoney(999.999, 'SAR', locale: _en), 'SAR 1,000.00');
    });

    test('negative amounts keep the sign next to the digits', () {
      expect(formatMoney(-79.5, 'AED', locale: _en), 'AED -79.50');
    });

    test('an unknown amount is a dash; no currency is just the number', () {
      expect(formatMoney(null, 'SAR', locale: _en), '-');
      expect(formatMoney(double.nan, 'SAR', locale: _en), '-');
      expect(formatMoney(12500, null, locale: _en), '12,500.00');
      expect(formatMoney(12500, '  ', locale: _en), '12,500.00');
    });

    test('Arabic uses Western digits and the same separators', () {
      expect(formatMoney(4000, 'SAR', locale: _ar), 'SAR 4,000.00');
      expect(formatMoney(1234567.891, 'ر.س', locale: _ar), 'ر.س 1,234,567.89');
      // Regional Arabic locales would switch to Arabic-Indic digits; the app
      // keeps Western digits in every Arabic locale.
      expect(formatMoney(2500, 'SAR', locale: const Locale('ar', 'EG')),
          'SAR 2,500.00');
      // intl marks the minus left to right so it stays with the digits in
      // right-to-left text.
      expect(formatMoney(-79.5, 'AED', locale: _ar), 'AED $_lrm-79.50');
    });

    test('another locale uses its own separators', () {
      expect(formatMoney(2500.5, 'EUR', locale: const Locale('de')),
          'EUR 2.500,50');
    });
  });

  group('formatMoneyText', () {
    test('groups the whole part and keeps the decimals as sent', () {
      expect(formatMoneyText('15349.5', 'AED', locale: _en), 'AED 15,349.5');
      expect(formatMoneyText('-1234.50', 'AED', locale: _en), 'AED -1,234.50');
      expect(
          formatMoneyText('0.00012345', 'BTC', locale: _en), 'BTC 0.00012345');
      expect(formatMoneyText('4000', 'SAR', locale: _ar), 'SAR 4,000');
      expect(formatMoneyText('-4000.25', 'SAR', locale: _ar),
          'SAR $_lrm-4,000.25');
      expect(formatMoneyText('abc', 'AED', locale: _en), '-');
      expect(formatMoneyText('', 'AED', locale: _en), '-');
    });
  });

  test('every screen formatter goes through the shared one', () {
    expect(money(4000, 'SAR'), 'SAR 4,000.00');
    expect(money(null, 'SAR'), '-');
    expect(commerce.formatMoney(41250.5, 'SAR'), 'SAR 41,250.50');
    expect(formatAmount('2500', 'SAR'), 'SAR 2,500');
  });
}
