import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'report_charts.dart';
import 'report_sections.dart';

/// The typed sections of a report as the ANUM 360 "dynamic workspace":
/// tab chips, metric tiles, trend bars, the category donut, the ranked list
/// and text (docs/reports.md). Shown inside the library's report screen.
class ReportSectionsView extends StatefulWidget {
  const ReportSectionsView({required this.layout, super.key});
  final ReportLayout layout;

  @override
  State<ReportSectionsView> createState() => _ReportSectionsViewState();
}

class _ReportSectionsViewState extends State<ReportSectionsView> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final tabs = widget.layout.tabs;
    final tab = tabs.isEmpty ? null : tabs[_tab.clamp(0, tabs.length - 1)];
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (tabs.length > 1) ...[
        _Tabs(
            tabs: tabs,
            selected: _tab,
            onSelected: (value) => setState(() => _tab = value)),
        const SizedBox(height: AnumSpacing.md),
      ],
      _Sections(sections: widget.layout.sectionsFor(tab)),
    ]);
  }
}

class _Tabs extends StatelessWidget {
  const _Tabs(
      {required this.tabs, required this.selected, required this.onSelected});
  final List<String> tabs;
  final int selected;
  final ValueChanged<int> onSelected;

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(children: [
          for (var i = 0; i < tabs.length; i++)
            Padding(
              padding: const EdgeInsetsDirectional.only(end: AnumSpacing.xs),
              child: ChoiceChip(
                label: Text(tabs[i]),
                selected: i == selected,
                onSelected: (_) => onSelected(i),
              ),
            ),
        ]),
      );
}

class _Sections extends StatelessWidget {
  const _Sections({required this.sections});
  final List<ReportSection> sections;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    if (sections.isEmpty) {
      return AnumSurface(
        child: AnumEmpty(
          icon: Icons.tab_unselected,
          title: l.t('reportTabEmpty'),
          message: l.t('reportTabEmptyText'),
        ),
      );
    }
    return LayoutBuilder(builder: (context, constraints) {
      final wide = constraints.maxWidth >= AnumBreakpoints.expanded;
      const gap = AnumSpacing.md;
      final half = (constraints.maxWidth - gap) / 2;
      final children = <Widget>[];
      for (final section in sections) {
        final full = !wide ||
            section is MetricsSection ||
            section is TextSection ||
            section is SeriesSection && section.points.length > 12;
        children.add(SizedBox(
          width: full ? constraints.maxWidth : half,
          child: _SectionView(section: section),
        ));
      }
      return Wrap(spacing: gap, runSpacing: gap, children: children);
    });
  }
}

class _SectionView extends StatelessWidget {
  const _SectionView({required this.section});
  final ReportSection section;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final section = this.section;
    if (section is MetricsSection) {
      return AnumMetricGrid(children: [
        for (final metric in section.items) _MetricTile(metric: metric),
      ]);
    }
    final body = switch (section) {
      SeriesSection() => SeriesBarChart(section: section),
      BreakdownSection() => BreakdownDonut(section: section),
      RankedSection() => _Ranked(section: section),
      TextSection() => Text(section.body,
          style: TextStyle(color: context.palette.muted, fontSize: 14)),
      UnsupportedSection() => Text(
          l.f('reportUnsupported', {'type': section.type}),
          style: TextStyle(color: context.palette.faint)),
      MetricsSection() => const SizedBox.shrink(),
    };
    final title = section.title;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (title != null) ...[
          Semantics(
            header: true,
            child: Text(title,
                style: TextStyle(
                    color: context.palette.text,
                    fontWeight: FontWeight.w700,
                    fontSize: 15)),
          ),
          const SizedBox(height: AnumSpacing.sm),
        ],
        body,
      ]),
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.metric});
  final SectionMetric metric;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final value = metric.style.format(metric.value);
    final change = metric.changePct;
    return AnumSurface(
      semanticLabel: [
        metric.label,
        value,
        if (change != null)
          l.f('reportChangeSpoken', {'change': formatChange(change)}),
        if (metric.caption != null) metric.caption!,
      ].join(', '),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(metric.label, style: TextStyle(color: p.muted, fontSize: 12.5)),
        const SizedBox(height: AnumSpacing.xs),
        Text(value,
            style: TextStyle(
                color: p.text,
                fontSize: 22,
                fontWeight: FontWeight.w800,
                height: 1.1)),
        if (change != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: ChangeLabel(
                change: change, higherIsBetter: metric.higherIsBetter),
          ),
        ],
        if (metric.caption != null) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text(metric.caption!, style: TextStyle(color: p.faint, fontSize: 12)),
        ],
      ]),
    );
  }
}

class _Ranked extends StatefulWidget {
  const _Ranked({required this.section});
  final RankedSection section;

  @override
  State<_Ranked> createState() => _RankedState();
}

class _RankedState extends State<_Ranked> {
  static const _shown = 5;
  bool _all = false;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final more = widget.section.items.length > _shown;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      RankedBars(section: widget.section, limit: _all ? null : _shown),
      if (more)
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: TextButton(
            onPressed: () => setState(() => _all = !_all),
            child: Text(_all
                ? l.t('reportShowFewer')
                : l.f('reportShowAll', {'count': widget.section.items.length})),
          ),
        ),
    ]);
  }
}

class ReportAskBar extends StatelessWidget {
  const ReportAskBar(
      {super.key,
      required this.controller,
      required this.busy,
      required this.onSend});
  final TextEditingController controller;
  final bool busy;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final width = MediaQuery.sizeOf(context).width;
    final gutter =
        width < AnumBreakpoints.compact ? AnumSpacing.md : AnumSpacing.lg;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: p.surface1,
        border: Border(top: BorderSide(color: p.line)),
      ),
      child: SafeArea(
        top: false,
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1100),
            child: Padding(
              padding: EdgeInsets.fromLTRB(
                  gutter, AnumSpacing.xs, gutter, AnumSpacing.xs),
              child: Row(children: [
                Expanded(
                  child: TextField(
                    controller: controller,
                    minLines: 1,
                    maxLines: 3,
                    maxLength: 1000,
                    textInputAction: TextInputAction.send,
                    onSubmitted: (_) => onSend(),
                    decoration: InputDecoration(
                      hintText: l.t('reportAskHint'),
                      counterText: '',
                      prefixIcon: const Icon(Icons.auto_awesome),
                    ),
                  ),
                ),
                const SizedBox(width: AnumSpacing.xs),
                IconButton.filled(
                  tooltip: l.t('reportAskSend'),
                  onPressed: busy ? null : onSend,
                  icon: busy
                      ? const SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.arrow_upward),
                ),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}
