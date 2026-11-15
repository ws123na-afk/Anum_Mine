import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_budget_data.dart';
import 'finance_budget_strings.dart';
import 'finance_models.dart';
import 'finance_strings.dart';

/// Budgets and the salary plan, recurring bills and subscriptions, and
/// spending categories on the phone (docs/finance.md#budgets-and-planning,
/// #recurring-payments, #category-ontology, #learned-merchants). Native
/// screens over the same API as the web; every figure is the API's.

/// A list tile on the page's own background: its ink needs a Material of its own.
Widget _tile(Widget child) =>
    Material(type: MaterialType.transparency, child: child);

String _signed(String amount, String currency) =>
    '${(double.tryParse(amount) ?? 0) > 0 ? '+' : ''}${formatAmount(amount, currency)}';

AnumTone _riskTone(String risk) => switch (risk) {
      'over' || 'high' => AnumTone.stop,
      'watch' || 'behind' => AnumTone.warn,
      'unknown' => AnumTone.neutral,
      _ => AnumTone.ok,
    };

/// A load-and-show state shared by the three screens.
mixin _Loads<W extends StatefulWidget, T> on State<W> {
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  T? value;
  String? flash;
  bool flashError = false;

  Future<T> fetch();

  Future<void> load() async {
    setState(() => phase = value == null ? RecordsPhase.loading : phase);
    try {
      final next = await fetch();
      if (!mounted) return;
      setState(() {
        value = next;
        phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final f = RecordsFailure.from(error);
      setState(() {
        failure = f;
        phase = f.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> act(Future<void> Function() run, String done) async {
    try {
      await run();
      if (!mounted) return;
      setState(() {
        flash = done;
        flashError = false;
      });
      await load();
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        flash = RecordsFailure.from(error).message;
        flashError = true;
      });
    }
  }

  Widget? flashView(BuildContext context) => flash == null
      ? null
      : AnumRow(
          key: const Key('finance-budget-flash'),
          icon: flashError ? Icons.error_outline : Icons.check_circle_outline,
          tone: flashError ? AnumTone.stop : AnumTone.ok,
          title: flash!);

  Future<bool> confirm(
      BuildContext context, String title, String text, String action) async {
    final m = FinanceBudgetStrings.of(context);
    return await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
                  title: Text(title),
                  content: Text(text),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(c, false),
                        child: Text(m.t('cancel'))),
                    FilledButton(
                        onPressed: () => Navigator.pop(c, true),
                        child: Text(action)),
                  ],
                )) ??
        false;
  }
}

// -- budgets and the salary plan ----------------------------------------------------

class BudgetsScreen extends StatefulWidget {
  const BudgetsScreen({required this.repository, this.base = 'AED', super.key});
  final FinanceBudgetRepository repository;
  final String base;

  @override
  State<BudgetsScreen> createState() => _BudgetsScreenState();
}

typedef _BudgetPage = ({
  PlanReport report,
  List<AllocationView> allocation,
  CategoryTree? tree
});

