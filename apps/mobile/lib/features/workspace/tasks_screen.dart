import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'approvals_screen.dart';
import 'list_kit.dart';
import 'sources_used.dart';
import 'task_management.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';

/// The tabs of the Tasks list (mockup 10): everything, what I created, what
/// agents are working on now, and what is done.
enum TaskTab { all, mine, agents, completed }

/// The server query of a tab (docs/tasks.md). "Agents" is the work agents
/// are doing now: queued, running or paused for an approval.
TaskListQuery taskTabQuery(TaskTab tab,
        {String search = '', bool archived = false}) =>
    TaskListQuery(
      statuses: switch (tab) {
        TaskTab.agents => const {
            WorkStatus.queued,
            WorkStatus.running,
            WorkStatus.waitingApproval
          },
        TaskTab.completed => const {WorkStatus.completed},
        _ => const {},
      },
      mine: tab == TaskTab.mine,
      search: search,
      archived: archived,
    );

/// Whether [task] belongs on [tab] (used when the repository has no server
/// filters, and for the snapshot shown before the first server page).
bool taskInTab(WorkspaceTask task, TaskTab tab, {String? userId}) =>
    switch (tab) {
      TaskTab.all => true,
      TaskTab.mine => userId != null && task.createdBy == userId,
      TaskTab.agents => const {
          WorkStatus.queued,
          WorkStatus.running,
          WorkStatus.waitingApproval
        }.contains(task.status),
      TaskTab.completed => task.status == WorkStatus.completed,
    };

String workStatusKey(WorkStatus status) => switch (status) {
      WorkStatus.created => 'statusCreated',
      WorkStatus.queued => 'statusQueued',
      WorkStatus.running => 'statusRunning',
      WorkStatus.waitingApproval => 'statusApproval',
      WorkStatus.completed => 'statusCompleted',
      WorkStatus.failed => 'statusFailed',
      WorkStatus.cancelled => 'statusCancelled',
    };

/// "Today", "Tomorrow", "Overdue" or a short date such as "8 Oct".
String dueLabel(BuildContext context, DateTime due, {DateTime? now}) {
  final l = AnumLocalizations.maybeOf(context);
  return switch (dueDay(due, now: now)) {
    DueDay.overdue => l.t('dueOverdue'),
    DueDay.today => l.t('dueToday'),
    DueDay.tomorrow => l.t('dueTomorrow'),
    DueDay.later =>
      MaterialLocalizations.of(context).formatShortMonthDay(due.toLocal()),
  };
}

class TasksScreen extends StatefulWidget {
  const TasksScreen({required this.controller, super.key});
  final WorkspaceController controller;

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  final _search = TextEditingController();
  TaskTab _tab = TaskTab.all;

  // Server mode (docs/tasks.md): the list is fetched with its filters and
  // paged by keyset; without task management the loaded snapshot is
  // filtered here instead.
  bool _archived = false;
  List<WorkspaceTask>? _rows;
  TaskSummaryCounts? _summary;
  bool _loading = false, _paging = false, _more = false;
  Object? _error;
  Timer? _debounce;
  int _fetchId = 0;
  List<WorkspaceTask>? _seenTasks;

  TaskManagementRepository? get _server => widget.controller.taskManagement;

