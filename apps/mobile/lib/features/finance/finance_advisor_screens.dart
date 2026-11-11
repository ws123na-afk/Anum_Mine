import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_money_models.dart';
import 'finance_money_strings.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';

/// Finance, Money advisor and the Plan on the phone
/// (docs/finance.md#money-advisor, #planning-and-advice): the weekly note
/// (runway, where money went, coming payments, cards, next steps), its
/// switches and "Work it out now", the past notes; and the Plan's "Can I
/// afford it?" and "Cash, a loan or installments?". Advice only.

AnumTone _severityTone(String s) => switch (s) {
      'urgent' => AnumTone.stop,
      'high' => AnumTone.warn,
      'medium' => AnumTone.neutral,
      _ => AnumTone.ok,
    };

class AdvisorScreen extends StatefulWidget {
  const AdvisorScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<AdvisorScreen> createState() => _AdvisorScreenState();
}

class _AdvisorScreenState extends State<AdvisorScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  AdvisorOverviewData? _overview;
  AdvisorNoteData? _note;
  bool _busy = false;
  late final RecordsList<AdvisorNoteRow> _past = RecordsList(
      (_, __, token) => widget.repository.advisorNotes(token: token));

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _past.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final overview = await widget.repository.advisor();
      if (!mounted) return;
      setState(() {
        _overview = overview;
        _note = overview.latest;
        _phase = RecordsPhase.ready;
      });
      await _past.load();
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _generate() async {
    final m = FinanceMoneyStrings.of(context);
    setState(() => _busy = true);
    try {
      final note = await widget.repository.generateAdvice();
      if (!mounted) return;
      setState(() => _note = note);
      recordsToast(context, m.t('adv.generated'));
      await _past.load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save({bool? enabled, int? weekday}) async {
    final m = FinanceMoneyStrings.of(context);
    final current = _overview!.settings;
    try {
      await widget.repository.saveAdvisorSettings(
          enabled: enabled ?? current.enabled,
          weekday: weekday ?? current.weekday);
      if (!mounted) return;
      recordsToast(context, m.t('adv.saved'));
      final overview = await widget.repository.advisor();
      if (mounted) setState(() => _overview = overview);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _openPast(AdvisorNoteRow row) async {
    try {
      final note = await widget.repository.advisorNote(row.id);
      if (mounted) setState(() => _note = note);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final overview = _overview;
    return Scaffold(
      appBar: AppBar(title: Text(m.t('adv.title'))),
      body: ListenableBuilder(
        listenable: _past,
        builder: (context, _) => AnumPage(onRefresh: _load, children: [
          Text(m.t('adv.onlyAdvises'),
              key: const Key('adv-only'),
              style: TextStyle(color: context.palette.muted)),
          if (_phase != RecordsPhase.ready || overview == null)
            RecordsStateView(
                phase: _phase,
                failure: _failure,
                deniedHint: FinanceMoreStrings.of(context).t('denied'),
                onRetry: _load)
          else ...[
            _settings(context, overview),
            if (_note == null)
              AnumEmpty(
                  icon: Icons.tips_and_updates_outlined,
                  title: m.t('adv.empty'),
                  message: m.t('adv.emptyText'),
                  action: m.t('adv.generate'),
                  onAction: _busy ? null : _generate)
            else
              ...AdvisorNoteView.parts(context, _note!),
            AnumHeader(title: m.t('adv.past')),
            ...recordsPagedBody<AdvisorNoteRow>(
              _past,
              deniedHint: FinanceMoreStrings.of(context).t('denied'),
              empty: Text(m.t('adv.past.none'),
                  style: TextStyle(color: context.palette.muted)),
              row: (AdvisorNoteRow row) => AnumRow(
                key: Key('adv-past-${row.id}'),
                icon: Icons.event_note_outlined,
                tone: row.urgent > 0 ? AnumTone.warn : null,
                title: m.t(
                    'adv.weekOf', {'date': ltrValue(context, row.weekStart)}),
                subtitle: [
                  m.t('adv.past.row',
                      {'actions': row.actions, 'urgent': row.urgent}),
                  if (row.trigger == 'manual') m.t('adv.past.manual'),
                ].join(' · '),
                detail: row.survival.entries
                    .map((e) => e.value == null
                        ? '${e.key}: -'
                        : '${e.key}: ${m.days(e.value!)}')
                    .join(' · '),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _openPast(row),
              ),
            ),
          ],
        ]),
      ),
    );
  }

  Widget _settings(BuildContext context, AdvisorOverviewData o) {
    final m = FinanceMoneyStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Material(
          type: MaterialType.transparency,
          child: SwitchListTile(
            key: const Key('adv-enabled'),
            contentPadding: EdgeInsets.zero,
            title: Text(m.t('adv.weekly')),
            subtitle: Text(m.t('adv.weeklyText')),
            value: o.settings.enabled,
            onChanged: (on) => _save(enabled: on),
          ),
        ),
        if (o.settings.enabled)
          KeyedSubtree(
            key: ValueKey('adv-day-${o.settings.weekday}'),
            child: DropdownButtonFormField<int>(
              key: const Key('adv-weekday'),
              initialValue: o.settings.weekday,
              isExpanded: true,
              decoration: InputDecoration(labelText: m.t('adv.day')),
              items: [
                for (var d = 0; d < 7; d++)
                  DropdownMenuItem(
                      value: d, child: Text(m.t('adv.weekday.$d'))),
              ],
              onChanged: (d) {
                if (d != null && d != o.settings.weekday) _save(weekday: d);
              },
            ),
          ),
        const SizedBox(height: AnumSpacing.xs),
        Text(
            o.settings.enabled && o.nextNoteOn != null
                ? m.t('adv.next', {'date': ltrValue(context, o.nextNoteOn!)})
                : m.t('adv.off'),
            style: muted),
        const SizedBox(height: AnumSpacing.xs),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('adv-generate'),
            onPressed: _busy ? null : _generate,
            icon: const Icon(Icons.refresh),
            label: Text(_busy ? m.t('adv.generating') : m.t('adv.generate')),
          ),
        ),
      ]),
    );
  }
}

