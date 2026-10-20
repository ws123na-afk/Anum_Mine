import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'tyre_controller.dart';
import 'tyre_models.dart';
import 'tyre_ops.dart';
import 'tyre_ops_screen.dart';

/// Tyre planning (docs/tyre-planning.md): the fleet dashboard, the tyres due
/// in a month and the open alerts. Connecting and mapping the data is done on
/// the web; this screen says so when there is nothing synced yet.
class TyreScreen extends StatefulWidget {
  const TyreScreen({
    required this.controller,
    this.initialTab = 0,
    this.onOpenApprovals,
    super.key,
  });
  final TyreController controller;
  final VoidCallback? onOpenApprovals;

  /// 0 dashboard, 1 due, 2 alerts (a tapped tyre alert push opens 2).
  final int initialTab;

  @override
  State<TyreScreen> createState() => _TyreScreenState();
}

class _TyreScreenState extends State<TyreScreen> {
  bool _syncing = false;

  Future<void> _sync(TyreOps ops) async {
    final l = AnumLocalizations.maybeOf(context);
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _syncing = true);
    try {
      final raised = await ops.sync();
      await widget.controller.load();
      // The synced data changes the plan: reload it when it is on show.
      if (ops.plan != null) unawaited(ops.loadPlan());
      messenger
        ..clearSnackBars()
        ..showSnackBar(
            SnackBar(content: Text(l.f('tyreOps.synced', {'n': raised}))));
    } on ApiException catch (e) {
      messenger
        ..clearSnackBars()
        ..showSnackBar(SnackBar(content: Text(e.readable)));
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final ops = widget.controller.ops;
    final tabs = ops == null ? 3 : 5;
    return DefaultTabController(
      length: tabs,
      initialIndex: widget.initialTab.clamp(0, tabs - 1),
      child: Scaffold(
        appBar: AppBar(
          title: Text(l.t('tyreTitle')),
          actions: [
            if (ops != null)
              ListenableBuilder(
                listenable: widget.controller,
                builder: (context, _) =>
                    (widget.controller.access?.canOperate ?? false)
                        ? IconButton(
                            key: const Key('tyre-sync'),
                            tooltip: l.t('tyreOps.syncNow'),
                            onPressed: _syncing ? null : () => _sync(ops),
                            icon: const Icon(Icons.sync),
                          )
                        : const SizedBox.shrink(),
              ),
            IconButton(
              tooltip: l.t('refresh'),
              onPressed: widget.controller.load,
              icon: const Icon(Icons.refresh),
            ),
          ],
          bottom: TabBar(
            isScrollable: ops != null,
            tabAlignment: ops != null ? TabAlignment.start : null,
            tabs: [
              Tab(text: l.t('tyreDashboard')),
              Tab(text: l.t('tyreDue')),
              Tab(text: l.t('tyreAlerts')),
              if (ops != null) Tab(text: l.t('tyreOps.tabPlan')),
              if (ops != null) Tab(text: l.t('tyreOps.tabReports')),
            ],
          ),
        ),
        body: ListenableBuilder(
          listenable: widget.controller,
          builder: (context, _) {
            final c = widget.controller;
            if (c.loading && c.access == null) {
              return const Center(child: CircularProgressIndicator());
            }
            if (c.error != null && c.access == null) {
              return _Message(
                icon: c.denied ? Icons.lock_outline : Icons.cloud_off_outlined,
                title: l.t(c.denied ? 'tyreDeniedTitle' : 'tyreErrorTitle'),
                message: c.denied
                    ? l.t('tyreDenied')
                    : l.f('tyreErrorHint', {'message': c.error!}),
                action: c.denied ? null : l.t('retry'),
                onAction: c.load,
              );
            }
            final access = c.access;
            if (access == null || !access.synced) {
              return _Message(
                icon: Icons.tire_repair_outlined,
                title: l.t('tyreEmptyTitle'),
                message: l.t(
                  access?.connected == true
                      ? 'tyreEmptySync'
                      : 'tyreEmptyConnect',
                ),
                action: l.t('retry'),
                onAction: c.load,
              );
            }
            return TabBarView(
              children: [
                _Dashboard(controller: c),
                _Due(controller: c),
                _Alerts(controller: c),
                if (ops != null) TyrePlanView(ops: ops),
                if (ops != null)
                  TyreReportsView(
                    ops: ops,
                    canOperate: access.canOperate,
                    onOpenApprovals: widget.onOpenApprovals,
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.message,
    this.action,
    this.onAction,
  });
  final IconData icon;
  final String title, message;
  final String? action;
  final Future<void> Function()? onAction;
  @override
  Widget build(BuildContext context) => AnumPage(
        children: [
          AnumEmpty(
            icon: icon,
            title: title,
            message: message,
            action: action,
            onAction: onAction == null ? null : () => onAction!(),
          ),
        ],
      );
}

String _mm(double? value) =>
    value == null ? '-' : '${value.toStringAsFixed(1)} mm';

String _day(BuildContext context, DateTime? value) => value == null
    ? '-'
    : MaterialLocalizations.of(context).formatMediumDate(value);

class _Dashboard extends StatelessWidget {
  const _Dashboard({required this.controller});
  final TyreController controller;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final dash = controller.dashboard;
    if (dash == null) return const SizedBox.shrink();
    final s = dash.summary;
    final p = context.palette;
    return AnumPage(
      onRefresh: controller.load,
      children: [
        AnumHeader(
          title: l.t('tyreDashboard'),
          subtitle: l.f('tyreSynced', {
            'when': MaterialLocalizations.of(context)
                .formatMediumDate(dash.syncedAt.toLocal()),
          }),
        ),
        AnumMetricGrid(
          children: [
            AnumMetric(
              label: l.t('tyreFitted'),
              value: '${s.tyresFitted}',
              caption: l.f('tyreVehicles', {'n': s.vehicles}),
              icon: Icons.local_shipping_outlined,
            ),
            AnumMetric(
              label: l.t('tyreAvgTread'),
              value: _mm(s.averageTreadMm),
              icon: Icons.straighten,
            ),
            AnumMetric(
              label: l.f('tyreBelowMin', {
                'mm': s.minTreadMm.toStringAsFixed(1),
              }),
              value: '${s.belowMinimum}',
              caption: l.f('tyreWarningBand', {'n': s.inWarningBand}),
              icon: Icons.warning_amber_outlined,
              accent: s.belowMinimum > 0 ? p.stop : null,
            ),
            AnumMetric(
              label: l.t('tyrePressure'),
              value: s.pressureCompliantPct == null
                  ? '-'
                  : '${s.pressureCompliantPct!.toStringAsFixed(1)}%',
              icon: Icons.speed,
            ),
            AnumMetric(
              label: l.t('tyreDue30'),
              value: '${s.dueNext30Days}',
              caption: l.f('tyreDue90', {'n': s.dueNext90Days}),
              icon: Icons.event_outlined,
              onTap: () => DefaultTabController.of(context).animateTo(1),
            ),
            AnumMetric(
              label: l.t('tyreOpenAlerts'),
              value: '${dash.openAlerts}',
              caption: l.f('tyreCritical', {'n': dash.criticalAlerts}),
              icon: Icons.notifications_outlined,
              accent: dash.criticalAlerts > 0 ? p.stop : null,
              onTap: () => DefaultTabController.of(context).animateTo(2),
            ),
            AnumMetric(
              label: l.t('tyreOverdue'),
              value: '${s.overdueInspections}',
              icon: Icons.fact_check_outlined,
              accent: s.overdueInspections > 0 ? p.warn : null,
            ),
            AnumMetric(
              label: l.f('tyreCost', {'currency': s.currency}),
              value: formatMoney(s.costPer1000Km, null,
                  locale: Localizations.localeOf(context)),
              icon: Icons.payments_outlined,
            ),
          ],
        ),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AnumHeader(title: l.t('tyreDueSoon')),
              if (dash.dueSoon.isEmpty)
                Text(l.t('tyreNoneDue'))
              else
                for (final item in dash.dueSoon) _ForecastRow(item: item),
            ],
          ),
        ),
      ],
    );
  }
}

class _ForecastRow extends StatelessWidget {
  const _ForecastRow({required this.item});
  final TyreForecast item;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final tone = item.due
        ? AnumTone.stop
        : item.confidence == 'low'
            ? AnumTone.neutral
            : AnumTone.warn;
    final when =
        item.due ? l.t('tyreDueNow') : _day(context, item.projectedRemoval);
    // Narrow screens or large text: the date joins the detail line instead of
    // a pill beside the title, so nothing is squeezed.
    final roomy = MediaQuery.sizeOf(context).width >= 600 &&
        MediaQuery.textScalerOf(context).scale(14) <= 21;
    final detail = l.f('tyreTreadWear', {
      'tread': _mm(item.treadMm),
      'wear': item.wearPer30Days?.toStringAsFixed(2) ?? '-',
    });
    return AnumRow(
      icon: Icons.tire_repair_outlined,
      tone: tone,
      title: item.label,
      subtitle: [
        item.where,
        if (item.size != null) item.size!,
      ].where((part) => part.isNotEmpty).join(' · '),
      detail: roomy ? detail : '$when · $detail',
      trailing: roomy ? AnumPill(label: when, tone: tone) : null,
    );
  }
}

class _Due extends StatefulWidget {
  const _Due({required this.controller});
  final TyreController controller;
  @override
  State<_Due> createState() => _DueState();
}

class _DueState extends State<_Due> {
  final _search = TextEditingController();
  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    final due = c.due;
    final monthLabel =
        MaterialLocalizations.of(context).formatMonthYear(c.month);
    return AnumPage(
      onRefresh: c.loadDue,
      children: [
        Row(
          children: [
            IconButton(
              tooltip: l.t('tyrePreviousMonth'),
              onPressed: () => c.shiftMonth(-1),
              icon: const Icon(Icons.chevron_left),
            ),
            Expanded(
              child: Text(
                l.f('tyreDueIn', {'month': monthLabel}),
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            IconButton(
              tooltip: l.t('tyreNextMonth'),
              onPressed: () => c.shiftMonth(1),
              icon: const Icon(Icons.chevron_right),
            ),
          ],
        ),
        TextField(
          controller: _search,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            prefixIcon: const Icon(Icons.search),
            labelText: l.t('tyreSearch'),
          ),
          onSubmitted: c.search,
        ),
        if (due.error != null)
          AnumEmpty(
            icon: Icons.cloud_off_outlined,
            title: l.t('tyreErrorTitle'),
            message: l.f('tyreErrorHint', {'message': due.error!}),
            action: l.t('retry'),
            onAction: c.loadDue,
          )
        else if (due.loading && due.items.isEmpty)
          const Center(child: CircularProgressIndicator())
        else if (due.items.isEmpty)
          AnumEmpty(
            icon: Icons.check_circle_outline,
            title: l.t('tyreNoneDueTitle'),
            message: l.f('tyreNoneDueMonth', {'month': monthLabel}),
          )
        else
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(l.f('tyreDueCount', {'n': due.total})),
                for (final item in due.items) _ForecastRow(item: item),
                if (due.hasMore)
                  TextButton(
                    onPressed: due.loading ? null : () => c.loadDue(more: true),
                    child: Text(l.t('tyreMore')),
                  ),
              ],
            ),
          ),
      ],
    );
  }
}