  TaskListQuery get _query =>
      taskTabQuery(_tab, search: _search.text, archived: _archived);

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onController);
    if (_server != null) _fetch();
  }

  /// A reload of the workspace (after a run, an approval, pull to refresh)
  /// refreshes the server list too.
  void _onController() {
    final tasks = widget.controller.tasks;
    if (_server == null || identical(tasks, _seenTasks)) return;
    _seenTasks = tasks;
    _fetch();
  }

  Future<void> _fetch() async {
    final server = _server;
    if (server == null) return;
    final id = ++_fetchId;
    final query = _query;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait<Object>([
        server.listTasks(query),
        // Counts for the whole list (not the tab), for the subtitle.
        server.taskSummary(
            TaskListQuery(search: query.search, archived: query.archived)),
      ]);
      if (!mounted || id != _fetchId) return;
      setState(() {
        _rows = results[0] as List<WorkspaceTask>;
        _summary = results[1] as TaskSummaryCounts;
        _more = _rows!.length >= taskPageSize;
        _loading = false;
      });
    } on Object catch (error) {
      if (!mounted || id != _fetchId) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  Future<void> _loadMore() async {
    final server = _server, rows = _rows;
    if (server == null || rows == null || rows.isEmpty) return;
    setState(() => _paging = true);
    try {
      final page = await server.listTasks(_query, cursor: rows.last.id);
      if (!mounted) return;
      final seen = {for (final t in rows) t.id};
      setState(() {
        _rows = [...rows, ...page.where((t) => !seen.contains(t.id))];
        _more = page.length >= taskPageSize;
      });
    } on Object catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(AnumLocalizations.maybeOf(context)
                .f('tasksMoreFailed', {'reason': _describe(error)}))));
      }
    } finally {
      if (mounted) setState(() => _paging = false);
    }
  }

  void _searchChanged() {
    setState(() {});
    if (_server == null) return;
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), _fetch);
  }

  Future<void> _refresh() async {
    await widget.controller.load();
    await _fetch();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onController);
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  bool _matches(WorkspaceTask task) {
    final query = _search.text.trim().toLowerCase();
    return taskInTab(task, _tab, userId: widget.controller.currentUserId) &&
        (query.isEmpty ||
            task.title.toLowerCase().contains(query) ||
            task.prompt.toLowerCase().contains(query));
  }

  Future<void> _create() async {
    final task = await showTaskCreateSheet(context, widget.controller);
    if (!mounted || task == null) return;
    openTaskDetail(context, widget.controller, task);
  }

  void _clearFilters() {
    _search.clear();
    setState(() {
      _tab = TaskTab.all;
      _archived = false;
    });
    _fetch();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final l = AnumLocalizations.maybeOf(context);
          final p = context.palette;
          final server = _server != null;
          final tasks = [...widget.controller.tasks]
            ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
          final visible = server
              ? (_rows ?? (_query.isDefault ? tasks : const <WorkspaceTask>[]))
              : tasks.where(_matches).toList();
          final summary = _summary;
          final total =
              server && summary != null ? summary.total : tasks.length;
          final open = server && summary != null
              ? total - summary.count(const {WorkStatus.completed})
              : tasks.where((t) => t.status != WorkStatus.completed).length;
          final filtered = !_query.isDefault;

          return Stack(children: [
            AnumPage(
              onRefresh: _refresh,
              children: [
                AnumListHeader(
                  title: _archived ? l.t('tasksArchivedTitle') : l.t('tasks'),
                  subtitle: _archived
                      ? l.f('tasksArchivedCount',
                          {'n': summary?.archived ?? visible.length})
                      : l.f('tasksOpenCount', {'open': open, 'n': total}),
                  actionTooltip: l.t('tasksNew'),
                  onAction: widget.controller.mutating ? null : _create,
                  trailing: server
                      ? IconButton(
                          key: const Key('tasks-archived'),
                          tooltip: _archived
                              ? l.t('tasksShowActive')
                              : l.t('tasksShowArchived'),
                          isSelected: _archived,
                          onPressed: () {
                            setState(() => _archived = !_archived);
                            _fetch();
                          },
                          icon: const Icon(Icons.inventory_2_outlined),
                          selectedIcon: const Icon(Icons.inventory_2))
                      : null,
                ),
                AnumSegmentTabs<TaskTab>(
                  segments: [
                    AnumSegment(TaskTab.all, l.t('listTabAll'),
                        key: const Key('tasks-tab-all')),
                    AnumSegment(TaskTab.mine, l.t('tasksTabMine'),
                        key: const Key('tasks-tab-mine')),
                    AnumSegment(TaskTab.agents, l.t('tasksTabAgents'),
                        key: const Key('tasks-tab-agents')),
                    AnumSegment(TaskTab.completed, l.t('tasksTabCompleted'),
                        key: const Key('tasks-tab-completed')),
                  ],
                  selected: _tab,
                  onSelected: (tab) {
                    setState(() => _tab = tab);
                    _fetch();
                  },
                ),
                TextField(
                  key: const Key('tasks-search'),
                  controller: _search,
                  onChanged: (_) => _searchChanged(),
                  textInputAction: TextInputAction.search,
                  decoration: InputDecoration(
                      prefixIcon: const Icon(Icons.search),
                      hintText: l.t('tasksSearch')),
                ),
                if (widget.controller.budgetMessage != null)
                  _BudgetCard(controller: widget.controller),
                if (_error != null)
                  AnumSurface(
                    key: const Key('tasks-error'),
                    accent: p.stop,
                    child: AnumEmpty(
                      icon: Icons.cloud_off_outlined,
                      title: l.t('tasksLoadFailed'),
                      message: _describe(_error!),
                      action: l.t('retry'),
                      onAction: _fetch,
                    ),
                  )
                else if (_loading && visible.isEmpty)
                  AnumListLoading(label: l.t('tasksLoading'))
                else if (visible.isEmpty)
                  AnumSurface(
                    child: AnumEmpty(
                      icon: _archived
                          ? Icons.inventory_2_outlined
                          : Icons.task_alt,
                      title: _archived
                          ? l.t('tasksNoArchived')
                          : !filtered && tasks.isEmpty
                              ? l.t('tasksEmptyTitle')
                              : _emptyTitle(l),
                      message: _archived
                          ? l.t('tasksNoArchivedHint')
                          : !filtered && tasks.isEmpty
                              ? l.t('tasksEmptyHint')
                              : _emptyHint(l),
                      action:
                          filtered ? l.t('tasksClearFilters') : l.t('tasksNew'),
                      onAction: filtered ? _clearFilters : _create,
                    ),
                  )
                else ...[
                  AnumListCard(children: [
                    for (final task in visible)
                      TaskListRow(
                          task: task,
                          onTap: () =>
                              openTaskDetail(context, widget.controller, task)),
                  ]),
                  if (server && _more)
                    AnumLoadMore(
                        key: const Key('tasks-load-more'),
                        loading: _paging,
                        onPressed: _loadMore),
                ],
                // Room for the create button over the last row.
                const SizedBox(height: AnumSpacing.huge),
              ],
            ),
            PositionedDirectional(
              end: AnumSpacing.md,
              bottom: AnumSpacing.md,
              child: AnumGradientFab(
                  key: const Key('tasks-fab'),
                  tooltip: l.t('tasksNew'),
                  onPressed: _create),
            ),
          ]);
        },
      );

  String _emptyTitle(AnumLocalizations l) => _search.text.trim().isNotEmpty
      ? l.t('tasksNoMatch')
      : switch (_tab) {
          TaskTab.mine => l.t('tasksMineEmpty'),
          TaskTab.agents => l.t('tasksAgentsEmpty'),
          TaskTab.completed => l.t('tasksCompletedEmpty'),
          TaskTab.all => l.t('tasksNoMatch'),
        };

  String _emptyHint(AnumLocalizations l) => _search.text.trim().isNotEmpty
      ? l.t('tasksNoMatchHint')
      : switch (_tab) {
          TaskTab.mine => l.t('tasksMineEmptyHint'),
          TaskTab.agents => l.t('tasksAgentsEmptyHint'),
          TaskTab.completed => l.t('tasksCompletedEmptyHint'),
          TaskTab.all => l.t('tasksNoMatchHint'),
        };
}

