import 'dart:convert';

import 'package:file_picker/file_picker.dart';

import '../../src/infrastructure/picked_file_size.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'finance_controller.dart' show FinanceLoad;
import 'finance_intel_controller.dart';
import 'finance_intel_models.dart';
import 'finance_intel_strings.dart';

/// The parts of the phone's trading intelligence section that change
/// research or read the written reports (docs/finance-trading-intel.md#dashboard,
/// #daily-report): market sessions, a new strategy, lifecycle moves, one
/// Supervisor cycle now, the pause of every paper strategy, and the daily
/// reports. Every change asks first and shows the server's own refusal.
/// SIMULATED: nothing here approves an order, buys, sells or trades live.

Widget _ltr(String text, {TextStyle? style}) =>
    Text(text, textDirection: TextDirection.ltr, style: style);

Future<bool> _confirm(
  BuildContext context, {
  required String title,
  required String text,
  required String action,
  bool danger = false,
  required FinanceIntelStrings strings,
}) async {
  final ok = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: SingleChildScrollView(child: Text(text)),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: Text(strings.t('cancel')),
        ),
        FilledButton(
          style: danger
              ? FilledButton.styleFrom(
                  backgroundColor: context.palette.stop,
                  foregroundColor: Colors.white,
                )
              : null,
          onPressed: () => Navigator.of(context).pop(true),
          child: Text(action),
        ),
      ],
    ),
  );
  return ok == true;
}

void _say(BuildContext context, String text) {
  final messenger = ScaffoldMessenger.maybeOf(context);
  messenger?.hideCurrentSnackBar();
  messenger?.showSnackBar(SnackBar(content: Text(text)));
}

// -- market sessions ----------------------------------------------------------

class IntelVenues extends StatelessWidget {
  const IntelVenues({required this.overview, required this.strings, super.key});
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, p = context.palette;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('venues')),
          Text(s.t('venuesHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.xs),
          if (overview.venues.isEmpty)
            Text(s.t('noVenues'), style: TextStyle(color: p.muted))
          else
            for (final v in overview.venues)
              AnumRow(
                title: v.name,
                subtitle: [
                  s.t('venueTime', {
                    'day': v.weekday == null ? '' : s.t('weekday.${v.weekday}'),
                    'time': v.hhmm,
                    'zone': v.timezone,
                  }),
                  if (v.reason.isNotEmpty) v.reason,
                  if (!v.holidaysKnown) s.t('holidaysUnknown'),
                ].join('\n'),
                detail: s.label('session', v.state),
                icon: Icons.schedule,
                tone: v.state == 'open' ? AnumTone.ok : AnumTone.neutral,
              ),
        ],
      ),
    );
  }
}

// -- a new strategy -----------------------------------------------------------

class IntelNewStrategy extends StatelessWidget {
  const IntelNewStrategy({
    required this.controller,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final Widget body;
    if (c.setupLoad == FinanceLoad.denied) {
      body = AnumFeedback(
        kind: AnumFeedbackKind.permission,
        message: s.t('actionsDenied'),
      );
    } else if (c.setupLoad == FinanceLoad.error) {
      body = AnumFeedback(
        kind: AnumFeedbackKind.error,
        message: s.t('newError', {'message': c.setupError ?? ''}),
        onRetry: c.loadSetup,
      );
    } else if (c.setupLoad != FinanceLoad.ready) {
      body = AnumFeedback(
        kind: AnumFeedbackKind.loading,
        message: s.t('newLoading'),
      );
    } else if (c.instruments.isEmpty) {
      body = Text(s.t('newNeedBars'), style: TextStyle(color: p.muted));
    } else {
      body = Align(
        alignment: AlignmentDirectional.centerStart,
        child: FilledButton.icon(
          onPressed: c.acting ? null : () => _open(context),
          icon: const Icon(Icons.add),
          label: Text(s.t('newOpen')),
        ),
      );
    }
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('newTitle')),
          Text(s.t('newHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.sm),
          body,
        ],
      ),
    );
  }

  Future<void> _open(BuildContext context) async {
    final done = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            NewStrategyScreen(controller: controller, strings: strings),
      ),
    );
    if (done == true && context.mounted) _say(context, strings.t('created'));
  }
}

