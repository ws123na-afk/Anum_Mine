import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_export.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import '../vault/vault_transfer.dart' show VaultFilePicker, pickVaultFile;
import 'finance_core_models.dart';
import 'finance_core_repository.dart';
import 'finance_core_strings.dart';
import 'finance_correct_view.dart';
import 'finance_models.dart';
import 'finance_personal_models.dart';
import 'finance_review_models.dart';
import 'finance_review_strings.dart';
import 'finance_review_view.dart' show typeLabel;
import 'finance_strings.dart';

/// Personal Finance on the phone (docs/finance.md#dashboard): the overview's
/// cash against cards, what the month left out, money in with the salary
/// month, what needs review and coming payments; each account's freshness;
/// the transaction register with its filters; a transaction's evidence,
/// links, related rows, category and note; and the salary month by month.
/// Every figure is the server's (`GET /finance/summary`), the same answer the
/// web reads, so both show the same totals.

/// Every category and type, in the web's order (the register's filters).
const financeCategories = fileableCategories;
const _types = [...fileableTypes, 'uncategorized'];
const _statuses = [
  'needs_review',
  'uncategorized',
  'duplicate_candidate',
  'linked',
  'recorded'
];

String _day(String? iso) =>
    iso == null || iso.length < 10 ? '-' : iso.substring(0, 10);

TextStyle _muted(BuildContext context) =>
    TextStyle(color: context.palette.muted);

// -- the overview -------------------------------------------------------------

/// The dashboard cards under the overview's figures. Nothing is shown for an
/// older server that does not send the dashboard.
class FinanceDashboardCards extends StatelessWidget {
  const FinanceDashboardCards(
      {required this.summary,
      this.onOpenTransactions,
      this.onOpenSms,
      this.onOpenBills,
      this.onOpenSalary,
      super.key});
  final FinanceSummary summary;
  final VoidCallback? onOpenTransactions, onOpenSms, onOpenBills, onOpenSalary;

