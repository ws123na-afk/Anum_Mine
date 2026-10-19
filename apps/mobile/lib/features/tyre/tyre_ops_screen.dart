import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'tyre_ops.dart';

/// Purchase plan, reports and report schedules (docs/tyre-planning.md).

void _say(BuildContext context, String text) {
  final messenger = ScaffoldMessenger.of(context);
  messenger.clearSnackBars();
  messenger.showSnackBar(SnackBar(content: Text(text)));
}

Future<bool> _confirm(
  BuildContext context, {
  required String title,
  required String message,
  required String action,
}) async {
  final l = AnumLocalizations.maybeOf(context);
  return await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(message),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(l.t('tyreOps.cancel')),
            ),
            FilledButton(
              key: const Key('tyre-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(action),
            ),
          ],
        ),
      ) ??
      false;
}

Widget _state(
    BuildContext context, OpsLoad state, String? error, VoidCallback retry) {
  final l = AnumLocalizations.maybeOf(context);
  return switch (state) {
    OpsLoad.denied => AnumFeedback(
        kind: AnumFeedbackKind.permission, message: l.t('tyreDenied')),
    OpsLoad.error => AnumFeedback(
        kind: AnumFeedbackKind.error,
        message: l.f('tyreErrorHint', {'message': error ?? ''}),
        onRetry: retry),
    _ => AnumFeedback(
        kind: AnumFeedbackKind.loading, message: l.t('tyreOps.loading')),
  };
}

class TyrePlanView extends StatefulWidget {
  const TyrePlanView({required this.ops, super.key});
  final TyreOps ops;

  @override
  State<TyrePlanView> createState() => _TyrePlanViewState();
}

class _TyrePlanViewState extends State<TyrePlanView> {
  static const _chunk = 25;
  int shown = _chunk;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      // Always reload: the data belongs to the workspace now open.
      if (mounted) widget.ops.loadPlan();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return ListenableBuilder(
      listenable: widget.ops,
      builder: (context, _) {
        final ops = widget.ops;
        final plan = ops.plan;
        return AnumPage(
          onRefresh: ops.loadPlan,
          children: [
            AnumHeader(
                title: l.t('tyreOps.plan'), subtitle: l.t('tyreOps.planHint')),
            Wrap(spacing: AnumSpacing.xs, children: [
              for (final m in TyreOps.planMonths)
                ChoiceChip(
                  key: Key('tyre-plan-$m'),
                  label: Text(l.f('tyreOps.nMonths', {'n': m})),
                  selected: ops.months == m,
                  onSelected: (_) {
                    setState(() => shown = _chunk);
                    ops.setMonths(m);
                  },
                ),
            ]),
            if (plan == null || ops.planState != OpsLoad.ready)
              _state(context, ops.planState, ops.planError, ops.loadPlan)
            else if (plan.rows.isEmpty)
              AnumEmpty(
                title: l.t('tyreOps.planEmpty'),
                message: l.t('tyreOps.planEmptyText'),
              )
            else ...[
              AnumMetricGrid(children: [
                AnumMetric(
                  label: l.t('tyreOps.toBuy'),
                  value: '${plan.totalTyres}',
                  icon: Icons.shopping_cart_outlined,
                ),
                AnumMetric(
                  label: l.t('tyreOps.estCost'),
                  value: plan.totalCost == null
                      ? '-'
                      : formatMoney(plan.totalCost!, plan.currency),
                  icon: Icons.payments_outlined,
                  caption: plan.unpricedRows > 0
                      ? l.f('tyreOps.unpriced', {'n': plan.unpricedRows})
                      : null,
                ),
              ]),
              AnumSurface(
                child: Column(children: [
                  for (final row in plan.rows.take(shown))
                    AnumRow(
                      title: '${row.size} ${row.brand}'.trim(),
                      subtitle: l.f('tyreOps.planRow', {
                        'due': row.due,
                        'safety': row.safety,
                        'stock': row.fromStock,
                      }),
                      detail: row.estimatedCost == null
                          ? l.t('tyreOps.noPrice')
                          : formatMoney(row.estimatedCost!, plan.currency),
                      icon: Icons.tire_repair_outlined,
                      trailing: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(row.month),
                          Text(l.f('tyreOps.buyN', {'n': row.toBuy}),
                              style: Theme.of(context).textTheme.labelLarge),
                        ],
                      ),
                    ),
                ]),
              ),
              if (plan.rows.length > shown)
                OutlinedButton(
                  key: const Key('tyre-plan-more'),
                  onPressed: () => setState(() => shown += _chunk),
                  child: Text(l.f('tyreOps.more',
                      {'shown': shown, 'total': plan.rows.length})),
                ),
            ],
          ],
        );
      },
    );
  }
}