/// The new-strategy form on its own screen, so it has the whole phone.
class NewStrategyScreen extends StatefulWidget {
  const NewStrategyScreen({
    required this.controller,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final FinanceIntelStrings strings;

  @override
  State<NewStrategyScreen> createState() => _NewStrategyScreenState();
}

class _NewStrategyScreenState extends State<NewStrategyScreen> {
  String? _rule;
  final _chosen = <String>{};
  final _reason = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  StrategyRule? get _picked {
    for (final r in widget.controller.rules) {
      if (r.key == _rule) return r;
    }
    return null;
  }

  Future<void> _submit() async {
    final s = widget.strings, rule = _picked;
    String? problem;
    if (rule == null) {
      problem = s.t('pickRule');
    } else if (_chosen.length < rule.minSymbols ||
        _chosen.length > rule.maxSymbols) {
      problem = s.t('symbolCount', {
        'min': rule.minSymbols,
        'max': rule.maxSymbols,
      });
    } else if (_reason.text.trim().length < 3) {
      problem = s.t('reasonRequired');
    }
    setState(() => _error = problem);
    if (problem != null || rule == null) return;
    final ok = await _confirm(
      context,
      title: s.t('newConfirmTitle'),
      text: s.t('newConfirmText', {'name': rule.name, 'count': _chosen.length}),
      action: s.t('create'),
      strings: s,
    );
    if (!ok || !mounted) return;
    final error = await widget.controller.createStrategy(
      key: rule.key,
      instrumentIds: _chosen.toList(),
      reason: _reason.text.trim(),
    );
    if (!mounted) return;
    if (error != null) {
      setState(() => _error = error);
      return;
    }
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings, c = widget.controller, p = context.palette;
    final rule = _picked;
    final groups = <int, List<StrategyRule>>{};
    for (final r in c.rules) {
      groups.putIfAbsent(r.candidate, () => []).add(r);
    }
    final ordered = groups.keys.toList()..sort();
    return Scaffold(
      appBar: AppBar(title: Text(s.t('newTitle'))),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Text(s.t('newHelp'), style: TextStyle(color: p.muted)),
            const SizedBox(height: AnumSpacing.md),
            DropdownButtonFormField<String>(
              initialValue: _rule,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('rule')),
              hint: Text(s.t('chooseRule')),
              items: [
                for (final candidate in ordered)
                  for (final r in groups[candidate]!)
                    DropdownMenuItem(
                      value: r.key,
                      child: Text(
                        candidate == 0
                            ? '${s.t('benchmarkRule')}: ${r.name}'
                            : '$candidate. ${r.name}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
              ],
              onChanged: (v) => setState(() {
                _rule = v;
                _error = null;
              }),
            ),
            if (rule != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              for (final (label, text) in [
                (s.t('hypothesis'), rule.hypothesis),
                (s.t('entry'), rule.entry),
                (s.t('exit'), rule.exit),
              ])
                Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                  child: Text.rich(
                    TextSpan(
                      children: [
                        TextSpan(
                          text: '$label: ',
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                        TextSpan(text: text),
                      ],
                    ),
                  ),
                ),
            ],
            const SizedBox(height: AnumSpacing.sm),
            Text(
              s.t('instrumentsPick'),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            for (final i in c.instruments)
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                value: _chosen.contains(i.id),
                onChanged: (on) => setState(() {
                  on == true ? _chosen.add(i.id) : _chosen.remove(i.id);
                  _error = null;
                }),
                title: _ltr(i.symbol),
                subtitle: Text(
                  s.t('barsRange', {
                    'count': i.bars,
                    'first': i.firstBar ?? '-',
                    'last': i.lastBar ?? '-',
                  }),
                ),
              ),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              controller: _reason,
              maxLength: 300,
              decoration: InputDecoration(labelText: s.t('reason')),
            ),
            if (_error != null)
              Semantics(
                liveRegion: true,
                child: Text(_error!, style: TextStyle(color: p.stop)),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              spacing: AnumSpacing.sm,
              runSpacing: AnumSpacing.xs,
              children: [
                FilledButton(
                  onPressed: c.acting ? null : _submit,
                  child: Text(s.t('create')),
                ),
                OutlinedButton(
                  onPressed:
                      c.acting ? null : () => Navigator.of(context).pop(false),
                  child: Text(s.t('cancel')),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// -- a lifecycle move ---------------------------------------------------------

/// "Move" for one strategy version: pick a state the lifecycle allows and a
/// reason, then confirm (more strongly for paper trading or retiring).
class IntelMoveButton extends StatelessWidget {
  const IntelMoveButton({
    required this.controller,
    required this.strategy,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final IntelStrategy strategy;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final moves = strategyMoves[strategy.state] ?? const [];
    if (moves.isEmpty || controller.setupLoad == FinanceLoad.denied) {
      return const SizedBox.shrink();
    }
    return TextButton.icon(
      onPressed: controller.acting ? null : () => _move(context, moves),
      icon: const Icon(Icons.swap_horiz),
      label: Text(strings.t('move')),
    );
  }

  Future<void> _move(BuildContext context, List<String> moves) async {
    final s = strings;
    final picked = await showDialog<(String, String)>(
      context: context,
      builder: (_) => _MoveDialog(strategy: strategy, moves: moves, strings: s),
    );
    if (picked == null || !context.mounted) return;
    final (target, reason) = picked;
    final names = {
      'name': strategy.name,
      'version': strategy.version,
      'state': s.t('state.$target'),
    };
    final ok = await _confirm(
      context,
      title: s.t('confirmMove', names),
      text: s.t(
        target == 'PAPER_ACTIVE'
            ? 'confirmActive'
            : target == 'RETIRED'
                ? 'confirmRetire'
                : 'confirmMoveText',
        names,
      ),
      action: s.t('move'),
      danger: target == 'RETIRED',
      strings: s,
    );
    if (!ok || !context.mounted) return;
    final error = await controller.move(
      strategy,
      state: target,
      reason: reason,
    );
    if (!context.mounted) return;
    _say(context, error ?? s.t('moved', {'state': s.t('state.$target')}));
  }
}

class _MoveDialog extends StatefulWidget {
  const _MoveDialog({
    required this.strategy,
    required this.moves,
    required this.strings,
  });
  final IntelStrategy strategy;
  final List<String> moves;
  final FinanceIntelStrings strings;

  @override
  State<_MoveDialog> createState() => _MoveDialogState();
}

class _MoveDialogState extends State<_MoveDialog> {
  String? _target;
  final _reason = TextEditingController();
  String? _error;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    return AlertDialog(
      title: Text(
        s.t('moveTitle', {
          'name': widget.strategy.name,
          'version': widget.strategy.version,
        }),
      ),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              s.t('moveTo'),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            RadioGroup<String>(
              groupValue: _target,
              onChanged: (v) => setState(() {
                _target = v;
                _error = null;
              }),
              child: Column(
                children: [
                  for (final m in widget.moves)
                    RadioListTile<String>(
                      contentPadding: EdgeInsets.zero,
                      value: m,
                      title: Text(s.t('state.$m')),
                    ),
                ],
              ),
            ),
            TextField(
              controller: _reason,
              maxLength: 300,
              decoration: InputDecoration(labelText: s.t('reason')),
            ),
            if (_error != null)
              Text(_error!, style: TextStyle(color: context.palette.stop)),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(s.t('cancel')),
        ),
        FilledButton(
          onPressed: () {
            if (_target == null || _reason.text.trim().length < 3) {
              setState(
                () => _error =
                    _target == null ? s.t('moveTo') : s.t('reasonRequired'),
              );
              return;
            }
            Navigator.of(context).pop((_target!, _reason.text.trim()));
          },
          child: Text(s.t('move')),
        ),
      ],
    );
  }
}

// -- the Supervisor -----------------------------------------------------------

class IntelSupervisor extends StatefulWidget {
  const IntelSupervisor({
    required this.controller,
    required this.overview,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  State<IntelSupervisor> createState() => _IntelSupervisorState();
}

class _IntelSupervisorState extends State<IntelSupervisor> {
  String _phase = 'pre_open';

  int get _active => widget.overview.counts['PAPER_ACTIVE'] ?? 0;

  Future<void> _run() async {
    final s = widget.strings, phase = s.t('phase.$_phase');
    final ok = await _confirm(
      context,
      title: s.t('confirmRun', {'phase': phase}),
      text: s.t('confirmRunText'),
      action: s.t('runNow'),
      strings: s,
    );
    if (!ok || !mounted) return;
    final error = await widget.controller.runCycle(_phase);
    if (!mounted) return;
    if (error != null) _say(context, error);
  }

  Future<void> _pause() async {
    final s = widget.strings;
    final ok = await _confirm(
      context,
      title: s.t('confirmPause'),
      text: s.t('confirmPauseText', {'count': _active}),
      action: s.t('pauseAll'),
      danger: true,
      strings: s,
    );
    if (!ok || !mounted) return;
    final (error, paused) = await widget.controller.pauseAll(
      s.t('pauseReason'),
    );
    if (!mounted) return;
    _say(context, error ?? s.t('paused', {'count': paused}));
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings, c = widget.controller, p = context.palette;
    final cycle = c.lastCycle;
    final denied = c.setupLoad == FinanceLoad.denied;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
            title: s.t('supTitle'),
            trailing: AnumPill(label: s.t('simulated'), tone: AnumTone.info),
          ),
          Text(s.t('supHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.sm),
          if (cycle != null)
            Semantics(
              liveRegion: true,
              child: Text(
                [
                  cycle.skipped
                      ? s.t('ranSkipped', {
                          'phase': s.t('phase.${cycle.phase}'),
                        })
                      : s.t('ran', {
                          'phase': s.t('phase.${cycle.phase}'),
                          'active': cycle.active,
                          'proposed': cycle.proposed,
                          'refused': cycle.refused,
                          'suspended': cycle.suspended,
                        }),
                  if (!cycle.skipped && cycle.halted > 0) s.t('ranHalted'),
                  if (cycle.reportId != null) s.t('ranReport'),
                ].join(' '),
              ),
            ),
          if (denied)
            AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: s.t('actionsDenied'),
            )
          else ...[
            DropdownButtonFormField<String>(
              initialValue: _phase,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('phase')),
              items: [
                for (final ph in intelPhases)
                  DropdownMenuItem(value: ph, child: Text(s.t('phase.$ph'))),
              ],
              onChanged: (v) => setState(() => _phase = v ?? _phase),
            ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              spacing: AnumSpacing.sm,
              runSpacing: AnumSpacing.xs,
              children: [
                FilledButton.icon(
                  onPressed: c.acting ? null : _run,
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: Text(s.t('runNow')),
                ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(foregroundColor: p.stop),
                  onPressed: c.acting || _active == 0 ? null : _pause,
                  icon: const Icon(Icons.pause_circle_outline),
                  label: Text(s.t('pauseAll')),
                ),
              ],
            ),
            if (_active == 0)
              Text(s.t('noneActive'), style: TextStyle(color: p.muted)),
          ],
        ],
      ),
    );
  }
}

// -- daily reports ------------------------------------------------------------

class IntelReports extends StatelessWidget {
  const IntelReports({
    required this.controller,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final page = c.reports;
    final List<Widget> body;
    if (c.reportsLoad == FinanceLoad.denied) {
      body = [
        AnumFeedback(kind: AnumFeedbackKind.permission, message: s.t('denied')),
      ];
    } else if (c.reportsLoad == FinanceLoad.error) {
      body = [
        AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: s.t('reportsError', {'message': c.reportsError ?? ''}),
          onRetry: c.loadReports,
        ),
      ];
    } else if (page == null) {
      body = [
        AnumFeedback(
          kind: AnumFeedbackKind.loading,
          message: s.t('reportsLoading'),
        ),
      ];
    } else if (page.items.isEmpty) {
      body = [
        AnumEmpty(
          icon: Icons.description_outlined,
          title: s.t('reportsEmpty'),
          message: s.t('reportsEmptyText'),
        ),
      ];
    } else {
      body = [
        for (final r in page.items)
          AnumRow(
            title: r.date,
            subtitle: s.t('reportLine', {
              'active': r.count('active_strategies'),
              'proposed': r.count('proposed_orders'),
              'refused': r.count('refused_signals'),
              'suspended': r.count('suspended'),
            }),
            detail: r.needsAction ? s.t('needsYou') : null,
            tone: r.needsAction ? AnumTone.warn : null,
            icon: Icons.description_outlined,
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) =>
                    DailyReportScreen(controller: c, item: r, strings: s),
              ),
            ),
          ),
        if (page.total > FinanceIntelController.reportPageSize ||
            page.offset > 0)
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: AnumSpacing.sm,
            runSpacing: AnumSpacing.xs,
            children: [
              OutlinedButton(
                onPressed: page.offset == 0
                    ? null
                    : () => c.loadReports(
                          offset: (page.offset -
                                  FinanceIntelController.reportPageSize)
                              .clamp(0, page.total),
                        ),
                child: Text(s.t('previous')),
              ),
              Text(
                s.t('range', {
                  'from': page.offset + 1,
                  'to': page.offset + page.items.length,
                  'total': page.total,
                }),
                style: TextStyle(color: p.muted),
              ),
              OutlinedButton(
                onPressed: page.offset + page.items.length >= page.total
                    ? null
                    : () => c.loadReports(
                          offset: page.offset +
                              FinanceIntelController.reportPageSize,
                        ),
                child: Text(s.t('next')),
              ),
            ],
          ),
      ];
    }
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: s.t('reports')),
          Text(s.t('reportsHelp'), style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.xs),
          ...body,
        ],
      ),
    );
  }
}