  @override
  Widget build(BuildContext context) {
    final d = summary.dashboard;
    if (!d.hasDashboard) return const SizedBox.shrink();
    final s = FinanceCoreStrings.of(context);
    final currency = summary.pick(summary.balances).currency;
    final totals = dashboardTotals(summary, currency);
    final position = d.positionFor(currency);
    final excluded = [
      for (final k in ExcludedFlow.order)
        if (totals.excluded[k] case final row?) (k, row)
    ];
    final income =
        d.incomeBreakdown.where((l) => l.currency == currency).toList();
    final review = d.review;
    final reviewRows = [
      if (review.uncategorized > 0)
        ('review.uncategorized', review.uncategorized, onOpenTransactions),
      if (review.smsReview > 0) ('review.sms', review.smsReview, onOpenSms),
      if (review.linksNeedReview > 0)
        ('review.links', review.linksNeedReview, onOpenTransactions),
      if (review.installmentsReview > 0)
        ('review.installments', review.installmentsReview, onOpenBills),
    ];
    final salary = d.salary;
    return Column(
        key: const Key('finance-dashboard'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumSurface(
            key: const Key('finance-cash-cards'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                      title: s.t('cash.title'),
                      subtitle: s.t('cash.sub', {'currency': currency})),
                  if (position != null) ...[
                    _Line(s.t('cash.cash'), formatAmount(totals.cash, currency),
                        valueKey: const Key('dash-cash')),
                    _Line(s.t('cash.cardOwed'),
                        formatAmount(totals.cardOwed, currency),
                        valueKey: const Key('dash-card-owed')),
                    if ((double.tryParse(totals.cardOwed) ?? 0) > 0)
                      _Line(
                          s.t('cash.available'),
                          totals.cardAvailable == null
                              ? s.t('cash.availableUnknown')
                              : formatAmount(totals.cardAvailable!, currency)),
                    if ((double.tryParse(position.loansOwed) ?? 0) > 0)
                      _Line(s.t('cash.loans'),
                          formatAmount(position.loansOwed, currency)),
                  ],
                  const SizedBox(height: AnumSpacing.sm),
                  Text(s.t('excluded.title'),
                      style: Theme.of(context).textTheme.titleSmall),
                  if (excluded.isEmpty)
                    Text(s.t('excluded.none'), style: _muted(context))
                  else
                    for (final (kind, row) in excluded)
                      _Line(
                          s.t('excluded.$kind'),
                          s.t('excluded.row', {
                            'in': formatAmount(row.$1, currency),
                            'out': formatAmount(row.$2, currency)
                          }),
                          valueKey: Key('dash-excluded-$kind')),
                ]),
          ),
          AnumSurface(
            key: const Key('finance-money-in'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: s.t('income.title'), subtitle: currency),
                  if (income.isEmpty)
                    Text(s.t('income.none'), style: _muted(context))
                  else
                    for (final line in income)
                      AnumRow(
                          title: s.t('income.${line.kind}'),
                          subtitle: s.count('income.count', line.count),
                          detail: formatAmount(line.amount, line.currency)),
                  if (salary != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Wrap(
                        spacing: AnumSpacing.sm,
                        runSpacing: AnumSpacing.xs,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          AnumPill(
                              key: const Key('dash-salary-status'),
                              label: s.t('salary.status.${salary.status}'),
                              tone: salary.status == 'received'
                                  ? AnumTone.ok
                                  : salary.status == 'upcoming'
                                      ? AnumTone.info
                                      : AnumTone.warn),
                          Text(s.t('salary.expected', {
                            'expected': formatAmount(
                                salary.expectedNet, salary.currency),
                            'received':
                                formatAmount(salary.received, salary.currency)
                          })),
                        ]),
                    Text(s.t('salary.payOn', {'date': salary.payOn}),
                        style: _muted(context)),
                  ] else if (d.detail == 'full')
                    Text(s.t('salary.noProfile'), style: _muted(context)),
                  if (onOpenSalary != null)
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: TextButton(
                          key: const Key('dash-salary-history'),
                          onPressed: onOpenSalary,
                          child: Text(s.t('salary.history'))),
                    ),
                ]),
          ),
          AnumSurface(
            key: const Key('finance-review'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: s.t('review.title')),
                  if (reviewRows.isEmpty)
                    Text(s.t('review.none'), style: _muted(context))
                  else
                    for (final (key, n, open) in reviewRows)
                      AnumRow(
                          title: s.t(key, {'n': n}),
                          icon: Icons.task_alt,
                          tone: AnumTone.warn,
                          onTap: open),
                ]),
          ),
          AnumSurface(
            key: const Key('finance-upcoming-payments'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                      title: s.t('upcoming.title'),
                      subtitle: d.paymentsDue.isEmpty
                          ? null
                          : s.t('upcoming.total',
                              {'total': formatAmounts(d.paymentsDue)})),
                  if (d.upcomingPayments.isEmpty)
                    Text(s.t('upcoming.none'), style: _muted(context))
                  else
                    for (final p in d.upcomingPayments.take(6))
                      AnumRow(
                        title: p.name,
                        icon: p.kind == 'bill'
                            ? Icons.event_outlined
                            : Icons.view_week_outlined,
                        tone: p.overdue || p.status == 'needs_review'
                            ? AnumTone.warn
                            : null,
                        subtitle: [
                          s.t('upcoming.${p.kind}'),
                          p.dueOn,
                          p.overdue
                              ? s.t('upcoming.overdue')
                              : p.daysUntil <= 0
                                  ? s.t('upcoming.today')
                                  : s.t('upcoming.dueIn',
                                      {'days': s.days(p.daysUntil)}),
                          s.t(p.status == 'needs_review'
                              ? 'upcoming.review'
                              : 'upcoming.pending'),
                        ].join(' · '),
                        detail: formatAmount(p.amount, p.currency),
                      ),
                ]),
          ),
        ]);
  }
}

