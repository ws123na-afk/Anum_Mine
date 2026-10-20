import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'marketing_controller.dart';
import 'marketing_models.dart';
import 'marketing_ops_models.dart';
import 'marketing_screen.dart' show ApprovalPanel, mkTone;

final _utmPattern = RegExp(r'^[A-Za-z0-9_.\-]*$');

/// Campaigns: search, status filter, paging, create, edit, delete and the
/// owner-only auto-post switch (docs/marketing.md#campaigns).
class CampaignsTab extends StatefulWidget {
  const CampaignsTab({required this.controller, super.key});
  final MarketingController controller;
  @override
  State<CampaignsTab> createState() => _CampaignsTabState();
}

class _CampaignsTabState extends State<CampaignsTab> {
  final _search = TextEditingController();

  MarketingController get c => widget.controller;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _form(BuildContext context, [MkCampaign? campaign]) async {
    final l = context.anum;
    final input = await showModalBottomSheet<MkCampaignInput>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => CampaignFormSheet(campaign: campaign));
    if (input == null) return;
    if (campaign == null) {
      await c.createCampaign(input, l.t('mk.campaignCreated'));
    } else {
      await c.updateCampaign(campaign, input, l.t('mk.campaignUpdated'));
    }
  }

  Future<void> _delete(BuildContext context, MkCampaign campaign) async {
    final l = context.anum;
    final total = campaign.counts.values.fold<int>(0, (sum, v) => sum + v);
    final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: Text(l.t('mkOps.camp.deleteTitle')),
                content: Text(l.f('mkOps.camp.confirmDelete',
                    {'name': campaign.name, 'count': total})),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: Text(l.t('mk.dismiss'))),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: Text(l.t('mk.delete'))),
                ]));
    if (ok ?? false) {
      await c.deleteCampaign(campaign, l.t('mkOps.camp.deleted'));
    }
  }

  Future<void> _autoPost(BuildContext context, MkCampaign campaign) async {
    final l = context.anum;
    if (campaign.autoPost) {
      await c.setAutoPost(campaign,
          enabled: false, done: l.t('mkOps.camp.autoStopped'));
      return;
    }
    final accounts = await showModalBottomSheet<List<String>>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => AutoPostSheet(controller: c, campaign: campaign));
    if (accounts == null) return;
    await c.setAutoPost(campaign,
        enabled: true,
        accountIds: accounts,
        done: l.t('mkOps.camp.autoRequested'));
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final filtered = c.campaignQuery.isNotEmpty || c.campaignStatus.isNotEmpty;
    return AnumPage(onRefresh: c.load, children: [
      AnumHeader(
          title: l.t('mk.tab.campaigns'),
          trailing: c.canWrite
              ? FilledButton.icon(
                  onPressed: c.busy ? null : () => _form(context),
                  icon: const Icon(Icons.add),
                  label: Text(l.t('mk.newCampaign')))
              : null),
      Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.sm,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 360),
                child: TextField(
                    controller: _search,
                    textInputAction: TextInputAction.search,
                    onSubmitted: (value) => c.filterCampaigns(search: value),
                    decoration: InputDecoration(
                        labelText: l.t('mkOps.camp.search'),
                        prefixIcon: const Icon(Icons.search)))),
            ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 260),
                child: DropdownButton<String>(
                    isExpanded: true,
                    value: c.campaignStatus,
                    onChanged: (value) =>
                        c.filterCampaigns(status: value ?? ''),
                    items: [
                      DropdownMenuItem(value: '', child: Text(l.t('mk.all'))),
                      for (final status in marketingCampaignStatuses)
                        DropdownMenuItem(
                            value: status,
                            child: Text(l.t('mk.campaign.$status')))
                    ])),
          ]),
      if (c.campaigns.isEmpty)
        AnumEmpty(
            icon: Icons.flag_outlined,
            title:
                filtered ? l.t('mkOps.camp.noMatch') : l.t('mk.campaignsEmpty'),
            message: filtered
                ? l.t('mkOps.camp.noMatchHint')
                : c.canWrite
                    ? l.t('mk.campaignsHint')
                    : l.t('mkOps.camp.emptyViewer'),
            action: !filtered && c.canWrite ? l.t('mk.newCampaign') : null,
            onAction: !filtered && c.canWrite ? () => _form(context) : null)
      else
        for (final campaign in c.campaigns)
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(AnumSpacing.md),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Wrap(
                            spacing: AnumSpacing.xs,
                            runSpacing: AnumSpacing.xxs,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              Text(campaign.name, style: text.titleSmall),
                              AnumPill(
                                  label: l.t('mk.campaign.${campaign.status}'),
                                  tone: mkTone(campaign.status))
                            ]),
                        if (campaign.product.isNotEmpty) Text(campaign.product),
                        if (campaign.startDate != null ||
                            campaign.endDate != null)
                          Text(
                              l.f('mkOps.camp.range', {
                                'start': campaign.startDate ?? '-',
                                'end': campaign.endDate ?? '-'
                              }),
                              style: text.bodySmall),
                        if (campaign.utmCampaign.isNotEmpty)
                          Text('utm_campaign=${campaign.utmCampaign}',
                              textDirection: TextDirection.ltr,
                              style: text.bodySmall),
                        if (campaign.brief.isNotEmpty)
                          Text(campaign.brief,
                              maxLines: 4,
                              overflow: TextOverflow.ellipsis,
                              style: text.bodySmall),
                        Text(l.f('mk.posts', {
                          'count': campaign.counts.values
                              .fold<int>(0, (sum, value) => sum + value)
                        })),
                        Text(campaign.autoPost
                            ? l.f('mkOps.camp.autoOn',
                                {'count': campaign.autoPostAccounts.length})
                            : l.t('mkOps.camp.autoOff')),
                        if (campaign.approval != null)
                          ApprovalPanel(
                              controller: c, approval: campaign.approval!),
                        const SizedBox(height: AnumSpacing.xs),
                        _actions(context, campaign),
                      ]))),
      if (c.moreCampaigns)
        Center(
            child: OutlinedButton(
                onPressed: c.busy ? null : c.loadMoreCampaigns,
                child: Text(l.t('mk.loadMore')))),
    ]);
  }

  Widget _actions(BuildContext context, MkCampaign campaign) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final canAuto = c.canManage &&
        campaign.approval == null &&
        (campaign.autoPost || campaign.status == 'active');
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (c.canWrite)
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          for (final (status, label) in [
            ('active', 'mk.activate'),
            ('paused', 'mk.pause'),
            ('completed', 'mk.complete')
          ])
            if (status != campaign.status)
              OutlinedButton(
                  onPressed: c.busy
                      ? null
                      : () => c.setCampaignStatus(
                          campaign, status, l.t('mk.campaignUpdated')),
                  child: Text(l.t(label))),
          OutlinedButton.icon(
              onPressed: c.busy ? null : () => _form(context, campaign),
              icon: const Icon(Icons.edit_outlined),
              label: Text(l.t('mkOps.camp.edit'))),
          IconButton(
              tooltip: l.f('mkOps.camp.deleteOf', {'name': campaign.name}),
              onPressed: c.busy ? null : () => _delete(context, campaign),
              icon: const Icon(Icons.delete_outline)),
        ]),
      if (c.canManage && campaign.approval == null)
        Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                    onPressed: c.busy || !canAuto
                        ? null
                        : () => _autoPost(context, campaign),
                    icon: Icon(campaign.autoPost
                        ? Icons.pause_circle_outline
                        : Icons.bolt_outlined),
                    label: Text(campaign.autoPost
                        ? l.t('mkOps.camp.autoStop')
                        : l.t('mkOps.camp.autoStart'))))),
      if (c.canManage && !campaign.autoPost && campaign.status != 'active')
        Text(l.t('mkOps.camp.autoNeedsActive'), style: text.bodySmall)
      else if (!c.canManage)
        Text(l.t('mkOps.camp.autoOwnerOnly'), style: text.bodySmall)
      else if (!campaign.autoPost && campaign.approval == null)
        Text(l.t('mkOps.camp.autoHint'), style: text.bodySmall),
    ]);
  }
}