class _BudgetsScreenState extends State<BudgetsScreen>
    with _Loads<BudgetsScreen, _BudgetPage> {
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  Future<_BudgetPage> fetch() async {
    final report = await widget.repository.budgets();
    final allocation = await widget.repository.allocation();
    CategoryTree? tree;
    try {
      tree = await widget.repository.categories();
    } on Object {
      tree = null; // names fall back to the category labels
    }
    return (report: report, allocation: allocation, tree: tree);
  }

  Future<void> _edit(PlanBudget? budget) async {
    final m = FinanceBudgetStrings.of(context);
    final saved = await Navigator.of(context).push<String>(MaterialPageRoute(
        builder: (_) => BudgetFormScreen(
            repository: widget.repository,
            budget: budget,
            tree: value?.tree,
            base: widget.base)));
    if (saved != null && mounted) {
      setState(() {
        flash = m.t('budgets.saved', {'name': saved});
        flashError = false;
      });
      await load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceBudgetStrings.of(context);
    final page = value;
    return Scaffold(
      appBar: AppBar(title: Text(m.t('budgets.title'))),
      floatingActionButton: phase == RecordsPhase.ready
          ? FloatingActionButton.extended(
              key: const Key('budget-new'),
              onPressed: () => _edit(null),
              icon: const Icon(Icons.add),
              label: Text(m.t('budgets.new')))
          : null,
      body: AnumPage(onRefresh: load, children: [
        if (phase != RecordsPhase.ready || page == null)
          RecordsStateView(
              phase: phase,
              failure: failure,
              deniedHint: m.t('denied'),
              onRetry: load)
        else
          ..._body(context, page),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, _BudgetPage page) {
    final m = FinanceBudgetStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return [
      if (flashView(context) != null) flashView(context)!,
      Text(m.t('budgets.intro'), style: muted),
      if (page.report.budgets.isEmpty)
        AnumEmpty(
            icon: Icons.savings_outlined,
            title: m.t('budgets.empty'),
            message: m.t('budgets.emptyText'),
            action: m.t('budgets.new'),
            onAction: () => _edit(null))
      else ...[
        for (final t in page.report.totals)
          Text(
              m.t('budgets.totals', {
                'currency': t.currency,
                'spent': formatAmount(t.spent, t.currency),
                'budget': formatAmount(t.budget, t.currency)
              }),
              style: muted),
        for (final b in page.report.budgets) _budgetCard(context, b, page.tree),
      ],
      AnumHeader(title: m.t('alloc.title')),
      for (final a in page.allocation) _allocation(context, a),
    ];
  }

  String _categoryName(BuildContext context, String id, CategoryTree? tree) {
    final arabic = FinanceBudgetStrings.of(context).arabic;
    for (final g in tree?.groups ?? const <CategoryGroup>[]) {
      for (final c in g.categories) {
        if (c.id == id) return c.label(arabic);
      }
    }
    return FinanceStrings.of(context).t('category.$id');
  }

  Widget _budgetCard(BuildContext context, PlanBudget b, CategoryTree? tree) {
    final m = FinanceBudgetStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final percent = ((b.percent ?? 0).clamp(0, 100)) / 100;
    return AnumSurface(
      key: Key('budget-${b.id}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumRow(
          icon: Icons.account_balance_wallet_outlined,
          tone: _riskTone(b.risk),
          title: b.name,
          subtitle: [
            m.t('risk.${b.risk}'),
            m.t('kind.${b.kind}'),
            m.t('period.${b.period}'),
            if (!b.active) m.t('budgets.paused'),
          ].join(' · '),
          trailing: PopupMenuButton<String>(
            key: Key('budget-menu-${b.id}'),
            onSelected: (choice) async {
              if (choice == 'edit') return _edit(b);
              if (choice == 'pause') {
                return act(
                    () => widget.repository
                        .saveBudget(b.body(active: !b.active), id: b.id),
                    m.t('budgets.saved', {'name': b.name}));
              }
              if (await confirm(
                  context,
                  m.t('budgets.deleteTitle', {'name': b.name}),
                  m.t('budgets.deleteText'),
                  m.t('budgets.delete'))) {
                await act(() => widget.repository.deleteBudget(b.id),
                    m.t('budgets.deleted', {'name': b.name}));
              }
            },
            itemBuilder: (_) => [
              PopupMenuItem(value: 'edit', child: Text(m.t('budgets.edit'))),
              PopupMenuItem(
                  value: 'pause',
                  child: Text(
                      b.active ? m.t('budgets.pause') : m.t('budgets.resume'))),
              PopupMenuItem(
                  value: 'delete', child: Text(m.t('budgets.delete'))),
            ],
          ),
        ),
        Text(m.t('budgets.period', {'from': b.periodStart, 'to': b.periodEnd}),
            style: muted),
        if (b.categories.isNotEmpty)
          Text(
              b.categories
                  .map((c) => _categoryName(context, c, tree))
                  .join(' · '),
              style: muted),
        if (b.spent != null) ...[
          const SizedBox(height: AnumSpacing.sm),
          Semantics(
            label: '${b.name}: ${b.percent ?? 0}%',
            child: LinearProgressIndicator(
                value: percent,
                color: anumToneColor(context, _riskTone(b.risk))),
          ),
          const SizedBox(height: AnumSpacing.sm),
          Text(m.t('budgets.spentOf', {
            'spent': formatAmount(b.spent!, b.currency),
            'amount': formatAmount(b.available ?? b.amount, b.currency)
          })),
          if ((double.tryParse(b.over) ?? 0) > 0)
            Text(
                m.t('budgets.over',
                    {'amount': formatAmount(b.over, b.currency)}),
                style: TextStyle(color: anumToneColor(context, AnumTone.stop)))
          else if (b.remaining != null)
            Text(m.t('budgets.left',
                {'amount': formatAmount(b.remaining!, b.currency)})),
          if ((double.tryParse(b.coming) ?? 0) > 0)
            Text(m.t('budgets.coming',
                {'amount': formatAmount(b.coming, b.currency)})),
          if (b.forecast != null && b.kind == 'spending')
            Text(
                m.t('budgets.forecast', {
                  'date': b.periodEnd,
                  'amount': formatAmount(b.forecast!, b.currency)
                }),
                style: muted),
          for (final c in b.comingItems)
            RecordsField(
                '${c.name} · ${c.dueOn}', formatAmount(c.amount, b.currency)),
          if (b.kind == 'spending' && b.showsRollover) _rollover(context, b),
        ] else
          Text(
              m.t('budgets.noRecord', {
                'saved': formatAmount(b.goalSaved ?? '0', b.currency),
                'target': formatAmount(b.goalTarget ?? '0', b.currency)
              }),
              style: muted),
      ]),
    );
  }

  /// Rollover (docs/finance.md#budget-rollover): what came in, what is
  /// available, the setting, and the chain of periods with the setting each
  /// one used.
  Widget _rollover(BuildContext context, PlanBudget b) {
    final m = FinanceBudgetStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final carried = double.tryParse(b.carriedIn) ?? 0;
    return Column(
        key: Key('budget-rollover-${b.id}'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Divider(),
          Text(carried < 0
              ? m.t('roll.deficitIn', {
                  'amount': formatAmount(
                      b.carriedIn.replaceFirst('-', ''), b.currency)
                })
              : m.t('roll.carriedIn',
                  {'amount': formatAmount(b.carriedIn, b.currency)})),
          Text(m.t('roll.available', {
            'amount': formatAmount(b.available ?? b.amount, b.currency),
            'base': formatAmount(b.amount, b.currency)
          })),
          Text(
              rolloverWords(
                  m, b.rollover, b.rolloverCap, b.overspend, b.currency),
              style: muted),
          if (b.chain.isNotEmpty)
            ExpansionTile(
              key: Key('budget-chain-${b.id}'),
              tilePadding: EdgeInsets.zero,
              childrenPadding: EdgeInsets.zero,
              title: Text(m.t('roll.chain', {'count': b.chainPeriods})),
              children: [
                for (final p in b.chain.reversed)
                  Padding(
                    padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              m.t('budgets.period',
                                  {'from': p.start, 'to': p.end}),
                              style:
                                  const TextStyle(fontWeight: FontWeight.w600)),
                          Text(
                              [
                                rolloverShort(m, p.rollover, p.cap, p.overspend,
                                    p.currency),
                                if (p.restarted) m.t('roll.restarted'),
                              ].join(' · '),
                              style: muted),
                          RecordsField(m.t('roll.in'),
                              formatAmount(p.carriedIn, p.currency)),
                          RecordsField(m.t('roll.availableCol'),
                              formatAmount(p.available, p.currency)),
                          RecordsField(m.t('roll.spent'),
                              formatAmount(p.spent, p.currency)),
                          RecordsField(
                              m.t('roll.out'),
                              p.carriedOut == null
                                  ? m.t('roll.open')
                                  : formatAmount(p.carriedOut!, p.currency)),
                        ]),
                  ),
                if (b.chainPeriods > b.chain.length)
                  Text(
                      m.t('roll.older',
                          {'count': b.chainPeriods - b.chain.length}),
                      style: muted),
              ],
            ),
        ]);
  }

  Widget _allocation(BuildContext context, AllocationView a) {
    final m = FinanceBudgetStrings.of(context);
    final unallocated = double.tryParse(a.unallocated ?? '') ?? 0;
    return AnumSurface(
      key: Key('allocation-${a.currency}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (a.income != null)
          AnumRow(
              icon: Icons.payments_outlined,
              title: m.t('alloc.income',
                  {'amount': formatAmount(a.income!, a.currency)}))
        else
          Text(m.t('alloc.noIncome', {'currency': a.currency})),
        for (final bucket in allocationBuckets)
          if ((double.tryParse(a.buckets[bucket] ?? '0') ?? 0) > 0)
            RecordsField(m.t('bucket.$bucket'),
                formatAmount(a.buckets[bucket]!, a.currency)),
        if (a.unallocated != null)
          Text(
              unallocated >= 0
                  ? m.t('alloc.unallocated',
                      {'amount': formatAmount(a.unallocated!, a.currency)})
                  : m.t('alloc.overplanned', {
                      'amount': formatAmount(
                          (-unallocated).toStringAsFixed(2), a.currency)
                    }),
              style: unallocated < 0
                  ? TextStyle(color: anumToneColor(context, AnumTone.stop))
                  : null),
        for (final name in a.leftOut)
          Text(m.t('alloc.leftOut', {'name': name})),
      ]),
    );
  }
}