class TyreReportsView extends StatefulWidget {
  const TyreReportsView({
    required this.ops,
    required this.canOperate,
    this.onOpenApprovals,
    super.key,
  });
  final TyreOps ops;
  final bool canOperate;
  final VoidCallback? onOpenApprovals;

  @override
  State<TyreReportsView> createState() => _TyreReportsViewState();
}

class _TyreReportsViewState extends State<TyreReportsView> {
  TyreOps get ops => widget.ops;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      ops.loadReports();
      ops.loadSchedules();
    });
  }

  Future<void> _generate() async {
    final made = await Navigator.of(context).push<TyreReportItem>(
      MaterialPageRoute(builder: (_) => ReportFormScreen(ops: ops)),
    );
    if (made != null && mounted) await _open(made);
  }

  Future<void> _open(TyreReportItem item) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => TyreReportScreen(
            ops: ops,
            report: item,
            canOperate: widget.canOperate,
            onOpenApprovals: widget.onOpenApprovals,
          ),
        ),
      );

  Future<void> _addSchedule() => Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => ScheduleFormScreen(ops: ops)),
      );

  Future<void> _deleteSchedule(ReportSchedule s) async {
    final l = AnumLocalizations.maybeOf(context);
    final ok = await _confirm(
      context,
      title: l.f('tyreOps.deleteScheduleTitle', {'name': s.title}),
      message: l.t('tyreOps.deleteScheduleText'),
      action: l.t('tyreOps.delete'),
    );
    if (!ok) return;
    try {
      await ops.deleteSchedule(s.id);
    } on ApiException catch (e) {
      if (mounted) _say(context, e.readable);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final local = MaterialLocalizations.of(context);
    return ListenableBuilder(
      listenable: ops,
      builder: (context, _) => AnumPage(
        onRefresh: () async {
          await ops.loadReports();
          await ops.loadSchedules();
        },
        children: [
          AnumHeader(
            title: l.t('tyreOps.reports'),
            subtitle: widget.canOperate ? null : l.t('tyreViewerNote'),
          ),
          if (widget.canOperate)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: FilledButton.icon(
                key: const Key('tyre-report-new'),
                onPressed: _generate,
                icon: const Icon(Icons.add_chart),
                label: Text(l.t('tyreOps.generate')),
              ),
            ),
          if (ops.reportsState != OpsLoad.ready)
            _state(context, ops.reportsState, ops.reportsError, ops.loadReports)
          else if (ops.reports.isEmpty)
            AnumEmpty(
              title: l.t('tyreOps.noReports'),
              message: widget.canOperate
                  ? l.t('tyreOps.noReportsText')
                  : l.t('tyreOps.noReportsViewer'),
            )
          else ...[
            for (final r in ops.reports)
              AnumSurface(
                child: AnumRow(
                  key: Key('tyre-report-${r.id}'),
                  title: r.title,
                  subtitle: [
                    local.formatMediumDate(r.createdAt.toLocal()),
                    if (r.source == 'schedule') l.t('tyreOps.scheduled'),
                  ].join(' · '),
                  detail: r.highlights.isEmpty
                      ? null
                      : '${r.highlights.first.label}: ${r.highlights.first.value}',
                  icon: Icons.description_outlined,
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => _open(r),
                ),
              ),
            if (ops.hasMoreReports)
              OutlinedButton(
                key: const Key('tyre-reports-more'),
                onPressed: ops.loadingMore ? null : ops.moreReports,
                child: Text(l.f('tyreOps.more',
                    {'shown': ops.reports.length, 'total': ops.reportsTotal})),
              ),
          ],
          AnumHeader(
            title: l.t('tyreOps.schedules'),
            subtitle: l.t('tyreOps.schedulesHint'),
          ),
          if (widget.canOperate)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: const Key('tyre-schedule-new'),
                onPressed: _addSchedule,
                icon: const Icon(Icons.schedule),
                label: Text(l.t('tyreOps.addSchedule')),
              ),
            ),
          if (ops.schedulesState != OpsLoad.ready)
            _state(context, ops.schedulesState, ops.schedulesError,
                ops.loadSchedules)
          else if (ops.schedules.isEmpty)
            Text(l.t('tyreOps.noSchedules'),
                style: TextStyle(color: context.palette.muted))
          else
            for (final s in ops.schedules)
              AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AnumRow(
                      title: s.title,
                      subtitle: s.description,
                      detail: s.nextRunAt == null
                          ? null
                          : l.f('tyreOps.nextRun', {
                              'when':
                                  local.formatMediumDate(s.nextRunAt!.toLocal())
                            }),
                      icon: Icons.event_repeat,
                    ),
                    if (widget.canOperate)
                      Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: TextButton(
                          key: Key('tyre-schedule-delete-${s.id}'),
                          onPressed: () => _deleteSchedule(s),
                          child: Text(l.t('tyreOps.delete')),
                        ),
                      ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}

class TyreReportScreen extends StatefulWidget {
  const TyreReportScreen({
    required this.ops,
    required this.report,
    required this.canOperate,
    this.onOpenApprovals,
    super.key,
  });
  final TyreOps ops;
  final TyreReportItem report;
  final bool canOperate;
  final VoidCallback? onOpenApprovals;

  @override
  State<TyreReportScreen> createState() => _TyreReportScreenState();
}

class _TyreReportScreenState extends State<TyreReportScreen> {
  late TyreReportItem report = widget.report;
  bool loading = true, busy = false;
  String? error, actionError, delivered;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final full = await widget.ops.repository.report(report.id);
      if (mounted) setState(() => report = full);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _delete() async {
    final l = AnumLocalizations.maybeOf(context);
    final ok = await _confirm(
      context,
      title: l.f('tyreOps.deleteReportTitle', {'name': report.title}),
      message: l.t('tyreOps.deleteReportText'),
      action: l.t('tyreOps.delete'),
    );
    if (!ok) return;
    setState(() {
      busy = true;
      actionError = null;
    });
    try {
      await widget.ops.deleteReport(report.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          actionError = e.readable;
          busy = false;
        });
      }
    }
  }

  Future<void> _deliver() async {
    final result = await showDialog<DeliveryResult>(
      context: context,
      builder: (_) => _DeliverDialog(ops: widget.ops, reportId: report.id),
    );
    if (result != null && mounted) {
      setState(() => delivered = result.message);
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final local = MaterialLocalizations.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(report.title)),
      body: AnumPage(
        onRefresh: _load,
        children: [
          if (actionError != null)
            Semantics(
              liveRegion: true,
              child: Text(actionError!,
                  key: const Key('tyre-report-error'),
                  style: TextStyle(color: context.palette.stop)),
            ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AnumHeader(
                  eyebrow: l.t('tyreOps.kind.${report.kind}'),
                  title: report.title,
                  subtitle: local.formatMediumDate(report.createdAt.toLocal()),
                ),
                if (report.periodFrom != null && report.periodTo != null)
                  Text(
                      l.f('tyreOps.period',
                          {'from': report.periodFrom!, 'to': report.periodTo!}),
                      style: muted),
                if (widget.canOperate) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Wrap(spacing: AnumSpacing.xs, children: [
                    OutlinedButton.icon(
                      key: const Key('tyre-report-deliver'),
                      onPressed: busy ? null : _deliver,
                      icon: const Icon(Icons.send_outlined),
                      label: Text(l.t('tyreOps.deliver')),
                    ),
                    OutlinedButton.icon(
                      key: const Key('tyre-report-delete'),
                      onPressed: busy ? null : _delete,
                      icon: const Icon(Icons.delete_outline),
                      label: Text(l.t('tyreOps.delete')),
                    ),
                  ]),
                ],
                if (delivered != null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  Text(delivered!, key: const Key('tyre-delivered')),
                  if (widget.onOpenApprovals != null)
                    TextButton(
                      onPressed: widget.onOpenApprovals,
                      child: Text(l.t('tyreOps.openApprovals')),
                    ),
                ],
              ],
            ),
          ),
          if (loading && report.tables.isEmpty && report.highlights.isEmpty)
            AnumFeedback(
                kind: AnumFeedbackKind.loading,
                message: l.t('tyreOps.loading')),
          if (error != null)
            AnumFeedback(
                kind: AnumFeedbackKind.error,
                message: l.f('tyreErrorHint', {'message': error!}),
                onRetry: _load),
          if (report.highlights.isNotEmpty)
            AnumSurface(
              child: Column(children: [
                for (final h in report.highlights)
                  AnumRow(title: h.label, detail: h.value),
              ]),
            ),
          for (final t in report.tables) _TableCard(table: t),
          for (final n in report.notes) Text(n, style: muted),
          if (report.deliveries.isNotEmpty) ...[
            AnumHeader(title: l.t('tyreOps.deliveries')),
            AnumSurface(
              child: Column(children: [
                for (final d in report.deliveries)
                  AnumRow(
                    title: '${d['channel']} · ${d['to']}',
                    subtitle: _deliveryLabel(l, '${d['status']}'),
                    icon: Icons.mark_email_read_outlined,
                  ),
              ]),
            ),
          ],
          Text(l.t('tyreOps.exportsOnWeb'), style: muted),
        ],
      ),
    );
  }
}

