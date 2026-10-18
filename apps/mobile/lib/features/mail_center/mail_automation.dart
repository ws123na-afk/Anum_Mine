import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_models.dart';
import 'mail_center_screens.dart';
import 'mail_directory.dart';
import 'mail_directory_models.dart';

/// Retention, background sync and forwarding rules of one mailbox on the
/// phone (docs/email.md#mailbox-settings): every change waits for an owner's
/// approval of exactly that change (`change_mail_settings`,
/// `activate_mail_forward_rule`), approved in place with the payload hash.

String _days(AnumLocalizations l, int? days) =>
    days == null ? l.t('mc.rs.keep') : l.f('mc.rs.days', {'days': days});

String describeSettings(AnumLocalizations l, MailSettingsEdit e) =>
    l.f('mc.rs.summary', {
      'sent': _days(l, e.sentRetentionDays),
      'drafts': _days(l, e.draftRetentionDays),
      'labels': _days(l, e.labelRetentionDays),
      'sync': e.backgroundSync ? l.t('mc.op.on') : l.t('mc.op.offShort'),
    });

class MailRetentionSection extends StatefulWidget {
  const MailRetentionSection({super.key, required this.controller});
  final MailDirectoryController controller;

  @override
  State<MailRetentionSection> createState() => _MailRetentionSectionState();
}

class _MailRetentionSectionState extends State<MailRetentionSection> {
  MailSettingsEdit? _edit;
  int? _version;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    final theme = Theme.of(context);
    final state = stateFor(context, c.settingsPhase, c.settingsError,
        onRetry: c.loadSettings, hasData: c.settings != null);
    if (state != null) return state;
    final s = c.settings;
    if (s == null) return const SizedBox.shrink();
    if (_version != s.version) {
      _version = s.version;
      _edit = s.values;
    }
    final edit = _edit ?? s.values;
    final waiting = s.approval != null;
    final canEdit = s.canChange && !waiting && c.busy == null;
    Widget row(String field, String label) {
      final value = edit.days(field);
      final choices = {...s.retentionChoices, if (value != null) value}.toList()
        ..sort();
      return DropdownButtonFormField<int?>(
        isExpanded: true,
        initialValue: value,
        key: ValueKey('$field-${s.version}-$value'),
        decoration: InputDecoration(labelText: label),
        items: [
          DropdownMenuItem<int?>(value: null, child: Text(l.t('mc.rs.keep'))),
          for (final d in choices)
            DropdownMenuItem<int?>(
                value: d, child: Text(l.f('mc.rs.days', {'days': d}))),
        ],
        onChanged: canEdit
            ? (days) => setState(() => _edit = edit.copyWith(field, days))
            : null,
      );
    }

    final sync = s.sync;
    final syncText = !s.syncRunning
        ? l.t('mc.rs.syncServerOff')
        : sync == null
            ? l.t('mc.rs.syncNever')
            : switch (sync.status) {
                'ok' => l.f('mc.rs.syncOk', {
                    'time': sync.checkedAt == null
                        ? '-'
                        : mailDateTime(context, sync.checkedAt!),
                    'unread': sync.inboxUnread ?? 0,
                  }),
                'pending' => l.t('mc.rs.syncNever'),
                _ => l.f('mc.rs.syncFailed', {
                    'state': l.t('mc.status.${sync.status}'),
                    'reason': sync.lastError ?? '',
                  }),
              };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!s.canChange)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Text(
                l.t(s.access == 'owner' ? 'mc.rs.noRole' : 'mc.rs.readOnly'),
                style: theme.textTheme.bodySmall),
          ),
        row('sent', l.t('mc.rs.sent')),
        const SizedBox(height: AnumSpacing.xs),
        row('draft', l.t('mc.rs.drafts')),
        const SizedBox(height: AnumSpacing.xs),
        row('label', l.t('mc.rs.labels')),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          value: edit.backgroundSync,
          onChanged: canEdit
              ? (value) => setState(() => _edit = edit.withSync(value))
              : null,
          title: Text(l.t('mc.rs.sync')),
          subtitle: Text(l.f('mc.rs.syncHint',
              {'minutes': (s.syncIntervalSeconds / 60).ceil()})),
        ),
        Semantics(
          liveRegion: true,
          child: Text(syncText, style: theme.textTheme.bodySmall),
        ),
        if (c.settingsStale)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Text(l.t('mc.rs.stale'),
                style: TextStyle(color: theme.colorScheme.error)),
          ),
        const SizedBox(height: AnumSpacing.sm),
        if (waiting) ...[
          MailApprovalCard(
            busy: c.busy != null,
            approval: s.approval!,
            title: l.t('mc.rs.approvalTitle'),
            lines: [
              if (s.pending != null) describeSettings(l, s.pending!),
            ],
            approveLabel: l.t('mc.rs.approve'),
            onApprove: () => c.approve(s.approval!),
          ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton(
              onPressed: c.busy == null ? c.withdrawSettings : null,
              child: Text(l.t('mc.rs.withdraw')),
            ),
          ),
        ] else if (s.canChange)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
              onPressed: canEdit && !edit.sameAs(s.values)
                  ? () => c.askSettings(edit)
                  : null,
              icon: c.busy == 'settings'
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.verified_user_outlined),
              label: Text(l.t('mc.rs.ask')),
            ),
          ),
        if (s.canChange && !waiting)
          Text(l.t('mc.rs.askHint'), style: theme.textTheme.bodySmall),
      ],
    );
  }
}