class _Line extends StatelessWidget {
  const _Line(this.label, this.value, {this.valueKey});
  final String label, value;
  final Key? valueKey;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
        child: MergeSemantics(
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: Text(label, style: _muted(context))),
            const SizedBox(width: AnumSpacing.sm),
            Flexible(
                child: Text(value,
                    key: valueKey,
                    textAlign: TextAlign.end,
                    style: Theme.of(context).textTheme.bodyMedium)),
          ]),
        ),
      );
}

/// One account's freshness: verified or estimated balance, the newest
/// captures and, when stale, for how long. Null when the server sent none.
String? freshnessText(
    FinanceCoreStrings s, AccountFreshness? f, String currency,
    {DateTime? now}) {
  if (f == null) return null;
  final parts = <String>[
    if (f.balanceKind == 'verified' && f.verifiedAt != null)
      s.t('fresh.verified', {'date': _day(f.verifiedAt)})
    else
      s.t('fresh.estimated'),
    if (f.verifiedBy == 'sms' &&
        f.difference != null &&
        (double.tryParse(f.difference!) ?? 0) != 0 &&
        f.verifiedBalance != null)
      s.t('fresh.statedDiffers', {
        'amount': formatAmount(f.verifiedBalance!, currency),
        'date': _day(f.verifiedAt)
      }),
    if (f.lastSmsAt != null) s.t('fresh.lastSms', {'date': _day(f.lastSmsAt)}),
    if (f.lastApplePayAt != null)
      s.t('fresh.lastApplePay', {'date': _day(f.lastApplePayAt)}),
    if (f.lastImportAt != null)
      s.t('fresh.lastStatement', {'date': _day(f.lastImportAt)}),
    if (f.state == 'manual') s.t('fresh.manual'),
  ];
  if (f.state == 'stale') {
    final at = DateTime.tryParse(f.lastCapture?.$2 ?? '');
    final days = at == null
        ? null
        : (now ?? DateTime.now()).toUtc().difference(at.toUtc()).inDays;
    parts.insert(
        0,
        days == null
            ? s.t('fresh.staleNever')
            : s.t('fresh.stale', {'days': s.days(days < 0 ? 0 : days)}));
  }
  return parts.join(' · ');
}

// -- the register --------------------------------------------------------------

/// Transactions with search, filters (account, category, type, status,
/// money in or out, dates) and paging, each opening its detail.
class RegisterScreen extends StatefulWidget {
  const RegisterScreen(
      {required this.repository,
      required this.accounts,
      required this.openTransaction,
      this.initial = const RegisterFilters(),
      this.saver = const SystemExportSaver(),
      super.key});
  final FinanceCoreRepository repository;
  final List<FinanceAccount> accounts;
  final RegisterFilters initial;

  /// Where Download CSV hands the file (the system save dialog; a fake in
  /// tests).
  final ExportSaver saver;

  /// Opens one transaction; completes when it is closed.
  final Future<void> Function(BuildContext context, RegisterRow row)
      openTransaction;

  @override
  State<RegisterScreen> createState() => _RegisterScreenState();
}

class _RegisterScreenState extends State<RegisterScreen> {
  late RegisterFilters _filters = widget.initial;
  late final _search = TextEditingController(text: widget.initial.query);
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  final List<RegisterRow> _rows = [];
  List<RegisterBalance> _balances = const [];
  int _total = 0;
  bool _more = false;
  bool _exporting = false;

