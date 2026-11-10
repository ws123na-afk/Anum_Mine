import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'books_controller.dart';
import 'books_data.dart';
import 'books_review.dart';
import 'books_strings.dart';

String _money(BuildContext context, String amount, String currency) =>
    formatMoneyText(amount, currency, locale: Localizations.localeOf(context));

String _amounts(BuildContext context, List<BooksAmount> amounts) =>
    amounts.isEmpty
        ? '-'
        : amounts.map((a) => _money(context, a.amount, a.currency)).join(', ');

AnumTone billTone(BooksBill bill) => bill.status == 'void'
    ? AnumTone.neutral
    : bill.overdue
        ? AnumTone.stop
        : bill.paymentState == 'paid'
            ? AnumTone.ok
            : AnumTone.warn;

/// Business books on the phone (docs/business-books.md#mobile): the P&L
/// for a period, read only, and the bills with their payments. Members
/// record a payment; voiding stays with owners, as on the web.
class BooksScreen extends StatefulWidget {
  const BooksScreen({required this.controller, super.key});
  final BooksController controller;

  @override
  State<BooksScreen> createState() => _BooksScreenState();
}

class _BooksScreenState extends State<BooksScreen> {
  int _tab = 0;
  BooksController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.phase == RecordsPhase.loading) c.load();
  }

  Future<void> _setUp() async {
    final b = BooksStrings.of(context);
    final months =
        DateFormat.MMMM(Localizations.localeOf(context).languageCode);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: b.t('setUp'),
        intro: b.t('notSetUpOwner'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.text('base_currency', b.t('baseCurrency'),
              required: true, initial: 'AED', maxLength: 3),
          RecordsInput.text('country', b.t('country'), maxLength: 2),
          RecordsInput.choice('fiscal_year_start_month', b.t('fiscalMonth'),
              initial: '1',
              options: {
                for (var m = 1; m <= 12; m++)
                  '$m': months.format(DateTime(2026, m))
              }),
          RecordsInput.choice('basis', b.t('basis'),
              initial: 'accrual',
              options: {
                'accrual': b.t('basis.accrual'),
                'cash': b.t('basis.cash')
              }),
        ],
        onSubmit: (v) => c.saveSettings({
          'base_currency': (v['base_currency']! as String).toUpperCase(),
          'country': (v['country'] as String?)?.toUpperCase(),
          'fiscal_year_start_month':
              int.parse(v['fiscal_year_start_month']! as String),
          'basis': v['basis'],
          'tax_label': '',
          'category_sections': <String, String>{},
          'rate_max_age_days': 31,
        }),
      ),
    );
    if (saved && mounted) {
      recordsToast(context, KitStrings.of(context).t('saved'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    return ListenableBuilder(
      listenable: Listenable.merge([c, c.bills, c.review]),
      builder: (context, _) {
        final setup = c.setup;
        Widget body;
        if (c.phase != RecordsPhase.ready || setup == null) {
          body = AnumPage(onRefresh: c.load, children: [
            AnumHeader(title: b.t('title'), subtitle: b.t('subtitle')),
            RecordsStateView(
                phase: c.phase,
                failure: c.failure,
                deniedHint: b.t('denied'),
                onRetry: c.load),
          ]);
        } else if (!setup.configured) {
          body = AnumPage(onRefresh: c.load, children: [
            AnumHeader(title: b.t('title'), subtitle: b.t('subtitle')),
            AnumEmpty(
              key: const Key('books-not-set-up'),
              icon: Icons.menu_book_outlined,
              title: b.t('notSetUp'),
              message: setup.permissions.manage
                  ? b.t('notSetUpOwner')
                  : b.t('notSetUpMember'),
              action: setup.permissions.manage ? b.t('setUp') : null,
              onAction: setup.permissions.manage ? _setUp : null,
            ),
          ]);
        } else {
          final review = setup.permissions.account;
          final tab = !review && _tab == 2 ? 0 : _tab;
          body = Column(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                  AnumSpacing.md, AnumSpacing.sm, AnumSpacing.md, 0),
              child: SegmentedButton<int>(
                segments: [
                  ButtonSegment(value: 0, label: Text(b.t('tab.pnl'))),
                  ButtonSegment(value: 1, label: Text(b.t('tab.bills'))),
                  if (review)
                    ButtonSegment(
                        value: 2,
                        label: Text(b.t('tab.review'),
                            key: const Key('books-tab-review'))),
                ],
                selected: {tab},
                onSelectionChanged: (v) => setState(() => _tab = v.first),
              ),
            ),
            Expanded(
              child: switch (tab) {
                0 => _PnlView(controller: c),
                1 => _BillsView(controller: c),
                _ => ReviewView(controller: c),
              },
            ),
          ]);
        }
        return Scaffold(appBar: AppBar(title: Text(b.t('title'))), body: body);
      },
    );
  }
}

