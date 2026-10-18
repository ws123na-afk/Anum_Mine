import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'finance_controller.dart';
import 'finance_intel_view.dart';
import 'finance_models.dart';
import 'finance_plan_models.dart';
import 'finance_strings.dart';
import 'finance_trading_models.dart';

/// Finance on the phone (docs/finance.md): the signed-in person's own month,
/// transactions, accounts, bills, debts and goals. Read only: nothing here
/// pays, transfers or trades. Statement import is on the web.
class FinanceScreen extends StatefulWidget {
  const FinanceScreen({required this.controller, super.key});
  final FinanceController controller;

  @override
  State<FinanceScreen> createState() => _FinanceScreenState();
}

class _FinanceScreenState extends State<FinanceScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      widget.controller.load();
    });
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _say(String text) {
    if (!mounted) return;
    ScaffoldMessenger.maybeOf(context)
        ?.showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceStrings.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return Scaffold(
          // The AppBar's back button returns to where Finance was opened from.
          appBar: AppBar(title: Text(s.t('title'))),
          body: AnumPage(
            onRefresh: c.load,
            children: [
              AnumHeader(eyebrow: s.t('subtitle'), title: s.t('title')),
              _Sections(controller: c, strings: s),
              ..._body(context, c, s),
              Text(s.t('readOnly'),
                  style: TextStyle(color: context.palette.muted)),
              Text(s.t('importOnWeb'),
                  style: TextStyle(color: context.palette.muted)),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(
      BuildContext context, FinanceController c, FinanceStrings s) {
    if (c.summaryState == FinanceLoad.denied) {
      return [
        AnumFeedback(kind: AnumFeedbackKind.permission, message: s.t('denied'))
      ];
    }
    return switch (c.section) {
      FinanceSection.overview => _overview(context, c, s),
      FinanceSection.transactions => _transactions(context, c, s),
      FinanceSection.accounts => _accounts(context, c, s),
      FinanceSection.bills => _items(context, c, s, 'bills'),
      FinanceSection.debts => _items(context, c, s, 'debts'),
      FinanceSection.goals => _items(context, c, s, 'goals'),
      FinanceSection.safe => _safe(context, c, s),
      FinanceSection.guardian => _guardian(context, c, s),
      FinanceSection.connections => _connections(context, c, s),
      FinanceSection.trading => _trading(context, c, s),
      FinanceSection.intel => [
          if (c.intel != null) FinanceIntelView(controller: c.intel!)
        ],
      FinanceSection.execution => _execution(context, c, s),
    };
  }

  /// Loading, error or permission denied for a section, or null when ready.
  List<Widget>? _state(FinanceLoad state, bool hasData, String? error,
      Future<void> Function() retry, FinanceStrings s) {
    if ((state == FinanceLoad.idle || state == FinanceLoad.loading) &&
        !hasData) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (state == FinanceLoad.denied) {
      return [
        AnumFeedback(kind: AnumFeedbackKind.permission, message: s.t('denied'))
      ];
    }
    if (state == FinanceLoad.error) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('listFailed', {'message': error ?? ''}),
            onRetry: retry)
      ];
    }
    return null;
  }

  // -- paper trading and the execution gateway (stages 6 and 7) -------------

  String _intentLine(TradeIntent i, FinanceStrings s) => s.t(
      i.side == 'sell' ? 'trading.sellLine' : 'trading.buyLine',
      {'quantity': trimDecimal(i.quantity), 'symbol': i.symbol ?? ''});

  List<Widget> _trading(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final waiting = _state(c.tradingState, c.tradingState == FinanceLoad.ready,
        c.tradingError, c.loadTrading, s);
    if (waiting != null) return waiting;
    return [
      AnumSurface(
          child: Text('${s.t('trading.simulated')} ${s.t('trading.note')}')),
      for (final intent in c.pendingIntents)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: _intentLine(intent, s)),
            if (intent.requestedBy == 'agent') Text(s.t('trading.byAgent')),
            if (intent.portfolioName != null)
              Text(intent.portfolioName!,
                  style: TextStyle(color: context.palette.muted)),
            if (intent.notional != null)
              Text(s.t('trading.estimate', {
                'value': formatAmount(intent.notional!, intent.currency ?? ''),
                'fee': formatAmount(intent.fee ?? '0', intent.currency ?? ''),
                'slippage':
                    formatAmount(intent.slippage ?? '0', intent.currency ?? ''),
              })),
            Text(s.t('trading.verdict.${intent.outcome ?? 'allow'}')),
            Text(
                '${s.t('guardian.code')} ${intent.payloadHash.substring(0, 12)}',
                textDirection: TextDirection.ltr,
                style: TextStyle(color: context.palette.muted)),
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton(
                      onPressed: () => _approveIntent(context, c, s, intent),
                      child: Text(s.t('trading.approve'))),
                  OutlinedButton(
                      onPressed: () async => _say(
                          await c.rejectIntent(intent) ??
                              s.t('trading.rejected')),
                      child: Text(s.t('guardian.reject'))),
                ]),
          ]),
        ),
      if (c.portfolios.isEmpty)
        AnumEmpty(
            title: s.t('trading.empty'),
            message: s.t('trading.emptyText'),
            icon: Icons.science_outlined)
      else
        for (final p in c.portfolios)
          AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                      eyebrow: s.t('trading.mode.${p.mode}'), title: p.name),
                  if (p.mode == 'paper')
                    AnumRow(
                        title: s.t('trading.cash'),
                        detail: formatAmounts(p.cash),
                        icon: Icons.account_balance_wallet_outlined),
                  AnumRow(
                      title: s.t('trading.realised'),
                      detail: formatAmounts(p.realised),
                      icon: Icons.check_circle_outline),
                  AnumRow(
                      title: s.t('trading.unrealised'),
                      detail: formatAmounts(p.unrealised),
                      icon: Icons.show_chart),
                  Text(
                      s.t('trading.counts',
                          {'positions': p.positions, 'waiting': p.waiting}),
                      style: TextStyle(color: context.palette.muted)),
                  Text(
                      s.t('trading.assumptions', {
                        'fee': _bps(p.feeBps),
                        'slippage': _bps(p.slippageBps)
                      }),
                      style: TextStyle(color: context.palette.muted)),
                ]),
          ),
      Text(s.t('trading.onWeb'),
          style: TextStyle(color: context.palette.muted)),
    ];
  }

  String _bps(String value) =>
      '${((double.tryParse(value) ?? 0) / 100).toStringAsFixed(2)}%';

  Future<void> _approveIntent(BuildContext context, FinanceController c,
      FinanceStrings s, TradeIntent intent) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('trading.reviewTitle')),
        scrollable: true,
        content: Text('${_intentLine(intent, s)}\n\n${s.t('trading.note')}\n\n'
            '${s.t('guardian.code')} ${intent.payloadHash.substring(0, 12)}'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(s.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(s.t('trading.approve'))),
        ],
      ),
    );
    if (ok != true) return;
    _say(await c.approveIntent(intent) ?? s.t('trading.approved'));
  }

  // -- execution, kill switch, limited and wider live (stages 7 to 9) -------

  List<Widget> _execution(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final waiting = _state(c.executionLoad, c.execution != null,
        c.executionError, c.loadExecution, s);
    if (waiting != null) return waiting;
    final e = c.execution!;
    final live = c.live;
    return [
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('execution.kill')),
          AnumRow(
            title: s.t('execution.killPerson'),
            detail: e.personSwitch
                ? s.t('execution.killOn')
                : s.t('execution.killOff'),
            icon: Icons.power_settings_new,
            tone: e.personSwitch ? AnumTone.warn : null,
            trailing: Semantics(
              label: s.t('execution.killPerson'),
              child: Switch(
                value: e.personSwitch,
                onChanged: (value) async => _say(await c.setKillSwitch(value) ??
                    s.t(value ? 'execution.stopped' : 'execution.resumed')),
              ),
            ),
          ),
          AnumRow(
              title: s.t('execution.killGlobal'),
              detail: e.globalSwitch
                  ? s.t('execution.killOn')
                  : s.t('execution.killOff'),
              icon: Icons.shield_outlined,
              tone: e.globalSwitch ? AnumTone.warn : null),
        ]),
      ),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('live.title')),
          Text(s.t('live.off'), style: TextStyle(color: context.palette.warn)),
          for (final check in live?.preconditions ?? const <LiveCheck>[])
            AnumRow(
                title: s.t('live.pre.${check.code}'),
                icon: check.met ? Icons.check : Icons.close,
                tone: check.met ? AnumTone.ok : AnumTone.warn),
        ]),
      ),
      if (live != null)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                title: s.t('wider.title'),
                eyebrow: s
                    .t('wider.tier', {'tier': live.tier, 'max': live.maxTier})),
            Text(s.t('wider.off'),
                style: TextStyle(color: context.palette.warn)),
            if (live.frozen) ...[
              Text(s.t('wider.frozen', {
                'reason': s.t('wider.incident.${live.frozenReason ?? ''}')
              })),
              OutlinedButton(
                  onPressed: () async =>
                      _say(await c.reviewIncidents() ?? s.t('wider.reviewed')),
                  child: Text(s.t('wider.review'))),
            ],
            for (final check in live.wider)
              AnumRow(
                  title: s.t('wider.criterion.${check.code}'),
                  detail: '${check.value ?? ''} / ${check.needed ?? ''}',
                  icon: check.met ? Icons.check : Icons.close,
                  tone: check.met ? AnumTone.ok : AnumTone.warn),
          ]),
        ),
      Text(s.t('execution.onWeb'),
          style: TextStyle(color: context.palette.muted)),
    ];
  }

  // -- safe to spend (stage 3) ---------------------------------------------

  String _safeNote(SafeLine line, FinanceStrings s) => switch (line.note) {
        'bill_due' =>
          s.t('safe.note.bill_due', {'date': line.params['date'] ?? ''}),
        'minimum_due' ||
        'minimum_passed' =>
          s.t('safe.note.${line.note}', {'day': line.params['day'] ?? ''}),
        'goal_derived' => s.t('safe.note.goal_derived', {
            'left': formatAmount(line.params['left'] ?? '0', line.currency),
            'n': line.params['months'] ?? '1'
          }),
        _ => s.t('safe.note.${line.note}'),
      };

  List<Widget> _safe(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final waiting =
        _state(c.safeState, c.safe != null, c.safeError, c.loadSafe, s);
    if (waiting != null) return waiting;
    final d = c.safe!;
    if (d.currencies.isEmpty) {
      return [
        AnumEmpty(
            title: s.t('safe.empty'),
            message: s.t('safe.emptyText'),
            icon: Icons.account_balance_outlined,
            action: s.t('addAccount'),
            onAction: () => _addAccount(context, c, s)),
      ];
    }
    return [
      Text(s.t('safe.intro', {'end': d.monthEnd, 'n': d.daysLeft}),
          style: TextStyle(color: context.palette.muted)),
      for (final row in d.currencies)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumMetric(
                label: row.short
                    ? s.t('safe.shortBy')
                    : s.t('safe.left', {'currency': row.currency}),
                value: formatAmount(
                    row.safeToSpend.replaceFirst('-', ''), row.currency),
                icon: row.short
                    ? Icons.warning_amber_outlined
                    : Icons.speed_outlined,
                caption: row.short
                    ? s.t('safe.shortHint')
                    : s.t('safe.perDay', {
                        'amount': formatAmount(row.perDay, row.currency),
                        'n': d.daysLeft
                      })),
            AnumHeader(title: s.t('safe.howBuilt')),
            for (final line in row.lines)
              AnumRow(
                  title:
                      line.kind == 'buffer' ? s.t('safe.buffer') : line.label,
                  subtitle:
                      '${s.t('safe.group.${line.kind}')} · ${_safeNote(line, s)}',
                  detail: formatAmount(line.amount, line.currency),
                  tone: line.amount.startsWith('-') ? null : AnumTone.ok),
          ]),
        ),
      if (d.combined != null)
        AnumMetric(
            label: s.t('safe.combined', {'currency': d.combined!.currency}),
            value: formatAmount(d.combined!.amount, d.combined!.currency),
            icon: Icons.currency_exchange),
      if (d.excluded.isNotEmpty)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('safe.notCounted', {'n': d.excluded.length})),
            for (final line in d.excluded)
              AnumRow(title: line.label, subtitle: _safeNote(line, s)),
          ]),
        ),
      Text(s.t('safe.disclaimer'),
          style: TextStyle(color: context.palette.muted)),
    ];
  }

  // -- guardian (stage 4) --------------------------------------------------

  String _rule(RuleLine line, FinanceStrings s) => s.t(line.$1, line.$2);

  List<Widget> _guardian(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final waiting = _state(c.guardianState, c.guardian != null, c.guardianError,
        c.loadGuardian, s);
    if (waiting != null) return waiting;
    final g = c.guardian!;
    final lines = describeRules(g.rules, category: (x) => s.t('category.$x'));
    return [
      Text(s.t('guardian.intro'),
          style: TextStyle(color: context.palette.muted)),
      for (final change in g.pending)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                title: change.proposedBy == 'agent'
                    ? s.t('guardian.byAgent')
                    : s.t('guardian.byYou')),
            if (change.reason != null) Text(change.reason!),
            for (final line in describeRules(change.rules,
                category: (x) => s.t('category.$x')))
              AnumRow(title: _rule(line, s), icon: Icons.rule),
            if (change.loosens.isNotEmpty)
              Text(s.t('guardian.loosens'),
                  style: TextStyle(color: context.palette.warn)),
            Text(
                '${s.t('guardian.code')} ${change.payloadHash.substring(0, 12)}',
                textDirection: TextDirection.ltr,
                style: TextStyle(color: context.palette.muted)),
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton(
                      onPressed: () => _confirmChange(context, c, s, change),
                      child: Text(s.t('guardian.confirm'))),
                  OutlinedButton(
                      onPressed: () async => _say(
                          await c.rejectChange(change) ??
                              s.t('guardian.rejected')),
                      child: Text(s.t('guardian.reject'))),
                ]),
          ]),
        ),
      if (lines.isEmpty)
        AnumEmpty(
            title: s.t('guardian.empty'),
            message: s.t('guardian.emptyText'),
            icon: Icons.shield_outlined)
      else
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('guardian.version', {'n': g.version})),
            for (final line in lines)
              AnumRow(
                  title: _rule(line, s), icon: Icons.check, tone: AnumTone.ok),
          ]),
        ),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('guardian.alerts')),
          if (c.alerts.isEmpty)
            Text(s.t('guardian.alertsNone'))
          else
            for (final alert in c.alerts)
              AnumRow(
                  title: s.t('guardian.alert.${alert.code}', {
                    'category':
                        s.t('category.${alert.params['category'] ?? 'other'}'),
                    'merchant': alert.params['merchant'] ?? '',
                    'n': alert.params['count'] ?? '',
                    'spent': formatAmount(alert.params['spent'] ?? '0',
                        alert.params['currency'] ?? ''),
                    'limit': formatAmount(alert.params['limit'] ?? '0',
                        alert.params['currency'] ?? ''),
                  }),
                  icon: Icons.warning_amber_outlined,
                  tone: alert.severity == 'over' ? AnumTone.warn : null),
        ]),
      ),
      Text(s.t('guardian.editOnWeb'),
          style: TextStyle(color: context.palette.muted)),
    ];
  }

  Future<void> _confirmChange(BuildContext context, FinanceController c,
      FinanceStrings s, GuardianChange change) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('guardian.reviewTitle')),
        scrollable: true,
        content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(s.t('guardian.reviewText')),
              if (change.loosens.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: AnumSpacing.xs),
                  child: Text(s.t('guardian.loosens'),
                      style: TextStyle(color: context.palette.warn)),
                ),
            ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(s.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(s.t('guardian.confirm'))),
        ],
      ),
    );
    if (ok != true) return;
    _say(await c.confirmChange(change) ?? s.t('guardian.confirmed'));
  }

  // -- connections (stage 5) -----------------------------------------------

  List<Widget> _connections(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final waiting = _state(c.connectionsState, c.connections.isNotEmpty,
        c.connectionsError, c.loadConnections, s);
    if (waiting != null) return waiting;
    return [
      Text(s.t('connections.intro'),
          style: TextStyle(color: context.palette.muted)),
      if (c.connections.isEmpty)
        AnumEmpty(
            title: s.t('connections.none'),
            message: s.t('connections.connectOnWeb'),
            icon: Icons.power_outlined)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final conn in c.connections)
              AnumRow(
                title: conn.label,
                icon: Icons.power_outlined,
                tone: conn.status == 'error' ? AnumTone.warn : null,
                subtitle: [
                  s.t('connections.status.${conn.status}'),
                  if (conn.lastSuccessAt != null)
                    s.t('connections.lastSync',
                        {'date': conn.lastSuccessAt!.substring(0, 10)}),
                  if (conn.lastError != null) conn.lastError!,
                ].join(' · '),
                trailing: conn.status == 'disconnected'
                    ? null
                    : IconButton(
                        tooltip: '${s.t('connections.sync')} ${conn.label}',
                        icon: const Icon(Icons.sync),
                        onPressed: () async => _say(
                            await c.syncConnection(conn.id) ??
                                s.t('connections.synced')),
                      ),
              ),
          ]),
        ),
    ];
  }

  // -- overview -----------------------------------------------------------

  List<Widget> _overview(
      BuildContext context, FinanceController c, FinanceStrings s) {
    switch (c.summaryState) {
      case FinanceLoad.idle:
      case FinanceLoad.loading:
        if (c.summary == null) {
          return [
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: s.t('loading'))
          ];
        }
      case FinanceLoad.denied:
        return [];
      case FinanceLoad.error:
        return [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('summaryFailed', {'message': c.summaryError ?? ''}),
              onRetry: c.loadSummary)
        ];
      case FinanceLoad.ready:
        break;
    }
    final d = c.summary!;
    final balance = d.pick(d.balances), spent = d.pick(d.spend);
    final income = d.pick(d.income);
    final spend = d.spendByCategory
        .where((row) => row.currency == spent.currency)
        .toList();
    final top = spend.isEmpty
        ? 0.0
        : spend
            .map((row) => double.tryParse(row.amount) ?? 0)
            .reduce((a, b) => a > b ? a : b);
    return [
      if (d.accounts.isEmpty)
        AnumEmpty(
            title: s.t('welcome'),
            message: s.t('welcomeText'),
            icon: Icons.account_balance_wallet_outlined,
            action: s.t('addAccount'),
            onAction: () => _addAccount(context, c, s)),
      AnumMetricGrid(children: [
        AnumMetric(
            label: s.t('balance'),
            value: formatAmount(balance.amount, balance.currency),
            icon: Icons.account_balance_outlined,
            caption: d.balances.length > 1 ? formatAmounts(d.balances) : null),
        AnumMetric(
            label: s.t('spent'),
            value: formatAmount(spent.amount, spent.currency),
            icon: Icons.north_east,
            caption: d.month),
        AnumMetric(
            label: s.t('income'),
            value: formatAmount(income.amount, income.currency),
            icon: Icons.south_west,
            caption: formatAmounts(d.net)),
        AnumMetric(
            label: s.t('bills30'),
            value: '${d.upcomingBills.length}',
            icon: Icons.event_outlined,
            caption:
                d.billsDue.isEmpty ? s.t('none') : formatAmounts(d.billsDue)),
        AnumMetric(
            label: s.t('debts'),
            value: d.debtCount == 0 ? '-' : formatAmounts(d.debts),
            icon: Icons.credit_card_outlined,
            caption: d.debtCount == 0
                ? s.t('none')
                : formatAmounts(d.minimumPayments)),
      ]),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('byCategory')),
          if (spend.isEmpty)
            Text(s.t('noSpending'))
          else
            for (final row in spend)
              _SpendBar(
                  label: s.t('category.${row.category}'),
                  amount: formatAmount(row.amount, row.currency),
                  share:
                      top <= 0 ? 0 : (double.tryParse(row.amount) ?? 0) / top),
        ]),
      ),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('upcoming')),
          if (d.upcomingBills.isEmpty)
            Text(s.t('noBillsDue'))
          else
            for (final bill in d.upcomingBills.take(6))
              AnumRow(
                  title: bill.name,
                  icon: Icons.event_outlined,
                  tone: bill.daysUntil <= 3 ? AnumTone.warn : null,
                  subtitle:
                      '${bill.dueOn} · ${bill.daysUntil <= 0 ? s.t('dueToday') : s.t('dueIn', {
                              'n': bill.daysUntil
                            })}',
                  detail: formatAmount(bill.amount, bill.currency)),
        ]),
      ),
      if (d.accounts.isNotEmpty)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('accounts')),
            for (final a in d.accounts.where((a) => !a.archived))
              AnumRow(
                  title: a.name,
                  icon: Icons.account_balance_outlined,
                  subtitle: [
                    s.t('kind.${a.kind}'),
                    if (a.last4 != null) s.t('endingIn', {'last4': a.last4!}),
                  ].join(' · '),
                  detail: formatAmount(a.balance, a.currency)),
          ]),
        ),
    ];
  }

  // -- transactions --------------------------------------------------------

  List<Widget> _transactions(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final search = TextField(
      controller: _search,
      textInputAction: TextInputAction.search,
      decoration: InputDecoration(
          labelText: s.t('search'), prefixIcon: const Icon(Icons.search)),
      onSubmitted: c.search,
    );
    final add = Align(
      alignment: AlignmentDirectional.centerStart,
      child: FilledButton.icon(
          onPressed:
              c.accounts.isEmpty ? null : () => _addTransaction(context, c, s),
          icon: const Icon(Icons.add),
          label: Text(s.t('addTransaction'))),
    );
    if (c.transactionsState == FinanceLoad.loading && c.transactions.isEmpty) {
      return [
        search,
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (c.transactionsState == FinanceLoad.error ||
        c.transactionsState == FinanceLoad.denied) {
      return [
        search,
        AnumFeedback(
            kind: c.transactionsState == FinanceLoad.denied
                ? AnumFeedbackKind.permission
                : AnumFeedbackKind.error,
            message: c.transactionsState == FinanceLoad.denied
                ? s.t('denied')
                : s.t('listFailed', {'message': c.transactionsError ?? ''}),
            onRetry: c.loadTransactions),
      ];
    }
    if (c.transactions.isEmpty) {
      return [
        search,
        add,
        c.query.isEmpty
            ? AnumEmpty(
                title: s.t('noTransactions'),
                message: s.t('noTransactionsText'),
                icon: Icons.receipt_long_outlined)
            : AnumEmpty(title: s.t('noMatch'), message: c.query),
      ];
    }
    return [
      search,
      add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final t in c.transactions)
            AnumRow(
              title: t.description,
              subtitle: '${t.postedOn} · ${s.t('category.${t.category}')}',
              detail: formatAmount(t.amount, t.currency),
              icon: t.moneyIn ? Icons.south_west : Icons.north_east,
              tone: t.moneyIn ? AnumTone.ok : null,
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${t.description}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, t.description, () => c.deleteTransaction(t.id)),
              ),
            ),
        ]),
      ),
      if (c.hasMoreTransactions)
        OutlinedButton(
          onPressed: c.loadingMore ? null : c.loadMoreTransactions,
          child: Text(s.t('more',
              {'shown': c.transactions.length, 'total': c.transactionsTotal})),
        ),
    ];
  }

  // -- accounts ------------------------------------------------------------

  List<Widget> _accounts(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final add = Align(
      alignment: AlignmentDirectional.centerStart,
      child: FilledButton.icon(
          onPressed: () => _addAccount(context, c, s),
          icon: const Icon(Icons.add),
          label: Text(s.t('addAccount'))),
    );
    if (c.accountsState == FinanceLoad.loading && c.accounts.isEmpty) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (c.accountsState == FinanceLoad.error) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('listFailed', {'message': ''}),
            onRetry: c.loadAccounts)
      ];
    }
    if (c.accounts.isEmpty) {
      return [
        AnumEmpty(
            title: s.t('noAccounts'),
            message: s.t('noAccountsText'),
            icon: Icons.account_balance_outlined,
            action: s.t('addAccount'),
            onAction: () => _addAccount(context, c, s)),
      ];
    }
    return [
      add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final a in c.accounts)
            AnumRow(
              title: a.name,
              icon: Icons.account_balance_outlined,
              subtitle: [
                s.t('kind.${a.kind}'),
                a.currency,
                if (a.last4 != null) s.t('endingIn', {'last4': a.last4!}),
              ].join(' · '),
              detail: formatAmount(a.balance, a.currency),
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${a.name}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, a.name, () => c.deleteAccount(a.id)),
              ),
            ),
        ]),
      ),
    ];
  }

  // -- bills, debts, goals -------------------------------------------------

  List<Widget> _items(BuildContext context, FinanceController c,
      FinanceStrings s, String kind) {
    final state = c.itemStates[kind] ?? FinanceLoad.idle;
    final list = c.items[kind] ?? const <FinanceItem>[];
    final add = kind == 'bills'
        ? Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
                onPressed: () => _addBill(context, c, s),
                icon: const Icon(Icons.add),
                label: Text(s.t('addBill'))),
          )
        : null;
    if ((state == FinanceLoad.loading || state == FinanceLoad.idle) &&
        list.isEmpty) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (state == FinanceLoad.error || state == FinanceLoad.denied) {
      return [
        AnumFeedback(
            kind: state == FinanceLoad.denied
                ? AnumFeedbackKind.permission
                : AnumFeedbackKind.error,
            message: state == FinanceLoad.denied
                ? s.t('denied')
                : s.t('listFailed', {'message': c.itemsError ?? ''}),
            onRetry: () => c.loadItems(kind)),
      ];
    }
    final empty = switch (kind) {
      'bills' => (s.t('noBills'), s.t('noBillsText')),
      'debts' => (s.t('noDebts'), s.t('noDebtsText')),
      _ => (s.t('noGoals'), s.t('noGoalsText')),
    };
    if (list.isEmpty) {
      return [
        if (add != null) add,
        AnumEmpty(
            title: empty.$1,
            message: empty.$2,
            icon: Icons.event_note_outlined,
            action: kind == 'bills' ? s.t('addBill') : null,
            onAction: kind == 'bills' ? () => _addBill(context, c, s) : null),
      ];
    }
    return [
      if (add != null) add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final item in list)
            AnumRow(
              title: item.name,
              subtitle: kind == 'goals'
                  ? formatAmount(item.detail, item.currency)
                  : item.detail,
              detail: formatAmount(item.amount, item.currency),
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${item.name}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, item.name, () => c.deleteItem(kind, item.id)),
              ),
            ),
        ]),
      ),
    ];
  }

  // -- dialogs -------------------------------------------------------------

  Future<void> _confirmDelete(BuildContext context, FinanceStrings s,
      String name, Future<String?> Function() action) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('deleteTitle', {'name': name})),
        content: Text(s.t('deleteText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(s.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(s.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    final error = await action();
    _say(error ?? s.t('deleted'));
  }

  /// One small form in a dialog: the fields, a check, then the save call.
  Future<void> _form(
    BuildContext context,
    FinanceStrings s,
    String title,
    List<(String key, String label, TextInputType type)> fields,
    String? Function(Map<String, String> values) check,
    Future<String?> Function(Map<String, String> values) save,
  ) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => _FormDialog(
          strings: s, title: title, fields: fields, check: check, save: save),
    );
    if (saved == true) _say(s.t('saved'));
  }

  Future<void> _addAccount(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addAccount'),
        [
          ('name', s.t('name'), TextInputType.text),
          ('last4', s.t('last4'), TextInputType.number),
        ],
        (v) => v['name']!.isEmpty
            ? s.t('badText')
            : (v['last4']!.isNotEmpty &&
                    !RegExp(r'^\d{4}$').hasMatch(v['last4']!))
                ? s.t('badLast4')
                : null,
        (v) => c.addAccount(name: v['name']!, kind: 'bank', last4: v['last4']),
      );

  Future<void> _addTransaction(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addTransaction'),
        [
          ('description', s.t('description'), TextInputType.text),
          (
            'amount',
            s.t('amount'),
            const TextInputType.numberWithOptions(signed: true, decimal: true)
          ),
          ('date', s.t('date'), TextInputType.datetime),
        ],
        (v) => v['description']!.isEmpty
            ? s.t('badText')
            : parseAmount(v['amount']!) == null
                ? s.t('badAmount')
                : !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(v['date']!)
                    ? s.t('badDate')
                    : null,
        (v) => c.addTransaction(
            accountId: c.accounts
                .firstWhere((a) => !a.archived, orElse: () => c.accounts.first)
                .id,
            postedOn: v['date']!,
            amount: parseAmount(v['amount']!)!,
            description: v['description']!),
      );

  Future<void> _addBill(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addBill'),
        [
          ('name', s.t('name'), TextInputType.text),
          (
            'amount',
            s.t('amountPositive'),
            const TextInputType.numberWithOptions(decimal: true)
          ),
          ('date', s.t('date'), TextInputType.datetime),
        ],
        (v) => v['name']!.isEmpty
            ? s.t('badText')
            : parseAmount(v['amount']!, signed: false) == null
                ? s.t('badAmount')
                : !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(v['date']!)
                    ? s.t('badDate')
                    : null,
        (v) => c.addBill(
            name: v['name']!,
            amount: parseAmount(v['amount']!, signed: false)!,
            dueOn: v['date']!),
      );
}

