import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'accounts_screen.dart';
import 'business_controller.dart';
import 'business_models.dart';
import 'business_pipeline_repository.dart';
import 'business_pipeline_screen.dart';
import 'business_repository.dart';
import 'invoices.dart';
import 'portal_access.dart';
import 'catalog_screen.dart';
import 'deals_data.dart';
import 'deals_screen.dart';
import 'desk_widgets.dart';
import 'project_money.dart';
import 'support_screen.dart';

/// Business on the phone (docs/business.md): the owner's summary, leads with
/// their evidence and state, the pipeline, proposals waiting for acceptance
/// and project milestones. Sending is off; the catalog and proposal editing
/// are on the web, and acceptance is decided in Approvals.
class BusinessScreen extends StatefulWidget {
  const BusinessScreen({
    required this.controller,
    this.onOpenApprovals,
    this.pipelineRepository,
    super.key,
  });
  final BusinessController controller;
  final VoidCallback? onOpenApprovals;
  final BusinessPipelineRepository? pipelineRepository;

  @override
  State<BusinessScreen> createState() => _BusinessScreenState();
}

enum _Section {
  overview,
  leads,
  pipeline,
  proposals,
  projects,
  catalog,
  accounts,
  invoices,
  clients,
  support,
}

String money(double? amount, String currency) => formatMoney(amount, currency);