/// The words for a rollover setting ("What is left carries over, at most
/// AED 150.00; an overspend is not carried").
String rolloverWords(FinanceBudgetStrings m, String rollover, String? cap,
    String overspend, String currency) {
  final surplus = rollover == 'capped' && cap != null
      ? m.t('roll.cappedAt', {'amount': formatAmount(cap, currency)})
      : m.t('roll.mode.$rollover');
  return '$surplus${m.t('roll.join')}${m.t('roll.over.$overspend')}';
}

/// The short words for one period's setting in the history.
String rolloverShort(FinanceBudgetStrings m, String rollover, String? cap,
    String overspend, String currency) {
  final surplus = rollover == 'capped' && cap != null
      ? m.t('roll.short.capped', {'amount': formatAmount(cap, currency)})
      : m.t('roll.short.${rollover == 'capped' ? 'surplus' : rollover}');
  return overspend == 'carry'
      ? '$surplus · ${m.t('roll.short.deficit')}'
      : surplus;
}

/// What is missing before a budget can be sent (null: ready). The API checks again.
String? budgetFormProblem(FinanceBudgetStrings m,
    {required String name,
    required String amount,
    required String currency,
    required String kind,
    required String period,
    required Set<String> categories,
    String? goalId,
    String startsOn = '',
    String endsOn = '',
    String rollover = 'none',
    String rolloverCap = ''}) {
  if (name.trim().isEmpty) return m.t('form.needName');
  final value = double.tryParse(amount.trim());
  if (value == null || value <= 0) return m.t('form.needAmount');
  if (!RegExp(r'^[A-Za-z]{3}$').hasMatch(currency.trim())) {
    return m.t('form.needCurrency');
  }
  if (kind == 'spending' && categories.isEmpty) return m.t('form.needCategory');
  if (savingKinds.contains(kind) && (goalId == null || goalId.isEmpty)) {
    return m.t('form.needGoal');
  }
  if (kind == 'spending' && rollover == 'capped') {
    final cap = double.tryParse(rolloverCap.trim());
    if (cap == null || cap <= 0) return m.t('form.needCap');
  }
  if (period == 'custom') {
    final start = DateTime.tryParse(startsOn.trim());
    final end = DateTime.tryParse(endsOn.trim());
    if (start == null || end == null || end.isBefore(start)) {
      return m.t('form.needDates');
    }
  }
  return null;
}

