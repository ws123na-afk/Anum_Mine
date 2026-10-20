import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'marketing_ops_controller.dart';
import 'marketing_ops_models.dart';

/// Names of the days, Monday first, for best-time labels.
List<String> mkWeekdays(AnumLocalizations l) =>
    [for (var i = 0; i < 7; i++) l.t('mkOps.day.$i')];

/// The sentence for an AI assist refusal: the server's own words plus what to
/// do next (budget used up, no right, model not connected).
String mkAssistProblemText(AnumLocalizations l, MarketingOps ops) {
  final message = ops.assistMessage ?? '';
  return switch (ops.assistProblem) {
    MkAssistProblem.budget => l.f('mkOps.ai.budget', {'message': message}),
    MkAssistProblem.denied => l.f('mkOps.denied', {'message': message}),
    MkAssistProblem.unavailable =>
      l.f('mkOps.ai.unavailable', {'message': message}),
    _ => message.isEmpty
        ? l.t('mkOps.ai.failed')
        : l.f('mkOps.ai.failedWith', {'message': message}),
  };
}

/// Ideas and hashtags from the budgeted model gateway. Every result says
/// where it came from (model or template) and whether pasted outside text
/// shaped it; tapping Use puts an idea into the composer.
class AssistPanel extends StatefulWidget {
  const AssistPanel(
      {required this.ops,
      required this.platforms,
      required this.languages,
      required this.fallbackBrief,
      required this.campaignId,
      required this.onUse,
      super.key});
  final MarketingOps ops;
  final List<String> platforms, languages;

  /// The post text so far, used when no brief is typed.
  final String Function() fallbackBrief;
  final String? campaignId;
  final void Function(MkIdea idea, MkIdeas result) onUse;
  @override
  State<AssistPanel> createState() => _AssistPanelState();
}

class _AssistPanelState extends State<AssistPanel> {
  final _brief = TextEditingController(), _source = TextEditingController();
  String? _local;

  @override
  void dispose() {
    _brief.dispose();
    _source.dispose();
    super.dispose();
  }