AnumTone stateTone(String state) => switch (state) {
      'won' => AnumTone.ok,
      'lost' || 'suppressed' => AnumTone.stop,
      'qualified' || 'offered' || 'meeting' || 'replied' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

class _BusinessScreenState extends State<BusinessScreen> {
  _Section section = _Section.overview;
  final search = TextEditingController();

  /// Prospecting and outreach (business_pipeline_screen.dart), over the same API client.
  BusinessPipelineRepository? get _pipeline {
    if (widget.pipelineRepository != null) return widget.pipelineRepository;
    final repository = widget.controller.repository;
    return repository is ApiBusinessRepository
        ? ApiBusinessPipelineRepository(repository.api)
        : null;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) widget.controller.load();
    });
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  void pick(_Section next) {
    setState(() => section = next);
    final c = widget.controller;
    switch (next) {
      case _Section.pipeline:
        c.first(c.opportunities);
      case _Section.proposals:
        c.first(c.proposals);
      case _Section.projects:
        c.first(c.projects);
      case _Section.leads:
        if (c.leads.state == BusinessLoad.idle) c.first(c.leads);
      case _Section.invoices:
        final invoices = c.invoices;
        if (invoices != null && invoices.state != BusinessLoad.ready) {
          invoices.load();
        }
      case _Section.clients:
        final invoices = c.invoices;
        if (invoices != null && invoices.clients.state == BusinessLoad.idle) {
          invoices.firstClients();
        }
      case _Section.catalog:
        final catalog = c.catalog;
        catalog?.load();
      case _Section.accounts:
        final accounts = c.accounts;
        accounts?.load();
      case _Section.support:
        final support = c.support;
        if (support != null && support.state != BusinessLoad.ready) {
          support.load();
        }
      case _Section.overview:
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return Scaffold(
          appBar: AppBar(title: Text(l.t('biz.title'))),
          body: AnumPage(
            onRefresh: c.load,
            children: [
              // The app bar already says "Business"; only the subtitle here.
              Text(
                l.t('biz.subtitle'),
                style: TextStyle(
                    color: context.palette.muted, fontSize: AnumType.smPlus),
              ),
              ...switch (c.setupState) {
                BusinessLoad.idle || BusinessLoad.loading => [
                    AnumFeedback(
                      kind: AnumFeedbackKind.loading,
                      message: l.t('biz.loading'),
                    ),
                  ],
                BusinessLoad.denied => [
                    AnumFeedback(
                      kind: AnumFeedbackKind.permission,
                      message: l.t('biz.denied'),
                    ),
                  ],
                BusinessLoad.error => [
                    AnumFeedback(
                      kind: AnumFeedbackKind.error,
                      message:
                          l.f('biz.failed', {'message': c.setupError ?? ''}),
                      onRetry: c.load,
                    ),
                  ],
                BusinessLoad.ready => [
                    Text(
                      l.t('biz.sendingOff'),
                      style: TextStyle(color: context.palette.muted),
                    ),
                    if (_pipeline != null)
                      Align(
                        alignment: AlignmentDirectional.centerStart,
                        child: OutlinedButton.icon(
                          key: const Key('biz-open-pipeline'),
                          icon: const Icon(Icons.travel_explore),
                          label: Text(l.t('biz.openPipeline')),
                          onPressed: () => Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => BusinessPipelineScreen(
                                repository: _pipeline!,
                                canWrite: c.setup?.permissions.write ?? false,
                                onOpenApprovals: widget.onOpenApprovals,
                              ),
                            ),
                          ),
                        ),
                      ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final item in _Section.values.where(
                          (item) => switch (item) {
                            _Section.support => c.support != null,
                            _Section.invoices ||
                            _Section.clients =>
                              c.invoices != null,
                            _Section.catalog => c.catalog != null,
                            _Section.accounts => c.accounts != null,
                            _ => true,
                          },
                        ))
                          ChoiceChip(
                            key: Key('biz-tab-${item.name}'),
                            label: Text(l.t('biz.tab.${item.name}')),
                            selected: section == item,
                            onSelected: (_) => pick(item),
                          ),
                      ],
                    ),
                    ..._body(context, c),
                  ],
              },
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(
    BuildContext context,
    BusinessController c,
  ) =>
      switch (section) {
        _Section.overview => _overview(context, c),
        _Section.leads => _leads(context, c),
        _Section.pipeline => _list<BusinessOpportunity>(
            context,
            c,
            c.opportunities,
            'biz.pipeline',
            (item) {
              final l = context.anum;
              return AnumRow(
                title: item.name,
                subtitle:
                    '${item.accountName} · ${l.t('biz.stage.${item.stage}')}',
                detail: l.f('biz.pipeline.assumed', {
                  'value': money(item.value, item.currency),
                  'probability': item.probability == null
                      ? l.t('biz.unknown')
                      : '${item.probability!.toStringAsFixed(0)}%',
                }),
                icon: Icons.trending_up,
                trailing:
                    c.deals == null ? null : const Icon(Icons.chevron_right),
                onTap: c.deals == null
                    ? null
                    : () => Navigator.of(context).push(
                          MaterialPageRoute<void>(
                            builder: (_) => OpportunityScreen(
                              controller: c,
                              opportunity: item,
                            ),
                          ),
                        ),
              );
            },
          ),
        _Section.proposals => _list<BusinessProposal>(
            context,
            c,
            c.proposals,
            'biz.proposals',
            (item) {
              final l = context.anum;
              return AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AnumRow(
                      title: item.title,
                      subtitle:
                          '${item.accountName} · ${l.f('biz.proposals.version', {
                            'version': item.version
                          })}',
                      detail: money(item.total, item.currency),
                      icon: Icons.description_outlined,
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: AnumPill(
                        label: l.t('biz.proposal.${item.status}'),
                        tone: item.status == 'accepted'
                            ? AnumTone.ok
                            : item.status == 'pending_approval'
                                ? AnumTone.warn
                                : AnumTone.neutral,
                      ),
                    ),
                    if (item.decisionNote.isNotEmpty) Text(item.decisionNote),
                    if (item.portalDecision != null)
                      PortalDecisionLine(decision: item.portalDecision!),
                    if (item.approvalState == 'pending') ...[
                      Text(l.t('biz.proposals.waiting')),
                      if (widget.onOpenApprovals != null)
                        Align(
                          alignment: AlignmentDirectional.centerStart,
                          child: TextButton(
                            onPressed: widget.onOpenApprovals,
                            child: Text(l.t('biz.proposals.openApprovals')),
                          ),
                        ),
                    ],
                  ],
                ),
              );
            },
          ),
        _Section.projects => _list<BusinessProject>(
            context,
            c,
            c.projects,
            'biz.projects',
            (item) => _ProjectCard(project: item, controller: c),
          ),
        _Section.invoices => [
            InvoicesView(
              controller: c.invoices!,
              onOpenApprovals: widget.onOpenApprovals,
            ),
          ],
        _Section.clients => [ClientsView(controller: c.invoices!)],
        _Section.catalog => [
            CatalogView(
              controller: c.catalog!,
              canManage: c.setup?.permissions.manage ?? false,
            ),
          ],
        _Section.accounts => [
            AccountsView(
              controller: c.accounts!,
              canWrite: c.setup?.permissions.write ?? false,
              canManage: c.setup?.permissions.manage ?? false,
            ),
          ],
        _Section.support => [
            SupportTicketsView(
              controller: c.support!,
              onOpenApprovals: widget.onOpenApprovals,
              loadProjects: () async {
                final page = await c.repository.projects(offset: 0, limit: 100);
                return [
                  for (final item in page.items)
                    if (item.status != 'closed')
                      (item.id, '${item.name} · ${item.accountName}'),
                ];
              },
            ),
          ],
      };

  List<Widget> _overview(BuildContext context, BusinessController c) {
    final l = context.anum;
    switch (c.kpiState) {
      case BusinessLoad.idle:
      case BusinessLoad.loading:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.loading,
            message: l.t('biz.loading'),
          ),
        ];
      case BusinessLoad.denied:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.permission,
            message: l.t('biz.denied'),
          ),
        ];
      case BusinessLoad.error:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: l.f('biz.failed', {'message': c.kpiError ?? ''}),
            onRetry: c.loadKpi,
          ),
        ];
      case BusinessLoad.ready:
        break;
    }
    final k = c.kpi!;
    String sum(List<MoneyFigure> list) => list.isEmpty
        ? money(0, 'SAR')
        : list.map((item) => money(item.amount, item.currency)).join(' · ');
    final qualified =
        k.funnel.where((step) => step.stage == 'qualified').firstOrNull;
    return [
      Wrap(
        spacing: 8,
        children: [
          for (final days in const [7, 30, 90])
            ChoiceChip(
              label: Text(l.f('biz.kpi.days', {'days': days})),
              selected: c.days == days,
              onSelected: (_) => c.setDays(days),
            ),
        ],
      ),
      AnumMetricGrid(
        children: [
          AnumMetric(
            label: l.t('biz.kpi.leads'),
            value: '${k.leadsCreated}',
            icon: Icons.person_search_outlined,
            caption: l.f('biz.kpi.days', {'days': k.days}),
          ),
          AnumMetric(
            label: l.t('biz.kpi.qualified'),
            value: qualified?.ratePct == null ? '-' : '${qualified!.ratePct}%',
            icon: Icons.verified_outlined,
            caption: l.f('biz.kpi.ofLeads', {
              'reached': qualified?.reached ?? 0,
              'of': qualified?.of ?? 0,
            }),
          ),
          AnumMetric(
            label: l.t('biz.kpi.pipeline'),
            value: sum(k.pipelineAssumed),
            icon: Icons.trending_up,
            caption: l.f('biz.kpi.open', {'count': k.openOpportunities}),
          ),
          AnumMetric(
            label: l.t('biz.kpi.contracted'),
            value: sum(k.contracted),
            icon: Icons.handshake_outlined,
            caption: l.f('biz.kpi.accepted', {'count': k.proposalsAccepted}),
          ),
          AnumMetric(
            label: l.t('biz.kpi.cash'),
            value: sum(k.cashCollected),
            icon: Icons.payments_outlined,
            caption: l.t('biz.kpi.cashHint'),
          ),
        ],
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.kpi.funnel')),
            for (final step in k.funnel)
              AnumRow(
                title: l.t('biz.funnel.${step.stage}'),
                detail: l.f('biz.kpi.reachedOf', {
                  'reached': step.reached,
                  'of': step.of,
                }),
                subtitle: step.ratePct == null ? null : '${step.ratePct}%',
              ),
          ],
        ),
      ),
      for (final note in k.notes)
        Text(note, style: TextStyle(color: context.palette.muted)),
    ];
  }

  List<Widget> _leads(BuildContext context, BusinessController c) {
    final l = context.anum;
    return [
      TextField(
        key: const Key('biz-search'),
        controller: search,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          labelText: l.t('biz.leads.search'),
          prefixIcon: const Icon(Icons.search),
        ),
        onSubmitted: (value) => c.search(value, c.stateFilter),
      ),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          ChoiceChip(
            label: Text(l.t('biz.leads.all')),
            selected: c.stateFilter == null,
            onSelected: (_) => c.search(search.text, null),
          ),
          for (final state in const [
            'discovered',
            'researched',
            'qualified',
            'offered',
            'won',
            'lost',
          ])
            ChoiceChip(
              label: Text(l.t('biz.state.$state')),
              selected: c.stateFilter == state,
              onSelected: (_) => c.search(search.text, state),
            ),
        ],
      ),
      if (c.deals != null && (c.setup?.permissions.write ?? false))
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton.icon(
            key: const Key('lead-new'),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => LeadFormScreen(controller: c),
              ),
            ),
            icon: const Icon(Icons.add),
            label: Text(l.t('biz.deal.lead.new')),
          ),
        ),
      ..._list<BusinessLead>(
        context,
        c,
        c.leads,
        'biz.leads',
        (lead) => AnumRow(
          key: Key('biz-lead-${lead.id}'),
          title: lead.accountName,
          subtitle:
              '${lead.title} · ${lead.productName ?? l.t('biz.leads.noOffer')}',
          detail: l.f('biz.score', {
            'total': lead.scoreTotal,
            'known': lead.scoreKnown,
          }),
          icon:
              lead.blockReason == null ? Icons.business_outlined : Icons.block,
          tone: lead.blockReason == null ? null : AnumTone.stop,
          trailing: AnumPill(
            label: l.t('biz.state.${lead.state}'),
            tone: stateTone(lead.state),
          ),
          onTap: () => Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => LeadScreen(controller: c, leadId: lead.id),
            ),
          ),
        ),
        filtered: c.query != null || c.stateFilter != null,
      ),
    ];
  }

  List<Widget> _list<T>(
    BuildContext context,
    BusinessController c,
    PagedList<T> list,
    String prefix,
    Widget Function(T item) row, {
    bool filtered = false,
  }) {
    final l = context.anum;
    switch (list.state) {
      case BusinessLoad.idle:
      case BusinessLoad.loading:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.loading,
            message: l.t('biz.loading'),
          ),
        ];
      case BusinessLoad.denied:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.permission,
            message: l.t('biz.denied'),
          ),
        ];
      case BusinessLoad.error:
        return [
          AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: l.f('biz.failed', {'message': list.error ?? ''}),
            onRetry: () => c.first(list),
          ),
        ];
      case BusinessLoad.ready:
        break;
    }
    if (list.items.isEmpty) {
      return [
        filtered
            ? AnumFeedback(
                kind: AnumFeedbackKind.empty,
                message: l.t('biz.leads.noMatch'),
              )
            : AnumEmpty(
                title: l.t('$prefix.empty'),
                message: l.t('$prefix.emptyText'),
                icon: Icons.inbox_outlined,
              ),
      ];
    }
    return [
      for (final item in list.items) row(item),
      if (list.hasMore)
        OutlinedButton(
          onPressed: list.loadingMore ? null : () => c.more(list),
          child: Text(
            l.f('biz.more', {'shown': list.items.length, 'total': list.total}),
          ),
        ),
      if (list.error != null)
        Text(list.error!, style: TextStyle(color: context.palette.stop)),
    ];
  }
}

