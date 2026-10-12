import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../voice/voice_models.dart';
import '../../src/widgets/brand.dart';
import 'command_models.dart';
import 'command_strings.dart';

/// The primary action: the shell's [AnumGradientButton], with an optional
/// caption under it ("ANUM will run these 3 steps in parallel").
class CommandGradientButton extends StatelessWidget {
  const CommandGradientButton({
    required this.label,
    required this.onPressed,
    this.icon,
    this.caption,
    this.busy = false,
    super.key,
  });

  final String label;
  final String? caption;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final button = AnumGradientButton(
        label: label, onPressed: onPressed, icon: icon, busy: busy);
    if (caption == null) return button;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        button,
        const SizedBox(height: AnumSpacing.xxs),
        Text(caption!,
            textAlign: TextAlign.center,
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: context.palette.muted)),
      ],
    );
  }
}

/// The shell's [AnumOrb], sized to the space it has; it moves (and follows the
/// microphone level) while listening.
class CommandOrb extends StatelessWidget {
  const CommandOrb(
      {required this.state,
      required this.level,
      this.maxSize = 220,
      super.key});
  final VoiceState state;
  final ValueListenable<double> level;
  final double maxSize;

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    final dimension = [maxSize, size.width * 0.55, size.height * 0.3]
        .reduce((a, b) => a < b ? a : b)
        .clamp(120.0, maxSize);
    final listening = state == VoiceState.listening;
    return Center(
      child: AnumOrb(
          size: dimension, active: listening, level: listening ? level : null),
    );
  }
}

IconData agentIcon(String id) => switch (id) {
      'email' => Icons.mail_outline,
      'whatsapp' => Icons.chat_outlined,
      'commerce' => Icons.storefront_outlined,
      'research' => Icons.travel_explore,
      'action' => Icons.bolt_outlined,
      'calendar' => Icons.event_outlined,
      'tyre' => Icons.tire_repair_outlined,
      'writer' => Icons.description_outlined,
      _ => Icons.auto_awesome_outlined,
    };

AnumTone agentTone(String id) => switch (id) {
      'email' => AnumTone.info,
      'whatsapp' => AnumTone.ok,
      'commerce' => AnumTone.ok,
      'research' => AnumTone.accent,
      'action' => AnumTone.warn,
      'calendar' => AnumTone.stop,
      'tyre' => AnumTone.warn,
      'writer' => AnumTone.accent,
      _ => AnumTone.info,
    };

String stepStatusLabel(CommandStrings s, String status) => switch (status) {
      'created' => s.t('statusCreated'),
      'queued' => s.t('statusQueued'),
      'running' => s.t('statusRunning'),
      'waiting_approval' => s.t('statusWaiting'),
      'completed' => s.t('statusCompleted'),
      'failed' => s.t('statusFailed'),
      'cancelled' => s.t('statusCancelled'),
      _ => s.t('statusMissing'),
    };

AnumTone stepStatusTone(String status) => switch (status) {
      'completed' => AnumTone.ok,
      'failed' => AnumTone.stop,
      'waiting_approval' => AnumTone.warn,
      'running' || 'queued' => AnumTone.info,
      _ => AnumTone.neutral,
    };

IconData stepStatusIcon(String status) => switch (status) {
      'completed' => Icons.check_circle,
      'failed' => Icons.error_outline,
      'waiting_approval' => Icons.pending_actions,
      'cancelled' => Icons.block,
      'running' || 'queued' => Icons.autorenew,
      _ => Icons.radio_button_unchecked,
    };

String riskLabel(CommandStrings s, String level) => s.f('risk', {
      'level': switch (level) {
        'medium' => s.t('riskMedium'),
        'high' => s.t('riskHigh'),
        'blocked' => s.t('riskBlocked'),
        _ => s.t('riskLow'),
      }
    });

String eventLabel(CommandStrings s, String type) => switch (type) {
      'task.created' => s.t('eventTaskCreated'),
      'task.queued' => s.t('eventTaskQueued'),
      'task.started' => s.t('eventTaskStarted'),
      'task.completed' => s.t('eventTaskCompleted'),
      'task.failed' => s.t('eventTaskFailed'),
      'task.cancelled' => s.t('eventTaskCancelled'),
      'agent_run.started' => s.t('eventRunStarted'),
      'agent_run.checkpointed' => s.t('eventRunCheckpointed'),
      'agent_run.waiting_approval' => s.t('eventRunWaiting'),
      'agent_run.resumed' => s.t('eventRunResumed'),
      'agent_run.completed' => s.t('eventRunCompleted'),
      'agent_run.failed' => s.t('eventRunFailed'),
      'approval.requested' => s.t('eventApprovalRequested'),
      'approval.approved' => s.t('eventApprovalApproved'),
      'approval.rejected' => s.t('eventApprovalRejected'),
      'approval.expired' => s.t('eventApprovalExpired'),
      _ => s.t('eventOther'),
    };

String clockTime(DateTime value) {
  final local = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(local.hour)}:${two(local.minute)}';
}

String durationLabel(Duration value) {
  String two(int n) => n.toString().padLeft(2, '0');
  final minutes = value.inMinutes;
  final seconds = value.inSeconds % 60;
  return minutes >= 60
      ? '${value.inHours}:${two(minutes % 60)}:${two(seconds)}'
      : '${two(minutes)}:${two(seconds)}';
}

/// A labelled linear progress bar (value 0..1).
class CommandProgress extends StatelessWidget {
  const CommandProgress({required this.value, required this.label, super.key});
  final double value;
  final String label;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      label: label,
      value: '${(value * 100).round()}%',
      child: ClipRRect(
        borderRadius: BorderRadius.circular(8),
        child: LinearProgressIndicator(
          value: value,
          minHeight: 6,
          backgroundColor: p.line,
          valueColor: AlwaysStoppedAnimation(p.sky),
        ),
      ),
    );
  }
}

/// The centred page title of each stage ("ANUM Command", "Listening…").
class CommandTitle extends StatelessWidget {
  const CommandTitle({required this.title, this.subtitle, super.key});
  final String title;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      children: [
        Semantics(
          header: true,
          child: Text(title,
              textAlign: TextAlign.center,
              style: theme.textTheme.headlineSmall
                  ?.copyWith(fontWeight: FontWeight.w700)),
        ),
        if (subtitle != null) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text(subtitle!,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium
                  ?.copyWith(color: context.palette.muted)),
        ],
      ],
    );
  }
}

/// A short hash for display (first and last 8 characters).
String shortHash(String? value) {
  if (value == null || value.length < 20) return value ?? '';
  return '${value.substring(0, 8)}…${value.substring(value.length - 8)}';
}

/// Plain text with the agent's icon, used in lists of agents.
class AgentChip extends StatelessWidget {
  const AgentChip({required this.agent, super.key});
  final CommandAgent agent;

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          AnumIconChip(icon: agentIcon(agent.id), tone: agentTone(agent.id)),
          const SizedBox(height: AnumSpacing.xxs),
          SizedBox(
            width: 84,
            child: Text(agent.name,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelSmall),
          ),
        ],
      );
}
