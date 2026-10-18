import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'brevo_data.dart';

enum BvPhase { loading, ready, notConnected, denied, error }

/// State of the email campaigns screen (docs/brevo.md): read-only on the
/// phone. Campaigns are paged 20 at a time.
class BrevoController extends ChangeNotifier {
  BrevoController(this.repository);
  final BrevoRepository repository;
  static const pageSize = 20;

  BvPhase phase = BvPhase.loading;
  String? error;
  BvStatus? status;
  List<BvCampaign> campaigns = const [];
  int total = 0;
  bool loadingMore = false;
  String? moreError;

  bool get hasMore => campaigns.length < total;

  Future<void> load() async {
    phase = BvPhase.loading;
    error = null;
    notifyListeners();
    try {
      final current = await repository.status();
      status = current;
      if (!current.connected) {
        phase = BvPhase.notConnected;
      } else {
        final page = await repository.campaigns(limit: pageSize);
        campaigns = page.items;
        total = page.total;
        phase = BvPhase.ready;
      }
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? BvPhase.denied : BvPhase.error;
      error = e.message;
    } on Object catch (e) {
      phase = BvPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    moreError = null;
    notifyListeners();
    try {
      final page =
          await repository.campaigns(offset: campaigns.length, limit: pageSize);
      campaigns = [...campaigns, ...page.items];
      total = page.total;
    } on ApiException catch (e) {
      moreError = e.message;
    } on Object catch (e) {
      moreError = '$e';
    }
    loadingMore = false;
    notifyListeners();
  }
}

AnumTone bvTone(String status) => switch (status) {
      'sent' => AnumTone.ok,
      'suspended' || 'unknown' => AnumTone.stop,
      'scheduled' || 'queued' || 'in_process' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String bvStatusName(AnumLocalizations l, String status) {
  const known = {
    'draft',
    'scheduled',
    'queued',
    'in_process',
    'sent',
    'suspended',
    'archive'
  };
  return l.t('bv.status.${known.contains(status) ? status : 'unknown'}');
}

String _date(BuildContext context, DateTime? value) {
  if (value == null) return '-';
  final m = MaterialLocalizations.of(context);
  final local = value.toLocal();
  return '${m.formatMediumDate(local)} '
      '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}

/// Email campaigns sent through the workspace's Brevo account, with their
/// numbers and one campaign's report. Read-only: drafting and sending are on
/// the web, where an owner approves every send.
class BrevoScreen extends StatefulWidget {
  const BrevoScreen({required this.controller, super.key});
  final BrevoController controller;
  @override
  State<BrevoScreen> createState() => _BrevoScreenState();
}

class _BrevoScreenState extends State<BrevoScreen> {
  BrevoController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('bv.title')), actions: [
        IconButton(
            onPressed: c.load,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh))
      ]),
      body: ListenableBuilder(
        listenable: c,
        builder: (context, _) => switch (c.phase) {
          BvPhase.loading => Center(
              child: Semantics(
                  label: l.t('bv.loading'),
                  child: const CircularProgressIndicator())),
          BvPhase.denied =>
            _message(Icons.lock_outline, l.t('bv.title'), l.t('bv.denied')),
          BvPhase.error => _message(Icons.error_outline, l.t('bv.errorTitle'),
              l.f('bv.errorHint', {'message': c.error ?? ''}),
              action: l.t('retry'), onAction: c.load),
          BvPhase.notConnected => _message(
              Icons.link_off,
              c.status?.state == 'needs_key'
                  ? l.t('bv.needsKeyTitle')
                  : l.t('bv.notConnectedTitle'),
              c.status?.state == 'needs_key'
                  ? l.t('bv.needsKey')
                  : (c.status?.canManage ?? false)
                      ? l.t('bv.notConnectedOwner')
                      : l.t('bv.notConnectedMember'),
              action: l.t('retry'),
              onAction: c.load),
          BvPhase.ready => _list(context, l),
        },
      ),
    );
  }

  Widget _message(IconData icon, String title, String text,
          {String? action, VoidCallback? onAction}) =>
      AnumPage(children: [
        AnumEmpty(
            icon: icon,
            title: title,
            message: text,
            action: action,
            onAction: onAction)
      ]);

  Widget _list(BuildContext context, AnumLocalizations l) {
    final status = c.status;
    return AnumPage(onRefresh: c.load, children: [
      Text(l.t('bv.lead'), style: Theme.of(context).textTheme.bodyMedium),
      if (status != null && status.accountEmail.isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(top: AnumSpacing.xs),
          child: Text(
              l.f('bv.account', {
                'name': status.company.isEmpty ? '-' : status.company,
                'email': status.accountEmail
              }),
              style: Theme.of(context).textTheme.bodySmall),
        ),
      const SizedBox(height: AnumSpacing.md),
      if (c.campaigns.isEmpty)
        AnumEmpty(
            icon: Icons.mail_outline,
            title: l.t('bv.empty'),
            message: l.t('bv.emptyHint'))
      else ...[
        for (final campaign in c.campaigns)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: AnumRow(
              icon: Icons.mail_outline,
              tone: bvTone(campaign.status),
              title: campaign.name.isEmpty ? l.t('bv.untitled') : campaign.name,
              subtitle: campaign.subject,
              detail: l.f('bv.line', {
                'delivered': campaign.stats.delivered,
                'opens': bvPercent(campaign.stats.openRate),
                'clicks': bvPercent(campaign.stats.clickRate),
              }),
              trailing: AnumPill(
                  label: bvStatusName(l, campaign.status),
                  tone: bvTone(campaign.status)),
              onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => BrevoReportScreen(
                      repository: c.repository, campaign: campaign))),
            ),
          ),
        if (c.moreError != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
            child: Text(l.f('bv.errorHint', {'message': c.moreError!}),
                style: TextStyle(color: anumToneColor(context, AnumTone.stop))),
          ),
        if (c.hasMore)
          Center(
            child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.more,
                child:
                    Text(c.loadingMore ? l.t('bv.loading') : l.t('bv.more'))),
          ),
        Padding(
          padding: const EdgeInsets.only(top: AnumSpacing.xs),
          child: Text(
              l.f('bv.count', {'shown': c.campaigns.length, 'total': c.total}),
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall),
        ),
      ],
      const SizedBox(height: AnumSpacing.md),
      Text(l.t('bv.webOnly'), style: Theme.of(context).textTheme.bodySmall),
    ]);
  }
}

