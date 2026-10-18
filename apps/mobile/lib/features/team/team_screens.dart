// Team work in the mobile app (docs/team-work.md, docs/customer-organisations.md):
// My tasks, the team's open work, a work item, Ask ANUM, notifications,
// workload, department assistants and the platform console. Every list,
// state and permission comes from the API.
import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/localization/anum_localizations.dart';
import '../admin/owned_controller.dart';
import '../calls/call_screens.dart';
import '../calls/call_strings.dart';
import '../mail_center/mail_health.dart';
import 'team_controllers.dart';
import 'team_models.dart';
import 'team_repository.dart';
import 'team_strings.dart';

String _date(BuildContext context, DateTime value) {
  final local = value.toLocal();
  final m = MaterialLocalizations.of(context);
  return '${m.formatMediumDate(local)} ${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}

Widget _state(
  BuildContext context,
  TeamPhase phase,
  String? error,
  VoidCallback retry,
) {
  final s = TeamText.of(context);
  return switch (phase) {
    TeamPhase.loading => AnumFeedback(
        kind: AnumFeedbackKind.loading,
        message: s.t('loading'),
      ),
    TeamPhase.denied => AnumFeedback(
        kind: AnumFeedbackKind.permission,
        message: s.f('denied', {'message': error ?? ''}),
      ),
    _ => AnumFeedback(
        kind: AnumFeedbackKind.error,
        message: error ?? '',
        onRetry: retry,
      ),
  };
}

/// Team work as its own page, opened from the menu drawer (the same entries
/// as the Settings card).
class TeamHubScreen extends StatelessWidget {
  const TeamHubScreen({required this.repository, super.key});
  final TeamRepository repository;

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('section'))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [TeamHubCard(repository: repository)],
        ),
      ),
    );
  }
}

/// The Settings card that opens every team screen; each pushed screen owns
/// its controller and disposes it when popped.
class TeamHubCard extends StatelessWidget {
  const TeamHubCard({required this.repository, super.key});
  final TeamRepository repository;

  void _push(BuildContext context, Widget screen) =>
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => screen));

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    ListTile tile(IconData icon, String title, String hint, Widget screen) =>
        ListTile(
          leading: Icon(icon),
          title: Text(s.t(title)),
          subtitle: Text(s.t(hint)),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => _push(context, screen),
        );
    final calls = AppCallsScope.maybeOf(context);
    final callText = CallText.of(context);
    return Card(
      child: Column(
        children: [
          if (calls != null)
            ListTile(
              leading: const Icon(Icons.call_outlined),
              title: Text(callText.t('calls')),
              subtitle: Text(callText.t('callsHint')),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => _push(context, CallsScreen(controller: calls)),
            ),
          tile(
            Icons.assignment_ind_outlined,
            'myWork',
            'myWorkHint',
            WorkListScreen(repository: repository, scope: 'mine'),
          ),
          tile(
            Icons.view_kanban_outlined,
            'teamWork',
            'teamWorkHint',
            WorkListScreen(repository: repository, scope: 'all'),
          ),
          tile(
            Icons.notifications_outlined,
            'notifications',
            'notificationsHint',
            NotificationsScreen(repository: repository),
          ),
          tile(
            Icons.speed_outlined,
            'workload',
            'workloadHint',
            WorkloadScreen(repository: repository),
          ),
          tile(
            Icons.smart_toy_outlined,
            'assistants',
            'assistantsHint',
            AssistantsScreen(repository: repository),
          ),
          tile(
            Icons.domain_outlined,
            'organisations',
            'organisationsHint',
            OrganisationsScreen(
              repository: repository,
              mailHealth: switch (repository) {
                ApiTeamRepository(:final api) => () =>
                    MailHealthScreen(repository: ApiMailHealthRepository(api)),
                _ => null,
              },
            ),
          ),
        ],
      ),
    );
  }
}

class WorkListScreen extends StatelessWidget {
  const WorkListScreen({
    required this.repository,
    this.scope = 'mine',
    super.key,
  });
  final TeamRepository repository;
  final String scope;

  @override
  Widget build(BuildContext context) => OwnedController<WorkListController>(
        create: () => WorkListController(repository, scope: scope)..load(),
        builder: (context, c) =>
            _WorkList(controller: c, repository: repository),
      );
}

