import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'tasks_screen.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';

/// Approvals as a designed surface: risk first, the exact action, why it was
/// stopped, and which task asked. Approving always needs a deliberate confirm.
class ApprovalsScreen extends StatelessWidget {
  const ApprovalsScreen({required this.controller, super.key});
  final WorkspaceController controller;

  int _riskRank(String risk) => switch (risk.toLowerCase()) {
        'blocked' || 'critical' => 0,
        'high' => 1,
        'medium' => 2,
        _ => 3,
      };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final p = context.palette;
          final pending = [...controller.pendingApprovals]..sort((a, b) {
              final byRisk = _riskRank(a.risk).compareTo(_riskRank(b.risk));
              return byRisk != 0 ? byRisk : a.createdAt.compareTo(b.createdAt);
            });
          final decided = controller.approvals
              .where((a) => a.status != 'pending')
              .toList()
            ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
          final approved = decided.where((a) => a.status == 'approved').length;
          final rejected = decided.where((a) => a.status == 'rejected').length;

          return AnumPage(
            onRefresh: controller.load,
            children: [
              const AnumHeader(
                eyebrow: 'Governance',
                title: 'Approvals',
                subtitle:
                    'Agents stop and ask before risky actions. Highest risk first.',
                large: true,
              ),
              AnumMetricGrid(children: [
                AnumMetric(
                  label: 'Waiting',
                  value: '${pending.length}',
                  icon: Icons.front_hand_outlined,
                  accent: pending.isEmpty ? null : p.warn,
                ),
                AnumMetric(
                    label: 'Approved',
                    value: '$approved',
                    icon: Icons.check_circle_outline),
                AnumMetric(
                    label: 'Rejected', value: '$rejected', icon: Icons.block),
                AnumMetric(
                  label: 'Total',
                  value: '${controller.approvals.length}',
                  icon: Icons.fact_check_outlined,
                ),
              ]),
              if (pending.isEmpty)
                const AnumSurface(
                  child: AnumEmpty(
                    icon: Icons.verified_outlined,
                    title: 'Nothing is waiting for you',
                    message:
                        'When an agent wants to do something risky, it will appear here first.',
                  ),
                )
              else
                for (final approval in pending)
                  _PendingCard(approval: approval, controller: controller),
              if (decided.isNotEmpty)
                AnumSurface(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const AnumHeader(
                            eyebrow: 'History', title: 'Decisions'),
                        const SizedBox(height: 6),
                        for (final approval in decided.take(20))
                          AnumRow(
                            icon: approval.status == 'approved'
                                ? Icons.check
                                : Icons.block,
                            tone: approval.status == 'approved'
                                ? AnumTone.ok
                                : AnumTone.stop,
                            title: approvalTitle(approval, controller.tasks),
                            subtitle: approvalReason(approval,
                                approvalTitle(approval, controller.tasks)),
                            detail:
                                '${approval.status[0].toUpperCase()}${approval.status.substring(1)} · ${exactTime(approval.createdAt)}',
                            trailing: AnumPill(
                                label: approval.risk,
                                tone: riskTone(approval.risk)),
                          ),
                      ]),
                ),
              const SizedBox(height: AnumSpacing.lg),
            ],
          );
        },
      );
}

class _PendingCard extends StatelessWidget {
  const _PendingCard({required this.approval, required this.controller});
  final WorkspaceApproval approval;
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final tone = riskTone(approval.risk);
    final task =
        controller.tasks.where((t) => t.id == approval.taskId).firstOrNull;
    return AnumSurface(
      accent: anumToneColor(context, tone),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          AnumPill(
              label: '${approval.risk} risk',
              tone: tone,
              icon: Icons.shield_outlined),
          const Spacer(),
          Text(relativeTime(approval.createdAt),
              style: TextStyle(color: p.faint, fontSize: 12)),
        ]),
        const SizedBox(height: 10),
        Text(approvalTitle(approval, controller.tasks),
            style: TextStyle(
                color: p.text, fontSize: 17, fontWeight: FontWeight.w700)),
        const SizedBox(height: 2),
        Text('Tool: ${approval.action}',
            style: TextStyle(color: p.faint, fontSize: 12)),
        const SizedBox(height: 6),
        Text('Why it stopped', style: TextStyle(color: p.faint, fontSize: 12)),
        const SizedBox(height: 2),
        Text(
            approvalReason(approval, approvalTitle(approval, controller.tasks)),
            style: TextStyle(color: p.muted, fontSize: 14)),
        if (task != null) ...[
          const SizedBox(height: 10),
          InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: () => openTaskDetail(context, controller, task),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(children: [
                Icon(Icons.task_alt, size: 16, color: p.sky),
                const SizedBox(width: 6),
                Expanded(
                  child: Text('From task: ${task.title}',
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          color: p.sky,
                          fontSize: 13,
                          fontWeight: FontWeight.w600)),
                ),
              ]),
            ),
          ),
        ],
        const SizedBox(height: 12),
        Row(children: [
          Expanded(
            child: OutlinedButton.icon(
              onPressed: controller.mutating
                  ? null
                  : () => controller.decide(approval.id, approve: false),
              icon: const Icon(Icons.close),
              label: const Text('Reject'),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: FilledButton.icon(
              onPressed:
                  controller.mutating ? null : () => _confirmApprove(context),
              icon: const Icon(Icons.check),
              label: const Text('Approve'),
            ),
          ),
        ]),
      ]),
    );
  }

  Future<void> _confirmApprove(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Approve this action?'),
        content: Text(
            'The agent will now: ${approvalTitle(approval, controller.tasks)}\n\nRisk: ${approval.risk}. This is recorded in the audit log.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Not yet')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Approve')),
        ],
      ),
    );
    if (ok == true) await controller.decide(approval.id, approve: true);
  }
}
