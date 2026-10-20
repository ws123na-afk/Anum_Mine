import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';

/// The ANUM 360 mark: two joined peaks (an "A" and an "M" read together),
/// drawn as vector paths with the accent gradient. No image assets.
class AnumLogoMark extends StatelessWidget {
  const AnumLogoMark({this.size = 40, this.semanticLabel, super.key});

  /// Width of the mark; the height is 70 percent of it.
  final double size;

  /// Spoken name; null when a visible "ANUM 360" label sits next to it.
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final mark = CustomPaint(
      size: Size(size, size * 0.7),
      painter: AnumLogoPainter(
          start: p.brandStart,
          middle: p.brandMiddle,
          end: p.brandEnd,
          highlight: p.brandEnd),
    );
    return semanticLabel == null
        ? ExcludeSemantics(child: mark)
        : Semantics(label: semanticLabel, image: true, child: mark);
  }
}

class AnumLogoPainter extends CustomPainter {
  const AnumLogoPainter({
    required this.start,
    required this.middle,
    required this.end,
    required this.highlight,
  });

  final Color start, middle, end, highlight;

  /// The outer "M" stroke and the inner "A" chevron, in a 100 x 70 box.
  static Path outer(Size size) {
    final sx = size.width / 100, sy = size.height / 70;
    return Path()
      ..moveTo(6 * sx, 64 * sy)
      ..lineTo(30 * sx, 8 * sy)
      ..lineTo(50 * sx, 46 * sy)
      ..lineTo(70 * sx, 8 * sy)
      ..lineTo(94 * sx, 64 * sy);
  }

  static Path inner(Size size) {
    final sx = size.width / 100, sy = size.height / 70;
    return Path()
      ..moveTo(24 * sx, 64 * sy)
      ..lineTo(50 * sx, 18 * sy)
      ..lineTo(76 * sx, 64 * sy);
  }

  @override
  void paint(Canvas canvas, Size size) {
    final bounds = Offset.zero & size;
    final shader = LinearGradient(colors: [start, middle, end, highlight])
        .createShader(bounds);
    canvas.drawPath(
        outer(size),
        Paint()
          ..shader = shader
          ..style = PaintingStyle.stroke
          ..strokeWidth = size.height * 0.15
          ..strokeCap = StrokeCap.round
          ..strokeJoin = StrokeJoin.round);
    canvas.drawPath(
        inner(size),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = size.height * 0.07
          ..strokeCap = StrokeCap.round
          ..strokeJoin = StrokeJoin.round
          ..color =
              Color.lerp(start, Colors.white, 0.7)!.withValues(alpha: 0.9));
  }

  @override
  bool shouldRepaint(AnumLogoPainter old) =>
      old.start != start ||
      old.middle != middle ||
      old.end != end ||
      old.highlight != highlight;
}

/// The mark with "ANUM 360" and an optional tagline under it.
class AnumWordmark extends StatelessWidget {
  const AnumWordmark({
    this.markSize = 40,
    this.tagline,
    this.vertical = false,
    this.title = 'ANUM 360',
    this.showMark = true,
    super.key,
  });

  /// False when an orb above already shows the mark.
  final bool showMark;
  final double markSize;
  final String? tagline;
  final bool vertical;
  final String title;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final words = Column(
      crossAxisAlignment:
          vertical ? CrossAxisAlignment.center : CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(title,
            textAlign: vertical ? TextAlign.center : TextAlign.start,
            style: (vertical ? text.headlineMedium : text.titleLarge)
                ?.copyWith(fontWeight: FontWeight.w800, color: p.text)),
        if (tagline != null)
          Text(tagline!,
              textAlign: vertical ? TextAlign.center : TextAlign.start,
              style: text.bodyMedium?.copyWith(color: p.muted)),
      ],
    );
    return Semantics(
      container: true,
      header: true,
      child: vertical
          ? Column(mainAxisSize: MainAxisSize.min, children: [
              if (showMark) ...[
                AnumLogoMark(size: markSize),
                const SizedBox(height: AnumSpacing.md),
              ],
              words,
            ])
          : Row(mainAxisSize: MainAxisSize.min, children: [
              if (showMark) ...[
                AnumLogoMark(size: markSize),
                const SizedBox(width: AnumSpacing.sm),
              ],
              Flexible(child: words),
            ]),
    );
  }
}

/// The ANUM orb: the mark over a waveform inside a gradient ring with an
/// aurora glow. It animates only while [active] and the platform allows
/// motion; reduced motion shows a still waveform. [level] (0..1, e.g. the
/// microphone) scales the waveform when given.
class AnumOrb extends StatefulWidget {
  const AnumOrb({
    this.size = 160,
    this.active = false,
    this.level,
    this.showMark = true,
    this.semanticLabel,
    super.key,
  });

  final double size;
  final bool active;
  final ValueListenable<double>? level;
  final bool showMark;

  /// What the orb means where it stands ("ANUM is listening"); null keeps it
  /// out of the semantics tree (a labelled button around it speaks instead).
  final String? semanticLabel;

