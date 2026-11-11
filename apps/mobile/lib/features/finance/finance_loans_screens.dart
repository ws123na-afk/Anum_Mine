import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';

/// Finance, Debts, Bank loans on the phone (docs/finance.md#bank-loans):
/// every loan with terms, its whole schedule with each instalment's state,
/// the totals, the payments accepted at once (with Undo), a pick for an
/// instalment more than one debit fits, and adding, editing or removing
/// the terms. Nothing here pays a lender.

AnumTone _loanTone(BankLoanTotals t) => t.status != 'open'
    ? AnumTone.neutral
    : t.missing > 0 || t.monthsAhead < 0
        ? AnumTone.warn
        : t.ambiguous > 0
            ? AnumTone.warn
            : AnumTone.ok;

AnumTone _rowTone(String status) => switch (status) {
      'paid' || 'settled' => AnumTone.ok,
      'missed' => AnumTone.stop,
      'ambiguous' || 'part_paid' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String _pace(FinanceMoreStrings m, BankLoanTotals t) => t.monthsAhead > 0
    ? m.t('loan.ahead', {'n': t.monthsAhead})
    : t.monthsAhead < 0
        ? m.t('loan.behind', {'n': -t.monthsAhead})
        : m.t('loan.onTrack');

class BankLoansScreen extends StatefulWidget {
  const BankLoansScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<BankLoansScreen> createState() => _BankLoansScreenState();
}

class _BankLoansScreenState extends State<BankLoansScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<BankLoanSummary> _loans = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final loans = await widget.repository.bankLoans();
      if (!mounted) return;
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

  Future<void> _open(String debtId) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) =>
            BankLoanScreen(repository: widget.repository, debtId: debtId)));
    if (mounted) await _load();
  }

  /// Picks a debt without terms, then opens the terms form for it.
  Future<void> _add() async {
    final m = FinanceMoreStrings.of(context);
    final List<DebtChoice> debts;
    try {
      final taken = {for (final l in _loans) l.debtId};
      debts = [
        for (final d in await widget.repository.debts())
          if (!taken.contains(d.id)) d
      ];
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
      return;
    }
    if (!mounted) return;
    if (debts.isEmpty) {
      recordsToast(context, m.t('loan.noDebts'));
      return;
    }
    final picked = await showRecordsSheet<DebtChoice>(
      context,
      (sheet) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(m.t('loan.add'), style: Theme.of(sheet).textTheme.titleLarge),
          for (final d in debts)
            ListTile(
              key: Key('loan-debt-${d.id}'),
              contentPadding: EdgeInsets.zero,
              title: Text(d.name),
              subtitle: Text(d.currency),
              onTap: () => Navigator.pop(sheet, d),
            ),
        ],
      ),
    );
    if (picked == null || !mounted) return;
    final saved = await openLoanTermsForm(context, widget.repository,
        debtId: picked.id, currency: picked.currency);
    if (saved && mounted) await _open(picked.id);
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('loan.title'))),
      body: AnumPage(onRefresh: _load, children: [
        Text(m.t('loan.intro'), style: muted),
        if (_phase == RecordsPhase.ready)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              key: const Key('loan-add'),
              onPressed: _add,
              icon: const Icon(Icons.add),
              label: Text(m.t('loan.add')),
            ),
          ),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else if (_loans.isEmpty)
          AnumEmpty(
              icon: Icons.account_balance_outlined,
              title: m.t('loan.empty'),
              message: m.t('loan.emptyText'),
              action: m.t('loan.add'),
              onAction: _add)
        else
          AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final l in _loans)
                AnumRow(
                  key: Key('loan-${l.debtId}'),
                  icon: Icons.account_balance_outlined,
                  tone: _loanTone(l.totals),
                  title: l.name,
                  subtitle: [
                    if (l.lender != null && l.lender!.isNotEmpty) l.lender!,
                    m.t('loan.status.${l.totals.status}'),
                    m.t('loan.repay.${l.repayment}'),
                  ].join(' · '),
                  detail: [
                    m.t('loan.left',
                        {'amount': formatAmount(l.totals.balance, l.currency)}),
                    if (l.totals.status == 'open' && l.totals.nextDueOn != null)
                      m.t('loan.next', {
                        'amount': formatAmount(l.totals.nextAmount, l.currency),
                        'date': ltrValue(context, l.totals.nextDueOn!),
                      }),
                    if (l.totals.status == 'open') _pace(m, l.totals),
                    if (l.totals.found > 0)
                      m.t('loan.found', {'n': l.totals.found}),
                    if (l.totals.ambiguous > 0)
                      m.t('loan.ambiguous', {'n': l.totals.ambiguous}),
                  ].join(' · '),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _open(l.debtId),
                ),
            ]),
          ),
      ]),
    );
  }
}