class _ProjectCard extends StatelessWidget {
  const _ProjectCard({required this.project, required this.controller});
  final BusinessProject project;
  final BusinessController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    const statuses = [
      'planned',
      'in_progress',
      'submitted',
      'accepted',
      'blocked',
    ];
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumRow(
            title: project.name,
            subtitle:
                '${project.accountName} · ${l.t('biz.project.${project.status}')}',
            detail: l.f('biz.projects.paid', {
              'paid': money(project.paidValue, project.currency),
              'total': money(project.contractTotal, project.currency),
            }),
            icon: Icons.work_outline,
          ),
          for (final m in project.milestones)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text('${m.title} · ${money(m.amount, project.currency)}'),
                  DropdownButton<String>(
                    key: Key('biz-milestone-${m.id}'),
                    isExpanded: true,
                    value: m.status,
                    items: [
                      for (final status in statuses)
                        DropdownMenuItem(
                          value: status,
                          child: Text(l.t('biz.milestone.$status')),
                        ),
                    ],
                    onChanged: (value) async {
                      if (value == null || value == m.status) return;
                      final messenger = ScaffoldMessenger.of(context);
                      try {
                        await controller.milestone(m.id, value);
                      } on ApiException catch (error) {
                        messenger.showSnackBar(
                          SnackBar(content: Text(error.message)),
                        );
                      }
                    },
                  ),
                ],
              ),
            ),
          if (controller.money != null && controller.setup != null)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: Key('biz-project-money-${project.id}'),
                icon: const Icon(Icons.request_quote_outlined),
                label: Text(l.t('biz.money.open')),
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => ProjectMoneyScreen(
                      repository: controller.money!,
                      project: project,
                      permissions: controller.setup!.permissions,
                      onContractChanged: controller.refreshProjects,
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// One lead: what is known, the sources, the trail; move it or record what happened.
class LeadScreen extends StatefulWidget {
  const LeadScreen({required this.controller, required this.leadId, super.key});
  final BusinessController controller;
  final String leadId;

  @override
  State<LeadScreen> createState() => _LeadScreenState();
}

class _LeadScreenState extends State<LeadScreen> {
  LeadDetail? detail;
  String? error;
  bool busy = false;
  final note = TextEditingController();
  String noteKey = DateTime.now().microsecondsSinceEpoch.toString();

  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    note.dispose();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final next = await widget.controller.repository.lead(widget.leadId);
      if (mounted) {
        setState(() {
          detail = next;
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> move(String to) async {
    final l = context.anum;
    var reason = '';
    if (to == 'lost' || to == 'suppressed') {
      final given = await showDialog<String>(
        context: context,
        builder: (context) => _ReasonDialog(
          title: l.f('biz.lead.move', {'state': l.t('biz.state.$to')}),
        ),
      );
      if (given == null) return;
      if (given.length < 3) {
        setState(() => error = l.t('biz.lead.reasonNeeded'));
        return;
      }
      reason = given;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final next = await widget.controller.transition(
        widget.leadId,
        to,
        reason,
      );
      if (mounted) setState(() => detail = next);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> edit() async {
    final next = await Navigator.of(context).push<LeadDetail>(
      MaterialPageRoute(
        builder: (_) => LeadFormScreen(
          controller: widget.controller,
          lead: detail!.lead,
        ),
      ),
    );
    if (next != null && mounted) setState(() => detail = next);
  }

  Future<void> openOpportunity() async {
    final made = await Navigator.of(context).push<BusinessOpportunity>(
      MaterialPageRoute(
        builder: (_) => OpportunityFormScreen(
          controller: widget.controller,
          lead: detail!.lead,
        ),
      ),
    );
    if (made == null || !mounted) return;
    await load();
    if (!mounted) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (_) => OpportunityScreen(
          controller: widget.controller,
          opportunity: made,
        ),
      ),
    );
  }

  Future<void> remove() async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.deal.lead.deleteTitle', {'name': detail!.lead.title}),
      message: l.t('biz.deal.lead.deleteText'),
      action: l.t('biz.deal.lead.delete'),
    );
    if (!ok) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.controller.deleteLead(widget.leadId);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          error = e.readable;
          busy = false;
        });
      }
    }
  }

  Future<void> record() async {
    final text = note.text.trim();
    if (text.length < 2) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.controller.repository.logNote(
        widget.leadId,
        'note',
        text,
        noteKey,
      );
      noteKey = DateTime.now().microsecondsSinceEpoch.toString();
      note.clear();
      await load();
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = detail;
    final write = widget.controller.setup?.permissions.write ?? false;
    final manage = widget.controller.setup?.permissions.manage ?? false;
    return Scaffold(
      appBar: AppBar(title: Text(d?.lead.accountName ?? l.t('biz.title'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (d == null && error == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: l.t('biz.loading'),
            ),
          if (error != null)
            AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: error!,
              onRetry: load,
            ),
          if (d != null) ...[
            AnumHeader(
              eyebrow: l.t('biz.state.${d.lead.state}'),
              title: d.lead.title,
              subtitle: l.f('biz.score', {
                'total': d.lead.scoreTotal,
                'known': d.lead.scoreKnown,
              }),
            ),
            if (d.lead.blockReason != null)
              Text(
                d.lead.blockReason!,
                style: TextStyle(color: context.palette.stop),
              ),
            if (write)
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final to in d.transitions.where(
                    (state) => state != 'won',
                  ))
                    OutlinedButton(
                      key: Key('biz-move-$to'),
                      onPressed: busy ? null : () => move(to),
                      child: Text(
                        l.f('biz.lead.move', {'state': l.t('biz.state.$to')}),
                      ),
                    ),
                ],
              ),
            if (widget.controller.deals != null && (write || manage))
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (write)
                    OutlinedButton.icon(
                      key: const Key('lead-edit'),
                      onPressed: busy ? null : edit,
                      icon: const Icon(Icons.edit_outlined),
                      label: Text(l.t('biz.form.edit')),
                    ),
                  if (write &&
                      d.opportunityId == null &&
                      opportunityReadyStates.contains(d.lead.state))
                    OutlinedButton.icon(
                      key: const Key('lead-open-opportunity'),
                      onPressed: busy ? null : openOpportunity,
                      icon: const Icon(Icons.trending_up),
                      label: Text(l.t('biz.deal.opp.new')),
                    ),
                  if (manage)
                    OutlinedButton.icon(
                      key: const Key('lead-delete'),
                      onPressed: busy ? null : remove,
                      icon: const Icon(Icons.delete_outline),
                      label: Text(l.t('biz.deal.lead.delete')),
                    ),
                ],
              ),
            if (d.gaps.isNotEmpty &&
                (d.lead.state == 'discovered' || d.lead.state == 'researched'))
              AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AnumHeader(title: l.t('biz.lead.toQualify')),
                    for (final gap in d.gaps) Text('• $gap'),
                  ],
                ),
              ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumRow(
                    title: l.t('biz.lead.website'),
                    detail: d.website ?? l.t('biz.unknown'),
                    icon: Icons.language,
                  ),
                  AnumRow(
                    title: l.t('biz.lead.contact'),
                    detail: d.contactName ?? l.t('biz.unknown'),
                    icon: Icons.person_outline,
                  ),
                  if (d.consentGap != null)
                    Text(
                      d.consentGap!,
                      style: TextStyle(color: context.palette.muted),
                    ),
                ],
              ),
            ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: l.t('biz.lead.evidence')),
                  if (d.evidence.isEmpty) Text(l.t('biz.lead.noEvidence')),
                  for (final item in d.evidence)
                    AnumRow(
                      title: item.title.isEmpty ? item.url : item.title,
                      subtitle:
                          '${l.t('biz.confidence.${item.confidence}')} · ${l.f('biz.lead.published', {
                            'date': item.publishedOn ?? l.t('biz.unknown')
                          })}',
                      detail: item.excerpt.isEmpty ? null : item.excerpt,
                      icon: Icons.link,
                    ),
                ],
              ),
            ),
            if (d.inferences.isNotEmpty)
              AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AnumHeader(title: l.t('biz.lead.inferences')),
                    for (final item in d.inferences)
                      AnumRow(
                        title: item.statement,
                        subtitle: l.f('biz.lead.sources', {
                          'count': item.sources,
                        }),
                        icon: Icons.lightbulb_outline,
                      ),
                  ],
                ),
              ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: l.t('biz.lead.activity')),
                  if (write) ...[
                    TextField(
                      key: const Key('biz-note'),
                      controller: note,
                      decoration: InputDecoration(
                        labelText: l.t('biz.lead.note'),
                        hintText: l.t('biz.lead.noteHint'),
                      ),
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerEnd,
                      child: TextButton(
                        onPressed: busy ? null : record,
                        child: Text(l.t('biz.lead.noteSave')),
                      ),
                    ),
                  ],
                  for (final item in d.activities)
                    AnumRow(
                      title: item.summary,
                      subtitle: item.actor,
                      icon: Icons.history,
                    ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Asks for the reason a lead is lost or suppressed; owns its text field.
class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({required this.title});
  final String title;

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final reason = TextEditingController();

  @override
  void dispose() {
    reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        controller: reason,
        decoration: InputDecoration(labelText: l.t('biz.lead.reason')),
        autofocus: true,
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.cancel')),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, reason.text.trim()),
          child: Text(l.t('biz.confirm')),
        ),
      ],
    );
  }
}
