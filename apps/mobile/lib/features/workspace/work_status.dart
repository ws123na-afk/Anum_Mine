import 'package:flutter/material.dart';

import '../../src/widgets/depth.dart';
import 'workspace_models.dart';

String workStatusLabel(WorkStatus status) => switch (status) {
      WorkStatus.created => 'Created',
      WorkStatus.queued => 'Queued',
      WorkStatus.running => 'Running',
      WorkStatus.waitingApproval => 'Needs approval',
      WorkStatus.completed => 'Completed',
      WorkStatus.failed => 'Failed',
      WorkStatus.cancelled => 'Cancelled',
    };

AnumTone workStatusTone(WorkStatus status) => switch (status) {
      WorkStatus.created || WorkStatus.queued => AnumTone.neutral,
      WorkStatus.running => AnumTone.info,
      WorkStatus.waitingApproval => AnumTone.warn,
      WorkStatus.completed => AnumTone.ok,
      WorkStatus.failed => AnumTone.stop,
      WorkStatus.cancelled => AnumTone.neutral,
    };

IconData workStatusIcon(WorkStatus status) => switch (status) {
      WorkStatus.created || WorkStatus.queued => Icons.schedule,
      WorkStatus.running => Icons.play_circle_outline,
      WorkStatus.waitingApproval => Icons.front_hand_outlined,
      WorkStatus.completed => Icons.check_circle_outline,
      WorkStatus.failed => Icons.error_outline,
      WorkStatus.cancelled => Icons.cancel_outlined,
    };

/// The status pill, with a spoken label for screen readers.
class WorkStatusPill extends StatelessWidget {
  const WorkStatusPill(this.status, {super.key});
  final WorkStatus status;

  @override
  Widget build(BuildContext context) => Semantics(
        container: true,
        label: 'Status: ${workStatusLabel(status)}',
        excludeSemantics: true,
        child: AnumPill(
            label: workStatusLabel(status), tone: workStatusTone(status)),
      );
}

AnumTone riskTone(String risk) => switch (risk.toLowerCase()) {
      'high' || 'blocked' || 'critical' => AnumTone.stop,
      'medium' => AnumTone.warn,
      _ => AnumTone.ok,
    };

String formatBytes(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
  return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
}

/// Integration health as the API reports it: connected, degraded, configured, disabled.
AnumTone integrationTone(String status) => switch (status) {
      'connected' => AnumTone.ok,
      'degraded' => AnumTone.warn,
      'configured' => AnumTone.info,
      _ => AnumTone.neutral,
    };

String integrationLabel(String status) => switch (status) {
      'connected' => 'Connected',
      'degraded' => 'Degraded',
      'configured' => 'Configured, not checked',
      'disabled' => 'Not enabled',
      _ => status,
    };
