import 'package:flutter/material.dart';

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
            title: 'Access required',
            message:
                'Reading $what needs an active membership in this workspace. '
                'Ask a workspace owner to invite you.\nThe API answered: $message',
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
                '${what[0].toUpperCase()}${what.substring(1)} could not be loaded',
            message: '${message ?? ''} Check your connection and try again.',
            action: 'Retry',
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
          return Scaffold(
            appBar: AppBar(
              title: const Text('Activity'),
              actions: [
                IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                const AnumHeader(
                  eyebrow: 'Autonomy',
                  title: 'What ANUM did on its own',
                  subtitle:
                      'Every action ANUM took without asking, newest first. Undo what can be reversed, or make a kind of action ask again.',
                  large: true,
                ),
                if (c.proposals.isNotEmpty) _proposals(context, c),
                _filters(c),
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
                        'the activity',
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
            const AnumHeader(
              title: 'Suggestions to ask less',
              subtitle:
                  'You approved these actions several times. ANUM never raises its autonomy by itself.',
            ),
            for (final p in c.proposals) ...[
              const SizedBox(height: AnumSpacing.sm),
              Text(p.sentence),
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
                      child: const Text('Accept'),
                    ),
                    OutlinedButton(
                      onPressed: c.busy == p.id
                          ? null
                          : () => c.decide(p, accept: false),
                      child: const Text('Keep asking'),
                    ),
                  ],
                )
              else
                Text(
                  'An owner of this workspace decides.',
                  style: TextStyle(color: context.palette.muted),
                ),
            ],
          ],
        ),
      );

  Widget _filters(ActivityController c) => Wrap(
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
              decoration: const InputDecoration(labelText: 'Category'),
              items: [
                const DropdownMenuItem(value: '', child: Text('All')),
                for (final category in autonomyCategories)
                  DropdownMenuItem(
                    value: category,
                    child: Text(categoryLabel(category)),
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
                labelText: 'Search',
                counterText: '',
                suffixIcon: IconButton(
                  tooltip: 'Search the activity',
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
              child: const Text('Clear filters'),
            ),
        ],
      );

  List<Widget> _feed(BuildContext context, ActivityController c) {
    final p = context.palette;
    if (c.items.isEmpty) {
      return [
        AnumSurface(
          child: c.filtered
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: 'No matching actions',
                  message: 'Nothing ANUM did matches these filters.',
                  action: 'Clear filters',
                  onAction: () {
                    _search.clear();
                    c.clearFilters();
                  },
                )
              : AnumEmpty(
                  icon: Icons.notifications_none,
                  title: 'Nothing done on its own yet',
                  message:
                      'When ANUM acts without asking, each action appears here with Undo where it can be reversed.',
                  action: widget.onOpenSettings == null
                      ? null
                      : 'Autonomy settings',
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
                '${categoryLabel(a.category)} · ${a.tool}${a.template != null ? ' · ${a.template}' : ''} · ${_time(a.createdAt)}',
                style: TextStyle(color: p.faint, fontSize: 12),
              ),
              const SizedBox(height: AnumSpacing.xs),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  AnumPill(
                    label: levelLabel(a.level),
                    tone: a.level == 'act_silently'
                        ? AnumTone.neutral
                        : AnumTone.ok,
                  ),
                  if (a.undone)
                    AnumPill(
                      label: 'Undone by ${a.undoneBy ?? '?'}',
                      tone: AnumTone.ok,
                    )
                  else if (a.reversible && a.undoExpiresAt != null)
                    AnumPill(
                      label: now.isBefore(a.undoExpiresAt!)
                          ? 'Undo until ${_time(a.undoExpiresAt!).substring(11)}'
                          : 'Undo window closed',
                      tone: AnumTone.warn,
                    ),
                  if (a.asksNow)
                    const AnumPill(
                      label: 'Asks from now on',
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
                      label: const Text('Undo'),
                    ),
                  if (a.reversible && !a.undone && !a.canUndo)
                    Text(
                      'Only an owner or the person whose task did this can undo it.',
                      style: TextStyle(color: p.muted, fontSize: 12),
                    ),
                  if (c.canTighten && !a.asksNow)
                    OutlinedButton.icon(
                      key: Key('tighten-${a.id}'),
                      onPressed: c.busy == a.id ? null : () => c.tighten(a),
                      icon: const Icon(Icons.pan_tool_outlined),
                      label: const Text('Always ask for this'),
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
            child: Text(c.loadingMore ? 'Loading...' : 'Load more'),
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
          return Scaffold(
            appBar: AppBar(
              title: const Text('Autonomy'),
              actions: [
                IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.saving ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
            ),
            body: AnumPage(
              onRefresh: c.load,
              children: [
                const AnumHeader(
                  eyebrow: 'Autonomy',
                  title: 'When ANUM may act without asking',
                  subtitle:
                      'Money, prices, stock, deleting, merging code, new recipients and bulk sends always ask.',
                  large: true,
                ),
                ...c.phase == AdminPhase.ready
                    ? _ready(context, c)
                    : _phaseState(
                        context,
                        c.phase,
                        c.loadMessage,
                        'autonomy settings',
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
    return [
      if (c.readOnly)
        AnumSurface(
          key: const Key('autonomy-owner-required'),
          accent: p.warn,
          child: AnumEmpty(
            icon: Icons.lock_outline,
            title: 'Owner access required',
            message: [
              'Only workspace owners change autonomy. What is in force is shown below; ask an owner to change it.',
              if (c.denied != null) 'The API answered: ${c.denied}',
            ].join('\n'),
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const AnumHeader(
              title: 'Level by area',
              subtitle:
                  'A level is the most ANUM may do in that area. Reading and drafts run silently; archiving and replies to known customers with approved templates act and notify.',
            ),
            for (final category in autonomyCategories) ...[
              const SizedBox(height: AnumSpacing.sm),
              DropdownButtonFormField<String>(
                key: Key('level-$category'),
                initialValue: c.levels[category],
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: categoryLabel(category),
                  helperText: o.ceilings[category] == null
                      ? null
                      : 'Organization limit: ${levelLabel(o.ceilings[category]!)}',
                ),
                items: [
                  for (final level in autonomyLevels)
                    DropdownMenuItem(
                      value: level,
                      enabled: levelAllowed(level, o.ceilings[category]),
                      child: Text(levelLabel(level)),
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
            const AnumHeader(
              title: 'Thresholds',
              subtitle: 'Anything above these asks first, whatever the level.',
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextFormField(
              key: const Key('recipients-threshold'),
              initialValue: c.recipients,
              enabled: !c.readOnly,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                labelText: 'Recipients threshold',
                helperText: o.maxAutoRecipients == null
                    ? null
                    : 'The organization allows at most ${o.maxAutoRecipients}.',
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
              decoration: const InputDecoration(labelText: 'Amount threshold'),
              onChanged: (v) => c.amount = v,
            ),
            const SizedBox(height: AnumSpacing.sm),
            TextFormField(
              key: const Key('approved-templates'),
              initialValue: c.templates,
              enabled: !c.readOnly,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(
                labelText: 'Approved templates',
                helperText:
                    'One per line, for example order_status. Replies with these to known recipients act and notify.',
                helperMaxLines: 3,
              ),
              onChanged: (v) => c.templates = v,
            ),
            if (o.spendingLimit != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              Text(
                'Organization spending limit: ${o.spendingLimit}. Anything above is blocked, even with approval.',
                style: TextStyle(color: p.muted, fontSize: 13),
              ),
            ],
            const SizedBox(height: AnumSpacing.xs),
            Text(
              'Quiet hours, daily caps, blocked recipients and the organization guardrails are edited on the web.',
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
                  label: Text(c.saving ? 'Saving...' : 'Save autonomy'),
                ),
              ),
            ],
          ],
        ),
      ),
    ];
  }
}