class BudgetFormScreen extends StatefulWidget {
  const BudgetFormScreen(
      {required this.repository,
      this.budget,
      this.tree,
      this.base = 'AED',
      super.key});
  final FinanceBudgetRepository repository;
  final PlanBudget? budget;
  final CategoryTree? tree;
  final String base;

  @override
  State<BudgetFormScreen> createState() => _BudgetFormScreenState();
}

class _BudgetFormScreenState extends State<BudgetFormScreen> {
  late final _name = TextEditingController(text: widget.budget?.name ?? '');
  late final _amount = TextEditingController(text: widget.budget?.amount ?? '');
  late final _currency =
      TextEditingController(text: widget.budget?.currency ?? widget.base);
  // The budget's own dates: a custom budget that carries money repeats, so
  // the period shown is not its first window.
  late final _start = TextEditingController(
      text: widget.budget != null && widget.budget!.period != 'monthly'
          ? (widget.budget!.startsOn ?? widget.budget!.periodStart)
          : '');
  late final _end = TextEditingController(
      text: widget.budget?.period == 'custom'
          ? (widget.budget!.endsOn ?? widget.budget!.periodEnd)
          : '');
  late String _rollover = widget.budget?.rollover ?? 'none';
  late String _overspend = widget.budget?.overspend ?? 'absorb';
  late final _cap =
      TextEditingController(text: widget.budget?.rolloverCap ?? '');
  late String _kind = widget.budget?.kind ?? 'spending';
  late String _period = widget.budget?.period ?? 'monthly';
  late final Set<String> _categories = {...?widget.budget?.categories};
  late bool _fixed = widget.budget?.fixed ?? false;
  late bool _essential = widget.budget?.essential ?? true;
  late bool _reserve = widget.budget?.reserve ?? false;
  late bool _active = widget.budget?.active ?? true;
  late int _alert = widget.budget?.alertPercent ?? 80;
  late String? _goal = widget.budget?.goalId;
  List<GoalChoice> _goals = const [];
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    widget.repository.goals().then((g) {
      if (mounted) setState(() => _goals = g);
    }).catchError((Object _) {});
  }

  @override
  void dispose() {
    for (final c in [_name, _amount, _currency, _start, _end, _cap]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final m = FinanceBudgetStrings.of(context);
    final problem = budgetFormProblem(m,
        name: _name.text,
        amount: _amount.text,
        currency: _currency.text,
        kind: _kind,
        period: _period,
        categories: _categories,
        goalId: _goal,
        startsOn: _start.text,
        endsOn: _end.text,
        rollover: _rollover,
        rolloverCap: _cap.text);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final body = <String, Object?>{
      'name': _name.text.trim(),
      'kind': _kind,
      'period': _period,
      'currency': _currency.text.trim().toUpperCase(),
      'amount': _amount.text.trim(),
      'categories': _kind == 'spending' ? _categories.toList() : <String>[],
      'subcategory_ids': widget.budget?.subcategoryIds ?? <String>[],
      'starts_on': _period == 'monthly' || _start.text.trim().isEmpty
          ? null
          : _start.text.trim(),
      'ends_on': _period == 'custom' ? _end.text.trim() : null,
      'fixed': _fixed,
      'essential': _essential,
      'allocation': null,
      'goal_id': savingKinds.contains(_kind) ? _goal : null,
      'debt_ids': widget.budget?.debtIds ?? <String>[],
      'alert_percent': _alert,
      'reserve_in_safe_to_spend': _kind == 'spending' && _reserve,
      'active': _active,
      'notes': widget.budget?.notes,
      if (_kind == 'spending') ...{
        'rollover': _rollover,
        'rollover_cap': _rollover == 'capped' ? _cap.text.trim() : null,
        'overspend': _overspend,
      },
    };
    try {
      await widget.repository.saveBudget(body, id: widget.budget?.id);
      if (mounted) Navigator.of(context).pop(_name.text.trim());
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = RecordsFailure.from(error).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceBudgetStrings.of(context);
    final arabic = m.arabic;
    final categories =
        widget.tree?.spendingCategories.toList() ?? const <CategoryNode>[];
    return Scaffold(
      appBar: AppBar(
          title: Text(widget.budget == null
              ? m.t('budgets.new')
              : widget.budget!.name)),
      body: AnumPage(children: [
        TextField(
            key: const Key('budget-name'),
            controller: _name,
            maxLength: 80,
            decoration: InputDecoration(labelText: m.t('form.name'))),
        DropdownButtonFormField<String>(
          isExpanded: true,
          key: const Key('budget-kind'),
          initialValue: _kind,
          decoration: InputDecoration(labelText: m.t('form.kind')),
          items: [
            for (final k in budgetKinds)
              DropdownMenuItem(value: k, child: Text(m.t('kind.$k')))
          ],
          onChanged: (v) => setState(() => _kind = v ?? _kind),
        ),
        DropdownButtonFormField<String>(
          isExpanded: true,
          key: const Key('budget-period'),
          initialValue: _period,
          decoration: InputDecoration(labelText: m.t('form.period')),
          items: [
            for (final p in budgetPeriods)
              DropdownMenuItem(value: p, child: Text(m.t('period.$p')))
          ],
          onChanged: (v) => setState(() => _period = v ?? _period),
        ),
        TextField(
            key: const Key('budget-amount'),
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: m.t('form.amount'))),
        TextField(
            key: const Key('budget-currency'),
            controller: _currency,
            maxLength: 3,
            textCapitalization: TextCapitalization.characters,
            decoration: InputDecoration(labelText: m.t('form.currency'))),
        if (_period != 'monthly')
          TextField(
              key: const Key('budget-start'),
              controller: _start,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(labelText: m.t('form.startsOn'))),
        if (_period == 'custom')
          TextField(
              key: const Key('budget-end'),
              controller: _end,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(labelText: m.t('form.endsOn'))),
        if (_kind == 'spending') ...[
          AnumHeader(title: m.t('form.categories')),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            for (final c in categories)
              FilterChip(
                key: Key('budget-cat-${c.id}'),
                label: Text(c.label(arabic)),
                selected: _categories.contains(c.id),
                onSelected: (on) => setState(() =>
                    on ? _categories.add(c.id) : _categories.remove(c.id)),
              ),
          ]),
          _tile(CheckboxListTile(
              value: _fixed,
              onChanged: (v) => setState(() => _fixed = v ?? false),
              title: Text(m.t('form.fixed')))),
          _tile(CheckboxListTile(
              value: _essential,
              onChanged: (v) => setState(() => _essential = v ?? true),
              title: Text(m.t('form.essential')))),
          _tile(CheckboxListTile(
              key: const Key('budget-reserve'),
              value: _reserve,
              onChanged: (v) => setState(() => _reserve = v ?? false),
              title: Text(m.t('form.reserve')))),
          AnumHeader(title: m.t('form.rollover')),
          DropdownButtonFormField<String>(
            isExpanded: true,
            key: const Key('budget-rollover'),
            initialValue: _rollover,
            decoration: InputDecoration(labelText: m.t('form.rolloverLeft')),
            items: [
              for (final r in rolloverModes)
                DropdownMenuItem(value: r, child: Text(m.t('roll.mode.$r')))
            ],
            onChanged: (v) => setState(() => _rollover = v ?? _rollover),
          ),
          if (_rollover == 'capped')
            TextField(
                key: const Key('budget-rollover-cap'),
                controller: _cap,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration:
                    InputDecoration(labelText: m.t('form.rolloverCap'))),
          DropdownButtonFormField<String>(
            isExpanded: true,
            key: const Key('budget-overspend'),
            initialValue: _overspend,
            decoration: InputDecoration(labelText: m.t('form.overspend')),
            items: [
              for (final o in overspendModes)
                DropdownMenuItem(value: o, child: Text(m.t('roll.overOpt.$o')))
            ],
            onChanged: (v) => setState(() => _overspend = v ?? _overspend),
          ),
          Text(m.t('form.rolloverHelp'),
              style: TextStyle(color: context.palette.muted)),
        ],
        if (savingKinds.contains(_kind))
          _goals.isEmpty
              ? Text(m.t('form.noGoals'))
              : DropdownButtonFormField<String>(
                  isExpanded: true,
                  key: const Key('budget-goal'),
                  initialValue: _goals.any((g) => g.id == _goal) ? _goal : null,
                  decoration: InputDecoration(labelText: m.t('form.goal')),
                  items: [
                    for (final g in _goals)
                      DropdownMenuItem(
                          value: g.id, child: Text('${g.name} (${g.currency})'))
                  ],
                  onChanged: (v) => setState(() {
                    _goal = v;
                    final g = _goals.where((x) => x.id == v);
                    if (g.isNotEmpty) _currency.text = g.first.currency;
                  }),
                ),
        DropdownButtonFormField<int>(
          isExpanded: true,
          initialValue: _alert,
          decoration: InputDecoration(
              labelText: m.t('form.alert', {'percent': _alert})),
          items: [
            for (final p in const [50, 60, 70, 75, 80, 85, 90, 95, 99])
              DropdownMenuItem(
                  value: p, child: Text(m.t('form.alert', {'percent': p})))
          ],
          onChanged: (v) => setState(() => _alert = v ?? _alert),
        ),
        _tile(SwitchListTile(
            value: _active,
            onChanged: (v) => setState(() => _active = v),
            title: Text(m.t('form.active')))),
        if (_error != null)
          AnumRow(
              key: const Key('budget-error'),
              icon: Icons.error_outline,
              tone: AnumTone.stop,
              title: _error!),
        FilledButton(
            key: const Key('budget-save'),
            onPressed: _busy ? null : _save,
            child: Text(m.t('save'))),
      ]),
    );
  }
}

