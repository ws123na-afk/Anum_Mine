import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';
import 'support_controller.dart';
import 'support_models.dart';

/// Projects a ticket can be covered by: (id, name).
typedef ProjectLoader = Future<List<(String, String)>> Function();

/// Business support tickets on the phone (docs/business.md#support-tickets).
/// SLA targets count the owner's working hours only; there is no round the
/// clock cover. Replies stay drafts until an owner approves them in Approvals,
/// and the inbox keeps the customer's message text (the phone never shows it).

AnumTone severityTone(String severity) => switch (severity) {
      'critical' => AnumTone.stop,
      'high' => AnumTone.warn,
      'normal' => AnumTone.info,
      _ => AnumTone.neutral,
    };

AnumTone ticketStatusTone(String status) => switch (status) {
      'new' => AnumTone.accent,
      'waiting_on_customer' => AnumTone.warn,
      'resolved' || 'closed' => AnumTone.ok,
      _ => AnumTone.info,
    };

AnumTone slaTone(String state) => switch (state) {
      'breached' || 'missed' => AnumTone.stop,
      'due_soon' => AnumTone.warn,
      'on_track' || 'met' => AnumTone.ok,
      _ => AnumTone.neutral,
    };

/// One badge for the list: the most urgent of the two clocks.
String slaOverall(TicketSla sla) {
  if (sla.breached) return 'breached';
  const order = [
    'breached',
    'due_soon',
    'missed',
    'on_track',
    'paused',
    'met',
    'none',
  ];
  final states = [sla.response, sla.resolution];
  for (final state in order) {
    if (states.contains(state)) return state;
  }
  return 'none';
}

String _hour(int hour) => '${hour.toString().padLeft(2, '0')}:00';

/// "SLA targets count working hours only ..." from the owner's policy.
String supportHoursText(AnumLocalizations l, SupportPolicy policy) {
  final days = [
    for (final day in [6, 0, 1, 2, 3, 4, 5])
      if (policy.workDays.contains(day)) l.t('biz.support.day.$day'),
  ].join(', ');
  return l.f('biz.support.hours', {
    'days': days.isEmpty ? '-' : days,
    'start': _hour(policy.startHour),
    'end': _hour(policy.endHour),
    'timezone': policy.timezone,
  });
}

String _assigneeText(AnumLocalizations l, String? assignee, String? me) =>
    assignee == null
        ? l.t('biz.support.unassigned')
        : assignee == me
            ? l.t('biz.support.assignedYou')
            : l.f('biz.support.assignedTo', {'name': assignee});

String _requesterText(AnumLocalizations l, SupportTicket t) => t
        .requesterName.isNotEmpty
    ? t.requesterName
    : t.requesterEmail ?? t.requesterPhone ?? l.t('biz.support.noRequester');

/// Support as a full screen (the Business tab embeds [SupportTicketsView]).
class SupportScreen extends StatefulWidget {
  const SupportScreen({
    required this.controller,
    this.onOpenApprovals,
    this.loadProjects,
    super.key,
  });
  final SupportController controller;
  final VoidCallback? onOpenApprovals;
  final ProjectLoader? loadProjects;

  @override
  State<SupportScreen> createState() => _SupportScreenState();
}

class _SupportScreenState extends State<SupportScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) widget.controller.load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.support.title'))),
      body: AnumPage(
        onRefresh: widget.controller.load,
        children: [
          SupportTicketsView(
            controller: widget.controller,
            onOpenApprovals: widget.onOpenApprovals,
            loadProjects: widget.loadProjects,
          ),
        ],
      ),
    );
  }
}

/// The ticket list with its summary, search and filters, and every state.
class SupportTicketsView extends StatefulWidget {
  const SupportTicketsView({
    required this.controller,
    this.onOpenApprovals,
    this.loadProjects,
    super.key,
  });
  final SupportController controller;
  final VoidCallback? onOpenApprovals;
  final ProjectLoader? loadProjects;

  @override
  State<SupportTicketsView> createState() => _SupportTicketsViewState();
}

class _SupportTicketsViewState extends State<SupportTicketsView> {
  final search = TextEditingController();

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  SupportController get c => widget.controller;