/// One daily report on its own screen. Read only; recorded text is shown as
/// written, never followed.
class DailyReportScreen extends StatefulWidget {
  const DailyReportScreen({
    required this.controller,
    required this.item,
    required this.strings,
    super.key,
  });
  final FinanceIntelController controller;
  final DailyReportItem item;
  final FinanceIntelStrings strings;

  @override
  State<DailyReportScreen> createState() => _DailyReportScreenState();
}

class _DailyReportScreenState extends State<DailyReportScreen> {
  DailyReport? _report;
  String? _error;
  bool _denied = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _report = null;
      _error = null;
      _denied = false;
    });
    try {
      final found = await widget.controller.report(widget.item.id);
      if (mounted) setState(() => _report = found);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error is ApiException ? error.message : '$error';
        _denied = error is ApiException && error.isPermissionDenied;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings, p = context.palette, r = _report;
    final List<Widget> children;
    if (_error != null) {
      children = [
        _denied
            ? AnumFeedback(
                kind: AnumFeedbackKind.permission,
                message: s.t('denied'),
              )
            : AnumFeedback(
                kind: AnumFeedbackKind.error,
                message: s.t('reportError', {'message': _error!}),
                onRetry: _load,
              ),
      ];
    } else if (r == null) {
      children = [
        AnumFeedback(
          kind: AnumFeedbackKind.loading,
          message: s.t('reportLoading'),
        ),
      ];
    } else {
      Widget section(String key, List<ReportLine> lines) =>
          _Section(title: s.t('section.$key'), lines: lines, strings: s);
      children = [
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [AnumPill(label: s.t('simulated'), tone: AnumTone.info)],
        ),
        const SizedBox(height: AnumSpacing.xs),
        Text(r.disclaimer, style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.sm),
        if (r.commentaryStatus != null) ...[
          _Heading(s.t('commentary')),
          if (r.commentaryStatus == 'ok' && r.commentaryText != null) ...[
            Text(r.commentaryText!),
            Text(s.t('commentaryLabel', {'model': r.commentaryModel ?? '-'}),
                style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
          ] else
            Text(
                s.t(r.commentaryStatus == 'withheld'
                    ? 'commentary.withheld'
                    : r.commentaryStatus == 'failed'
                        ? 'commentary.failed'
                        : 'commentary.${const [
                            'no_model',
                            'disabled',
                            'monthly_limit',
                            'model_budget'
                          ].contains(r.commentaryReason) ? r.commentaryReason : 'no_model'}'),
                style: TextStyle(color: p.muted)),
          const SizedBox(height: AnumSpacing.sm),
        ],
        for (final key in reportSummaryKeys)
          AnumRow(
            title: s.t('summary.$key'),
            detail: '${r.item.count(key)}',
            icon: Icons.analytics_outlined,
          ),
        const SizedBox(height: AnumSpacing.sm),
        _Heading(s.t('section.next')),
        for (final step in r.nextSteps) Text('• $step'),
        section('orders', r.orders),
        section('refused', r.refused),
        section('suspended', r.suspended),
        section('data', r.data),
        section('portfolios', r.portfolios),
        section('performance', r.performance),
        section('problems', r.problems),
        const SizedBox(height: AnumSpacing.sm),
        Text(s.t('recordedText'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: r.markdown));
              if (context.mounted) _say(context, s.t('copied'));
            },
            icon: const Icon(Icons.copy),
            label: Text(s.t('copyMarkdown')),
          ),
        ),
      ];
    }
    return Scaffold(
      appBar: AppBar(
        title: Text(s.t('reportTitle', {'date': widget.item.date})),
      ),
      body: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: children,
      ),
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(
          top: AnumSpacing.sm,
          bottom: AnumSpacing.xxs,
        ),
        child: Semantics(
          header: true,
          child: Text(
            text,
            style: const TextStyle(
                fontWeight: FontWeight.w700, fontSize: AnumType.lg),
          ),
        ),
      );
}

