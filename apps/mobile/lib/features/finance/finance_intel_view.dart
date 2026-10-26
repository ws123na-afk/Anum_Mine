import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'finance_controller.dart' show FinanceLoad;
import 'finance_intel_actions.dart';
import 'finance_broker_statement.dart';
import 'finance_intel_controller.dart';
import 'finance_intel_models.dart';
import 'finance_intel_strings.dart';
import 'finance_models.dart';
import 'finance_trading_models.dart' show trimDecimal;

/// The trading intelligence section of Finance on the phone
/// (docs/finance-trading-intel.md#dashboard). SIMULATED: the simulation
/// banner with live off, the kill switch (asks first), market sessions, a new
/// strategy and lifecycle moves (ask first), strategies with their runs, one
/// Supervisor cycle now and the pause of every paper strategy (ask first),
/// the daily reports, positions with P&L and drawdown over time against buy
/// and hold, agent decisions with reasons, and the Guardian rules.
class FinanceIntelView extends StatelessWidget {
  const FinanceIntelView({required this.controller, super.key});
  final FinanceIntelController controller;

  @override
  Widget build(BuildContext context) {
    final s = FinanceIntelStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final o = c.overview;
        final List<Widget> parts;
        if (c.overviewLoad == FinanceLoad.denied) {
          parts = [
            AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: s.t('denied'),
            ),
          ];
        } else if (o == null && c.overviewLoad == FinanceLoad.error) {
          parts = [
            AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('error', {'message': c.overviewError ?? ''}),
              onRetry: c.loadOverview,
            ),
          ];
        } else if (o == null) {
          parts = [
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: s.t('loading'),
            ),
          ];
        } else {
          parts = [
            _Banner(controller: c, overview: o, strings: s),
            IntelVenues(overview: o, strings: s),
            IntelMarketData(controller: c, overview: o, strings: s),
            IntelNewStrategy(controller: c, strings: s),
            _Strategies(controller: c, overview: o, strings: s),
            IntelSupervisor(controller: c, overview: o, strings: s),
            IntelReports(controller: c, strings: s),
            if (c.repository is ApiFinanceIntelRepository)
              FinanceBrokerStatement(api: (c.repository as ApiFinanceIntelRepository).api),
            _Positions(controller: c, overview: o, strings: s),
            _Decisions(controller: c, strings: s),
            _Risk(overview: o, strings: s),
          ];
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < parts.length; i++) ...[
              parts[i],
              if (i < parts.length - 1) const SizedBox(height: AnumSpacing.md),
            ],
          ],
        );
      },
    );
  }
}

