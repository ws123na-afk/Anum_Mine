import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../auth/welcome_screens.dart' show openExternalUrl;
import 'approval_policy.dart';
import 'approval_settings.dart';

/// The organization's approval rules, read only (docs/approvals-and-risk.md).
class ApprovalRulesSection extends StatelessWidget {
  const ApprovalRulesSection(
      {required this.controller,
      this.webLink,
      this.openUrl = openExternalUrl,
      super.key});
  final ApprovalRulesController controller;

  /// The web Governance page, or null when the build has no web address.
  final Uri? webLink;
  final Future<bool> Function(Uri uri) openUrl;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final l = AnumLocalizations.maybeOf(context);
        final p = context.palette;
        final c = controller;
        return AnumSurface(
          key: const Key('approval-rules'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                eyebrow: l.t('ad.apr.rules.eyebrow'),
                title: l.t('ad.apr.rules.title'),
                subtitle: l.t('ad.apr.rules.intro')),
            const SizedBox(height: AnumSpacing.sm),
            ...switch (c.phase) {
              SectionPhase.loading => [
                  Semantics(
                      liveRegion: true,
                      label: l.t('ad.apr.rules.loading'),
                      child: const LinearProgressIndicator()),
                ],
              SectionPhase.denied => [
                  AnumEmpty(
                      icon: Icons.lock_outline,
                      title: l.t('ad.apr.rules.denied'),
                      message: c.error ?? ''),
                ],
              SectionPhase.error => [
                  AnumEmpty(
                      key: const Key('approval-rules-error'),
                      icon: Icons.error_outline,
                      title: l.f('ad.apr.rules.error', {'message': ''}).trim(),
                      message: c.error ?? '',
                      action: l.t('ad.use.retry'),
                      onAction: c.load),
                ],
              SectionPhase.ready => [
                  if (c.rules.isEmpty)
                    Text(l.t('ad.apr.rules.empty'),
                        key: const Key('approval-rules-empty'),
                        style: TextStyle(color: p.muted))
                  else ...[
                    Text(
                        l.f('ad.apr.rules.count', {
                          'count': c.rules.length,
                          'enabled': c.rules.where((r) => r.enabled).length
                        }),
                        style:
                            TextStyle(color: p.faint, fontSize: AnumType.xs)),
                    for (final rule in c.rules) _RuleRow(rule: rule),
                  ],
                  const SizedBox(height: AnumSpacing.sm),
                  Text(l.t('ad.apr.rules.readOnly'),
                      style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
                  const SizedBox(height: AnumSpacing.xs),
                  if (webLink != null)
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton.icon(
                          key: const Key('approval-rules-web'),
                          onPressed: () => openUrl(webLink!),
                          icon: const Icon(Icons.open_in_new),
                          label: Text(l.t('ad.apr.rules.manage'))),
                    )
                  else
                    Text(l.t('ad.apr.rules.webHint'),
                        key: const Key('approval-rules-web-hint'),
                        style:
                            TextStyle(color: p.faint, fontSize: AnumType.xs)),
                ],
            },
          ]),
        );
      });
}

class _RuleRow extends StatelessWidget {
  const _RuleRow({required this.rule});
  final ApprovalRuleView rule;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final band =
        ruleBand(rule.conditions, l) ?? l.t('ad.apr.rules.everyAmount');
    return Padding(
      key: Key('approval-rule-${rule.id}'),
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Divider(height: 1, color: p.line),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(rule.name,
                  textDirection: _auto(rule.name),
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
              AnumPill(
                  label: l
                      .t(rule.enabled ? 'ad.apr.rules.on' : 'ad.apr.rules.off'),
                  tone: rule.enabled ? AnumTone.ok : AnumTone.neutral),
            ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(
            '${l.f('ad.apr.rules.pattern', {
                  'pattern': rule.actionPattern
                })} · $band',
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.xxs),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
          for (final step in ruleSteps(rule, l)) AnumPill(label: step),
        ]),
      ]),
    );
  }
}

/// Arabic names read right to left, others as written.
TextDirection? _auto(String text) =>
    RegExp(r'[؀-ۿ]').hasMatch(text) ? TextDirection.rtl : null;

/// How a pausing action is routed, from the rules and the policy as drafted.
class ApprovalRoutingSection extends StatelessWidget {
  const ApprovalRoutingSection(
      {required this.rules,
      required this.policy,
      this.drafted = false,
      super.key});
  final ApprovalRulesController rules;

  /// The switches as shown above (the draft), null when unknown.
  final ApprovalPolicy? policy;