/// A small form in a dialog that owns its text controllers, so they live
/// until the dialog has finished closing.
class _FormDialog extends StatefulWidget {
  const _FormDialog(
      {required this.strings,
      required this.title,
      required this.fields,
      required this.check,
      required this.save});
  final FinanceStrings strings;
  final String title;
  final List<(String key, String label, TextInputType type)> fields;
  final String? Function(Map<String, String> values) check;
  final Future<String?> Function(Map<String, String> values) save;

  @override
  State<_FormDialog> createState() => _FormDialogState();
}

class _FormDialogState extends State<_FormDialog> {
  late final Map<String, TextEditingController> _controllers = {
    for (final f in widget.fields) f.$1: TextEditingController()
  };
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    final values = {
      for (final e in _controllers.entries) e.key: e.value.text.trim()
    };
    final problem = widget.check(values);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() => _busy = true);
    final failed = await widget.save(values);
    if (!mounted) return;
    if (failed != null) {
      setState(() {
        _error = failed;
        _busy = false;
      });
      return;
    }
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    final error = _error;
    return AlertDialog(
      title: Text(widget.title),
      scrollable: true,
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        for (final f in widget.fields)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
            child: TextField(
                controller: _controllers[f.$1],
                keyboardType: f.$3,
                decoration: InputDecoration(labelText: f.$2)),
          ),
        if (error != null)
          Text(error, style: TextStyle(color: context.palette.stop)),
      ]),
      actions: [
        TextButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(false),
            child: Text(s.t('cancel'))),
        FilledButton(
            onPressed: _busy ? null : _submit, child: Text(s.t('save'))),
      ],
    );
  }
}