class _WorkList extends StatelessWidget {
  const _WorkList({required this.controller, required this.repository});
  final WorkListController controller;
  final TeamRepository repository;

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        return Scaffold(
          appBar: AppBar(
            title: Text(s.t(c.scope == 'mine' ? 'myWork' : 'teamWork')),
            actions: [
              IconButton(
                tooltip: s.t('retry'),
                onPressed: c.load,
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          floatingActionButton: FloatingActionButton.extended(
            onPressed: () async {
              final created = await Navigator.push<bool>(
                context,
                MaterialPageRoute(
                  builder: (_) => AskAnumScreen(repository: repository),
                ),
              );
              if (created == true) c.load();
            },
            icon: const Icon(Icons.auto_awesome),
            label: Text(s.t('ask')),
          ),
          body: RefreshIndicator(
            onRefresh: c.load,
            child: ListView(
              padding: const EdgeInsets.all(AnumSpacing.md),
              children: [
                TextField(
                  decoration: InputDecoration(
                    labelText: s.t('search'),
                    prefixIcon: const Icon(Icons.search),
                  ),
                  textInputAction: TextInputAction.search,
                  onSubmitted: c.search,
                ),
                const SizedBox(height: AnumSpacing.md),
                if (c.phase != TeamPhase.ready)
                  _state(context, c.phase, c.error, c.load)
                else if (c.items.isEmpty)
                  AnumFeedback(
                    kind: AnumFeedbackKind.empty,
                    message: '${s.t('empty')} ${s.t('emptyHint')}',
                  )
                else ...[
                  for (final item in c.items)
                    _WorkTile(
                      item: item,
                      onTap: () async {
                        await Navigator.push<void>(
                          context,
                          MaterialPageRoute(
                            builder: (_) => WorkDetailScreen(
                              repository: repository,
                              itemId: item.id,
                            ),
                          ),
                        );
                        c.load();
                      },
                    ),
                  if (c.nextOffset != null)
                    OutlinedButton(
                      onPressed: c.loadingMore ? null : c.loadMore,
                      child: Text(s.t('loadMore')),
                    ),
                ],
                const SizedBox(height: 88),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _WorkTile extends StatelessWidget {
  const _WorkTile({required this.item, required this.onTap});
  final WorkItem item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    final p = context.palette;
    final due = item.dueAt;
    return Card(
      child: ListTile(
        onTap: onTap,
        title: Text(item.title),
        subtitle: Text(
          [
            s.t('priority.${item.priority}'),
            item.assigneeName ?? s.t('unassigned'),
            if (due != null)
              item.overdue
                  ? '${s.t('overdue')} · ${_date(context, due)}'
                  : s.f('due', {'when': _date(context, due)}),
            if (item.checklistTotal > 0)
              '${item.checklistDone}/${item.checklistTotal}',
          ].join(' · '),
        ),
        trailing: Text(
          s.t('status.${item.status}'),
          style: TextStyle(color: item.overdue ? p.stop : null),
        ),
      ),
    );
  }
}

class WorkDetailScreen extends StatelessWidget {
  const WorkDetailScreen({
    required this.repository,
    required this.itemId,
    super.key,
  });
  final TeamRepository repository;
  final String itemId;

  @override
  Widget build(BuildContext context) => OwnedController<WorkDetailController>(
        create: () => WorkDetailController(repository, itemId)..load(),
        builder: (context, c) => _WorkDetail(controller: c),
      );
}

class _WorkDetail extends StatefulWidget {
  const _WorkDetail({required this.controller});
  final WorkDetailController controller;
  @override
  State<_WorkDetail> createState() => _WorkDetailState();
}

class _WorkDetailState extends State<_WorkDetail> {
  final _comment = TextEditingController();

  @override
  void dispose() {
    _comment.dispose();
    super.dispose();
  }

  String _flash(TeamText s, String flash) {
    final at = flash.indexOf(':');
    final kind = flash.substring(0, at);
    final message = flash.substring(at + 1);
    return switch (kind) {
      'conflict' => s.f('conflict', {'message': message}),
      'denied' => s.f('denied', {'message': message}),
      _ => message,
    };
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final item = c.item;
        return Scaffold(
          appBar: AppBar(title: Text(item?.title ?? s.t('loading'))),
          body: item == null
              ? _state(context, c.phase, c.error, c.load)
              : ListView(
                  padding: const EdgeInsets.all(AnumSpacing.md),
                  children: [
                    Text(
                      item.title,
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: AnumSpacing.xs),
                    Text(
                      [
                        item.assigneeName ?? s.t('unassigned'),
                        s.t('priority.${item.priority}'),
                        if (item.dueAt != null)
                          s.f('due', {'when': _date(context, item.dueAt!)}),
                      ].join(' · '),
                    ),
                    if (item.description.isNotEmpty) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Text(item.description),
                    ],
                    if (!item.canEdit) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Text(s.t('readOnly')),
                    ],
                    const SizedBox(height: AnumSpacing.md),
                    DropdownButtonFormField<String>(
                      isExpanded: true,
                      initialValue: item.status,
                      decoration: InputDecoration(labelText: s.t('status')),
                      items: [
                        for (final status in workStatuses)
                          DropdownMenuItem(
                            value: status,
                            child: Text(s.t('status.$status')),
                          ),
                      ],
                      onChanged: item.canEdit && !c.busy
                          ? (value) {
                              if (value != null && value != item.status) {
                                c.setStatus(value);
                              }
                            }
                          : null,
                    ),
                    if (c.flash != null) ...[
                      const SizedBox(height: AnumSpacing.sm),
                      Semantics(
                        liveRegion: true,
                        child: Text(
                          _flash(s, c.flash!),
                          style: TextStyle(color: context.palette.stop),
                        ),
                      ),
                    ],
                    if (item.checklist.isNotEmpty) ...[
                      const SizedBox(height: AnumSpacing.lg),
                      Text(
                        s.t('checklist'),
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      for (final entry in item.checklist)
                        CheckboxListTile(
                          value: entry.done,
                          title: Text(entry.label),
                          onChanged: item.canEdit && !c.busy
                              ? (v) => c.tick(entry, v ?? false)
                              : null,
                        ),
                    ],
                    const SizedBox(height: AnumSpacing.lg),
                    Text(
                      s.t('comments'),
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    for (final comment in item.comments)
                      ListTile(
                        leading: Icon(
                          comment.authorType == 'user'
                              ? Icons.person_outline
                              : Icons.smart_toy_outlined,
                        ),
                        title: Text(comment.body),
                        subtitle: Text(
                          '${comment.authorName} · ${_date(context, comment.createdAt)}',
                        ),
                      ),
                    TextField(
                      controller: _comment,
                      minLines: 1,
                      maxLines: 4,
                      maxLength: 4000,
                      decoration: InputDecoration(labelText: s.t('addComment')),
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerEnd,
                      child: FilledButton.icon(
                        onPressed: c.busy
                            ? null
                            : () {
                                final body = _comment.text.trim();
                                if (body.isEmpty) return;
                                c.comment(body).then((_) {
                                  if (c.flash == null) _comment.clear();
                                });
                              },
                        icon: const Icon(Icons.send),
                        label: Text(s.t('post')),
                      ),
                    ),
                  ],
                ),
        );
      },
    );
  }
}

/// "ANUM, ask Ahmed to call the supplier by Thursday": preview, then create.
class AskAnumScreen extends StatefulWidget {
  const AskAnumScreen({required this.repository, super.key});
  final TeamRepository repository;
  @override
  State<AskAnumScreen> createState() => _AskAnumScreenState();
}

class _AskAnumScreenState extends State<AskAnumScreen> {
  final _text = TextEditingController();
  CommandPreview? _preview;
  AssigneeChoice? _choice;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _check() async {
    if (_text.text.trim().length < 3) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final preview = await widget.repository.parseCommand(_text.text.trim());
      setState(() {
        _preview = preview;
        _choice = preview.assignee ??
            (preview.candidates.isEmpty ? null : preview.candidates.first);
      });
    } catch (e) {
      setState(() => _error = describeTeamError(e));
    }
    setState(() => _busy = false);
  }

  Future<void> _assign() async {
    setState(() => _busy = true);
    try {
      final item = await widget.repository.runCommand(
        _text.text.trim(),
        _choice,
      );
      if (!mounted) return;
      final s = TeamText.of(context);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            s.f('assigned', {
              'title': item.title,
              'who': item.assigneeName ?? '',
            }),
          ),
        ),
      );
      Navigator.pop(context, true);
    } catch (e) {
      setState(() {
        _error = describeTeamError(e);
        _busy = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    final preview = _preview;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('ask'))),
      body: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          TextField(
            controller: _text,
            maxLength: 500,
            decoration: InputDecoration(
              labelText: s.t('askLabel'),
              hintText: s.t('askHint'),
            ),
            onChanged: (_) => setState(() => _preview = null),
            onSubmitted: (_) => _check(),
          ),
          FilledButton.icon(
            onPressed: _busy ? null : _check,
            icon: const Icon(Icons.search),
            label: Text(s.t('check')),
          ),
          if (preview != null) ...[
            const SizedBox(height: AnumSpacing.md),
            if (preview.title != null)
              ListTile(
                leading: const Icon(Icons.assignment_outlined),
                title: Text(preview.title!),
                subtitle: Text(
                  [
                    _choice?.name ?? '',
                    if (preview.dueAt != null) _date(context, preview.dueAt!),
                  ].where((v) => v.isNotEmpty).join(' · '),
                ),
              ),
            if (preview.problem != null)
              Semantics(liveRegion: true, child: Text(preview.problem!)),
            if (preview.title != null && preview.candidates.length > 1)
              DropdownButtonFormField<String>(
                isExpanded: true,
                initialValue: _choice?.id,
                decoration: InputDecoration(labelText: s.t('pick')),
                items: [
                  for (final c in preview.candidates)
                    DropdownMenuItem(value: c.id, child: Text(c.name)),
                ],
                onChanged: (id) => setState(
                  () => _choice = preview.candidates.firstWhere(
                    (c) => c.id == id,
                  ),
                ),
              ),
            const SizedBox(height: AnumSpacing.md),
            FilledButton.icon(
              onPressed: _busy || preview.title == null || _choice == null
                  ? null
                  : _assign,
              icon: const Icon(Icons.send),
              label: Text(s.t('assign')),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(_error!, style: TextStyle(color: context.palette.stop)),
          ],
        ],
      ),
    );
  }
}

