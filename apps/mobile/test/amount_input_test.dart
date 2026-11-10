// The app's one amount parser (docs/mobile.md#amount-fields).
import 'package:anum_mobile/features/business/invoices.dart';
import 'package:anum_mobile/features/finance/finance_models.dart' as finance;
import 'package:anum_mobile/src/localization/amount_input.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('readAmount', () {
    const accepted = {
      '1250': '1250',
      '1,250': '1250',
      '12,500': '12500',
      '1,250.50': '1250.50',
      '1,234,567.8': '1234567.8',
      '12,5': '12.5',
      '0,75': '0.75',
      '12.5': '12.5',
      '.5': '0.5',
      '5.': '5',
      ' 1 250 ': '1250',
      '007': '7',
      '0': '0',
      // Arabic-Indic digits with the Arabic thousands and decimal marks.
      '١٬٢٥٠': '1250',
      '١٢٥٠': '1250',
      '١٬٢٥٠٫٥٠': '1250.50',
      '١٢٫٥': '12.5',
      // Eastern Arabic-Indic (Persian, Urdu) digits.
      '۱۲۵۰': '1250',
      // A direction mark pasted with the number.
      '‏1,250': '1250',
    };
    accepted.forEach((typed, wire) {
      test('"$typed" is $wire', () {
        final read = readAmount(typed);
        expect(read.problem, isNull);
        expect(read.text, wire);
        expect(read.number, double.parse(wire));
      });
    });

    const refused = {
      '': AmountProblem.empty,
      '   ': AmountProblem.empty,
      'abc': AmountProblem.notANumber,
      '12a': AmountProblem.notANumber,
      '1.2.3': AmountProblem.notANumber,
      ',': AmountProblem.notANumber,
      // A dot for thousands and a comma for decimals: not this app's format.
      '1.250,50': AmountProblem.ambiguous,
      '1,2345': AmountProblem.ambiguous,
      '1,25,000': AmountProblem.ambiguous,
      '12,50,000.00': AmountProblem.ambiguous,
      '1,250,5': AmountProblem.ambiguous,
      ',5': AmountProblem.ambiguous,
      '1.250': AmountProblem.tooManyDecimals,
      '1,250.505': AmountProblem.tooManyDecimals,
      '-45': AmountProblem.negative,
      '-1,250': AmountProblem.negative,
      '1234567890123': AmountProblem.tooLarge,
    };
    refused.forEach((typed, problem) {
      test('"$typed" is refused: ${problem.name}', () {
        final read = readAmount(typed);
        expect(read.text, isNull);
        expect(read.number, isNull);
        expect(read.problem, problem);
      });
    });

    test('a minus sign is read where the field allows one', () {
      expect(readAmount('-45', signed: true).text, '-45');
      expect(readAmount('-1,250.5', signed: true).text, '-1250.5');
      expect(readAmount('−1250', signed: true).text, '-1250');
      expect(readAmount('-0', signed: true).text, '0');
    });

    test('the number of decimals is the field\'s', () {
      expect(readAmount('1.2345', decimals: 4).text, '1.2345');
      expect(readAmount('1.5', decimals: 0).problem,
          AmountProblem.tooManyDecimals);
      expect(typedNumber('1,250'), 1250);
      expect(typedNumber('2.125'), 2.125);
      expect(typedNumber(''), isNull);
    });

    test('Finance reads amounts with the same parser', () {
      expect(finance.parseAmount('1,234.50'), '1234.50');
      expect(finance.parseAmount('12,5'), '12.5');
      expect(finance.parseAmount('1,2,3'), isNull);
      expect(finance.parseAmount('-45'), '-45');
      expect(finance.parseAmount('-45', signed: false), isNull);
    });

    test('every problem has an English and an Arabic sentence', () {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final l = AnumLocalizations(locale);
        for (final problem in AmountProblem.values) {
          final text = amountProblemText(l, problem);
          expect(text, isNot(startsWith('amount.')), reason: '$problem');
          expect(text, isNot(contains('{')), reason: '$problem');
        }
      }
      expect(
          amountProblemText(
              AnumLocalizations.english, AmountProblem.tooManyDecimals),
          'Use at most 2 decimal places.');
    });
  });

  group('invoice amounts', () {
    /// Opens [dialog] and returns a reader of what it closed with.
    Future<Map<String, Object?>? Function()> open(
        WidgetTester tester, Widget dialog,
        {Locale locale = const Locale('en')}) async {
      Map<String, Object?>? result;
      await tester.pumpWidget(MaterialApp(
        locale: locale,
        supportedLocales: const [Locale('en'), Locale('ar')],
        localizationsDelegates: const [
          AnumLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        home: Builder(
          builder: (context) => Scaffold(
            body: FilledButton(
              onPressed: () async => result =
                  await showDialog<Map<String, Object?>>(
                      context: context, builder: (_) => dialog),
              child: const Text('open'),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      return () => result;
    }

    Future<void> submitPayment(WidgetTester tester, String typed) async {
      await tester.enterText(find.byKey(const Key('inv-pay-amount')), typed);
      await tester.tap(find.byKey(const Key('inv-pay-submit')));
      await tester.pumpAndSettle();
    }

    testWidgets('a payment typed as "1,250" is recorded as 1250, not 1.25',
        (tester) async {
      final result = await open(
          tester, const PaymentDialog(balance: 5000, currency: 'AED'));
      await submitPayment(tester, '1,250');
      expect(result()?['amount'], 1250.0);
    });

    testWidgets('a payment typed two ways is refused with the reason',
        (tester) async {
      final result = await open(
          tester, const PaymentDialog(balance: 5000, currency: 'AED'));
      for (final typed in const ['1.250,50', '1,2345']) {
        await submitPayment(tester, typed);
        // Still open, with the reason under the field.
        expect(find.byKey(const Key('inv-pay-amount')), findsOneWidget);
        expect(
            find.text('This amount can be read in two ways. Write it with a '
                'dot for decimals, for example 1250.50.'),
            findsOneWidget);
      }
      await submitPayment(tester, '12.345');
      expect(find.text('Use at most 2 decimal places.'), findsOneWidget);
      expect(result(), isNull);
    });

    testWidgets('Arabic digits are read and the reason is in Arabic',
        (tester) async {
      final result = await open(
          tester, const PaymentDialog(balance: 5000, currency: 'AED'),
          locale: const Locale('ar'));
      await submitPayment(tester, '1.250,50');
      expect(find.textContaining('يمكن قراءة هذا المبلغ بطريقتين'),
          findsOneWidget);
      await submitPayment(tester, '١٬٢٥٠٫٥٠');
      expect(result()?['amount'], 1250.5);
    });

    testWidgets('a credit note typed as "1,250" is 1250', (tester) async {
      final result = await open(
          tester, const CreditDialog(balance: 5000, currency: 'AED'));
      await tester.enterText(
          find.byKey(const Key('inv-credit-amount')), '1,250');
      await tester.enterText(
          find.byKey(const Key('inv-credit-reason')), 'Returned goods');
      await tester.tap(find.byKey(const Key('inv-credit-submit')));
      await tester.pumpAndSettle();
      expect(result()?['amount'], 1250.0);
    });
  });
}