/// One task: status icon, title, priority and status, due label.
class TaskListRow extends StatelessWidget {
  const TaskListRow({required this.task, required this.onTap, super.key});
  final WorkspaceTask task;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final status = l.t(workStatusKey(task.status));
    final due = task.dueAt;
    final done = task.status == WorkStatus.completed;
    final overdue = due != null && !done && dueDay(due) == DueDay.overdue;
    // A finished task shows its date, never "Overdue".
    final dueText = due == null
        ? null
        : done && dueDay(due) == DueDay.overdue
            ? MaterialLocalizations.of(context)
                .formatShortMonthDay(due.toLocal())
            : dueLabel(context, due);
    final tone = workStatusTone(task.status);
    return AnumListRow(
      key: Key('task-row-${task.id}'),
      leading: AnumTileIcon(
          icon: workStatusIcon(task.status),
          color: anumToneColor(context, tone)),
      title: task.title,
      detail: Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            PriorityPill(task.priority),
            Text(status,
                style: Theme.of(context)
                    .textTheme
                    .bodySmall
                    ?.copyWith(color: anumToneColor(context, tone))),
          ]),
      trailing: dueText == null
          ? null
          : Text(dueText,
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                  color: overdue ? p.stop : p.faint,
                  fontWeight: overdue ? FontWeight.w700 : FontWeight.w500)),
      semanticLabel: [
        task.title,
        l.t(priorityKey(task.priority)),
        status,
        if (dueText != null) l.f('dueSpoken', {'when': dueText}),
      ].join(', '),
      onTap: onTap,
    );
  }
}

class _BudgetCard extends StatelessWidget {
  const _BudgetCard({required this.controller});
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('budget-exceeded'),
      accent: p.stop,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const AnumIconChip(icon: Icons.speed_outlined, tone: AnumTone.stop),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l.t('budgetReached'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            const SizedBox(height: AnumSpacing.xxs),
            Text(controller.budgetMessage ?? '',
                style: TextStyle(color: p.muted, fontSize: 13)),
            const SizedBox(height: AnumSpacing.xxs),
            Text(l.t('budgetOwners'),
                style: TextStyle(color: p.faint, fontSize: 12)),
          ]),
        ),
        IconButton(
            tooltip: l.t('listDismiss'),
            onPressed: controller.dismissBudget,
            icon: const Icon(Icons.close)),
      ]),
    );
  }
}

/// The create sheet: what to do, title, priority, due date and whether to
/// start now. Returns the created task, or null when closed.
Future<WorkspaceTask?> showTaskCreateSheet(
        BuildContext context, WorkspaceController controller) =>
    showModalBottomSheet<WorkspaceTask>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (_) => TaskCreateSheet(controller: controller));

class TaskCreateSheet extends StatefulWidget {
  const TaskCreateSheet({required this.controller, super.key});
  final WorkspaceController controller;

  @override
  State<TaskCreateSheet> createState() => _TaskCreateSheetState();
}