class NotificationsScreen extends StatelessWidget {
  const NotificationsScreen({required this.repository, super.key});
  final TeamRepository repository;

  /// Where a notice that is not about a work item opens, by its kind. The
  /// app registers these (for example the post-close trading report opens
  /// Finance, Trading intelligence; docs/finance-trading-intel.md#daily-report).
  static final Map<String, Future<void> Function(BuildContext context)>
      openers = {};

  @override
  Widget build(BuildContext context) =>
      OwnedController<TeamLoader<NotificationPage>>(
        create: () => TeamLoader(() => repository.notifications())..load(),
        builder: (context, c) => ListenableBuilder(
          listenable: c,
          builder: (context, _) {
            final s = TeamText.of(context);
            final page = c.value;
            return Scaffold(
              appBar: AppBar(
                title: Text(s.t('notifications')),
                actions: [
                  if (page != null && page.unread > 0)
                    IconButton(
                      tooltip: s.t('readAll'),
                      onPressed: () async {
                        await repository.markAllRead();
                        c.load();
                      },
                      icon: const Icon(Icons.done_all),
                    ),
                ],
              ),
              body: page == null
                  ? _state(context, c.phase, c.error, c.load)
                  : page.items.isEmpty
                      ? AnumFeedback(
                          kind: AnumFeedbackKind.empty,
                          message: s.t('noNotifications'),
                        )
                      : ListView(
                          children: [
                            for (final n in page.items)
                              ListTile(
                                leading: Icon(
                                  n.read
                                      ? Icons.notifications_none
                                      : Icons.notifications_active,
                                ),
                                title: Text(
                                  n.title,
                                  style: TextStyle(
                                    fontWeight: n.read
                                        ? FontWeight.normal
                                        : FontWeight.w700,
                                  ),
                                ),
                                subtitle: Text(
                                  '${n.body}\n${_date(context, n.createdAt)}',
                                ),
                                isThreeLine: true,
                                onTap: () async {
                                  if (!n.read) await repository.markRead(n.id);
                                  final id = n.workItemId;
                                  final opener = openers[n.kind];
                                  if (id == null &&
                                      opener != null &&
                                      context.mounted) {
                                    await opener(context);
                                  } else if (id != null && context.mounted) {
                                    await Navigator.push<void>(
                                      context,
                                      MaterialPageRoute(
                                        builder: (_) => WorkDetailScreen(
                                          repository: repository,
                                          itemId: id,
                                        ),
                                      ),
                                    );
                                  }
                                  c.load();
                                },
                              ),
                          ],
                        ),
            );
          },
        ),
      );
}