  Future<void> openNew() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => NewTicketScreen(
          controller: c,
          loadProjects: widget.loadProjects,
          onOpenApprovals: widget.onOpenApprovals,
        ),
      ),
    );
  }

  void openTicket(SupportTicket ticket) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => TicketScreen(
            controller: c,
            ticketId: ticket.id,
            onOpenApprovals: widget.onOpenApprovals,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: _children(context),
        ),
      );

  List<Widget> _gap(List<Widget> items) => [
        for (var i = 0; i < items.length; i++) ...[
          items[i],
          if (i < items.length - 1) const SizedBox(height: AnumSpacing.sm),
        ],
      ];

  List<Widget> _children(BuildContext context) {
    final l = context.anum;
    switch (c.state) {
      case BusinessLoad.idle:
      case BusinessLoad.loading:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.loading,
            message: l.t('biz.support.loading'),
          ),
        ];
      case BusinessLoad.denied:
        return [
          AnumFeedback(
            key: const Key('support-denied'),
            kind: AnumFeedbackKind.permission,
            message: c.error ?? l.t('biz.denied'),
          ),
        ];
      case BusinessLoad.error:
        return [
          AnumFeedback(
            key: const Key('support-error'),
            kind: AnumFeedbackKind.error,
            message: l.f('biz.support.failed', {'message': c.error ?? ''}),
            onRetry: c.load,
          ),
        ];
      case BusinessLoad.ready:
        break;
    }
    final s = c.summary;
    final f = c.filter;
    return _gap([
      Text(
        supportHoursText(l, c.setup!.policy),
        style: TextStyle(color: context.palette.muted),
      ),
      if (s != null)
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            AnumPill(label: l.f('biz.support.sum.open', {'count': s.open})),
            AnumPill(
              label: l.f('biz.support.sum.new', {'count': s.newCount}),
              tone: AnumTone.accent,
            ),
            AnumPill(
              label: l.f('biz.support.sum.unassigned', {'count': s.unassigned}),
              tone: s.unassigned > 0 ? AnumTone.warn : AnumTone.neutral,
            ),
            AnumPill(
              label: l.f('biz.support.sum.escalated', {'count': s.escalated}),
              tone: s.escalated > 0 ? AnumTone.stop : AnumTone.neutral,
            ),
            AnumPill(
              label: l.f('biz.support.sum.breached', {'count': s.breached}),
              tone: s.breached > 0 ? AnumTone.stop : AnumTone.ok,
            ),
          ],
        ),
      if (c.canWrite)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('support-new'),
            onPressed: openNew,
            icon: const Icon(Icons.add),
            label: Text(l.t('biz.support.new')),
          ),
        ),
      TextField(
        key: const Key('support-search'),
        controller: search,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          labelText: l.t('biz.support.search'),
          prefixIcon: const Icon(Icons.search),
        ),
        onSubmitted: c.search,
      ),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          FilterChip(
            key: const Key('support-filter-open'),
            label: Text(l.t('biz.support.filter.open')),
            selected: f.openOnly && f.status == null,
            onSelected: (on) => c.applyFilter(
              f.copyWith(openOnly: on, status: () => null),
            ),
          ),
          FilterChip(
            key: const Key('support-filter-mine'),
            label: Text(l.t('biz.support.filter.mine')),
            selected: f.mine,
            onSelected: (on) =>
                c.applyFilter(f.copyWith(mine: on, unassigned: false)),
          ),
          FilterChip(
            key: const Key('support-filter-unassigned'),
            label: Text(l.t('biz.support.filter.unassigned')),
            selected: f.unassigned,
            onSelected: (on) =>
                c.applyFilter(f.copyWith(unassigned: on, mine: false)),
          ),
          FilterChip(
            key: const Key('support-filter-escalated'),
            label: Text(l.t('biz.support.filter.escalated')),
            selected: f.escalated,
            onSelected: (on) => c.applyFilter(f.copyWith(escalated: on)),
          ),
          for (final severity in ticketSeverities)
            FilterChip(
              key: Key('support-filter-$severity'),
              label: Text(l.t('biz.ticket.severity.$severity')),
              selected: f.severities.contains(severity),
              onSelected: (on) => c.applyFilter(f.copyWith(
                severities: on
                    ? {...f.severities, severity}
                    : ({...f.severities}..remove(severity)),
              )),
            ),
        ],
      ),
      LayoutBuilder(builder: (context, box) {
        final width =
            box.maxWidth >= 600 ? math.min(320.0, box.maxWidth) : box.maxWidth;
        return Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            SizedBox(
              width: width,
              child: DropdownButtonFormField<String?>(
                key: const Key('support-status'),
                isExpanded: true,
                initialValue: f.status,
                decoration:
                    InputDecoration(labelText: l.t('biz.support.status')),
                items: [
                  DropdownMenuItem(
                    value: null,
                    child: Text(l.t('biz.support.anyStatus')),
                  ),
                  for (final status in ticketStatuses)
                    DropdownMenuItem(
                      value: status,
                      child: Text(l.t('biz.ticket.status.$status')),
                    ),
                ],
                onChanged: (value) =>
                    c.applyFilter(f.copyWith(status: () => value)),
              ),
            ),
            SizedBox(
              width: width,
              child: DropdownButtonFormField<String>(
                key: const Key('support-sort'),
                isExpanded: true,
                initialValue: f.sort,
                decoration: InputDecoration(labelText: l.t('biz.support.sort')),
                items: [
                  for (final sort in const ['updated', 'due', 'created'])
                    DropdownMenuItem(
                      value: sort,
                      child: Text(l.t('biz.support.sort.$sort')),
                    ),
                ],
                onChanged: (value) {
                  if (value != null) c.applyFilter(f.copyWith(sort: value));
                },
              ),
            ),
          ],
        );
      }),
      ..._list(context),
    ]);
  }

  List<Widget> _list(BuildContext context) {
    final l = context.anum;
    final list = c.tickets;
    switch (list.state) {
      case BusinessLoad.idle:
      case BusinessLoad.loading:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.loading,
            message: l.t('biz.support.loading'),
          ),
        ];
      case BusinessLoad.denied:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.permission,
            message: list.error ?? l.t('biz.denied'),
          ),
        ];
      case BusinessLoad.error:
        return [
          AnumFeedback(
            key: const Key('support-list-error'),
            kind: AnumFeedbackKind.error,
            message: l.f('biz.support.failed', {'message': list.error ?? ''}),
            onRetry: c.first,
          ),
        ];
      case BusinessLoad.ready:
        break;
    }
    if (list.items.isEmpty) {
      if (c.filter.narrowed) {
        return [
          AnumEmpty(
            title: l.t('biz.support.noMatch'),
            message: l.t('biz.support.noMatchText'),
            icon: Icons.filter_alt_off_outlined,
            action: l.t('biz.support.clearFilters'),
            onAction: () {
              search.clear();
              c.applyFilter(const TicketFilter());
            },
          ),
        ];
      }
      return [
        AnumEmpty(
          key: const Key('support-empty'),
          title: l.t('biz.support.empty'),
          message: c.canWrite
              ? l.t('biz.support.emptyText')
              : l.t('biz.support.emptyReadOnly'),
          icon: Icons.support_agent_outlined,
          action: c.canWrite ? l.t('biz.support.new') : null,
          onAction: c.canWrite ? openNew : null,
        ),
      ];
    }
    return [
      for (final ticket in list.items)
        _TicketCard(
          ticket: ticket,
          me: c.myId,
          onTap: () => openTicket(ticket),
        ),
      if (list.hasMore)
        OutlinedButton(
          key: const Key('support-more'),
          onPressed: list.loadingMore ? null : c.more,
          child: Text(
            l.f('biz.more', {'shown': list.items.length, 'total': list.total}),
          ),
        ),
      if (list.error != null)
        Text(list.error!, style: TextStyle(color: context.palette.stop)),
    ];
  }
}

