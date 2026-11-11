import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';
import 'finance_personal_models.dart';
import 'finance_tags_screens.dart' show pickPayment;

/// One payment split across loans with one person
/// (docs/finance.md#lent-and-borrowed, `POST /people-loans/split`): pick the
/// payment, then an amount per open loan with that person in its currency,
/// each starting at what is due on it. The screen says what the API would
/// refuse before sending (fewer than two parts, a part over what a loan
/// expects, parts over what is left of the payment); the API checks again
/// and makes every part or none.

/// What is due on a loan now: what is overdue, else the next repayment,
/// else what is left.
String dueNow(PersonLoan loan) {
  if (loan.hasOverdue) return loan.overdueAmount;
  if ((double.tryParse(loan.nextDueAmount) ?? 0) > 0) {
    return loan.nextDueAmount;
  }
  return loan.outstanding;
}

/// Cents of a plain decimal string, to compare amounts exactly.
int? cents(String? value) {
  if (value == null) return null;
  final parts = value.trim().split('.');
  final whole = int.tryParse(parts[0].isEmpty ? '0' : parts[0]);
  if (whole == null) return null;
  final fraction = parts.length > 1 ? parts[1].padRight(2, '0') : '00';
  if (fraction.length > 2 || int.tryParse(fraction) == null) return null;
  return whole * 100 + int.parse(fraction);
}

String centsText(int value) =>
    '${value ~/ 100}.${(value % 100).toString().padLeft(2, '0')}';

class SplitPaymentScreen extends StatefulWidget {
  const SplitPaymentScreen(
      {required this.repository, required this.loan, super.key});
  final FinanceMoreRepository repository;

  /// The loan the split was opened from: its person, currency and
  /// direction choose the loans offered.
  final PersonLoan loan;

  @override
  State<SplitPaymentScreen> createState() => _SplitPaymentScreenState();
}

class _SplitPaymentScreenState extends State<SplitPaymentScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<PersonLoan> _loans = const [];
  final _amounts = <String, TextEditingController>{};
  LoanCandidate? _payment;
  String? _problem;
  bool _saving = false;
  final _key = newIdempotencyKey('split');

  PersonLoan get _from => widget.loan;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final c in _amounts.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final all = await widget.repository.loansWith(_from.person);
      if (!mounted) return;
      final person = _from.person.trim().toLowerCase();
      final loans = [
        for (final l in all)
          if (!l.settled &&
              l.person.trim().toLowerCase() == person &&
              l.currency == _from.currency &&
              l.direction == _from.direction)
            l
      ];
      for (final l in loans) {
        _amounts.putIfAbsent(
            l.id, () => TextEditingController(text: dueNow(l)));
      }
      setState(() {
        _loans = loans;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _pick() async {
    final m = FinanceMoreStrings.of(context);
    final picked = await pickPayment(context,
        title: m.t('split.pickPayment'),
        intro: m.t('split.intro', {'person': _from.person}),
        load: (q) => widget.repository.splitCandidates(
            direction: _from.direction == LoanDirection.lent ? 'in' : 'out',
            currency: _from.currency,
            query: q));
    if (picked != null && mounted) {
      setState(() {
        _payment = picked;
        _problem = null;
      });
    }
  }

  /// The parts to send, or null with [_problem] set.
  Map<String, String>? _parts(FinanceMoreStrings m) {
    final parts = <String, String>{};
    var total = 0;
    for (final l in _loans) {
      final typed = _amounts[l.id]!.text;
      if (typed.trim().isEmpty) continue;
      final plain = decimalText(typed);
      final value = cents(plain);
      if (plain == null || value == null || value <= 0) {
        _problem = m.t('split.badAmount');
        return null;
      }
      final expected = cents(l.outstanding) ?? 0;
      if (value > expected) {
        _problem = m.t('split.overLoan', {
          'loan': _loanName(l),
          'amount': formatAmount(l.outstanding, l.currency),
        });
        return null;
      }
      total += value;
      parts[l.id] = centsText(value);
    }
    if (parts.length < 2) {
      _problem = m.t('split.fewParts');
      return null;
    }
    final left = cents(_payment?.remaining) ?? 0;
    if (total > left) {
      _problem = m.t('split.overLeft');
      return null;
    }
    _problem = null;
    return parts;
  }

  String _loanName(PersonLoan l) =>
      '${formatAmount(l.amount, l.currency)}, ${l.lentOn}';

  Future<void> _save() async {
    final m = FinanceMoreStrings.of(context);
    final payment = _payment;
    if (payment == null) return;
    final parts = _parts(m);
    setState(() {});
    if (parts == null) return;
    setState(() => _saving = true);
    try {
      await widget.repository.splitLoans(
          canonicalId: payment.canonicalId,
          kind: 'repayment',
          parts: parts,
          idempotencyKey: _key);
      if (!mounted) return;
      recordsToast(context, m.t('split.done', {'n': parts.length}));
      Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) {
        setState(() => _problem = RecordsFailure.from(error).message);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('split.title'))),
      body: AnumPage(onRefresh: _load, children: [
        Text(m.t('split.intro', {'person': _from.person}),
            style: TextStyle(color: context.palette.muted)),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else if (_loans.length < 2)
          AnumEmpty(
              icon: Icons.call_split,
              title: m.t('split.title'),
              message: m.t('split.needTwo', {'currency': _from.currency}))
        else
          ..._form(context, m),
      ]),
    );
  }

  List<Widget> _form(BuildContext context, FinanceMoreStrings m) {
    final payment = _payment;
    final p = context.palette;
    var total = 0;
    for (final c in _amounts.values) {
      total += cents(decimalText(c.text)) ?? 0;
    }
    return [
      if (payment == null)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('split-pick'),
            onPressed: _pick,
            icon: const Icon(Icons.receipt_long_outlined),
            label: Text(m.t('split.pickPayment')),
          ),
        )
      else ...[
        AnumSurface(
          child: RecordsRowAction(
            action: TextButton(
                key: const Key('split-change'),
                onPressed: _pick,
                child: Text(m.t('split.change'))),
            row: AnumRow(
              key: const Key('split-payment'),
              icon: Icons.receipt_long_outlined,
              title: m.t('split.payment', {
                'date': payment.postedOn,
                'merchant': payment.merchant,
                'amount': formatAmount(payment.amount, payment.currency),
              }),
              subtitle: m.t('split.left', {
                'amount': formatAmount(payment.remaining, payment.currency)
              }),
            ),
          ),
        ),
        for (final l in _loans)
          TextField(
            key: Key('split-part-${l.id}'),
            controller: _amounts[l.id],
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (_) => setState(() => _problem = null),
            decoration: InputDecoration(
              labelText: m.t('split.partFor', {'loan': _loanName(l)}),
              helperText: m.t('split.partHelp',
                  {'amount': formatAmount(dueNow(l), l.currency)}),
              helperMaxLines: 3,
            ),
          ),
        Text(
            m.t('split.total', {
              'amount': formatAmount(centsText(total), _from.currency),
              'left': formatAmount(payment.remaining, payment.currency),
            }),
            key: const Key('split-total')),
        if (_problem != null)
          Semantics(
            liveRegion: true,
            child: Text(_problem!,
                key: const Key('split-problem'),
                style: TextStyle(color: p.stop)),
          ),
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: FilledButton(
            key: const Key('split-save'),
            onPressed: _saving ? null : _save,
            child: Text(_saving
                ? KitStrings.of(context).t('saving')
                : m.t('split.save')),
          ),
        ),
      ],
    ];
  }
}