class WorkloadScreen extends StatelessWidget {
  const WorkloadScreen({required this.repository, super.key});
  final TeamRepository repository;

  @override
  Widget build(BuildContext context) =>
      OwnedController<TeamLoader<List<WorkloadRow>>>(
        create: () => TeamLoader(repository.workload)..load(),
        builder: (context, c) => ListenableBuilder(
          listenable: c,
          builder: (context, _) {
            final s = TeamText.of(context);
            final rows = c.value;
            return Scaffold(
              appBar: AppBar(title: Text(s.t('workload'))),
              body: rows == null
                  ? _state(context, c.phase, c.error, c.load)
                  : ListView(
                      children: [
                        for (final r in rows)
                          ListTile(
                            leading: Icon(
                              r.assigneeType == 'user'
                                  ? Icons.person_outline
                                  : Icons.smart_toy_outlined,
                            ),
                            title: Text(r.name),
                            subtitle: Text(
                              [
                                '${s.t('open')}: ${r.open}',
                                '${s.t('overdue')}: ${r.overdue}',
                                '${s.t('dueWeek')}: ${r.dueThisWeek}',
                              ].join(' · '),
                            ),
                            trailing: Text(
                              r.loadPercent == null
                                  ? s.t('noCapacity')
                                  : s.f('capacity', {
                                      'percent': r.loadPercent!,
                                    }),
                            ),
                          ),
                      ],
                    ),
            );
          },
        ),
      );
}

