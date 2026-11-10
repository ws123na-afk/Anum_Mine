import 'anum_localizations.dart';

/// Why a typed amount was not accepted.
enum AmountProblem {
  /// Nothing was typed.
  empty,

  /// Letters, several dots or anything else that is not a number.
  notANumber,

  /// Separators that can be read two ways ("1.250,50", "1,2345"): the app
  /// asks instead of guessing.
  ambiguous,

  /// More decimals than the field keeps.
  tooManyDecimals,

  /// A minus sign where only a positive amount makes sense.
  negative,

  /// More than twelve digits before the decimal point.
  tooLarge,
}

/// What the person typed in an amount field, read once for the whole app.
class AmountInput {
  const AmountInput.ok(String this.text) : problem = null;
  const AmountInput.rejected(AmountProblem this.problem) : text = null;

  /// The amount as the API's decimal string ("1250.50", "-45"), with Latin
  /// digits, a dot for decimals and no grouping; null when [problem] is set.
  final String? text;
  final AmountProblem? problem;

  bool get isValid => text != null;

  /// [text] as a number, for the screens whose API takes JSON numbers. The
  /// decimal string is the source, so nothing is rounded on the way.
  double? get number => text == null ? null : double.parse(text!);
}

const _digitZeros = [0x0660, 0x06F0]; // Arabic-Indic, Eastern Arabic-Indic

/// Reads a typed amount (docs/mobile.md#amount-fields). The app shows amounts
/// with a comma for thousands and a dot for decimals in English and Arabic
/// (`formatMoney`), so typing follows the same reading:
///
/// * Latin, Arabic-Indic and Eastern Arabic-Indic digits; the Arabic decimal
///   (`٫`) and thousands (`٬`) marks; spaces are ignored.
/// * Commas that group thousands exactly (`1,250`, `12,500`, `1,250.50`) are
///   thousands separators.
/// * Without a dot, one comma followed by one or two digits is a decimal
///   comma (`12,5`, `0,75`).
/// * Anything else with separators (`1.250,50`, `1,2345`, `1,25,000`) is
///   refused as [AmountProblem.ambiguous] instead of guessed.
///
/// At most [decimals] decimals and twelve digits before them; a minus sign
/// only when [signed].
AmountInput readAmount(String input, {int decimals = 2, bool signed = false}) {
  final buffer = StringBuffer();
  for (final rune in input.runes) {
    final zero = _digitZeros.where((z) => rune >= z && rune <= z + 9);
    if (zero.isNotEmpty) {
      buffer.writeCharCode(0x30 + rune - zero.first);
    } else if (rune == 0x066B) {
      buffer.write('.');
    } else if (rune == 0x066C || rune == 0x060C) {
      buffer.write(',');
    } else if (rune == 0x2212 || rune == 0x2012 || rune == 0x2013) {
      buffer.write('-');
    } else if (rune == 0x20 ||
        rune == 0x09 ||
        rune == 0xA0 ||
        rune == 0x202F ||
        rune == 0x2009 ||
        // Direction marks a right-to-left keyboard or a paste can add.
        rune == 0x200E ||
        rune == 0x200F ||
        rune == 0x061C ||
        (rune >= 0x202A && rune <= 0x202E) ||
        (rune >= 0x2066 && rune <= 0x2069)) {
      continue;
    } else {
      buffer.writeCharCode(rune);
    }
  }
  var text = buffer.toString();
  if (text.isEmpty) return const AmountInput.rejected(AmountProblem.empty);
  // Arabic text may carry the minus after the digits.
  var negative = false;
  if (text.startsWith('-')) {
    negative = true;
    text = text.substring(1);
  } else if (text.endsWith('-')) {
    negative = true;
    text = text.substring(0, text.length - 1);
  } else if (text.startsWith('+')) {
    text = text.substring(1);
  }
  if (!RegExp(r'^[0-9.,]+$').hasMatch(text) ||
      !text.contains(RegExp('[0-9]'))) {
    return const AmountInput.rejected(AmountProblem.notANumber);
  }
  final String whole;
  final String fraction;
  if (RegExp(r'^\d+$').hasMatch(text)) {
    whole = text;
    fraction = '';
  } else if (RegExp(r'^\d*\.\d+$').hasMatch(text) ||
      RegExp(r'^\d+\.$').hasMatch(text)) {
    final dot = text.indexOf('.');
    whole = dot == 0 ? '0' : text.substring(0, dot);
    fraction = text.substring(dot + 1);
  } else if (RegExp(r'^\d{1,3}(,\d{3})+(\.\d+)?$').hasMatch(text)) {
    final dot = text.indexOf('.');
    whole = (dot < 0 ? text : text.substring(0, dot)).replaceAll(',', '');
    fraction = dot < 0 ? '' : text.substring(dot + 1);
  } else if (RegExp(r'^\d+,\d{1,2}$').hasMatch(text)) {
    final comma = text.indexOf(',');
    whole = text.substring(0, comma);
    fraction = text.substring(comma + 1);
  } else if ('.'.allMatches(text).length > 1 && !text.contains(',')) {
    return const AmountInput.rejected(AmountProblem.notANumber);
  } else {
    return const AmountInput.rejected(AmountProblem.ambiguous);
  }
  if (negative && !signed) {
    return const AmountInput.rejected(AmountProblem.negative);
  }
  if (fraction.length > decimals) {
    return const AmountInput.rejected(AmountProblem.tooManyDecimals);
  }
  final digits = whole.replaceFirst(RegExp(r'^0+(?=\d)'), '');
  if (digits.length > 12) {
    return const AmountInput.rejected(AmountProblem.tooLarge);
  }
  final zero =
      RegExp(r'^0*$').hasMatch(digits) && RegExp(r'^0*$').hasMatch(fraction);
  return AmountInput.ok('${negative && !zero ? '-' : ''}$digits'
      '${fraction.isEmpty ? '' : '.$fraction'}');
}

/// A typed amount as the wire string, or null when [readAmount] refuses it.
String? parseAmount(String input, {int decimals = 2, bool signed = true}) =>
    readAmount(input, decimals: decimals, signed: signed).text;

/// The sentence shown under an amount field for [problem], in the app's
/// language: what is wrong and how to write it.
String amountProblemText(AnumLocalizations l, AmountProblem problem,
        {int decimals = 2}) =>
    switch (problem) {
      AmountProblem.empty => l.t('amount.empty'),
      AmountProblem.notANumber => l.t('amount.notANumber'),
      AmountProblem.ambiguous => l.t('amount.ambiguous'),
      AmountProblem.tooManyDecimals => l.p('amount.tooManyDecimals', decimals),
      AmountProblem.negative => l.t('amount.negative'),
      AmountProblem.tooLarge => l.t('amount.tooLarge'),
    };

/// A typed quantity, rate, percentage or price as a number, read by
/// [readAmount] (so "1,250" is 1250 everywhere); null when it is refused.
double? typedNumber(String input, {int decimals = 6, bool signed = false}) =>
    readAmount(input, decimals: decimals, signed: signed).number;

/// The sentence for the problems a screen's own "enter a valid amount" does
/// not explain (two possible readings, too many decimals, too large); null
/// for the rest, where the screen's message is the clearer one.
String? amountHint(AnumLocalizations l, AmountInput typed,
        {int decimals = 2}) =>
    switch (typed.problem) {
      final AmountProblem problem?
          when problem == AmountProblem.ambiguous ||
              problem == AmountProblem.tooManyDecimals ||
              problem == AmountProblem.tooLarge =>
        amountProblemText(l, problem, decimals: decimals),
      _ => null,
    };
