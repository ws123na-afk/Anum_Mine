import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../admin/admin_controller.dart' show AdminPhase;
import 'autonomy_controller.dart';
import 'autonomy_models.dart';

String _time(DateTime value) {
  final local = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
}

List<Widget> _phaseState(
  BuildContext context,
  AdminPhase phase,
  String? message,
  String what,
  VoidCallback retry,
) {
  final p = context.palette;
  final l = AnumLocalizations.maybeOf(context);
  return switch (phase) {
    AdminPhase.initial || AdminPhase.loading => [
        const Padding(
          padding: EdgeInsets.all(AnumSpacing.lg),
          child: Center(child: CircularProgressIndicator()),
        ),
      ],
    AdminPhase.forbidden => [
        AnumSurface(
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('auto.accessRequired'),
            message: l.f('auto.accessMessage',
                {'what': l.t('auto.what.$what'), 'message': message ?? ''}),
          ),
        ),
      ],
    _ => [
        AnumSurface(
          accent: p.stop,
          child: AnumEmpty(
            icon: phase == AdminPhase.offline
                ? Icons.cloud_off_outlined
                : Icons.error_outline,
            title:
                l.f('auto.loadFailed', {'what': l.t('auto.what.${what}Title')}),
            message: l.f('auto.checkConnection', {'message': message ?? ''}),
            action: l.t('da.retry'),
            onAction: retry,
          ),
        ),
      ],
  };
}