class _Section extends StatelessWidget {
  const _Section({
    required this.title,
    required this.lines,
    required this.strings,
  });
  final String title;
  final List<ReportLine> lines;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, p = context.palette;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _Heading('$title (${lines.length})'),
        if (lines.isEmpty)
          Text(s.t('noneHere'), style: TextStyle(color: p.muted))
        else
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    line.title,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  for (final f in line.fields)
                    Text.rich(
                      TextSpan(
                        children: [
                          TextSpan(
                            text: '${s.t('field.${f.key}')}: ',
                            style: TextStyle(color: p.muted),
                          ),
                          TextSpan(
                            text: f.key == 'agent'
                                ? s.label('agent', f.value)
                                : f.key == 'outcome' || f.key == 'status'
                                    ? s.label('outcome', f.value)
                                    : f.value,
                          ),
                        ],
                      ),
                    ),
                  for (final reason in line.reasons)
                    Text('• ${reason.replaceAll('_', ' ')}'),
                ],
              ),
            ),
      ],
    );
  }
}

// -- market data: instruments and their daily bars -----------------------------

/// The system file picker for a bars file (CSV, at most 2 MB, read as text).
Future<BarsFile?> pickBarsFile() async {
  final file = await FilePicker.pickFile(
    type: FileType.custom,
    allowedExtensions: const ['csv', 'txt'],
  );
  if (file == null) return null;
  final size = await pickedFileSize(file);
  if (size == 0) {
    return (name: file.name, text: null, problem: 'fileEmpty');
  }
  if (size != null && size > maxBarsFileBytes) {
    return (name: file.name, text: null, problem: 'fileTooBig');
  }
  final Uint8List bytes;
  try {
    bytes = await file.readAsBytes();
  } on Object {
    return (name: file.name, text: null, problem: 'fileEmpty');
  }
  // An unknown size is judged on what was read, never assumed empty.
  if (bytes.isEmpty) {
    return (name: file.name, text: null, problem: 'fileEmpty');
  }
  if (bytes.length > maxBarsFileBytes) {
    return (name: file.name, text: null, problem: 'fileTooBig');
  }
  try {
    return (name: file.name, text: utf8.decode(bytes), problem: null);
  } on FormatException {
    return (name: file.name, text: null, problem: 'fileNotText');
  }
}