class _Alerts extends StatelessWidget {
  const _Alerts({required this.controller});
  final TyreController controller;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    final section = c.alerts;
    final canAct = c.access?.canOperate ?? false;
    return AnumPage(
      onRefresh: c.loadAlerts,
      children: [
        AnumHeader(
          title: l.t('tyreAlerts'),
          subtitle: canAct ? null : l.t('tyreViewerNote'),
        ),
        if (c.notice != null)
          Text(c.notice!, style: TextStyle(color: context.palette.stop)),
        if (section.error != null)
          AnumEmpty(
            icon: Icons.cloud_off_outlined,
            title: l.t('tyreErrorTitle'),
            message: l.f('tyreErrorHint', {'message': section.error!}),
            action: l.t('retry'),
            onAction: c.loadAlerts,
          )
        else if (section.loading && section.items.isEmpty)
          const Center(child: CircularProgressIndicator())
        else if (section.items.isEmpty)
          AnumEmpty(
            icon: Icons.verified_outlined,
            title: l.t('tyreNoAlertsTitle'),
            message: l.t('tyreNoAlerts'),
          )
        else ...[
          for (final alert in section.items)
            AnumSurface(
              accent: alert.severity == 'critical'
                  ? context.palette.stop
                  : alert.severity == 'warning'
                      ? context.palette.warn
                      : null,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    spacing: 8,
                    runSpacing: 4,
                    children: [
                      AnumPill(
                        label: l.t('tyreSeverity_${alert.severity}'),
                        tone: alert.severity == 'critical'
                            ? AnumTone.stop
                            : alert.severity == 'warning'
                                ? AnumTone.warn
                                : AnumTone.neutral,
                      ),
                      AnumPill(label: l.t('tyreKind_${alert.kind}')),
                      if (alert.status == 'acknowledged')
                        AnumPill(
                          label: l.t('tyreAcknowledged'),
                          tone: AnumTone.ok,
                        ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    alert.subject,
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 4),
                  Text(alert.message),
                  if (canAct && alert.status == 'open')
                    Align(
                      alignment: AlignmentDirectional.centerEnd,
                      child: TextButton(
                        onPressed: c.acting == alert.id
                            ? null
                            : () => c.acknowledge(alert),
                        child: Text(l.t('tyreAcknowledge')),
                      ),
                    ),
                ],
              ),
            ),
          if (section.hasMore)
            TextButton(
              onPressed:
                  section.loading ? null : () => c.loadAlerts(more: true),
              child: Text(l.t('tyreMore')),
            ),
        ],
      ],
    );
  }
}
