import 'package:flutter/material.dart';

import '../theme/anum_theme.dart';

enum AnumStatus { running, approval, completed, failed, queued, paused }

class AnumStatusBadge extends StatelessWidget {
  const AnumStatusBadge({required this.status, super.key});

  final AnumStatus status;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      AnumStatus.running => ('Running', const Color(0xFF087F73)),
      AnumStatus.approval => ('Approval required', const Color(0xFF8A5A00)),
      AnumStatus.completed => ('Completed', const Color(0xFF067647)),
      AnumStatus.failed => ('Failed', Theme.of(context).colorScheme.error),
      AnumStatus.queued => ('Queued', Theme.of(context).colorScheme.secondary),
      AnumStatus.paused => ('Paused', Theme.of(context).colorScheme.outline),
    };
    return Semantics(
      label: 'Status: $label',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.w600)),
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
          borderRadius: BorderRadius.circular(8),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 88),
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.md),
              child: LayoutBuilder(
                builder: (context, constraints) {
                  final details = Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title, style: Theme.of(context).textTheme.titleSmall),
                        const SizedBox(height: AnumSpacing.xs),
                        Text(metadata, style: Theme.of(context).textTheme.bodySmall),
                      ],
                    );
                  if (status == null) return details;
                  if (constraints.maxWidth < 380) {
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [details, const SizedBox(height: AnumSpacing.sm), AnumStatusBadge(status: status!)],
                    );
                  }
                  return Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [Expanded(child: details), const SizedBox(width: AnumSpacing.sm), AnumStatusBadge(status: status!)],
                  );
                },
              ),
            ),
          ),
        ),
      );
}

enum AnumFeedbackKind { loading, empty, error, offline, permission }

class AnumFeedback extends StatelessWidget {
  const AnumFeedback({required this.kind, required this.message, this.onRetry, super.key});

  final AnumFeedbackKind kind;
  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final icon = switch (kind) {
      AnumFeedbackKind.loading => Icons.hourglass_top,
      AnumFeedbackKind.empty => Icons.inbox_outlined,
      AnumFeedbackKind.error => Icons.error_outline,
      AnumFeedbackKind.offline => Icons.cloud_off_outlined,
      AnumFeedbackKind.permission => Icons.lock_outline,
    };
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 360),
        child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (kind == AnumFeedbackKind.loading)
                const CircularProgressIndicator()
              else
                Icon(icon, size: 40),
              const SizedBox(height: AnumSpacing.md),
              Text(message, textAlign: TextAlign.center),
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