  Future<void> _download() async {
    setState(() => _exporting = true);
    await exportCsvFile(context,
        load: () => widget.repository.exportCsv(_filters),
        fileName: 'anum-transactions.csv',
        saver: widget.saver);
    if (mounted) setState(() => _exporting = false);
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load({bool append = false}) async {
    setState(() {
      if (!append) _phase = RecordsPhase.loading;
      _more = append;
    });
    try {
      final page = await widget.repository
          .register(_filters, offset: append ? _rows.length : 0);
      if (!append) _rows.clear();
      _rows.addAll(page.items);
      if (!append) _balances = page.balances;
      _total = page.total;
      _failure = null;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() => _more = false);
  }

  void _apply(RegisterFilters next) {
    _filters = next;
    _load();
  }

  Future<void> _editFilters() async {
    final next = await showModalBottomSheet<RegisterFilters>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) =>
          _FilterSheet(filters: _filters, accounts: widget.accounts),
    );
    if (next != null) _apply(next);
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceCoreStrings.of(context);
    final f = FinanceStrings.of(context);
    final rs = FinanceReviewStrings.of(context);
    final names = {for (final a in widget.accounts) a.id: a.name};
    return Scaffold(
      appBar: AppBar(title: Text(s.t('reg.title')), actions: [
        IconButton(
            key: const Key('register-download'),
            tooltip: _phase == RecordsPhase.ready && _total == 0
                ? s.t('reg.downloadNone')
                : s.t('reg.download'),
            onPressed: _exporting || _phase != RecordsPhase.ready || _total == 0
                ? null
                : _download,
            icon: const Icon(Icons.download_outlined)),
        IconButton(
            key: const Key('register-filters'),
            tooltip: s.t('reg.filters'),
            onPressed: _editFilters,
            icon: Badge(
                isLabelVisible: _filters.narrowed,
                child: const Icon(Icons.filter_list))),
      ]),
      body: AnumPage(onRefresh: _load, children: [
        TextField(
          key: const Key('register-search'),
          controller: _search,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
              labelText: s.t('reg.search'),
              prefixIcon: const Icon(Icons.search)),
          onSubmitted: (v) => _apply(_filters.copyWith(query: v.trim())),
        ),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilterChip(
            key: const Key('register-needs-review'),
            label: Text(s.t('reg.status.needs_review')),
            selected: _filters.status == 'needs_review',
            onSelected: (on) => _apply(
                _filters.copyWith(status: () => on ? 'needs_review' : null)),
          ),
          FilterChip(
            label: Text(s.t('reg.status.uncategorized')),
            selected: _filters.status == 'uncategorized',
            onSelected: (on) => _apply(
                _filters.copyWith(status: () => on ? 'uncategorized' : null)),
          ),
          if (_filters.narrowed)
            ActionChip(
                key: const Key('register-clear'),
                label: Text(s.t('reg.clear')),
                onPressed: () {
                  _search.clear();
                  _apply(const RegisterFilters());
                }),
        ]),
        ...switch (_phase) {
          RecordsPhase.loading => [
              AnumFeedback(
                  kind: AnumFeedbackKind.loading, message: s.t('reg.loading'))
            ],
          RecordsPhase.denied => [
              AnumFeedback(
                  kind: AnumFeedbackKind.permission, message: s.t('reg.denied'))
            ],
          RecordsPhase.error => [
              AnumFeedback(
                  kind: _failure?.offline == true
                      ? AnumFeedbackKind.offline
                      : AnumFeedbackKind.error,
                  message:
                      s.t('reg.failed', {'message': _failure?.message ?? ''}),
                  onRetry: _load)
            ],
          RecordsPhase.ready when _rows.isEmpty => [
              _filters.narrowed
                  ? AnumEmpty(
                      title: s.t('reg.noMatch'),
                      message: s.t('reg.noMatchText'),
                      icon: Icons.filter_alt_off_outlined,
                      action: s.t('reg.clear'),
                      onAction: () {
                        _search.clear();
                        _apply(const RegisterFilters());
                      })
                  : AnumEmpty(
                      title: s.t('reg.empty'),
                      message: s.t('reg.emptyText'),
                      icon: Icons.receipt_long_outlined)
            ],
          RecordsPhase.ready => [
              if (_balances.isNotEmpty)
                AnumSurface(
                  key: const Key('register-balances'),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        AnumHeader(
                            title: s.t('reg.balances', {
                          'where': _filters.accountId == null
                              ? s.t('reg.allAccounts')
                              : names[_filters.accountId] ??
                                  s.t('reg.allAccounts'),
                        })),
                        for (final b in _balances) ...[
                          RecordsField(s.t('reg.beginning'),
                              formatAmount(b.beginning, b.currency)),
                          RecordsField(s.t('reg.ending'),
                              formatAmount(b.ending, b.currency)),
                        ],
                        Text(s.t('reg.balancesHint'),
                            style: TextStyle(
                                color: context.palette.muted,
                                fontSize: AnumType.sm)),
                      ]),
                ),
              AnumSurface(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
                child: Column(children: [
                  for (final r in _rows)
                    AnumRow(
                      key: Key('reg-${r.id}'),
                      title: r.description,
                      subtitle: [
                        r.postedOn,
                        names[r.accountId] ?? '-',
                        f.t('category.${r.category}'),
                        if (typeLabel(rs, r.transactionType) case final t?) t,
                        s.t('origin.${r.origin}'),
                        if (r.status != 'recorded' && r.status != 'linked')
                          s.t('reg.status.${r.status}'),
                      ].join(' · '),
                      detail: r.runningBalance == null
                          ? formatAmount(r.amount, r.currency)
                          : '${formatAmount(r.amount, r.currency)}\n${s.t('reg.balanceAfter', {
                                  'amount': formatAmount(
                                      r.runningBalance!, r.currency)
                                })}',
                      icon: r.status == 'recorded' || r.status == 'linked'
                          ? (r.moneyIn ? Icons.south_west : Icons.north_east)
                          : Icons.help_outline,
                      tone: r.status == 'recorded' || r.status == 'linked'
                          ? (r.moneyIn ? AnumTone.ok : null)
                          : AnumTone.warn,
                      onTap: () async {
                        await widget.openTransaction(context, r);
                        if (mounted) _load();
                      },
                    ),
                ]),
              ),
              if (_rows.length < _total)
                OutlinedButton(
                  key: const Key('register-more'),
                  onPressed: _more ? null : () => _load(append: true),
                  child: Text(s
                      .t('reg.more', {'shown': _rows.length, 'total': _total})),
                ),
            ],
        },
      ]),
    );
  }
}