/// Everything ANUM did on its own (docs/autonomy.md), with Undo while the
/// window is open, "Always ask for this", suggestions, a filter and search.
class ActivityScreen extends StatefulWidget {
  const ActivityScreen({
    required this.controller,
    this.onOpenSettings,
    super.key,
  });
  final ActivityController controller;
  final VoidCallback? onOpenSettings;
  @override
  State<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends State<ActivityScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final l = AnumLocalizations.maybeOf(context);
          c.l = l;
          return Scaffold(
            appBar: AppBar(
              title: Text(l.t('auto.activity')),
              actions: [
                IconButton(
                  tooltip: l.t('da.refresh'),
                  onPressed: c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                AnumHeader(
                  eyebrow: l.t('auto.eyebrow'),
                  title: l.t('auto.activityTitle'),
                  subtitle: l.t('auto.activitySubtitle'),
                  large: true,
                ),
                if (c.proposals.isNotEmpty) _proposals(context, c),
                _filters(l, c),
                if (c.notice != null)
                  Semantics(
                    liveRegion: true,
                    child: Text(
                      c.notice!,
                      key: const Key('activity-notice'),
                      style: TextStyle(
                        color: c.noticeFailed
                            ? context.palette.stop
                            : context.palette.ok,
                      ),
                    ),
                  ),
                ...c.phase == AdminPhase.ready
                    ? _feed(context, c)
                    : _phaseState(
                        context,
                        c.phase,
                        c.loadMessage,
                        'activity',
                        c.load,
                      ),
              ],
            ),
          );
        },
      );

  Widget _proposals(BuildContext context, ActivityController c) => AnumSurface(
        accent: context.palette.violet,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(
              title: c.l.t('auto.suggestions'),
              subtitle: c.l.t('auto.suggestionsSubtitle'),
            ),
            for (final p in c.proposals) ...[
              const SizedBox(height: AnumSpacing.sm),
              Text(p.sentenceIn(c.l)),
              const SizedBox(height: AnumSpacing.xs),
              if (c.canDecide)
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    FilledButton(
                      onPressed: c.busy == p.id
                          ? null
                          : () => c.decide(p, accept: true),
                      child: Text(c.l.t('auto.accept')),
                    ),
                    OutlinedButton(
                      onPressed: c.busy == p.id
                          ? null
                          : () => c.decide(p, accept: false),
                      child: Text(c.l.t('auto.keepAsking')),
                    ),
                  ],
                )
              else
                Text(
                  c.l.t('auto.ownerDecides'),
                  style: TextStyle(color: context.palette.muted),
                ),
            ],
          ],
        ),
      );

  Widget _filters(AnumLocalizations l, ActivityController c) => Wrap(
        spacing: AnumSpacing.sm,
        runSpacing: AnumSpacing.sm,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 320),
            child: DropdownButtonFormField<String>(
              key: const Key('activity-category'),
              initialValue: c.category,
              isExpanded: true,
              decoration: InputDecoration(labelText: l.t('auto.category')),
              items: [
                DropdownMenuItem(value: '', child: Text(l.t('auto.all'))),
                for (final category in autonomyCategories)
                  DropdownMenuItem(
                    value: category,
                    child: Text(categoryLabel(category, l)),
                  ),
              ],
              onChanged: (v) => c.setCategory(v ?? ''),
            ),
          ),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: TextField(
              key: const Key('activity-search'),
              controller: _search,
              maxLength: 200,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                labelText: l.t('da.search'),
                counterText: '',
                suffixIcon: IconButton(
                  tooltip: l.t('auto.searchActivity'),
                  onPressed: () => c.search(_search.text),
                  icon: const Icon(Icons.search),
                ),
              ),
              onSubmitted: c.search,
            ),
          ),
          if (c.filtered)
            TextButton(
              onPressed: () {
                _search.clear();
                c.clearFilters();
              },
              child: Text(l.t('auto.clearFilters')),
            ),
        ],
      );

  List<Widget> _feed(BuildContext context, ActivityController c) {
    final p = context.palette;
    final l = c.l;
    if (c.items.isEmpty) {
      return [
        AnumSurface(
          child: c.filtered
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: l.t('auto.noMatch'),
                  message: l.t('auto.noMatchText'),
                  action: l.t('auto.clearFilters'),
                  onAction: () {
                    _search.clear();
                    c.clearFilters();
                  },
                )
              : AnumEmpty(
                  icon: Icons.notifications_none,
                  title: l.t('auto.emptyTitle'),
                  message: l.t('auto.emptyText'),
                  action: widget.onOpenSettings == null
                      ? null
                      : l.t('auto.openSettings'),
                  onAction: widget.onOpenSettings,
                ),
        ),
      ];
    }
    final now = c.clock();
    return [
      for (final a in c.items)
        AnumSurface(
          key: Key('action-${a.id}'),
          semanticLabel: a.summary,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                a.summary,
                style: TextStyle(
                  color: a.undone ? p.muted : p.text,
                  fontWeight: FontWeight.w600,
                  decoration: a.undone ? TextDecoration.lineThrough : null,
                ),
              ),
              const SizedBox(height: AnumSpacing.xxs),
              Text(
                '${categoryLabel(a.category, l)} · ${a.tool}${a.template != null ? ' · ${a.template}' : ''} · ${_time(a.createdAt)}',
                style: TextStyle(color: p.faint, fontSize: 12),
              ),
              const SizedBox(height: AnumSpacing.xs),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  AnumPill(
                    label: levelLabel(a.level, l),
                    tone: a.level == 'act_silently'
                        ? AnumTone.neutral
                        : AnumTone.ok,
                  ),
                  if (a.undone)
                    AnumPill(
                      label: l.f('auto.undoneBy', {'user': a.undoneBy ?? '?'}),
                      tone: AnumTone.ok,
                    )
                  else if (a.reversible && a.undoExpiresAt != null)
                    AnumPill(
                      label: now.isBefore(a.undoExpiresAt!)
                          ? l.f('auto.undoUntil',
                              {'time': _time(a.undoExpiresAt!).substring(11)})
                          : l.t('auto.undoClosed'),
                      tone: AnumTone.warn,
                    ),
                  if (a.asksNow)
                    AnumPill(
                      label: l.t('auto.asksNow'),
                      tone: AnumTone.warn,
                    ),
                ],
              ),
              if (c.rowErrors[a.id] != null) ...[
                const SizedBox(height: AnumSpacing.xs),
                Semantics(
                  liveRegion: true,
                  child: Text(
                    c.rowErrors[a.id]!,
                    style: TextStyle(color: p.stop),
                  ),
                ),
              ],
              const SizedBox(height: AnumSpacing.xs),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (a.undoableAt(now))
                    FilledButton.icon(
                      key: Key('undo-${a.id}'),
                      onPressed: c.busy == a.id ? null : () => c.undo(a),
                      icon: const Icon(Icons.undo),
                      label: Text(l.t('auto.undo')),
                    ),
                  if (a.reversible && !a.undone && !a.canUndo)
                    Text(
                      l.t('auto.undoWho'),
                      style: TextStyle(color: p.muted, fontSize: 12),
                    ),
                  if (c.canTighten && !a.asksNow)
                    OutlinedButton.icon(
                      key: Key('tighten-${a.id}'),
                      onPressed: c.busy == a.id ? null : () => c.tighten(a),
                      icon: const Icon(Icons.pan_tool_outlined),
                      label: Text(l.t('auto.alwaysAsk')),
                    ),
                ],
              ),
            ],
          ),
        ),
      if (c.nextCursor != null)
        Center(
          child: OutlinedButton(
            onPressed: c.loadingMore ? null : c.loadMore,
            child:
                Text(c.loadingMore ? l.t('da.loading') : l.t('auto.loadMore')),
          ),
        ),
    ];
  }
}

