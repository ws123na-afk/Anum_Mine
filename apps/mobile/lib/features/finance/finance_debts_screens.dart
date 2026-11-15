import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_debts_data.dart';
import 'finance_debts_strings.dart';
import 'finance_models.dart';

/// Finance on the phone: the debt overview, one obligation with its
/// figures, sources, payments and promises, the payment calendar, credit
/// cards, financial alerts and the advisor's answers
/// (docs/finance.md#debt-overview, #credit-cards, #financial-alerts,
/// #advisor-answers). Every figure is the API's; a provider's "paid" is
/// reported, never paid, until a bank payment is matched. Nothing here
/// moves money.

AnumTone _statusTone(String status) => switch (status) {
      'overdue' => AnumTone.stop,
      'disputed' || 'due_soon' => AnumTone.warn,
      'paid_off' || 'closed' => AnumTone.ok,
      _ => AnumTone.info,
    };

AnumTone _evidenceTone(String evidence) => switch (evidence) {
      'verified' || 'posted' || 'paid' => AnumTone.ok,
      'reported' ||
      'provisional' ||
      'pending' ||
      'review' ||
      'reported_paid' =>
        AnumTone.warn,
      'overdue' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

/// Loads one thing for a screen with the four states every page shows.
mixin _Loader<T extends StatefulWidget, V> on State<T> {
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  V? value;

  Future<V> fetch();

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() => phase = RecordsPhase.loading);
    try {
      final next = await fetch();
      if (!mounted) return;
      setState(() {
        value = next;
        phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final found = RecordsFailure.from(error);
      setState(() {
        failure = found;
        phase = found.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Widget states(FinanceDebtsStrings s) => RecordsStateView(
      phase: phase, failure: failure, deniedHint: s.t('denied'), onRetry: load);
}

Widget _section(BuildContext context, String title, List<Widget> children) =>
    Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
        child: Text(title, style: Theme.of(context).textTheme.titleMedium),
      ),
      AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch, children: children),
      ),
    ]);

// -- the overview -------------------------------------------------------------

class DebtOverviewScreen extends StatefulWidget {
  const DebtOverviewScreen({required this.repository, super.key});
  final FinanceDebtsRepository repository;

  @override
  State<DebtOverviewScreen> createState() => _DebtOverviewScreenState();
}

class _DebtOverviewScreenState extends State<DebtOverviewScreen>
    with _Loader<DebtOverviewScreen, DebtOverview> {
  @override
  Future<DebtOverview> fetch() => widget.repository.overview();

  Future<void> _open(Obligation item) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) =>
            ObligationScreen(repository: widget.repository, item: item)));
    if (mounted) await load();
  }

  Widget _row(FinanceDebtsStrings s, Obligation o) => AnumRow(
        key: Key('debt-${o.id}'),
        icon: o.owe ? Icons.account_balance_outlined : Icons.handshake_outlined,
        tone: o.discrepancy ? AnumTone.warn : _statusTone(o.status),
        title: o.name,
        subtitle: [
          s.label('type', o.type),
          s.label('status', o.status),
          if (o.discrepancy) s.t('overview.lenderDiffers'),
        ].join(' · '),
        detail: [
          s.t('overview.left',
              {'amount': formatAmount(o.remaining, o.currency)}),
          if (o.nextDueOn != null && o.open)
            s.t('overview.next', {
              'amount': formatAmount(o.nextDueAmount, o.currency),
              'date': ltrValue(context, o.nextDueOn!),
            }),
          if (double.tryParse(o.unverifiedAmount) case final u? when u > 0)
            s.t('overview.unverified',
                {'amount': formatAmount(o.unverifiedAmount, o.currency)}),
        ].join(' · '),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => _open(o),
      );

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final view = value;
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('menu.debts'))),
      body: AnumPage(onRefresh: load, children: [
        Text(s.t('overview.intro'), style: muted),
        if (phase != RecordsPhase.ready || view == null)
          states(s)
        else if (view.items.isEmpty)
          AnumEmpty(
              icon: Icons.account_balance_outlined,
              title: s.t('overview.empty'),
              message: s.t('overview.emptyText'))
        else ...[
          AnumMetricGrid(children: [
            for (final total in view.totals) ...[
              AnumMetric(
                icon: Icons.account_balance_outlined,
                label: s.t('overview.owe', {'currency': total.currency}),
                value: formatAmount(total.owe, total.currency),
                caption: (double.tryParse(total.overdue) ?? 0) > 0
                    ? s.t('overview.overdue',
                        {'amount': formatAmount(total.overdue, total.currency)})
                    : s.t('overview.due30',
                        {'amount': formatAmount(total.due30, total.currency)}),
              ),
              if ((double.tryParse(total.owedToMe) ?? 0) > 0)
                AnumMetric(
                  icon: Icons.handshake_outlined,
                  label: s.t('overview.owedToMe', {'currency': total.currency}),
                  value: formatAmount(total.owedToMe, total.currency),
                ),
            ],
          ]),
          if (view.discrepancies > 0)
            AnumPill(
                tone: AnumTone.warn,
                icon: Icons.balance_outlined,
                label: s.t('overview.differs', {'count': view.discrepancies})),
          if (view.owe.isNotEmpty)
            _section(context, s.t('overview.oweTitle'),
                [for (final o in view.owe) _row(s, o)]),
          if (view.owedToMe.isNotEmpty)
            _section(context, s.t('overview.owedTitle'),
                [for (final o in view.owedToMe) _row(s, o)]),
          if (view.plan.isNotEmpty)
            _section(context, s.t('overview.planTitle'), [
              for (final p in view.plan)
                AnumRow(
                  key: Key('plan-${p.obligationId}'),
                  icon: Icons.format_list_numbered,
                  title: '${p.currency} #${p.rank}  ${p.name}',
                  subtitle: s.t('reason.${p.reason}', {'rate': p.rate ?? ''}),
                  detail: formatAmount(p.remaining, p.currency),
                ),
            ]),
          if (view.finished.isNotEmpty)
            _section(context, s.t('overview.finishedTitle'),
                [for (final o in view.finished) _row(s, o)]),
        ],
      ]),
    );
  }
}