/// Create or edit a campaign: the name, product, objective, brief, dates and
/// the tracking name links carry. Pops the input to save.
class CampaignFormSheet extends StatefulWidget {
  const CampaignFormSheet({this.campaign, super.key});
  final MkCampaign? campaign;
  @override
  State<CampaignFormSheet> createState() => _CampaignFormSheetState();
}

class _CampaignFormSheetState extends State<CampaignFormSheet> {
  late final _name = TextEditingController(text: widget.campaign?.name);
  late final _product = TextEditingController(text: widget.campaign?.product);
  late final _objective =
      TextEditingController(text: widget.campaign?.objective);
  late final _brief = TextEditingController(text: widget.campaign?.brief);
  late final _utm = TextEditingController(text: widget.campaign?.utmCampaign);
  late String? _start = widget.campaign?.startDate;
  late String? _end = widget.campaign?.endDate;
  String? _problem;

  @override
  void dispose() {
    for (final c in [_name, _product, _objective, _brief, _utm]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<String?> _pick(String? current) async {
    final now = DateTime.now();
    final initial = DateTime.tryParse(current ?? '') ?? now;
    final day = await showDatePicker(
        context: context,
        initialDate: initial,
        firstDate: DateTime(now.year - 1),
        lastDate: DateTime(now.year + 3));
    return day == null ? current : mkIsoDate(day);
  }

  void _save() {
    final l = context.anum;
    if (_name.text.trim().isEmpty) {
      return setState(() => _problem = l.t('mkOps.camp.nameRequired'));
    }
    if (!_utmPattern.hasMatch(_utm.text.trim())) {
      return setState(() => _problem = l.t('mkOps.camp.utmInvalid'));
    }
    if (_start != null && _end != null && _end!.compareTo(_start!) < 0) {
      return setState(() => _problem = l.t('mkOps.camp.datesOrder'));
    }
    Navigator.pop(
        context,
        MkCampaignInput(
            name: _name.text.trim(),
            product: _product.text.trim(),
            objective: _objective.text.trim(),
            brief: _brief.text.trim(),
            startDate: _start,
            endDate: _end,
            utmCampaign: _utm.text.trim()));
  }

  Widget _date(String label, String? value, ValueChanged<String?> onChanged) {
    final l = context.anum;
    return Row(children: [
      Expanded(
          child: OutlinedButton.icon(
              onPressed: () async => onChanged(await _pick(value)),
              icon: const Icon(Icons.event_outlined),
              label: Text('$label: ${value ?? '-'}',
                  overflow: TextOverflow.ellipsis))),
      if (value != null)
        IconButton(
            tooltip: l.f('mkOps.camp.clearDate', {'label': label}),
            onPressed: () => onChanged(null),
            icon: const Icon(Icons.close)),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final editing = widget.campaign != null;
    return Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(context).bottom,
            left: AnumSpacing.md,
            right: AnumSpacing.md,
            top: AnumSpacing.md),
        child: SingleChildScrollView(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
              Semantics(
                  header: true,
                  child: Text(
                      editing
                          ? l.t('mkOps.camp.editTitle')
                          : l.t('mk.newCampaign'),
                      style: Theme.of(context).textTheme.titleLarge)),
              const SizedBox(height: AnumSpacing.sm),
              TextField(
                  controller: _name,
                  maxLength: 160,
                  decoration:
                      InputDecoration(labelText: l.t('mk.campaignName'))),
              TextField(
                  controller: _product,
                  maxLength: 200,
                  decoration:
                      InputDecoration(labelText: l.t('mk.campaignProduct'))),
              TextField(
                  controller: _objective,
                  maxLength: 500,
                  decoration:
                      InputDecoration(labelText: l.t('mkOps.camp.objective'))),
              TextField(
                  controller: _brief,
                  minLines: 2,
                  maxLines: 5,
                  maxLength: 4000,
                  decoration:
                      InputDecoration(labelText: l.t('mkOps.camp.brief'))),
              _date(l.t('mkOps.camp.start'), _start,
                  (value) => setState(() => _start = value)),
              _date(l.t('mkOps.camp.end'), _end,
                  (value) => setState(() => _end = value)),
              const SizedBox(height: AnumSpacing.xs),
              TextField(
                  controller: _utm,
                  maxLength: 100,
                  textDirection: TextDirection.ltr,
                  decoration: InputDecoration(
                      labelText: l.t('mkOps.camp.utm'),
                      helperText: l.t('mkOps.camp.utmHint'),
                      helperMaxLines: 3)),
              if (_problem != null)
                Semantics(
                    liveRegion: true,
                    child: Text(_problem!,
                        style: TextStyle(
                            color: Theme.of(context).colorScheme.error))),
              const SizedBox(height: AnumSpacing.sm),
              Row(children: [
                Expanded(
                    child: OutlinedButton(
                        onPressed: () => Navigator.pop(context),
                        child: Text(l.t('mk.dismiss')))),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: FilledButton(
                        onPressed: _save,
                        child: Text(editing
                            ? l.t('mkOps.camp.saveChanges')
                            : l.t('mk.create')))),
              ]),
              const SizedBox(height: AnumSpacing.md),
            ])));
  }
}