class _TaskCreateSheetState extends State<TaskCreateSheet> {
  final _title = TextEditingController();
  final _prompt = TextEditingController();
  TaskPriority _priority = TaskPriority.medium;
  DateTime? _due;
  bool _runNow = true, _busy = false;
  String? _error;

  @override
  void dispose() {
    _title.dispose();
    _prompt.dispose();
    super.dispose();
  }

  Future<void> _pickDue() async {
    final now = DateTime.now();
    final date = await showDatePicker(
        context: context,
        initialDate: _due ?? now,
        firstDate: DateTime(now.year, now.month, now.day),
        lastDate: DateTime(now.year + 5));
    if (date == null || !mounted) return;
    final time = await showTimePicker(
        context: context,
        initialTime: _due == null
            ? const TimeOfDay(hour: 17, minute: 0)
            : TimeOfDay.fromDateTime(_due!));
    setState(() => _due = DateTime(
        date.year, date.month, date.day, time?.hour ?? 17, time?.minute ?? 0));
  }

  Future<void> _submit() async {
    final l = AnumLocalizations.maybeOf(context);
    if (_prompt.text.trim().isEmpty) {
      setState(() => _error = l.t('taskPromptRequired'));
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final c = widget.controller;
    final task = await c.createPlannedTask(TaskDraft(
        title: _title.text,
        prompt: _prompt.text,
        priority: _priority,
        dueAt: _due,
        runNow: _runNow));
    if (!mounted) return;
    if (task != null) {
      Navigator.pop(context, task);
      return;
    }
    setState(() {
      _busy = false;
      _error = c.taskRefusal ??
          c.budgetMessage ??
          c.message ??
          l.t('taskCreateFailed');
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.lg, 0, AnumSpacing.lg, AnumSpacing.lg),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 640),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Semantics(
                      header: true,
                      child: Text(l.t('tasksNew'),
                          style: text.titleLarge?.copyWith(
                              color: p.text, fontWeight: FontWeight.w800)),
                    ),
                    const SizedBox(height: AnumSpacing.md),
                    TextField(
                      key: const Key('task-create-prompt'),
                      controller: _prompt,
                      autofocus: true,
                      minLines: 2,
                      maxLines: 6,
                      maxLength: 8000,
                      decoration: InputDecoration(
                          labelText: l.t('taskPromptLabel'),
                          hintText: l.t('taskPromptHint')),
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    TextField(
                      key: const Key('task-create-title'),
                      controller: _title,
                      maxLength: 160,
                      decoration: InputDecoration(
                          labelText: l.t('taskTitleLabel'),
                          helperText: l.t('taskTitleHelp')),
                    ),
                    const SizedBox(height: AnumSpacing.sm),
                    Text(l.t('taskPriority'),
                        style: text.labelLarge?.copyWith(color: p.muted)),
                    const SizedBox(height: AnumSpacing.xs),
                    PrioritySelector(
                        value: _priority,
                        onChanged: (v) => setState(() => _priority = v)),
                    const SizedBox(height: AnumSpacing.md),
                    Wrap(
                        spacing: AnumSpacing.xs,
                        runSpacing: AnumSpacing.xs,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          OutlinedButton.icon(
                            key: const Key('task-create-due'),
                            onPressed: _busy ? null : _pickDue,
                            icon: const Icon(Icons.event_outlined),
                            label: Text(_due == null
                                ? l.t('taskDueAdd')
                                : l.f('taskDueOn', {
                                    'when': MaterialLocalizations.of(context)
                                        .formatMediumDate(_due!),
                                    'time': MaterialLocalizations.of(context)
                                        .formatTimeOfDay(
                                            TimeOfDay.fromDateTime(_due!))
                                  })),
                          ),
                          if (_due != null)
                            TextButton(
                                onPressed: () => setState(() => _due = null),
                                child: Text(l.t('taskDueClear'))),
                        ]),
                    const SizedBox(height: AnumSpacing.xs),
                    SwitchListTile(
                      key: const Key('task-create-run'),
                      contentPadding: EdgeInsets.zero,
                      value: _runNow,
                      onChanged: (v) => setState(() => _runNow = v),
                      title: Text(l.t('taskRunNow')),
                      subtitle: Text(
                          _runNow ? l.t('taskRunNowOn') : l.t('taskRunNowOff')),
                    ),
                    if (_error != null) ...[
                      const SizedBox(height: AnumSpacing.xs),
                      Semantics(
                        liveRegion: true,
                        child: Text(_error!,
                            key: const Key('task-create-error'),
                            style: text.bodyMedium?.copyWith(color: p.stop)),
                      ),
                    ],
                    const SizedBox(height: AnumSpacing.md),
                    FilledButton.icon(
                      key: const Key('task-create-submit'),
                      onPressed: _busy ? null : _submit,
                      icon: _busy
                          ? const SizedBox.square(
                              dimension: AnumSpacing.md,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : Icon(_runNow
                              ? Icons.play_arrow_rounded
                              : Icons.add_task),
                      label: Text(_runNow
                          ? l.t('taskCreateRun')
                          : l.t('taskCreateSave')),
                    ),
                  ]),
            ),
          ),
        ),
      ),
    );
  }
}