class _FilterSheet extends StatefulWidget {
  const _FilterSheet({required this.filters, required this.accounts});
  final RegisterFilters filters;
  final List<FinanceAccount> accounts;
  @override
  State<_FilterSheet> createState() => _FilterSheetState();
}

class _FilterSheetState extends State<_FilterSheet> {
  late RegisterFilters _f = widget.filters;

  Widget _pick(String label, String? value, List<(String, String)> options,
      void Function(String?) set, Key key) {
    final s = FinanceCoreStrings.of(context);
    return DropdownButtonFormField<String?>(
      key: key,
      initialValue: value,
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: [
        DropdownMenuItem<String?>(value: null, child: Text(s.t('reg.any'))),
        for (final (v, l) in options)
          DropdownMenuItem<String?>(
              value: v, child: Text(l, overflow: TextOverflow.ellipsis)),
      ],
      onChanged: (v) => setState(() => set(v)),
    );
  }

  Future<void> _date(bool start) async {
    final current = DateTime.tryParse((start ? _f.from : _f.to) ?? '');
    final picked = await showDatePicker(
        context: context,
        initialDate: current ?? DateTime.now(),
        firstDate: DateTime(2000),
        lastDate: DateTime(2100));
    if (picked == null) return;
    final iso = picked.toIso8601String().substring(0, 10);
    setState(() =>
        _f = start ? _f.copyWith(from: () => iso) : _f.copyWith(to: () => iso));
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceCoreStrings.of(context);
    final f = FinanceStrings.of(context);
    final rs = FinanceReviewStrings.of(context);
    return SafeArea(
      child: SingleChildScrollView(
        padding: EdgeInsets.fromLTRB(AnumSpacing.md, 0, AnumSpacing.md,
            AnumSpacing.md + MediaQuery.viewInsetsOf(context).bottom),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(s.t('reg.filters'),
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: AnumSpacing.sm),
              _pick(
                  s.t('reg.account'),
                  _f.accountId,
                  [for (final a in widget.accounts) (a.id, a.name)],
                  (v) => _f = _f.copyWith(accountId: () => v),
                  const Key('filter-account')),
              _pick(
                  s.t('reg.category'),
                  _f.category,
                  [for (final c in financeCategories) (c, f.t('category.$c'))],
                  (v) => _f = _f.copyWith(category: () => v),
                  const Key('filter-category')),
              _pick(
                  s.t('reg.type'),
                  _f.type,
                  [for (final t in _types) (t, typeLabel(rs, t) ?? t)],
                  (v) => _f = _f.copyWith(type: () => v),
                  const Key('filter-type')),
              _pick(
                  s.t('reg.status'),
                  _f.status,
                  [for (final st in _statuses) (st, s.t('reg.status.$st'))],
                  (v) => _f = _f.copyWith(status: () => v),
                  const Key('filter-status')),
              _pick(
                  s.t('reg.direction'),
                  _f.direction,
                  [
                    ('in', s.t('reg.direction.in')),
                    ('out', s.t('reg.direction.out'))
                  ],
                  (v) => _f = _f.copyWith(direction: () => v),
                  const Key('filter-direction')),
              const SizedBox(height: AnumSpacing.xs),
              Wrap(spacing: AnumSpacing.sm, children: [
                OutlinedButton.icon(
                    key: const Key('filter-from'),
                    onPressed: () => _date(true),
                    icon: const Icon(Icons.event_outlined),
                    label: Text('${s.t('reg.from')}: ${_f.from ?? '-'}')),
                OutlinedButton.icon(
                    key: const Key('filter-to'),
                    onPressed: () => _date(false),
                    icon: const Icon(Icons.event_outlined),
                    label: Text('${s.t('reg.to')}: ${_f.to ?? '-'}')),
              ]),
              const SizedBox(height: AnumSpacing.sm),
              Wrap(
                  alignment: WrapAlignment.end,
                  spacing: AnumSpacing.sm,
                  children: [
                    TextButton(
                        onPressed: () => Navigator.of(context)
                            .pop(RegisterFilters(query: _f.query)),
                        child: Text(s.t('reg.clear'))),
                    FilledButton(
                        key: const Key('filter-apply'),
                        onPressed: () => Navigator.of(context).pop(_f),
                        child: Text(s.t('reg.apply'))),
                  ]),
            ]),
      ),
    );
  }
}