/// Which connected accounts a campaign may post to without a per-post
/// approval. Pops the chosen ids; the owner approval follows.
class AutoPostSheet extends StatefulWidget {
  const AutoPostSheet(
      {required this.controller, required this.campaign, super.key});
  final MarketingController controller;
  final MkCampaign campaign;
  @override
  State<AutoPostSheet> createState() => _AutoPostSheetState();
}

class _AutoPostSheetState extends State<AutoPostSheet> {
  late final Set<String> _chosen = {
    for (final account in widget.controller.connected) account.id
  };

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final accounts = widget.controller.connected;
    return Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: SingleChildScrollView(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
              Semantics(
                  header: true,
                  child: Text(l.t('mkOps.camp.autoTitle'),
                      style: text.titleLarge)),
              const SizedBox(height: AnumSpacing.xs),
              Text(widget.campaign.name, style: text.titleSmall),
              const SizedBox(height: AnumSpacing.xs),
              Text(l.t('mkOps.camp.autoHint')),
              const SizedBox(height: AnumSpacing.sm),
              Text(l.t('mkOps.camp.autoAccounts'), style: text.titleSmall),
              if (accounts.isEmpty) Text(l.t('mk.noAccounts')),
              for (final account in accounts)
                CheckboxListTile(
                    value: _chosen.contains(account.id),
                    onChanged: (on) => setState(() => on ?? false
                        ? _chosen.add(account.id)
                        : _chosen.remove(account.id)),
                    title: Text(
                        '${l.t('mk.platform.${account.platform}')} · ${account.displayName}')),
              const SizedBox(height: AnumSpacing.sm),
              Row(children: [
                Expanded(
                    child: OutlinedButton(
                        onPressed: () => Navigator.pop(context),
                        child: Text(l.t('mk.dismiss')))),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: FilledButton(
                        onPressed: _chosen.isEmpty
                            ? null
                            : () => Navigator.pop(context, _chosen.toList()),
                        child: Text(l.t('mkOps.camp.autoAsk')))),
              ]),
            ])));
  }
}