// -- recurring bills and subscriptions -------------------------------------------------

class RecurringScreen extends StatefulWidget {
  const RecurringScreen({required this.repository, super.key});
  final FinanceBudgetRepository repository;

  @override
  State<RecurringScreen> createState() => _RecurringScreenState();
}

class _RecurringScreenState extends State<RecurringScreen>
    with _Loads<RecurringScreen, RecurringView> {
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  Future<RecurringView> fetch() => widget.repository.recurring();

  Future<void> _keep(FoundRecurring f) async {
    final m = FinanceBudgetStrings.of(context);
    final name = TextEditingController(text: f.name);
    var kind = f.kind;
    var autoLink = f.merchant != null;
    var usage = 'unknown';
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, set) => AlertDialog(
          title: Text(m.t('rec.confirmTitle', {'name': f.name})),
          content: SingleChildScrollView(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              TextField(
                  controller: name,
                  decoration: InputDecoration(labelText: m.t('form.name'))),
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: kind,
                decoration: InputDecoration(labelText: m.t('rec.confirmKind')),
                items: [
                  for (final k in recurringKinds)
                    DropdownMenuItem(value: k, child: Text(m.t('rkind.$k')))
                ],
                onChanged: (v) => set(() => kind = v ?? kind),
              ),
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: usage,
                decoration: InputDecoration(labelText: m.t('rec.usage')),
                items: [
                  for (final u in const [
                    'unknown',
                    'often',
                    'sometimes',
                    'rarely'
                  ])
                    DropdownMenuItem(value: u, child: Text(m.t('usage.$u')))
                ],
                onChanged: (v) => set(() => usage = v ?? usage),
              ),
              CheckboxListTile(
                key: const Key('recurring-auto'),
                value: autoLink,
                onChanged: (v) => set(() => autoLink = v ?? false),
                title: Text(m.t('rec.confirmAuto')),
                subtitle: Text(m.t('rec.confirmAutoHint')),
              ),
            ]),
          ),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(c, false),
                child: Text(m.t('cancel'))),
            FilledButton(
                key: const Key('recurring-keep-save'),
                onPressed: () => Navigator.pop(c, true),
                child: Text(m.t('rec.keep'))),
          ],
        ),
      ),
    );
    final chosen = name.text.trim().isEmpty ? f.name : name.text.trim();
    name.dispose();
    if (ok != true) return;
    await act(
        () => widget.repository.confirmRecurring(f,
            name: chosen,
            kind: kind,
            autoLink: autoLink,
            usage: usage == 'unknown' ? null : usage),
        m.t('rec.kept', {'name': chosen}));
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceBudgetStrings.of(context);
    final page = value;
    return Scaffold(
      appBar: AppBar(title: Text(m.t('rec.title'))),
      body: AnumPage(onRefresh: load, children: [
        if (phase != RecordsPhase.ready || page == null)
          RecordsStateView(
              phase: phase,
              failure: failure,
              deniedHint: m.t('denied'),
              onRetry: load)
        else
          ..._body(context, page),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, RecurringView page) {
    final m = FinanceBudgetStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final kept = page.kept.where((k) => k.active).toList();
    return [
      if (flashView(context) != null) flashView(context)!,
      AnumHeader(title: m.t('rec.found'), subtitle: m.t('rec.foundText')),
      if (page.found.isEmpty) Text(m.t('rec.foundEmpty'), style: muted),
      for (final f in page.found)
        AnumSurface(
          key: Key('found-${f.name}'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumRow(
                icon: Icons.repeat,
                title: f.name,
                subtitle:
                    '${m.t('rkind.${f.kind}')} · ${m.t('cadence.${f.cadence}')}',
                detail: formatAmount(f.lastAmount ?? f.amount, f.currency)),
            Text(m.t('rec.seen', {'n': f.occurrences, 'date': f.nextDueOn}),
                style: muted),
            if (f.priceChange != null)
              Text(
                  m.t('rec.priceChange',
                      {'amount': _signed(f.priceChange!, f.currency)}),
                  style:
                      TextStyle(color: anumToneColor(context, AnumTone.warn))),
            Wrap(spacing: AnumSpacing.sm, children: [
              FilledButton.tonal(
                  key: Key('found-keep-${f.name}'),
                  onPressed: () => _keep(f),
                  child: Text(m.t('rec.keep'))),
              OutlinedButton(
                  key: Key('found-reject-${f.name}'),
                  onPressed: () => act(
                      () => widget.repository.rejectRecurring(f.key),
                      m.t('rec.rejected', {'name': f.name})),
                  child: Text(m.t('rec.reject'))),
            ]),
          ]),
        ),
      if (page.dismissed > 0)
        Text(m.t('rec.dismissed', {'n': page.dismissed}), style: muted),
      AnumHeader(
        title: m.t('rec.keptTitle'),
        trailing: kept.any((k) => k.autoLink)
            ? TextButton(
                key: const Key('recurring-link-now'),
                onPressed: () async {
                  try {
                    final done = await widget.repository.linkNow();
                    if (!mounted) return;
                    setState(() {
                      flash = m.t('rec.linked',
                          {'linked': done.linked, 'left': done.left});
                      flashError = false;
                    });
                    await load();
                  } on Object catch (error) {
                    if (!mounted) return;
                    setState(() {
                      flash = RecordsFailure.from(error).message;
                      flashError = true;
                    });
                  }
                },
                child: Text(m.t('rec.linkNow')))
            : null,
      ),
      if (kept.isEmpty) Text(m.t('rec.keptEmpty'), style: muted),
      for (final k in kept) _kept(context, k),
    ];
  }

  Widget _kept(BuildContext context, KeptBill k) {
    final m = FinanceBudgetStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return AnumSurface(
      key: Key('kept-${k.id}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumRow(
            icon: Icons.receipt_long_outlined,
            tone: k.stillBillingRarelyUsed ? AnumTone.stop : null,
            title: k.name,
            subtitle: [
              if (k.recurringKind != null) m.t('rkind.${k.recurringKind}'),
              m.t('cadence.${k.cadence}'),
              k.autoLink ? m.t('rec.autoLink') : m.t('rec.manual'),
            ].join(' · '),
            detail: formatAmount(k.amount, k.currency)),
        Text(
            k.lastPaidOn != null && k.lastAmount != null
                ? m.t('rec.lastPaid', {
                    'date': k.lastPaidOn!,
                    'amount': formatAmount(k.lastAmount!, k.currency)
                  })
                : m.t('rec.notSeen'),
            style: muted),
        if (k.priceChange != null)
          Text(
              m.t('rec.billPrice',
                  {'amount': _signed(k.priceChange!, k.currency)}),
              style: TextStyle(color: anumToneColor(context, AnumTone.warn))),
        if (k.stillBillingRarelyUsed)
          Text(m.t('rec.rarely'),
              style: TextStyle(color: anumToneColor(context, AnumTone.stop))),
        DropdownButtonFormField<String>(
          isExpanded: true,
          key: Key('kept-usage-${k.id}'),
          initialValue: k.usage ?? 'unknown',
          decoration: InputDecoration(labelText: m.t('rec.usage')),
          items: [
            for (final u in const ['unknown', 'often', 'sometimes', 'rarely'])
              DropdownMenuItem(value: u, child: Text(m.t('usage.$u')))
          ],
          onChanged: (v) => act(
              () => widget.repository.recurringSettings(k.id, usage: v),
              m.t('rec.updated', {'name': k.name})),
        ),
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: TextButton(
              key: Key('kept-auto-${k.id}'),
              onPressed: k.autoLink || k.canAutoLink
                  ? () => act(
                      () => widget.repository
                          .recurringSettings(k.id, autoLink: !k.autoLink),
                      m.t('rec.updated', {'name': k.name}))
                  : null,
              child: Text(k.autoLink ? m.t('rec.autoOff') : m.t('rec.autoOn'))),
        ),
      ]),
    );
  }
}

