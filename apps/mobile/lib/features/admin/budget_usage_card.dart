import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'admin_models.dart';
import 'budget_usage.dart';

/// "Where the spend goes" on Model budgets: group, scope, period and
/// measure, the ranked rows with their bars and share, Load more, and the
/// CSV export (docs/model-gateway.md#spend-breakdown).
class BudgetUsageCard extends StatefulWidget {
  const BudgetUsageCard(
      {required this.controller, this.currentUserId, super.key});
  final BudgetUsageController controller;
  final String? currentUserId;

  @override
  State<BudgetUsageCard> createState() => _BudgetUsageCardState();
}

class _BudgetUsageCardState extends State<BudgetUsageCard> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.breakdown == null) widget.controller.load();
  }

  String _value(UsageRow row, UsageMeasure m) => m == UsageMeasure.cost
      ? formatUsd(row.costUsd)
      : formatTokens(row.tokens);

  Future<void> _pickPeriod(UsagePreset? value) async {
    if (value == null) return;
    final c = widget.controller;
    if (value != UsagePreset.custom) {
      await c.setPreset(value);
      return;
    }
    await _pickDates();
  }

  Future<void> _pickDates() async {
    final c = widget.controller;
    final l = AnumLocalizations.maybeOf(context);
    final now = DateTime.now().toUtc();
    final current = c.range;
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime.utc(2020),
      lastDate: DateTime.utc(now.year, now.month, now.day),
      initialDateRange: DateTimeRange(start: current.from, end: current.to),
      helpText: l.t('ad.use.pickDates'),
    );
    if (picked == null || !mounted) return;
    final range = UsageRange(
        DateTime.utc(picked.start.year, picked.start.month, picked.start.day),
        DateTime.utc(picked.end.year, picked.end.month, picked.end.day));
    final ok = await c.setCustom(range);
    if (!ok && mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(l.t('ad.use.badRange'))));
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final l = AnumLocalizations.maybeOf(context);
        final p = context.palette;
        final range = c.range;
        return AnumSurface(
          key: const Key('budget-usage'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
              eyebrow: l.t('ad.use.eyebrow'),
              title: l.t('ad.use.title'),
              subtitle: l.t('ad.use.intro'),
            ),
            const SizedBox(height: AnumSpacing.md),
            LayoutBuilder(builder: (context, box) {
              final wide = box.maxWidth >= 640;
              final width =
                  wide ? (box.maxWidth - AnumSpacing.sm) / 2 : box.maxWidth;
              Widget sized(Widget child) =>
                  SizedBox(width: width, child: child);
              return Wrap(
                  spacing: AnumSpacing.sm,
                  runSpacing: AnumSpacing.sm,
                  children: [
                    sized(_Choice<UsageGroup>(
                        key: const Key('usage-group'),
                        label: l.t('ad.use.groupBy'),
                        value: c.group,
                        options: {
                          for (final g in UsageGroup.values)
                            g: l.t('ad.use.group.${g.name}')
                        },
                        onChanged: (v) => c.setGroup(v))),
                    sized(_Choice<UsageScope>(
                        key: const Key('usage-scope'),
                        label: l.t('ad.use.scope'),
                        value: c.scope,
                        options: {
                          for (final s in UsageScope.values)
                            s: l.t('ad.use.scope.${s.name}')
                        },
                        onChanged: (v) => c.setScope(v))),
                    sized(_Choice<UsagePreset>(
                        key: const Key('usage-period'),
                        label: l.t('ad.use.range'),
                        value: c.preset,
                        options: {
                          UsagePreset.month: l.t('ad.use.range.month'),
                          UsagePreset.last30: l.t('ad.use.range.30d'),
                          UsagePreset.last3m: l.t('ad.use.range.3m'),
                          UsagePreset.last12m: l.t('ad.use.range.12m'),
                          UsagePreset.custom: l.t('ad.use.range.custom'),
                        },
                        onChanged: _pickPeriod)),
                    sized(_Choice<UsageMeasure>(
                        key: const Key('usage-measure'),
                        label: l.t('ad.use.measure'),
                        value: c.measure,
                        options: {
                          UsageMeasure.cost: l.t('ad.use.measure.cost'),
                          UsageMeasure.tokens: l.t('ad.use.measure.tokens'),
                        },
                        onChanged: c.setMeasure)),
                  ]);
            }),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
                spacing: AnumSpacing.sm,
                runSpacing: AnumSpacing.xs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(
                      l.f('ad.use.period', {
                        'from': usageDate(range.fromDay, l),
                        'to': usageDate(range.toDay, l),
                      }),
                      key: const Key('usage-period-text'),
                      style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
                  if (c.preset == UsagePreset.custom)
                    TextButton.icon(
                        key: const Key('usage-pick-dates'),
                        onPressed: _pickDates,
                        icon: const Icon(Icons.date_range_outlined),
                        label: Text(l.t('ad.use.pickDates'))),
                ]),
            const SizedBox(height: AnumSpacing.sm),
            ..._body(context, c, l),
          ]),
        );
      });

  List<Widget> _body(
      BuildContext context, BudgetUsageController c, AnumLocalizations l) {
    final p = context.palette;
    switch (c.phase) {
      case UsagePhase.loading:
        return [
          Semantics(
            liveRegion: true,
            label: l.t('ad.use.loading'),
            child: const Padding(
                padding: EdgeInsets.all(AnumSpacing.lg),
                child: Center(child: CircularProgressIndicator())),
          )
        ];
      case UsagePhase.denied:
        return [
          AnumEmpty(
              key: const Key('usage-denied'),
              icon: Icons.lock_outline,
              title: l.t('ad.use.denied'),
              message: c.error ?? ''),
        ];
      case UsagePhase.error:
        return [
          AnumEmpty(
              key: const Key('usage-error'),
              icon: Icons.error_outline,
              title: l.f('ad.use.error', {'message': ''}).trim(),
              message: c.error ?? '',
              action: l.t('ad.use.retry'),
              onAction: c.load),
        ];
      case UsagePhase.ready:
        break;
    }
    final b = c.breakdown!;
    final range = c.range;
    final exportRow = Wrap(
        spacing: AnumSpacing.sm,
        runSpacing: AnumSpacing.xs,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          OutlinedButton.icon(
              key: const Key('usage-export'),
              onPressed: c.exporting ? null : () => c.export(l),
              icon: const Icon(Icons.download_outlined),
              label: Text(
                  l.t(c.exporting ? 'ad.use.exporting' : 'ad.use.export'))),
          if (c.exportNotice != null)
            Text(c.exportNotice!,
                key: const Key('usage-export-notice'),
                style: TextStyle(
                    color: c.exportFailed ? p.stop : p.ok,
                    fontSize: AnumType.sm)),
        ]);
    if (c.rows.isEmpty) {
      return [
        AnumEmpty(
            key: const Key('usage-empty'),
            icon: Icons.insights_outlined,
            title: l.t('ad.use.empty'),
            message: l.f('ad.use.emptyText', {
              'from': usageDate(range.fromDay, l),
              'to': usageDate(range.toDay, l),
            }),
            action:
                c.preset == UsagePreset.last12m ? null : l.t('ad.use.longer'),
            onAction: c.preset == UsagePreset.last12m
                ? null
                : () => c.setPreset(UsagePreset.last12m)),
      ];
    }
    final bars = barFractions(c.rows, c.measure);
    return [
      if (b.recordedSince != null) ...[
        Text(
            l.f('ad.use.recordedSince',
                {'date': usageDate(b.recordedSince!, l)}),
            key: const Key('usage-recorded-since'),
            style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.sm),
      ],
      _TotalRow(
          value: _value(b.total, c.measure),
          calls: b.total.calls,
          unpriced: b.total.unpricedCalls),
      Divider(height: 20, color: p.line),
      for (var i = 0; i < c.rows.length; i++)
        _UsageRowView(
          key: Key('usage-row-${c.rows[i].key}'),
          label: usageRowLabel(c.group, c.rows[i], l,
              currentUserId: widget.currentUserId),
          value: _value(c.rows[i], c.measure),
          share: l.f('ad.use.share',
              {'percent': shareOf(c.rows[i], b.total, c.measure)}),
          detail: [
            l.f('ad.use.calls', {'count': formatTokens(c.rows[i].calls)}),
            l.f('ad.use.tokens', {'count': formatTokens(c.rows[i].tokens)}),
            if (c.rows[i].unpricedCalls > 0)
              l.f('ad.use.unpriced',
                  {'count': formatTokens(c.rows[i].unpricedCalls)}),
          ].join(' · '),
          fraction: bars[i],
        ),
      const SizedBox(height: AnumSpacing.sm),
      Text(l.f('ad.use.showing', {'shown': c.rows.length, 'count': b.count}),
          key: const Key('usage-showing'),
          style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
      if (c.moreError != null) ...[
        const SizedBox(height: AnumSpacing.xs),
        Text(l.f('ad.use.moreFailed', {'message': c.moreError!}),
            style: TextStyle(color: p.stop, fontSize: AnumType.sm)),
      ],
      if (c.hasMore) ...[
        const SizedBox(height: AnumSpacing.xs),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton(
              key: const Key('usage-more'),
              onPressed: c.loadingMore ? null : c.more,
              child: Text(l.t('ad.use.loadMore'))),
        ),
      ],
      const SizedBox(height: AnumSpacing.sm),
      exportRow,
    ];
  }
}