String _date(DateTime d) {
  final l = d.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${l.year}-${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
}

String _metric(String key, double? value) {
  if (value == null || !value.isFinite) return '-';
  const percent = {
    'total_return',
    'cagr',
    'max_drawdown',
    'win_rate',
    'volatility',
    'exposure',
    'out_of_sample_return',
  };
  const count = {'trades', 'closed_trades', 'bars'};
  if (percent.contains(key)) return '${(value * 100).toStringAsFixed(2)}%';
  if (count.contains(key)) return value.round().toString();
  return value.toStringAsFixed(2);
}

String _percent(double? value) => value == null || !value.isFinite
    ? '-'
    : '${(value * 100).toStringAsFixed(2)}%';

Widget _ltr(String text, {TextStyle? style}) =>
    Text(text, textDirection: TextDirection.ltr, style: style);

class _Pager extends StatelessWidget {
  const _Pager({
    required this.offset,
    required this.shown,
    required this.total,
    required this.size,
    required this.onPage,
    required this.strings,
  });
  final int offset, shown, total, size;
  final void Function(int offset) onPage;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final range = strings.t('range', {
      'from': shown == 0 ? 0 : offset + 1,
      'to': offset + shown,
      'total': total,
    });
    if (total <= size && offset == 0) {
      return Text(range, style: TextStyle(color: context.palette.muted));
    }
    return Wrap(
      spacing: AnumSpacing.xs,
      runSpacing: AnumSpacing.xs,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        OutlinedButton(
          onPressed:
              offset == 0 ? null : () => onPage(math.max(0, offset - size)),
          child: Text(strings.t('previous')),
        ),
        Text(range, style: TextStyle(color: context.palette.muted)),
        OutlinedButton(
          onPressed:
              offset + shown >= total ? null : () => onPage(offset + size),
          child: Text(strings.t('next')),
        ),
      ],
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.controller,
    required this.overview,
    required this.strings,
  });
  final FinanceIntelController controller;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  Future<void> _confirm(BuildContext context) async {
    final s = strings;
    final stop = !overview.personEngaged;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t(stop ? 'stopTitle' : 'allowTitle')),
        content: Text(s.t(stop ? 'stopText' : 'allowText')),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(s.t('cancel')),
          ),
          FilledButton(
            style: stop
                ? FilledButton.styleFrom(
                    backgroundColor: context.palette.stop,
                    foregroundColor: Colors.white,
                  )
                : null,
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(s.t(stop ? 'stopAll' : 'allow')),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final error = await controller.setStopped(stop);
    if (!context.mounted) return;
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(
      SnackBar(
        content: Text(error ?? s.t(stop ? 'stoppedDone' : 'allowedDone')),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = strings, o = overview, p = context.palette;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
            title: s.t('title'),
            trailing: AnumPill(label: s.t('simulated'), tone: AnumTone.info),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Semantics(
            container: true,
            child: Text.rich(
              TextSpan(
                children: [
                  TextSpan(
                    text: '${s.t('simOnly')} ',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  TextSpan(text: s.t('simOnlyText')),
                ],
              ),
            ),
          ),
          const SizedBox(height: AnumSpacing.sm),
          AnumRow(
            title: s.t('live'),
            detail: o.liveEnabled ? s.t('liveOn') : s.t('liveOff'),
            icon: Icons.block,
            tone: AnumTone.warn,
          ),
          AnumRow(
            title: s.t('execution'),
            detail: o.stopped ? s.t('stopped') : s.t('running'),
            subtitle: o.globalEngaged ? s.t('globalStopped') : null,
            icon: Icons.power_settings_new,
            tone: o.stopped ? AnumTone.warn : AnumTone.ok,
          ),
          Text(
            s.t('waiting', {'count': o.pendingRequests}),
            style: TextStyle(color: p.muted),
          ),
          Text(
            s.t('agentRequests', {'count': o.agentRequests}),
            style: TextStyle(color: p.muted),
          ),
          const SizedBox(height: AnumSpacing.sm),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              style: o.personEngaged
                  ? null
                  : FilledButton.styleFrom(
                      backgroundColor: p.stop,
                      foregroundColor: Colors.white,
                    ),
              onPressed: controller.switching ? null : () => _confirm(context),
              icon: Icon(
                o.personEngaged
                    ? Icons.play_arrow_rounded
                    : Icons.stop_circle_outlined,
              ),
              label: Text(s.t(o.personEngaged ? 'allow' : 'stopAll')),
            ),
          ),
        ],
      ),
    );
  }
}