class _PnlView extends StatelessWidget {
  const _PnlView({required this.controller});
  final BooksController controller;

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final c = controller;
    final report = c.report;
    final children = <Widget>[
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
        for (final period in BooksPeriod.values)
          ChoiceChip(
            key: Key('period-${period.name}'),
            label: Text(b.t('period.${period.name}')),
            selected: c.period == period,
            onSelected: (_) => c.choose(period: period),
          ),
        for (final basis in ['accrual', 'cash'])
          ChoiceChip(
            key: Key('basis-$basis'),
            label: Text(b.t('basis.$basis')),
            selected: c.basis == basis,
            onSelected: (_) => c.choose(basis: basis),
          ),
      ]),
    ];
    if (c.reportPhase != RecordsPhase.ready || report == null) {
      children.add(RecordsStateView(
          phase: c.reportPhase,
          failure: c.reportFailure,
          deniedHint: b.t('denied'),
          onRetry: c.loadReport));
      return AnumPage(onRefresh: c.loadReport, children: children);
    }
    final cur = report.currency;
    String money(String amount) => _money(context, amount, cur);
    final empty = [
      report.revenue,
      report.costOfSales,
      report.operatingExpenses,
      report.otherIncome,
      report.financeCosts
    ].every((g) => g.lines.isEmpty);
    children.add(Text(
      b.t('periodRange', {
        'from': recordsDate(context, report.from),
        'to': recordsDate(context, report.to),
        'basis': b.t('basis.${report.basis}'),
      }),
      style: TextStyle(color: context.palette.muted),
    ));
    children.add(AnumMetricGrid(children: [
      AnumMetric(
          label: b.t('pnl.revenue'),
          value: money(report.revenue.total),
          icon: Icons.trending_up),
      AnumMetric(
          label: b.t('pnl.gross_profit'),
          value: money(report.grossProfit),
          caption: report.grossMarginPct == null
              ? null
              : b.t('margin', {'pct': report.grossMarginPct!}),
          icon: Icons.stacked_line_chart),
      AnumMetric(
          label: b.t('pnl.operating_profit'),
          value: money(report.operatingProfit),
          icon: Icons.work_outline),
      AnumMetric(
          key: const Key('books-net-profit'),
          label: b.t('pnl.net_profit'),
          value: money(report.netProfit),
          caption: report.netMarginPct == null
              ? null
              : b.t('margin', {'pct': report.netMarginPct!}),
          icon: Icons.account_balance_outlined,
          accent: report.netProfit.startsWith('-')
              ? context.palette.stop
              : context.palette.ok),
    ]));
    if (!report.complete) {
      children.add(RecordsSection(
        key: const Key('books-not-converted'),
        title: b.t('incomplete'),
        children: [
          for (final item in report.notConverted)
            Text(b.t('notConverted', {
              'group': b.label('pnl', item.group),
              'amount': _money(context, item.amount, item.currency),
            })),
        ],
      ));
    }
    if (empty) {
      children.add(AnumEmpty(
        title: b.t('pnlEmpty'),
        message: b.t('pnlEmptyText'),
        icon: Icons.insights_outlined,
        action: b.t('period.year'),
        onAction: c.period == BooksPeriod.year
            ? null
            : () => c.choose(period: BooksPeriod.year),
      ));
    }
    Widget group(String key, BooksGroup g) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Line(label: b.t('pnl.$key'), amount: money(g.total), bold: true),
            for (final line in g.lines)
              _Line(
                  label: b.label('line', line.key), amount: money(line.amount)),
          ],
        );
    children.add(RecordsSection(title: b.t('statement'), children: [
      group('revenue', report.revenue),
      group('cost_of_sales', report.costOfSales),
      _Line(
          label: b.t('pnl.gross_profit'),
          amount: money(report.grossProfit),
          bold: true),
      group('operating_expenses', report.operatingExpenses),
      _Line(
          label: b.t('pnl.operating_profit'),
          amount: money(report.operatingProfit),
          bold: true),
      group('other_income', report.otherIncome),
      group('finance_costs', report.financeCosts),
      _Line(
          label: b.t('pnl.fx_differences'),
          amount: money(report.fxDifferences)),
      const Divider(),
      _Line(
          label: b.t('pnl.net_profit'),
          amount: money(report.netProfit),
          bold: true),
    ]));
    children.add(RecordsSection(title: b.t('owed'), children: [
      RecordsField(b.t('receivables'), _amounts(context, report.receivables)),
      RecordsField(b.t('payables'), _amounts(context, report.payables)),
      if (report.unpaidBills > 0)
        Text(b.t('unpaidBills', {'count': report.unpaidBills})),
    ]));
    if (report.notes.isNotEmpty) {
      children.add(RecordsSection(title: b.t('notes'), children: [
        for (final note in report.notes) Text(note),
      ]));
    }
    children.add(Text(b.t('readOnlyPhone'),
        style: TextStyle(color: context.palette.muted)));
    return AnumPage(onRefresh: c.loadReport, children: children);
  }
}

