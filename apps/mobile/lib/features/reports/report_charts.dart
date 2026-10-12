import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'report_sections.dart';

/// Chart colours (docs/reports.md#charts). The categorical order is fixed and
/// was checked with the dataviz validator against the app's surfaces (dark
/// `surface1` #111829 and light white): lightness band, chroma, colour-blind
/// and normal-vision separation all pass; three light-mode slots are under 3:1,
/// so every chart also prints its values as text (legend and labels).
/// Kept here, not in the shared theme, until the shell agent folds it in.
abstract final class ReportChartColors {
  static const _dark = [
    Color(0xFF3987E5), // blue
    Color(0xFFD95926), // orange
    Color(0xFF199E70), // aqua
    Color(0xFFC98500), // yellow
    Color(0xFFD55181), // magenta
    Color(0xFF9085E9), // violet
  ];
  static const _light = [
    Color(0xFF2A78D6),
    Color(0xFFEB6834),
    Color(0xFF1BAF7A),
    Color(0xFFEDA100),
    Color(0xFFE87BA4),
    Color(0xFF6250D6),
  ];

  static const slots = 6;

  static List<Color> of(BuildContext context) =>
      Theme.of(context).brightness == Brightness.dark ? _dark : _light;

  /// The single-series colour (slot 1, blue).
  static Color series(BuildContext context) => of(context).first;
}

/// Bars over ordered labels (a monthly trend). Tapping a bar (or focusing
/// it with a screen reader) shows its value; the latest bar starts selected.
class SeriesBarChart extends StatefulWidget {
  const SeriesBarChart({required this.section, this.height = 180, super.key});
  final SeriesSection section;

  /// The plot's height; the chart is a figure, so it has an intrinsic size.
  final double height;

  @override
  State<SeriesBarChart> createState() => _SeriesBarChartState();
}

class _SeriesBarChartState extends State<SeriesBarChart> {
  late int _selected = widget.section.points.length - 1;

  @override
  void didUpdateWidget(SeriesBarChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_selected >= widget.section.points.length) {
      _selected = widget.section.points.length - 1;
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final points = widget.section.points;
    final style = widget.section.style;
    final color = ReportChartColors.series(context);
    final selected = points[_selected];
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Semantics(
        liveRegion: true,
        child: Text(
          '${selected.label}: ${style.format(selected.value)}',
          style: TextStyle(
              color: p.text, fontWeight: FontWeight.w700, fontSize: 14),
        ),
      ),
      const SizedBox(height: AnumSpacing.xs),
      LayoutBuilder(builder: (context, outer) {
        // Every bar keeps a 48 px wide target; many bars scroll sideways.
        final width = math.max(outer.maxWidth, points.length * 48.0);
        final chart = SizedBox(
          width: width,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SizedBox(
              height: widget.height,
              child: LayoutBuilder(builder: (context, constraints) {
                final slot = constraints.maxWidth / points.length;
                return Stack(children: [
                  Positioned.fill(
                    child: CustomPaint(
                      painter: BarSeriesPainter(
                        values: [for (final point in points) point.value],
                        selected: _selected,
                        color: color,
                        grid: p.line,
                        axis: p.faint,
                        selectedColor: p.violet,
                        label: style.compact,
                        textDirection: Directionality.of(context),
                      ),
                    ),
                  ),
                  // Hit targets wider than the bars, one per point.
                  for (var i = 0; i < points.length; i++)
                    PositionedDirectional(
                      start: slot * i,
                      width: slot,
                      top: 0,
                      bottom: 0,
                      child: Semantics(
                        button: true,
                        selected: i == _selected,
                        label:
                            '${points[i].label}: ${style.format(points[i].value)}',
                        child: GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onTap: () => setState(() => _selected = i),
                        ),
                      ),
                    ),
                ]);
              }),
            ),
            const SizedBox(height: AnumSpacing.xxs),
            ExcludeSemantics(
              child: Row(children: [
                for (var i = 0; i < points.length; i++)
                  Expanded(
                    child: Text(
                      points[i].label,
                      maxLines: 1,
                      overflow: TextOverflow.clip,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                          color: i == _selected ? p.text : p.faint,
                          fontSize: 11),
                    ),
                  ),
              ]),
            ),
          ]),
        );
        return width > outer.maxWidth
            ? SingleChildScrollView(
                scrollDirection: Axis.horizontal, child: chart)
            : chart;
      }),
    ]);
  }
}

/// Paints the bars (4 px rounded tops anchored to the baseline), three
/// recessive grid lines with compact values, and the selected bar in violet.
class BarSeriesPainter extends CustomPainter {
  BarSeriesPainter({
    required this.values,
    required this.selected,
    required this.color,
    required this.selectedColor,
    required this.grid,
    required this.axis,
    required this.label,
    required this.textDirection,
  });