/// The loan terms form (`PUT /debts/{id}/loan`), filled from [terms] when
/// editing. True once saved.
Future<bool> openLoanTermsForm(
    BuildContext context, FinanceMoreRepository repository,
    {required String debtId,
    required String currency,
    BankLoanTerms? terms,
    void Function(BankLoan loan)? onSaved}) async {
  final m = FinanceMoreStrings.of(context);
  List<AccountChoice> accounts;
  try {
    accounts = [
      for (final a in await repository.accounts())
        if ((a.kind == 'bank' || a.kind == 'cash') && a.currency == currency) a
    ];
  } on Object {
    // The account list is only needed for a bank debit; the API says so if
    // one is missing.
    accounts = const [];
  }
  if (!context.mounted) return false;
  return openRecordsForm(
    context,
    RecordsForm(
      title: terms == null ? m.t('loan.add') : m.t('loan.editTerms'),
      intro: m.t('loan.t.installmentHelp'),
      submitLabel: m.t('loan.saveTerms'),
      fields: [
        RecordsInput.text('lender', m.t('loan.t.lender'),
            initial: terms?.lender, maxLength: 120),
        RecordsInput.date('start_on', m.t('loan.t.startOn'),
            required: true, initial: terms?.startOn),
        RecordsInput.number('amount', '${m.t('loan.t.amount')} ($currency)',
            required: true, initial: terms?.amount),
        RecordsInput.choice('rate_type', m.t('loan.t.rateType'),
            options: {
              'reducing': m.t('loan.rate.reducing'),
              'flat': m.t('loan.rate.flat'),
            },
            initial: terms?.rateType ?? 'reducing'),
        RecordsInput.number('rate', m.t('loan.t.rate'),
            initial: terms?.rate, help: m.t('loan.t.rateHelp')),
        RecordsInput.text('term_months', m.t('loan.t.term'),
            initial: terms?.termMonths?.toString()),
        RecordsInput.number(
            'installment', '${m.t('loan.t.installment')} ($currency)',
            initial: terms?.installment),
        RecordsInput.text('payment_day', m.t('loan.t.paymentDay'),
            initial: terms?.paymentDay?.toString()),
        RecordsInput.choice('repayment', m.t('loan.t.repayment'),
            options: {
              for (final r in const [
                'bank_debit',
                'salary_deduction',
                'manual'
              ])
                r: m.t('loan.repay.$r'),
            },
            initial: terms?.repayment ?? 'manual'),
        RecordsInput.choice('account_id', m.t('loan.t.account'),
            required: false,
            options: {
              '': m.t('loan.noAccount'),
              for (final a in accounts) a.id: a.name,
            },
            initial: terms?.accountId ?? ''),
        RecordsInput.choice('status', m.t('loan.t.status'),
            options: {
              for (final s in const ['open', 'paid_off', 'closed'])
                s: m.t('loan.status.$s'),
            },
            initial: terms?.status ?? 'open'),
        RecordsInput.date('closed_on', m.t('loan.t.closedOn'),
            initial: terms?.closedOn),
      ],
      onSubmit: (v) async {
        int? whole(String key, int max, String problem) {
          final text = decimalText((v[key] as String?) ?? '');
          if ((v[key] as String?) == null) return null;
          final value = int.tryParse(text ?? '');
          if (value == null || value < 1 || value > max) {
            throw FormatException(m.t(problem));
          }
          return value;
        }

        final term = whole('term_months', 600, 'loan.badTerm');
        final day = whole('payment_day', 31, 'loan.badDay');
        if (term == null && v['installment'] == null) {
          throw FormatException(m.t('loan.needTerm'));
        }
        final account = v['account_id'] as String?;
        final loan = await repository.saveLoanTerms(debtId, {
          'lender': v['lender'],
          'start_on': v['start_on'],
          'amount': v['amount'],
          'rate_type': v['rate_type'] ?? 'reducing',
          'rate': v['rate'],
          'term_months': term,
          'installment': v['installment'],
          'payment_day': day,
          'repayment': v['repayment'] ?? 'manual',
          'account_id': account == null || account.isEmpty ? null : account,
          'status': v['status'] ?? 'open',
          'closed_on': v['status'] == 'open' ? null : v['closed_on'],
        });
        onSaved?.call(loan);
      },
    ),
  );
}

/// One loan: totals, terms, every instalment, the accepted batches.
class BankLoanScreen extends StatefulWidget {
  const BankLoanScreen(
      {required this.repository, required this.debtId, super.key});
  final FinanceMoreRepository repository;
  final String debtId;