class _Strategies extends StatelessWidget {
  const _Strategies({
    required this.controller,
    required this.overview,
    required this.strings,
  });
  final FinanceIntelController controller;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final page = c.strategies;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('strategies')),
          Text(s.t('lifecycleHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              ChoiceChip(
                label: Text(s.t('allStates')),
                selected: c.stateFilter == null,
                onSelected: (_) => c.filterState(null),
              ),
              for (final state in strategyStates)
                ChoiceChip(
                  label: Text(
                    s.t('stateCount', {
                      'state': s.t('state.$state'),
                      'count': overview.counts[state] ?? 0,
                    }),
                  ),
                  selected: c.stateFilter == state,
                  onSelected: (_) => c.filterState(state),
                ),
            ],
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.strategiesLoad == FinanceLoad.denied)
            AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: s.t('denied'),
            )
          else if (c.strategiesLoad == FinanceLoad.error)
            AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('strategiesError', {
                'message': c.strategiesError ?? '',
              }),
              onRetry: c.loadStrategies,
            )
          else if (page == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: s.t('loading'),
            )
          else if (page.items.isEmpty)
            c.stateFilter != null
                ? AnumEmpty(
                    title: s.t('noneInState', {
                      'state': s.t('state.${c.stateFilter}'),
                    }),
                    message: s.t('noneInStateText'),
                    action: s.t('allStates'),
                    onAction: () => c.filterState(null),
                  )
                : AnumEmpty(
                    icon: Icons.show_chart,
                    title: s.t('noStrategies'),
                    message: s.t('noStrategiesText'),
                  )
          else ...[
            for (final strategy in page.items) ...[
              AnumRow(
                title: '${strategy.name} ${s.t('version', {
                      'version': strategy.version
                    })}',
                subtitle: '${strategy.reason} ${s.t('movedBy', {
                      'who': s.label('agent', strategy.stateChangedBy)
                    })}',
                detail: s.t('state.${strategy.state}'),
                icon: Icons.show_chart,
                tone: strategy.state == 'PAPER_ACTIVE'
                    ? AnumTone.ok
                    : strategy.state == 'PAPER_SUSPENDED' ||
                            strategy.state == 'RETIRED'
                        ? AnumTone.warn
                        : null,
              ),
              Wrap(
                spacing: AnumSpacing.xs,
                children: [
                  TextButton(
                    onPressed: () => c.select(strategy),
                    child: Text(
                      s.t(
                        c.selected?.id == strategy.id
                            ? 'hideResults'
                            : 'results',
                      ),
                    ),
                  ),
                  IntelMoveButton(
                    controller: c,
                    strategy: strategy,
                    strings: s,
                  ),
                  IntelEvaluateButton(
                    controller: c,
                    strategy: strategy,
                    strings: s,
                  ),
                  IntelPortfolioButton(
                    controller: c,
                    strategy: strategy,
                    overview: overview,
                    strings: s,
                  ),
                ],
              ),
              if (c.selected?.id == strategy.id)
                _Runs(controller: c, strings: s),
            ],
            _Pager(
              offset: page.offset,
              shown: page.items.length,
              total: page.total,
              size: FinanceIntelController.pageSize,
              onPage: (o) => c.loadStrategies(offset: o),
              strings: s,
            ),
          ],
        ],
      ),
    );
  }
}