  @override
  State<AnumOrb> createState() => _AnumOrbState();
}

class _AnumOrbState extends State<AnumOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _clock =
      AnimationController(vsync: this, duration: AnumMotion.orbCycle);
  bool _reduceMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    _sync();
  }

  @override
  void didUpdateWidget(AnumOrb oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync();
  }

  bool get running => widget.active && !_reduceMotion;

  void _sync() {
    if (running && !_clock.isAnimating) {
      _clock.repeat();
    } else if (!running && _clock.isAnimating) {
      _clock.stop();
    }
  }

  @override
  void dispose() {
    _clock.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final level = widget.level;
    final orb = SizedBox.square(
      dimension: widget.size,
      child: RepaintBoundary(
        child: AnimatedBuilder(
          animation: Listenable.merge([_clock, if (level != null) level]),
          builder: (context, child) => CustomPaint(
            painter: AnumOrbPainter(
              phase: running ? _clock.value : 0.2,
              level: running ? (level?.value ?? 0.55) : 0.35,
              ringStart: p.brandStart,
              ringMiddle: p.brandMiddle,
              ringEnd: p.brandEnd,
              glow: p.glow,
              core: p.background,
              wave: p.brandStart,
              waveEnd: p.brandEnd,
            ),
            child: child,
          ),
          child: widget.showMark
              ? Align(
                  alignment: const Alignment(0, -0.38),
                  child: AnumLogoMark(size: widget.size * 0.34),
                )
              : null,
        ),
      ),
    );
    final label = widget.semanticLabel;
    return label == null
        ? ExcludeSemantics(child: orb)
        : Semantics(
            label: label, image: true, child: ExcludeSemantics(child: orb));
  }
}

class AnumOrbPainter extends CustomPainter {
  const AnumOrbPainter({
    required this.phase,
    required this.level,
    required this.ringStart,
    required this.ringMiddle,
    required this.ringEnd,
    required this.glow,
    required this.core,
    required this.wave,
    required this.waveEnd,
  });

  /// 0..1, loops.
  final double phase;

  /// 0..1, waveform amplitude.
  final double level;
  final Color ringStart, ringMiddle, ringEnd, glow, core, wave, waveEnd;

  /// Heights (0..1) of the waveform's bars at [phase] and [level]: tallest in
  /// the middle, symmetric, never zero so the wave stays visible.
  static List<double> bars(double phase, double level, {int count = 15}) {
    final out = <double>[];
    final mid = (count - 1) / 2;
    for (var i = 0; i < count; i++) {
      final distance = (i - mid).abs() / mid;
      final envelope = math.cos(distance * math.pi / 2);
      final motion =
          0.55 + 0.45 * math.sin(phase * math.pi * 2 * 2 + i * 0.9).abs();
      out.add((0.12 + 0.88 * envelope * motion * (0.35 + 0.65 * level))
          .clamp(0.08, 1.0));
    }
    return out;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.shortestSide / 2;
    final ring = radius * 0.78;

    // Aurora glow behind the orb.
    canvas.drawCircle(
        center,
        radius,
        Paint()
          ..shader = RadialGradient(colors: [
            glow.withValues(alpha: 0.55),
            glow.withValues(alpha: 0.18),
            glow.withValues(alpha: 0),
          ], stops: const [
            0.45,
            0.75,
            1
          ]).createShader(Rect.fromCircle(center: center, radius: radius)));

    // Dark core, so the mark and wave keep their contrast.
    final coreRect = Rect.fromCircle(center: center, radius: ring);
    canvas.drawCircle(
        center,
        ring,
        Paint()
          ..shader = RadialGradient(center: const Alignment(0, -0.3), colors: [
            Color.lerp(core, glow, 0.35)!,
            core,
          ]).createShader(coreRect));

    // Gradient ring.
    canvas.drawCircle(
        center,
        ring,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = math.max(2, radius * 0.06)
          ..shader = SweepGradient(
                  transform: GradientRotation(phase * math.pi * 2),
                  colors: [ringStart, ringMiddle, ringEnd, ringStart])
              .createShader(coreRect));

    // Waveform across the lower middle.
    final heights = bars(phase, level);
    final span = ring * 1.2;
    final step = span / heights.length;
    final barWidth = math.max(1.5, step * 0.5);
    final baseY = center.dy + ring * 0.22;
    final maxHeight = ring * 0.62;
    final waveShader = LinearGradient(colors: [wave, waveEnd]).createShader(
        Rect.fromLTWH(
            center.dx - span / 2, baseY - maxHeight / 2, span, maxHeight));
    final paint = Paint()
      ..shader = waveShader
      ..strokeCap = StrokeCap.round
      ..strokeWidth = barWidth;
    for (var i = 0; i < heights.length; i++) {
      final x = center.dx - span / 2 + step * (i + 0.5);
      final h = heights[i] * maxHeight / 2;
      canvas.drawLine(Offset(x, baseY - h), Offset(x, baseY + h), paint);
    }
  }

