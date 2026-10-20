import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';

/// The app background: the plain neutral page colour (no washes, so content
/// carries the page).
class AnumBackdrop extends StatelessWidget {
  const AnumBackdrop({required this.child, super.key});
  final Widget child;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: BoxDecoration(color: context.palette.background),
        child: child,
      );
}

/// A raised surface: solid (never glass, so text keeps contrast), a hairline
/// border and a soft ambient shadow. When tappable it presses in slightly
/// (150 ms, none with reduced motion).
class AnumSurface extends StatefulWidget {
  const AnumSurface({
    required this.child,
    this.onTap,
    this.padding = const EdgeInsets.all(AnumSpacing.md),
    this.accent,
    this.semanticLabel,
    super.key,
  });

  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry padding;

  /// Optional status bar on the leading edge, e.g. amber for "needs you".
  final Color? accent;
  final String? semanticLabel;

  @override
  State<AnumSurface> createState() => _AnumSurfaceState();
}

class _AnumSurfaceState extends State<AnumSurface> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final radius = BorderRadius.circular(AnumRadius.lg);
    // The shadow sits on an uncoloured box and the fill is a Material, so
    // list tiles and ink inside the card still paint (a coloured box between
    // a ListTile and its Material would hide them).
    final card = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: radius,
        boxShadow: [
          BoxShadow(
              color: p.shadow, blurRadius: 16, offset: const Offset(0, 4)),
        ],
      ),
      child: Material(
        color: _pressed ? p.surface3 : p.surface1,
        animationDuration: AnumMotion.of(context, AnumMotion.quick),
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
            borderRadius: radius, side: BorderSide(color: p.line)),
        child: Stack(children: [
          if (widget.accent != null)
            PositionedDirectional(
              top: 0,
              bottom: 0,
              start: 0,
              child: Container(width: 3, color: widget.accent),
            ),
          Padding(padding: widget.padding, child: widget.child),
        ]),
      ),
    );
    // When a summary label is given it replaces the children's text, so a
    // screen reader announces the card once instead of twice.
    final summarise = widget.semanticLabel != null;
    if (widget.onTap == null) {
      return Semantics(
          label: widget.semanticLabel,
          excludeSemantics: summarise,
          child: card);
    }
    return Semantics(
      button: true,
      label: widget.semanticLabel,
      excludeSemantics: summarise,
      child: GestureDetector(
        onTapDown: (_) => setState(() => _pressed = true),
        onTapUp: (_) => setState(() => _pressed = false),
        onTapCancel: () => setState(() => _pressed = false),
        onTap: widget.onTap,
        child: AnimatedScale(
          scale: _pressed ? 0.985 : 1,
          duration: AnumMotion.of(context, AnumMotion.quick),
          curve: AnumMotion.curve,
          child: card,
        ),
      ),
    );
  }
}

/// Eyebrow + title + optional subtitle and trailing action.
class AnumHeader extends StatelessWidget {
  const AnumHeader({
    required this.title,
    this.eyebrow,
    this.subtitle,
    this.trailing,
    this.large = false,
    super.key,
  });

  final String title;
  final String? eyebrow, subtitle;
  final Widget? trailing;
  final bool large;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (eyebrow != null)
            Text(eyebrow!,
                style: TextStyle(
                    color: p.sky,
                    fontSize: AnumType.xsPlus,
                    fontWeight: FontWeight.w600)),
          if (eyebrow != null) const SizedBox(height: 4),
          Text(title,
              style: TextStyle(
                  color: p.text,
                  fontSize: large ? AnumType.x5l : AnumType.xlPlus,
                  fontWeight: FontWeight.w700,
                  height: 1.15)),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(subtitle!,
                style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
          ],
        ]),
      ),
      // Shares the row at large text sizes instead of pushing it over.
      if (trailing != null) Flexible(child: trailing!),
    ]);
  }
}

/// A big number in the text colour, with a label, icon and caption.
class AnumMetric extends StatelessWidget {
  const AnumMetric({
    required this.label,
    required this.value,
    required this.icon,
    this.caption,
    this.onTap,
    this.accent,
    super.key,
  });