class ObligationScreen extends StatefulWidget {
  const ObligationScreen(
      {required this.repository, required this.item, super.key});
  final FinanceDebtsRepository repository;
  final Obligation item;

  @override
  State<ObligationScreen> createState() => _ObligationScreenState();
}

class _ObligationScreenState extends State<ObligationScreen> {
  late Obligation _item = widget.item;

  Future<void> _reload() async {
    try {
      final view = await widget.repository.overview();
      final fresh = view.items.where((o) => o.id == _item.id);
      if (mounted && fresh.isNotEmpty) setState(() => _item = fresh.first);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _editTerms() async {
    final s = FinanceDebtsStrings.of(context);
    final o = _item;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('detail.terms'),
        intro: s.t('profile.intro'),
        submitLabel: s.t('profile.save'),
        fields: [
          RecordsInput.number(
              'verified_balance', '${s.t('profile.lender')} (${o.currency})',
              initial: o.verifiedBalance),
          RecordsInput.date('verified_on', s.t('profile.lenderOn'),
              initial: o.verifiedOn),
          RecordsInput.number(
              'disputed_amount', '${s.t('profile.disputed')} (${o.currency})',
              initial: o.disputedAmount),
          RecordsInput.number('fees', '${s.t('profile.fees')} (${o.currency})',
              initial: o.fees),
        ],
        onSubmit: (v) => widget.repository.saveProfile(o.refId, {
          'debt_type': o.type == 'borrowed_person' || o.type == 'lent_person'
              ? null
              : o.type,
          'verified_balance': v['verified_balance'],
          'verified_on': v['verified_on'],
          'disputed_amount': v['disputed_amount'],
          'fees': v['fees'],
        }),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, s.t('profile.saved'));
      await _reload();
    }
  }

