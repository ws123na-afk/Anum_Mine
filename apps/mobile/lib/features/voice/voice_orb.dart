import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'voice_models.dart';

const orbSky = Color(0xFF7DD3FC);
const orbViolet = Color(0xFFA78BFA);
const orbRose = Color(0xFFF9A8D4);
const orbAmber = Color(0xFFFFB547);
const _orbDeep = Color(0xFF1B1446);

/// A lit, slowly swirling sphere that shows what the assistant is doing.
/// Painted with layered gradients, so it needs no 3D engine or assets.
class VoiceOrb extends StatefulWidget {
  const VoiceOrb({
    required this.state,
    required this.level,
    this.alert = false,
    this.size = 200,
    this.active = true,
    super.key,
  });

  final VoiceState state;

  /// Microphone level, 0..1.
  final ValueListenable<double> level;

  /// Approvals are waiting: the rim and glow turn amber.
  final bool alert;
  final double size;

  /// False while the screen is hidden, to stop animating off screen.
  final bool active;

  @override
  State<VoiceOrb> createState() => _VoiceOrbState();
}

class _VoiceOrbState extends State<VoiceOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _clock =
      AnimationController(vsync: this, duration: const Duration(seconds: 12));
  bool _reduceMotion = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    _syncClock();
  }

  @override
  void didUpdateWidget(VoiceOrb oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncClock();
  }

  void _syncClock() {
    final run = widget.active && !_reduceMotion;
    if (run && !_clock.isAnimating) {
      _clock.repeat();
    } else if (!run && _clock.isAnimating) {
      _clock.stop();
    }
  }

  @override
  void dispose() {
    _clock.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox.square(
        dimension: widget.size,
        child: RepaintBoundary(
          child: AnimatedBuilder(
            animation: Listenable.merge([_clock, widget.level]),
            builder: (context, _) => CustomPaint(
              painter: OrbPainter(
                time: _reduceMotion ? 0.15 : _clock.value,
                state: widget.state,
                level: _reduceMotion ? 0 : widget.level.value,
                alert: widget.alert,
                animate: !_reduceMotion,
              ),
            ),
          ),
        ),
      );
}

class OrbPainter extends CustomPainter {
  OrbPainter({
    required this.time,
    required this.state,
    required this.level,
    required this.alert,
    this.animate = true,
  });

  /// 0..1, loops.
  final double time;
  final VoiceState state;
  final double level;
  final bool alert;
  final bool animate;

  double get _spin => switch (state) {
        VoiceState.thinking => 4,
        VoiceState.speaking => 2,
        VoiceState.listening => 1.5,
        VoiceState.idle => 1,
      };

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final angle = time * math.pi * 2;
    final wave = math.sin(angle * 6);

    var scale = switch (state) {
      VoiceState.idle => 1 + 0.015 * math.sin(angle * 3),
      VoiceState.listening => 1 + level * 0.12,
      VoiceState.thinking => 1 + 0.02 * math.sin(angle * 10),
      VoiceState.speaking => 1 + 0.035 * (0.5 + 0.5 * math.sin(angle * 14)),
    };
    if (!animate) scale = 1;
    final radius = size.shortestSide * 0.36 * scale;
    final rim = alert ? orbAmber : Color.lerp(orbSky, orbViolet, 0.5)!;
    final sphere = Rect.fromCircle(center: center, radius: radius);

    // 1. Soft glow behind the sphere.
    final glowStrength = switch (state) {
      VoiceState.idle => 0.32,
      VoiceState.listening => 0.42 + level * 0.3,
      VoiceState.thinking => 0.4 + 0.12 * wave,
      VoiceState.speaking => 0.5,
    };
    final glowRadius = radius * 1.45;
    canvas.drawCircle(
      center,
      glowRadius,
      Paint()
        ..shader = RadialGradient(colors: [
          rim.withValues(alpha: glowStrength),
          rim.withValues(alpha: glowStrength * 0.35),
          rim.withValues(alpha: 0),
        ], stops: const [
          0.55,
          0.75,
          1
        ]).createShader(Rect.fromCircle(center: center, radius: glowRadius)),
    );