class _Line extends StatelessWidget {
  const _Line({required this.label, required this.amount, this.bold = false});
  final String label, amount;
  final bool bold;

  @override
  Widget build(BuildContext context) {
    final style = TextStyle(
        fontWeight: bold ? FontWeight.w700 : FontWeight.w400,
        color: bold ? context.palette.text : context.palette.muted);
    return MergeSemantics(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
        child: Wrap(
          alignment: WrapAlignment.spaceBetween,
          spacing: AnumSpacing.sm,
          children: [
            Text(label, style: style),
            Text(amount, style: style),
          ],
        ),
      ),
    );
  }
}

class _BillsView extends StatelessWidget {
  const _BillsView({required this.controller});
  final BooksController controller;

  void _open(BuildContext context, BooksBill bill) =>
      openBill(context, controller, bill.id);

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final k = KitStrings.of(context);
    final list = controller.bills;
    final children = <Widget>[
      RecordsSearchField(
          initial: list.query,
          hint: b.t('search'),
          onSearch: (q) => list.load(query: q)),
      RecordsFilterChips(
        options: {
          for (final f in [
            '', 'open', 'overdue', 'unpaid', 'partly_paid', 'paid', //
            'recurring', 'void',
          ])
            f: b.t('filter.${f.isEmpty ? 'all' : f}')
        },
        selected: list.filter,
        onSelected: (f) => list.load(filter: f),
      ),
    ];
    if (list.phase != RecordsPhase.ready) {
      children.add(RecordsStateView(
          phase: list.phase,
          failure: list.failure,
          deniedHint: b.t('denied'),
          onRetry: list.load));
    } else {
      children.add(Text(
        '${b.t('openTotal', {
              'amounts': _amounts(context, controller.openTotals)
            })}\n'
        '${b.t('overdueTotal', {
              'amounts': _amounts(context, controller.overdueTotals)
            })}',
        key: const Key('bills-totals'),
      ));
      if (list.items.isEmpty) {
        children.add(list.narrowed
            ? AnumEmpty(
                title: k.t('noMatch'),
                message: k.t('noMatchText'),
                icon: Icons.search_off,
                action: k.t('clearFilters'),
                onAction: () => list.load(query: '', filter: ''))
            : AnumEmpty(
                key: const Key('bills-empty'),
                title: b.t('billsEmpty'),
                message: b.t('billsEmptyText'),
                icon: Icons.receipt_long_outlined,
                action: k.t('retry'),
                onAction: list.load));
      } else {
        children.add(AnumSurface(
          child: Column(children: [
            for (final bill in list.items)
              _BillRow(
                key: Key('bill-${bill.id}'),
                missing: controller.evidenceMissing.contains(bill.id),
                icon: Icons.receipt_long_outlined,
                tone: billTone(bill),
                title: bill.vendorName,
                subtitle: [
                  _money(context, bill.total, bill.currency),
                  b.label('state',
                      bill.status == 'void' ? 'void' : bill.paymentState),
                  if (bill.reference.isNotEmpty) bill.reference,
                ].join(' · '),
                detail: bill.overdue
                    ? b.t('overdueDays', {'days': b.days(bill.daysOverdue)})
                    : b.t('due', {'date': recordsDate(context, bill.dueDate)}),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _open(context, bill),
              ),
          ]),
        ));
        children.add(RecordsMore(list: list));
      }
    }
    return AnumPage(onRefresh: list.load, children: children);
  }
}