  @override
  State<BankLoanScreen> createState() => _BankLoanScreenState();
}

class _BankLoanScreenState extends State<BankLoanScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  BankLoan? _loan;
  bool _all = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final loan = await widget.repository.bankLoan(widget.debtId);
      if (!mounted) return;
      setState(() {
        _loan = loan;
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

  Future<void> _act(Future<void> Function() work, String done) async {
    try {
      await work();
      if (!mounted) return;
      recordsToast(context, done);
      await _load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _edit(BankLoan loan) async {
    final m = FinanceMoreStrings.of(context);
    final saved = await openLoanTermsForm(context, widget.repository,
        debtId: loan.debtId,
        currency: loan.currency,
        terms: loan.terms,
        onSaved: (fresh) => setState(() => _loan = fresh));
    if (saved && mounted) recordsToast(context, m.t('loan.saved'));
  }

  Future<void> _remove(BankLoan loan) async {
    final m = FinanceMoreStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('loan.remove.title'),
        message: m.t('loan.remove.text'),
        action: m.t('loan.removeTerms'))) {
      return;
    }
    try {
      await widget.repository.removeLoanTerms(loan.debtId);
      if (!mounted) return;
      recordsToast(context, m.t('loan.removed'));
      Navigator.of(context).pop();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _undo(BankLoanBatch batch) async {
    final m = FinanceMoreStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('loan.batch.undo.title'),
        message: m.t('loan.batch.undo.text'),
        action: m.t('loan.batch.undo'))) {
      return;
    }
    await _act(() => widget.repository.undoLoanBatch(batch.id),
        m.t('loan.batch.undoneToast'));
  }

  Future<void> _pick(BankLoan loan, BankLoanRow row) async {
    final m = FinanceMoreStrings.of(context);
    final picked = await showRecordsSheet<BankLoanFit>(
      context,
      (sheet) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(m.t('loan.pick.title', {'n': row.number}),
              style: Theme.of(sheet).textTheme.titleLarge),
          const SizedBox(height: AnumSpacing.xs),
          Text(m.t('loan.pick.text'),
              style: TextStyle(color: sheet.palette.muted)),
          for (final fit in row.fits)
            ListTile(
              key: Key('loan-fit-${fit.canonicalId}'),
              contentPadding: EdgeInsets.zero,
              title: Text(fit.merchant.isEmpty ? fit.postedOn : fit.merchant),
              subtitle: Text(
                  '${ltrValue(sheet, fit.postedOn)} · ${formatAmount(fit.amount, fit.currency)}'),
              onTap: () => Navigator.pop(sheet, fit),
            ),
        ],
      ),
    );
    if (picked == null || !mounted) return;
    await _act(
        () => widget.repository.pickLoanPayment(loan.debtId, picked.canonicalId,
            idempotencyKey: newIdempotencyKey('loan')),
        m.t('loan.picked'));
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoreStrings.of(context);
    final loan = _loan;
    return Scaffold(
      appBar: AppBar(title: Text(loan?.name ?? m.t('loan.title'))),
      body: AnumPage(onRefresh: _load, children: [
        if (_phase != RecordsPhase.ready || loan == null)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: m.t('denied'),
              onRetry: _load)
        else
          ..._body(context, loan),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, BankLoan loan) {
    final m = FinanceMoreStrings.of(context);
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    final c = loan.currency;
    final t = loan.totals;
    final terms = loan.terms;
    String money(String? v) =>
        v == null ? m.t('loan.unknown') : formatAmount(v, c);
    final rows = _all ? loan.rows : loan.rows.take(12).toList();
    return [
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        AnumPill(label: m.t('loan.status.${t.status}'), tone: _loanTone(t)),
        if (t.status == 'open') AnumPill(label: _pace(m, t)),
        if (t.found > 0)
          AnumPill(label: m.t('loan.found', {'n': t.found}), tone: AnumTone.ok),
        if (t.missing > 0)
          AnumPill(
              label: m.t('loan.missing', {'n': t.missing}),
              tone: AnumTone.warn),
        if (t.ambiguous > 0)
          AnumPill(
              label: m.t('loan.ambiguous', {'n': t.ambiguous}),
              tone: AnumTone.warn),
      ]),
      if (t.neverEnds) Text(m.t('loan.never'), style: TextStyle(color: p.stop)),
      Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
        FilledButton.icon(
          key: const Key('loan-edit'),
          onPressed: () => _edit(loan),
          icon: const Icon(Icons.edit_outlined),
          label: Text(m.t('loan.editTerms')),
        ),
        OutlinedButton.icon(
          key: const Key('loan-remove'),
          onPressed: () => _remove(loan),
          icon: const Icon(Icons.delete_outline),
          label: Text(m.t('loan.removeTerms')),
        ),
      ]),
      RecordsSection(title: m.t('loan.summary'), children: [
        RecordsField(m.t('loan.f.installment'), formatAmount(t.installment, c)),
        RecordsField(
            m.t('loan.f.totalPayable'), formatAmount(t.totalPayable, c)),
        RecordsField(m.t('loan.f.totalInterest'), money(t.totalInterest)),
        RecordsField(m.t('loan.f.paid'), formatAmount(t.paid, c)),
        RecordsField(m.t('loan.f.interestPaid'), money(t.interestPaid)),
        RecordsField(m.t('loan.f.balance'), formatAmount(t.balance, c)),
        RecordsField(m.t('loan.f.payoff'), t.payoffOn ?? '-'),
        RecordsField(m.t('loan.f.progress'),
            m.t('loan.progress', {'paid': t.paidCount, 'due': t.dueCount})),
        if (!t.splitKnown) Text(m.t('loan.basis.plain'), style: muted),
      ]),
      RecordsSection(title: m.t('loan.terms'), children: [
        RecordsField(m.t('loan.t.lender'), terms.lender),
        RecordsField(m.t('loan.t.startOn'), terms.startOn),
        RecordsField(m.t('loan.t.amount'), formatAmount(terms.amount, c)),
        RecordsField(
            m.t('loan.t.rateType'), m.t('loan.rate.${terms.rateType}')),
        RecordsField(m.t('loan.t.rate'), terms.rate),
        RecordsField(m.t('loan.t.term'), terms.termMonths?.toString()),
        RecordsField(m.t('loan.t.paymentDay'), terms.paymentDay?.toString()),
        RecordsField(
            m.t('loan.t.repayment'), m.t('loan.repay.${terms.repayment}')),
        if (terms.closedOn != null)
          RecordsField(m.t('loan.t.closedOn'), terms.closedOn),
      ]),
      AnumHeader(title: m.t('loan.schedule')),
      AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final row in rows)
            RecordsRowAction(
              action: row.fits.isEmpty
                  ? null
                  : TextButton(
                      key: Key('loan-pick-${row.number}'),
                      onPressed: () => _pick(loan, row),
                      child: Text(m.t('loan.pick'))),
              row: AnumRow(
                key: Key('loan-row-${row.number}'),
                icon: Icons.event_outlined,
                tone: _rowTone(row.status),
                title: m.t('loan.row',
                    {'n': row.number, 'date': ltrValue(context, row.dueOn)}),
                subtitle: [
                  formatAmount(row.payment, c),
                  m.t('loan.st.${row.status}'),
                ].join(' · '),
                detail: [
                  if (row.principal != null && row.interest != null)
                    m.t('loan.row.split', {
                      'principal': formatAmount(row.principal!, c),
                      'interest': formatAmount(row.interest!, c),
                    }),
                  if (double.tryParse(row.paid) != 0)
                    m.t('loan.row.paid', {'amount': formatAmount(row.paid, c)}),
                  m.t('loan.row.after',
                      {'amount': formatAmount(row.balanceAfter, c)}),
                ].join(' · '),
              ),
            ),
        ]),
      ),
      if (!_all && loan.rows.length > 12)
        Align(
          alignment: AlignmentDirectional.center,
          child: OutlinedButton(
            key: const Key('loan-all'),
            onPressed: () => setState(() => _all = true),
            child: Text(m.t('loan.showAll', {'n': loan.rows.length})),
          ),
        ),
      AnumHeader(title: m.t('loan.batches')),
      if (loan.batches.isEmpty)
        Text(m.t('loan.batches.none'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final b in loan.batches)
              RecordsRowAction(
                action: b.undone
                    ? null
                    : TextButton(
                        key: Key('loan-undo-${b.id}'),
                        onPressed: () => _undo(b),
                        child: Text(m.t('loan.batch.undo'))),
                row: AnumRow(
                  key: Key('loan-batch-${b.id}'),
                  icon: Icons.playlist_add_check,
                  tone: b.undone ? AnumTone.neutral : AnumTone.ok,
                  title: m.t('loan.batch', {
                    'count': b.count,
                    'amount': formatAmount(b.amount, b.currency ?? c),
                  }),
                  subtitle: recordsDateTime(context, b.createdAt),
                  detail: b.undone ? m.t('loan.batch.undone') : null,
                ),
              ),
          ]),
        ),
    ];
  }
}
