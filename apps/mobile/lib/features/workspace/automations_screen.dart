import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'workspace_controller.dart';
import 'workspace_sheets.dart';

AnumTone automationTone(String status) => switch (status) {
      'running' || 'active' => AnumTone.info,
      'completed' || 'succeeded' => AnumTone.ok,
      'paused' || 'waiting' || 'waiting_approval' => AnumTone.warn,
      'failed' || 'cancelled' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

String humanStatus(String status) {
  final text = status.replaceAll('_', ' ');
  return text.isEmpty ? text : '${text[0].toUpperCase()}${text.substring(1)}';
}

class AutomationsScreen extends StatelessWidget {
  const AutomationsScreen({required this.controller, super.key});
  final WorkspaceController controller;

  void _sheet(BuildContext context, Widget child) => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) => child,
      );

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final p = context.palette;
          final definitions = controller.workflowDefinitions;
          final runs = [...controller.automations]
            ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
          final schedules = controller.schedules;
          final active = runs.where((r) => r.status == 'running').length;

          return AnumPage(
            onRefresh: controller.load,
            children: [
              AnumHeader(
                eyebrow: 'Durable work',
                title: 'Automations',
                subtitle:
                    'Workflows that survive restarts, pause for approval and can be retried.',
                large: true,
                trailing: FilledButton.icon(
                  onPressed: () => _sheet(
                      context, AutomationCreateSheet(controller: controller)),
                  icon: const Icon(Icons.add),
                  label: const Text('New'),
                ),
              ),
              AnumMetricGrid(children: [
                AnumMetric(
                    label: 'Workflows',
                    value: '${definitions.length}',
                    icon: Icons.account_tree_outlined),
                AnumMetric(
                    label: 'Running now',
                    value: '$active',
                    icon: Icons.play_circle_outline),
                AnumMetric(
                    label: 'Runs',
                    value: '${runs.length}',
                    icon: Icons.history),
                AnumMetric(
                  label: 'Schedules',
                  value:
                      '${schedules.where((s) => s.enabled).length}/${schedules.length}',
                  caption: 'Enabled / total',
                  icon: Icons.schedule,
                ),
              ]),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const AnumHeader(
                          eyebrow: 'Workflows', title: 'Your workflows'),
                      const SizedBox(height: 6),
                      if (definitions.isEmpty)
                        AnumEmpty(
                          icon: Icons.account_tree_outlined,
                          title: 'No workflows yet',
                          message:
                              'Create one to chain agent steps and run them on demand or on a schedule.',
                          action: 'Create workflow',
                          onAction: () => _sheet(context,
                              AutomationCreateSheet(controller: controller)),
                        )
                      else
                        for (final d in definitions)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 6),
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  AnumRow(
                                    icon: Icons.account_tree_outlined,
                                    title: d.name,
                                    subtitle: d.description.isEmpty
                                        ? null
                                        : d.description,
                                    detail:
                                        '${d.steps.length} steps · updated ${relativeTime(d.updatedAt)}',
                                    trailing: AnumPill(
                                        label: humanStatus(d.status),
                                        tone: automationTone(d.status)),
                                  ),
                                  if (d.steps.isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsetsDirectional.only(
                                          start: 52, bottom: 6),
                                      child: Wrap(
                                          spacing: 6,
                                          runSpacing: 6,
                                          children: [
                                            for (var i = 0;
                                                i < d.steps.length;
                                                i++)
                                              AnumPill(
                                                  label:
                                                      '${i + 1}. ${d.steps[i].replaceAll('_', ' ')}'),
                                          ]),
                                    ),
                                  Padding(
                                    padding: const EdgeInsetsDirectional.only(
                                        start: 52),
                                    child: FilledButton.tonalIcon(
                                      onPressed: controller.mutating
                                          ? null
                                          : () =>
                                              controller.startAutomation(d.id),
                                      icon:
                                          const Icon(Icons.play_arrow_rounded),
                                      label: const Text('Run now'),
                                    ),
                                  ),
                                ]),
                          ),
                    ]),
              ),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const AnumHeader(eyebrow: 'Runs', title: 'Recent runs'),
                      const SizedBox(height: 6),
                      if (runs.isEmpty)
                        const AnumEmpty(
                          icon: Icons.history,
                          title: 'No runs yet',
                          message:
                              'Runs appear here with their progress, step by step.',
                        )
                      else
                        for (final run in runs.take(20))
                          AnumRow(
                            icon: Icons.play_circle_outline,
                            tone: automationTone(run.status),
                            title: run.name,
                            subtitle: run.stepCount == 0
                                ? 'Waiting to start'
                                : 'Step ${run.currentStep} of ${run.stepCount}',
                            detail: 'Updated ${relativeTime(run.updatedAt)}',
                            trailing: AnumPill(
                                label: humanStatus(run.status),
                                tone: automationTone(run.status)),
                            onTap: () => Navigator.push(
                              context,
                              MaterialPageRoute(
                                  builder: (_) => AutomationDetailScreen(
                                      controller: controller, run: run)),
                            ),
                          ),
                      if (runs.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text('Tap a run to resume, retry or cancel it.',
                            style: TextStyle(color: p.faint, fontSize: 12)),
                      ],
                    ]),
              ),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      AnumHeader(
                        eyebrow: 'Schedules',
                        title: 'When workflows run by themselves',
                        trailing: definitions.isEmpty
                            ? null
                            : TextButton.icon(
                                onPressed: () => _sheet(
                                    context,
                                    ScheduleCreateSheet(
                                        controller: controller)),
                                icon: const Icon(Icons.add_alarm),
                                label: const Text('Add'),
                              ),
                      ),
                      const SizedBox(height: 6),
                      if (schedules.isEmpty)
                        AnumEmpty(
                          icon: Icons.schedule,
                          title: 'No schedules',
                          message: definitions.isEmpty
                              ? 'Create a workflow first, then schedule it.'
                              : 'Add a schedule to run a workflow automatically.',
                        )
                      else
                        for (final s in schedules)
                          AnumRow(
                            icon: Icons.schedule,
                            title: s.name,
                            subtitle: 'Cron ${s.cron} · ${s.timezone}',
                            trailing: AnumPill(
                              label: s.enabled ? 'Enabled' : 'Paused',
                              tone: s.enabled ? AnumTone.ok : AnumTone.neutral,
                            ),
                          ),
                    ]),
              ),
              const SizedBox(height: AnumSpacing.lg),
            ],
          );
        },
      );
}