class IntelMarketData extends StatelessWidget {
  const IntelMarketData(
      {required this.controller,
      required this.overview,
      required this.strings,
      super.key});
  final FinanceIntelController controller;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings, c = controller, p = context.palette;
    final List<Widget> body;
    if (c.setupLoad == FinanceLoad.denied) {
      body = [
        AnumFeedback(
            kind: AnumFeedbackKind.permission, message: s.t('actionsDenied'))
      ];
    } else if (c.setupLoad == FinanceLoad.error) {
      body = [
        AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('newError', {'message': c.setupError ?? ''}),
            onRetry: c.loadSetup)
      ];
    } else if (c.setupLoad != FinanceLoad.ready) {
      body = [
        AnumFeedback(kind: AnumFeedbackKind.loading, message: s.t('newLoading'))
      ];
    } else {
      body = [
        if (c.allInstruments.isEmpty)
          AnumEmpty(
              icon: Icons.candlestick_chart_outlined,
              title: s.t('noInstruments'),
              message: s.t('noInstrumentsText'))
        else
          for (final i in c.allInstruments) ...[
            AnumRow(
              title: i.symbol,
              subtitle: [
                '${i.venue} · ${i.currency} · ${s.label('asset', i.assetClass)}',
                i.bars == 0
                    ? s.t('noBarsYet')
                    : s.t('barsRange', {
                        'count': i.bars,
                        'first': i.firstBar ?? '-',
                        'last': i.lastBar ?? '-'
                      }),
                if (i.lastSource != null)
                  s.t('barsSource', {'source': i.lastSource!}),
              ].join('\n'),
              icon: Icons.candlestick_chart_outlined,
            ),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: c.acting ? null : () => _import(context, i),
                icon: const Icon(Icons.upload_file),
                label: Text(s.t('importBarsFor', {'symbol': i.symbol})),
              ),
            ),
          ],
        const SizedBox(height: AnumSpacing.xs),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            onPressed: c.acting ? null : () => _add(context),
            icon: const Icon(Icons.add),
            label: Text(s.t('addInstrument')),
          ),
        ),
      ];
    }
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(title: s.t('marketData')),
        Text(s.t('marketDataHelp'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.xs),
        ...body,
      ]),
    );
  }

  Future<void> _add(BuildContext context) async {
    final done = await Navigator.of(context).push<bool>(MaterialPageRoute(
        builder: (_) => AddInstrumentScreen(
            controller: controller,
            venues: [for (final v in overview.venues) v],
            strings: strings)));
    if (done == true && context.mounted) {
      _say(context, strings.t('instrumentAdded'));
    }
  }

  Future<void> _import(BuildContext context, IntelInstrument instrument) async {
    final s = strings;
    final file = await (controller.pickBars ?? pickBarsFile)();
    if (file == null || !context.mounted) return;
    if (file.problem != null) {
      _say(context, s.t(file.problem!));
      return;
    }
    final ok = await _confirm(context,
        title: s.t('importConfirmTitle', {'symbol': instrument.symbol}),
        text: s.t('importConfirmText',
            {'file': file.name, 'symbol': instrument.symbol}),
        action: s.t('importBars'),
        strings: s);
    if (!ok || !context.mounted) return;
    final (error, done) = await controller.importBars(instrument, file);
    if (!context.mounted) return;
    if (error != null || done == null) {
      _say(context, s.t('importFailed', {'message': error ?? ''}));
      return;
    }
    _say(
        context,
        [
          s.t('imported', {'count': done.imported, 'changed': done.changed}),
          if (done.issues.isNotEmpty)
            s.t('importIssues', {'count': done.issues.length}),
        ].join(' '));
  }
}

