import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';
import 'mail_center_screens.dart';
import 'mail_directory.dart';
import 'mail_directory_models.dart';

/// AI on the opened message (docs/email.md#ai-on-email): summarise it or its
/// thread, label it, and draft a reply into the person's drafts. Every call
/// is a governed tool on the server; mail is untrusted input and nothing the
/// model writes picks an address or an action. Sending a drafted reply goes
/// through the normal send and its approval.
class MailAssistPanel extends StatelessWidget {
  const MailAssistPanel({super.key, required this.controller});
  final MailCenterController controller;

  Future<void> _draft(BuildContext context) async {
    final c = controller;
    final request = await showModalBottomSheet<(String, String)>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _DraftReplySheet(),
    );
    if (request == null || !context.mounted) return;
    final ok =
        await c.draftReplyWithAi(kind: request.$1, instructions: request.$2);
    if (ok && context.mounted) {
      await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => MailComposeScreen(controller: c)),
      );
    }
  }

  Future<void> _forward(BuildContext context) async {
    final request = await showModalBottomSheet<(List<String>, String, bool)>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _ForwardSheet(controller: controller),
    );
    if (request == null) return;
    await controller.forwardWithAi(request.$1,
        note: request.$2, writeNote: request.$3);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    final theme = Theme.of(context);
    final ai = c.ai;
    if (ai == null) return const SizedBox.shrink();
    if (!ai.available) {
      return Text(
        l.f('mc.ai.off', {'reason': ai.reason ?? ''}),
        style: theme.textTheme.bodySmall,
      );
    }
    final label = c.label;
    final summary = c.summary;
    final approval = c.draftApproval;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              header: true,
              child:
                  Text(l.t('mc.ai.title'), style: theme.textTheme.titleSmall),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                OutlinedButton.icon(
                  onPressed: c.summaryPhase == MailPhase.loading
                      ? null
                      : () => c.summarize(),
                  icon: const Icon(Icons.short_text),
                  label: Text(l.t('mc.ai.summarize')),
                ),
                OutlinedButton.icon(
                  onPressed: c.summaryPhase == MailPhase.loading
                      ? null
                      : () => c.summarize(thread: true),
                  icon: const Icon(Icons.forum_outlined),
                  label: Text(l.t('mc.ai.summarizeThread')),
                ),
                OutlinedButton.icon(
                  onPressed: c.busy == null ? c.classifyOpened : null,
                  icon: c.busy == 'label'
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.label_outline),
                  label: Text(label == null
                      ? l.t('mc.ai.label')
                      : l.t('mc.ai.labelAgain')),
                ),
                if (c.canWrite)
                  FilledButton.tonalIcon(
                    onPressed: c.busy == null ? () => _draft(context) : null,
                    icon: c.busy == 'aiDraft'
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.auto_awesome_outlined),
                    label: Text(l.t('mc.ai.draftReply')),
                  ),
                if (c.canWrite)
                  OutlinedButton.icon(
                    onPressed: c.busy == null ? () => _forward(context) : null,
                    icon: c.busy == 'forward'
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.forward_to_inbox_outlined),
                    label: Text(l.t('mc.ai.forward')),
                  ),
              ],
            ),
            if (label != null) ...[
              const SizedBox(height: AnumSpacing.xs),
              MailLabelChips(label: label),
              if (label.reason.isNotEmpty)
                Text(label.reason, style: theme.textTheme.bodySmall),
              if (label.injectionSuspected) _injection(context, l),
            ],
            if (c.summaryPhase == MailPhase.loading)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: Semantics(
                  label: l.t('mc.ai.summarizing'),
                  child: const LinearProgressIndicator(),
                ),
              ),
            if (c.summaryPhase
                case MailPhase.error || MailPhase.offline || MailPhase.denied)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.sm),
                child: Semantics(
                  liveRegion: true,
                  child: Text(
                    c.summaryPhase == MailPhase.denied
                        ? l.t('mc.denied')
                        : l.f('mc.ai.failed', {'reason': c.summaryError ?? ''}),
                    style: TextStyle(color: theme.colorScheme.error),
                  ),
                ),
              ),
            if (summary != null && c.summaryPhase == MailPhase.ready)
              _SummaryView(summary: summary),
            if (c.forwardDraft case final MailDraft fwd) ...[
              const SizedBox(height: AnumSpacing.sm),
              if (fwd.waiting && fwd.approval != null)
                sendApprovalCard(context, c, fwd, fwd.approval!,
                    onApprove: c.approveForward)
              else
                Semantics(
                  liveRegion: true,
                  child: Text(l.f('mc.ai.forwardState',
                      {'state': mailDraftStatus(context, fwd)})),
                ),
            ],
            if (approval != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              MailApprovalCard(
                busy: c.busy != null,
                approval: approval,
                title: l.t('mc.ai.draftApprovalTitle'),
                lines: [l.t('mc.ai.draftApprovalText')],
                approveLabel: l.t('mc.ai.approveDraft'),
                onApprove: c.approveAiDraft,
              ),
            ],
            const SizedBox(height: AnumSpacing.xs),
            Text(l.t('mc.ai.note'), style: theme.textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

Widget _injection(BuildContext context, AnumLocalizations l) => Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xs),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.warning_amber_outlined,
              size: 18, color: Theme.of(context).colorScheme.error),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(child: Text(l.t('mc.ai.injection'))),
        ],
      ),
    );