/// Low / Medium / High as a segmented choice.
class PrioritySelector extends StatelessWidget {
  const PrioritySelector(
      {required this.value, required this.onChanged, super.key});
  final TaskPriority value;
  final ValueChanged<TaskPriority> onChanged;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    // Chips wrap at large text sizes where a segmented button would clip.
    return Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
      for (final priority in TaskPriority.values)
        ChoiceChip(
          key: Key('priority-${taskPriorityWire(priority)}'),
          avatar: Icon(Icons.flag_outlined,
              color: anumToneColor(context, priorityTone(priority))),
          label: Text(l.t(priorityKey(priority))),
          selected: value == priority,
          onSelected: (_) => onChanged(priority),
        ),
    ]);
  }
}

void openTaskDetail(BuildContext context, WorkspaceController controller,
        WorkspaceTask task) =>
    Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) =>
                TaskDetailScreen(controller: controller, task: task)));

class TaskDetailScreen extends StatefulWidget {
  const TaskDetailScreen(
      {required this.controller, required this.task, super.key});
  final WorkspaceController controller;
  final WorkspaceTask task;

  @override
  State<TaskDetailScreen> createState() => _TaskDetailState();
}

class _TaskDetailState extends State<TaskDetailScreen> {
  late WorkspaceTask task = widget.task;
  bool _busy = false;

  /// The latest run is being fetched; [_runError] says why it failed.
  bool _runLoading = true;
  Object? _runError;

  /// Every run of the task, newest first (a retried task has several), and
  /// the one whose steps are shown (null: the latest).
  List<WorkspaceRun> _runs = const [];
  String? _chosenRun;

  @override
  void initState() {
    super.initState();
    // The list carries only task summaries; fetch the latest run for its steps and result.
    _refresh();
  }