/// Adding an instrument on its own screen.
class AddInstrumentScreen extends StatefulWidget {
  const AddInstrumentScreen(
      {required this.controller,
      required this.venues,
      required this.strings,
      super.key});
  final FinanceIntelController controller;
  final List<IntelVenue> venues;
  final FinanceIntelStrings strings;

  @override
  State<AddInstrumentScreen> createState() => _AddInstrumentScreenState();
}

class _AddInstrumentScreenState extends State<AddInstrumentScreen> {
  final _symbol = TextEditingController();
  final _currency = TextEditingController();
  final _listed = TextEditingController();
  String _asset = 'etf';
  String? _venue;
  String? _error;

  @override
  void dispose() {
    _symbol.dispose();
    _currency.dispose();
    _listed.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final s = widget.strings;
    final symbol = _symbol.text.trim(), currency = _currency.text.trim();
    final listed = _listed.text.trim();
    String? problem;
    if (symbol.isEmpty || symbol.length > 20) {
      problem = s.t('symbolRequired');
    } else if (_venue == null) {
      problem = s.t('venueRequired');
    } else if (!RegExp(r'^[A-Za-z]{3,10}$').hasMatch(currency)) {
      problem = s.t('currencyRequired');
    } else if (listed.isNotEmpty &&
        !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(listed)) {
      problem = s.t('dateFormat');
    }
    setState(() => _error = problem);
    if (problem != null) return;
    final ok = await _confirm(context,
        title: s.t('addInstrumentConfirm', {'symbol': symbol.toUpperCase()}),
        text: s.t('addInstrumentText'),
        action: s.t('addInstrument'),
        strings: s);
    if (!ok || !mounted) return;
    final error = await widget.controller.createInstrument(
        symbol: symbol,
        assetClass: _asset,
        venue: _venue!,
        currency: currency,
        listedOn: listed.isEmpty ? null : listed);
    if (!mounted) return;
    if (error != null) {
      setState(() => _error = error);
      return;
    }
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings, c = widget.controller, p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('addInstrument'))),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Text(s.t('addInstrumentText'), style: TextStyle(color: p.muted)),
            const SizedBox(height: AnumSpacing.md),
            TextField(
              controller: _symbol,
              maxLength: 20,
              textCapitalization: TextCapitalization.characters,
              decoration: InputDecoration(labelText: s.t('symbol')),
            ),
            DropdownButtonFormField<String>(
              initialValue: _asset,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('assetClass')),
              items: [
                for (final a in intelAssetClasses)
                  DropdownMenuItem(value: a, child: Text(s.label('asset', a))),
              ],
              onChanged: (v) => setState(() => _asset = v ?? _asset),
            ),
            const SizedBox(height: AnumSpacing.sm),
            DropdownButtonFormField<String>(
              initialValue: _venue,
              isExpanded: true,
              decoration: InputDecoration(labelText: s.t('venue')),
              hint: Text(s.t('chooseVenue')),
              items: [
                for (final v in widget.venues)
                  DropdownMenuItem(
                      value: v.code,
                      child: Text(v.name, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (v) => setState(() {
                _venue = v;
                _error = null;
              }),
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              controller: _currency,
              maxLength: 10,
              textCapitalization: TextCapitalization.characters,
              decoration: InputDecoration(labelText: s.t('currency')),
            ),
            TextField(
              controller: _listed,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(
                  labelText: s.t('listedOn'), helperText: s.t('dateHint')),
            ),
            if (_error != null)
              Semantics(
                liveRegion: true,
                child: Text(_error!, style: TextStyle(color: p.stop)),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton(
                      onPressed: c.acting ? null : _submit,
                      child: Text(s.t('addInstrument'))),
                  OutlinedButton(
                      onPressed: c.acting
                          ? null
                          : () => Navigator.of(context).pop(false),
                      child: Text(s.t('cancel'))),
                ]),
          ],
        ),
      ),
    );
  }
}