/// A weekly note's sections, in the page's order.
class AdvisorNoteView {
  static List<Widget> parts(BuildContext context, AdvisorNoteData note) {
    final m = FinanceMoneyStrings.of(context);
    final arabic = AnumLocalizations.maybeOf(context).isArabic;
    final p = context.palette;
    final muted = TextStyle(color: p.muted);
    return [
      AnumHeader(
          title: m.t('adv.weekOf', {'date': ltrValue(context, note.weekStart)}),
          subtitle: note.notice(arabic).isEmpty ? null : note.notice(arabic)),
      AnumHeader(title: m.t('adv.actions')),
      if (note.actions.isEmpty)
        Text(m.t('adv.actions.none'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final (i, a) in note.actions.indexed)
              AnumRow(
                key: Key('adv-action-$i'),
                icon: Icons.flag_outlined,
                tone: _severityTone(a.severity),
                title: a.text(arabic),
                subtitle: m.t('adv.severity.${a.severity}'),
              ),
          ]),
        ),
      if (!note.salaryFound) Text(m.t('adv.salary.none'), style: muted),
      for (final c in note.currencies) ..._currency(context, c),
      if (note.cards.isNotEmpty) ...[
        AnumHeader(title: m.t('adv.cards')),
        for (final card in note.cards) _card(context, card),
      ],
    ];
  }

  static List<Widget> _currency(BuildContext context, AdvisorCurrency c) {
    final m = FinanceMoneyStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    String money(String v) => formatAmount(v, c.currency);
    String change(AdvisorPeriod period) => period.changePercent == null
        ? ''
        : m.t('adv.spent.change', {'percent': period.changePercent!.round()});
    return [
      AnumHeader(
          title: '${m.t('adv.runway')} · ${c.currency}',
          subtitle: m.t('adv.liquid', {'amount': money(c.liquid)})),
      if (c.runwayStatus == 'not_enough_history')
        Text(m.t('adv.history'), style: muted),
      if (c.runwayStatus == 'no_records')
        Text(m.t('adv.noRecords'), style: muted),
      AnumSurface(
        padding: const EdgeInsets.symmetric(
            horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final s in c.scenarios)
            AnumRow(
              key: Key('adv-scenario-${c.currency}-${s.kind}'),
              icon: Icons.hourglass_bottom,
              title: m.t('adv.scenario.${s.kind}'),
              subtitle: s.days == null
                  ? m.t('adv.scenario.never')
                  : s.runsOutOn == null
                      ? m.t('adv.scenario.daysOnly', {'days': m.days(s.days!)})
                      : m.t('adv.scenario.days', {
                          'days': m.days(s.days!),
                          'date': ltrValue(context, s.runsOutOn!)
                        }),
              detail:
                  m.t('adv.scenario.perMonth', {'amount': money(s.monthly)}),
            ),
        ]),
      ),
      if (c.week != null || c.month != null)
        RecordsSection(title: m.t('adv.spent'), children: [
          if (c.week != null)
            RecordsField(
                m.t('adv.spent.week'),
                [money(c.week!.amount), change(c.week!)]
                    .where((t) => t.isNotEmpty)
                    .join(' · ')),
          if (c.month != null)
            RecordsField(
                m.t('adv.spent.month'),
                [money(c.month!.amount), change(c.month!)]
                    .where((t) => t.isNotEmpty)
                    .join(' · ')),
        ]),
      AnumHeader(
          title: m.t('adv.coming'),
          subtitle: m.t('adv.coming.total',
              {'amount': money(c.comingTotal), 'soon': money(c.within7)})),
      if (c.coming.isEmpty)
        Text(m.t('adv.coming.none'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final item in c.coming.take(10))
              AnumRow(
                icon: Icons.event_outlined,
                title: item.name,
                subtitle:
                    '${m.t('adv.kind.${item.kind}')} · ${money(item.amount)}',
                detail: item.dueOn ?? m.t('adv.coming.noDate'),
              ),
          ]),
        ),
    ];
  }

  static Widget _card(BuildContext context, AdvisorCard card) {
    final m = FinanceMoneyStrings.of(context);
    final p = context.palette;
    String money(String v) => formatAmount(v, card.currency);
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumRow(
          icon: Icons.credit_card_outlined,
          tone: card.neverClears ? AnumTone.stop : null,
          title: card.last4 == null ? card.name : '${card.name} ${card.last4}',
          subtitle: m.t('adv.card.owed', {'amount': money(card.owed)}),
        ),
        if (card.available != null)
          Text(m.t('adv.card.available', {'amount': money(card.available!)})),
        if (card.minimum != null)
          Text(m.t('adv.card.minimum', {
            'amount': money(card.minimum!),
            'date': ltrValue(context, card.dueOn ?? '-')
          })),
        if (card.interestIfMinimum != null)
          Text(m.t(
              'adv.card.interest', {'amount': money(card.interestIfMinimum!)})),
        if (card.neverClears)
          Text(m.t('adv.card.never'), style: TextStyle(color: p.stop))
        else if (card.monthsToClear != null)
          Text(m.t('adv.card.clears', {'months': card.monthsToClear!})),
        if (card.missing.isNotEmpty)
          Text(
              m.t('adv.card.missing', {
                'list':
                    card.missing.map((k) => m.t('adv.missing.$k')).join(', '),
              }),
              style: TextStyle(color: p.warn)),
      ]),
    );
  }
}

