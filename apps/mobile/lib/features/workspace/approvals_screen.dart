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
              .where((a) => a.effectiveStatus() != 'pending')
              .toList()
            ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
          final approved =
              decided.where((a) => a.effectiveStatus() == 'approved').length;
          final rejected =
              decided.where((a) => a.effectiveStatus() == 'rejected').length;

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
                            icon: approval.effectiveStatus() == 'approved'
                                ? Icons.check
                                : Icons.block,
                            tone: approval.effectiveStatus() == 'approved'
                                ? AnumTone.ok
                                : AnumTone.stop,
                            title: approvalTitle(approval, controller.tasks),
                            subtitle: 'Tool: ${approval.action}',
                            detail: decisionSummary(approval),
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
        const SizedBox(height: 8),
        Text('Exactly what it will send',
            style: TextStyle(color: p.faint, fontSize: 12)),
        const SizedBox(height: 4),
        ApprovalArgumentsTable(approval: approval),
        const SizedBox(height: 6),
        Text(
            'Payload hash ${shortHash(approval.payloadHash)}'
            '${expiryLabel(approval).isEmpty ? '' : ' · ${expiryLabel(approval)}'}',
            style: TextStyle(color: p.faint, fontSize: 12)),
        if (approval.payloadHash == null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
                'Not bound to a payload hash; it cannot be approved. Run the task again.',
                style: TextStyle(color: p.stop, fontSize: 12)),
          ),
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
                  : () => controller.decide(approval, approve: false),
              icon: const Icon(Icons.close),
              label: const Text('Reject'),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: FilledButton.icon(
              onPressed: controller.mutating || !approval.canApprove()
                  ? null
                  : () => _confirmApprove(context),
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
            'The agent will now call ${approval.action} with the arguments shown '
            '(payload hash ${shortHash(approval.payloadHash)}).\n\n'
            'Risk: ${approval.risk}. If the call changes before it runs, it is '
            'not executed. This is recorded in the audit log.'),
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
    if (ok == true) await controller.decide(approval, approve: true);
  }
}

/// The exact tool arguments as key/value rows; redacted values are marked.
class ApprovalArgumentsTable extends StatelessWidget {
  const ApprovalArgumentsTable({required this.approval, super.key});
  final WorkspaceApproval approval;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final rows = approvalArguments(approval.arguments);
    if (rows.isEmpty) {
      return Text('No arguments.',
          style: TextStyle(color: p.muted, fontSize: 13));
    }
    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: p.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(children: [
        for (final (index, row) in rows.indexed)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: index == 0
                ? null
                : BoxDecoration(border: Border(top: BorderSide(color: p.line))),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(row.key,
                  style: TextStyle(
                      color: p.faint, fontSize: 11, fontFamily: 'monospace')),
              const SizedBox(height: 2),
              SelectableText(row.value,
                  style: TextStyle(
                      color: row.redacted ? p.warn : p.text,
                      fontSize: 13,
                      fontFamily: 'monospace')),
            ]),
          ),
      ]),
    );
  }
}

/// Short form of the payload hash for display.
String shortHash(String? hash) => hash == null || hash.length < 18
    ? (hash ?? 'not bound')
    : '${hash.substring(0, 12)}…${hash.substring(hash.length - 6)}';

/// "Expires in 3 h" for a pending approval, "Expired" once it lapsed.
String expiryLabel(WorkspaceApproval approval, {DateTime? now}) {
  final expiresAt = approval.expiresAt;
  if (approval.status != 'pending' || expiresAt == null) return '';
  final remaining = expiresAt.difference(now ?? DateTime.now());
  if (remaining <= Duration.zero) return 'Expired';
  if (remaining.inMinutes < 60) {
    return 'Expires in ${remaining.inMinutes + 1} min';
  }
  return remaining.inHours < 48
      ? 'Expires in ${remaining.inHours} h'
      : 'Expires in ${remaining.inDays} d';
}

/// History line: who decided and when, or how it ended without a decision.
String decisionSummary(WorkspaceApproval approval, {DateTime? now}) {
  final status = approval.effectiveStatus(now);
  final at = approval.decidedAt;
  if (status == 'approved' || status == 'rejected') {
    final verb = status == 'approved' ? 'Approved' : 'Rejected';
    return '$verb by ${approval.decidedBy ?? 'unknown user'}'
        '${at == null ? '' : ' · ${exactTime(at)}'}';
  }
  if (status == 'expired') {
    final when = at ?? approval.expiresAt;
    return 'Expired without a decision'
        '${when == null ? '' : ' · ${exactTime(when)}'}';
  }
  return '';
}