// -- one strategy: evaluations and its paper portfolio --------------------------

/// "Evaluate": a SIMULATED backtest or walk-forward run, after a confirmation.
class IntelEvaluateButton extends StatelessWidget {
  const IntelEvaluateButton(
      {required this.controller,
      required this.strategy,
      required this.strings,
      super.key});
  final FinanceIntelController controller;
  final IntelStrategy strategy;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    if (strategy.state == 'RETIRED' ||
        controller.setupLoad == FinanceLoad.denied) {
      return const SizedBox.shrink();
    }
    return TextButton.icon(
      onPressed: controller.acting ? null : () => _run(context),
      icon: const Icon(Icons.science_outlined),
      label: Text(strings.t('evaluate')),
    );
  }

  Future<void> _run(BuildContext context) async {
    final s = strings;
    final kind = await showDialog<String>(
      context: context,
      builder: (context) => SimpleDialog(
        title: Text(s.t('evaluateTitle',
            {'name': strategy.name, 'version': strategy.version})),
        children: [
          for (final k in ['backtest', 'walk_forward'])
            SimpleDialogOption(
              onPressed: () => Navigator.of(context).pop(k),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
                child: Text(s.label('kind', k)),
              ),
            ),
        ],
      ),
    );
    if (kind == null || !context.mounted) return;
    final ok = await _confirm(context,
        title: s.t('evaluateConfirm', {'kind': s.label('kind', kind)}),
        text: s.t('evaluateText'),
        action: s.t('evaluate'),
        strings: s);
    if (!ok || !context.mounted) return;
    final (error, verdict) = await controller.evaluate(strategy, kind: kind);
    if (!context.mounted) return;
    _say(
        context,
        error ??
            s.t('evaluated', {
              'kind': s.label('kind', kind),
              'verdict': s.label('verdict', verdict ?? '')
            }));
  }
}