class _TicketCard extends StatelessWidget {
  const _TicketCard({required this.ticket, required this.me, this.onTap});
  final SupportTicket ticket;
  final String? me;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final t = ticket;
    final sla = slaOverall(t.sla);
    return AnumSurface(
      key: Key('support-ticket-${t.id}'),
      onTap: onTap,
      semanticLabel: '${t.reference} ${t.title}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              AnumPill(label: t.reference),
              AnumPill(
                label: l.t('biz.ticket.severity.${t.severity}'),
                tone: severityTone(t.severity),
              ),
              AnumPill(
                label: l.t('biz.ticket.status.${t.status}'),
                tone: ticketStatusTone(t.status),
              ),
              AnumPill(
                label: l.t('biz.sla.badge.$sla'),
                tone: slaTone(sla),
                icon: Icons.timer_outlined,
              ),
              if (t.escalated)
                AnumPill(
                  label: l.t('biz.support.escalated'),
                  tone: AnumTone.stop,
                  icon: Icons.flag_outlined,
                ),
            ],
          ),
          const SizedBox(height: AnumSpacing.xs),
          Text(
            t.title,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: context.palette.text,
              fontWeight: FontWeight.w600,
              fontSize: 15,
            ),
          ),
          const SizedBox(height: AnumSpacing.xxs),
          Text(
            [
              _requesterText(l, t),
              if (t.accountName != null) t.accountName!,
              l.t('biz.support.source.${t.source}'),
              _assigneeText(l, t.assigneeId, me),
            ].join(' · '),
            style: TextStyle(color: context.palette.muted, fontSize: 13),
          ),
        ],
      ),
    );
  }
}

/// Two blocks side by side on wide screens, stacked on phones.
class _TwoUp extends StatelessWidget {
  const _TwoUp({required this.first, required this.second});
  final Widget first, second;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, box) => box.maxWidth >= 760
            ? Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: first),
                  const SizedBox(width: AnumSpacing.md),
                  Expanded(child: second),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  first,
                  const SizedBox(height: AnumSpacing.md),
                  second,
                ],
              ),
      );
}

/// Opening a ticket by hand: who asked, what, how urgent, what covers it.
class NewTicketScreen extends StatefulWidget {
  const NewTicketScreen({
    required this.controller,
    this.loadProjects,
    this.onOpenApprovals,
    super.key,
  });
  final SupportController controller;
  final ProjectLoader? loadProjects;
  final VoidCallback? onOpenApprovals;

  @override
  State<NewTicketScreen> createState() => _NewTicketScreenState();
}

class _NewTicketScreenState extends State<NewTicketScreen> {
  final form = GlobalKey<FormState>();
  final title = TextEditingController();
  final description = TextEditingController();
  final name = TextEditingController();
  final email = TextEditingController();
  final phone = TextEditingController();
  String severity = 'normal';
  String entitlement = 'none';
  String? projectId;
  bool assignToMe = true;
  bool busy = false;
  String? error;
  List<(String, String)>? projects;
  String? projectsError;

  @override
  void initState() {
    super.initState();
    assignToMe = widget.controller.myId != null;
  }