// -- the Plan ----------------------------------------------------------------------

class PlanScreen extends StatefulWidget {
  const PlanScreen({required this.repository, super.key});
  final FinanceMoreRepository repository;

  @override
  State<PlanScreen> createState() => _PlanScreenState();
}

class _PlanScreenState extends State<PlanScreen> {
  AffordAnswer? _afford;
  List<CompareOption>? _options;
  String? _currency;

  String? _currencyOf(RecordsValues v, FinanceMoneyStrings m) {
    final c = (v['currency'] as String?)?.toUpperCase();
    if (c != null && !RegExp(r'^[A-Z]{3}$').hasMatch(c)) {
      throw FormatException(m.t('plan.badCurrency'));
    }
    return c;
  }

  int? _months(RecordsValues v, String key, FinanceMoneyStrings m) {
    final typed = v[key] as String?;
    if (typed == null) return null;
    final n = int.tryParse(decimalText(typed) ?? '');
    if (n == null || n < 1 || n > 360) {
      throw FormatException(m.t('plan.badMonths'));
    }
    return n;
  }

  Future<void> _check() async {
    final m = FinanceMoneyStrings.of(context);
    await openRecordsForm(
      context,
      RecordsForm(
        title: m.t('plan.afford'),
        intro: m.t('plan.afford.sub'),
        submitLabel: m.t('plan.check'),
        fields: [
          RecordsInput.text('item', m.t('plan.item'), maxLength: 120),
          RecordsInput.number('price', m.t('plan.price'), required: true),
          RecordsInput.text('currency', m.t('plan.currency'),
              initial: _currency),
          RecordsInput.number('monthly', m.t('plan.monthly')),
        ],
        onSubmit: (v) async {
          final currency = _currencyOf(v, m);
          final answer = await widget.repository.afford({
            'price': v['price'],
            'currency': currency,
            'item': v['item'],
            'monthly_cost': v['monthly'],
          });
          if (mounted) {
            setState(() {
              _afford = answer;
              _currency = answer.currency;
            });
          }
        },
      ),
    );
  }