  Future<void> _addPromise() async {
    final s = FinanceDebtsStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('promise.add'),
        submitLabel: s.t('promise.add'),
        fields: [
          RecordsInput.number(
              'amount', s.t('promise.amount', {'currency': _item.currency}),
              required: true),
          RecordsInput.date('due_by', s.t('promise.dueBy'), required: true),
          RecordsInput.text('note', s.t('promise.note'), maxLength: 300),
        ],
        onSubmit: (v) => widget.repository.addPromise(_item.refId, {
          'amount': v['amount'],
          'due_by': v['due_by'],
          'note': v['note'],
        }),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, s.t('promise.added'));
      await _reload();
    }
  }

  Future<void> _agreed() async {
    final s = FinanceDebtsStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('agreed.title'),
        submitLabel: s.t('profile.save'),
        fields: [
          RecordsInput.number(
              'balance', s.t('agreed.label', {'currency': _item.currency}),
              initial: _item.verifiedBalance),
        ],
        onSubmit: (v) => widget.repository
            .saveAgreedBalance(_item.refId, v['balance'] as String?),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, s.t('agreed.saved'));
      await _reload();
    }
  }

  Future<void> _removePromise(DebtPromise p) async {
    final s = FinanceDebtsStrings.of(context);
    try {
      await widget.repository.removePromise(_item.refId, p.id);
      if (!mounted) return;
      recordsToast(context, s.t('promise.removed'));
      await _reload();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final o = _item;
    final c = o.currency;
    String money(String value) => formatAmount(value, c);
    final figures = <(String, String)>[
      if (o.originalAmount != null)
        (s.t('detail.original'), money(o.originalAmount!)),
      (s.t('detail.paid'), money(o.paid)),
      (s.t('detail.left'), money(o.remaining)),
      if (o.expectedInstallment != null)
        (s.t('detail.installment'), money(o.expectedInstallment!)),
      (
        s.t('detail.rate'),
        o.rate != null
            ? '${o.rate}%'
            : s.t(o.interest == 'interest_free'
                ? 'detail.interestFree'
                : 'detail.rateUnknown')
      ),
      if (o.interestPaid != null)
        (s.t('detail.interestPaid'), money(o.interestPaid!)),
      if (o.fees != null) (s.t('detail.fees'), money(o.fees!)),
      if ((double.tryParse(o.overdueAmount) ?? 0) > 0)
        (s.t('detail.overdue'), money(o.overdueAmount)),
      if (o.payoffOn != null)
        (s.t('detail.payoff'), ltrValue(context, o.payoffOn!)),
      if (o.verifiedBalance != null)
        (
          s.t('detail.lender',
              {'date': ltrValue(context, o.verifiedOn ?? '-')}),
          money(o.verifiedBalance!)
        ),
      if (o.discrepancy && o.difference != null)
        (s.t('detail.difference', {'amount': ''}).trim(), money(o.difference!)),
      if (o.disputedAmount != null)
        (s.t('detail.disputed'), money(o.disputedAmount!)),
      if ((double.tryParse(o.unverifiedAmount) ?? 0) > 0)
        (s.t('detail.unverified'), money(o.unverifiedAmount)),
    ];
    final editable = o.refType == 'debt';
    final person = o.refType == 'person_loan';
    return Scaffold(
      appBar: AppBar(title: Text(o.name)),
      body: AnumPage(onRefresh: _reload, children: [
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          AnumPill(label: s.label('type', o.type)),
          AnumPill(
              label: s.label('status', o.status), tone: _statusTone(o.status)),
        ]),
        AnumSurface(
          child: Column(children: [
            for (final (label, text) in figures)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
                child: Row(children: [
                  Expanded(child: Text(label)),
                  Text(text,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                ]),
              ),
          ]),
        ),
        _section(context, s.t('detail.sources'), [
          for (final e in o.sources.entries)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
              child: Text(
                  '${s.label('figure', e.key)}: ${s.label('source', e.value)}'),
            ),
          for (final a in o.assumptions)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
              child: Text(a),
            ),
        ]),
        if (o.missing.isNotEmpty)
          _section(context, s.t('detail.missing'),
              [for (final m in o.missing) Text(s.label('missing', m))]),
        _section(context, s.t('detail.history'), [
          if (o.history.isEmpty) Text(s.t('detail.noHistory')),
          for (final h in o.history)
            AnumRow(
              icon: Icons.payments_outlined,
              tone: _evidenceTone(h.evidence),
              title: formatAmount(h.amount, h.currency),
              subtitle: [
                if (h.on != null) ltrValue(context, h.on!),
                s.label('evidence', h.evidence),
                if (h.note != null) h.note!,
              ].join(' · '),
            ),
        ]),
        if (person)
          _section(context, s.t('promise.title'), [
            if (o.promises.isEmpty) Text(s.t('promise.none')),
            for (final p in o.promises)
              AnumRow(
                key: Key('promise-${p.id}'),
                icon: Icons.event_available_outlined,
                tone: p.status == 'kept'
                    ? AnumTone.ok
                    : p.status == 'broken'
                        ? AnumTone.stop
                        : AnumTone.warn,
                title: s.t('promise.by', {
                  'date': ltrValue(context, p.dueBy),
                  'amount': formatAmount(p.amount, c)
                }),
                subtitle: [
                  s.label('promise', p.status),
                  if (p.note != null) p.note!
                ].join(' · '),
                trailing: IconButton(
                  tooltip: s.t('promise.remove'),
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () => _removePromise(p),
                ),
              ),
          ]),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          if (editable)
            OutlinedButton.icon(
              key: const Key('debt-terms'),
              onPressed: _editTerms,
              icon: const Icon(Icons.edit_outlined),
              label: Text(s.t('detail.terms')),
            ),
          if (person) ...[
            FilledButton.icon(
              key: const Key('promise-add'),
              onPressed: _addPromise,
              icon: const Icon(Icons.add),
              label: Text(s.t('promise.add')),
            ),
            OutlinedButton(
              key: const Key('agreed-balance'),
              onPressed: _agreed,
              child: Text(s.t('agreed.title')),
            ),
          ],
        ]),
      ]),
    );
  }
}