// -- spending categories ----------------------------------------------------------------

class SpendingCategoriesScreen extends StatefulWidget {
  const SpendingCategoriesScreen({required this.repository, super.key});
  final FinanceBudgetRepository repository;

  @override
  State<SpendingCategoriesScreen> createState() =>
      _SpendingCategoriesScreenState();
}

typedef _CategoryPage = ({CategoryTree tree, List<CategoryMapping> learned});

class _SpendingCategoriesScreenState extends State<SpendingCategoriesScreen>
    with _Loads<SpendingCategoriesScreen, _CategoryPage> {
  String _query = '';

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  Future<_CategoryPage> fetch() async {
    final now = DateTime.now();
    final month = '${now.year}-${now.month.toString().padLeft(2, '0')}';
    final tree = await widget.repository.categories(month: month);
    final learned = await widget.repository.mappings();
    return (tree: tree, learned: learned);
  }

  Future<void> _add(CategoryNode category) async {
    final m = FinanceBudgetStrings.of(context);
    final name = TextEditingController();
    final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
              title: Text(
                  m.t('cat.addTo', {'category': category.label(m.arabic)})),
              content: TextField(
                  key: const Key('subcategory-name'),
                  controller: name,
                  maxLength: 60,
                  autofocus: true,
                  decoration: InputDecoration(labelText: m.t('form.name'))),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(c, false),
                    child: Text(m.t('cancel'))),
                FilledButton(
                    key: const Key('subcategory-save'),
                    onPressed: () => Navigator.pop(c, true),
                    child: Text(m.t('cat.add'))),
              ],
            ));
    final text = name.text.trim();
    name.dispose();
    if (ok != true || text.isEmpty) return;
    await act(() => widget.repository.addSubcategory(category.id, text),
        m.t('cat.added', {'name': text}));
  }

  bool _matches(String text) =>
      text.toLowerCase().contains(_query.trim().toLowerCase());

  @override
  Widget build(BuildContext context) {
    final m = FinanceBudgetStrings.of(context);
    final page = value;
    return Scaffold(
      appBar: AppBar(title: Text(m.t('cat.title'))),
      body: AnumPage(onRefresh: load, children: [
        if (phase != RecordsPhase.ready || page == null)
          RecordsStateView(
              phase: phase,
              failure: failure,
              deniedHint: m.t('denied'),
              onRetry: load)
        else
          ..._body(context, page),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, _CategoryPage page) {
    final m = FinanceBudgetStrings.of(context);
    final arabic = m.arabic;
    final muted = TextStyle(color: context.palette.muted);
    final groups = page.tree.groups.where((g) {
      if (_query.trim().isEmpty) return true;
      return _matches(g.name) ||
          _matches(g.nameAr) ||
          g.categories.any((c) =>
              _matches(c.name) ||
              _matches(c.nameAr) ||
              c.subcategories
                  .any((s) => _matches(s.name) || _matches(s.nameAr ?? '')));
    }).toList();
    return [
      if (flashView(context) != null) flashView(context)!,
      Text(m.t('cat.intro'), style: muted),
      TextField(
          key: const Key('categories-search'),
          decoration: InputDecoration(
              prefixIcon: const Icon(Icons.search),
              labelText: m.t('cat.search')),
          onChanged: (v) => setState(() => _query = v)),
      if (groups.isEmpty) Text(m.t('cat.noMatch'), style: muted),
      for (final g in groups)
        AnumSurface(
          key: Key('group-${g.id}'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                title: g.label(arabic),
                subtitle: (page.tree.groupTotals[g.id] ?? const {}).isEmpty
                    ? null
                    : m.t('cat.thisMonth', {
                        'amounts': page.tree.groupTotals[g.id]!.entries
                            .map((e) => formatAmount(e.value, e.key))
                            .join(' · ')
                      })),
            for (final c in g.categories) ...[
              AnumRow(
                title: c.label(arabic),
                subtitle: c.essential ? m.t('cat.essential') : null,
                trailing: IconButton(
                    key: Key('subcategory-add-${c.id}'),
                    tooltip: m.t('cat.addTo', {'category': c.label(arabic)}),
                    icon: const Icon(Icons.add),
                    onPressed: () => _add(c)),
              ),
              if (c.subcategories.isNotEmpty)
                Wrap(
                    spacing: AnumSpacing.sm,
                    runSpacing: AnumSpacing.sm,
                    children: [
                      for (final s in c.subcategories.where((s) => !s.archived))
                        Chip(
                            label: Text(s.label(arabic)),
                            avatar: s.builtIn
                                ? null
                                : const Icon(Icons.person_outline, size: 16)),
                    ]),
            ],
          ]),
        ),
      AnumHeader(title: m.t('cat.learned'), subtitle: m.t('cat.learnedText')),
      if (page.learned.isEmpty)
        AnumEmpty(
            icon: Icons.storefront_outlined,
            title: m.t('cat.learnedEmpty'),
            message: m.t('cat.learnedEmptyText'))
      else
        for (final l in page.learned)
          AnumSurface(
            key: Key('learned-${l.merchant}'),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumRow(
                      icon: Icons.storefront_outlined,
                      tone: l.enabled ? AnumTone.ok : AnumTone.neutral,
                      title: l.merchant,
                      subtitle: m.t('cat.times', {
                        'category': FinanceStrings.of(context)
                            .t('category.${l.category}'),
                        'n': l.confirmations
                      }),
                      detail: l.enabled ? m.t('cat.on') : m.t('cat.off')),
                  Wrap(
                      alignment: WrapAlignment.end,
                      spacing: AnumSpacing.sm,
                      children: [
                        TextButton(
                            key: Key('learned-switch-${l.merchant}'),
                            onPressed: () => act(
                                () => widget.repository
                                    .switchMapping(l, !l.enabled),
                                l.enabled ? m.t('cat.off') : m.t('cat.on')),
                            child: Text(l.enabled
                                ? m.t('cat.switchOff')
                                : m.t('cat.switchOn'))),
                        TextButton(
                            key: Key('learned-forget-${l.merchant}'),
                            onPressed: () async {
                              if (await confirm(context, m.t('cat.forget'),
                                  m.t('cat.forgotten'), m.t('cat.forget'))) {
                                await act(
                                    () => widget.repository.forgetMapping(l),
                                    m.t('cat.forgotten'));
                              }
                            },
                            child: Text(m.t('cat.forget'))),
                      ]),
                ]),
          ),
    ];
  }
}