class _Runs extends StatelessWidget {
  const _Runs({required this.controller, required this.strings});
  final FinanceIntelController controller;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final page = c.runs;
    final List<Widget> body;
    if (c.runsLoad == FinanceLoad.error || c.runsLoad == FinanceLoad.denied) {
      body = [
        AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: s.t('runsError', {'message': c.runsError ?? ''}),
          onRetry: c.loadRuns,
        ),
      ];
    } else if (page == null) {
      body = [
        AnumFeedback(kind: AnumFeedbackKind.loading, message: s.t('loading')),
      ];
    } else if (page.items.isEmpty) {
      body = [Text(s.t('noRuns'), style: TextStyle(color: p.muted))];
    } else {
      body = [
        for (final run in page.items)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xxs,
                  children: [
                    Text(
                      s.label('kind', run.kind),
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    AnumPill(
                      label: s.label('verdict', run.verdict),
                      tone: run.verdict == 'passed'
                          ? AnumTone.ok
                          : run.verdict == 'rejected'
                              ? AnumTone.warn
                              : AnumTone.neutral,
                    ),
                    AnumPill(
                      label: s.t(
                        run.reproduced ? 'reproduced' : 'notReproduced',
                      ),
                    ),
                    AnumPill(label: s.t('simulated'), tone: AnumTone.info),
                  ],
                ),
                Text(
                  '${run.dataSource} · ${_date(run.createdAt)}',
                  style: TextStyle(color: p.muted),
                ),
                for (final e in run.metrics.entries)
                  _ltr(
                    '${s.label('metric', e.key)}: ${_metric(e.key, e.value)}',
                  ),
                if (run.benchmark.isNotEmpty)
                  Text(
                    '${s.t('benchmark')}: ${run.benchmark.entries.map((e) => '${s.label('metric', e.key)} ${_metric(e.key, e.value)}').join(', ')}',
                  ),
                if (run.folds > 0) Text(s.t('folds', {'count': run.folds})),
                for (final reason in run.reasons) Text('• $reason'),
                if (run.warnings.isNotEmpty)
                  Text(
                    '${s.t('warnings')}: ${run.warnings.join(' ')}',
                    style: TextStyle(color: p.warn),
                  ),
              ],
            ),
          ),
        _Pager(
          offset: page.offset,
          shown: page.items.length,
          total: page.total,
          size: FinanceIntelController.runPageSize,
          onPage: (o) => c.loadRuns(offset: o),
          strings: s,
        ),
        Text(s.t('noPromise'), style: TextStyle(color: p.muted)),
      ];
    }
    return Container(
      margin: const EdgeInsetsDirectional.only(
        start: AnumSpacing.md,
        bottom: AnumSpacing.sm,
      ),
      padding: const EdgeInsetsDirectional.only(start: AnumSpacing.sm),
      decoration: BoxDecoration(
        border: BorderDirectional(start: BorderSide(color: p.line, width: 2)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: body,
      ),
    );
  }
}

class _Positions extends StatelessWidget {
  const _Positions({
    required this.controller,
    required this.overview,
    required this.strings,
  });
  final FinanceIntelController controller;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('positions')),
          if (overview.portfolios.isEmpty)
            AnumEmpty(
              icon: Icons.account_balance_wallet_outlined,
              title: s.t('noPortfolios'),
              message: s.t('noPortfoliosText'),
            )
          else
            for (final portfolio in overview.portfolios)
              _Book(controller: controller, portfolio: portfolio, strings: s),
        ],
      ),
    );
  }
}

class _Book extends StatelessWidget {
  const _Book({
    required this.controller,
    required this.portfolio,
    required this.strings,
  });
  final FinanceIntelController controller;
  final IntelPortfolio portfolio;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, b = portfolio, p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            children: [
              Text(b.name, style: const TextStyle(fontWeight: FontWeight.w700)),
              AnumPill(label: s.label('mode', b.mode)),
              AnumPill(label: s.t('simulated'), tone: AnumTone.info),
            ],
          ),
          if (b.mode == 'paper')
            _ltr('${s.t('cash')}: ${formatAmounts(b.cash)}'),
          _ltr('${s.t('realised')}: ${formatAmounts(b.realised)}'),
          _ltr('${s.t('unrealised')}: ${formatAmounts(b.unrealised)}'),
          Text(
            s.t('costs', {
              'fee': formatAmounts(b.fees),
              'slippage': formatAmounts(b.slippage),
            }),
          ),
          if (b.unpriced.isNotEmpty)
            Text(
              s.t('unpriced', {'list': b.unpriced.join(', ')}),
              style: TextStyle(color: p.warn),
            ),
          if (b.holdings.isEmpty)
            Text(s.t('noHoldings'), style: TextStyle(color: p.muted))
          else
            for (final h in b.holdings)
              _ltr(
                s.t('holding', {
                  'quantity': trimDecimal(h.quantity),
                  'symbol': h.symbol,
                  'value': h.marketValue == null
                      ? '-'
                      : formatAmount(h.marketValue!, h.currency),
                  'gain': h.unrealised == null
                      ? '-'
                      : formatAmount(h.unrealised!, h.currency),
                }),
              ),
          if (b.holdingsTotal > b.holdings.length)
            Text(
              s.t('moreHoldings', {
                'shown': b.holdings.length,
                'total': b.holdingsTotal,
              }),
              style: TextStyle(color: p.muted),
            ),
          const SizedBox(height: AnumSpacing.xs),
          _Equity(controller: controller, portfolio: b, strings: s),
        ],
      ),
    );
  }
}