// -- the payment calendar -------------------------------------------------------

class PaymentCalendarScreen extends StatefulWidget {
  const PaymentCalendarScreen({required this.repository, super.key});
  final FinanceDebtsRepository repository;

  @override
  State<PaymentCalendarScreen> createState() => _PaymentCalendarScreenState();
}

class _PaymentCalendarScreenState extends State<PaymentCalendarScreen>
    with _Loader<PaymentCalendarScreen, DebtOverview> {
  @override
  Future<DebtOverview> fetch() => widget.repository.overview();

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final view = value;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('calendar.title'))),
      body: AnumPage(onRefresh: load, children: [
        Text(s.t('calendar.intro'),
            style: TextStyle(color: context.palette.muted)),
        if (phase != RecordsPhase.ready || view == null)
          states(s)
        else if (view.calendar.isEmpty)
          AnumEmpty(
              icon: Icons.event_available_outlined,
              title: s.t('calendar.empty'),
              message: s.t('overview.intro'))
        else
          AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final c in view.calendar)
                AnumRow(
                  icon: c.direction == 'owe'
                      ? Icons.call_made
                      : Icons.call_received,
                  tone: _evidenceTone(c.status),
                  title: c.name,
                  subtitle: [
                    c.on == view.today
                        ? s.t('calendar.today')
                        : ltrValue(context, c.on),
                    s.label('cal', c.status),
                  ].join(' · '),
                  detail: formatAmount(c.amount, c.currency),
                ),
            ]),
          ),
      ]),
    );
  }
}

// -- credit cards ---------------------------------------------------------------

class CreditCardsScreen extends StatefulWidget {
  const CreditCardsScreen({required this.repository, super.key});
  final FinanceDebtsRepository repository;

  @override
  State<CreditCardsScreen> createState() => _CreditCardsScreenState();
}