class _Sections extends StatelessWidget {
  const _Sections({required this.controller, required this.strings});
  final FinanceController controller;
  final FinanceStrings strings;

  @override
  Widget build(BuildContext context) => Semantics(
        label: strings.t('sections'),
        container: true,
        child: Wrap(spacing: 8, runSpacing: 8, children: [
          for (final section in FinanceSection.values)
            if (section != FinanceSection.intel || controller.intel != null)
              ChoiceChip(
                label: Text(strings.t('section.${section.name}')),
                selected: controller.section == section,
                onSelected: (_) => controller.select(section),
              ),
        ]),
      );
}

/// One category's spend as a bar scaled to the largest, with its amount.
class _SpendBar extends StatelessWidget {
  const _SpendBar(
      {required this.label, required this.amount, required this.share});
  final String label, amount;
  final double share;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      label: '$label $amount',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          // Wraps instead of clipping when large text leaves no room.
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            spacing: AnumSpacing.xs,
            children: [
              Text(label),
              Text(amount, textDirection: TextDirection.ltr),
            ],
          ),
          const SizedBox(height: AnumSpacing.xxs),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
                value: share.clamp(0.0, 1.0),
                minHeight: 6,
                color: p.violet,
                backgroundColor: p.surface3),
          ),
        ]),
      ),
    );
  }
}