String _deliveryLabel(AnumLocalizations l, String status) {
  final key = 'tyreOps.delivery.$status';
  final text = l.t(key);
  return text == key ? status.replaceAll('_', ' ') : text;
}

class _TableCard extends StatelessWidget {
  const _TableCard({required this.table});
  final ReportTable table;
  static const _cap = 25;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final rows = table.rows.take(_cap).toList();
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: table.title),
          if (rows.isEmpty) Text(l.t('tyreOps.tableEmpty')),
          for (final row in rows)
            AnumRow(
              title: row.isEmpty ? '' : row.first,
              subtitle: [
                for (var i = 1; i < row.length && i < table.columns.length; i++)
                  '${table.columns[i]}: ${row[i]}',
              ].join(' · '),
            ),
          if (table.rows.length > _cap)
            Text(l.f('tyreOps.tableCapped',
                {'shown': _cap, 'total': table.rows.length})),
        ],
      ),
    );
  }
}

class _DeliverDialog extends StatefulWidget {
  const _DeliverDialog({required this.ops, required this.reportId});
  final TyreOps ops;
  final String reportId;

  @override
  State<_DeliverDialog> createState() => _DeliverDialogState();
}

class _DeliverDialogState extends State<_DeliverDialog> {
  final to = TextEditingController();
  List<String>? channels;
  String? channel, error, loadError;
  String format = 'pdf';
  bool busy = false;