class AssistantsScreen extends StatefulWidget {
  const AssistantsScreen({required this.repository, super.key});
  final TeamRepository repository;
  @override
  State<AssistantsScreen> createState() => _AssistantsScreenState();
}

class _AssistantsScreenState extends State<AssistantsScreen> {
  late final _loader = TeamLoader(widget.repository.assistants)..load();
  final _sender = TextEditingController();
  final _body = TextEditingController();
  String? _result;

  @override
  void dispose() {
    _loader.dispose();
    _sender.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _route() async {
    final s = TeamText.of(context);
    if (_sender.text.trim().isEmpty || _body.text.trim().isEmpty) return;
    try {
      final name = await widget.repository.route(
        channel: 'other',
        sender: _sender.text.trim(),
        body: _body.text.trim(),
      );
      setState(() {
        _result = name == null ? s.t('triage') : s.f('routed', {'name': name});
        _body.clear();
      });
    } catch (e) {
      setState(() => _result = describeTeamError(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    return ListenableBuilder(
      listenable: _loader,
      builder: (context, _) {
        final list = _loader.value;
        return Scaffold(
          appBar: AppBar(title: Text(s.t('assistants'))),
          body: ListView(
            padding: const EdgeInsets.all(AnumSpacing.md),
            children: [
              if (list == null)
                _state(context, _loader.phase, _loader.error, _loader.load)
              else if (list.isEmpty)
                AnumFeedback(
                  kind: AnumFeedbackKind.empty,
                  message: s.t('noAssistants'),
                )
              else
                for (final a in list)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.smart_toy_outlined),
                      title: Text(a.name),
                      subtitle: Text(
                        '${s.t('autonomy.${a.autonomy}')}\n${a.tools.join(', ')}',
                      ),
                      isThreeLine: true,
                      trailing: Text(a.enabled ? s.t('on') : s.t('off')),
                    ),
                  ),
              const SizedBox(height: AnumSpacing.lg),
              Text(
                s.t('route'),
                style: Theme.of(context).textTheme.titleMedium,
              ),
              TextField(
                controller: _sender,
                decoration: InputDecoration(labelText: s.t('from')),
              ),
              TextField(
                controller: _body,
                minLines: 2,
                maxLines: 5,
                decoration: InputDecoration(labelText: s.t('message')),
              ),
              const SizedBox(height: AnumSpacing.sm),
              FilledButton.icon(
                onPressed: _route,
                icon: const Icon(Icons.call_split),
                label: Text(s.t('route')),
              ),
              if (_result != null)
                Padding(
                  padding: const EdgeInsets.only(top: AnumSpacing.sm),
                  child: Semantics(liveRegion: true, child: Text(_result!)),
                ),
            ],
          ),
        );
      },
    );
  }
}

class OrganisationsScreen extends StatefulWidget {
  const OrganisationsScreen(
      {required this.repository, this.mailHealth, super.key});