  @override
  void dispose() {
    for (final field in [title, description, name, email, phone]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> pickEntitlement(String? value) async {
    if (value == null) return;
    setState(() {
      entitlement = value;
      if (value != 'project') projectId = null;
    });
    if (value == 'project' && projects == null && widget.loadProjects != null) {
      try {
        final found = await widget.loadProjects!();
        if (mounted) setState(() => projects = found);
      } catch (e) {
        if (mounted) {
          setState(() => projectsError = SupportController.messageOf(e));
        }
      }
    }
  }

  Future<void> submit() async {
    final l = context.anum;
    if (!(form.currentState?.validate() ?? false)) return;
    if (entitlement == 'project' && projectId == null) {
      setState(() => error = l.t('biz.support.form.projectNeeded'));
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    String? text(TextEditingController field) =>
        field.text.trim().isEmpty ? null : field.text.trim();
    final me = widget.controller.myId;
    try {
      final detail = await widget.controller.create({
        'title': title.text.trim(),
        'severity': severity,
        if (text(description) != null) 'description': text(description),
        if (text(name) != null) 'requester_name': text(name),
        if (text(email) != null) 'requester_email': text(email),
        if (text(phone) != null) 'requester_phone': text(phone),
        'entitlement': entitlement,
        if (projectId != null) 'project_id': projectId,
        if (assignToMe && me != null) 'assignee_id': me,
      });
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => TicketScreen(
            controller: widget.controller,
            ticketId: detail.ticket.id,
            initial: detail,
            onOpenApprovals: widget.onOpenApprovals,
          ),
        ),
      );
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final policy = widget.controller.setup?.policy;
    final canProject = widget.loadProjects != null;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.support.new'))),
      body: Form(
        key: form,
        child: AnumPage(
          children: [
            AnumHeader(
              eyebrow: l.t('biz.support.title'),
              title: l.t('biz.support.new'),
              subtitle: policy == null ? null : supportHoursText(l, policy),
            ),
            if (error != null)
              Text(
                error!,
                key: const Key('support-form-error'),
                style: TextStyle(color: context.palette.stop),
              ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextFormField(
                    key: const Key('support-form-title'),
                    controller: title,
                    maxLength: 200,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.title'),
                    ),
                    validator: (value) => (value ?? '').trim().length < 3
                        ? l.t('biz.support.form.titleNeeded')
                        : null,
                  ),
                  TextFormField(
                    key: const Key('support-form-description'),
                    controller: description,
                    minLines: 3,
                    maxLines: 8,
                    maxLength: 4000,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.description'),
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.sm),
                  DropdownButtonFormField<String>(
                    key: const Key('support-form-severity'),
                    isExpanded: true,
                    initialValue: severity,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.severity'),
                    ),
                    items: [
                      for (final item in ticketSeverities)
                        DropdownMenuItem(
                          value: item,
                          child: Text(_severityOption(l, item, policy)),
                        ),
                    ],
                    onChanged: (value) =>
                        setState(() => severity = value ?? severity),
                  ),
                ],
              ),
            ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: l.t('biz.support.form.requester')),
                  TextFormField(
                    key: const Key('support-form-name'),
                    controller: name,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.name'),
                    ),
                  ),
                  TextFormField(
                    key: const Key('support-form-email'),
                    controller: email,
                    keyboardType: TextInputType.emailAddress,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.email'),
                    ),
                  ),
                  TextFormField(
                    key: const Key('support-form-phone'),
                    controller: phone,
                    keyboardType: TextInputType.phone,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.phone'),
                      helperText: l.t('biz.support.form.phoneHint'),
                      helperMaxLines: 3,
                    ),
                  ),
                ],
              ),
            ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                    title: l.t('biz.support.form.entitlement'),
                    subtitle: l.t('biz.support.form.planOnWeb'),
                  ),
                  DropdownButtonFormField<String>(
                    key: const Key('support-form-entitlement'),
                    isExpanded: true,
                    initialValue: entitlement,
                    decoration: InputDecoration(
                      labelText: l.t('biz.support.form.coveredBy'),
                    ),
                    items: [
                      for (final item in [
                        'none',
                        if (canProject) 'project',
                      ])
                        DropdownMenuItem(
                          value: item,
                          child: Text(l.t('biz.entitlement.$item')),
                        ),
                    ],
                    onChanged: pickEntitlement,
                  ),
                  if (entitlement == 'project') ...[
                    const SizedBox(height: AnumSpacing.sm),
                    if (projectsError != null)
                      Text(
                        projectsError!,
                        style: TextStyle(color: context.palette.stop),
                      )
                    else if (projects == null)
                      const LinearProgressIndicator()
                    else if (projects!.isEmpty)
                      Text(l.t('biz.support.form.noProjects'))
                    else
                      DropdownButtonFormField<String>(
                        key: const Key('support-form-project'),
                        isExpanded: true,
                        initialValue: projectId,
                        decoration: InputDecoration(
                          labelText: l.t('biz.support.form.project'),
                        ),
                        items: [
                          for (final (id, label) in projects!)
                            DropdownMenuItem(value: id, child: Text(label)),
                        ],
                        onChanged: (value) => setState(() => projectId = value),
                      ),
                  ],
                  if (widget.controller.myId != null)
                    Material(
                        type: MaterialType.transparency,
                        child: SwitchListTile(
                          key: const Key('support-form-mine'),
                          contentPadding: EdgeInsets.zero,
                          value: assignToMe,
                          title: Text(l.t('biz.support.form.assignMe')),
                          onChanged: (value) =>
                              setState(() => assignToMe = value),
                        )),
                ],
              ),
            ),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: FilledButton(
                key: const Key('support-form-submit'),
                onPressed: busy ? null : submit,
                child: Text(l.t('biz.support.form.submit')),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _severityOption(
    AnumLocalizations l,
    String item,
    SupportPolicy? policy,
  ) {
    final label = l.t('biz.ticket.severity.$item');
    final target = policy?.targets[item];
    if (target == null) return label;
    String hours(double value) => value == value.roundToDouble()
        ? value.toStringAsFixed(0)
        : value.toStringAsFixed(1);
    return l.f('biz.support.form.severityTarget', {
      'label': label,
      'response': hours(target.responseHours),
      'resolution': hours(target.resolutionHours),
    });
  }
}

/// One ticket: its fields and clocks, status and escalation, replies waiting
/// for approval, notes and the trail.
class TicketScreen extends StatefulWidget {
  const TicketScreen({
    required this.controller,
    required this.ticketId,
    this.initial,
    this.onOpenApprovals,
    super.key,
  });
  final SupportController controller;
  final String ticketId;
  final TicketDetail? initial;
  final VoidCallback? onOpenApprovals;

  @override
  State<TicketScreen> createState() => _TicketScreenState();
}