class _Choice<T> extends StatelessWidget {
  const _Choice(
      {required this.label,
      required this.value,
      required this.options,
      required this.onChanged,
      super.key});
  final String label;
  final T value;
  final Map<T, String> options;
  final void Function(T value) onChanged;

  @override
  Widget build(BuildContext context) => DropdownButtonFormField<T>(
        initialValue: value,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final entry in options.entries)
            DropdownMenuItem(
                value: entry.key,
                child: Text(entry.value, overflow: TextOverflow.ellipsis)),
        ],
        onChanged: (v) {
          if (v != null) onChanged(v);
        },
      );
}

class _TotalRow extends StatelessWidget {
  const _TotalRow(
      {required this.value, required this.calls, required this.unpriced});
  final String value;
  final int calls, unpriced;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    return Semantics(
      container: true,
      label: '${l.t('ad.use.total')}: $value',
      excludeSemantics: true,
      child: Wrap(
          key: const Key('usage-total'),
          spacing: AnumSpacing.md,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.end,
          children: [
            Text(l.t('ad.use.total'),
                style: TextStyle(
                    color: p.muted,
                    fontSize: AnumType.sm,
                    fontWeight: FontWeight.w600)),
            Text(value,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.lgPlus,
                    fontWeight: FontWeight.w800)),
            Text(l.f('ad.use.calls', {'count': formatTokens(calls)}),
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            if (unpriced > 0)
              Text(l.f('ad.use.unpriced', {'count': formatTokens(unpriced)}),
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          ]),
    );
  }
}