// -- one transaction -----------------------------------------------------------

/// A transaction's evidence: where it came from (with the masked message
/// while it is kept), who set its category (and changing it), the records it
/// is linked to with the evidence behind each, related refunds and fees, and
/// its private note.
class TransactionEvidenceSection extends StatefulWidget {
  const TransactionEvidenceSection(
      {required this.repository,
      required this.transactionId,
      this.onOpen,
      this.onChanged,
      this.onDeleted,
      this.pickFile = pickVaultFile,
      super.key});
  final FinanceCoreRepository repository;
  final String transactionId;
  final void Function(String id)? onOpen;
  final Future<void> Function()? onChanged;

  /// After the owner deleted this copy of a duplicate.
  final VoidCallback? onDeleted;

  /// Picks a receipt file (the system picker; a fake in tests).
  final VaultFilePicker pickFile;

  @override
  State<TransactionEvidenceSection> createState() =>
      _TransactionEvidenceSectionState();
}

class _TransactionEvidenceSectionState
    extends State<TransactionEvidenceSection> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  RegisterDetail? _detail;
  final _note = TextEditingController();
  bool _busy = false;
  String? _message;
  bool _messageError = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await widget.repository.detail(widget.transactionId);
      _detail = d;
      _note.text = d.row.notes ?? '';
      _failure = null;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _save(Future<void> Function() call, String done) async {
    final s = FinanceCoreStrings.of(context);
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await call();
      _message = s.t(done);
      _messageError = false;
      await _load();
      await widget.onChanged?.call();
    } on Object catch (error) {
      _message = s.t('tx.saveFailed',
          {'message': error is ApiException ? error.readable : '$error'});
      _messageError = true;
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceCoreStrings.of(context);
    switch (_phase) {
      case RecordsPhase.loading:
        return AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('tx.loading'));
      case RecordsPhase.denied:
        return AnumFeedback(
            kind: AnumFeedbackKind.permission, message: s.t('reg.denied'));
      case RecordsPhase.error:
        return AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('tx.failed', {'message': _failure?.message ?? ''}),
            onRetry: _load);
      case RecordsPhase.ready:
        break;
    }
    final d = _detail!;
    final row = d.row;
    final kept = d.sources.where((m) => m.text != null).firstOrNull;
    return Column(
        key: const Key('tx-evidence'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: s.t('tx.evidence')),
                  RecordsField(s.t('tx.account'),
                      d.accountName.isEmpty ? '-' : d.accountName),
                  RecordsField(
                      s.t('tx.categoryBy'), s.t('tx.by.${d.categoryBy}')),
                  RecordsField(
                      s.t('tx.evidence'),
                      [
                        for (final o in {row.origin, ...row.sources})
                          s.maybe('origin.$o') ?? s.label('origin', o)
                      ].join(', ')),
                  if (d.reference != null)
                    RecordsField(s.t('tx.reference'), d.reference),
                  Text(
                      s.t(row.reconciled
                          ? 'tx.reconciled'
                          : 'tx.notReconciled'),
                      style: _muted(context)),
                  if (kept != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(s.t('tx.smsText'),
                        style: Theme.of(context).textTheme.titleSmall),
                    Text(kept.text!, key: const Key('tx-sms-text')),
                  ],
                ]),
          ),
          TransactionCorrectionPanel(
              repository: widget.repository,
              detail: d,
              onOpen: widget.onOpen,
              onDeleted: widget.onDeleted,
              pickFile: widget.pickFile,
              onSaved: () async {
                await _load();
                await widget.onChanged?.call();
              }),
          AnumSurface(
            key: const Key('tx-links'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                      title: s.t('tx.links'),
                      subtitle: d.splitCount > 1
                          ? s.t('tx.split', {'n': d.splitCount})
                          : null),
                  if (d.links.isEmpty)
                    Text(s.t('tx.linksNone'), style: _muted(context))
                  else ...[
                    for (final l in d.links)
                      AnumRow(
                        title: l.targetName ??
                            (s.maybe('tx.dim.${l.dimension}') ?? l.dimension),
                        subtitle: [
                          s.maybe('tx.dim.${l.dimension}') ?? l.dimension,
                          if (l.amount != null && l.currency != null)
                            formatAmount(l.amount!, l.currency!),
                        ].join(' · '),
                        icon: Icons.link,
                        trailing: AnumPill(
                            key: Key('link-evidence-${l.id}'),
                            label: s.t('tx.evidence.${l.evidence}'),
                            tone: l.evidence == 'verified'
                                ? AnumTone.ok
                                : l.evidence == 'pending'
                                    ? AnumTone.warn
                                    : AnumTone.info),
                      ),
                    Text(s.t('tx.evidenceHint'), style: _muted(context)),
                  ],
                  if (d.related.isNotEmpty) ...[
                    const SizedBox(height: AnumSpacing.sm),
                    Text(s.t('tx.related'),
                        style: Theme.of(context).textTheme.titleSmall),
                    for (final r in d.related)
                      AnumRow(
                        key: Key('related-${r.id}'),
                        title:
                            '${s.t('tx.rel.${r.relation}')}: ${r.description}',
                        subtitle: r.postedOn,
                        detail: formatAmount(r.amount, r.currency),
                        icon: Icons.compare_arrows,
                        onTap: widget.onOpen == null
                            ? null
                            : () => widget.onOpen!(r.id),
                      ),
                  ],
                ]),
          ),
          AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    key: const Key('tx-note'),
                    controller: _note,
                    maxLength: 1000,
                    minLines: 1,
                    maxLines: 4,
                    decoration: InputDecoration(
                        labelText: s.t('tx.note'),
                        helperText: s.t('tx.noteHint')),
                  ),
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: OutlinedButton(
                      key: const Key('tx-note-save'),
                      onPressed: _busy
                          ? null
                          : () => _save(
                              () => widget.repository
                                  .saveNote(row.id, _note.text),
                              'tx.noteSaved'),
                      child: Text(s.t('tx.noteSave')),
                    ),
                  ),
                ]),
          ),
          if (_message != null)
            Text(_message!,
                key: const Key('tx-message'),
                style: TextStyle(
                    color: _messageError
                        ? Theme.of(context).colorScheme.error
                        : context.palette.muted)),
        ]);
  }
}