class _TicketScreenState extends State<TicketScreen> {
  TicketDetail? detail;
  String? loadError;
  bool denied = false;
  bool busy = false;
  String? actionError;
  String? notice;
  final reply = TextEditingController();
  final note = TextEditingController();
  bool responded = false;

  SupportController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    detail = widget.initial;
    if (detail == null) load();
  }

  @override
  void dispose() {
    reply.dispose();
    note.dispose();
    super.dispose();
  }

  Future<void> load() async {
    setState(() {
      loadError = null;
      denied = false;
    });
    try {
      final next = await c.repository.ticket(widget.ticketId);
      if (mounted) setState(() => detail = next);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        denied = SupportController.stateOf(e) == BusinessLoad.denied;
        loadError = SupportController.messageOf(e);
      });
    }
  }

  /// Runs one change; the fresh ticket replaces the old, the list refreshes.
  Future<bool> run(Future<TicketDetail> Function() action,
      {String? done}) async {
    setState(() {
      busy = true;
      actionError = null;
      notice = null;
    });
    try {
      final next = await action();
      if (!mounted) return true;
      setState(() {
        detail = next;
        notice = done;
      });
      unawaited(c.refresh());
      return true;
    } catch (e) {
      if (mounted) setState(() => actionError = SupportController.messageOf(e));
      return false;
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> move(String to) async {
    final l = context.anum;
    if (to == 'resolved') {
      final result = await showDialog<(String, String)>(
        context: context,
        builder: (_) => const _ResolveDialog(),
      );
      if (result == null) return;
      await run(
        () => c.repository.changeStatus(
          widget.ticketId,
          to,
          resolutionCode: result.$1,
          resolutionSummary: result.$2,
        ),
        done: l.f('biz.support.moved', {
          'status': l.t('biz.ticket.status.$to'),
        }),
      );
      return;
    }
    await run(
      () => c.repository.changeStatus(widget.ticketId, to),
      done: l.f('biz.support.moved', {'status': l.t('biz.ticket.status.$to')}),
    );
  }

  Future<void> escalate(bool on) async {
    final l = context.anum;
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => _TextDialog(
        title: l.t(on ? 'biz.support.escalate' : 'biz.support.deescalate'),
        label: l.t(on ? 'biz.support.escalateReason' : 'biz.support.optional'),
        minLength: on ? 3 : 0,
        tooShort: l.t('biz.support.escalateTooShort'),
        confirm: l.t(on ? 'biz.support.escalate' : 'biz.support.deescalate'),
      ),
    );
    if (reason == null) return;
    await run(() => c.repository.escalate(widget.ticketId, on, reason));
  }

  Future<void> saveNote() async {
    final text = note.text.trim();
    if (text.isEmpty) {
      setState(() => actionError = context.anum.t('biz.support.noteEmpty'));
      return;
    }
    final ok = await run(
      () => c.repository.addNote(widget.ticketId, text, responded),
      done: context.anum.t('biz.support.noteSaved'),
    );
    if (ok && mounted) {
      note.clear();
      setState(() => responded = false);
    }
  }

  Future<void> saveReply() async {
    final text = reply.text.trim();
    if (text.isEmpty) {
      setState(() => actionError = context.anum.t('biz.support.replyEmpty'));
      return;
    }
    final ok = await run(
      () => c.repository.addReply(widget.ticketId, text),
      done: context.anum.t('biz.support.replySaved'),
    );
    if (ok && mounted) reply.clear();
  }

  Future<void> editReply(TicketReply item) async {
    final l = context.anum;
    final text = await showDialog<String>(
      context: context,
      builder: (_) => _TextDialog(
        title: l.t('biz.support.reply.edit'),
        label: l.t('biz.support.reply.body'),
        initial: item.body,
        minLength: 1,
        multiline: true,
        tooShort: l.t('biz.support.replyEmpty'),
        confirm: l.t('biz.support.save'),
      ),
    );
    if (text == null) return;
    await run(() => c.repository.editReply(widget.ticketId, item.id, text));
  }

  Future<void> askApproval(TicketReply item) async {
    setState(() {
      busy = true;
      actionError = null;
      notice = null;
    });
    try {
      final (next, said) =
          await c.repository.requestApproval(widget.ticketId, item.id);
      if (!mounted) return;
      setState(() {
        detail = next;
        notice = said.isEmpty ? null : said;
      });
      unawaited(c.refresh());
    } catch (e) {
      if (mounted) setState(() => actionError = SupportController.messageOf(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> deleteTicket() async {
    final l = context.anum;
    final sure = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(l.t('biz.support.delete')),
        content: Text(l.t('biz.support.deleteText')),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(l.t('biz.cancel')),
          ),
          FilledButton(
            key: const Key('support-delete-confirm'),
            onPressed: () => Navigator.pop(context, true),
            child: Text(l.t('biz.support.delete')),
          ),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    setState(() => busy = true);
    try {
      await c.repository.delete(widget.ticketId);
      unawaited(c.refresh());
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          actionError = SupportController.messageOf(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = detail;
    return Scaffold(
      appBar:
          AppBar(title: Text(d?.ticket.reference ?? l.t('biz.support.title'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (d == null && loadError == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: l.t('biz.support.loading'),
            ),
          if (d == null && loadError != null)
            denied
                ? AnumFeedback(
                    kind: AnumFeedbackKind.permission,
                    message: loadError!,
                  )
                : AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: l.f('biz.support.failed', {'message': loadError!}),
                    onRetry: load,
                  ),
          if (d != null) ..._body(context, d),
        ],
      ),
    );
  }

  List<Widget> _body(BuildContext context, TicketDetail d) {
    final l = context.anum;
    final t = d.ticket;
    final write = c.canWrite;
    final p = context.palette;
    return [
      AnumHeader(
        eyebrow: '${t.reference} · ${l.t('biz.support.source.${t.source}')}',
        title: t.title,
        subtitle: _assigneeText(l, t.assigneeId, c.myId),
      ),
      Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          AnumPill(
            label: l.t('biz.ticket.severity.${t.severity}'),
            tone: severityTone(t.severity),
          ),
          AnumPill(
            key: const Key('support-detail-status'),
            label: l.t('biz.ticket.status.${t.status}'),
            tone: ticketStatusTone(t.status),
          ),
          if (t.escalated)
            AnumPill(
              label: l.t('biz.support.escalated'),
              tone: AnumTone.stop,
              icon: Icons.flag_outlined,
            ),
        ],
      ),
      if (actionError != null)
        Text(
          actionError!,
          key: const Key('support-action-error'),
          style: TextStyle(color: p.stop),
        ),
      if (notice != null)
        Text(
          notice!,
          key: const Key('support-notice'),
          style: TextStyle(color: p.ok),
        ),
      if (write && t.allowed.isNotEmpty)
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final to in t.allowed)
              OutlinedButton(
                key: Key('support-move-$to'),
                onPressed: busy ? null : () => move(to),
                child: Text(
                  t.status == 'closed' || t.status == 'resolved'
                      ? (to == 'in_progress'
                          ? l.t('biz.support.reopen')
                          : l.f('biz.support.moveTo', {
                              'status': l.t('biz.ticket.status.$to'),
                            }))
                      : l.f('biz.support.moveTo', {
                          'status': l.t('biz.ticket.status.$to'),
                        }),
                ),
              ),
            if (t.isOpen)
              OutlinedButton.icon(
                key: Key(
                    t.escalated ? 'support-deescalate' : 'support-escalate'),
                onPressed: busy ? null : () => escalate(!t.escalated),
                icon: Icon(t.escalated ? Icons.outlined_flag : Icons.flag),
                label: Text(l.t(
                  t.escalated
                      ? 'biz.support.deescalate'
                      : 'biz.support.escalate',
                )),
              ),
            if (t.isOpen && c.myId != null && t.assigneeId != c.myId)
              TextButton(
                key: const Key('support-assign-me'),
                onPressed: busy
                    ? null
                    : () => run(() => c.repository
                        .update(widget.ticketId, {'assignee_id': c.myId})),
                child: Text(l.t('biz.support.assignMe')),
              ),
            if (t.isOpen && t.assigneeId != null)
              TextButton(
                key: const Key('support-unassign'),
                onPressed: busy
                    ? null
                    : () => run(() => c.repository
                        .update(widget.ticketId, {'assignee_id': null})),
                child: Text(l.t('biz.support.unassign')),
              ),
          ],
        ),
      if (t.escalated && t.escalationReason.isNotEmpty)
        Text(
          l.f('biz.support.escalatedBecause', {'reason': t.escalationReason}),
          style: TextStyle(color: p.stop),
        ),
      if (t.status == 'resolved' || t.status == 'closed')
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(
                title: l.t('biz.support.resolution'),
                subtitle: t.resolutionCode == null
                    ? null
                    : l.t('biz.resolution.${t.resolutionCode}'),
              ),
              if (t.resolutionSummary.isNotEmpty) Text(t.resolutionSummary),
            ],
          ),
        ),
      _TwoUp(
        first: _sla(context, t),
        second: _fields(context, t, write),
      ),
      _replies(context, d, write),
      if (write) _noteBox(context),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.support.activity')),
            if (d.activities.isEmpty) Text(l.t('biz.support.noActivity')),
            for (final item in d.activities)
              AnumRow(
                title: item.summary,
                subtitle: '${item.actor} · ${exactTime(item.createdAt)}',
                icon: item.kind == 'response_recorded'
                    ? Icons.mark_chat_read_outlined
                    : Icons.history,
              ),
          ],
        ),
      ),
      if (c.canManage)
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton.icon(
            key: const Key('support-delete'),
            onPressed: busy ? null : deleteTicket,
            icon: Icon(Icons.delete_outline, color: p.stop),
            label: Text(
              l.t('biz.support.delete'),
              style: TextStyle(color: p.stop),
            ),
          ),
        ),
    ];
  }

  Widget _sla(BuildContext context, SupportTicket t) {
    final l = context.anum;
    String due(DateTime? at) => at == null ? '-' : exactTime(at);
    Widget clock(String label, DateTime? at, String state) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AnumRow(
              title: label,
              subtitle: l.f('biz.support.dueAt', {'time': due(at)}),
              icon: Icons.timer_outlined,
              tone: slaTone(state),
            ),
            AnumPill(label: l.t('biz.sla.$state'), tone: slaTone(state)),
          ],
        );
    final policy = c.setup?.policy;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: l.t('biz.support.sla')),
          clock(
              l.t('biz.support.sla.response'), t.responseDueAt, t.sla.response),
          clock(l.t('biz.support.sla.resolution'), t.resolutionDueAt,
              t.sla.resolution),
          Text(
            policy == null
                ? l.t('biz.support.hoursShort')
                : supportHoursText(l, policy),
            style: TextStyle(color: context.palette.muted, fontSize: 13),
          ),
        ],
      ),
    );
  }

  Widget _fields(BuildContext context, SupportTicket t, bool write) {
    final l = context.anum;
    String or(String? value) => value == null || value.isEmpty ? '-' : value;
    final entitlement = switch (t.entitlement) {
      'project' => '${l.t('biz.entitlement.project')}: ${or(t.projectName)}',
      'support_plan' =>
        '${l.t('biz.entitlement.support_plan')}: ${or(t.supportPlanName)}',
      _ => l.t('biz.entitlement.none'),
    };
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: l.t('biz.support.details')),
          AnumRow(
            title: l.t('biz.support.requester'),
            subtitle: [
              _requesterText(l, t),
              if (t.requesterEmail != null) t.requesterEmail!,
              if (t.requesterPhone != null) t.requesterPhone!,
            ].join(' · '),
            detail: t.requesterVerified
                ? l.t('biz.support.verified')
                : l.t('biz.support.unverified'),
            icon: Icons.person_outline,
          ),
          AnumRow(
            title: l.t('biz.support.account'),
            subtitle:
                '${or(t.accountName)} · ${l.t('biz.lead.contact')}: ${or(t.contactName)}',
            icon: Icons.business_outlined,
          ),
          AnumRow(
            title: l.t('biz.support.form.entitlement'),
            subtitle: entitlement,
            detail: t.entitlementNote.isEmpty ? null : t.entitlementNote,
            icon: Icons.verified_user_outlined,
          ),
          AnumRow(
            title: l.t('biz.support.reported'),
            subtitle: exactTime(t.reportedAt),
            detail: l.t('biz.support.source.${t.source}'),
            icon: Icons.schedule,
          ),
          if (write && t.isOpen) ...[
            const SizedBox(height: AnumSpacing.xs),
            DropdownButtonFormField<String>(
              key: const Key('support-severity'),
              isExpanded: true,
              initialValue: t.severity,
              decoration: InputDecoration(
                labelText: l.t('biz.support.form.severity'),
                helperText: l.t('biz.support.severityHint'),
                helperMaxLines: 3,
              ),
              items: [
                for (final item in ticketSeverities)
                  DropdownMenuItem(
                    value: item,
                    child: Text(l.t('biz.ticket.severity.$item')),
                  ),
              ],
              onChanged: busy
                  ? null
                  : (value) {
                      if (value == null || value == t.severity) return;
                      run(() => c.repository
                          .update(widget.ticketId, {'severity': value}));
                    },
            ),
          ],
          if (t.description.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(
              l.t('biz.support.form.description'),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: AnumSpacing.xxs),
            Text(t.description),
          ],
        ],
      ),
    );
  }

  Widget _replies(BuildContext context, TicketDetail d, bool write) {
    final l = context.anum;
    final p = context.palette;
    final shown = d.replies.where((item) => item.status != 'discarded');
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
            title: l.t('biz.support.replies'),
            subtitle: d.replyChannel == null
                ? l.t('biz.support.reply.manual')
                : l.t('biz.support.reply.how'),
          ),
          if (d.canReply && write) ...[
            TextField(
              key: const Key('support-reply'),
              controller: reply,
              minLines: 3,
              maxLines: 10,
              maxLength: 4000,
              decoration: InputDecoration(
                labelText: l.f('biz.support.reply.draft', {
                  'channel': l.t('biz.support.channel.${d.replyChannel}'),
                }),
                helperText: l.t('biz.support.reply.rules'),
                helperMaxLines: 3,
              ),
            ),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: FilledButton.tonal(
                key: const Key('support-reply-save'),
                onPressed: busy ? null : saveReply,
                child: Text(l.t('biz.support.reply.save')),
              ),
            ),
          ],
          if (shown.isEmpty && d.replyChannel != null)
            Text(l.t('biz.support.reply.none'),
                style: TextStyle(color: p.muted)),
          for (final item in shown)
            Padding(
              key: Key('support-reply-${item.id}'),
              padding: const EdgeInsets.only(top: AnumSpacing.sm),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  border: Border.all(color: p.edge),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(AnumSpacing.sm),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Wrap(
                        spacing: 6,
                        runSpacing: 6,
                        children: [
                          AnumPill(
                            label: l.t('biz.support.channel.${item.channel}'),
                          ),
                          AnumPill(
                            label: l.t('biz.reply.${item.status}'),
                            tone: switch (item.status) {
                              'sent' => AnumTone.ok,
                              'failed' => AnumTone.stop,
                              'pending_approval' => AnumTone.warn,
                              _ => AnumTone.neutral,
                            },
                          ),
                        ],
                      ),
                      const SizedBox(height: AnumSpacing.xs),
                      Text(item.body),
                      if (item.status == 'failed' && item.error.isNotEmpty)
                        Text(item.error, style: TextStyle(color: p.stop)),
                      if (item.status == 'pending_approval') ...[
                        const SizedBox(height: AnumSpacing.xs),
                        Text(
                          l.t('biz.support.reply.waiting'),
                          key: Key('support-reply-waiting-${item.id}'),
                          style: TextStyle(color: p.warn),
                        ),
                        if (item.approval?.payloadHash != null)
                          Text(
                            l.f('biz.support.reply.check', {
                              'code': item.approval!.payloadHash!.substring(
                                  0,
                                  math.min(
                                      12, item.approval!.payloadHash!.length)),
                            }),
                            style: TextStyle(color: p.muted, fontSize: 12),
                          ),
                        if (widget.onOpenApprovals != null)
                          Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: TextButton(
                              onPressed: widget.onOpenApprovals,
                              child: Text(l.t('biz.proposals.openApprovals')),
                            ),
                          ),
                      ],
                      if (item.status == 'sent' && item.sentAt != null)
                        Text(
                          l.f('biz.support.reply.sentAt', {
                            'time': exactTime(item.sentAt!),
                          }),
                          style: TextStyle(color: p.muted, fontSize: 12),
                        ),
                      if (item.editable && write && d.canReply)
                        Wrap(
                          spacing: 8,
                          runSpacing: 4,
                          children: [
                            TextButton(
                              key: Key('support-reply-edit-${item.id}'),
                              onPressed: busy ? null : () => editReply(item),
                              child: Text(l.t('biz.support.reply.edit')),
                            ),
                            TextButton(
                              key: Key('support-reply-discard-${item.id}'),
                              onPressed: busy
                                  ? null
                                  : () => run(() => c.repository
                                      .discardReply(widget.ticketId, item.id)),
                              child: Text(l.t('biz.support.reply.discard')),
                            ),
                            FilledButton(
                              key: Key('support-reply-ask-${item.id}'),
                              onPressed: busy ? null : () => askApproval(item),
                              child: Text(l.t('biz.support.reply.ask')),
                            ),
                          ],
                        ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _noteBox(BuildContext context) {
    final l = context.anum;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(title: l.t('biz.support.note')),
          TextField(
            key: const Key('support-note'),
            controller: note,
            maxLength: 500,
            minLines: 2,
            maxLines: 5,
            decoration: InputDecoration(labelText: l.t('biz.support.noteHint')),
          ),
          Material(
              type: MaterialType.transparency,
              child: SwitchListTile(
                key: const Key('support-note-responded'),
                contentPadding: EdgeInsets.zero,
                value: responded,
                title: Text(l.t('biz.support.responded')),
                subtitle: Text(l.t('biz.support.respondedHint')),
                onChanged: (value) => setState(() => responded = value),
              )),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: TextButton(
              key: const Key('support-note-save'),
              onPressed: busy ? null : saveNote,
              child: Text(l.t('biz.support.noteSave')),
            ),
          ),
        ],
      ),
    );
  }
}