class _UsageRowView extends StatelessWidget {
  const _UsageRowView(
      {required this.label,
      required this.value,
      required this.share,
      required this.detail,
      required this.fraction,
      super.key});
  final String label, value, share, detail;
  final double fraction;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = AnumLocalizations.maybeOf(context);
    return Semantics(
      container: true,
      label: l.f(
          'ad.use.rowLabel', {'label': label, 'value': value, 'share': share}),
      hint: detail,
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.only(bottom: AnumSpacing.md),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
              alignment: WrapAlignment.spaceBetween,
              spacing: AnumSpacing.sm,
              runSpacing: AnumSpacing.xxs,
              children: [
                Text(label,
                    textDirection: null,
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w600)),
                Text('$value · $share',
                    style: TextStyle(
                        color: p.text,
                        fontWeight: FontWeight.w700,
                        fontFeatures: const [FontFeature.tabularFigures()])),
              ]),
          const SizedBox(height: AnumSpacing.xxs),
          ClipRRect(
            borderRadius: BorderRadius.circular(AnumRadius.pill),
            child: LinearProgressIndicator(
                value: fraction,
                minHeight: 8,
                color: p.sky,
                backgroundColor: p.background),
          ),
          const SizedBox(height: AnumSpacing.xxs),
          Text(detail, style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ]),
      ),
    );
  }
}