  @override
  void initState() {
    super.initState();
    widget.ops.repository.deliveryChannels().then((found) {
      if (mounted) {
        setState(() {
          channels = found;
          channel = found.isEmpty ? null : found.first;
        });
      }
    }).catchError((Object e) {
      if (mounted) {
        setState(() {
          channels = const [];
          loadError = e is ApiException ? e.readable : '$e';
        });
      }
    });
  }

  @override
  void dispose() {
    to.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final l = AnumLocalizations.maybeOf(context);
    if (to.text.trim().length < 3) {
      setState(() => error = l.t('tyreOps.recipientNeeded'));
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await widget.ops.repository
          .deliver(widget.reportId, channel!, to.text.trim(), format);
      if (mounted) Navigator.pop(context, result);
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          error = e.readable;
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final list = channels;
    return AlertDialog(
      title: Text(l.t('tyreOps.deliver')),
      content: list == null
          ? const SizedBox(
              height: 48, child: Center(child: CircularProgressIndicator()))
          : list.isEmpty
              ? Text(loadError ?? l.t('tyreOps.noChannels'),
                  key: const Key('tyre-no-channels'))
              : SingleChildScrollView(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    DropdownButtonFormField<String>(
                      key: const Key('tyre-deliver-channel'),
                      isExpanded: true,
                      initialValue: channel,
                      decoration:
                          InputDecoration(labelText: l.t('tyreOps.channel')),
                      items: [
                        for (final c in list)
                          DropdownMenuItem(value: c, child: Text(c)),
                      ],
                      onChanged: (v) => setState(() => channel = v),
                    ),
                    TextField(
                      key: const Key('tyre-deliver-to'),
                      controller: to,
                      decoration: InputDecoration(
                          labelText: l.t('tyreOps.recipient'),
                          errorText: error),
                    ),
                    DropdownButtonFormField<String>(
                      key: const Key('tyre-deliver-format'),
                      initialValue: format,
                      decoration:
                          InputDecoration(labelText: l.t('tyreOps.format')),
                      items: const [
                        DropdownMenuItem(value: 'pdf', child: Text('PDF')),
                        DropdownMenuItem(value: 'csv', child: Text('CSV')),
                      ],
                      onChanged: (v) => setState(() => format = v ?? 'pdf'),
                    ),
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Text(l.t('tyreOps.deliverNote'),
                          style: TextStyle(color: context.palette.muted)),
                    ),
                  ]),
                ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('tyreOps.cancel')),
        ),
        if (list != null && list.isNotEmpty)
          FilledButton(
            key: const Key('tyre-deliver-send'),
            onPressed: busy ? null : _send,
            child: Text(l.t('tyreOps.send')),
          ),
      ],
    );
  }
}