  Future<void> _act(Future<WorkspaceTask?> Function() action,
      {String? done}) async {
    setState(() => _busy = true);
    final next = await action();
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (next != null) {
        task = next;
        _chosenRun = null;
      }
    });
    if (next != null) {
      if (done != null) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(done)));
      }
      unawaited(_refresh());
    }
  }

  Future<void> _rename(BuildContext context) async {
    final title = await showDialog<String>(
        context: context, builder: (_) => _RenameDialog(initial: task.title));
    final value = title?.trim() ?? '';
    if (value.isEmpty || value == task.title) return;
    await _act(() => widget.controller.renameTask(task.id, value),
        done: 'Task renamed');
  }

  Future<void> _copyRunLog() async {
    final server = widget.controller.taskManagement;
    if (server == null) return;
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final log = await server.exportRunLog(task.id);
      await Clipboard.setData(ClipboardData(text: log));
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(const SnackBar(content: Text('Run log copied as JSON')));
    } on Object catch (error) {
      messenger.showSnackBar(SnackBar(
          content: Text('Could not export the run log: ${_describe(error)}')));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _refresh() async {
    // initState starts with _runLoading set, where setState is not allowed.
    if (!_runLoading || _runError != null) {
      setState(() {
        _runLoading = true;
        _runError = null;
      });
    }
    try {
      final next = await widget.controller.loadTask(task.id);
      final server = widget.controller.taskManagement;
      List<WorkspaceRun> runs = const [];
      if (server != null) {
        try {
          runs = await server.listRuns(task.id);
        } on Object {
          // The latest run still shows; the history simply stays short.
        }
      }
      if (mounted) {
        setState(() {
          task = next;
          _runs = runs;
          _runLoading = false;
        });
      }
    } on Object catch (error) {
      // Keep showing the summary; the sources panel says what failed and
      // offers a retry, as does pull to refresh.
      if (mounted) {
        setState(() {
          _runLoading = false;
          _runError = error;
        });
      }
    }
  }

  // Rebuilt on controller changes, so an approval or a source label that
  // arrives with a reload shows without leaving the screen.
  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: widget.controller, builder: (context, _) => _build(context));

  Widget _build(BuildContext context) {
    final p = context.palette;
    final server = widget.controller.taskManagement;
    final actions = TaskActionSet.of(task);
    final latest = task.run;
    final runs = _runs.isNotEmpty
        ? (latest != null && !_runs.any((r) => r.id == latest.id)
            ? [latest, ..._runs]
            : _runs)
        : [if (latest != null) latest];
    final run = runs.firstWhere((r) => r.id == _chosenRun,
        orElse: () => runs.isEmpty ? latest ?? _noRun : runs.first);
    final shownRun = identical(run, _noRun) ? null : run;
    final refusal = widget.controller.taskRefusal;
    final approval = task.status == WorkStatus.waitingApproval
        ? widget.controller.pendingApprovalFor(task)
        : null;
    final terminal = {
      WorkStatus.completed,
      WorkStatus.failed,
      WorkStatus.cancelled
    }.contains(task.status);
    return Scaffold(
      extendBodyBehindAppBar: true,
      appBar: AppBar(title: const Text('Task')),
      body: AnumPage(
        onRefresh: _refresh,
        children: [
          const SizedBox(height: kToolbarHeight - 8),
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              // The status pill moves below a long title at large text sizes.
              Wrap(
                  spacing: 10,
                  runSpacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(task.title,
                        style: TextStyle(
                            color: p.text,
                            fontSize: 21,
                            fontWeight: FontWeight.w700)),
                    WorkStatusPill(task.status),
                  ]),
              const SizedBox(height: 12),
              Text('What was asked',
                  style: TextStyle(color: p.faint, fontSize: 12)),
              const SizedBox(height: 4),
              SelectableText(task.prompt,
                  style: TextStyle(color: p.text, fontSize: 14.5)),
              const SizedBox(height: 14),
              TaskPlanning(
                  task: task,
                  editable: server != null && task.archivedAt == null,
                  busy: _busy,
                  onPriority: (value) => _act(
                      () =>
                          widget.controller.planTask(task.id, priority: value),
                      done: AnumLocalizations.maybeOf(context)
                          .t('taskPlanSaved')),
                  onDue: (value) => _act(
                      () => widget.controller.planTask(task.id,
                          dueAt: value, clearDue: value == null),
                      done: AnumLocalizations.maybeOf(context)
                          .t('taskPlanSaved'))),
              const SizedBox(height: 14),
              Wrap(spacing: 18, runSpacing: 8, children: [
                _Fact(label: 'Created', value: exactTime(task.createdAt)),
                _Fact(label: 'Updated', value: exactTime(task.updatedAt)),
                if (latest != null)
                  _Fact(label: 'Run', value: workStatusLabel(latest.status)),
                if (runs.length > 1)
                  _Fact(label: 'Runs', value: '${runs.length}'),
                if (task.archivedAt case final archived?)
                  _Fact(label: 'Archived', value: exactTime(archived)),
              ]),
              const SizedBox(height: 14),
              Wrap(spacing: 8, runSpacing: 8, children: [
                // Saved without running (the create sheet's "Start now" off).
                if (server != null &&
                    task.status == WorkStatus.created &&
                    task.archivedAt == null &&
                    latest == null)
                  FilledButton.icon(
                    key: const Key('task-run'),
                    onPressed: _busy
                        ? null
                        : () => _act(() => widget.controller.runTask(task.id),
                            done: AnumLocalizations.maybeOf(context)
                                .t('taskStarted')),
                    icon: const Icon(Icons.play_arrow_rounded),
                    label: Text(
                        AnumLocalizations.maybeOf(context).t('taskRunAction')),
                  ),
                if (!terminal && task.archivedAt == null)
                  OutlinedButton.icon(
                    onPressed: _busy ? null : () => _confirmCancel(context),
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: const Text('Cancel task'),
                  ),
                // Retry starts a new run; earlier runs stay in the history.
                if (server != null && actions.retry)
                  FilledButton.icon(
                    key: const Key('task-retry'),
                    onPressed: _busy
                        ? null
                        : () => _act(() => widget.controller.retryTask(task.id),
                            done: 'Retry started'),
                    icon: const Icon(Icons.replay),
                    label: const Text('Retry'),
                  )
                else if (server == null &&
                    (task.status == WorkStatus.failed ||
                        task.status == WorkStatus.cancelled))
                  FilledButton.icon(
                    onPressed: _busy
                        ? null
                        : () =>
                            _act(() => widget.controller.resumeTask(task.id)),
                    icon: const Icon(Icons.replay),
                    label: const Text('Resume'),
                  ),
                if (server != null && actions.rename)
                  OutlinedButton.icon(
                    key: const Key('task-rename'),
                    onPressed: _busy ? null : () => _rename(context),
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Rename'),
                  ),
                if (server != null && actions.archive)
                  OutlinedButton.icon(
                    key: const Key('task-archive'),
                    onPressed: _busy
                        ? null
                        : () => _act(
                            () => widget.controller.archiveTask(task.id),
                            done: 'Task archived; its history is kept'),
                    icon: const Icon(Icons.inventory_2_outlined),
                    label: const Text('Archive'),
                  ),
                if (server != null && actions.unarchive)
                  OutlinedButton.icon(
                    key: const Key('task-unarchive'),
                    onPressed: _busy
                        ? null
                        : () => _act(
                            () => widget.controller.unarchiveTask(task.id),
                            done: 'Task restored'),
                    icon: const Icon(Icons.unarchive_outlined),
                    label: const Text('Restore'),
                  ),
                if (server != null)
                  OutlinedButton.icon(
                    key: const Key('task-export'),
                    onPressed: _busy ? null : _copyRunLog,
                    icon: const Icon(Icons.download_outlined),
                    label: const Text('Copy run log'),
                  ),
              ]),
              if (refusal != null) ...[
                const SizedBox(height: 10),
                Semantics(
                  liveRegion: true,
                  child: Text(refusal,
                      key: const Key('task-refusal'),
                      style: TextStyle(color: p.stop, fontSize: 13)),
                ),
              ],
            ]),
          ),
          // The decision is made here too, with the same card as Approvals
          // (arguments, payload hash, confirm), so the task is no dead end.
          if (approval != null)
            PendingApprovalCard(
              key: const Key('task-approval'),
              approval: approval,
              controller: widget.controller,
              showTask: false,
              onDecided: () {
                if (mounted) unawaited(_refresh());
              },
            ),
          if (shownRun?.result case final result?)
            AnumSurface(
              accent: p.ok,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    AnumHeader(
                      eyebrow: 'Result',
                      title: 'What the agent produced',
                      trailing: IconButton(
                        tooltip: 'Copy result',
                        icon: const Icon(Icons.copy_rounded),
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: result));
                          ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Result copied')));
                        },
                      ),
                    ),
                    const SizedBox(height: 10),
                    SelectableText(result,
                        style: TextStyle(
                            color: p.text, fontSize: 14.5, height: 1.5)),
                  ]),
            ),
          // Only a run with a retrieval step has sources to show; while the
          // latest run loads, or if it failed to, the panel says so.
          if (SourcesUsed.shows(shownRun,
              loading: _runLoading, error: _runError))
            SourcesUsed(
                run: shownRun,
                controller: widget.controller,
                loading: _runLoading,
                error: _runError,
                onRetry: _refresh),
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              AnumHeader(
                eyebrow: 'Execution trace',
                title: 'Every step the agent took',
                subtitle: shownRun == null ? null : 'Run ${shownRun.id}',
              ),
              if (runs.length > 1) ...[
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (var i = 0; i < runs.length; i++)
                    ChoiceChip(
                      key: Key('task-run-${runs[i].id}'),
                      label: Text('${runLabel(i, runs.length)} · '
                          '${workStatusLabel(runs[i].status)}'),
                      selected: shownRun?.id == runs[i].id,
                      onSelected: (_) =>
                          setState(() => _chosenRun = runs[i].id),
                    ),
                ]),
              ],
              const SizedBox(height: 8),
              if (shownRun == null || shownRun.steps.isEmpty)
                const AnumEmpty(
                  icon: Icons.timeline,
                  title: 'No steps yet',
                  message: 'Steps appear here as soon as the run starts.',
                )
              else
                for (var i = 0; i < shownRun.steps.length; i++)
                  _Step(
                      step: shownRun.steps[i],
                      index: i + 1,
                      last: i == shownRun.steps.length - 1),
            ]),
          ),
          Text('Task ID: ${task.id}',
              style: TextStyle(color: p.faint, fontSize: 11.5)),
          const SizedBox(height: AnumSpacing.lg),
        ],
      ),
    );
  }

  Future<void> _confirmCancel(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Cancel this task?'),
        content: Text('“${task.title}” will stop. You can retry it later.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep running')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Cancel task')),
        ],
      ),
    );
    if (ok == true) await _act(() => widget.controller.cancelTask(task.id));
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.label, required this.value});
  final String label, value;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(label, style: TextStyle(color: p.faint, fontSize: 11.5)),
      const SizedBox(height: 2),
      Text(value,
          style: TextStyle(
              color: p.text, fontSize: 13, fontWeight: FontWeight.w600)),
    ]);
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.step, required this.index, required this.last});
  final RunStep step;
  final int index;
  final bool last;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return IntrinsicHeight(
      child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SizedBox(
          width: 34,
          child: Column(children: [
            Container(
              width: 26,
              height: 26,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(colors: [p.sky, p.violet]),
                boxShadow: [
                  BoxShadow(
                      color: p.violet.withValues(alpha: 0.4), blurRadius: 10)
                ],
              ),
              child: Text('$index',
                  style: const TextStyle(
                      color: Color(0xFF0B1020),
                      fontSize: 11,
                      fontWeight: FontWeight.w800)),
            ),
            if (!last)
              Expanded(
                child: Container(
                  width: 2,
                  margin: const EdgeInsets.symmetric(vertical: 4),
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [p.violet.withValues(alpha: 0.6), p.line],
                    ),
                  ),
                ),
              ),
          ]),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              // Wraps instead of overflowing at large text sizes.
              Wrap(
                  spacing: 10,
                  runSpacing: 2,
                  alignment: WrapAlignment.spaceBetween,
                  children: [
                    Text(step.type.replaceAll('_', ' '),
                        style: TextStyle(
                            color: p.text,
                            fontWeight: FontWeight.w700,
                            fontSize: 13.5)),
                    Text(exactTime(step.createdAt),
                        style: TextStyle(color: p.faint, fontSize: 11.5)),
                  ]),
              const SizedBox(height: 3),
              Text(step.summary,
                  style:
                      TextStyle(color: p.muted, fontSize: 13.5, height: 1.4)),
            ]),
          ),
        ),
      ]),
    );
  }
}