/// The workspace's autonomy levels, thresholds and approved templates. The
/// organization's guardrails are edited on the web by an administrator.
class AutonomySettingsScreen extends StatefulWidget {
  const AutonomySettingsScreen({required this.controller, super.key});
  final AutonomySettingsController controller;
  @override
  State<AutonomySettingsScreen> createState() => _AutonomySettingsScreenState();
}

class _AutonomySettingsScreenState extends State<AutonomySettingsScreen> {
  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final l = AnumLocalizations.maybeOf(context);
          c.l = l;
          return Scaffold(
            appBar: AppBar(
              title: Text(l.t('auto.settings')),
              actions: [
                IconButton(
                  tooltip: l.t('da.refresh'),
                  onPressed: c.saving ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                AnumHeader(
                  eyebrow: l.t('auto.eyebrow'),
                  title: l.t('auto.settingsTitle'),
                  subtitle: l.t('auto.settingsSubtitle'),
                  large: true,
                ),
                ...c.phase == AdminPhase.ready
                    ? _ready(context, c)
                    : _phaseState(
                        context,
                        c.phase,
                        c.loadMessage,
                        'settings',
                        c.load,
                      ),
              ],
            ),
          );
        },
      );

  List<Widget> _ready(BuildContext context, AutonomySettingsController c) {
    final p = context.palette;
    final o = c.overview!;
    final l = c.l;
    return [
      if (c.readOnly)
        AnumSurface(
          key: const Key('autonomy-owner-required'),
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: l.t('auto.ownerRequired'),
            message: [
              l.t('auto.ownerOnly'),
              if (c.denied != null)
                l.f('da.apiAnswered', {'message': c.denied!}),
            ].join('\n'),
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(
              title: l.t('auto.levels'),
              subtitle: l.t('auto.levelsSubtitle'),
            ),
            for (final category in autonomyCategories) ...[
              const SizedBox(height: AnumSpacing.sm),
              DropdownButtonFormField<String>(
                key: Key('level-$category'),
                initialValue: c.levels[category],
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: categoryLabel(category, l),
                  helperText: o.ceilings[category] == null
                      ? null
                      : l.f('auto.orgLimit',
                          {'level': levelLabel(o.ceilings[category]!, l)}),
                ),
                items: [
                  for (final level in autonomyLevels)
                    DropdownMenuItem(
                      value: level,
                      enabled: levelAllowed(level, o.ceilings[category]),
                      child: Text(levelLabel(level, l)),
                    ),
                ],
                onChanged: c.readOnly || c.saving
                    ? null
                    : (v) => c.setLevel(category, v ?? 'ask_always'),
              ),
            ],
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(
              title: l.t('auto.thresholds'),
              subtitle: l.t('auto.thresholdsSubtitle'),
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextFormField(
              key: const Key('recipients-threshold'),
              initialValue: c.recipients,
              enabled: !c.readOnly,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: l.t('auto.recipients'),
                helperText: o.maxAutoRecipients == null
                    ? null
                    : l.f('auto.recipientsMax', {'max': o.maxAutoRecipients!}),
              ),
              onChanged: (v) => c.recipients = v,
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextFormField(
              key: const Key('amount-threshold'),
              initialValue: c.amount,
              enabled: !c.readOnly,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: InputDecoration(labelText: l.t('auto.amount')),
              onChanged: (v) => c.amount = v,
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextFormField(
              key: const Key('approved-templates'),
              initialValue: c.templates,
              enabled: !c.readOnly,
              minLines: 2,
              maxLines: 6,
              decoration: InputDecoration(
                labelText: l.t('auto.templates'),
                helperText: l.t('auto.templatesHelp'),
                helperMaxLines: 3,
              ),
              onChanged: (v) => c.templates = v,
            ),
            if (o.spendingLimit != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              Text(
                l.f('auto.spendingLimit', {'limit': o.spendingLimit!}),
                style: TextStyle(color: p.muted, fontSize: 13),
              ),
            ],
            const SizedBox(height: AnumSpacing.xs),
            Text(
              l.t('auto.webOnly'),
              style: TextStyle(color: p.faint, fontSize: 12),
            ),
            if (c.notice != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              Semantics(
                liveRegion: true,
                child: Text(
                  c.notice!,
                  key: const Key('autonomy-notice'),
                  style: TextStyle(color: c.noticeFailed ? p.stop : p.ok),
                ),
              ),
            ],
            if (!c.readOnly) ...[
              const SizedBox(height: AnumSpacing.md),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: const Key('autonomy-save'),
                  onPressed: c.saving ? null : c.save,
                  icon: const Icon(Icons.save_outlined),
                  label: Text(c.saving ? l.t('da.saving') : l.t('auto.save')),
                ),
              ),
            ],
          ],
        ),
      ),
    ];
  }
}
