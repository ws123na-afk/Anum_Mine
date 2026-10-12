import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../commerce/commerce_strings.dart';
import 'destinations.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';

/// The overview: everything here comes from the workspace API. Nothing is
/// invented; empty sections say so and offer the next step.
class HomeScreen extends StatelessWidget {
  const HomeScreen({
    required this.controller,
    required this.onNavigate,
    required this.onOpenTask,
    this.workspaceName,
    this.assistantName = 'Anum',
    this.onOpenCommerce,
    this.header,
    super.key,
  });

  /// Opens Commerce (docs/commerce.md); without it the entry is hidden.
  final VoidCallback? onOpenCommerce;

  /// Replaces the page's own greeting (the shell's header with avatar and
  /// notification bell on phones).
  final Widget? header;

  final WorkspaceController controller;

  /// Switches the main destination: 1 tasks, 2 voice, 3 approvals, 4 automations, 5 resources.
  final ValueChanged<int> onNavigate;
  final ValueChanged<WorkspaceTask> onOpenTask;
  final String? workspaceName;
  final String assistantName;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final tasks = controller.tasks;
    final now = DateTime.now();
    final running = tasks
        .where((t) =>
            t.status == WorkStatus.running || t.status == WorkStatus.queued)
        .toList();
    final pending = controller.pendingApprovals;
    final completedToday = tasks
        .where((t) =>
            t.status == WorkStatus.completed &&
            now.difference(t.updatedAt.toLocal()).inHours < 24)
        .length;
    final failed = tasks.where((t) => t.status == WorkStatus.failed).toList();
    final recent = [...tasks]
      ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    final integrations = controller.integrations;
    final healthy = integrations.where((i) => i.status == 'connected').length;