/// Resolving needs a resolution code and a sentence on how (10 characters).
class _ResolveDialog extends StatefulWidget {
  const _ResolveDialog();

  @override
  State<_ResolveDialog> createState() => _ResolveDialogState();
}

class _ResolveDialogState extends State<_ResolveDialog> {
  final summary = TextEditingController();
  String? code;
  String? problem;

  @override
  void dispose() {
    summary.dispose();
    super.dispose();
  }

  void confirm() {
    final l = context.anum;
    final text = summary.text.trim();
    if (code == null) {
      setState(() => problem = l.t('biz.support.resolve.codeNeeded'));
      return;
    }
    if (text.length < 10) {
      setState(() => problem = l.t('biz.support.resolve.summaryNeeded'));
      return;
    }
    Navigator.pop(context, (code!, text));
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.support.resolve.title')),
      scrollable: true,
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          DropdownButtonFormField<String>(
            key: const Key('support-resolve-code'),
            isExpanded: true,
            initialValue: code,
            decoration: InputDecoration(
              labelText: l.t('biz.support.resolve.code'),
            ),
            items: [
              for (final item in resolutionCodes)
                DropdownMenuItem(
                  value: item,
                  child: Text(l.t('biz.resolution.$item')),
                ),
            ],
            onChanged: (value) => setState(() => code = value),
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('support-resolve-summary'),
            controller: summary,
            minLines: 2,
            maxLines: 6,
            maxLength: 2000,
            decoration: InputDecoration(
              labelText: l.t('biz.support.resolve.summary'),
            ),
          ),
          if (problem != null)
            Text(
              problem!,
              key: const Key('support-resolve-problem'),
              style: TextStyle(color: context.palette.stop),
            ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.cancel')),
        ),
        FilledButton(
          key: const Key('support-resolve-confirm'),
          onPressed: confirm,
          child: Text(l.t('biz.support.resolve.confirm')),
        ),
      ],
    );
  }
}

