import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_core_models.dart';
import 'finance_core_repository.dart';

/// A typed or imported transaction's date, amount and description corrected
/// on the phone, as the web's transaction form does (`TransactionForm` with a
/// transaction, `PATCH /finance/transactions/{id}`). The amount is signed
/// (money out below zero) in the row's own currency, which never changes.
/// The server reverses and reposts the ledger entry in the same chain, so
/// the balance and the journal follow, and refuses a row an SMS or Apple Pay
/// recorded (409, shown in its own words: those are corrected under iPhone
/// SMS, Review). Only what changed is sent.

/// The signed decimal the API accepts (`-45.00`, `120`), or null. Arabic
/// digits and a comma decimal are read as Western, as elsewhere.
String? signedDecimal(String text) {
  final trimmed = text.trim().replaceAll('−', '-');
  final negative = trimmed.startsWith('-');
  final body = trimmed.replaceFirst(RegExp(r'^[+-]'), '');
  final value = decimalText(body);
  if (value == null) return null;
  return negative ? '-$value' : value;
}

/// The PATCH body: only the fields that differ from [row].
Map<String, Object?> transactionEditBody(RegisterRow row,
    {required String postedOn,
    required String amount,
    required String description}) {
  final same = double.tryParse(amount) == double.tryParse(row.amount);
  return {
    if (postedOn != row.postedOn) 'posted_on': postedOn,
    if (!same) 'amount': amount,
    if (description != row.description) 'description': description,
  };
}

extension FinanceTransactionEditApi on FinanceCoreRepository {
  Future<void> editTransaction(String id, Map<String, Object?> body) =>
      api.request('PATCH',
          '${FinanceCoreRepository.base}/transactions/${Uri.encodeComponent(id)}',
          body: body);
}

class TransactionEditStrings extends ModuleStrings {
  const TransactionEditStrings(bool arabic) : super(arabic, _en, _ar);
  static TransactionEditStrings of(BuildContext context) =>
      TransactionEditStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  static const _en = <String, String>{
    'open': 'Edit date, amount and description',
    'title': 'Edit transaction',
    'intro':
        'Correct what was typed or imported wrongly. The balance and the journal are updated; the change is kept in the audit trail.',
    'date': 'Date',
    'amount': 'Amount in {currency} (money out below zero)',
    'amountBad': 'Enter an amount such as -45.00 or 120.',
    'amountZero': 'The amount cannot be zero.',
    'description': 'Description',
    'descriptionBad': 'Enter a description.',
    'save': 'Save changes',
    'saving': 'Saving...',
    'cancel': 'Cancel',
    'nothing': 'Nothing changed.',
    'saved': 'Transaction saved. Balance and journal updated.',
    'failed': 'Not saved: {message}',
  };

  static const _ar = <String, String>{
    'open': 'تعديل التاريخ والمبلغ والوصف',
    'title': 'تعديل المعاملة',
    'intro':
        'صحح ما كُتب أو استُورد خطأ. يُحدّث الرصيد والدفتر، ويبقى التغيير في سجل التدقيق.',
    'date': 'التاريخ',
    'amount': 'المبلغ بـ {currency} (المال الصادر أقل من صفر)',
    'amountBad': 'أدخل مبلغا مثل -45.00 أو 120.',
    'amountZero': 'لا يمكن أن يكون المبلغ صفرا.',
    'description': 'الوصف',
    'descriptionBad': 'أدخل وصفا.',
    'save': 'احفظ التغييرات',
    'saving': 'جارٍ الحفظ...',
    'cancel': 'إلغاء',
    'nothing': 'لم يتغير شيء.',
    'saved': 'حُفظت المعاملة. حُدّث الرصيد والدفتر.',
    'failed': 'لم تُحفظ: {message}',
  };
}

/// The edit page; pops `true` once saved.
class TransactionEditScreen extends StatefulWidget {
  const TransactionEditScreen(
      {required this.repository, required this.row, super.key});
  final FinanceCoreRepository repository;
  final RegisterRow row;

  @override
  State<TransactionEditScreen> createState() => _TransactionEditScreenState();
}

class _TransactionEditScreenState extends State<TransactionEditScreen> {
  final _form = GlobalKey<FormState>();
  late final _date = TextEditingController(text: widget.row.postedOn);
  late final _amount = TextEditingController(text: widget.row.amount);
  late final _description = TextEditingController(text: widget.row.description);
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _date.dispose();
    _amount.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final s = TransactionEditStrings.of(context);
    if (!(_form.currentState?.validate() ?? false)) return;
    final body = transactionEditBody(widget.row,
        postedOn: _date.text.trim(),
        amount: signedDecimal(_amount.text)!,
        description: _description.text.trim());
    if (body.isEmpty) {
      setState(() => _error = s.t('nothing'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.repository.editTransaction(widget.row.id, body);
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = s.t('failed', {'message': RecordsFailure.from(error).message});
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TransactionEditStrings.of(context);
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('title'))),
      body: Form(
        key: _form,
        child: AnumPage(children: [
          Text(s.t('intro'), style: TextStyle(color: p.muted)),
          RecordsDateField(
            fieldKey: const Key('tx-edit-date'),
            controller: _date,
            label: s.t('date'),
            required: true,
          ),
          TextFormField(
            key: const Key('tx-edit-amount'),
            controller: _amount,
            enabled: !_busy,
            keyboardType: const TextInputType.numberWithOptions(
                signed: true, decimal: true),
            textDirection: TextDirection.ltr,
            decoration: InputDecoration(
                labelText: s.t('amount', {'currency': widget.row.currency}),
                hintText: '-45.00'),
            validator: (v) {
              final value = signedDecimal(v ?? '');
              if (value == null) return s.t('amountBad');
              if ((double.tryParse(value) ?? 0) == 0) return s.t('amountZero');
              return null;
            },
          ),
          TextFormField(
            key: const Key('tx-edit-description'),
            controller: _description,
            enabled: !_busy,
            maxLength: 500,
            decoration: InputDecoration(labelText: s.t('description')),
            validator: (v) =>
                (v ?? '').trim().isEmpty ? s.t('descriptionBad') : null,
          ),
          if (_error != null)
            Semantics(
              liveRegion: true,
              child: Text(_error!,
                  key: const Key('tx-edit-error'),
                  style: TextStyle(color: p.stop)),
            ),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
              key: const Key('tx-edit-save'),
              onPressed: _busy ? null : _save,
              icon: const Icon(Icons.save_outlined),
              label: Text(s.t(_busy ? 'saving' : 'save')),
            ),
            TextButton(
              onPressed: _busy ? null : () => Navigator.of(context).pop(false),
              child: Text(s.t('cancel')),
            ),
          ]),
        ]),
      ),
    );
  }
}