  /// The draft differs from what is in force.
  final bool drafted;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: rules,
      builder: (context, _) {
        final l = AnumLocalizations.maybeOf(context);
        final p = context.palette;
        final ready = rules.phase == SectionPhase.ready;
        final summary =
            routingSummary(ready ? rules.rules : const [], policy, l);
        final steps = [
          'ad.apr.routing.step.pause',
          'ad.apr.routing.step.match',
          'ad.apr.routing.step.band',
          'ad.apr.routing.step.fallback',
          'ad.apr.routing.step.owners',
        ];
        TextStyle body([Color? color]) =>
            TextStyle(color: color ?? p.text, fontSize: AnumType.sm);
        return AnumSurface(
          key: const Key('approval-routing'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                eyebrow: l.t('ad.apr.routing.eyebrow'),
                title: l.t('ad.apr.routing.title'),
                subtitle: l.t('ad.apr.routing.intro')),
            const SizedBox(height: AnumSpacing.sm),
            for (var i = 0; i < steps.length; i++)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SizedBox(
                          width: 28,
                          child: Text('${i + 1}.',
                              style: body(p.muted)
                                  .copyWith(fontWeight: FontWeight.w700))),
                      Expanded(child: Text(l.t(steps[i]), style: body())),
                    ]),
              ),
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('ad.apr.routing.bands'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            const SizedBox(height: AnumSpacing.xs),
            if (!ready)
              Text(l.t('ad.apr.routing.rulesUnknown'),
                  key: const Key('routing-rules-unknown'), style: body(p.muted))
            else if (summary.bands.isEmpty)
              Text(l.t('ad.apr.routing.noBands'),
                  key: const Key('routing-no-bands'), style: body(p.muted))
            else
              for (final band in summary.bands)
                Padding(
                  padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
                  child: Text('• $band',
                      textDirection: _auto(band), style: body()),
                ),
            if (summary.off > 0) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(l.f('ad.apr.routing.off', {'count': summary.off}),
                  style: body(p.muted)),
            ],
            const SizedBox(height: AnumSpacing.sm),
            for (final line in summary.policy)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
                child: Text('• $line', style: body()),
              ),
            if (drafted)
              Text(l.t('ad.apr.routing.draft'),
                  key: const Key('routing-drafted'), style: body(p.warn)),
          ]),
        );
      });
}

/// When waiting approvals remind the owners (owners change it).
class ApprovalReminderSection extends StatelessWidget {
  const ApprovalReminderSection(
      {required this.controller, required this.readOnly, super.key});
  final ApprovalReminderController controller;

  /// The caller is known not to be an owner.
  final bool readOnly;

  static const hourChoices = [1, 2, 3, 4, 6, 8, 12, 18, 23];

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final l = AnumLocalizations.maybeOf(context);
        final p = context.palette;
        final c = controller;
        final locked = readOnly || c.denied != null;
        String hoursText(int h) => h == 1
            ? l.t('ad.apr.rem.hour')
            : l.f('ad.apr.rem.hours', {'hours': h});
        final current = c.draft;
        final choices = {...hourChoices, if (current != null) current}.toList()
          ..sort();
        return AnumSurface(
          key: const Key('approval-reminders'),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                eyebrow: l.t('ad.apr.rem.eyebrow'),
                title: l.t('ad.apr.rem.title'),
                subtitle: l.t('ad.apr.rem.help')),
            const SizedBox(height: AnumSpacing.sm),
            ...switch (c.phase) {
              SectionPhase.loading => [
                  Semantics(
                      liveRegion: true,
                      label: l.t('ad.apr.rem.loading'),
                      child: const LinearProgressIndicator()),
                ],
              SectionPhase.denied || SectionPhase.error => [
                  AnumEmpty(
                      key: const Key('approval-reminders-error'),
                      icon: Icons.error_outline,
                      title: l
                          .f('ad.apr.rem.error', {'message': ''})
                          .replaceAll(RegExp(r'[:：]\s*$'), '')
                          .trim(),
                      message: c.error ?? '',
                      action: l.t('ad.use.retry'),
                      onAction: c.load),
                ],
              SectionPhase.ready => [
                  DropdownButtonFormField<int?>(
                    key: const Key('reminder-hours'),
                    initialValue: c.draft,
                    isExpanded: true,
                    decoration:
                        InputDecoration(labelText: l.t('ad.apr.rem.label')),
                    items: [
                      DropdownMenuItem<int?>(
                          value: null, child: Text(l.t('ad.apr.rem.off'))),
                      for (final h in choices)
                        DropdownMenuItem<int?>(
                            value: h, child: Text(hoursText(h))),
                    ],
                    onChanged: locked || c.saving ? null : c.choose,
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      c.setting?.updatedAt == null
                          ? l.t('ad.apr.rem.never')
                          : l.f('ad.apr.rem.updated', {
                              'time': MaterialLocalizations.of(context)
                                  .formatMediumDate(
                                      c.setting!.updatedAt!.toLocal()),
                              'user': c.setting!.updatedBy ?? '-',
                            }),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                  if (locked) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(c.denied ?? l.t('ad.apr.rem.denied'),
                        key: const Key('reminder-read-only'),
                        style:
                            TextStyle(color: p.muted, fontSize: AnumType.sm)),
                  ],
                  if (c.notice != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(c.notice!,
                        key: const Key('reminder-notice'),
                        style:
                            TextStyle(color: c.noticeFailed ? p.stop : p.ok)),
                  ],
                  if (!locked) ...[
                    const SizedBox(height: AnumSpacing.sm),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: FilledButton.icon(
                          key: const Key('reminder-save'),
                          onPressed:
                              c.saving || !c.changed ? null : () => c.save(l),
                          icon: const Icon(Icons.save_outlined),
                          label: Text(l.t(c.saving
                              ? 'ad.apr.rem.saving'
                              : 'ad.apr.rem.save'))),
                    ),
                  ],
                ],
            },
          ]),
        );
      });
}