class ReportFormScreen extends StatefulWidget {
  const ReportFormScreen({required this.ops, super.key});
  final TyreOps ops;

  @override
  State<ReportFormScreen> createState() => _ReportFormScreenState();
}

class _ReportFormScreenState extends State<ReportFormScreen> {
  final form = GlobalKey<FormState>();
  final from = TextEditingController();
  final to = TextEditingController();
  String kind = reportKinds.first;
  bool busy = false;
  String? error;

  @override
  void dispose() {
    from.dispose();
    to.dispose();
    super.dispose();
  }

  String? _date(String? v) {
    final l = AnumLocalizations.maybeOf(context);
    final t = (v ?? '').trim();
    if (t.isEmpty) return null;
    return RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(t) &&
            DateTime.tryParse(t) != null
        ? null
        : l.t('tyreOps.dateBad');
  }

  Future<void> save() async {
    final l = AnumLocalizations.maybeOf(context);
    if (!(form.currentState?.validate() ?? false)) return;
    final f = from.text.trim(), t = to.text.trim();
    if ((f.isEmpty) != (t.isEmpty) || (f.isNotEmpty && f.compareTo(t) > 0)) {
      setState(() => error = l.t('tyreOps.periodBad'));
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final made = await widget.ops
          .generate(kind, f.isEmpty ? null : f, t.isEmpty ? null : t);
      if (mounted) Navigator.of(context).pop(made);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('tyreOps.generate'))),
      body: Form(
        key: form,
        child: AnumPage(children: [
          if (error != null)
            Semantics(
              liveRegion: true,
              child: Text(error!,
                  key: const Key('tyre-form-error'),
                  style: TextStyle(color: context.palette.stop)),
            ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DropdownButtonFormField<String>(
                  key: const Key('tyre-report-kind'),
                  isExpanded: true,
                  initialValue: kind,
                  decoration: InputDecoration(labelText: l.t('tyreOps.type')),
                  items: [
                    for (final k in reportKinds)
                      DropdownMenuItem(
                          value: k, child: Text(l.t('tyreOps.kind.$k'))),
                  ],
                  onChanged: (v) => setState(() => kind = v ?? kind),
                ),
                TextFormField(
                  key: const Key('tyre-report-from'),
                  controller: from,
                  keyboardType: TextInputType.datetime,
                  decoration: InputDecoration(
                      labelText: l.t('tyreOps.from'),
                      helperText: l.t('tyreOps.periodHint'),
                      helperMaxLines: 2),
                  validator: _date,
                ),
                TextFormField(
                  key: const Key('tyre-report-to'),
                  controller: to,
                  keyboardType: TextInputType.datetime,
                  decoration: InputDecoration(labelText: l.t('tyreOps.to')),
                  validator: _date,
                ),
              ],
            ),
          ),
          Text(l.t('tyreOps.frozen'),
              style: TextStyle(color: context.palette.muted)),
          FilledButton(
            key: const Key('tyre-save'),
            onPressed: busy ? null : save,
            child: busy
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l.t('tyreOps.generate')),
          ),
        ]),
      ),
    );
  }
}