/// One campaign's report from Brevo.
class BrevoReportScreen extends StatefulWidget {
  const BrevoReportScreen(
      {required this.repository, required this.campaign, super.key});
  final BrevoRepository repository;
  final BvCampaign campaign;
  @override
  State<BrevoReportScreen> createState() => _BrevoReportScreenState();
}

class _BrevoReportScreenState extends State<BrevoReportScreen> {
  BvCampaign? report;
  String? error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final value = await widget.repository.campaign(widget.campaign.id);
      if (mounted) setState(() => report = value);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } on Object catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final value = report;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('bv.report'))),
      body: error != null
          ? AnumPage(children: [
              AnumEmpty(
                  icon: Icons.error_outline,
                  title: l.t('bv.errorTitle'),
                  message: l.f('bv.errorHint', {'message': error!}),
                  action: l.t('retry'),
                  onAction: _load)
            ])
          : value == null
              ? Center(
                  child: Semantics(
                      label: l.t('bv.loading'),
                      child: const CircularProgressIndicator()))
              : AnumPage(onRefresh: _load, children: [
                  Text(value.name.isEmpty ? l.t('bv.untitled') : value.name,
                      style: Theme.of(context).textTheme.titleLarge),
                  Text(value.subject,
                      style: Theme.of(context).textTheme.bodyMedium),
                  const SizedBox(height: AnumSpacing.xs),
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        AnumPill(
                            label: bvStatusName(l, value.status),
                            tone: bvTone(value.status)),
                        if (value.sentAt != null || value.scheduledAt != null)
                          Text(
                              _date(context, value.sentAt ?? value.scheduledAt),
                              style: Theme.of(context).textTheme.bodySmall),
                      ]),
                  const SizedBox(height: AnumSpacing.md),
                  for (final (label, figure) in [
                    (l.t('bv.sent'), '${value.stats.sent}'),
                    (l.t('bv.delivered'), '${value.stats.delivered}'),
                    (
                      l.t('bv.opens'),
                      '${value.stats.opens} (${bvPercent(value.stats.openRate)})'
                    ),
                    (
                      l.t('bv.clicks'),
                      '${value.stats.clicks} (${bvPercent(value.stats.clickRate)})'
                    ),
                    (l.t('bv.unsubscribed'), '${value.stats.unsubscribed}'),
                    (l.t('bv.bounced'), '${value.stats.bounced}'),
                    (l.t('bv.complaints'), '${value.stats.complaints}'),
                  ])
                    AnumRow(title: label, trailing: Text(figure)),
                  if (value.stats.delivered == 0)
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.sm),
                      child: Text(l.t('bv.noNumbers'),
                          style: Theme.of(context).textTheme.bodySmall),
                    ),
                ]),
    );
  }
}