const _noRun = WorkspaceRun(id: '', status: WorkStatus.created, steps: []);

/// A sentence for a failed call: the API's own message when it sent one.
String _describe(Object error) =>
    error is ApiException ? error.message : error.toString();

/// Owns its text controller so it is disposed only after the dialog is gone.
class _RenameDialog extends StatefulWidget {
  const _RenameDialog({required this.initial});
  final String initial;
  @override
  State<_RenameDialog> createState() => _RenameDialogState();
}

class _RenameDialogState extends State<_RenameDialog> {
  late final _field = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('Rename task'),
        content: TextField(
          controller: _field,
          autofocus: true,
          maxLength: 160,
          decoration: const InputDecoration(labelText: 'Title'),
          onSubmitted: (value) => Navigator.pop(context, value),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(context, _field.text),
              child: const Text('Save')),
        ],
      );
}

/// Priority and due date of a task, with the controls that change them.
class TaskPlanning extends StatelessWidget {
  const TaskPlanning(
      {required this.task,
      required this.editable,
      required this.busy,
      required this.onPriority,
      required this.onDue,
      super.key});
  final WorkspaceTask task;
  final bool editable, busy;
  final ValueChanged<TaskPriority> onPriority;

  /// A new due date, or null to remove it.
  final ValueChanged<DateTime?> onDue;