// -- salary month by month -------------------------------------------------------

/// Each month from the joining date, newest first: expected against
/// received, the status and the salary found (`GET /salary/months`).
class SalaryHistoryScreen extends StatefulWidget {
  const SalaryHistoryScreen({required this.repository, super.key});
  final FinanceCoreRepository repository;
  @override
  State<SalaryHistoryScreen> createState() => _SalaryHistoryScreenState();
}

class _SalaryHistoryScreenState extends State<SalaryHistoryScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  final List<SalaryMonth> _months = [];
  int _total = 0;
  bool _more = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool append = false}) async {
    setState(() {
      if (!append) _phase = RecordsPhase.loading;
      _more = append;
    });
    try {
      final (rows, total, _) = await widget.repository
          .salaryMonths(offset: append ? _months.length : 0);
      if (!append) _months.clear();
      _months.addAll(rows);
      _total = total;
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() => _more = false);
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceCoreStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('salary.historyTitle'))),
      body: AnumPage(onRefresh: _load, children: [
        ...switch (_phase) {
          RecordsPhase.loading => [
              AnumFeedback(
                  kind: AnumFeedbackKind.loading,
                  message: s.t('salary.loading'))
            ],
          RecordsPhase.denied => [
              AnumFeedback(
                  kind: AnumFeedbackKind.permission, message: s.t('reg.denied'))
            ],
          RecordsPhase.error => [
              AnumFeedback(
                  kind: AnumFeedbackKind.error,
                  message: s
                      .t('salary.failed', {'message': _failure?.message ?? ''}),
                  onRetry: _load)
            ],
          RecordsPhase.ready when _months.isEmpty => [
              AnumEmpty(
                  title: s.t('salary.empty'),
                  message: s.t('salary.emptyText'),
                  icon: Icons.payments_outlined)
            ],
          RecordsPhase.ready => [
              for (final m in _months)
                AnumSurface(
                  key: Key('salary-${m.month}'),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        AnumHeader(
                            title: m.month,
                            subtitle: s.t('salary.payOn', {'date': m.payOn}),
                            trailing: AnumPill(
                                label: s.t('salary.status.${m.status}'),
                                tone: m.status == 'received'
                                    ? AnumTone.ok
                                    : m.status == 'upcoming'
                                        ? AnumTone.info
                                        : AnumTone.warn)),
                        Text(s.t('salary.expected', {
                          'expected': formatAmount(m.expectedNet, m.currency),
                          'received': formatAmount(m.received, m.currency)
                        })),
                        if ((double.tryParse(m.difference) ?? 0) != 0)
                          Text(
                              s.t('salary.difference', {
                                'amount': formatAmount(m.difference, m.currency)
                              }),
                              style: _muted(context)),
                        if (m.found case final found?)
                          Text(
                              s.t('salary.found', {
                                'description': found.description,
                                'date': found.postedOn
                              }),
                              style: _muted(context)),
                        if (m.basis != null)
                          Text(s.t('salary.basis.${m.basis}'),
                              style: _muted(context)),
                      ]),
                ),
              if (_months.length < _total)
                OutlinedButton(
                    onPressed: _more ? null : () => _load(append: true),
                    child: Text(s.t('reg.more',
                        {'shown': _months.length, 'total': _total}))),
            ],
        },
      ]),
    );
  }
}
