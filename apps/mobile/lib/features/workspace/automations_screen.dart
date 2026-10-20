import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../automations/automation_models.dart';
import '../automations/automation_repository.dart';
import '../automations/automation_triggers_card.dart';
import '../automations/automation_templates.dart';
import '../automations/automations_controller.dart';
import 'list_kit.dart';

AnumTone automationTone(String status) => switch (status) {
      'running' || 'active' => AnumTone.info,
      'completed' || 'succeeded' => AnumTone.ok,
      'paused' || 'waiting' || 'waiting_approval' => AnumTone.warn,
      'failed' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

String humanStatus(String status) => statusLabel(status);

void _say(BuildContext context, String message) =>
    ScaffoldMessenger.maybeOf(context)
        ?.showSnackBar(SnackBar(content: Text(message)));

/// The tabs of the Automations list (mockup 11). The API has no draft
/// state: a paused automation is the one that does not fire, so the
/// mockup's "Draft" tab is "Paused" here (docs/mobile.md).
enum AutomationTab { all, active, paused, templates }

String automationTabStatus(AutomationTab tab) => switch (tab) {
      AutomationTab.active => 'active',
      AutomationTab.paused => 'disabled',
      _ => '',
    };

/// The icon of an automation: what its first step does.
IconData automationIcon(AutomationWorkflow w) {
  final action = w.steps.isEmpty ? '' : w.steps.first.action;
  return switch (action) {
    'task.create' => Icons.smart_toy_outlined,
    'notify' => Icons.notifications_active_outlined,
    'pause' => Icons.front_hand_outlined,
    _ => Icons.account_tree_outlined,
  };
}

/// Automations (docs/automation.md): the list with a pause/resume switch on
/// each row, search, tabs and "Show more"; each automation opens its steps,
/// schedules and run history. Owners change any automation, members their
/// own, viewers read only, and the screen says so.
class AutomationsScreen extends StatefulWidget {
  const AutomationsScreen({required this.automations, super.key});
  final AutomationsController automations;

  @override
  State<AutomationsScreen> createState() => _AutomationsScreenState();
}

class _AutomationsScreenState extends State<AutomationsScreen> {
  late final TextEditingController _search =
      TextEditingController(text: widget.automations.query);
  late AutomationTab _tab = switch (widget.automations.status) {
    'active' => AutomationTab.active,
    'disabled' => AutomationTab.paused,
    _ => AutomationTab.all,
  };

  @override
  void initState() {
    super.initState();
    widget.automations.start();
  }

  @override
  void didUpdateWidget(AutomationsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Another workspace's controller: load it too.
    if (oldWidget.automations != widget.automations) widget.automations.start();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _create([AutomationTemplate? template]) async {
    final created = await Navigator.of(context).push<AutomationWorkflow>(
        MaterialPageRoute(
            builder: (_) => AutomationEditorScreen(
                controller: widget.automations, template: template)));
    if (created != null && mounted) {
      _say(
          context,
          AnumLocalizations.maybeOf(context)
              .f('autoCreated', {'name': created.name}));
      _open(created.id, suggest: template);
    }
  }

  void _open(String id, {AutomationTemplate? suggest}) =>
      Navigator.of(context).push<void>(MaterialPageRoute(
          builder: (_) => AutomationDetailScreen(
              controller: widget.automations,
              workflowId: id,
              suggest: suggest)));

  void _select(AutomationTab tab) {
    setState(() => _tab = tab);
    if (tab != AutomationTab.templates) {
      widget.automations.filter(automationTabStatus(tab));
    }
  }

  Future<void> _toggle(AutomationWorkflow w, bool enabled) async {
    final l = AnumLocalizations.maybeOf(context);
    final failure = await widget.automations.setEnabled(w, enabled);
    if (!mounted) return;
    _say(
        context,
        failure ??
            l.f(enabled ? 'autoResumed' : 'autoPaused', {'name': w.name}));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.automations,
        builder: (context, _) {
          final c = widget.automations;
          final l = AnumLocalizations.maybeOf(context);
          final p = context.palette;
          return AnumPage(
            onRefresh: c.refresh,
            children: [
              AnumListHeader(
                title: l.t('autoTitle'),
                subtitle: l.t('autoSubtitle'),
                actionTooltip: l.t('autoNew'),
                onAction: c.canManage ? () => _create() : null,
              ),
              AnumSegmentTabs<AutomationTab>(
                segments: [
                  AnumSegment(AutomationTab.all, l.t('listTabAll'),
                      key: const Key('auto-tab-all')),
                  AnumSegment(AutomationTab.active, l.t('autoTabActive'),
                      key: const Key('auto-tab-active')),
                  AnumSegment(AutomationTab.paused, l.t('autoTabPaused'),
                      key: const Key('auto-tab-paused')),
                  AnumSegment(AutomationTab.templates, l.t('autoTabTemplates'),
                      key: const Key('auto-tab-templates')),
                ],
                selected: _tab,
                onSelected: _select,
              ),
              if (!c.canManage && c.role != null)
                Text(l.t('autoViewerHint'),
                    key: const Key('auto-viewer-hint'),
                    style:
                        TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
              if (_tab == AutomationTab.templates)
                ..._templates(context, c)
              else ...[
                TextField(
                  key: const Key('auto-search'),
                  controller: _search,
                  decoration: InputDecoration(
                      hintText: l.t('autoSearch'),
                      prefixIcon: const Icon(Icons.search)),
                  textInputAction: TextInputAction.search,
                  onChanged: c.search,
                ),
                ..._list(context, c),
              ],
            ],
          );
        },
      );

  List<Widget> _templates(BuildContext context, AutomationsController c) {
    final l = AnumLocalizations.maybeOf(context);
    return [
      Text(l.t('autoTemplatesHint'),
          style: TextStyle(
              color: context.palette.muted, fontSize: AnumType.smPlus)),
      AnumListCard(children: [
        for (final template in automationTemplates)
          AnumListRow(
            key: Key('auto-template-${template.id}'),
            leading: AnumTileIcon(
                icon: template.icon,
                color: anumToneColor(context, template.tone)),
            title: template.name(l),
            subtitle: template.description(l),
            onTap: c.canManage ? () => _create(template) : null,
            trailing: c.canManage
                ? null
                : Icon(Icons.lock_outline, color: context.palette.faint),
          ),
      ]),
    ];
  }

  List<Widget> _list(BuildContext context, AutomationsController c) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    if (c.phase == AutomationLoad.loading) {
      return [AnumListLoading(label: l.t('autoLoading'))];
    }
    if (c.phase == AutomationLoad.error) {
      return [
        AnumSurface(
          accent: p.stop,
          child: AnumEmpty(
            icon: c.errorStatus == 403
                ? Icons.lock_outline
                : Icons.cloud_off_outlined,
            title: c.errorStatus == 403
                ? l.t('autoDenied')
                : l.t('autoLoadFailed'),
            message: c.errorStatus == 403
                ? '${c.error ?? ''} ${l.t('autoDeniedHint')}'.trim()
                : c.error ?? l.t('retry'),
            action: c.errorStatus == 403 ? null : l.t('retry'),
            onAction: c.errorStatus == 403 ? null : c.refresh,
          ),
        )
      ];
    }
    final custom = AnumListRow(
      key: const Key('auto-custom'),
      leading: AnumTileIcon(icon: Icons.tune, color: p.violet),
      title: l.t('autoCustom'),
      subtitle: l.t('autoCustomHint'),
      onTap: () => _create(),
    );
    if (c.workflows.isEmpty) {
      return [
        AnumSurface(
          child: c.filtered
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: l.t('autoNoMatch'),
                  message:
                      _tab == AutomationTab.paused && c.query.trim().isEmpty
                          ? l.t('autoNoPaused')
                          : l.t('autoNoMatchHint'),
                  action: l.t('tasksClearFilters'),
                  onAction: () {
                    _search.clear();
                    setState(() => _tab = AutomationTab.all);
                    c.clearFilters();
                  })
              : AnumEmpty(
                  icon: Icons.account_tree_outlined,
                  title: l.t('autoEmpty'),
                  message: c.canManage
                      ? l.t('autoEmptyHint')
                      : l.t('autoEmptyViewer'),
                  action: c.canManage ? l.t('autoBrowseTemplates') : null,
                  onAction: c.canManage
                      ? () => _select(AutomationTab.templates)
                      : null,
                ),
        ),
        if (c.canManage && !c.filtered) AnumListCard(children: [custom]),
      ];
    }
    return [
      AnumListCard(
        footer: c.canManage ? custom : null,
        children: [
          for (final w in c.workflows) _row(context, c, w),
        ],
      ),
      if (c.hasMore)
        AnumLoadMore(
            key: const Key('auto-load-more'),
            loading: c.loadingMore,
            onPressed: c.loadMore),
    ];
  }

  Widget _row(
      BuildContext context, AutomationsController c, AutomationWorkflow w) {
    final l = AnumLocalizations.maybeOf(context);
    final schedule = c.scheduleOf(w.id);
    final when = w.paused
        ? l.t('autoPausedLine')
        : schedule != null
            ? schedule.words(arabic: l.isArabic)
            : w.nextRunAt != null
                ? l.f('autoNext', {'when': relativeWhen(w.nextRunAt!)})
                : l.t('autoOnDemand');
    final last = w.lastRunStatus == null
        ? l.t('autoNeverRun')
        : l.f('autoLastRun', {
            'status': statusLabel(w.lastRunStatus!),
            'when': w.lastRunAt == null ? '' : relativeWhen(w.lastRunAt!),
          }).trim();
    final editable = c.canEdit(w.createdBy);
    final tone = automationTone(w.lastRunStatus ?? w.status);
    return AnumListRow(
      key: Key('auto-row-${w.id}'),
      leading: AnumTileIcon(
          icon: automationIcon(w), color: anumToneColor(context, tone)),
      title: w.name,
      subtitle: when,
      detail: Text(last,
          style:
              TextStyle(color: context.palette.faint, fontSize: AnumType.xs)),
      semanticLabel: '${w.name}, $when, $last',
      trailing: editable
          ? Semantics(
              label: l.f('autoSwitchLabel', {'name': w.name}),
              child: Switch(
                key: Key('auto-switch-${w.id}'),
                value: !w.paused,
                onChanged: c.toggling.contains(w.id)
                    ? null
                    : (value) => _toggle(w, value),
              ),
            )
          : AnumPill(
              label: w.paused ? l.t('autoTabPaused') : l.t('autoTabActive'),
              tone: w.paused ? AnumTone.warn : AnumTone.info),
      onTap: () => _open(w.id),
    );
  }
}

// --------------------------------------------------------------------- Detail

class AutomationDetailScreen extends StatefulWidget {
  const AutomationDetailScreen(
      {required this.controller,
      required this.workflowId,
      this.suggest,
      super.key});
  final AutomationsController controller;
  final String workflowId;

  /// The template it was just created from: its suggested schedule is
  /// offered at once (added only if the person saves it).
  final AutomationTemplate? suggest;

  @override
  State<AutomationDetailScreen> createState() => _AutomationDetailState();
}

class _AutomationDetailState extends State<AutomationDetailScreen> {
  static const _runPage = 10;
  AutomationWorkflow? workflow;
  List<AutomationScheduleItem> schedules = const [];
  List<AutomationRunItem> runs = const [];
  bool runsHasMore = false, busy = false, loading = true;
  String? error, scheduleError, runError;
  String runFilter = '';
  final Set<String> expanded = {};

  AutomationsController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    _load();
    final suggest = widget.suggest;
    if (suggest != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _schedule(null, suggest);
      });
    }
  }

  Future<void> _load() async {
    setState(() => loading = workflow == null);
    try {
      final w = await c.repository.workflow(widget.workflowId);
      if (!mounted) return;
      setState(() {
        workflow = w;
        error = null;
      });
      c.replace(w);
    } catch (e) {
      if (!mounted) return;
      setState(() => error = automationErrorText(e));
    }
    await Future.wait([_loadSchedules(), _loadRuns()]);
    if (mounted) setState(() => loading = false);
  }

  Future<void> _loadSchedules() async {
    try {
      final items = await c.repository.schedules(widget.workflowId);
      if (!mounted) return;
      setState(() {
        schedules = items;
        scheduleError = null;
      });
    } catch (e) {
      if (mounted) setState(() => scheduleError = automationErrorText(e));
    }
  }

  Future<void> _loadRuns({bool more = false}) async {
    try {
      final page = await c.repository.runs(widget.workflowId,
          status: runFilter, limit: _runPage, offset: more ? runs.length : 0);
      if (!mounted) return;
      setState(() {
        runs = more ? [...runs, ...page.items] : page.items;
        runsHasMore = page.hasMore;
        runError = null;
      });
    } catch (e) {
      if (mounted) setState(() => runError = automationErrorText(e));
    }
  }

  Future<void> _act(Future<void> Function() action, String done) async {
    setState(() => busy = true);
    final failure = await c.run(action);
    if (!mounted) return;
    setState(() => busy = false);
    _say(context, failure ?? done);
    await _load();
  }

  Future<void> _delete(AutomationWorkflow w) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete "${w.name}"?'),
        content: Text(w.activeRuns > 0
            ? 'It has ${w.activeRuns} active run(s). Cancel them first, then delete it.'
            : 'This removes its ${w.scheduleCount} schedule(s) and ${w.runCount} '
                'run(s) of history. Tasks its runs created stay under Tasks.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep it')),
          if (w.activeRuns == 0)
            FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Delete')),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    setState(() => busy = true);
    final failure = await c.run(() => c.repository.deleteWorkflow(w.id));
    if (!mounted) return;
    setState(() => busy = false);
    if (failure != null) return _say(context, failure);
    _say(context, 'Deleted "${w.name}" with its schedules and run history.');
    Navigator.of(context).pop();
  }

  Future<void> _edit(AutomationWorkflow w) async {
    final saved = await Navigator.of(context).push<AutomationWorkflow>(
        MaterialPageRoute(
            builder: (_) =>
                AutomationEditorScreen(controller: c, existing: w)));
    if (saved != null && mounted) {
      _say(context, 'Saved "${saved.name}" as version ${saved.version}.');
      await _load();
    }
  }

  Future<void> _schedule(
      [AutomationScheduleItem? existing, AutomationTemplate? suggest]) async {
    final l = AnumLocalizations.maybeOf(context);
    final saved = await showModalBottomSheet<AutomationScheduleItem>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => ScheduleSheet(
          controller: c,
          workflowId: widget.workflowId,
          existing: existing,
          initialName: suggest?.scheduleName(l),
          initialCron: suggest?.cron),
    );
    if (saved != null && mounted) {
      _say(
          context,
          existing == null
              ? 'Schedule "${saved.name}" added.'
              : 'Schedule saved.');
      await _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final w = workflow;
    return Scaffold(
      appBar: AppBar(title: Text(w?.name ?? 'Automation')),
      body: loading
          ? const Center(
              child: CircularProgressIndicator(
                  semanticsLabel: 'Loading automation'))
          : w == null
              ? AnumPage(onRefresh: _load, children: [
                  AnumEmpty(
                      icon: Icons.error_outline,
                      title: 'Could not open this automation',
                      message: error ?? 'It may have been deleted.',
                      action: 'Try again',
                      onAction: _load)
                ])
              : AnumPage(onRefresh: _load, children: [
                  _summary(context, w),
                  _schedulesCard(context, w),
                  if (c.repository
                      case final AutomationTriggerRepository triggers)
                    AutomationTriggersCard(
                        controller: c,
                        repository: triggers,
                        workflowId: w.id,
                        onChanged: _loadRuns),
                  _runsCard(context, w),
                  const SizedBox(height: AnumSpacing.lg),
                ]),
    );
  }

  Widget _fact(BuildContext context, String label, String value) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        Text(value, style: TextStyle(color: p.text, fontSize: AnumType.md)),
      ]),
    );
  }

  Widget _summary(BuildContext context, AutomationWorkflow w) {
    final p = context.palette;
    final editable = c.canEdit(w.createdBy);
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        AnumHeader(
            title: w.name,
            subtitle: w.description.isEmpty ? null : w.description,
            trailing: AnumPill(
                label: statusLabel(w.status), tone: automationTone(w.status))),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.lg, children: [
          _fact(
              context,
              'Last run',
              w.lastRunStatus == null
                  ? 'Never'
                  : '${statusLabel(w.lastRunStatus!)} ${w.lastRunAt == null ? '' : relativeWhen(w.lastRunAt!)}'),
          _fact(
              context,
              'Next run',
              w.paused
                  ? 'Paused'
                  : w.nextRunAt == null
                      ? 'No schedule'
                      : '${exactTime(w.nextRunAt!)} (${relativeWhen(w.nextRunAt!)})'),
          _fact(context, 'Overlapping runs',
              overlapPolicies[w.overlapPolicy] ?? w.overlapPolicy),
          _fact(context, 'Version', 'v${w.version}'),
          _fact(context, 'Created by', w.createdBy ?? 'Unknown'),
        ]),
        if (c.canManage) ...[
          const SizedBox(height: AnumSpacing.xs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
                onPressed: busy || w.paused
                    ? null
                    : () => _act(
                        () => c.repository.start(
                            w.id, AutomationsController.idempotencyKey()),
                        'Run of "${w.name}" started. See it under Run history.'),
                icon: const Icon(Icons.play_arrow_rounded),
                label: const Text('Run now')),
            if (editable) ...[
              OutlinedButton.icon(
                  onPressed: busy ? null : () => _edit(w),
                  icon: const Icon(Icons.edit_outlined),
                  label: const Text('Edit')),
              OutlinedButton.icon(
                  onPressed: busy
                      ? null
                      : () => _act(
                          () => c.repository.setWorkflowEnabled(w.id, w.paused),
                          w.paused
                              ? 'Resumed "${w.name}".'
                              : 'Paused "${w.name}". Its schedules skip their times until you resume it.'),
                  icon:
                      Icon(w.paused ? Icons.play_circle_outline : Icons.pause),
                  label: Text(w.paused ? 'Resume' : 'Pause')),
              OutlinedButton.icon(
                  onPressed: busy ? null : () => _delete(w),
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Delete')),
            ],
          ]),
          if (w.paused)
            Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(
                    'Paused: Run now and its schedules are off until it is resumed.',
                    style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
          if (!editable)
            Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(
                    'Only ${w.createdBy ?? 'its creator'} or a workspace owner can change this.',
                    style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
        ],
        const SizedBox(height: AnumSpacing.md),
        Text('Steps',
            style: TextStyle(
                color: p.text,
                fontSize: AnumType.base,
                fontWeight: FontWeight.w700)),
        for (var i = 0; i < w.steps.length; i++)
          AnumRow(
            icon: Icons.looks_one_outlined,
            title: '${i + 1}. ${w.steps[i].name}',
            subtitle: actionLabel(w.steps[i].action),
          ),
      ]),
    );
  }

  Widget _schedulesCard(BuildContext context, AutomationWorkflow w) {
    final p = context.palette;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: 'Schedules',
          trailing: c.canManage
              ? TextButton.icon(
                  onPressed: () => _schedule(),
                  icon: const Icon(Icons.add_alarm),
                  label: const Text('Add'))
              : null,
        ),
        Text('Schedules run as the person who created them.',
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        if (scheduleError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: 'Could not load schedules',
              message: scheduleError!,
              action: 'Try again',
              onAction: _loadSchedules)
        else if (schedules.isEmpty)
          AnumEmpty(
              icon: Icons.schedule,
              title: 'No schedules',
              message: c.canManage
                  ? 'Add one to run this automation by itself, for example on weekdays at 09:00.'
                  : 'This automation runs only when someone starts it.')
        else
          for (final s in schedules) _scheduleRow(context, w, s),
      ]),
    );
  }

  Widget _scheduleRow(
      BuildContext context, AutomationWorkflow w, AutomationScheduleItem s) {
    final editable = c.canEdit(s.createdBy);
    final takeOver = canTakeOver(c.role, c.userId, s.createdBy) &&
        c.repository is AutomationTriggerRepository;
    final outcome = outcomeLabel(s.lastOutcome);
    final next = !s.enabled
        ? 'Off'
        : w.paused
            ? 'Skipped while paused'
            : s.nextRunAt == null
                ? 'Not scheduled'
                : 'Next ${exactTime(s.nextRunAt!)} (${relativeWhen(s.nextRunAt!)})';
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      AnumRow(
        icon: Icons.schedule,
        tone: s.enabled ? AnumTone.ok : AnumTone.neutral,
        title: s.name,
        subtitle:
            '${s.words(arabic: AnumLocalizations.maybeOf(context).isArabic)} (${s.timezone})',
        detail: [
          next,
          if (s.lastRunAt != null)
            'last ${relativeWhen(s.lastRunAt!)}${outcome == null ? '' : ': $outcome'}',
          'runs as ${s.createdBy ?? 'unknown'}',
        ].join(' · '),
        trailing: AnumPill(
            label: s.enabled ? 'On' : 'Off',
            tone: s.enabled ? AnumTone.ok : AnumTone.neutral),
      ),
      if (editable)
        Wrap(spacing: AnumSpacing.xs, children: [
          TextButton(
              onPressed: busy
                  ? null
                  : () => _act(
                      () => c.repository.setScheduleEnabled(s.id, !s.enabled),
                      s.enabled
                          ? 'Schedule turned off.'
                          : 'Schedule turned on.'),
              child: Text(s.enabled ? 'Turn off' : 'Turn on',
                  semanticsLabel:
                      '${s.enabled ? 'Turn off' : 'Turn on'} schedule ${s.name}')),
          TextButton(
              onPressed: busy ? null : () => _schedule(s),
              child: Text('Edit', semanticsLabel: 'Edit schedule ${s.name}')),
          TextButton(
              onPressed: busy
                  ? null
                  : () => _act(() => c.repository.deleteSchedule(s.id),
                      'Schedule "${s.name}" deleted.'),
              child:
                  Text('Delete', semanticsLabel: 'Delete schedule ${s.name}')),
        ]),
      if (takeOver)
        Wrap(spacing: AnumSpacing.xs, children: [
          TextButton(
              onPressed: busy ? null : () => _takeOver(s),
              child: Text('Take over',
                  semanticsLabel: 'Take over schedule ${s.name}')),
        ]),
    ]);
  }

  Future<void> _takeOver(AutomationScheduleItem s) async {
    final yes = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              content: Text(
                  'Take over "${s.name}" from ${s.createdBy}? From now on it runs as you. This is recorded in the audit log.'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('Keep it')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('Take over')),
              ],
            ));
    if (yes != true || !mounted) return;
    await _act(
        () => (c.repository as AutomationTriggerRepository)
            .takeOverSchedule(s.id),
        'Schedule "${s.name}" now runs as you.');
  }

  Widget _runsCard(BuildContext context, AutomationWorkflow w) {
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const AnumHeader(title: 'Run history'),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          for (final entry in const {
            '': 'All',
            'running': 'Running or waiting',
            'failed': 'Failed',
            'paused': 'Waiting',
            'completed': 'Completed',
            'cancelled': 'Cancelled'
          }.entries)
            ChoiceChip(
                label: Text(entry.value),
                selected: runFilter == entry.key,
                onSelected: (_) {
                  setState(() => runFilter = entry.key);
                  _loadRuns();
                }),
        ]),
        if (runError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: 'Could not load the run history',
              message: runError!,
              action: 'Try again',
              onAction: _loadRuns)
        else if (runs.isEmpty)
          AnumEmpty(
              icon: Icons.history,
              title: runFilter.isEmpty
                  ? 'No runs yet'
                  : 'No runs with this status',
              message: runFilter.isEmpty
                  ? (c.canManage
                      ? 'Use Run now, or add a schedule, to start the first run.'
                      : 'Runs appear when someone starts the automation or a schedule fires.')
                  : 'Choose All to see the full history.')
        else ...[
          for (final r in runs) _runTile(context, r),
          if (runsHasMore)
            OutlinedButton(
                onPressed: () => _loadRuns(more: true),
                child: const Text('Show older runs')),
        ],
      ]),
    );
  }

  Widget _runTile(BuildContext context, AutomationRunItem r) {
    final p = context.palette;
    final open = expanded.contains(r.id);
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      AnumRow(
        icon: open ? Icons.expand_less : Icons.expand_more,
        tone: automationTone(r.status),
        title: '${exactTime(r.createdAt)} · ${triggerLabel(r.trigger)}',
        subtitle: r.error ??
            '${r.completedSteps} of ${r.steps.length} steps${r.workflowVersion == null ? '' : ' · v${r.workflowVersion}'}',
        detail: r.createdBy,
        trailing: AnumPill(
            label: runStatusText(r),
            tone: r.waitingFor == null
                ? automationTone(r.status)
                : AnumTone.warn),
        onTap: () =>
            setState(() => open ? expanded.remove(r.id) : expanded.add(r.id)),
      ),
      if (c.canManage)
        Wrap(spacing: AnumSpacing.xs, children: [
          if (r.status == 'paused')
            TextButton(
                onPressed: busy
                    ? null
                    : () => _act(() => c.repository.transition(r.id, 'resume'),
                        'Run resumed.'),
                child: const Text('Resume')),
          if (r.status == 'failed' || r.status == 'cancelled')
            TextButton(
                onPressed: busy
                    ? null
                    : () => _act(() => c.repository.transition(r.id, 'retry'),
                        'Retry started.'),
                child: const Text('Retry')),
          if (r.active)
            TextButton(
                onPressed: busy
                    ? null
                    : () => _act(() => c.repository.transition(r.id, 'cancel'),
                        'Run cancelled.'),
                child: const Text('Cancel run')),
        ]),
      if (open)
        Padding(
          padding: const EdgeInsetsDirectional.only(start: 52, bottom: 8),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            for (final s in r.steps) ...[
              Text(
                  '${s.name} · ${actionLabel(s.action)} · ${statusLabel(s.status)}',
                  style: TextStyle(color: p.text, fontSize: AnumType.smPlus)),
              if (s.attempt > 0 && s.maxAttempts > 1)
                Text('Attempt ${s.attempt} of ${s.maxAttempts}',
                    style:
                        TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
              if (waitingLabel(s) != null)
                Text(waitingLabel(s)!,
                    style: TextStyle(
                        color: p.warn,
                        fontSize: AnumType.sm,
                        fontWeight: FontWeight.w600)),
              if (s.error != null)
                Text(s.error!,
                    style: TextStyle(color: p.stop, fontSize: AnumType.sm)),
              if (s.result != null)
                Text('Result: ${s.result}',
                    maxLines: 6,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: p.text, fontSize: AnumType.sm)),
              if (s.taskId != null)
                TextButton.icon(
                    onPressed: () => Navigator.of(context)
                        .pushNamed('/tasks/${Uri.encodeComponent(s.taskId!)}'),
                    icon: const Icon(Icons.open_in_new, size: 16),
                    label:
                        Text('Open task "${s.output['title'] ?? s.taskId}"')),
              const SizedBox(height: 6),
            ],
            SelectableText('Run id ${r.id}',
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          ]),
        ),
    ]);
  }
}