    // 2. Body: lit from the upper left so it reads as a sphere.
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-0.35, -0.45),
          radius: 1.1,
          colors: [Color(0xFFE0F2FE), orbSky, orbViolet, _orbDeep],
          stops: [0, 0.3, 0.68, 1],
        ).createShader(sphere),
    );

    canvas.save();
    canvas.clipPath(Path()..addOval(sphere));

    // 3. Swirling colour bands inside the sphere.
    final spin = angle * _spin;
    canvas.save();
    canvas.translate(center.dx, center.dy);
    canvas.rotate(spin);
    canvas.translate(-center.dx, -center.dy);
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..blendMode = BlendMode.softLight
        ..shader = SweepGradient(colors: [
          orbSky.withValues(alpha: 0.9),
          orbViolet.withValues(alpha: 0.9),
          orbRose.withValues(alpha: 0.9),
          orbSky.withValues(alpha: 0.9),
        ]).createShader(sphere),
    );
    canvas.restore();

    // Drifting colour blobs, like light moving through glass.
    final blobs = [
      (orbRose, 0.0, 0.45),
      (orbSky, 2.1, 0.5),
      (orbViolet, 4.2, 0.4),
    ];
    for (final (color, phase, size) in blobs) {
      final a = spin * 0.7 + phase;
      final offset = Offset(math.cos(a), math.sin(a * 1.3)) * radius * 0.42;
      final blobRadius = radius * size;
      final blobCenter = center + offset;
      canvas.drawCircle(
        blobCenter,
        blobRadius,
        Paint()
          ..blendMode = BlendMode.screen
          ..shader = RadialGradient(colors: [
            color.withValues(alpha: 0.55),
            color.withValues(alpha: 0),
          ]).createShader(
              Rect.fromCircle(center: blobCenter, radius: blobRadius)),
      );
    }

    // 4. Shadow on the far side: limb darkening sells the depth.
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.3, -0.35),
          radius: 1.05,
          colors: [
            Colors.transparent,
            Colors.transparent,
            _orbDeep.withValues(alpha: 0.75),
          ],
          stops: const [0, 0.55, 1],
        ).createShader(sphere),
    );

    // Coloured bounce light along the lower rim.
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(0.2, 1.0),
          radius: 0.7,
          colors: [rim.withValues(alpha: 0.45), rim.withValues(alpha: 0)],
        ).createShader(sphere),
    );
    canvas.restore();

    // 5. Rim light.
    final rimPulse = state == VoiceState.thinking ? 0.6 + 0.3 * wave : 0.7;
    canvas.drawCircle(
      center,
      radius - 0.75,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5
        ..shader = SweepGradient(
          transform: GradientRotation(spin * 0.5),
          colors: [
            rim.withValues(alpha: rimPulse),
            Colors.white.withValues(alpha: 0.15),
            rim.withValues(alpha: rimPulse),
          ],
        ).createShader(sphere),
    );

    // 6. Specular highlight, plus a faint secondary one.
    final highlight = Rect.fromCenter(
      center: center + Offset(-radius * 0.34, -radius * 0.42),
      width: radius * 0.62,
      height: radius * 0.4,
    );
    canvas.save();
    canvas.translate(highlight.center.dx, highlight.center.dy);
    canvas.rotate(-0.5);
    canvas.translate(-highlight.center.dx, -highlight.center.dy);
    canvas.drawOval(
      highlight,
      Paint()
        ..shader = RadialGradient(colors: [
          Colors.white.withValues(alpha: 0.85),
          Colors.white.withValues(alpha: 0),
        ]).createShader(highlight),
    );
    canvas.restore();
    final glint = Rect.fromCircle(
        center: center + Offset(radius * 0.42, radius * 0.48),
        radius: radius * 0.12);
    canvas.drawOval(
      glint,
      Paint()
        ..shader = RadialGradient(colors: [
          Colors.white.withValues(alpha: 0.25),
          Colors.white.withValues(alpha: 0),
        ]).createShader(glint),
    );
  }

  @override
  bool shouldRepaint(OrbPainter old) =>
      old.time != time ||
      old.state != state ||
      old.level != level ||
      old.alert != alert ||
      old.animate != animate;
}