class ScheduleFormScreen extends StatefulWidget {
  const ScheduleFormScreen({required this.ops, super.key});
  final TyreOps ops;

  @override
  State<ScheduleFormScreen> createState() => _ScheduleFormScreenState();
}

class _ScheduleFormScreenState extends State<ScheduleFormScreen> {
  final form = GlobalKey<FormState>();
  final time = TextEditingController(text: '07:00');
  String kind = reportKinds.first;
  String cadence = 'weekly';
  int weekday = 0;
  bool busy = false;
  String? error;

  @override
  void dispose() {
    time.dispose();
    super.dispose();
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.ops.createSchedule({
        'kind': kind,
        'cadence': cadence,
        if (cadence == 'weekly') 'weekday': weekday,
        'time': time.text.trim(),
      });
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('tyreOps.addSchedule'))),
      body: Form(
        key: form,
        child: AnumPage(children: [
          if (error != null)
            Semantics(
              liveRegion: true,
              child: Text(error!,
                  key: const Key('tyre-form-error'),
                  style: TextStyle(color: context.palette.stop)),
            ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DropdownButtonFormField<String>(
                  key: const Key('tyre-schedule-kind'),
                  isExpanded: true,
                  initialValue: kind,
                  decoration: InputDecoration(labelText: l.t('tyreOps.type')),
                  items: [
                    for (final k in reportKinds)
                      DropdownMenuItem(
                          value: k, child: Text(l.t('tyreOps.kind.$k'))),
                  ],
                  onChanged: (v) => setState(() => kind = v ?? kind),
                ),
                DropdownButtonFormField<String>(
                  key: const Key('tyre-schedule-cadence'),
                  isExpanded: true,
                  initialValue: cadence,
                  decoration:
                      InputDecoration(labelText: l.t('tyreOps.cadence')),
                  items: [
                    for (final c in const ['daily', 'weekly', 'monthly'])
                      DropdownMenuItem(
                          value: c, child: Text(l.t('tyreOps.cadence.$c'))),
                  ],
                  onChanged: (v) => setState(() => cadence = v ?? cadence),
                ),
                if (cadence == 'weekly')
                  DropdownButtonFormField<int>(
                    key: const Key('tyre-schedule-weekday'),
                    isExpanded: true,
                    initialValue: weekday,
                    decoration:
                        InputDecoration(labelText: l.t('tyreOps.weekday')),
                    items: [
                      for (var d = 0; d < 7; d++)
                        DropdownMenuItem(
                            value: d, child: Text(l.t('tyreOps.day.$d'))),
                    ],
                    onChanged: (v) => setState(() => weekday = v ?? 0),
                  ),
                TextFormField(
                  key: const Key('tyre-schedule-time'),
                  controller: time,
                  keyboardType: TextInputType.datetime,
                  decoration: InputDecoration(
                      labelText: l.t('tyreOps.time'),
                      helperText: l.t('tyreOps.timeHint')),
                  validator: (v) => RegExp(r'^([01]\d|2[0-3]):[0-5]\d$')
                          .hasMatch((v ?? '').trim())
                      ? null
                      : l.t('tyreOps.timeBad'),
                ),
              ],
            ),
          ),
          Text(l.t('tyreOps.scheduleNote'),
              style: TextStyle(color: context.palette.muted)),
          FilledButton(
            key: const Key('tyre-save'),
            onPressed: busy ? null : save,
            child: busy
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : Text(l.t('tyreOps.save')),
          ),
        ]),
      ),
    );
  }
}