/// The owner's forwarding rules, each forwarding only once approved.
class MailRulesSection extends StatelessWidget {
  const MailRulesSection({super.key, required this.controller});
  final MailDirectoryController controller;

  static const maxRules = 20;

  Future<void> _edit(BuildContext context, MailRule? rule) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (_) => _RuleSheet(controller: controller, rule: rule),
      );

  Future<void> _remove(BuildContext context, MailRule rule) async {
    final l = AnumLocalizations.maybeOf(context);
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.t('mc.fr.removeTitle')),
        content: Text(l.f('mc.fr.removeText', {'name': rule.name})),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.fr.remove'))),
        ],
      ),
    );
    if (yes == true) await controller.deleteRule(rule);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    final theme = Theme.of(context);
    final state = stateFor(context, c.rulesPhase, c.rulesError,
        onRetry: c.loadRules, hasData: c.rules.isNotEmpty);
    if (state != null) return state;
    final idle = c.busy == null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (c.rules.isEmpty) Text(l.t('mc.fr.none')),
        for (final rule in c.rules)
          Card(
            margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.sm),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(rule.name,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                  Semantics(
                    liveRegion: true,
                    child: Text(
                      l.t('mc.fr.status.${rule.status}') +
                          (rule.status == 'paused' && rule.lastError != null
                              ? ': ${rule.lastError}'
                              : ''),
                      style: TextStyle(
                          color: rule.status == 'paused'
                              ? theme.colorScheme.error
                              : null),
                    ),
                  ),
                  Text([
                    if (rule.fromContains.isNotEmpty)
                      l.f('mc.fr.fromIs', {'text': rule.fromContains}),
                    if (rule.subjectContains.isNotEmpty)
                      l.f('mc.fr.subjectIs', {'text': rule.subjectContains}),
                  ].join(' · ')),
                  Text(l.f('mc.fr.to', {'to': rule.forwardTo.join(', ')})),
                  Text(
                    rule.lastRunAt == null
                        ? l.f(
                            'mc.fr.countNever', {'count': rule.forwardedCount})
                        : l.f('mc.fr.count', {
                            'count': rule.forwardedCount,
                            'time': mailDateTime(context, rule.lastRunAt!),
                          }),
                    style: theme.textTheme.bodySmall,
                  ),
                  if (rule.approvalNote != null)
                    Text(rule.approvalNote!,
                        style: TextStyle(color: theme.colorScheme.error)),
                  if (rule.status == 'pending_approval' &&
                      rule.approval != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    MailApprovalCard(
                      busy: !idle,
                      approval: rule.approval!,
                      title: l.t('mc.fr.approvalTitle'),
                      lines: [
                        l.f('mc.fr.approvalText', {
                          'to': rule.forwardTo.join(', '),
                          'name': rule.name,
                        }),
                      ],
                      approveLabel: l.t('mc.fr.approve'),
                      onApprove: () => c.approve(rule.approval!),
                    ),
                  ],
                  Wrap(
                    spacing: AnumSpacing.xs,
                    runSpacing: AnumSpacing.xs,
                    children: [
                      TextButton.icon(
                        onPressed: idle ? () => _edit(context, rule) : null,
                        icon: const Icon(Icons.edit_outlined),
                        label: Text(l.t('mc.fr.edit')),
                      ),
                      if (rule.status == 'active')
                        TextButton.icon(
                          onPressed: idle ? () => c.pauseRule(rule) : null,
                          icon: const Icon(Icons.pause_circle_outline),
                          label: Text(l.t('mc.fr.pause')),
                        ),
                      if (rule.status == 'paused')
                        TextButton.icon(
                          onPressed: idle ? () => c.resumeRule(rule) : null,
                          icon: const Icon(Icons.play_circle_outline),
                          label: Text(l.t('mc.fr.resume')),
                        ),
                      TextButton.icon(
                        onPressed: idle ? () => _remove(context, rule) : null,
                        icon: const Icon(Icons.delete_outline),
                        label: Text(l.t('mc.fr.remove')),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
        if (c.rules.length < maxRules)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              onPressed: idle ? () => _edit(context, null) : null,
              icon: const Icon(Icons.add),
              label: Text(l.t('mc.fr.new')),
            ),
          )
        else
          Text(l.t('mc.fr.full'), style: theme.textTheme.bodySmall),
      ],
    );
  }
}