/// A short text asked in a dialog (escalation reason, reply edit).
class _TextDialog extends StatefulWidget {
  const _TextDialog({
    required this.title,
    required this.label,
    required this.minLength,
    required this.tooShort,
    required this.confirm,
    this.initial = '',
    this.multiline = false,
  });
  final String title, label, tooShort, confirm, initial;
  final int minLength;
  final bool multiline;

  @override
  State<_TextDialog> createState() => _TextDialogState();
}

class _TextDialogState extends State<_TextDialog> {
  late final text = TextEditingController(text: widget.initial);
  String? problem;

  @override
  void dispose() {
    text.dispose();
    super.dispose();
  }

  void confirm() {
    final value = text.text.trim();
    if (value.length < widget.minLength) {
      setState(() => problem = widget.tooShort);
      return;
    }
    Navigator.pop(context, value);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(widget.title),
      scrollable: true,
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            key: const Key('support-dialog-text'),
            controller: text,
            autofocus: true,
            minLines: widget.multiline ? 3 : 1,
            maxLines: widget.multiline ? 10 : 3,
            decoration: InputDecoration(labelText: widget.label),
          ),
          if (problem != null)
            Text(problem!, style: TextStyle(color: context.palette.stop)),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.cancel')),
        ),
        FilledButton(
          key: const Key('support-dialog-confirm'),
          onPressed: confirm,
          child: Text(widget.confirm),
        ),
      ],
    );
  }
}
