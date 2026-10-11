import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'approvals_screen.dart';
import 'sources_used.dart';
import 'task_management.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';

enum _TaskFilter { all, active, approval, completed, failed }

class TasksScreen extends StatefulWidget {
  const TasksScreen({required this.controller, super.key});
  final WorkspaceController controller;

  @override
  State<TasksScreen> createState() => _TasksScreenState();
}

class _TasksScreenState extends State<TasksScreen> {
  final _prompt = TextEditingController();
  final _search = TextEditingController();
  _TaskFilter _filter = _TaskFilter.all;

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

  static Set<WorkStatus> _statusesOf(_TaskFilter f) => switch (f) {
        _TaskFilter.all => const {},
        _TaskFilter.active => const {
            WorkStatus.created,
            WorkStatus.queued,
            WorkStatus.running
          },
        _TaskFilter.approval => const {WorkStatus.waitingApproval},
        _TaskFilter.completed => const {WorkStatus.completed},
        _TaskFilter.failed => const {WorkStatus.failed, WorkStatus.cancelled},
      };

  TaskListQuery get _query => TaskListQuery(
      statuses: _statusesOf(_filter),
      search: _search.text,
      archived: _archived);

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
        server.taskSummary(query),
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
            content: Text('Could not load more tasks: ${_describe(error)}')));
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
    _prompt.dispose();
    _search.dispose();
    super.dispose();
  }

  bool _matches(WorkspaceTask task, _TaskFilter filter) {
    final query = _search.text.trim().toLowerCase();
    final inFilter = switch (filter) {
      _TaskFilter.all => true,
      _TaskFilter.active => {
          WorkStatus.created,
          WorkStatus.queued,
          WorkStatus.running
        }.contains(task.status),
      _TaskFilter.approval => task.status == WorkStatus.waitingApproval,
      _TaskFilter.completed => task.status == WorkStatus.completed,
      _TaskFilter.failed =>
        task.status == WorkStatus.failed || task.status == WorkStatus.cancelled,
    };
    return inFilter &&
        (query.isEmpty ||
            task.title.toLowerCase().contains(query) ||
            task.prompt.toLowerCase().contains(query));
  }

  Future<void> _create() async {
    final text = _prompt.text.trim();
    if (text.isEmpty) return;
    final task = await widget.controller.createTask(text);
    if (!mounted) return;
    if (task != null) {
      _prompt.clear();
      openTaskDetail(context, widget.controller, task);
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final p = context.palette;
          final server = _server != null;
          final tasks = [...widget.controller.tasks]
            ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
          final visible = server
              ? (_rows ?? (_query.isDefault ? tasks : const <WorkspaceTask>[]))
              : tasks.where((t) => _matches(t, _filter)).toList();
          final summary = _summary;
          int count(_TaskFilter f) => server && summary != null
              ? (f == _TaskFilter.all
                  ? summary.total
                  : summary.count(_statusesOf(f)))
              : tasks.where((t) => _matches(t, f)).length;
          final total =
              server && summary != null ? summary.total : tasks.length;

          return AnumPage(
            onRefresh: _refresh,
            children: [
              AnumHeader(
                eyebrow: 'Work',
                title: _archived ? 'Archived tasks' : 'Tasks',
                subtitle: _archived
                    ? '${summary?.archived ?? visible.length} archived; '
                        'their history is kept'
                    : '$total in this workspace',
                large: true,
              ),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text('New task',
                          style: TextStyle(
                              color: p.text, fontWeight: FontWeight.w700)),
                      const SizedBox(height: 10),
                      TextField(
                        controller: _prompt,
                        minLines: 2,
                        maxLines: 6,
                        textInputAction: TextInputAction.newline,
                        decoration: const InputDecoration(
                            hintText:
                                'Describe the result you want, e.g. “Summarise this week’s incidents”'),
                        onChanged: (_) => setState(() {}),
                      ),
                      const SizedBox(height: 10),
                      // Wraps under the hint at large text sizes instead of overflowing.
                      Wrap(
                          spacing: 12,
                          runSpacing: 8,
                          alignment: WrapAlignment.spaceBetween,
                          crossAxisAlignment: WrapCrossAlignment.center,
                          children: [
                            ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 360),
                              child: Text(
                                'Risky actions pause for your approval before they run.',
                                style: TextStyle(color: p.faint, fontSize: 12),
                              ),
                            ),
                            FilledButton.icon(
                              onPressed: widget.controller.mutating ||
                                      _prompt.text.trim().isEmpty
                                  ? null
                                  : _create,
                              icon: widget.controller.mutating
                                  ? const SizedBox.square(
                                      dimension: 16,
                                      child: CircularProgressIndicator(
                                          strokeWidth: 2))
                                  : const Icon(Icons.play_arrow_rounded),
                              label: const Text('Run task'),
                            ),
                          ]),
                    ]),
              ),
              if (widget.controller.budgetMessage != null)
                AnumSurface(
                  key: const Key('budget-exceeded'),
                  accent: p.stop,
                  semanticLabel:
                      'Monthly model budget reached. ${widget.controller.budgetMessage}',
                  child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const AnumIconChip(
                            icon: Icons.speed_outlined, tone: AnumTone.stop),
                        const SizedBox(width: 12),
                        Expanded(
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text('Monthly model budget reached',
                                    style: TextStyle(
                                        color: p.text,
                                        fontWeight: FontWeight.w700)),
                                const SizedBox(height: 4),
                                Text(widget.controller.budgetMessage!,
                                    style: TextStyle(
                                        color: p.muted, fontSize: 13)),
                                const SizedBox(height: 4),
                                Text(
                                    'Owners can raise it in Settings › Model budgets.',
                                    style: TextStyle(
                                        color: p.faint, fontSize: 12)),
                              ]),
                        ),
                        IconButton(
                            tooltip: 'Dismiss',
                            onPressed: widget.controller.dismissBudget,
                            icon: const Icon(Icons.close)),
                      ]),
                ),
              TextField(
                controller: _search,
                onChanged: (_) => _searchChanged(),
                decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search), hintText: 'Search tasks'),
              ),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(children: [
                  for (final f in _TaskFilter.values) ...[
                    ChoiceChip(
                      label: Text('${_filterLabel(f)} · ${count(f)}'),
                      selected: _filter == f,
                      onSelected: (_) {
                        setState(() => _filter = f);
                        _fetch();
                      },
                    ),
                    const SizedBox(width: 8),
                  ],
                  if (server)
                    FilterChip(
                      key: const Key('tasks-archived'),
                      avatar: const Icon(Icons.inventory_2_outlined, size: 18),
                      label: Text('Archived'
                          '${summary == null ? '' : ' · ${summary.archived}'}'),
                      selected: _archived,
                      onSelected: (value) {
                        setState(() => _archived = value);
                        _fetch();
                      },
                    ),
                ]),
              ),
              if (_error != null)
                AnumSurface(
                  key: const Key('tasks-error'),
                  accent: p.stop,
                  child: AnumEmpty(
                    icon: Icons.cloud_off_outlined,
                    title: 'Could not load tasks',
                    message: _describe(_error!),
                    action: 'Try again',
                    onAction: _fetch,
                  ),
                )
              else if (_loading && visible.isEmpty)
                const Center(
                    child: Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: CircularProgressIndicator()))
              else if (visible.isEmpty)
                AnumSurface(
                  child: AnumEmpty(
                    icon:
                        _archived ? Icons.inventory_2_outlined : Icons.task_alt,
                    title: _archived
                        ? 'No archived tasks'
                        : tasks.isEmpty && _query.isDefault
                            ? 'No tasks yet'
                            : 'Nothing matches',
                    message: _archived
                        ? 'Archive a finished task from its screen to tidy the list.'
                        : tasks.isEmpty && _query.isDefault
                            ? 'Write what you want done above and press Run task.'
                            : 'Try another filter or search term.',
                    action: _query.isDefault ? null : 'Clear filters',
                    onAction: _query.isDefault
                        ? null
                        : () {
                            _search.clear();
                            setState(() {
                              _filter = _TaskFilter.all;
                              _archived = false;
                            });
                            _fetch();
                          },
                  ),
                )
              else ...[
                for (final task in visible)
                  _TaskCard(task: task, controller: widget.controller),
                if (server && _more)
                  Center(
                    child: OutlinedButton.icon(
                      key: const Key('tasks-load-more'),
                      onPressed: _paging ? null : _loadMore,
                      icon: const Icon(Icons.expand_more),
                      label: Text(_paging ? 'Loading...' : 'Load more tasks'),
                    ),
                  ),
              ],
            ],
          );
        },
      );

  static String _filterLabel(_TaskFilter f) => switch (f) {
        _TaskFilter.all => 'All',
        _TaskFilter.active => 'Active',
        _TaskFilter.approval => 'Needs approval',
        _TaskFilter.completed => 'Completed',
        _TaskFilter.failed => 'Failed or cancelled',
      };
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.task, required this.controller});
  final WorkspaceTask task;
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final steps = task.run?.steps.length ?? 0;
    return AnumSurface(
      onTap: () => openTaskDetail(context, controller, task),
      accent: task.status == WorkStatus.waitingApproval
          ? p.warn
          : task.status == WorkStatus.failed
              ? p.stop
              : null,
      semanticLabel: '${task.title}, ${workStatusLabel(task.status)}',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // The status pill moves below a long title at large text sizes.
        Wrap(
            spacing: 10,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(task.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      color: p.text,
                      fontSize: 16,
                      fontWeight: FontWeight.w700)),
              WorkStatusPill(task.status),
            ]),
        if (task.run?.result case final result?) ...[
          const SizedBox(height: 6),
          Text(result,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: p.muted, fontSize: 13.5)),
        ] else if (task.prompt != task.title) ...[
          const SizedBox(height: 6),
          Text(task.prompt,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: p.muted, fontSize: 13.5)),
        ],
        const SizedBox(height: 10),
        Wrap(spacing: 14, runSpacing: 4, children: [
          _Meta(
              icon: Icons.update,
              text: 'Updated ${relativeTime(task.updatedAt)}'),
          // The list has no run details; only show a step count when we have one.
          if (steps > 0)
            _Meta(
                icon: Icons.timeline,
                text: steps == 1 ? '1 step' : '$steps steps'),
        ]),
      ]),
    );
  }
}

class _Meta extends StatelessWidget {
  const _Meta({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    // Text.rich wraps at large text sizes where a Row would overflow.
    return Text.rich(
        TextSpan(children: [
          WidgetSpan(
              alignment: PlaceholderAlignment.middle,
              child: Icon(icon, size: 14, color: p.faint)),
          TextSpan(text: ' $text'),
        ]),
        style: TextStyle(color: p.faint, fontSize: 12));
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
          if (approval != null)
            AnumSurface(
              key: const Key('task-approval'),
              accent: p.warn,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    AnumHeader(
                      eyebrow: 'Needs approval',
                      title: 'Waiting for a decision',
                      subtitle: 'The agent will call ${approval.action} only '
                          'after approval. Decide in Approvals.',
                    ),
                    if (approval.progressLabel != null) ...[
                      const SizedBox(height: 10),
                      ApprovalProgress(approval: approval),
                    ],
                  ]),
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
