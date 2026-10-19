import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';

enum AnumStatus { running, approval, completed, failed, queued, paused }

class AnumStatusBadge extends StatelessWidget {
  const AnumStatusBadge({required this.status, super.key});

  final AnumStatus status;

  @override
  Widget build(BuildContext context) {
    // Status colours come from the palette, so they keep contrast in both
    // themes; the label always says the status too.
    final p = context.palette;
    final (label, color) = switch (status) {
      AnumStatus.running => ('Running', p.sky),
      AnumStatus.approval => ('Approval required', p.warn),
      AnumStatus.completed => ('Completed', p.ok),
      AnumStatus.failed => ('Failed', p.stop),
      AnumStatus.queued => ('Queued', p.violet),
      AnumStatus.paused => ('Paused', p.muted),
    };
    return Semantics(
      container: true,
      label: 'Status: $label',
      excludeSemantics: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: Color.alphaBlend(color.withValues(alpha: 0.12), p.surface1),
          borderRadius: BorderRadius.circular(AnumRadius.pill),
          border: Border.all(color: color.withValues(alpha: 0.24)),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          child: Text(label,
              style: TextStyle(color: color, fontWeight: FontWeight.w600)),
        ),
      ),
    );
  }
}

class AnumOperationalCard extends StatelessWidget {
  const AnumOperationalCard({
    required this.title,
    required this.metadata,
    this.status,
    this.onTap,
    super.key,
  });

  final String title;
  final String metadata;
  final AnumStatus? status;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Card(
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(AnumRadius.lg),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 88),
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.md),
              child: LayoutBuilder(
                builder: (context, constraints) {
                  final details = Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          style: Theme.of(context).textTheme.titleSmall),
                      const SizedBox(height: AnumSpacing.xs),
                      Text(metadata,
                          style: Theme.of(context).textTheme.bodySmall),
                    ],
                  );
                  if (status == null) return details;
                  if (constraints.maxWidth < 380) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        details,
                        const SizedBox(height: AnumSpacing.sm),
                        AnumStatusBadge(status: status!)
                      ],
                    );
                  }
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(child: details),
                      const SizedBox(width: AnumSpacing.sm),
                      AnumStatusBadge(status: status!)
                    ],
                  );
                },
              ),
            ),
          ),
        ),
      );
}

enum AnumFeedbackKind { loading, empty, error, offline, permission }

/// A page or panel state: loading (a skeleton with the message), empty,
/// error, offline or permission denied (an icon, the message and Retry).
class AnumFeedback extends StatelessWidget {
  const AnumFeedback(
      {required this.kind, required this.message, this.onRetry, super.key});

  final AnumFeedbackKind kind;
  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final icon = switch (kind) {
      AnumFeedbackKind.loading => Icons.hourglass_top,
      AnumFeedbackKind.empty => Icons.inbox_outlined,
      AnumFeedbackKind.error => Icons.error_outline,
      AnumFeedbackKind.offline => Icons.cloud_off_outlined,
      AnumFeedbackKind.permission => Icons.lock_outline,
    };
    final tone = switch (kind) {
      AnumFeedbackKind.error => p.stop,
      AnumFeedbackKind.permission || AnumFeedbackKind.offline => p.warn,
      _ => p.muted,
    };
    if (kind == AnumFeedbackKind.loading) {
      return Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 360),
          child: Padding(
            padding: const EdgeInsets.all(AnumSpacing.lg),
            child: Semantics(
              liveRegion: true,
              label: message,
              excludeSemantics: true,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const AnumSkeleton(lines: 3),
                  const SizedBox(height: AnumSpacing.md),
                  Text(message,
                      textAlign: TextAlign.center,
                      style: TextStyle(color: p.muted, fontSize: 13.5)),
                ],
              ),
            ),
          ),
        ),
      );
    }
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 360),
        child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                    color: p.surface3,
                    borderRadius: BorderRadius.circular(AnumRadius.lg),
                    border: Border.all(color: p.line)),
                child: Icon(icon, size: 24, color: tone),
              ),
              const SizedBox(height: AnumSpacing.md),
              Text(message,
                  textAlign: TextAlign.center,
                  style: TextStyle(color: p.text, fontSize: 14.5, height: 1.4)),
              if (onRetry != null) ...[
                const SizedBox(height: AnumSpacing.md),
                OutlinedButton.icon(
                  onPressed: onRetry,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// Placeholder bars for content that is loading: a soft highlight sweeps
/// across them (a still placeholder when motion is reduced). Decorative:
/// the caller says what is loading in text or semantics.
class AnumSkeleton extends StatefulWidget {
  const AnumSkeleton({this.lines = 3, this.lineHeight = 12, super.key});

  /// Number of bars; the first is shorter, like a title.
  final int lines;
  final double lineHeight;

  @override
  State<AnumSkeleton> createState() => _AnumSkeletonState();
}

class _AnumSkeletonState extends State<AnumSkeleton>
    with SingleTickerProviderStateMixin {
  late final AnimationController _sweep = AnimationController(
      vsync: this, duration: const Duration(milliseconds: 1400));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduce = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    if (reduce) {
      _sweep.stop();
    } else if (!_sweep.isAnimating) {
      _sweep.repeat();
    }
  }

  @override
  void dispose() {
    _sweep.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final bars = ExcludeSemantics(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < widget.lines; i++) ...[
            if (i > 0) const SizedBox(height: AnumSpacing.xs),
            FractionallySizedBox(
              widthFactor: i == 0 ? 0.55 : (i.isOdd ? 1 : 0.8),
              child: Container(
                height: widget.lineHeight,
                decoration: BoxDecoration(
                    color: p.surface3,
                    borderRadius: BorderRadius.circular(AnumRadius.sm)),
              ),
            ),
          ],
        ],
      ),
    );
    return AnimatedBuilder(
      animation: _sweep,
      child: bars,
      builder: (context, child) {
        if (!_sweep.isAnimating) return child!;
        final x = _sweep.value * 3 - 1.5;
        return ShaderMask(
          blendMode: BlendMode.srcATop,
          shaderCallback: (rect) => LinearGradient(
            begin: Alignment(x - 0.6, 0),
            end: Alignment(x + 0.6, 0),
            colors: [p.surface3, p.lineStrong, p.surface3],
          ).createShader(rect),
          child: child,
        );
      },
    );
  }
}