class _SummaryView extends StatelessWidget {
  const _SummaryView({required this.summary});
  final MailSummary summary;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final theme = Theme.of(context);
    return Semantics(
      liveRegion: true,
      child: Padding(
        padding: const EdgeInsets.only(top: AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
                summary.thread
                    ? l.f('mc.ai.threadOf', {'count': summary.messages})
                    : l.t('mc.ai.summary'),
                style: const TextStyle(fontWeight: FontWeight.w600)),
            SelectableText(summary.summary),
            if (summary.keyPoints.isNotEmpty) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(l.t('mc.ai.keyPoints'), style: theme.textTheme.titleSmall),
              for (final point in summary.keyPoints) Text('• $point'),
            ],
            if (summary.actionItems.isNotEmpty) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(l.t('mc.ai.actions'), style: theme.textTheme.titleSmall),
              for (final item in summary.actionItems) Text('• $item'),
            ],
            if (summary.injectionSuspected) _injection(context, l),
          ],
        ),
      ),
    );
  }
}

/// A message's label: its category, and its priority unless normal.
class MailLabelChips extends StatelessWidget {
  const MailLabelChips({super.key, required this.label});
  final MailLabel label;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final scheme = Theme.of(context).colorScheme;
    Widget chip(String text, {bool strong = false}) => Container(
          margin: const EdgeInsetsDirectional.only(
              end: AnumSpacing.xs, top: AnumSpacing.xs),
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          decoration: BoxDecoration(
            color: strong ? scheme.errorContainer : scheme.secondaryContainer,
            borderRadius: BorderRadius.circular(999),
          ),
          child: Text(text,
              style: TextStyle(
                  fontSize: AnumType.xs,
                  color: strong
                      ? scheme.onErrorContainer
                      : scheme.onSecondaryContainer)),
        );
    return Semantics(
      label: l.f('mc.ai.labelSays', {
        'category': l.t('mc.cat.${label.category}'),
        'priority': l.t('mc.pri.${label.priority}'),
      }),
      excludeSemantics: true,
      child: Wrap(
        children: [
          chip(l.t('mc.cat.${label.category}')),
          if (label.priority != 'normal')
            chip(l.t('mc.pri.${label.priority}'),
                strong: label.priority == 'high'),
        ],
      ),
    );
  }
}

/// Reply or reply to all, with optional instructions in the person's words.
class _DraftReplySheet extends StatefulWidget {
  const _DraftReplySheet();

  @override
  State<_DraftReplySheet> createState() => _DraftReplySheetState();
}

class _DraftReplySheetState extends State<_DraftReplySheet> {
  final _instructions = TextEditingController();
  String _kind = 'reply';