  Future<void> _compare() async {
    final m = FinanceMoneyStrings.of(context);
    await openRecordsForm(
      context,
      RecordsForm(
        title: m.t('plan.compare'),
        intro: m.t('plan.compare.sub'),
        submitLabel: m.t('plan.run'),
        fields: [
          RecordsInput.number('price', m.t('plan.price'), required: true),
          RecordsInput.text('currency', m.t('plan.currency'),
              initial: _currency),
          RecordsInput.number('down', m.t('plan.down')),
          RecordsInput.text('loan_months', m.t('plan.loanMonths')),
          RecordsInput.number('loan_rate', m.t('plan.loanRate')),
          RecordsInput.choice('basis', m.t('plan.basis'),
              options: {
                'reducing': m.t('plan.reducing'),
                'flat': m.t('plan.flat'),
              },
              initial: 'reducing'),
          RecordsInput.text('inst_months', m.t('plan.instMonths')),
          RecordsInput.number('inst_rate', m.t('plan.instRate')),
          RecordsInput.number('inst_fees', m.t('plan.instFees')),
        ],
        onSubmit: (v) async {
          final currency = _currencyOf(v, m);
          final loanMonths = _months(v, 'loan_months', m);
          final instMonths = _months(v, 'inst_months', m);
          if (loanMonths == null && instMonths == null) {
            throw FormatException(m.t('plan.needOption'));
          }
          final answer = await widget.repository.compare({
            'price': v['price'],
            'currency': currency,
            'options': [
              {'kind': 'cash'},
              if (loanMonths != null)
                {
                  'kind': 'bank_loan',
                  'months': loanMonths,
                  'rate_percent': v['loan_rate'],
                  'basis': v['basis'] ?? 'reducing',
                  'down_payment': v['down'] ?? '0',
                },
              if (instMonths != null)
                {
                  'kind': 'installments',
                  'months': instMonths,
                  'rate_percent': v['inst_rate'],
                  'fees': v['inst_fees'] ?? '0',
                  'down_payment': v['down'] ?? '0',
                },
            ],
          });
          if (mounted) {
            setState(() {
              _options = answer.options;
              _currency = answer.currency;
            });
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('plan.title'))),
      body: AnumPage(children: [
        Text(m.t('plan.onlyAdvises'), style: muted),
        AnumHeader(title: m.t('plan.afford'), subtitle: m.t('plan.afford.sub')),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('plan-afford'),
            onPressed: _check,
            icon: const Icon(Icons.shopping_bag_outlined),
            label: Text(m.t('plan.check')),
          ),
        ),
        if (_afford != null) ..._affordAnswer(context, _afford!),
        AnumHeader(
            title: m.t('plan.compare'), subtitle: m.t('plan.compare.sub')),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('plan-compare'),
            onPressed: _compare,
            icon: const Icon(Icons.compare_arrows),
            label: Text(m.t('plan.run')),
          ),
        ),
        if (_options != null)
          AnumSurface(
            key: const Key('plan-options'),
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final o in _options!)
                AnumRow(
                  icon: o.kind == 'cash'
                      ? Icons.payments_outlined
                      : Icons.account_balance_outlined,
                  tone: o.status == 'ok' ? null : AnumTone.warn,
                  title:
                      o.label.isNotEmpty ? o.label : m.t('plan.opt.${o.kind}'),
                  subtitle: o.status != 'ok'
                      ? m.t('plan.opt.unknown')
                      : [
                          if (o.monthly != null)
                            m.t('plan.opt.monthly', {
                              'amount':
                                  formatAmount(o.monthly!, _currency ?? ''),
                              'months': o.months,
                            }),
                          if (o.totalCost != null)
                            m.t('plan.opt.total', {
                              'amount':
                                  formatAmount(o.totalCost!, _currency ?? '')
                            }),
                        ].join(' · '),
                  detail: o.interest == null
                      ? null
                      : m.t('plan.opt.interest', {
                          'amount': formatAmount(o.interest!, _currency ?? '')
                        }),
                ),
            ]),
          ),
      ]),
    );
  }

  List<Widget> _affordAnswer(BuildContext context, AffordAnswer a) {
    final m = FinanceMoneyStrings.of(context);
    final p = context.palette;
    String money(String? v) => v == null ? '-' : formatAmount(v, a.currency);
    final good =
        a.verdict.startsWith('yes') || a.verdict == 'monthly_comfortable';
    return [
      AnumSurface(
        key: const Key('plan-verdict'),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumRow(
            icon: good ? Icons.check_circle_outline : Icons.warning_amber,
            tone: good ? AnumTone.ok : AnumTone.warn,
            title: m.t('plan.verdict.${a.verdict}'),
            subtitle: formatAmount(a.price, a.currency),
          ),
          RecordsField(m.t('plan.safe'), money(a.safeToSpend)),
          RecordsField(m.t('plan.liquid'), money(a.liquid)),
          RecordsField(m.t('plan.income'), money(a.income)),
          RecordsField(m.t('plan.commitments'), money(a.commitments)),
          if (a.ratioAfter != null)
            RecordsField(m.t('plan.ratio'), '${a.ratioAfter}%'),
          if (a.monthsLeft != null)
            RecordsField(m.t('plan.reserve'), a.monthsLeft),
        ]),
      ),
      if (a.payWith.isNotEmpty)
        RecordsSection(title: m.t('plan.payWith'), children: [
          for (final pay in a.payWith)
            RecordsField(
                pay.name,
                [
                  formatAmount(pay.balance, pay.currency),
                  if (m.maybe('plan.pay.${pay.note}') != null)
                    m.t('plan.pay.${pay.note}'),
                ].join(' · ')),
        ]),
      if (a.avoid.isNotEmpty)
        RecordsSection(title: m.t('plan.avoid'), children: [
          for (final avoid in a.avoid)
            if (m.maybe('plan.avoid.${avoid.code}') != null)
              Text(m.t('plan.avoid.${avoid.code}', avoid.params),
                  style: TextStyle(color: p.warn)),
        ]),
    ];
  }
}