  final List<double> values;
  final int selected;
  final Color color, selectedColor, grid, axis;
  final String Function(double) label;
  final TextDirection textDirection;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.isEmpty || size.width <= 0 || size.height <= 0) return;
    final top = values.reduce(math.max);
    final bottom = math.min(0.0, values.reduce(math.min));
    final span = top - bottom == 0 ? 1.0 : top - bottom;
    const gutter = 14.0;
    final plot = size.height - gutter;
    double y(double v) => gutter + plot * (1 - (v - bottom) / span);

    final gridPaint = Paint()
      ..color = grid
      ..strokeWidth = 1;
    for (final fraction in const [0.5, 1.0]) {
      final value = bottom + span * fraction;
      final dy = y(value);
      canvas.drawLine(Offset(0, dy), Offset(size.width, dy), gridPaint);
      final text = TextPainter(
        text: TextSpan(
            text: label(value), style: TextStyle(color: axis, fontSize: 10)),
        textDirection: textDirection,
      )..layout();
      final dx =
          textDirection == TextDirection.rtl ? size.width - text.width : 0.0;
      text.paint(canvas, Offset(dx, dy - text.height - 1));
    }

    final slot = size.width / values.length;
    final width = math.max(4.0, math.min(28.0, slot * 0.55));
    for (var i = 0; i < values.length; i++) {
      final index =
          textDirection == TextDirection.rtl ? values.length - 1 - i : i;
      final center = slot * index + slot / 2;
      final from = y(math.max(0, bottom));
      final to = y(values[i]);
      final rect = Rect.fromLTRB(center - width / 2, math.min(from, to),
          center + width / 2, math.max(from, to));
      final paint = Paint()..color = i == selected ? selectedColor : color;
      canvas.drawRRect(
        RRect.fromRectAndCorners(rect,
            topLeft: const Radius.circular(4),
            topRight: const Radius.circular(4)),
        paint,
      );
    }
    canvas.drawLine(Offset(0, y(math.max(0, bottom))),
        Offset(size.width, y(math.max(0, bottom))), gridPaint);
  }

  @override
  bool shouldRepaint(BarSeriesPainter old) =>
      old.values != values ||
      old.selected != selected ||
      old.color != color ||
      old.grid != grid;
}

/// Parts of a whole: a donut with a legend that prints every share as text.
/// More than six parts fold into "Other" (categorical colours are never
/// generated past the fixed six).
class BreakdownDonut extends StatelessWidget {
  const BreakdownDonut({required this.section, super.key});
  final BreakdownSection section;

  static List<SectionPoint> folded(List<SectionPoint> items, String other) {
    if (items.length <= ReportChartColors.slots) return items;
    final head = items.take(ReportChartColors.slots - 1).toList();
    final rest = items
        .skip(ReportChartColors.slots - 1)
        .fold<double>(0, (sum, item) => sum + item.value);
    return [...head, SectionPoint(other, rest)];
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final colors = ReportChartColors.of(context);
    final items = folded(section.items, l.t('reportOther'));
    final total = section.total;
    String share(double value) =>
        total <= 0 ? '0%' : '${(value / total * 100).round()}%';
    final legend = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < items.length; i++)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
            child: Semantics(
              label:
                  '${items[i].label}: ${share(items[i].value)}, ${section.style.format(items[i].value)}',
              excludeSemantics: true,
              child: Row(children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration:
                      BoxDecoration(color: colors[i], shape: BoxShape.circle),
                ),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                  child: Text(items[i].label,
                      style: TextStyle(color: p.muted, fontSize: 13)),
                ),
                const SizedBox(width: AnumSpacing.xs),
                Text(share(items[i].value),
                    style: TextStyle(
                        color: p.text,
                        fontSize: 13,
                        fontWeight: FontWeight.w600)),
              ]),
            ),
          ),
      ],
    );
    final donut = ExcludeSemantics(
      child: SizedBox.square(
        dimension: 120,
        child: CustomPaint(
          painter: DonutPainter(
            values: [for (final item in items) item.value],
            colors: colors,
            gap: p.surface1,
            empty: p.line,
          ),
        ),
      ),
    );
    return LayoutBuilder(builder: (context, constraints) {
      if (constraints.maxWidth < 300) {
        return Column(children: [
          donut,
          const SizedBox(height: AnumSpacing.sm),
          legend,
        ]);
      }
      return Row(children: [
        donut,
        const SizedBox(width: AnumSpacing.lg),
        Expanded(child: legend),
      ]);
    });
  }
}

class DonutPainter extends CustomPainter {
  DonutPainter(
      {required this.values,
      required this.colors,
      required this.gap,
      required this.empty});
  final List<double> values;
  final List<Color> colors;
  final Color gap, empty;

