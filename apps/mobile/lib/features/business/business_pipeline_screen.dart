import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_pipeline_repository.dart';

/// Prospecting and outreach on the phone (docs/business.md#prospecting-pipeline,
/// docs/business.md#outreach): estimate and confirm a discovery run for one
/// product, follow its progress, read a lead's product fit and apply a score
/// suggestion, and work the outbox (queue, ask again, cancel, record a reply).
/// Owners decide sends in Approvals; target profiles and signals are on the web.
class BusinessPipelineScreen extends StatefulWidget {
  const BusinessPipelineScreen({
    required this.repository,
    this.canWrite = true,
    this.onOpenApprovals,
    this.pollEvery = const Duration(seconds: 2),
    super.key,
  });
  final BusinessPipelineRepository repository;
  final bool canWrite;
  final VoidCallback? onOpenApprovals;
  final Duration pollEvery;

  @override
  State<BusinessPipelineScreen> createState() => _BusinessPipelineScreenState();
}

enum _Tab { prospecting, outreach }

enum _Load { loading, ready, error, denied }

AnumTone runTone(String status) => switch (status) {
      'succeeded' => AnumTone.ok,
      'failed' || 'interrupted' => AnumTone.stop,
      'running' || 'partial' || 'stopped' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

AnumTone outboxTone(String status) => switch (status) {
      'sent' => AnumTone.ok,
      'failed' || 'rejected' || 'cancelled' || 'needs_review' => AnumTone.stop,
      'pending_approval' || 'held' || 'sending' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

class _BusinessPipelineScreenState extends State<BusinessPipelineScreen> {
  _Tab tab = _Tab.prospecting;
  _Load load = _Load.loading;
  String error = '';
  PipelineSettings? settings;
  List<({String id, String name})> products = const [];
  String? productId;
  final terms = TextEditingController();
  PipelineRunView? estimate, current;
  List<PipelineRunView> runs = const [];
  int runsTotal = 0;
  LeadProfileView? profile;
  String? profileLead;
  List<OutboxItemView> outbox = const [];
  int outboxTotal = 0;
  List<ReadyDraftView> ready = const [];
  bool busy = false;
  Timer? poll;

  BusinessPipelineRepository get repo => widget.repository;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => refresh());
  }

  @override
  void dispose() {
    poll?.cancel();
    terms.dispose();
    super.dispose();
  }

  Future<void> refresh() async {
    setState(() => load = _Load.loading);
    try {
      final loaded = await repo.settings();
      final items = await repo.products();
      final page = await repo.runs(offset: 0);
      final box = await repo.outbox(offset: 0);
      final drafts = widget.canWrite ? await repo.ready() : <ReadyDraftView>[];
      if (!mounted) return;
      setState(() {
        settings = loaded;
        products = items;
        productId ??= items.isEmpty ? null : items.first.id;
        runs = page.items;
        runsTotal = page.total;
        outbox = box.items;
        outboxTotal = box.total;
        ready = drafts;
        load = _Load.ready;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        load = e.statusCode == 403 ? _Load.denied : _Load.error;
        error = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        load = _Load.error;
        error = '$e';
      });
    }
  }

  Future<void> act(Future<void> Function() run, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    final l = context.anum;
    setState(() => busy = true);
    try {
      await run();
      messenger.showSnackBar(SnackBar(content: Text(done)));
    } on ApiException catch (e) {
      messenger.showSnackBar(
          SnackBar(content: Text(l.f('bizp.failed', {'message': e.message}))));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void follow(PipelineRunView run) {
    poll?.cancel();
    setState(() => current = run);
    if (!run.running) return;
    poll = Timer.periodic(widget.pollEvery, (_) async {
      try {
        final next = await repo.run(run.id);
        if (!mounted) return;
        setState(() => current = next);
        if (!next.running) {
          poll?.cancel();
          unawaited(refresh());
        }
      } on ApiException {
        poll?.cancel();
      }
    });
  }

  Future<void> openProfile(String leadId) async {
    try {
      final view = await repo.profile(leadId);
      if (mounted) {
        setState(() {
          profile = view;
          profileLead = leadId;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content:
                Text(context.anum.f('bizp.failed', {'message': e.message}))));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('bizp.title'))),
      body: AnumPage(
        onRefresh: refresh,
        children: [
          AnumHeader(eyebrow: l.t('bizp.eyebrow'), title: l.t('bizp.title')),
          ...switch (load) {
            _Load.loading => [
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('biz.loading')),
              ],
            _Load.denied => [
                AnumFeedback(kind: AnumFeedbackKind.permission, message: error),
              ],
            _Load.error => [
                AnumFeedback(
                  kind: AnumFeedbackKind.error,
                  message: l.f('bizp.failed', {'message': error}),
                  onRetry: refresh,
                ),
              ],
            _Load.ready => [
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final item in _Tab.values)
                    ChoiceChip(
                      key: Key('bizp-tab-${item.name}'),
                      label: Text(l.t('bizp.tab.${item.name}')),
                      selected: tab == item,
                      onSelected: (_) => setState(() => tab = item),
                    ),
                ]),
                if (settings?.paused ?? false)
                  Text(l.f('bizp.paused', {'reason': settings!.pausedReason}),
                      style: TextStyle(color: context.palette.stop)),
                ...(tab == _Tab.prospecting
                    ? _prospecting(context)
                    : _outreach(context)),
              ],
          },
        ],
      ),
    );
  }

  List<Widget> _prospecting(BuildContext context) {
    final l = context.anum;
    final s = settings!;
    return [
      Text(l.t('bizp.webOnly'), style: TextStyle(color: context.palette.muted)),
      if (!s.webSearch)
        AnumFeedback(
            kind: AnumFeedbackKind.offline, message: l.t('bizp.noSearch')),
      if (widget.canWrite && products.isEmpty)
        AnumFeedback(
            kind: AnumFeedbackKind.empty, message: l.t('bizp.noProducts')),
      if (widget.canWrite && products.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('bizp.newRun'),
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: AnumSpacing.sm),
              DropdownButtonFormField<String>(
                key: const Key('bizp-product'),
                initialValue: productId,
                isExpanded: true,
                decoration: InputDecoration(labelText: l.t('bizp.product')),
                items: [
                  for (final item in products)
                    DropdownMenuItem(
                        value: item.id,
                        child:
                            Text(item.name, overflow: TextOverflow.ellipsis)),
                ],
                onChanged: (value) => setState(() => productId = value),
              ),
              const SizedBox(height: AnumSpacing.sm),
              TextField(
                key: const Key('bizp-terms'),
                controller: terms,
                decoration: InputDecoration(
                    labelText: l.t('bizp.terms'),
                    helperText: l.t('bizp.termsHint')),
              ),
              const SizedBox(height: AnumSpacing.sm),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton(
                  key: const Key('bizp-estimate'),
                  onPressed: busy || productId == null
                      ? null
                      : () => act(() async {
                            final words = terms.text
                                .split(',')
                                .map((e) => e.trim())
                                .where((e) => e.isNotEmpty)
                                .toList();
                            final run =
                                await repo.estimateDiscovery(productId!, words);
                            if (mounted) setState(() => estimate = run);
                          }, l.t('bizp.estimated')),
                  child: Text(l.t('bizp.estimate')),
                ),
              ),
            ],
          ),
        ),
      if (estimate != null) _estimateCard(context, estimate!),
      if (current != null) _runCard(context, current!),
      if (profile != null) _profileCard(context, profile!),
      Text(l.f('bizp.runs', {'count': runsTotal}),
          style: Theme.of(context).textTheme.titleMedium),
      if (runs.isEmpty)
        AnumFeedback(kind: AnumFeedbackKind.empty, message: l.t('bizp.noRuns'))
      else
        for (final run in runs)
          AnumRow(
            key: Key('bizp-run-${run.id}'),
            title: l.t('bizp.kind.${run.kind}'),
            subtitle: l.f('bizp.used',
                {'searches': run.usedSearches, 'pages': run.usedPages}),
            trailing: AnumPill(
                label: l.t('bizp.status.${run.status}'),
                tone: runTone(run.status)),
            icon: Icons.travel_explore,
            onTap: () async {
              try {
                follow(await repo.run(run.id));
              } on ApiException catch (_) {}
            },
          ),
    ];
  }

  Widget _estimateCard(BuildContext context, PipelineRunView run) {
    final l = context.anum;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t('bizp.estimateTitle'),
              style: Theme.of(context).textTheme.titleMedium),
          Text(l.f('bizp.estimateLine', {
            'searches': run.searches,
            'pages': run.pageFetches,
            'calls': run.modelCalls,
            'cost': run.cost == null
                ? l.t('biz.unknown')
                : 'USD ${run.cost!.toStringAsFixed(4)}',
          })),
          if (run.queries.isNotEmpty)
            Text(run.queries.join(' · '),
                style: TextStyle(color: context.palette.muted)),
          for (final problem in run.problems)
            Text(problem, style: TextStyle(color: context.palette.stop)),
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton(
              key: const Key('bizp-confirm'),
              onPressed: busy || !run.fits
                  ? null
                  : () => act(() async {
                        final started = await repo.confirm(run.id);
                        if (!mounted) return;
                        setState(() => estimate = null);
                        follow(started);
                      }, l.t('bizp.started')),
              child: Text(l.t('bizp.confirm')),
            ),
            OutlinedButton(
              onPressed: busy
                  ? null
                  : () => act(() async {
                        await repo.stop(run.id);
                        if (mounted) setState(() => estimate = null);
                      }, l.t('bizp.discarded')),
              child: Text(l.t('bizp.discard')),
            ),
          ]),
        ],
      ),
    );
  }

  Widget _runCard(BuildContext context, PipelineRunView run) {
    final l = context.anum;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(l.t('bizp.kind.${run.kind}'),
                    style: Theme.of(context).textTheme.titleMedium),
                AnumPill(
                    label: l.t('bizp.status.${run.status}'),
                    tone: runTone(run.status)),
              ]),
          Text(l.f('bizp.usedOf', {
            'searches': run.usedSearches,
            'maxSearches': run.searches,
            'pages': run.usedPages,
            'maxPages': run.pageFetches,
          })),
          if (run.stopReason.isNotEmpty)
            Text(run.stopReason, style: TextStyle(color: context.palette.warn)),
          for (final step in run.steps.reversed.take(10))
            Text(
                '${step.target == null ? '' : '${step.target}: '}${step.summary}',
                style: TextStyle(color: context.palette.muted)),
          for (final lead in run.leads)
            AnumRow(
              key: Key('bizp-lead-${lead.id}'),
              title: lead.name,
              subtitle: lead.domain ?? '-',
              icon: Icons.apartment,
              trailing: TextButton(
                  onPressed: () => openProfile(lead.id),
                  child: Text(l.t('bizp.fit'))),
            ),
          if (run.running && widget.canWrite)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                onPressed: () => act(
                    () async => follow(await repo.stop(run.id)),
                    l.t('bizp.stopping')),
                child: Text(l.t('bizp.stop')),
              ),
            ),
        ],
      ),
    );
  }

  Widget _profileCard(BuildContext context, LeadProfileView view) {
    final l = context.anum;
    return AnumSurface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t('bizp.profile'),
              style: Theme.of(context).textTheme.titleMedium),
          Text(view.best == null
              ? l.t('bizp.noFit')
              : l.f('bizp.bestFit', {
                  'name': view.best!.productName,
                  'score': view.best!.score ?? 0
                })),
          for (final fit in view.fits)
            Text(
                '${fit.productName}: ${fit.disqualified ? l.t('bizp.disqualified') : fit.score == null ? l.t('bizp.noProfile') : l.f('bizp.score5', {
                        'score': fit.score!
                      })}',
                style: TextStyle(color: context.palette.muted)),
          const SizedBox(height: AnumSpacing.sm),
          Text(l
              .f('bizp.scoreLine', {'total': view.total, 'known': view.known})),
          for (final line in view.lines)
            AnumRow(
              title: l.t('biz.criterion.${line.criterion}'),
              subtitle: line.conversationOnly
                  ? l.t('bizp.conversationOnly')
                  : line.suggestedScore == null
                      ? l.t('bizp.noSuggestion')
                      : '${l.f('bizp.score5', {
                              'score': line.suggestedScore!
                            })} · ${line.suggestedReason}',
              detail: line.score == null ? l.t('biz.unknown') : '${line.score}',
              trailing: line.suggestedScore != null &&
                      widget.canWrite &&
                      profileLead != null
                  ? TextButton(
                      key: Key('bizp-apply-${line.criterion}'),
                      onPressed: () => act(() async {
                        await repo.applySuggestion(
                            profileLead!,
                            line.criterion,
                            line.suggestedScore!,
                            line.suggestedReason,
                            line.suggestedEvidence);
                        await openProfile(profileLead!);
                      }, l.t('bizp.applied')),
                      child: Text(l.t('bizp.apply')),
                    )
                  : null,
            ),
          for (final fact in view.facts.take(10))
            Text('${fact.label}: ${fact.value}'),
        ],
      ),
    );
  }

  List<Widget> _outreach(BuildContext context) {
    final l = context.anum;
    final s = settings!;
    return [
      Text(s.sendingEnabled ? l.t('bizp.sendingOn') : l.t('bizp.sendingOff'),
          style: TextStyle(color: context.palette.muted)),
      if (widget.canWrite) ...[
        Text(l.t('bizp.ready'), style: Theme.of(context).textTheme.titleMedium),
        if (ready.isEmpty)
          AnumFeedback(
              kind: AnumFeedbackKind.empty, message: l.t('bizp.readyEmpty'))
        else
          for (final draft in ready)
            AnumRow(
              key: Key('bizp-ready-${draft.draftId}'),
              title: draft.company,
              subtitle: draft.subject,
              icon: Icons.outgoing_mail,
              trailing: TextButton(
                onPressed: busy
                    ? null
                    : () => act(() async {
                          await repo.queue(draft.draftId);
                          await refresh();
                        }, l.t('bizp.queued')),
                child: Text(l.t('bizp.queue')),
              ),
            ),
      ],
      Text(l.f('bizp.outbox', {'count': outboxTotal}),
          style: Theme.of(context).textTheme.titleMedium),
      if (outbox.isEmpty)
        AnumFeedback(
            kind: AnumFeedbackKind.empty, message: l.t('bizp.outboxEmpty'))
      else
        for (final item in outbox) _outboxItem(context, item),
    ];
  }

  Widget _outboxItem(BuildContext context, OutboxItemView item) {
    final l = context.anum;
    return AnumSurface(
      key: Key('bizp-item-${item.id}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(item.company,
                    style: Theme.of(context).textTheme.titleSmall),
                AnumPill(
                    label: l.t('bizp.outboxStatus.${item.status}'),
                    tone: outboxTone(item.status)),
              ]),
          Text('${item.recipient} · ${l.f('bizp.step', {'step': item.step})}',
              style: TextStyle(color: context.palette.muted)),
          Text(item.subject.isEmpty ? item.body : item.subject,
              maxLines: 2, overflow: TextOverflow.ellipsis),
          if (item.note.isNotEmpty)
            Text(item.note, style: TextStyle(color: context.palette.muted)),
          if (widget.canWrite)
            Wrap(spacing: 8, runSpacing: 4, children: [
              if (item.waitingApproval && widget.onOpenApprovals != null)
                TextButton(
                    onPressed: widget.onOpenApprovals,
                    child: Text(l.t('bizp.openApprovals'))),
              if (item.status == 'held' ||
                  item.status == 'rejected' ||
                  (item.status == 'scheduled' && item.due))
                TextButton(
                  onPressed: () => act(() async {
                    await repo.requestApproval(item.id);
                    await refresh();
                  }, l.t('bizp.asked')),
                  child: Text(l.t('bizp.askAgain')),
                ),
              if (const ['scheduled', 'pending_approval', 'held']
                  .contains(item.status))
                TextButton(
                  onPressed: () => act(() async {
                    await repo.cancel(item.id, l.t('bizp.cancelledOnPhone'));
                    await refresh();
                  }, l.t('bizp.cancelled')),
                  child: Text(l.t('bizp.cancel')),
                ),
              TextButton(
                key: Key('bizp-reply-${item.id}'),
                onPressed: () => _reply(context, item),
                child: Text(l.t('bizp.recordReply')),
              ),
            ]),
        ],
      ),
    );
  }

  Future<void> _reply(BuildContext context, OutboxItemView item) async {
    final l = context.anum;
    final value = await showDialog<String>(
      context: context,
      builder: (_) => _ReplyDialog(company: item.company),
    );
    if (value == null || !mounted) return;
    await act(() async {
      await repo.recordReply(item.leadId, item.channel, value);
      await refresh();
    }, l.t('bizp.replyRecorded'));
  }
}

/// The reply text for one lead; it owns its controller so closing never touches a disposed one.
class _ReplyDialog extends StatefulWidget {
  const _ReplyDialog({required this.company});
  final String company;

  @override
  State<_ReplyDialog> createState() => _ReplyDialogState();
}

class _ReplyDialogState extends State<_ReplyDialog> {
  final text = TextEditingController();

  @override
  void dispose() {
    text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.f('bizp.replyFrom', {'company': widget.company})),
      content: TextField(
        key: const Key('bizp-reply-text'),
        controller: text,
        maxLines: 3,
        decoration: InputDecoration(
            labelText: l.t('bizp.replyText'),
            helperText: l.t('bizp.replyPrivacy'),
            helperMaxLines: 3),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('bizp.close'))),
        FilledButton(
            onPressed: () => Navigator.pop(context, text.text),
            child: Text(l.t('bizp.recordReply'))),
      ],
    );
  }
}