class _CreditCardsScreenState extends State<CreditCardsScreen>
    with _Loader<CreditCardsScreen, List<CreditCardView>> {
  @override
  Future<List<CreditCardView>> fetch() => widget.repository.cards();

  Future<void> _terms(CreditCardView card) async {
    final s = FinanceDebtsStrings.of(context);
    String debtId;
    try {
      debtId = card.debtId ?? await widget.repository.addCardTerms(card);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
      return;
    }
    if (!mounted) return;
    final c = card.currency;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('cards.terms'),
        intro: s.t('profile.cardIntro'),
        submitLabel: s.t('profile.save'),
        fields: [
          RecordsInput.number('card_limit', '${s.t('profile.limit')} ($c)',
              initial: card.limit),
          RecordsInput.text('card_statement_day', s.t('profile.closingDay'),
              initial: card.statementDay?.toString()),
          RecordsInput.number('card_statement_balance',
              '${s.t('profile.statementBalance')} ($c)',
              initial: card.statementBalance),
          RecordsInput.date('card_statement_on', s.t('profile.statementOn'),
              initial: card.statementOn),
          RecordsInput.number(
              'card_statement_minimum', '${s.t('profile.minimum')} ($c)',
              initial: card.statementMinimum),
        ],
        onSubmit: (v) {
          final text = (v['card_statement_day'] as String?)?.trim() ?? '';
          final day =
              text.isEmpty ? null : int.tryParse(decimalText(text) ?? '');
          if (text.isNotEmpty && (day == null || day < 1 || day > 31)) {
            throw FormatException(s.t('profile.badDay'));
          }
          return widget.repository.saveProfile(debtId, {
            'debt_type': 'credit_card',
            'card_limit': v['card_limit'],
            'card_statement_day': day,
            'card_statement_balance': v['card_statement_balance'],
            'card_statement_on': v['card_statement_on'],
            'card_statement_minimum': v['card_statement_minimum'],
          });
        },
      ),
    );
    if (saved && mounted) {
      recordsToast(context, s.t('profile.saved'));
      await load();
    }
  }

  Widget _card(FinanceDebtsStrings s, CreditCardView card) {
    final c = card.currency;
    String money(String value) => formatAmount(value, c);
    final muted = TextStyle(color: context.palette.muted);
    return AnumSurface(
      key: Key('card-${card.accountId}'),
      accent: card.paymentState == 'not_paid'
          ? anumToneColor(context, AnumTone.stop)
          : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(card.last4 == null ? card.name : '${card.name} · ${card.last4}',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('cards.owed', {'amount': money(card.outstanding)})),
        Text(card.limit == null
            ? s.t('cards.noLimit')
            : s.t('cards.available', {
                'amount': money(card.available ?? '0'),
                'limit': money(card.limit!)
              })),
        if (card.dueOn != null)
          Text('${s.t('cards.due', {
                'date': ltrValue(context, card.dueOn!)
              })} · ${s.label('cards.state', card.paymentState)}'),
        const SizedBox(height: AnumSpacing.xs),
        if (card.checkStatus == 'differs')
          AnumPill(
              tone: AnumTone.warn,
              label: s.t('cards.differs', {
                'amount': money(card.checkDifference ?? '0'),
                'ledger': money(card.checkLedger ?? '0')
              })),
        if ((double.tryParse(card.paidProvisional) ?? 0) > 0)
          Text(
              s.t('cards.provisional', {'amount': money(card.paidProvisional)}),
              style: muted),
        if (card.payFull != null)
          Text(s.t('cards.payFull', {'amount': money(card.payFull!)})),
        if (card.payMinimum != null)
          Text(s.t('cards.payMinimum', {'amount': money(card.payMinimum!)})),
        if (card.interestEstimate != null)
          Text(
              s.t('cards.interest', {'amount': money(card.interestEstimate!)})),
        if (card.monthsToClear != null)
          Text(s.t('cards.months', {'n': card.monthsToClear!})),
        if (card.neverClears) Text(s.t('cards.never')),
        if (card.apr == null) Text(s.t('cards.noRate'), style: muted),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('cards.cycle'), style: Theme.of(context).textTheme.titleSmall),
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xxs, children: [
          for (final e in card.cycle.entries)
            Text('${s.label('cards.k', e.key)}: ${money(e.value)}'),
        ]),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('cards.payments'),
            style: Theme.of(context).textTheme.titleSmall),
        if (card.payments.isEmpty) Text(s.t('cards.noPayments'), style: muted),
        for (final p in card.payments.take(6))
          AnumRow(
            icon: Icons.swap_horiz,
            tone: p.linked ? AnumTone.ok : AnumTone.warn,
            title: money(p.amount),
            subtitle: [
              ltrValue(context, p.postedOn),
              p.linked
                  ? s.t('cards.linked', {'account': p.fromAccountName ?? '-'})
                  : s.t('cards.notLinked'),
              s.label('evidence', p.evidence),
            ].join(' · '),
          ),
        Text(
            s.t('cards.evidence', {
              'posted': card.evidence['posted'] ?? 0,
              'pending': card.evidence['pending'] ?? 0,
              'unverified': card.evidence['unverified'] ?? 0,
            }),
            style: muted),
        if (card.missing.isNotEmpty)
          Text(card.missing.map((m) => s.label('missing', m)).join(' '),
              style: muted),
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: OutlinedButton.icon(
            key: Key('card-terms-${card.accountId}'),
            onPressed: () => _terms(card),
            icon: const Icon(Icons.edit_outlined),
            label: Text(s.t('cards.terms')),
          ),
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final cards = value;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('cards.title'))),
      body: AnumPage(onRefresh: load, children: [
        Text(s.t('cards.intro'),
            style: TextStyle(color: context.palette.muted)),
        if (phase != RecordsPhase.ready || cards == null)
          states(s)
        else if (cards.isEmpty)
          AnumEmpty(
              icon: Icons.credit_card_outlined,
              title: s.t('cards.empty'),
              message: s.t('cards.emptyText'))
        else
          for (final card in cards) _card(s, card),
      ]),
    );
  }
}