  @override
  bool shouldRepaint(AnumOrbPainter old) =>
      old.phase != phase ||
      old.level != level ||
      old.ringStart != ringStart ||
      old.glow != glow ||
      old.core != core;
}

/// The primary action: the solid brand accent with white text, 48 px minimum,
/// keyboard and screen reader support from [FilledButton]. Shows a spinner
/// while [busy]; disabled when [onPressed] is null.
class AnumGradientButton extends StatelessWidget {
  const AnumGradientButton({
    required this.label,
    required this.onPressed,
    this.icon,
    this.busy = false,
    this.expand = true,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;
  final bool busy;

  /// Fill the available width (forms); false sizes to the label.
  final bool expand;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final enabled = onPressed != null && !busy;
    final radius = BorderRadius.circular(AnumRadius.md);
    final child = Row(
      mainAxisSize: MainAxisSize.min,
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        if (busy)
          SizedBox.square(
              dimension: 18,
              child:
                  CircularProgressIndicator(strokeWidth: 2, color: p.onAccent))
        else if (icon != null)
          Icon(icon, size: 20),
        if (busy || icon != null) const SizedBox(width: AnumSpacing.xs),
        Flexible(child: Text(label, textAlign: TextAlign.center)),
      ],
    );
    final button = DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: radius,
        gradient: enabled || busy ? p.accentGradient : null,
        color: enabled || busy ? null : p.surface3,
      ),
      child: FilledButton(
        onPressed: enabled ? onPressed : null,
        style: FilledButton.styleFrom(
          backgroundColor: Colors.transparent,
          disabledBackgroundColor: Colors.transparent,
          foregroundColor: p.onAccent,
          disabledForegroundColor: busy ? p.onAccent : p.muted,
          shadowColor: Colors.transparent,
          minimumSize: const Size(48, 52),
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.lg, vertical: AnumSpacing.sm),
          shape: RoundedRectangleBorder(borderRadius: radius),
          textStyle: const TextStyle(
              fontFamily: AnumTheme.fontFamily,
              fontFamilyFallback: AnumTheme.fontFamilyFallback,
              fontWeight: FontWeight.w600,
              fontSize: AnumType.base),
        ),
        child: child,
      ),
    );
    return expand ? SizedBox(width: double.infinity, child: button) : button;
  }
}

/// A translucent raised panel for content laid over the aurora (bottom
/// navigation, plan card, social sign-in). Opaque enough to keep contrast.
class AnumGlassPanel extends StatelessWidget {
  const AnumGlassPanel({
    required this.child,
    this.padding = const EdgeInsets.all(AnumSpacing.md),
    this.radius = AnumRadius.lg,
    this.highlight = false,
    super.key,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;

  /// A faint accent wash, for the one panel that should stand out.
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: highlight ? p.accentSoft : p.glass,
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(color: p.line),
        boxShadow: [
          BoxShadow(color: p.shadow, blurRadius: 16, offset: const Offset(0, 4))
        ],
      ),
      child: Padding(padding: padding, child: child),
    );
  }
}

/// Initials in a gradient ring: the signed-in person (no photo is stored).
class AnumAvatar extends StatelessWidget {
  const AnumAvatar({required this.name, this.size = 44, super.key});
  final String name;
  final double size;

  static String initials(String name) {
    final words = name
        .trim()
        .split(RegExp(r'[\s._@-]+'))
        .where((w) => w.isNotEmpty)
        .toList();
    if (words.isEmpty) return '?';
    final first = words.first.characters.first;
    final second = words.length > 1 ? words[1].characters.first : '';
    return (first + second).toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return ExcludeSemantics(
      child: Container(
        width: size,
        height: size,
        padding: const EdgeInsets.all(2),
        decoration:
            BoxDecoration(shape: BoxShape.circle, gradient: p.accentGradient),
        child: DecoratedBox(
          decoration: BoxDecoration(shape: BoxShape.circle, color: p.surface3),
          child: Center(
            child: Text(initials(name),
                textScaler: TextScaler.noScaling,
                style: TextStyle(
                    color: p.text,
                    fontWeight: FontWeight.w700,
                    fontSize: size * 0.36)),
          ),
        ),
      ),
    );
  }
}

/// The bell with the number of things waiting for the person.
class AnumNotificationBell extends StatelessWidget {
  const AnumNotificationBell({
    required this.count,
    required this.onPressed,
    required this.tooltip,
    super.key,
  });

  final int count;
  final VoidCallback onPressed;

  /// Already says the count ("Notifications, 3 waiting").
  final String tooltip;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return IconButton(
      tooltip: tooltip,
      onPressed: onPressed,
      icon: Badge(
        isLabelVisible: count > 0,
        backgroundColor: p.stop,
        textColor: p.background,
        label: Text(count > 99 ? '99+' : '$count'),
        child: Icon(count > 0
            ? Icons.notifications_active_outlined
            : Icons.notifications_none),
      ),
    );
  }
}