/// Opens a bill with its payments and supporting documents.
void openBill(BuildContext context, BooksController controller, String id) {
  Navigator.of(context).push(MaterialPageRoute<void>(
    builder: (_) => BillScreen(
        repository: controller.repository,
        billId: id,
        onChanged: controller.billChanged,
        onEvidence: (missing) =>
            controller.evidenceChanged(id, missing: missing)),
  ));
}

/// A bill in the list, with the "Evidence missing" badge when it is at or
/// above the evidence threshold and has no supporting document.
class _BillRow extends StatelessWidget {
  const _BillRow({
    required this.missing,
    required this.icon,
    required this.tone,
    required this.title,
    required this.subtitle,
    required this.detail,
    required this.trailing,
    required this.onTap,
    super.key,
  });
  final bool missing;
  final IconData icon;
  final AnumTone tone;
  final String title, subtitle, detail;
  final Widget trailing;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final row = AnumRow(
        icon: icon,
        tone: tone,
        title: title,
        subtitle: subtitle,
        detail: detail,
        trailing: trailing,
        onTap: onTap);
    if (!missing) return row;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      row,
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: Padding(
          padding: const EdgeInsetsDirectional.only(bottom: AnumSpacing.xs),
          child: AnumPill(
              key: const Key('evidence-missing'),
              icon: Icons.report_gmailerrorred_outlined,
              label: BooksStrings.of(context).t('evidence.missing'),
              tone: AnumTone.warn),
        ),
      ),
    ]);
  }
}

/// One bill with its payments: record a payment (members and owners),
/// void it or a payment (owners only, with a reason).
class BillScreen extends StatefulWidget {
  const BillScreen({
    required this.repository,
    required this.billId,
    this.onChanged,
    this.onEvidence,
    super.key,
  });
  final BooksRepository repository;
  final String billId;
  final Future<void> Function(BooksBill bill)? onChanged;

  /// The bill's evidence changed: whether it still misses a document.
  final void Function(bool missing)? onEvidence;

  @override
  State<BillScreen> createState() => _BillScreenState();
}

class _BillScreenState extends State<BillScreen> {
  BooksBill? _bill;
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      _bill = await widget.repository.bill(widget.billId);
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  void _changed(BooksBill bill) {
    setState(() => _bill = bill);
    widget.onChanged?.call(bill);
  }

  Future<void> _pay() async {
    final b = BooksStrings.of(context);
    final bill = _bill!;
    final key = newIdempotencyKey('bpay');
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: b.t('recordPayment'),
        intro: b.t('recordHelp'),
        submitLabel: b.t('recordPayment'),
        fields: [
          RecordsInput.number('amount', '${b.t('amount')} (${bill.currency})',
              required: true, initial: bill.balance),
          RecordsInput.date('paid_on', b.t('paidOn'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.choice('method', b.t('method'),
              initial: 'bank_transfer',
              options: {
                for (final m in [
                  'bank_transfer',
                  'cash',
                  'card',
                  'cheque',
                  'other'
                ])
                  m: b.t('method.$m')
              }),
          RecordsInput.text('reference', b.t('reference'), maxLength: 120),
          RecordsInput.text('note', b.t('note'), maxLength: 300),
        ],
        onSubmit: (v) async {
          final updated = await widget.repository.pay(
              bill.id,
              {
                'kind': 'recorded',
                'amount': v['amount'],
                'paid_on': v['paid_on'],
                'method': v['method'],
                'reference': v['reference'] ?? '',
                'note': v['note'] ?? '',
              },
              idempotencyKey: key);
          _changed(updated);
        },
      ),
    );
    if (saved && mounted) recordsToast(context, b.t('paymentRecorded'));
  }