    return AnumPage(
      onRefresh: controller.load,
      children: [
        if (header != null) header!,
        AnumHeader(
          eyebrow: header == null ? (workspaceName ?? 'Workspace') : null,
          title: header == null ? _greeting(now) : 'Today',
          subtitle: running.isEmpty
              ? 'Nothing is running right now.'
              : '${running.length} ${running.length == 1 ? 'agent is' : 'agents are'} working for you.',
          large: header == null,
        ),
        _AskCard(
          assistantName: assistantName,
          pending: pending.length,
          onTap: () => onNavigate(WorkspaceDestination.voice),
        ),
        if (onOpenCommerce != null)
          AnumSurface(
            onTap: onOpenCommerce,
            semanticLabel: CommerceStrings.of(context).t('entry'),
            child: AnumRow(
              title: CommerceStrings.of(context).t('entry'),
              subtitle: CommerceStrings.of(context).t('entrySubtitle'),
              icon: Icons.storefront_outlined,
              trailing: const Icon(Icons.chevron_right),
            ),
          ),
        AnumMetricGrid(children: [
          AnumMetric(
            label: 'Needs you',
            value: '${pending.length}',
            caption:
                pending.isEmpty ? 'No approvals waiting' : 'Approvals waiting',
            icon: Icons.front_hand_outlined,
            accent: pending.isEmpty ? null : p.warn,
            onTap: () => onNavigate(WorkspaceDestination.approvals),
          ),
          AnumMetric(
            label: 'Running',
            value: '${running.length}',
            caption: 'Queued or in progress',
            icon: Icons.bolt_outlined,
            onTap: () => onNavigate(WorkspaceDestination.tasks),
          ),
          AnumMetric(
            label: 'Done today',
            value: '$completedToday',
            caption: 'Completed in the last 24 h',
            icon: Icons.check_circle_outline,
            onTap: () => onNavigate(WorkspaceDestination.tasks),
          ),
          AnumMetric(
            label: 'Failed',
            value: '${failed.length}',
            caption: failed.isEmpty ? 'Nothing failed' : 'Can be resumed',
            icon: Icons.error_outline,
            accent: failed.isEmpty ? null : p.stop,
            onTap: () => onNavigate(WorkspaceDestination.tasks),
          ),
        ]),
        if (pending.isNotEmpty)
          AnumSurface(
            accent: p.warn,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              AnumHeader(
                eyebrow: 'Needs you',
                title: 'Waiting for your decision',
                trailing: TextButton(
                    onPressed: () => onNavigate(WorkspaceDestination.approvals),
                    child: const Text('Review all')),
              ),
              const SizedBox(height: 6),
              for (final approval in pending.take(3))
                AnumRow(
                  icon: Icons.front_hand_outlined,
                  tone: riskTone(approval.risk),
                  title: approvalTitle(approval, controller.tasks),
                  subtitle: approvalReason(
                      approval, approvalTitle(approval, controller.tasks)),
                  detail: 'Requested ${relativeTime(approval.createdAt)}',
                  trailing: AnumPill(
                      label: '${approval.risk} risk',
                      tone: riskTone(approval.risk)),
                  onTap: () => onNavigate(WorkspaceDestination.approvals),
                ),
            ]),
          ),
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            AnumHeader(
              eyebrow: 'Activity',
              title: 'Recent work',
              trailing: TextButton(
                  onPressed: () => onNavigate(WorkspaceDestination.tasks),
                  child: const Text('All tasks')),
            ),
            const SizedBox(height: 6),
            if (recent.isEmpty)
              AnumEmpty(
                icon: Icons.auto_awesome_outlined,
                title: 'No tasks yet',
                message: 'Ask $assistantName or create a task to get started.',
                action: 'Create a task',
                onAction: () => onNavigate(WorkspaceDestination.tasks),
              )
            else
              for (final task in recent.take(5))
                AnumRow(
                  icon: workStatusIcon(task.status),
                  tone: workStatusTone(task.status),
                  title: task.title,
                  subtitle: task.run?.result ?? task.prompt,
                  detail: 'Updated ${relativeTime(task.updatedAt)}',
                  trailing: WorkStatusPill(task.status),
                  onTap: () => onOpenTask(task),
                ),
          ]),
        ),
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            AnumHeader(
              eyebrow: 'System',
              title: 'Connected services',
              subtitle: integrations.isEmpty
                  ? 'No integrations reported by the API.'
                  : '$healthy of ${integrations.length} connected',
              trailing: TextButton(
                  onPressed: () => onNavigate(WorkspaceDestination.resources),
                  child: const Text('Details')),
            ),
            if (integrations.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(spacing: 8, runSpacing: 8, children: [
                for (final item in integrations)
                  AnumPill(
                    label: item.name,
                    tone: integrationTone(item.status),
                    icon:
                        item.status == 'connected' ? Icons.check : Icons.remove,
                  ),
              ]),
            ],
          ]),
        ),
        const SizedBox(height: AnumSpacing.xl),
      ],
    );
  }

  static String _greeting(DateTime now) {
    final hour = now.hour;
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
  }
}

class _AskCard extends StatelessWidget {
  const _AskCard(
      {required this.assistantName,
      required this.pending,
      required this.onTap});
  final String assistantName;
  final int pending;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      onTap: onTap,
      semanticLabel: 'Talk to $assistantName',
      child: Row(children: [
        Container(
          width: 54,
          height: 54,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(
              center: const Alignment(-0.3, -0.4),
              colors: [
                Colors.white.withValues(alpha: 0.9),
                p.sky,
                p.violet,
                p.rose
              ],
              stops: const [0, 0.25, 0.7, 1],
            ),
            boxShadow: [
              BoxShadow(
                  color:
                      (pending > 0 ? p.warn : p.violet).withValues(alpha: 0.45),
                  blurRadius: 18),
            ],
          ),
          child: const Icon(Icons.mic, color: Colors.white),
        ),
        const SizedBox(width: 14),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Talk to $assistantName',
                style: TextStyle(
                    color: p.text, fontSize: 16, fontWeight: FontWeight.w700)),
            const SizedBox(height: 2),
            Text('Say “$assistantName” or tap to ask anything about your work.',
                style: TextStyle(color: p.muted, fontSize: 13)),
          ]),
        ),
        Icon(Icons.chevron_right, color: p.faint),
      ]),
    );
  }
}