// -- financial alerts -------------------------------------------------------------

class FinancialAlertsScreen extends StatefulWidget {
  const FinancialAlertsScreen({required this.repository, super.key});
  final FinanceDebtsRepository repository;

  @override
  State<FinancialAlertsScreen> createState() => _FinancialAlertsScreenState();
}

class _FinancialAlertsScreenState extends State<FinancialAlertsScreen>
    with _Loader<FinancialAlertsScreen, AlertSettingsView> {
  List<AlertPreviewItem>? _preview;
  bool _saving = false;

  @override
  Future<AlertSettingsView> fetch() => widget.repository.alertSettings();

  Future<void> _save() async {
    final s = FinanceDebtsStrings.of(context);
    final view = value;
    if (view == null) return;
    setState(() => _saving = true);
    try {
      final saved = await widget.repository.saveAlertSettings(view.kinds);
      if (!mounted) return;
      setState(() => value = saved.channels.isEmpty
          ? AlertSettingsView(kinds: saved.kinds, channels: view.channels)
          : saved);
      recordsToast(context, s.t('alerts.saved'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _showPreview() async {
    try {
      final items = await widget.repository.alertPreview();
      if (mounted) setState(() => _preview = items);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  void _set(
      AlertSettingsView view, AlertKindSetting kind, String channel, bool on) {
    setState(() => value = view.withKinds([
          for (final x in view.kinds)
            x.kind == kind.kind ? x.withChannel(channel, on) : x
        ]));
  }

  Widget _kindRow(BuildContext context, FinanceDebtsStrings s,
      AlertSettingsView view, AlertKindSetting k) {
    final name = s.label('alert', k.kind);
    return Padding(
      padding: const EdgeInsets.symmetric(
          horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
      child: Semantics(
        container: true,
        label: name,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(name, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                for (final c in alertChannels)
                  FilterChip(
                    key: Key('alert-${k.kind}-$c'),
                    label: Text(s.t('alerts.channel.$c')),
                    tooltip: '${s.t('alerts.channel.$c')}: $name',
                    selected: k.channel(c),
                    onSelected: _saving || (!view.usable(c) && !k.channel(c))
                        ? null
                        : (on) => _set(view, k, c, on),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final view = value;
    final why = [
      if (view != null)
        for (final c in alertChannels)
          if (view.reasonKey(c) case final key?) key
    ];
    return Scaffold(
      appBar: AppBar(title: Text(s.t('alerts.title'))),
      body: AnumPage(onRefresh: load, children: [
        Text(s.t('alerts.intro'),
            style: TextStyle(color: context.palette.muted)),
        if (phase != RecordsPhase.ready || view == null)
          states(s)
        else ...[
          for (final key in why)
            AnumRow(
              key: Key('alerts-why-${key.split('.')[2]}'),
              icon: Icons.info_outline,
              title: s.t(key),
            ),
          AnumSurface(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final (i, k) in view.kinds.indexed) ...[
                if (i > 0) const Divider(height: 1),
                _kindRow(context, s, view, k),
              ],
            ]),
          ),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton(
                key: const Key('alerts-save'),
                onPressed: _saving ? null : _save,
                child: Text(s.t('alerts.save'))),
            OutlinedButton(
                key: const Key('alerts-preview'),
                onPressed: _showPreview,
                child: Text(s.t('alerts.preview'))),
          ]),
          if (_preview != null)
            _preview!.isEmpty
                ? Text(s.t('alerts.previewNone'))
                : _section(context, s.t('alerts.preview'), [
                    for (final a in _preview!)
                      AnumRow(
                        icon: Icons.notifications_outlined,
                        title: s.label('alert', a.kind),
                        subtitle: a.body,
                      ),
                  ]),
        ],
      ]),
    );
  }
}

// -- the advisor's answers ----------------------------------------------------------

class AdvisorAskScreen extends StatefulWidget {
  const AdvisorAskScreen(
      {required this.repository, this.base = 'AED', super.key});
  final FinanceDebtsRepository repository;
  final String base;

  @override
  State<AdvisorAskScreen> createState() => _AdvisorAskScreenState();
}

class _AdvisorAskScreenState extends State<AdvisorAskScreen> {
  String _question = 'owe';
  late final Map<String, TextEditingController> _params = {
    for (final k in const [
      'category',
      'words',
      'country',
      'currency',
      'month',
      'amount'
    ])
      k: TextEditingController(text: k == 'currency' ? widget.base : ''),
  };
  RecordsPhase? _phase;
  RecordsFailure? _failure;
  AdvisorAnswer? _answer;

  @override
  void dispose() {
    for (final c in _params.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _ask() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final found = await widget.repository.answer(
          _question, {for (final e in _params.entries) e.key: e.value.text});
      if (!mounted) return;
      setState(() {
        _answer = found;
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

  @override
  Widget build(BuildContext context) {
    final s = FinanceDebtsStrings.of(context);
    final used = questionParams[_question] ?? const [];
    final answer = _answer;
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('ask.title'))),
      body: AnumPage(children: [
        Text(s.t('ask.intro'), style: muted),
        DropdownButtonFormField<String>(
          key: const Key('ask-question'),
          initialValue: _question,
          isExpanded: true,
          decoration: InputDecoration(labelText: s.t('ask.question')),
          items: [
            for (final q in advisorQuestions)
              DropdownMenuItem(value: q, child: Text(s.t('q.$q')))
          ],
          onChanged: (q) => setState(() {
            _question = q ?? _question;
            _answer = null;
            _phase = null;
          }),
        ),
        for (final key in used)
          TextField(
            key: Key('ask-$key'),
            controller: _params[key],
            keyboardType: key == 'amount'
                ? const TextInputType.numberWithOptions(decimal: true)
                : TextInputType.text,
            decoration: InputDecoration(labelText: s.t('ask.$key')),
          ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton(
            key: const Key('ask-go'),
            onPressed: _phase == RecordsPhase.loading ? null : _ask,
            child: Text(s.t('ask.go')),
          ),
        ),
        if (_phase != null && _phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase!,
              failure: _failure,
              deniedHint: s.t('denied'),
              onRetry: _ask)
        else if (answer != null) ...[
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              AnumPill(
                  tone: answer.complete ? AnumTone.ok : AnumTone.warn,
                  label:
                      s.t(answer.complete ? 'ask.complete' : 'ask.incomplete')),
              const SizedBox(height: AnumSpacing.xs),
              Text(answer.answer, key: const Key('ask-answer')),
              const SizedBox(height: AnumSpacing.xxs),
              Text(s.t('ask.asOf', {'date': ltrValue(context, answer.asOf)}),
                  style: muted),
              for (final (label, amount, currency) in answer.figures)
                Text('$label: ${formatAmount(amount, currency)}'),
            ]),
          ),
          if (answer.calculation.isNotEmpty)
            _section(context, s.t('ask.calculation'), [
              for (final line in answer.calculation)
                Text(line, textDirection: TextDirection.ltr),
            ]),
          if (answer.missing.isNotEmpty)
            _section(context, s.t('ask.missing'),
                [for (final m in answer.missing) Text(m)]),
          _section(context, s.t('ask.records', {'n': answer.records.length}), [
            for (final r in answer.records)
              AnumRow(
                icon: Icons.receipt_long_outlined,
                tone: r.evidence == null ? null : _evidenceTone(r.evidence!),
                title: r.label,
                subtitle: [
                  if (r.on != null) ltrValue(context, r.on!),
                  if (r.evidence != null)
                    s.maybe('evidence.${r.evidence}') ??
                        s.maybe('source.${r.evidence}') ??
                        r.evidence!,
                ].join(' · '),
                detail: r.amount != null && r.currency != null
                    ? formatAmount(r.amount!, r.currency!)
                    : null,
              ),
          ]),
        ],
      ]),
    );
  }
}