// --------------------------------------------------------------------- Editor

class AutomationEditorScreen extends StatefulWidget {
  const AutomationEditorScreen(
      {required this.controller, this.existing, this.template, super.key});
  final AutomationsController controller;
  final AutomationWorkflow? existing;

  /// Fills a new automation from a template (name, description, steps).
  final AutomationTemplate? template;

  @override
  State<AutomationEditorScreen> createState() => _AutomationEditorState();
}

class _AutomationEditorState extends State<AutomationEditorScreen> {
  late final AnumLocalizations _l = AnumLocalizations.maybeOf(context);
  late final name = TextEditingController(
      text: widget.existing?.name ?? widget.template?.name(_l) ?? '');
  late final description = TextEditingController(
      text: widget.existing?.description ??
          widget.template?.description(_l) ??
          '');
  late String overlap = widget.existing?.overlapPolicy ??
      (widget.template == null ? 'allow' : 'skip');
  late final List<StepDraft> steps = widget.existing != null
      ? widget.existing!.steps.map(StepDraft.fromStep).toList()
      : widget.template?.steps(_l) ?? [newStepDraft(const [])];
  Map<String, String> errors = const {};
  String? summary;
  bool busy = false;

  @override
  void dispose() {
    name.dispose();
    description.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final check = checkWorkflowDraft(
        name: name.text,
        description: description.text,
        overlapPolicy: overlap,
        steps: steps);
    if (!check.ok) {
      setState(() {
        errors = check.errors;
        summary = check.errors.length == 1
            ? check.errors.values.first
            : '${check.errors.length} fields need attention. ${check.errors.values.first}';
      });
      return;
    }
    setState(() {
      errors = const {};
      summary = null;
      busy = true;
    });
    try {
      final repo = widget.controller.repository;
      final saved = widget.existing == null
          ? await repo.createWorkflow(check.body!)
          : await repo.updateWorkflow(widget.existing!.id, check.body!);
      unawaited(widget.controller.refresh());
      if (mounted) Navigator.of(context).pop(saved);
    } catch (e) {
      if (mounted) setState(() => summary = automationErrorText(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final existing = widget.existing;
    return Scaffold(
      appBar: AppBar(
          title: Text(existing == null ? 'New automation' : 'Edit automation')),
      body: AnumPage(children: [
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            if (existing != null)
              Text(
                  'Saving creates version ${existing.version + 1}. Runs in progress keep the steps they started with.',
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            TextField(
                controller: name,
                maxLength: 160,
                decoration: InputDecoration(
                    labelText: 'Name', errorText: errors['name'])),
            TextField(
                controller: description,
                maxLength: 1000,
                decoration: InputDecoration(
                    labelText: 'Description (optional)',
                    errorText: errors['description'])),
            DropdownButtonFormField<String>(
              initialValue: overlap,
              isExpanded: true,
              decoration: InputDecoration(
                  labelText: 'When a run is already active',
                  helperText: overlapHelp[overlap],
                  helperMaxLines: 3),
              items: [
                for (final e in overlapPolicies.entries)
                  DropdownMenuItem(value: e.key, child: Text(e.value))
              ],
              onChanged: (v) => setState(() => overlap = v ?? overlap),
            ),
          ]),
        ),
        for (var i = 0; i < steps.length; i++) _stepCard(context, i),
        OutlinedButton.icon(
            onPressed: steps.length >= 100
                ? null
                : () =>
                    setState(() => steps.add(newStepDraft(steps, 'notify'))),
            icon: const Icon(Icons.add),
            label: const Text('Add step')),
        if (errors['steps'] != null)
          Text(errors['steps']!, style: TextStyle(color: p.stop)),
        if (summary != null)
          Semantics(
              liveRegion: true,
              child: Text(summary!, style: TextStyle(color: p.stop))),
        FilledButton(
            onPressed: busy ? null : _save,
            child: Text(busy
                ? 'Saving...'
                : existing == null
                    ? 'Create automation'
                    : 'Save changes')),
        const SizedBox(height: AnumSpacing.lg),
      ]),
    );
  }

  /// A labelled switch that works inside a surface without a Material.
  Widget _toggle(String label, bool value, ValueChanged<bool> onChanged) =>
      MergeSemantics(
        child: Row(children: [
          Switch(value: value, onChanged: onChanged),
          const SizedBox(width: AnumSpacing.xs),
          Expanded(
              child: GestureDetector(
                  onTap: () => onChanged(!value), child: Text(label))),
        ]),
      );

  Widget _stepCard(BuildContext context, int i) {
    final s = steps[i];
    final p = context.palette;
    return AnumSurface(
      key: ObjectKey(s),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Expanded(
              child: Text('Step ${i + 1}',
                  style: TextStyle(
                      color: p.text,
                      fontWeight: FontWeight.w700,
                      fontSize: AnumType.base))),
          IconButton(
              tooltip: 'Move step ${i + 1} up',
              onPressed: i == 0
                  ? null
                  : () =>
                      setState(() => steps.insert(i - 1, steps.removeAt(i))),
              icon: const Icon(Icons.arrow_upward)),
          IconButton(
              tooltip: 'Move step ${i + 1} down',
              onPressed: i == steps.length - 1
                  ? null
                  : () =>
                      setState(() => steps.insert(i + 1, steps.removeAt(i))),
              icon: const Icon(Icons.arrow_downward)),
          IconButton(
              tooltip: 'Remove step ${i + 1}',
              onPressed: steps.length == 1
                  ? null
                  : () => setState(() => steps.removeAt(i)),
              icon: const Icon(Icons.close)),
        ]),
        DropdownButtonFormField<String>(
          initialValue: s.action,
          isExpanded: true,
          decoration: InputDecoration(
              labelText: 'Does',
              errorText: errors['$i.action'],
              helperText: stepActionHelp[s.action],
              helperMaxLines: 3),
          items: [
            if (!stepActions.containsKey(s.action))
              DropdownMenuItem(
                  value: s.action, child: Text('${s.action} (unsupported)')),
            for (final e in stepActions.entries)
              DropdownMenuItem(value: e.key, child: Text(e.value)),
          ],
          onChanged: (v) => setState(() => s.action = v ?? s.action),
        ),
        TextFormField(
            initialValue: s.name,
            maxLength: 160,
            decoration: InputDecoration(
                labelText: 'Step name', errorText: errors['$i.name']),
            onChanged: (v) => s.name = v),
        TextFormField(
            initialValue: s.id,
            maxLength: 80,
            decoration: InputDecoration(
                labelText: 'Step id', errorText: errors['$i.id']),
            onChanged: (v) => s.id = v),
        if (s.action == 'task.create') ...[
          TextFormField(
              initialValue: s.title,
              maxLength: 160,
              decoration: InputDecoration(
                  labelText: 'Task title', errorText: errors['$i.title']),
              onChanged: (v) => s.title = v),
          TextFormField(
              initialValue: s.prompt,
              maxLength: 8000,
              minLines: 2,
              maxLines: 6,
              decoration: InputDecoration(
                  labelText: 'Prompt for the agent',
                  errorText: errors['$i.prompt']),
              onChanged: (v) => s.prompt = v),
        ],
        if (s.action == 'task.create')
          _toggle('Run the task (off: only create it under Tasks)', s.runTask,
              (v) => setState(() => s.runTask = v)),
        if (s.action == 'email.send') ...[
          TextFormField(
              initialValue: s.accountId,
              decoration: InputDecoration(
                  labelText: 'Send from (mailbox id)',
                  helperText: 'The connected mailbox, from Email.',
                  errorText: errors['$i.accountId']),
              onChanged: (v) => s.accountId = v.trim()),
          TextFormField(
              initialValue: s.to,
              textDirection: TextDirection.ltr,
              decoration: InputDecoration(
                  labelText: 'To',
                  helperText:
                      'Addresses separated by commas; {{trigger.from}} answers the sender.',
                  helperMaxLines: 2,
                  errorText: errors['$i.to']),
              onChanged: (v) => s.to = v),
          TextFormField(
              initialValue: s.subject,
              maxLength: 500,
              decoration: const InputDecoration(labelText: 'Subject'),
              onChanged: (v) => s.subject = v),
          TextFormField(
              initialValue: s.body,
              minLines: 2,
              maxLines: 8,
              maxLength: 20000,
              decoration: InputDecoration(
                  labelText: 'Body', errorText: errors['$i.body']),
              onChanged: (v) => s.body = v),
        ],
        if (s.action == 'notify' || s.action == 'fail')
          TextFormField(
              initialValue: s.message,
              maxLength: 500,
              decoration: InputDecoration(
                  labelText:
                      s.action == 'notify' ? 'Message (optional)' : 'Message',
                  errorText: errors['$i.message']),
              onChanged: (v) => s.message = v),
        if (s.action == 'notify')
          _toggle('Also send a push notification to my phone', s.push,
              (v) => setState(() => s.push = v)),
        if (s.action == 'task.create' ||
            s.action == 'email.send' ||
            s.action == 'notify')
          Wrap(spacing: AnumSpacing.sm, children: [
            SizedBox(
              width: 140,
              child: TextFormField(
                  initialValue: '${s.maxAttempts}',
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(labelText: 'Attempts'),
                  onChanged: (v) => s.maxAttempts = int.tryParse(v) ?? 1),
            ),
            SizedBox(
              width: 220,
              child: TextFormField(
                  initialValue: '${s.retryDelay}',
                  keyboardType: TextInputType.number,
                  decoration: InputDecoration(
                      labelText: 'Seconds before a retry',
                      errorText: errors['$i.retryDelay']),
                  onChanged: (v) => s.retryDelay = int.tryParse(v) ?? 0),
            ),
          ]),
      ]),
    );
  }
}