  Future<void> _pickDue(BuildContext context) async {
    final now = DateTime.now();
    final current = task.dueAt;
    final date = await showDatePicker(
        context: context,
        initialDate: current != null && current.isAfter(now) ? current : now,
        firstDate: DateTime(now.year - 1),
        lastDate: DateTime(now.year + 5));
    if (date == null || !context.mounted) return;
    final time = await showTimePicker(
        context: context,
        initialTime: current == null
            ? const TimeOfDay(hour: 17, minute: 0)
            : TimeOfDay.fromDateTime(current));
    onDue(DateTime(
        date.year, date.month, date.day, time?.hour ?? 17, time?.minute ?? 0));
  }

  Future<void> _pickPriority(BuildContext context) async {
    final l = AnumLocalizations.maybeOf(context);
    final value = await showModalBottomSheet<TaskPriority>(
        context: context,
        showDragHandle: true,
        builder: (context) => SafeArea(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                for (final value in TaskPriority.values)
                  ListTile(
                    key: Key('task-priority-${taskPriorityWire(value)}'),
                    leading: Icon(Icons.flag_outlined,
                        color: anumToneColor(context, priorityTone(value))),
                    title: Text(l.t(priorityKey(value))),
                    trailing:
                        value == task.priority ? const Icon(Icons.check) : null,
                    selected: value == task.priority,
                    onTap: () => Navigator.pop(context, value),
                  ),
              ]),
            ));
    if (value != null && value != task.priority) onPriority(value);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final due = task.dueAt;
    final material = MaterialLocalizations.of(context);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            PriorityPill(task.priority),
            Text(
                due == null
                    ? l.t('taskNoDue')
                    : l.f('taskDueOn', {
                        'when': material.formatMediumDate(due),
                        'time': material
                            .formatTimeOfDay(TimeOfDay.fromDateTime(due)),
                      }),
                key: const Key('task-due-text'),
                style: TextStyle(
                    color: due != null &&
                            task.status != WorkStatus.completed &&
                            dueDay(due) == DueDay.overdue
                        ? p.stop
                        : p.muted,
                    fontSize: 13)),
          ]),
      if (editable) ...[
        const SizedBox(height: AnumSpacing.xs),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          OutlinedButton.icon(
            key: const Key('task-priority'),
            onPressed: busy ? null : () => _pickPriority(context),
            icon: const Icon(Icons.flag_outlined),
            label: Text(l.t('taskPriorityChange')),
          ),
          OutlinedButton.icon(
            key: const Key('task-due'),
            onPressed: busy ? null : () => _pickDue(context),
            icon: const Icon(Icons.event_outlined),
            label: Text(due == null ? l.t('taskDueAdd') : l.t('taskDueChange')),
          ),
          if (due != null)
            TextButton(
              key: const Key('task-due-clear'),
              onPressed: busy ? null : () => onDue(null),
              child: Text(l.t('taskDueClear')),
            ),
        ]),
      ],
    ]);
  }
}