class _RuleSheet extends StatefulWidget {
  const _RuleSheet({required this.controller, this.rule});
  final MailDirectoryController controller;
  final MailRule? rule;

  @override
  State<_RuleSheet> createState() => _RuleSheetState();
}

class _RuleSheetState extends State<_RuleSheet> {
  final _form = GlobalKey<FormState>();
  late final _name = TextEditingController(text: widget.rule?.name);
  late final _from = TextEditingController(text: widget.rule?.fromContains);
  late final _subject =
      TextEditingController(text: widget.rule?.subjectContains);
  late final _to =
      TextEditingController(text: widget.rule?.forwardTo.join(', '));

  static final _address = RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$');
  String? _conditionError;

  @override
  void dispose() {
    for (final c in [_name, _from, _subject, _to]) {
      c.dispose();
    }
    super.dispose();
  }

  String? _checkTo(String? text) {
    final l = AnumLocalizations.maybeOf(context);
    final list = MailDraftEdit.split(text ?? '');
    if (list.isEmpty) return l.t('mc.needsRecipient');
    final bad = [
      for (final a in list)
        if (!_address.hasMatch(a)) a
    ];
    if (bad.isNotEmpty) return l.f('mc.badAddresses', {'list': bad.join(', ')});
    if (list.length > 5) return l.t('mc.fr.toMany');
    final own = widget.controller.account?.address.toLowerCase();
    if (list.any((a) => a.toLowerCase() == own)) return l.t('mc.fr.toSelf');
    return null;
  }

  Future<void> _save() async {
    final l = AnumLocalizations.maybeOf(context);
    final missing = _from.text.trim().isEmpty && _subject.text.trim().isEmpty;
    setState(
        () => _conditionError = missing ? l.t('mc.fr.needsCondition') : null);
    final valid = _form.currentState?.validate() ?? false;
    if (!valid || missing) return;
    final ok = await widget.controller.saveRule(
        widget.rule,
        MailRuleEdit(
          name: _name.text,
          fromContains: _from.text,
          subjectContains: _subject.text,
          forwardTo: MailDraftEdit.split(_to.text),
        ));
    if (ok && mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = widget.controller;
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom,
          left: AnumSpacing.md,
          right: AnumSpacing.md,
          top: AnumSpacing.md),
      child: Form(
        key: _form,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(widget.rule == null ? l.t('mc.fr.new') : l.t('mc.fr.edit'),
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: AnumSpacing.sm),
              TextFormField(
                controller: _name,
                maxLength: 120,
                decoration: InputDecoration(labelText: l.t('mc.fr.name')),
                validator: (v) =>
                    (v ?? '').trim().isEmpty ? l.t('mc.fr.needsName') : null,
              ),
              TextFormField(
                controller: _from,
                maxLength: 200,
                decoration: InputDecoration(labelText: l.t('mc.fr.from')),
              ),
              TextFormField(
                controller: _subject,
                maxLength: 200,
                decoration: InputDecoration(
                    labelText: l.t('mc.fr.subject'),
                    helperText: l.t('mc.fr.conditionHint'),
                    helperMaxLines: 3,
                    errorText: _conditionError,
                    errorMaxLines: 3),
              ),
              const SizedBox(height: AnumSpacing.xs),
              MailRecipientField(
                controller: _to,
                label: l.t('mc.fr.forwardTo'),
                helperText: l.t('mc.fr.forwardToHint'),
                suggest: c.suggest,
                validator: _checkTo,
              ),
              const SizedBox(height: AnumSpacing.xs),
              Text(l.t('mc.fr.needsApproval'),
                  style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: AnumSpacing.sm),
              ListenableBuilder(
                listenable: c,
                builder: (context, _) => Wrap(
                  alignment: WrapAlignment.end,
                  spacing: AnumSpacing.xs,
                  children: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: Text(l.t('mc.cancel'))),
                    FilledButton(
                        onPressed: c.busy == null ? _save : null,
                        child: Text(l.t('mc.fr.askApproval'))),
                  ],
                ),
              ),
              const SizedBox(height: AnumSpacing.md),
            ],
          ),
        ),
      ),
    );
  }
}