  Future<void> _suggest() async {
    final l = context.anum;
    final brief = _brief.text.trim().isEmpty
        ? widget.fallbackBrief()
        : _brief.text.trim();
    if (brief.trim().isEmpty && widget.campaignId == null) {
      setState(() => _local = l.t('mkOps.ai.needBrief'));
      return;
    }
    setState(() => _local = null);
    await widget.ops.suggestIdeas(
        brief: brief,
        platforms:
            widget.platforms.isEmpty ? const ['instagram'] : widget.platforms,
        languages: widget.languages,
        campaignId: widget.campaignId,
        source: _source.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final ops = widget.ops;
    final text = Theme.of(context).textTheme;
    final error = Theme.of(context).colorScheme.error;
    return ListenableBuilder(
        listenable: ops,
        builder: (context, _) {
          final ideas = ops.ideas;
          return Card(
              child: ExpansionTile(
                  leading: const Icon(Icons.auto_awesome_outlined),
                  title: Text(l.t('mkOps.ai.title')),
                  childrenPadding: const EdgeInsets.fromLTRB(
                      AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.md),
                  expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                TextField(
                    controller: _brief,
                    minLines: 2,
                    maxLines: 4,
                    maxLength: 4000,
                    decoration: InputDecoration(
                        labelText: l.t('mkOps.ai.brief'),
                        hintText: l.t('mkOps.ai.briefHint'))),
                TextField(
                    controller: _source,
                    minLines: 2,
                    maxLines: 4,
                    maxLength: 8000,
                    decoration: InputDecoration(
                        labelText: l.t('mkOps.ai.source'),
                        helperMaxLines: 4,
                        helperText: l.t('mkOps.ai.sourceHint'))),
                const SizedBox(height: AnumSpacing.xs),
                Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: FilledButton.icon(
                        onPressed: ops.assistBusy ? null : _suggest,
                        icon: const Icon(Icons.auto_awesome),
                        label: Text(ops.assistBusy
                            ? l.t('mkOps.ai.thinking')
                            : l.t('mkOps.ai.suggest')))),
                if (_local != null)
                  Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Text(_local!, style: TextStyle(color: error))),
                if (ops.assistProblem != null && !ops.assistProblemIsTags)
                  Semantics(
                      liveRegion: true,
                      child: Padding(
                          padding: const EdgeInsets.only(top: AnumSpacing.xs),
                          child: Text(mkAssistProblemText(l, ops),
                              style: TextStyle(color: error)))),
                if (ideas != null) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        AnumPill(
                            label: ideas.fromModel
                                ? l.t('mkOps.ai.fromModel')
                                : l.t('mkOps.ai.fromTemplate'),
                            tone: ideas.fromModel
                                ? AnumTone.ok
                                : AnumTone.neutral,
                            icon: ideas.fromModel
                                ? Icons.auto_awesome
                                : Icons.description_outlined),
                        if (ideas.brandVoiceUsed)
                          AnumPill(label: l.t('mkOps.ai.brandVoice')),
                        if (ideas.untrustedSource)
                          AnumPill(
                              label: l.t('mkOps.ai.untrustedPill'),
                              tone: AnumTone.warn,
                              icon: Icons.warning_amber_outlined),
                      ]),
                  if (ideas.note != null && ideas.note!.isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(ideas.note!, style: text.bodySmall)),
                  if (ideas.untrustedSource)
                    Padding(
                        padding: const EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(l.t('mkOps.ai.untrustedNote'))),
                  if (ideas.ideas.isEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(l.t('mkOps.ai.none'))),
                  for (final idea in ideas.ideas)
                    _IdeaCard(
                        idea: idea, onUse: () => widget.onUse(idea, ideas)),
                  if (ideas.bestTimes.isNotEmpty)
                    Padding(
                        padding: const EdgeInsets.only(top: AnumSpacing.xs),
                        child: Text(
                            l.f('mkOps.ai.bestTimes', {
                              'slots': ideas.bestTimes
                                  .map((slot) =>
                                      mkSlotLabel(slot, mkWeekdays(l)))
                                  .join(', ')
                            }),
                            style: text.bodySmall)),
                ],
              ]));
        });
  }
}

class _IdeaCard extends StatelessWidget {
  const _IdeaCard({required this.idea, required this.onUse});
  final MkIdea idea;
  final VoidCallback onUse;
  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    return Semantics(
        container: true,
        label: idea.title,
        child: Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: DecoratedBox(
                decoration: BoxDecoration(
                    border: Border.all(color: Theme.of(context).dividerColor),
                    borderRadius: BorderRadius.circular(AnumRadius.lg)),
                child: Padding(
                    padding: const EdgeInsets.all(AnumSpacing.sm),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(idea.title, style: text.titleSmall),
                          Text(
                              [
                                if (idea.platform.isNotEmpty)
                                  l.t('mk.platform.${idea.platform}'),
                                if (idea.angle.isNotEmpty) idea.angle
                              ].join(' · '),
                              style: text.bodySmall),
                          if (idea.en != null && idea.en!.isNotEmpty)
                            Padding(
                                padding:
                                    const EdgeInsets.only(top: AnumSpacing.xs),
                                child: Text(idea.en!,
                                    textDirection: TextDirection.ltr)),
                          if (idea.ar != null && idea.ar!.isNotEmpty)
                            Padding(
                                padding:
                                    const EdgeInsets.only(top: AnumSpacing.xs),
                                child: Text(idea.ar!,
                                    textDirection: TextDirection.rtl)),
                          const SizedBox(height: AnumSpacing.xs),
                          Align(
                              alignment: AlignmentDirectional.centerStart,
                              child: OutlinedButton(
                                  onPressed: onUse,
                                  child: Text(l.t('mkOps.ai.use')))),
                        ])))));
  }
}
