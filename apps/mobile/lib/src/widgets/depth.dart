import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';

/// The app background: midnight with faint aurora washes.
class AnumBackdrop extends StatelessWidget {
  const AnumBackdrop({required this.child, super.key});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final dark = Theme.of(context).brightness == Brightness.dark;
    final wash = dark ? 1.0 : 0.5;
    return DecoratedBox(
      decoration: BoxDecoration(color: p.background),
      child: Stack(children: [
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(-0.9, -1.1),
                radius: 1.1,
                colors: [
                  p.sky.withValues(alpha: 0.12 * wash),
                  Colors.transparent
                ],
              ),
            ),
          ),
        ),
        Positioned.fill(
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(1.0, -1.0),
                radius: 1.0,
                colors: [
                  p.violet.withValues(alpha: 0.14 * wash),
                  Colors.transparent
                ],
              ),
            ),
          ),
        ),
        child,
      ]),
    );
  }
}

/// A raised surface: solid (never glass, so text keeps contrast), lighter at
/// the top, with a lit top edge and a contact + ambient shadow. When tappable
/// it presses in slightly, like a physical card.
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

  /// Optional coloured glow on the leading edge, e.g. amber for "needs you".
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
    final reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    final radius = BorderRadius.circular(18);
    final card = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: radius,
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [p.surface2, p.surface1],
        ),
        border: Border.all(color: p.line),
        boxShadow: [
          BoxShadow(
              color: p.shadow, blurRadius: 24, offset: const Offset(0, 10)),
          BoxShadow(
              color: p.shadow.withValues(alpha: 0.4),
              blurRadius: 4,
              offset: const Offset(0, 1)),
          if (widget.accent != null)
            BoxShadow(
                color: widget.accent!.withValues(alpha: 0.18),
                blurRadius: 22,
                spreadRadius: -4),
        ],
      ),
      child: ClipRRect(
        borderRadius: radius,
        child: Stack(children: [
          // Lit top edge.
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: Container(height: 1, color: p.edge),
          ),
          if (widget.accent != null)
            PositionedDirectional(
              top: 14,
              bottom: 14,
              start: 0,
              child: Container(
                  width: 3,
                  decoration: BoxDecoration(
                      color: widget.accent,
                      borderRadius: const BorderRadiusDirectional.horizontal(
                          end: Radius.circular(3)))),
            ),
          Padding(padding: widget.padding, child: widget.child),
        ]),
      ),
    );
    if (widget.onTap == null) {
      return Semantics(label: widget.semanticLabel, child: card);
    }
    return Semantics(
      button: true,
      label: widget.semanticLabel,
      child: GestureDetector(
        onTapDown: (_) => setState(() => _pressed = true),
        onTapUp: (_) => setState(() => _pressed = false),
        onTapCancel: () => setState(() => _pressed = false),
        onTap: widget.onTap,
        child: AnimatedScale(
          scale: _pressed && !reduceMotion ? 0.98 : 1,
          duration: const Duration(milliseconds: 120),
          curve: Curves.easeOut,
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
            Text(eyebrow!.toUpperCase(),
                style: TextStyle(
                    color: p.sky,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.8)),
          if (eyebrow != null) const SizedBox(height: 4),
          Text(title,
              style: TextStyle(
                  color: p.text,
                  fontSize: large ? 28 : 19,
                  fontWeight: FontWeight.w700,
                  height: 1.15)),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(subtitle!, style: TextStyle(color: p.muted, fontSize: 13.5)),
          ],
        ]),
      ),
      if (trailing != null) trailing!,
    ]);
  }
}

/// A big number with a soft gradient, label and caption.
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
                    style: TextStyle(color: p.muted, fontSize: 12.5))),
            Icon(icon, size: 18, color: accent ?? p.sky),
          ]),
          const SizedBox(height: 10),
          ShaderMask(
            shaderCallback: (rect) => LinearGradient(
              colors: [p.text, p.sky, p.violet],
              stops: const [0, 0.6, 1],
            ).createShader(rect),
            child: Text(value,
                style: const TextStyle(
                    color: Colors.white,
                    fontSize: 28,
                    fontWeight: FontWeight.w800,
                    height: 1)),
          ),
          if (caption != null) ...[
            const SizedBox(height: 8),
            Text(caption!, style: TextStyle(color: p.faint, fontSize: 12)),
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
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.28)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: color),
            const SizedBox(width: 4),
          ],
          Text(label,
              style: TextStyle(
                  color: color, fontSize: 11.5, fontWeight: FontWeight.w700)),
        ]),
      ),
    );
  }
}

/// A gradient icon chip used at the start of rows.
class AnumIconChip extends StatelessWidget {
  const AnumIconChip({required this.icon, this.tone, super.key});
  final IconData icon;
  final AnumTone? tone;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final color = tone == null ? null : anumToneColor(context, tone!);
    return Container(
      width: 40,
      height: 40,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(12),
        gradient: LinearGradient(colors: [
          (color ?? p.sky).withValues(alpha: 0.24),
          (color ?? p.violet).withValues(alpha: 0.24),
        ]),
        border: Border.all(color: p.edge),
      ),
      child: Icon(icon, size: 20, color: p.text),
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
                    color: p.text, fontSize: 15, fontWeight: FontWeight.w600)),
            if (subtitle != null) ...[
              const SizedBox(height: 3),
              Text(subtitle!,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: p.muted, fontSize: 13)),
            ],
            if (detail != null) ...[
              const SizedBox(height: 4),
              Text(detail!, style: TextStyle(color: p.faint, fontSize: 12)),
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
        Icon(icon, size: 34, color: p.violet),
        const SizedBox(height: 10),
        Text(title,
            textAlign: TextAlign.center,
            style: TextStyle(
                color: p.text, fontWeight: FontWeight.w700, fontSize: 15)),
        const SizedBox(height: 4),
        Text(message,
            textAlign: TextAlign.center,
            style: TextStyle(color: p.muted, fontSize: 13.5)),
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

/// Lays out metric tiles in 2 columns on phones, 4 on wide screens.
class AnumMetricGrid extends StatelessWidget {
  const AnumMetricGrid({required this.children, super.key});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, c) {
        final columns = c.maxWidth >= 760 ? 4 : 2;
        const gap = 12.0;
        final width = (c.maxWidth - gap * (columns - 1)) / columns;
        return Wrap(spacing: gap, runSpacing: gap, children: [
          for (final child in children) SizedBox(width: width, child: child),
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
