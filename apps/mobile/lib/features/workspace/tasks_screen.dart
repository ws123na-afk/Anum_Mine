import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
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

  @override
  void dispose() {
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
          final tasks = [...widget.controller.tasks]
            ..sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
          final visible = tasks.where((t) => _matches(t, _filter)).toList();
          int count(_TaskFilter f) => tasks.where((t) => _matches(t, f)).length;

          return AnumPage(
            onRefresh: widget.controller.load,
            children: [
              AnumHeader(
                eyebrow: 'Work',
                title: 'Tasks',
                subtitle: '${tasks.length} in this workspace',
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
                      Row(children: [
                        Expanded(
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
                                  child:
                                      CircularProgressIndicator(strokeWidth: 2))
                              : const Icon(Icons.play_arrow_rounded),
                          label: const Text('Run task'),
                        ),
                      ]),
                    ]),
              ),
              TextField(
                controller: _search,
                onChanged: (_) => setState(() {}),
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
                      onSelected: (_) => setState(() => _filter = f),
                    ),
                    const SizedBox(width: 8),
                  ]
                ]),
              ),
              if (visible.isEmpty)
                AnumSurface(
                  child: AnumEmpty(
                    icon: Icons.task_alt,
                    title: tasks.isEmpty ? 'No tasks yet' : 'Nothing matches',
                    message: tasks.isEmpty
                        ? 'Write what you want done above and press Run task.'
                        : 'Try another filter or search term.',
                  ),
                )
              else
                for (final task in visible)
                  _TaskCard(task: task, controller: widget.controller),
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
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
            child: Text(task.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    color: p.text, fontSize: 16, fontWeight: FontWeight.w700)),
          ),
          const SizedBox(width: 10),
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
    return Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(icon, size: 14, color: p.faint),
      const SizedBox(width: 4),
      Text(text, style: TextStyle(color: p.faint, fontSize: 12)),
    ]);
  }
}

void openTaskDetail(BuildContext context, WorkspaceController controller,
        WorkspaceTask task) =>
    Navigator.push(
        context,
        MaterialPageRoute(
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

  @override
  void initState() {
    super.initState();
    // The list carries only task summaries; fetch the latest run for its steps and result.
    _refresh();
  }

  Future<void> _act(Future<WorkspaceTask?> Function() action) async {
    setState(() => _busy = true);
    final next = await action();
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (next != null) task = next;
    });
  }

  Future<void> _refresh() async {
    try {
      final next = await widget.controller.loadTask(task.id);
      if (mounted) setState(() => task = next);
    } on Object {
      // Keep showing the summary; pull to refresh can try again.
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final run = task.run;
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
              Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(
                  child: Text(task.title,
                      style: TextStyle(
                          color: p.text,
                          fontSize: 21,
                          fontWeight: FontWeight.w700)),
                ),
                const SizedBox(width: 10),
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
                if (run != null)
                  _Fact(label: 'Run', value: workStatusLabel(run.status)),
              ]),
              const SizedBox(height: 14),
              Wrap(spacing: 8, runSpacing: 8, children: [
                if (!terminal)
                  OutlinedButton.icon(
                    onPressed: _busy ? null : () => _confirmCancel(context),
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: const Text('Cancel task'),
                  ),
                if (task.status == WorkStatus.failed ||
                    task.status == WorkStatus.cancelled)
                  FilledButton.icon(
                    onPressed: _busy
                        ? null
                        : () =>
                            _act(() => widget.controller.resumeTask(task.id)),
                    icon: const Icon(Icons.replay),
                    label: const Text('Resume'),
                  ),
              ]),
            ]),
          ),
          if (run?.result case final result?)
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
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              AnumHeader(
                eyebrow: 'Execution trace',
                title: 'Every step the agent took',
                subtitle: run == null ? null : 'Run ${run.id}',
              ),
              const SizedBox(height: 8),
              if (run == null || run.steps.isEmpty)
                const AnumEmpty(
                  icon: Icons.timeline,
                  title: 'No steps yet',
                  message: 'Steps appear here as soon as the run starts.',
                )
              else
                for (var i = 0; i < run.steps.length; i++)
                  _Step(
                      step: run.steps[i],
                      index: i + 1,
                      last: i == run.steps.length - 1),
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
        content: Text('“${task.title}” will stop. You can resume it later.'),
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
              Row(children: [
                Expanded(
                  child: Text(step.type.replaceAll('_', ' '),
                      style: TextStyle(
                          color: p.text,
                          fontWeight: FontWeight.w700,
                          fontSize: 13.5)),
                ),
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