class _Equity extends StatelessWidget {
  const _Equity({
    required this.controller,
    required this.portfolio,
    required this.strings,
  });
  final FinanceIntelController controller;
  final IntelPortfolio portfolio;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final load = c.equityLoad[portfolio.id] ?? FinanceLoad.idle;
    final curve = c.equity[portfolio.id];
    final title = Text(
      s.t('equity', {'days': FinanceIntelController.equityDays}),
      style: const TextStyle(fontWeight: FontWeight.w600),
    );
    if (load == FinanceLoad.denied) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          title,
          AnumFeedback(
            kind: AnumFeedbackKind.permission,
            message: s.t('denied'),
          ),
        ],
      );
    }
    if (load == FinanceLoad.error) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          title,
          AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('equityError', {
              'message': c.equityError[portfolio.id] ?? '',
            }),
            onRetry: () => c.loadEquity(portfolio.id),
          ),
        ],
      );
    }
    if (curve == null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          title,
          Text(s.t('equityLoading'), style: TextStyle(color: p.muted)),
        ],
      );
    }
    if (curve.orders == 0 || curve.series.isEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          title,
          Text(s.t('equityEmpty'), style: TextStyle(color: p.muted)),
        ],
      );
    }
    final paper = portfolio.mode == 'paper';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        title,
        for (final series in curve.series) ...[
          _seriesText(series, paper, s),
          const SizedBox(height: AnumSpacing.xxs),
          Semantics(
            label: s.t('equityChart', {
                  'currency': series.currency,
                  'start': curve.start,
                  'end': curve.end,
                  'latest': series.latest?.pnl == null
                      ? '-'
                      : formatAmount(series.latest!.pnl!, series.currency),
                  'drawdown': _drawdown(series, paper, s),
                }) +
                (paper && series.latestBenchmark != null
                    ? ' ${s.t('equityBenchmark', {
                            'pnl': formatAmount(
                                series.latestBenchmark!.toStringAsFixed(2),
                                series.currency)
                          })}'
                    : ''),
            image: true,
            excludeSemantics: true,
            child: SizedBox(
              height: 96,
              child: CustomPaint(
                size: Size.infinite,
                painter: EquityPainter(
                  values: [
                    for (final point in series.points)
                      point.complete ? double.tryParse(point.pnl ?? '') : null,
                  ],
                  benchmark: paper ? series.benchmark : const [],
                  line: p.sky,
                  benchmarkLine: p.muted,
                  zero: p.line,
                ),
              ),
            ),
          ),
          Directionality(
            textDirection: TextDirection.ltr,
            child: Wrap(
              alignment: WrapAlignment.spaceBetween,
              spacing: AnumSpacing.xs,
              children: [
                Text(
                  curve.start,
                  style: TextStyle(color: p.muted, fontSize: AnumType.xs),
                ),
                Text(curve.end,
                    style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
              ],
            ),
          ),
        ],
        Text(s.t('equityNote'), style: TextStyle(color: p.muted)),
      ],
    );
  }

  String _drawdown(EquitySeries series, bool paper, FinanceIntelStrings s) {
    final deep = double.tryParse(series.maxDrawdown ?? '');
    if (deep == null || deep >= 0) return s.t('equityNoDrawdown');
    final amount = formatAmount(series.maxDrawdown!, series.currency);
    return s.t('equityDrawdown', {
      'size': paper && series.maxDrawdownPct != null
          ? '$amount (${_percent(series.maxDrawdownPct)})'
          : amount,
      'peak': series.peakDay ?? '-',
      'trough': series.troughDay ?? '-',
    });
  }

  Widget _seriesText(EquitySeries series, bool paper, FinanceIntelStrings s) {
    final latest = series.latest;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (paper && latest?.value != null)
          Text(
            s.t('equityValue', {
              'value': formatAmount(latest!.value!, series.currency),
            }),
          ),
        Text(
          s.t('equityLatest', {
            'pnl': latest?.pnl == null
                ? '-'
                : formatAmount(latest!.pnl!, series.currency),
          }),
        ),
        Text(_drawdown(series, paper, s)),
        if (series.gaps > 0) Text(s.t('equityGaps', {'count': series.gaps})),
        if (paper && series.latestBenchmark != null) ...[
          Text(
            s.t('equityBenchmark', {
              'pnl': formatAmount(
                series.latestBenchmark!.toStringAsFixed(2),
                series.currency,
              ),
            }),
          ),
          Text(
            s.t('equityBenchmarkWhat', {
              'symbols': series.benchmarkSymbols.join(', '),
              'start': series.benchmarkStart ?? '-',
            }),
            style: const TextStyle(fontSize: AnumType.xs),
          ),
        ] else if (series.benchmarkReason != null)
          Text(
            s.t('equityBenchmark.${series.benchmarkReason}'),
            style: const TextStyle(fontSize: AnumType.xs),
          ),
      ],
    );
  }
}