/// "Paper portfolio": choose which paper portfolio the version will trade in.
class IntelPortfolioButton extends StatelessWidget {
  const IntelPortfolioButton(
      {required this.controller,
      required this.strategy,
      required this.overview,
      required this.strings,
      super.key});
  final FinanceIntelController controller;
  final IntelStrategy strategy;
  final IntelOverview overview;
  final FinanceIntelStrings strings;

  @override
  Widget build(BuildContext context) {
    if (strategy.state == 'PAPER_ACTIVE' ||
        strategy.state == 'RETIRED' ||
        controller.setupLoad == FinanceLoad.denied) {
      return const SizedBox.shrink();
    }
    return TextButton.icon(
      onPressed: controller.acting ? null : () => _choose(context),
      icon: const Icon(Icons.account_balance_wallet_outlined),
      label: Text(strings.t('paperPortfolio')),
    );
  }

  Future<void> _choose(BuildContext context) async {
    final s = strings;
    final paper = [
      for (final p in overview.portfolios)
        if (p.mode == 'paper') p
    ];
    if (paper.isEmpty) {
      _say(context, s.t('noPaperPortfolio'));
      return;
    }
    final picked = await showDialog<IntelPortfolio>(
      context: context,
      builder: (context) => SimpleDialog(
        title: Text(s.t('choosePortfolio',
            {'name': strategy.name, 'version': strategy.version})),
        children: [
          for (final p in paper)
            SimpleDialogOption(
              onPressed: () => Navigator.of(context).pop(p),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
                child: Text(p.id == strategy.portfolioId
                    ? s.t('currentPortfolio', {'name': p.name})
                    : p.name),
              ),
            ),
        ],
      ),
    );
    if (picked == null ||
        !context.mounted ||
        picked.id == strategy.portfolioId) {
      return;
    }
    final ok = await _confirm(context,
        title: s.t('portfolioConfirm', {'name': picked.name}),
        text: s.t('portfolioText'),
        action: s.t('usePortfolio'),
        strings: s);
    if (!ok || !context.mounted) return;
    final error = await controller.choosePortfolio(strategy, picked.id);
    if (!context.mounted) return;
    _say(context, error ?? s.t('portfolioChosen', {'name': picked.name}));
  }
}