  final String label, value;
  final String? caption;
  final IconData icon;
  final VoidCallback? onTap;
  final Color? accent;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      onTap: onTap,
      accent: accent,
      semanticLabel: '$label: $value${caption == null ? '' : ', $caption'}',
      child: ExcludeSemantics(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
                child: Text(label,
                    style:
                        TextStyle(color: p.muted, fontSize: AnumType.xsPlus))),
            Icon(icon, size: 18, color: accent ?? p.sky),
          ]),
          const SizedBox(height: 10),
          // A solid token colour, so a row of figures scans quickly; the
          // accent stays on the icon.
          Text(value,
              style: TextStyle(
                  color: p.text,
                  fontSize: AnumType.x4lPlus,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.5,
                  height: 1.1)),
          if (caption != null) ...[
            const SizedBox(height: 8),
            Text(caption!,
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          ],
        ]),
      ),
    );
  }
}

enum AnumTone { neutral, info, ok, warn, stop, accent }

Color anumToneColor(BuildContext context, AnumTone tone) {
  final p = context.palette;
  return switch (tone) {
    AnumTone.neutral => p.muted,
    AnumTone.info => p.sky,
    AnumTone.ok => p.ok,
    AnumTone.warn => p.warn,
    AnumTone.stop => p.stop,
    AnumTone.accent => p.violet,
  };
}

/// A small translucent status pill.
class AnumPill extends StatelessWidget {
  const AnumPill(
      {required this.label,
      this.tone = AnumTone.neutral,
      this.icon,
      super.key});
  final String label;
  final AnumTone tone;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final color = anumToneColor(context, tone);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Color.alphaBlend(
            color.withValues(alpha: 0.12), context.palette.surface1),
        borderRadius: BorderRadius.circular(AnumRadius.pill),
        border: Border.all(color: color.withValues(alpha: 0.24)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: color),
            const SizedBox(width: 4),
          ],
          // Wraps inside a narrow parent at large text sizes.
          Flexible(
            child: Text(label,
                style: TextStyle(
                    color: color,
                    fontSize: AnumType.xxsPlus,
                    fontWeight: FontWeight.w700)),
          ),
        ]),
      ),
    );
  }
}

/// A tinted icon chip used at the start of rows: the tone's (or the
/// accent's) soft tint with the icon in that colour.
class AnumIconChip extends StatelessWidget {
  const AnumIconChip({required this.icon, this.tone, super.key});
  final IconData icon;
  final AnumTone? tone;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = tone == null ? p.violet : anumToneColor(context, tone!);
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AnumRadius.md),
        color: tone == null
            ? p.accentSoft
            : Color.alphaBlend(color.withValues(alpha: 0.12), p.surface1),
      ),
      child: Icon(icon, size: 20, color: color),
    );
  }
}

/// A list row inside a surface: icon, title, details, trailing.
class AnumRow extends StatelessWidget {
  const AnumRow({
    required this.title,
    this.icon,
    this.tone,
    this.subtitle,
    this.detail,
    this.trailing,
    this.onTap,
    super.key,
  });

  final String title;
  final String? subtitle, detail;
  final IconData? icon;
  final AnumTone? tone;
  final Widget? trailing;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final content = Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (icon != null) ...[
          AnumIconChip(icon: icon!, tone: tone),
          const SizedBox(width: 12),
        ],
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.base,
                    fontWeight: FontWeight.w600)),
            if (subtitle != null) ...[
              const SizedBox(height: 3),
              Text(subtitle!,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            ],
            if (detail != null) ...[
              const SizedBox(height: 4),
              Text(detail!,
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            ],
          ]),
        ),
        if (trailing != null) ...[const SizedBox(width: 10), trailing!],
      ]),
    );
    if (onTap == null) return content;
    return InkWell(
        onTap: onTap, borderRadius: BorderRadius.circular(12), child: content);
  }
}

/// An honest empty or error state with an optional next action.
class AnumEmpty extends StatelessWidget {
  const AnumEmpty({
    required this.title,
    required this.message,
    this.icon = Icons.inbox_outlined,
    this.action,
    this.onAction,
    super.key,
  });