  Future<void> _void({BooksPayment? payment}) async {
    final b = BooksStrings.of(context);
    final bill = _bill!;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: b.t(payment == null ? 'voidBill' : 'voidPayment'),
        intro: b.t('ownerOnly'),
        submitLabel: b.t(payment == null ? 'voidBill' : 'voidPayment'),
        fields: [
          RecordsInput.text('reason', b.t('voidReason'),
              required: true, maxLength: 300),
        ],
        onSubmit: (v) async {
          final reason = v['reason']! as String;
          _changed(payment == null
              ? await widget.repository.voidBill(bill.id, reason)
              : await widget.repository
                  .voidPayment(bill.id, payment.id, reason));
        },
      ),
    );
    if (saved && mounted) recordsToast(context, b.t('voided'));
  }

  @override
  Widget build(BuildContext context) {
    final b = BooksStrings.of(context);
    final bill = _bill;
    return Scaffold(
      appBar: AppBar(title: Text(bill?.vendorName ?? b.t('bill'))),
      body: _phase != RecordsPhase.ready || bill == null
          ? AnumPage(children: [
              RecordsStateView(
                  phase: _phase,
                  failure: _failure,
                  deniedHint: b.t('denied'),
                  onRetry: _load),
            ])
          : AnumPage(onRefresh: _load, children: [
              RecordsSection(title: bill.vendorName, children: [
                Wrap(spacing: AnumSpacing.xs, children: [
                  AnumPill(
                      key: const Key('bill-state'),
                      label: b.label('state',
                          bill.status == 'void' ? 'void' : bill.paymentState),
                      tone: billTone(bill)),
                  if (bill.overdue)
                    AnumPill(
                        label: b.t(
                            'overdueDays', {'days': b.days(bill.daysOverdue)}),
                        tone: AnumTone.stop),
                  if (bill.recurrence != null)
                    AnumPill(label: b.label('recurrence', bill.recurrence!)),
                ]),
                const SizedBox(height: AnumSpacing.xs),
                RecordsField(b.t('reference'), bill.reference),
                if (bill.description.isNotEmpty) Text(bill.description),
                RecordsField(
                    b.t('billDate'), recordsDate(context, bill.billDate)),
                RecordsField(
                    b.t('dueDate'), recordsDate(context, bill.dueDate)),
                RecordsField(b.t('subtotal'),
                    _money(context, bill.subtotal, bill.currency)),
                RecordsField(
                    b.t('tax'), _money(context, bill.tax, bill.currency)),
                RecordsField(
                    b.t('total'), _money(context, bill.total, bill.currency)),
                RecordsField(b.t('paid'),
                    _money(context, bill.amountPaid, bill.currency)),
                RecordsField(b.t('balance'),
                    _money(context, bill.balance, bill.currency)),
                RecordsField(b.t('category'), bill.category),
                RecordsField(b.t('section'), b.label('section', bill.section)),
                if (bill.status == 'void')
                  Text(b.t('voidedLine', {'reason': bill.voidReason}),
                      style: TextStyle(color: context.palette.muted)),
              ]),
              RecordsSection(title: b.t('payments'), children: [
                if (bill.payments?.isEmpty ?? true)
                  Text(b.t('paymentsEmpty'),
                      style: TextStyle(color: context.palette.muted))
                else
                  for (final p in bill.payments!)
                    AnumRow(
                      key: Key('payment-${p.id}'),
                      icon: Icons.payments_outlined,
                      tone: p.voidedAt != null ? AnumTone.neutral : AnumTone.ok,
                      title: _money(context, p.amount, p.currency),
                      subtitle: [
                        recordsDate(context, p.paidOn),
                        b.label('method', p.method),
                        if (p.reference.isNotEmpty) p.reference,
                      ].join(' · '),
                      detail: p.voidedAt != null
                          ? b.t('voidedLine', {'reason': p.voidReason})
                          : (!p.counts ? b.t('notCounted') : null),
                      trailing: bill.canVoid && p.voidedAt == null
                          ? IconButton(
                              key: Key('void-payment-${p.id}'),
                              tooltip: b.t('voidPayment'),
                              onPressed: () => _void(payment: p),
                              icon: const Icon(Icons.block))
                          : null,
                    ),
                const SizedBox(height: AnumSpacing.xs),
                if (bill.canPay)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: FilledButton.icon(
                      key: const Key('bill-pay'),
                      onPressed: _pay,
                      icon: const Icon(Icons.add_card),
                      label: Text(b.t('recordPayment')),
                    ),
                  )
                else if (bill.status != 'void' && bill.paymentState != 'paid')
                  Text(b.t('cannotPay'),
                      key: const Key('bill-cannot-pay'),
                      style: TextStyle(color: context.palette.muted)),
              ]),
              EvidenceSection(
                  repository: widget.repository,
                  sourceType: 'bill',
                  sourceId: bill.id,
                  onChanged: widget.onEvidence),
              if (bill.status != 'void')
                RecordsSection(title: b.t('voidBill'), children: [
                  Text(b.t('ownerOnly'),
                      style: TextStyle(color: context.palette.muted)),
                  if (bill.canVoid) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton.icon(
                        key: const Key('bill-void'),
                        onPressed: _void,
                        icon: const Icon(Icons.block),
                        label: Text(b.t('voidBill')),
                      ),
                    ),
                  ],
                ]),
            ]),
    );
  }
}