// ------------------------------------------------------------------ Schedule

class ScheduleSheet extends StatefulWidget {
  const ScheduleSheet(
      {required this.controller,
      required this.workflowId,
      this.existing,
      this.initialName,
      this.initialCron,
      super.key});
  final AutomationsController controller;
  final String workflowId;
  final AutomationScheduleItem? existing;

  /// A template's suggested schedule, for a new one.
  final String? initialName, initialCron;

  @override
  State<ScheduleSheet> createState() => _ScheduleSheetState();
}

class _ScheduleSheetState extends State<ScheduleSheet> {
  late final name = TextEditingController(
      text: widget.existing?.name ?? widget.initialName ?? '');
  late final cron = TextEditingController(
      text: widget.existing?.cron ?? widget.initialCron ?? '0 9 * * 1-5');
  late final timezone =
      TextEditingController(text: widget.existing?.timezone ?? 'UTC');
  SchedulePreview? preview;
  String? previewError, nameError, error;
  bool checking = false, busy = false;
  Timer? _debounce;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _check();
  }

  @override
  void dispose() {
    _debounce?.cancel();
    name.dispose();
    cron.dispose();
    timezone.dispose();
    super.dispose();
  }

  void _changed() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 350), _check);
  }

  Future<void> _check() async {
    final shape = cronShapeError(cron.text);
    final generation = ++_generation;
    if (shape != null) {
      setState(() {
        preview = null;
        previewError = shape;
      });
      return;
    }
    setState(() => checking = true);
    try {
      final value = await widget.controller.repository.preview(cron.text.trim(),
          timezone.text.trim().isEmpty ? 'UTC' : timezone.text.trim());
      if (!mounted || generation != _generation) return;
      setState(() {
        preview = value;
        previewError = null;
        checking = false;
      });
    } catch (e) {
      if (!mounted || generation != _generation) return;
      setState(() {
        preview = null;
        previewError = automationErrorText(e);
        checking = false;
      });
    }
  }

  Future<void> _save() async {
    if (name.text.trim().isEmpty) {
      setState(() => nameError =
          'Give the schedule a name, for example "Weekday mornings".');
      return;
    }
    if (previewError != null) {
      setState(() => error = previewError);
      return;
    }
    setState(() {
      busy = true;
      error = null;
      nameError = null;
    });
    final repo = widget.controller.repository;
    final zone = timezone.text.trim().isEmpty ? 'UTC' : timezone.text.trim();
    try {
      final saved = widget.existing == null
          ? await repo.createSchedule(
              workflowId: widget.workflowId,
              name: name.text.trim(),
              cron: cron.text.trim(),
              timezone: zone)
          : await repo.updateSchedule(widget.existing!.id,
              name: name.text.trim(), cron: cron.text.trim(), timezone: zone);
      if (mounted) Navigator.of(context).pop(saved);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        busy = false;
        error = automationErrorText(e);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: EdgeInsets.fromLTRB(
          20, 0, 20, MediaQuery.viewInsetsOf(context).bottom + 20),
      child: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(widget.existing == null ? 'Add schedule' : 'Edit schedule',
                  style: Theme.of(context).textTheme.titleLarge),
              TextField(
                  controller: name,
                  maxLength: 160,
                  decoration: InputDecoration(
                      labelText: 'Schedule name', errorText: nameError)),
              Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xs,
                  children: [
                    for (final e in schedulePresets.entries)
                      ChoiceChip(
                          label: Text(e.value),
                          selected: cron.text.trim() == e.key,
                          onSelected: (_) {
                            cron.text = e.key;
                            _check();
                          }),
                  ]),
              TextField(
                  controller: cron,
                  maxLength: 120,
                  autocorrect: false,
                  decoration: const InputDecoration(
                      labelText: 'Cron (minute hour day month weekday)'),
                  onChanged: (_) => _changed()),
              TextField(
                  controller: timezone,
                  maxLength: 80,
                  autocorrect: false,
                  decoration: const InputDecoration(
                      labelText: 'Time zone',
                      helperText:
                          'An IANA name such as UTC, Europe/London or Asia/Riyadh'),
                  onChanged: (_) => _changed()),
              const SizedBox(height: AnumSpacing.xs),
              Semantics(
                liveRegion: true,
                child: checking
                    ? Text('Checking the schedule...',
                        style: TextStyle(color: p.muted))
                    : previewError != null
                        ? Text(previewError!, style: TextStyle(color: p.stop))
                        : preview == null
                            ? const SizedBox.shrink()
                            : Text(
                                '${preview!.description} (${preview!.timezone})\nNext: '
                                '${preview!.nextRuns.map(exactTime).join(' · ')}',
                                style: TextStyle(color: p.text)),
              ),
              if (error != null)
                Padding(
                    padding: const EdgeInsets.only(top: AnumSpacing.xs),
                    child: Text(error!, style: TextStyle(color: p.stop))),
              const SizedBox(height: AnumSpacing.md),
              FilledButton(
                  onPressed: busy ? null : _save,
                  child: Text(busy
                      ? 'Saving...'
                      : widget.existing == null
                          ? 'Add schedule'
                          : 'Save schedule')),
            ]),
      ),
    );
  }
}