  @override
  void paint(Canvas canvas, Size size) {
    final stroke = size.shortestSide * 0.16;
    final rect = Rect.fromCircle(
        center: size.center(Offset.zero),
        radius: (size.shortestSide - stroke) / 2);
    final total = values.fold<double>(0, (sum, v) => sum + v);
    final base = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke;
    if (total <= 0) {
      canvas.drawArc(rect, 0, math.pi * 2, false, base..color = empty);
      return;
    }
    var start = -math.pi / 2;
    for (var i = 0; i < values.length; i++) {
      final sweep = values[i] / total * math.pi * 2;
      if (sweep <= 0) continue;
      canvas.drawArc(
          rect, start, sweep, false, Paint.from(base)..color = colors[i]);
      // A 2 px surface gap between neighbouring parts.
      if (values.length > 1) {
        final edge = Offset(rect.center.dx + math.cos(start) * rect.width / 2,
            rect.center.dy + math.sin(start) * rect.height / 2);
        final inner = Offset.lerp(rect.center, edge, 1 - stroke / rect.width)!;
        final outer = Offset.lerp(rect.center, edge, 1 + stroke / rect.width)!;
        canvas.drawLine(
            inner,
            outer,
            Paint()
              ..color = gap
              ..strokeWidth = 2);
      }
      start += sweep;
    }
  }

  @override
  bool shouldRepaint(DonutPainter old) =>
      old.values != values || old.colors != colors || old.gap != gap;
}

/// A ranked list: label, a bar for the value against the largest, and the
/// value or change as text with an arrow (never colour alone).
class RankedBars extends StatelessWidget {
  const RankedBars({required this.section, this.limit, super.key});
  final RankedSection section;
  final int? limit;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final items =
        limit == null ? section.items : section.items.take(limit!).toList();
    final top = items.fold<double>(0, (m, i) => math.max(m, i.value.abs()));
    final color = ReportChartColors.series(context);
    return Column(children: [
      for (final item in items)
        Semantics(
          label: [
            item.label,
            section.style.format(item.value),
            if (item.changePct != null)
              l.f('reportChangeSpoken',
                  {'change': formatChange(item.changePct!)}),
            if (item.detail != null) item.detail!,
          ].join(', '),
          excludeSemantics: true,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
            child: LayoutBuilder(builder: (context, constraints) {
              final narrow = constraints.maxWidth < 300;
              final label = Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(item.label,
                      style: TextStyle(
                          color: p.text,
                          fontSize: 14,
                          fontWeight: FontWeight.w600)),
                  if (item.detail != null)
                    Text(item.detail!,
                        style: TextStyle(color: p.faint, fontSize: 12)),
                ],
              );
              final bar = ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: SizedBox(
                  height: 8,
                  child: Stack(children: [
                    Positioned.fill(child: ColoredBox(color: p.line)),
                    FractionallySizedBox(
                      alignment: AlignmentDirectional.centerStart,
                      widthFactor:
                          top <= 0 ? 0 : (item.value.abs() / top).clamp(0, 1),
                      child: ColoredBox(color: color),
                    ),
                  ]),
                ),
              );
              final value = _ChangeText(
                  value: section.style.format(item.value),
                  change: item.changePct,
                  higherIsBetter: section.higherIsBetter);
              if (narrow) {
                return Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      label,
                      const SizedBox(height: AnumSpacing.xxs),
                      bar,
                      const SizedBox(height: AnumSpacing.xxs),
                      value,
                    ]);
              }
              return Row(children: [
                Expanded(flex: 4, child: label),
                const SizedBox(width: AnumSpacing.sm),
                Expanded(flex: 3, child: bar),
                const SizedBox(width: AnumSpacing.sm),
                Flexible(flex: 3, child: value),
              ]);
            }),
          ),
        ),
    ]);
  }
}

class _ChangeText extends StatelessWidget {
  const _ChangeText(
      {required this.value,
      required this.change,
      required this.higherIsBetter});
  final String value;
  final double? change;
  final bool higherIsBetter;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final delta = change;
    if (delta == null) {
      return Text(value,
          textAlign: TextAlign.end,
          style: TextStyle(
              color: p.text, fontSize: 13, fontWeight: FontWeight.w600));
    }
    return ChangeLabel(change: delta, higherIsBetter: higherIsBetter);
  }
}

/// `↑ +66.6%` in the status colour for good or bad news, with the arrow so
/// the direction never depends on colour.
class ChangeLabel extends StatelessWidget {
  const ChangeLabel(
      {required this.change, required this.higherIsBetter, super.key});
  final double change;
  final bool higherIsBetter;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final good = change == 0 ? null : (change > 0) == higherIsBetter;
    final color = good == null ? p.muted : (good ? p.ok : p.stop);
    final icon = change > 0
        ? Icons.arrow_upward
        : change < 0
            ? Icons.arrow_downward
            : Icons.remove;
    return Row(
      mainAxisSize: MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.end,
      children: [
        Icon(icon, size: 14, color: color),
        const SizedBox(width: 2),
        Flexible(
          child: Text(formatChange(change),
              style: TextStyle(
                  color: color, fontSize: 13, fontWeight: FontWeight.w700)),
        ),
      ],
    );
  }
}