  @override
  void dispose() {
    _instructions.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom,
          left: AnumSpacing.md,
          right: AnumSpacing.md,
          top: AnumSpacing.md),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('mc.ai.draftReply'),
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: AnumSpacing.sm),
            SegmentedButton<String>(
              segments: [
                ButtonSegment(value: 'reply', label: Text(l.t('mc.reply'))),
                ButtonSegment(
                    value: 'reply_all', label: Text(l.t('mc.replyAll'))),
              ],
              selected: {_kind},
              onSelectionChanged: (value) =>
                  setState(() => _kind = value.first),
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              controller: _instructions,
              maxLength: 1000,
              minLines: 2,
              maxLines: 5,
              decoration: InputDecoration(
                labelText: l.t('mc.ai.instructions'),
                helperText: l.t('mc.ai.instructionsHint'),
                helperMaxLines: 3,
                alignLabelWithHint: true,
              ),
            ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              alignment: WrapAlignment.end,
              spacing: AnumSpacing.xs,
              children: [
                TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: Text(l.t('mc.cancel'))),
                FilledButton(
                  onPressed: () =>
                      Navigator.pop(context, (_kind, _instructions.text)),
                  child: Text(l.t('mc.ai.write')),
                ),
              ],
            ),
            const SizedBox(height: AnumSpacing.md),
          ],
        ),
      ),
    );
  }
}

/// Forward to typed addresses with an optional note, or one AI writes; the
/// forward is a send, so it waits for the send approval.
class _ForwardSheet extends StatefulWidget {
  const _ForwardSheet({required this.controller});
  final MailCenterController controller;

  @override
  State<_ForwardSheet> createState() => _ForwardSheetState();
}

class _ForwardSheetState extends State<_ForwardSheet> {
  final _form = GlobalKey<FormState>();
  final _to = TextEditingController();
  final _note = TextEditingController();
  bool _write = false;

  static final _address = RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$');

  @override
  void dispose() {
    _to.dispose();
    _note.dispose();
    super.dispose();
  }

  String? _check(String? text) {
    final l = AnumLocalizations.maybeOf(context);
    final list = MailDraftEdit.split(text ?? '');
    if (list.isEmpty) return l.t('mc.needsRecipient');
    final bad = [
      for (final a in list)
        if (!_address.hasMatch(a)) a
    ];
    if (bad.isNotEmpty) return l.f('mc.badAddresses', {'list': bad.join(', ')});
    if (list.length > 50) return l.f('mc.tooMany', {'max': 50});
    return null;
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
              Text(l.t('mc.ai.forward'),
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: AnumSpacing.sm),
              MailRecipientField(
                controller: _to,
                label: l.t('mc.toField'),
                helperText: l.t('mc.addressesHint'),
                suggest: c.directory.suggest,
                validator: _check,
              ),
              TextFormField(
                controller: _note,
                enabled: !_write,
                maxLength: 5000,
                minLines: 2,
                maxLines: 5,
                decoration: InputDecoration(
                    labelText: l.t('mc.ai.forwardNote'),
                    alignLabelWithHint: true),
              ),
              if (c.aiAvailable)
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _write,
                  onChanged: (value) => setState(() => _write = value),
                  title: Text(l.t('mc.ai.writeNote')),
                ),
              Text(l.t('mc.ai.forwardHint'),
                  style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: AnumSpacing.sm),
              Wrap(
                alignment: WrapAlignment.end,
                spacing: AnumSpacing.xs,
                children: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: Text(l.t('mc.cancel'))),
                  FilledButton(
                    onPressed: () {
                      if (!(_form.currentState?.validate() ?? false)) return;
                      Navigator.pop(context, (
                        MailDraftEdit.split(_to.text),
                        _write ? '' : _note.text,
                        _write,
                      ));
                    },
                    child: Text(l.t('mc.askToSend')),
                  ),
                ],
              ),
              const SizedBox(height: AnumSpacing.md),
            ],
          ),
        ),
      ),
    );
  }
}
