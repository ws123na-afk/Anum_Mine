import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/widgets/depth.dart';
import 'automation_models.dart';

/// A starting point for a new automation (the Templates tab, mockup 11).
/// Nothing is stored until the person saves it in the editor: the template
/// fills in the name, description and steps, and suggests a schedule that
/// is added only when they confirm it. Steps use only the engine's actions
/// (`task.create`, `notify`), so every run still goes through approvals.
class AutomationTemplate {
  const AutomationTemplate(
      {required this.id,
      required this.icon,
      required this.tone,
      required this.cron,
      this.notifyOnly = false});
  final String id;
  final IconData icon;
  final AnumTone tone;

  /// The suggested schedule (five-field cron, the workspace's time zone).
  final String cron;

  /// A reminder: one notify step instead of an agent task.
  final bool notifyOnly;

  String name(AnumLocalizations l) => l.t('autoTpl.$id.name');
  String description(AnumLocalizations l) => l.t('autoTpl.$id.desc');
  String scheduleName(AnumLocalizations l) => l.t('autoTpl.$id.schedule');

  /// The steps the editor starts with.
  List<StepDraft> steps(AnumLocalizations l) => [
        notifyOnly
            ? StepDraft(
                id: 'step-1',
                name: l.t('autoTplNotifyStep'),
                action: 'notify',
                message: l.t('autoTpl.$id.prompt'))
            : StepDraft(
                id: 'step-1',
                name: l.t('autoTplTaskStep'),
                title: name(l),
                prompt: l.t('autoTpl.$id.prompt')),
      ];
}

const automationTemplates = [
  AutomationTemplate(
      id: 'daily',
      icon: Icons.insert_chart_outlined,
      tone: AnumTone.stop,
      cron: '0 8 * * *'),
  AutomationTemplate(
      id: 'meetings',
      icon: Icons.groups_outlined,
      tone: AnumTone.info,
      cron: '0 17 * * 0-4'),
  AutomationTemplate(
      id: 'followup',
      icon: Icons.mark_email_read_outlined,
      tone: AnumTone.ok,
      cron: '0 16 * * 0-4'),
  AutomationTemplate(
      id: 'prices',
      icon: Icons.sell_outlined,
      tone: AnumTone.warn,
      cron: '0 10 * * *'),
  AutomationTemplate(
      id: 'weekly',
      icon: Icons.event_note_outlined,
      tone: AnumTone.accent,
      cron: '0 9 * * 0'),
  AutomationTemplate(
      id: 'reminder',
      icon: Icons.notifications_active_outlined,
      tone: AnumTone.neutral,
      cron: '0 9 1 * *',
      notifyOnly: true),
];