  final String title, message;
  final IconData icon;
  final String? action;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 24, horizontal: 8),
      child: Column(children: [
        Container(
          width: 52,
          height: 52,
          decoration: BoxDecoration(
              color: p.surface3,
              borderRadius: BorderRadius.circular(AnumRadius.lg),
              border: Border.all(color: p.line)),
          child: Icon(icon, size: 24, color: p.muted),
        ),
        const SizedBox(height: AnumSpacing.sm),
        Text(title,
            textAlign: TextAlign.center,
            style: TextStyle(
                color: p.text,
                fontWeight: FontWeight.w700,
                fontSize: AnumType.base)),
        const SizedBox(height: 4),
        Text(message,
            textAlign: TextAlign.center,
            style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
        if (action != null && onAction != null) ...[
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onAction, child: Text(action!)),
        ],
      ]),
    );
  }
}

/// Standard scrolling page: backdrop, pull to refresh, readable max width.
class AnumPage extends StatelessWidget {
  const AnumPage({required this.children, this.onRefresh, super.key});
  final List<Widget> children;
  final Future<void> Function()? onRefresh;

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final gutter = width < AnumBreakpoints.compact ? 16.0 : 28.0;
    final list = ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: EdgeInsets.fromLTRB(gutter, 16, gutter, 32),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1100),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (var i = 0; i < children.length; i++) ...[
                  children[i],
                  if (i < children.length - 1) const SizedBox(height: 16),
                ]
              ],
            ),
          ),
        ),
      ],
    );
    return AnumBackdrop(
      child: SafeArea(
        bottom: false,
        child: onRefresh == null
            ? list
            : RefreshIndicator(onRefresh: onRefresh!, child: list),
      ),
    );
  }
}

/// Lays out metric tiles in rows of at most 2 on phones and 4 from 760 px,
/// spread evenly so no tile is left alone on the last row: 5 tiles are
/// 3 + 2 on a wide screen and 2 + 2 + 1 (the last spanning the row) on a
/// phone; 3 tiles on a wide screen share one row.
class AnumMetricGrid extends StatelessWidget {
  const AnumMetricGrid({required this.children, super.key});
  final List<Widget> children;

  static const gap = 12.0;

  /// How many tiles go on each row for [count] tiles and at most
  /// [maxColumns] per row: as few rows as possible, the longer rows first,
  /// and row lengths that differ by one at most.
  static List<int> rowSizes(int count, int maxColumns) {
    if (count <= 0) return const [];
    final rows = (count + maxColumns - 1) ~/ maxColumns;
    final base = count ~/ rows, extra = count % rows;
    return [for (var r = 0; r < rows; r++) base + (r < extra ? 1 : 0)];
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final maxColumns = c.maxWidth >= 760 ? 4 : 2;
        final rows = <Widget>[];
        var start = 0;
        for (final size in rowSizes(children.length, maxColumns)) {
          final row = children.sublist(start, start + size);
          start += size;
          rows.add(Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            for (var i = 0; i < row.length; i++) ...[
              if (i > 0) const SizedBox(width: gap),
              Expanded(child: row[i]),
            ],
          ]));
        }
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              for (var i = 0; i < rows.length; i++) ...[
                if (i > 0) const SizedBox(height: gap),
                rows[i],
              ],
            ]);
      });
}

String relativeTime(DateTime value, {DateTime? now}) {
  final diff = (now ?? DateTime.now()).difference(value.toLocal());
  if (diff.inSeconds < 60) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
  if (diff.inHours < 24) return '${diff.inHours} h ago';
  if (diff.inDays < 7) return '${diff.inDays} d ago';
  final local = value.toLocal();
  return '${local.year}-${local.month.toString().padLeft(2, '0')}-${local.day.toString().padLeft(2, '0')}';
}

String exactTime(DateTime value) {
  final l = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${l.year}-${two(l.month)}-${two(l.day)} ${two(l.hour)}:${two(l.minute)}';
}
