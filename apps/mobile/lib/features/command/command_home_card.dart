import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'command_controller.dart';
import 'command_models.dart';
import 'command_strings.dart';
import 'command_widgets.dart';

/// The notice shown when a followed command settles while its screen is closed.
String commandSettledNotice(CommandView view, Locale? locale) {
  final s = CommandStrings(locale?.languageCode == 'ar');
  if (view.status == CommandStatus.waitingApproval) {
    return s.t('commandNeedsApproval');
  }
  final status = switch (view.status) {
    CommandStatus.completed => s.t('statusCompleted'),
    CommandStatus.cancelled => s.t('statusCancelled'),
    CommandStatus.partial => s.t('partialResults'),
    _ => s.t('statusFailed'),
  };
  return s.f('commandFinished', {'status': status});
}

/// A home card for the command that keeps working in the background: its
/// progress, and a tap to open it. Shows nothing when no command is followed.
class CommandProgressCard extends StatelessWidget {
  const CommandProgressCard(
      {required this.controller, required this.onOpen, super.key});
  final CommandController controller;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          final view = controller.command;
          if (view == null || !view.status.active) {
            return const SizedBox.shrink();
          }
          final s = CommandStrings.of(context);
          final waiting = view.status == CommandStatus.waitingApproval;
          return AnumSurface(
            onTap: onOpen,
            semanticLabel: '${s.t('running')}: ${view.text}',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(children: [
                  AnumIconChip(
                      icon: waiting ? Icons.pending_actions : Icons.autorenew,
                      tone: waiting ? AnumTone.warn : AnumTone.info),
                  const SizedBox(width: AnumSpacing.sm),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                            waiting
                                ? s.f('pendingCount',
                                    {'count': view.pendingApprovals})
                                : s.t('running'),
                            style: Theme.of(context).textTheme.labelLarge),
                        Text(view.text,
                            maxLines: 2, overflow: TextOverflow.ellipsis),
                        Text(
                            s.f('stepsCount', {
                              'done': view.completedSteps,
                              'total': view.stepCount
                            }),
                            style: Theme.of(context).textTheme.bodySmall),
                      ],
                    ),
                  ),
                ]),
                const SizedBox(height: AnumSpacing.xs),
                CommandProgress(value: view.progress, label: view.text),
              ],
            ),
          );
        },
      );
}