/// SIMULATED P&L as a line, always left to right in time, zero dashed. A gap
/// day breaks the line: nothing is drawn across it.
class EquityPainter extends CustomPainter {
  const EquityPainter({
    required this.values,
    required this.line,
    required this.zero,
    this.benchmark = const [],
    this.benchmarkLine,
  });
  final List<double?> values;

  /// Buy and hold of the same symbols, drawn dashed on the same scale.
  final List<double?> benchmark;
  final Color line, zero;
  final Color? benchmarkLine;

  @override
  void paint(Canvas canvas, Size size) {
    final known = [
      ...values.whereType<double>(),
      ...benchmark.whereType<double>(),
    ];
    if (known.isEmpty) return;
    var low = math.min(0.0, known.reduce(math.min));
    var high = math.max(0.0, known.reduce(math.max));
    if (low == high) {
      low -= 1;
      high += 1;
    }
    const pad = 4.0;
    double x(int i) => values.length <= 1
        ? size.width / 2
        : pad + i * (size.width - 2 * pad) / (values.length - 1);
    double y(double v) =>
        pad + (high - v) / (high - low) * (size.height - 2 * pad);
    final zeroPaint = Paint()
      ..color = zero
      ..strokeWidth = 1;
    for (var dx = 0.0; dx < size.width; dx += 6) {
      canvas.drawLine(
        Offset(dx, y(0)),
        Offset(math.min(dx + 3, size.width), y(0)),
        zeroPaint,
      );
    }
    if (benchmark.whereType<double>().isNotEmpty) {
      final dash = Paint()
        ..color = benchmarkLine ?? zero
        ..strokeWidth = 1.5;
      for (var i = 0; i + 1 < benchmark.length; i++) {
        final a = benchmark[i], b = benchmark[i + 1];
        if (a == null || b == null) continue;
        final from = Offset(x(i), y(a)), to = Offset(x(i + 1), y(b));
        final length = (to - from).distance;
        for (var d = 0.0; d < length; d += 6) {
          final t0 = d / length, t1 = math.min(d + 3, length) / length;
          canvas.drawLine(
            Offset.lerp(from, to, t0)!,
            Offset.lerp(from, to, t1)!,
            dash,
          );
        }
      }
    }
    final paint = Paint()
      ..color = line
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    Path? path;
    for (var i = 0; i < values.length; i++) {
      final v = values[i];
      if (v == null) {
        if (path != null) canvas.drawPath(path, paint);
        path = null;
        continue;
      }
      if (path == null) {
        path = Path()..moveTo(x(i), y(v));
        if (i == values.length - 1 || values[i + 1] == null) {
          path.lineTo(x(i) + 1, y(v));
        }
      } else {
        path.lineTo(x(i), y(v));
      }
    }
    if (path != null) canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(EquityPainter old) =>
      old.values != values ||
      old.benchmark != benchmark ||
      old.line != line ||
      old.zero != zero;
}

class _Decisions extends StatelessWidget {
  const _Decisions({required this.controller, required this.strings});
  final FinanceIntelController controller;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final page = c.decisions;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('agents')),
          Text(s.t('agentsHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.xs),
          DropdownButtonFormField<String?>(
            isExpanded: true,
            initialValue: c.agentFilter,
            decoration: InputDecoration(labelText: s.t('allAgents')),
            items: [
              DropdownMenuItem<String?>(
                value: null,
                child: Text(s.t('allAgents')),
              ),
              for (final agent in intelAgents)
                DropdownMenuItem<String?>(
                  value: agent,
                  child: Text(s.label('agent', agent)),
                ),
            ],
            onChanged: c.filterAgent,
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (c.decisionsLoad == FinanceLoad.error ||
              c.decisionsLoad == FinanceLoad.denied)
            AnumFeedback(
              kind: c.decisionsLoad == FinanceLoad.denied
                  ? AnumFeedbackKind.permission
                  : AnumFeedbackKind.error,
              message: c.decisionsLoad == FinanceLoad.denied
                  ? s.t('denied')
                  : s.t('decisionsError', {'message': c.decisionsError ?? ''}),
              onRetry: c.decisionsLoad == FinanceLoad.denied
                  ? null
                  : c.loadDecisions,
            )
          else if (page == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: s.t('loading'),
            )
          else if (page.items.isEmpty)
            Text(s.t('noDecisions'), style: TextStyle(color: p.muted))
          else ...[
            for (final d in page.items) ...[
              AnumRow(
                title:
                    '${s.label('agent', d.agent)}: ${d.action.replaceAll('_', ' ')}',
                detail: s.label('outcome', d.outcome),
                subtitle: _date(d.createdAt),
                icon: Icons.smart_toy_outlined,
                tone: d.outcome == 'refused' ||
                        d.outcome == 'failed' ||
                        d.outcome == 'suspended'
                    ? AnumTone.warn
                    : null,
              ),
              // Recorded reasons, shown in full as text and never followed.
              for (final reason in d.reasons)
                Padding(
                  padding: const EdgeInsetsDirectional.only(
                    start: AnumSpacing.xl,
                    bottom: AnumSpacing.xxs,
                  ),
                  child: Text('• $reason'),
                ),
            ],
            _Pager(
              offset: page.offset,
              shown: page.items.length,
              total: page.total,
              size: FinanceIntelController.pageSize,
              onPage: (o) => c.loadDecisions(offset: o),
              strings: s,
            ),
          ],
        ],
      ),
    );
  }
}

class _Risk extends StatelessWidget {
  const _Risk({required this.overview, required this.strings});
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, p = context.palette;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
            title: s.t('risk'),
            trailing: AnumPill(label: s.t('readOnly')),
          ),
          if (overview.rules.isEmpty)
            Text(s.t('noRules'), style: TextStyle(color: p.muted))
          else ...[
            Text(
              s.t('riskVersion', {'version': overview.riskVersion}),
              style: TextStyle(color: p.muted),
            ),
            for (final rule in overview.rules)
              Text('• ${s.t('rule.${rule.code}', {'value': rule.value})}'),
          ],
          const SizedBox(height: AnumSpacing.xs),
          Text(s.t('alwaysApprove'), style: TextStyle(color: p.muted)),
        ],
      ),
    );
  }
}