  /// The operator's mail health (docs/email.md#operator-view), when wired.
  final Widget Function()? mailHealth;
  final TeamRepository repository;
  @override
  State<OrganisationsScreen> createState() => _OrganisationsScreenState();
}

class _OrganisationsScreenState extends State<OrganisationsScreen> {
  late final _loader = TeamLoader(widget.repository.organisations)..load();
  final _name = TextEditingController();
  final _id = TextEditingController();
  final _email = TextEditingController();
  final _form = GlobalKey<FormState>();
  CreatedOrganisation? _created;
  String? _error;

  @override
  void dispose() {
    _loader.dispose();
    _name.dispose();
    _id.dispose();
    _email.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    try {
      final created = await widget.repository.createOrganisation(
        tenantId: _id.text.trim(),
        name: _name.text.trim(),
        email: _email.text.trim(),
        plan: 'trial',
      );
      setState(() {
        _created = created;
        _error = null;
      });
      _loader.load();
    } catch (e) {
      setState(() => _error = describeTeamError(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = TeamText.of(context);
    return ListenableBuilder(
      listenable: _loader,
      builder: (context, _) {
        final orgs = _loader.value;
        final created = _created;
        return Scaffold(
          appBar: AppBar(
            title: Text(s.t('organisations')),
            actions: [
              if (widget.mailHealth != null &&
                  _loader.phase != TeamPhase.denied)
                IconButton(
                  tooltip: AnumLocalizations.maybeOf(context).t('mc.op.title'),
                  icon: const Icon(Icons.mark_email_read_outlined),
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                        builder: (_) => widget.mailHealth!()),
                  ),
                ),
            ],
          ),
          body: _loader.phase == TeamPhase.denied
              ? AnumFeedback(
                  kind: AnumFeedbackKind.permission,
                  message: s.f('operatorsOnly', {
                    'message': _loader.error ?? '',
                  }),
                )
              : ListView(
                  padding: const EdgeInsets.all(AnumSpacing.md),
                  children: [
                    if (orgs == null)
                      _state(
                        context,
                        _loader.phase,
                        _loader.error,
                        _loader.load,
                      )
                    else if (orgs.isEmpty)
                      AnumFeedback(
                        kind: AnumFeedbackKind.empty,
                        message: s.t('noOrganisations'),
                      )
                    else
                      for (final o in orgs)
                        Card(
                          child: ListTile(
                            leading: const Icon(Icons.domain_outlined),
                            title: Text(o.displayName),
                            subtitle: Text(
                              [
                                o.tenantId,
                                '${s.t('members')}: ${o.activeMembers}',
                                '${s.t('open')}: ${o.openWork}',
                                '${s.t('overdueCount')}: ${o.overdueWork}',
                              ].join(' · '),
                            ),
                            trailing: Text(s.t('health.${o.health}')),
                          ),
                        ),
                    const SizedBox(height: AnumSpacing.lg),
                    Form(
                      key: _form,
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            s.t('onboard'),
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          TextFormField(
                            controller: _name,
                            decoration: InputDecoration(
                              labelText: s.t('orgName'),
                            ),
                            validator: (v) => (v ?? '').trim().isEmpty
                                ? s.t('orgName')
                                : null,
                          ),
                          TextFormField(
                            controller: _id,
                            decoration: InputDecoration(
                              labelText: s.t('orgId'),
                            ),
                            validator: (v) =>
                                organisationIdPattern.hasMatch((v ?? '').trim())
                                    ? null
                                    : s.t('orgIdError'),
                          ),
                          TextFormField(
                            controller: _email,
                            keyboardType: TextInputType.emailAddress,
                            decoration: InputDecoration(
                              labelText: s.t('adminEmail'),
                            ),
                            validator: (v) =>
                                emailPattern.hasMatch((v ?? '').trim())
                                    ? null
                                    : s.t('emailError'),
                          ),
                          const SizedBox(height: AnumSpacing.sm),
                          FilledButton(
                            onPressed: _create,
                            child: Text(s.t('create')),
                          ),
                        ],
                      ),
                    ),
                    if (_error != null)
                      Text(
                        _error!,
                        style: TextStyle(color: context.palette.stop),
                      ),
                    if (created != null) ...[
                      const SizedBox(height: AnumSpacing.md),
                      Text(
                        s.f('inviteOnce', {
                          'when': _date(context, created.expiresAt),
                        }),
                      ),
                      SelectableText(created.token),
                    ],
                  ],
                ),
        );
      },
    );
  }
}
