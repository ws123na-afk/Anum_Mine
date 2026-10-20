import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../workspace/workspace_models.dart';
import '../brevo/brevo_screen.dart';
import 'marketing_campaigns.dart';
import 'marketing_composer.dart';
import 'marketing_controller.dart';
import 'marketing_insights.dart';
import 'marketing_models.dart';

AnumTone mkTone(String status) => switch (status) {
      'published' || 'sent_to_inbox' || 'connected' || 'active' => AnumTone.ok,
      'failed' || 'error' => AnumTone.stop,
      'pending_approval' ||
      'scheduled' ||
      'publishing' ||
      'needs_reauth' ||
      'paused' =>
        AnumTone.warn,
      _ => AnumTone.neutral,
    };

/// Opens a checked https [link] in the browser; says so when it could not
/// (no usable link, no browser, or the launch failed).
Future<void> _openLink(BuildContext context, Uri? link) async {
  final messenger = ScaffoldMessenger.maybeOf(context);
  final failed = context.anum.t('linkOpenFailed');
  var opened = false;
  if (link != null) {
    try {
      opened = await launchUrl(link, mode: LaunchMode.externalApplication);
    } on Object {
      opened = false;
    }
  }
  if (!opened) messenger?.showSnackBar(SnackBar(content: Text(failed)));
}

String mkWhen(BuildContext context, DateTime? value) {
  if (value == null) return context.anum.t('mk.notScheduled');
  final local = value.toLocal();
  final m = MaterialLocalizations.of(context);
  return '${m.formatMediumDate(local)} '
      '${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}

Future<DateTime?> mkPickTime(BuildContext context, DateTime? initial) async {
  final now = DateTime.now();
  final start =
      initial != null && initial.isAfter(now) ? initial.toLocal() : now;
  final day = await showDatePicker(
      context: context,
      initialDate: start,
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: now.add(const Duration(days: 365)));
  if (day == null || !context.mounted) return null;
  final time = await showTimePicker(
      context: context, initialTime: TimeOfDay.fromDateTime(start));
  if (time == null) return null;
  final picked = DateTime(day.year, day.month, day.day, time.hour, time.minute);
  return picked.isBefore(now) ? now.add(const Duration(minutes: 5)) : picked;
}

/// Social media marketing (docs/marketing.md): posts with approvals, an
/// agenda calendar, campaigns, connected accounts and results.
class MarketingScreen extends StatefulWidget {
  const MarketingScreen({required this.controller, super.key});
  final MarketingController controller;
  @override
  State<MarketingScreen> createState() => _MarketingScreenState();
}

class _MarketingScreenState extends State<MarketingScreen> {
  MarketingController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.setup == null) c.load();
    c.addListener(_showNotice);
  }

  @override
  void dispose() {
    c.removeListener(_showNotice);
    super.dispose();
  }

  void _showNotice() {
    final notice = c.notice;
    if (notice == null || !mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(notice)));
    c.notice = null;
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    const tabs = ['posts', 'calendar', 'campaigns', 'accounts', 'analytics'];
    return DefaultTabController(
      length: tabs.length,
      child: Scaffold(
        appBar: AppBar(
          title: Text(l.t('mk.title')),
          actions: [
            if (c.brevo != null)
              IconButton(
                  onPressed: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                          builder: (_) => BrevoScreen(controller: c.brevo!))),
                  tooltip: l.t('bv.open'),
                  icon: const Icon(Icons.mail_outline)),
            IconButton(
                onPressed: c.load,
                tooltip: l.t('refresh'),
                icon: const Icon(Icons.refresh))
          ],
          bottom: TabBar(
              isScrollable: true,
              tabAlignment: TabAlignment.start,
              tabs: [for (final tab in tabs) Tab(text: l.t('mk.tab.$tab'))]),
        ),
        floatingActionButton: ListenableBuilder(
            listenable: c,
            builder: (context, _) => c.canWrite && c.connected.isNotEmpty
                ? FloatingActionButton.extended(
                    onPressed: () => _compose(context),
                    icon: const Icon(Icons.edit_outlined),
                    label: Text(l.t('mk.new')))
                : const SizedBox.shrink()),
        body: ListenableBuilder(
          listenable: c,
          builder: (context, _) => switch (c.phase) {
            MkPhase.loading => Center(
                child: Semantics(
                    label: l.t('mk.loading'),
                    child: const CircularProgressIndicator())),
            MkPhase.denied => _Message(
                icon: Icons.lock_outline,
                title: l.t('mk.title'),
                text: l.t('mk.denied')),
            MkPhase.error => _Message(
                icon: Icons.error_outline,
                title: l.t('mk.errorTitle'),
                text: l.f('mk.errorHint', {'message': c.error ?? ''}),
                action: l.t('retry'),
                onAction: c.load),
            MkPhase.ready => TabBarView(children: [
                _PostsTab(controller: c),
                _CalendarTab(controller: c),
                CampaignsTab(controller: c),
                _AccountsTab(controller: c),
                _AnalyticsTab(controller: c),
              ]),
          },
        ),
      ),
    );
  }

  Future<void> _compose(BuildContext context) => showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      builder: (_) => ComposeSheet(controller: c));
}

class _Message extends StatelessWidget {
  const _Message(
      {required this.icon,
      required this.title,
      required this.text,
      this.action,
      this.onAction});
  final IconData icon;
  final String title, text;
  final String? action;
  final VoidCallback? onAction;
  @override
  Widget build(BuildContext context) => AnumPage(children: [
        AnumEmpty(
            icon: icon,
            title: title,
            message: text,
            action: action,
            onAction: onAction)
      ]);
}

class _PostsTab extends StatefulWidget {
  const _PostsTab({required this.controller});
  final MarketingController controller;
  @override
  State<_PostsTab> createState() => _PostsTabState();
}

class _PostsTabState extends State<_PostsTab> {
  final _search = TextEditingController();
  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final l = context.anum;
    final filtered = c.statusFilter.isNotEmpty || c.query.isNotEmpty;
    return AnumPage(onRefresh: () => c.filterPosts(), children: [
      AnumHeader(
          eyebrow: l.t('mk.eyebrow'),
          title: l.t('mk.title'),
          subtitle: l.t('mk.subtitle')),
      if (c.connected.isEmpty) Text(l.t('mk.noAccounts')),
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
                    onSubmitted: (value) => c.filterPosts(search: value),
                    decoration: InputDecoration(
                        labelText: l.t('mk.search'),
                        prefixIcon: const Icon(Icons.search)))),
            ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 260),
                child: DropdownButton<String>(
                    isExpanded: true,
                    value: c.statusFilter,
                    onChanged: (value) => c.filterPosts(status: value ?? ''),
                    items: [
                      DropdownMenuItem(value: '', child: Text(l.t('mk.all'))),
                      for (final status in marketingPostStatuses)
                        DropdownMenuItem(
                            value: status,
                            child: Text(l.t('mk.status.$status')))
                    ])),
          ]),
      if (c.posts.isEmpty)
        AnumEmpty(
            icon: Icons.campaign_outlined,
            title: filtered ? l.t('mk.postsNoMatch') : l.t('mk.postsEmpty'),
            message: filtered ? '' : l.t('mk.postsEmptyHint'))
      else
        for (final post in c.posts) PostTile(controller: c, post: post),
      if (c.morePosts)
        Center(
            child: OutlinedButton(
                onPressed: c.busy ? null : c.loadMorePosts,
                child: Text(l.t('mk.loadMore')))),
    ]);
  }
}

/// One post: status, text, problems, the approval with its payload hash, and
/// the actions its status allows.
class PostTile extends StatelessWidget {
  const PostTile({required this.controller, required this.post, super.key});
  final MarketingController controller;
  final MkPost post;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final c = controller;
    final errors = post.issues.where((issue) => issue.severity == 'error');
    return Card(
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
                        Text(
                            '${l.t('mk.platform.${post.platform}')} · ${post.accountName}',
                            style: Theme.of(context).textTheme.titleSmall),
                        AnumPill(
                            label: l.t('mk.status.${post.status}'),
                            tone: mkTone(post.status))
                      ]),
                  const SizedBox(height: AnumSpacing.xxs),
                  Text(mkWhen(context, post.publishedAt ?? post.scheduledAt),
                      style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(post.composed,
                      textDirection: post.language == 'ar'
                          ? TextDirection.rtl
                          : TextDirection.ltr),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      l.f('mk.length',
                          {'length': post.length, 'limit': post.limit}),
                      style: Theme.of(context).textTheme.bodySmall),
                  if (post.untrusted) Text(l.t('mk.untrusted')),
                  for (final issue in errors)
                    Text(issue.message,
                        style: TextStyle(
                            color: Theme.of(context).colorScheme.error)),
                  if (post.lastError != null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(post.lastError!.message,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(post.lastError!.fix),
                  ],
                  if (post.reach != null || post.engagement != null)
                    Text(l.f('mk.metrics', {
                      'reach': post.reach ?? 0,
                      'engagement': post.engagement ?? 0
                    })),
                  if (post.approval != null)
                    ApprovalPanel(controller: c, approval: post.approval!),
                  const SizedBox(height: AnumSpacing.xs),
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        if (mkHttpsLink(post.externalUrl) case final link?)
                          TextButton.icon(
                              onPressed: () => _openLink(context, link),
                              icon: const Icon(Icons.open_in_new),
                              label: Text(l.t('mk.open'))),
                        if (c.canWrite && post.canSchedule)
                          FilledButton(
                              onPressed: c.busy
                                  ? null
                                  : () async {
                                      final at = await mkPickTime(
                                          context, post.scheduledAt);
                                      await c.schedule(
                                          post, at, l.t('mk.scheduled'));
                                    },
                              child: Text(l.t('mk.schedule'))),
                        if (c.canWrite &&
                            post.canEdit &&
                            post.status != 'draft')
                          OutlinedButton(
                              onPressed: c.busy
                                  ? null
                                  : () async {
                                      final at = await mkPickTime(
                                          context, post.scheduledAt);
                                      if (at != null) {
                                        await c.reschedule(
                                            post, at, l.t('mk.moved'));
                                      }
                                    },
                              child: Text(l.t('mk.move'))),
                        if (c.canWrite && post.canEdit)
                          OutlinedButton.icon(
                              onPressed: c.busy
                                  ? null
                                  : () => showModalBottomSheet<void>(
                                      context: context,
                                      isScrollControlled: true,
                                      useSafeArea: true,
                                      builder: (_) => ComposeSheet(
                                          controller: c, post: post)),
                              icon: const Icon(Icons.edit_outlined),
                              label: Text(l.t('mkOps.edit'))),
                        if (c.canWrite && post.canCancel)
                          OutlinedButton(
                              onPressed: c.busy
                                  ? null
                                  : () => c.cancel(post, l.t('mk.cancelled')),
                              child: Text(l.t('mk.cancel'))),
                        if (c.canWrite && post.canPublishNow)
                          FilledButton(
                              onPressed:
                                  c.busy ? null : () => c.publishNow(post),
                              child: Text(l.t('mk.publishNow'))),
                        if (c.canWrite && post.canRetry)
                          FilledButton(
                              onPressed: c.busy ? null : () => c.retry(post),
                              child: Text(l.t('mk.retry'))),
                        if (c.canWrite && post.status != 'publishing')
                          IconButton(
                              tooltip: l.t('mk.delete'),
                              onPressed: c.busy
                                  ? null
                                  : () async {
                                      final ok = await showDialog<bool>(
                                          context: context,
                                          builder: (context) => AlertDialog(
                                                  content: Text(
                                                      l.t('mk.confirmDelete')),
                                                  actions: [
                                                    TextButton(
                                                        onPressed: () =>
                                                            Navigator.pop(
                                                                context, false),
                                                        child: Text(
                                                            l.t('mk.dismiss'))),
                                                    FilledButton(
                                                        onPressed: () =>
                                                            Navigator.pop(
                                                                context, true),
                                                        child: Text(
                                                            l.t('mk.delete')))
                                                  ]));
                                      if (ok ?? false) {
                                        await c.delete(post, l.t('mk.deleted'));
                                      }
                                    },
                              icon: const Icon(Icons.delete_outline)),
                      ]),
                ])));
  }
}

/// A pending publish or auto-post approval: what it sends, the payload hash,
/// and Approve/Reject for whoever may decide (the API checks the role).
class ApprovalPanel extends StatelessWidget {
  const ApprovalPanel(
      {required this.controller, required this.approval, super.key});
  final MarketingController controller;
  final WorkspaceApproval approval;
  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final hash = approval.payloadHash ?? '';
    const hidden = {
      'content_hash',
      'post_id',
      'account_id',
      'campaign_id',
      'account_ids'
    };
    return Container(
        margin: const EdgeInsets.only(top: AnumSpacing.xs),
        padding: const EdgeInsets.all(AnumSpacing.sm),
        decoration: BoxDecoration(
            border: Border.all(color: anumToneColor(context, AnumTone.warn)),
            borderRadius: BorderRadius.circular(12)),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l.t('mk.waiting'),
              style: const TextStyle(fontWeight: FontWeight.w600)),
          for (final entry in approval.arguments.entries)
            if (!hidden.contains(entry.key) &&
                entry.value != null &&
                '${entry.value}' != '[]')
              Text('${entry.key}: ${entry.value}'),
          Text(
              '${l.t('mk.hash')}: ${hash.length > 18 ? '${hash.substring(0, 12)}…${hash.substring(hash.length - 6)}' : hash}',
              style: Theme.of(context).textTheme.bodySmall),
          if (controller.canManage)
            Wrap(spacing: AnumSpacing.xs, children: [
              FilledButton(
                  onPressed: controller.busy
                      ? null
                      : () => controller.decide(approval,
                          approve: true, fallback: l.t('mk.approved')),
                  child: Text(l.t('mk.approve'))),
              OutlinedButton(
                  onPressed: controller.busy
                      ? null
                      : () => controller.decide(approval,
                          approve: false, fallback: l.t('mk.rejected')),
                  child: Text(l.t('mk.reject'))),
            ])
          else
            Text(l.t('mk.ownerDecides')),
        ]));
  }
}

class _CalendarTab extends StatelessWidget {
  const _CalendarTab({required this.controller});
  final MarketingController controller;
  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final now = DateTime.now();
    final upcoming = controller.posts
        .where((post) =>
            post.scheduledAt != null &&
            post.scheduledAt!.isAfter(now.subtract(const Duration(days: 1))) &&
            post.scheduledAt!.isBefore(now.add(const Duration(days: 30))))
        .toList()
      ..sort((a, b) => a.scheduledAt!.compareTo(b.scheduledAt!));
    final days = <String, List<MkPost>>{};
    for (final post in upcoming) {
      final local = post.scheduledAt!.toLocal();
      days
          .putIfAbsent(
              MaterialLocalizations.of(context).formatFullDate(local), () => [])
          .add(post);
    }
    return AnumPage(onRefresh: () => controller.filterPosts(), children: [
      if (days.isEmpty)
        AnumEmpty(
            icon: Icons.calendar_month_outlined,
            title: l.t('mk.tab.calendar'),
            message: l.t('mk.calendarEmpty'))
      else
        for (final entry in days.entries) ...[
          Text(entry.key, style: Theme.of(context).textTheme.titleSmall),
          for (final post in entry.value)
            PostTile(controller: controller, post: post),
        ],
    ]);
  }
}

class _AccountsTab extends StatelessWidget {
  const _AccountsTab({required this.controller});
  final MarketingController controller;
  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final c = controller;
    final setup = c.setup!;
    return AnumPage(onRefresh: c.load, children: [
      AnumHeader(
          title: l.t('mk.tab.accounts'), subtitle: l.t('mk.connectHint')),
      if (!c.canManage) Text(l.t('mk.ownerOnly')),
      for (final provider in setup.providers)
        Card(
            child: Padding(
                padding: const EdgeInsets.all(AnumSpacing.md),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(provider.label,
                          style: Theme.of(context).textTheme.titleSmall),
                      if (!provider.configured)
                        Text(l.f('mk.notSetUp',
                            {'names': provider.missing.join(', ')})),
                      if (c.canManage && provider.configured) ...[
                        const SizedBox(height: AnumSpacing.xs),
                        FilledButton(
                            onPressed: c.busy
                                ? null
                                : () async {
                                    final url =
                                        await c.startConnect(provider.provider);
                                    if (url == null || !context.mounted) {
                                      return;
                                    }
                                    await _openLink(context, mkHttpsLink(url));
                                  },
                            child: Text(
                                l.f('mk.connect', {'name': provider.label}))),
                      ],
                    ]))),
      if (c.accounts.isEmpty)
        AnumEmpty(
            icon: Icons.link_off,
            title: l.t('mk.accountsEmpty'),
            message: l.t('mk.noAccounts'))
      else
        for (final account in c.accounts)
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
                              Text(
                                  '${l.t('mk.platform.${account.platform}')} · ${account.displayName}',
                                  style:
                                      Theme.of(context).textTheme.titleSmall),
                              AnumPill(
                                  label: l.t('mk.account.${account.status}'),
                                  tone: mkTone(account.status))
                            ]),
                        if (account.lastError != null) ...[
                          Text(account.lastError!.message),
                          Text(account.lastError!.fix),
                        ],
                        if (c.canManage && account.status != 'revoked')
                          Wrap(spacing: AnumSpacing.xs, children: [
                            OutlinedButton(
                                onPressed:
                                    c.busy ? null : () => c.check(account),
                                child: Text(l.t('mk.check'))),
                            OutlinedButton(
                                onPressed: c.busy
                                    ? null
                                    : () async {
                                        final ok = await showDialog<bool>(
                                            context: context,
                                            builder: (context) => AlertDialog(
                                                    content: Text(l.f(
                                                        'mk.confirmDisconnect',
                                                        {
                                                          'name': account
                                                              .displayName
                                                        })),
                                                    actions: [
                                                      TextButton(
                                                          onPressed: () =>
                                                              Navigator.pop(
                                                                  context,
                                                                  false),
                                                          child: Text(l.t(
                                                              'mk.dismiss'))),
                                                      FilledButton(
                                                          onPressed: () =>
                                                              Navigator.pop(
                                                                  context,
                                                                  true),
                                                          child: Text(l.t(
                                                              'mk.disconnect')))
                                                    ]));
                                        if (ok ?? false) {
                                          await c.disconnect(
                                              account, l.t('mk.disconnected'));
                                        }
                                      },
                                child: Text(l.t('mk.disconnect'))),
                          ]),
                      ]))),
      PreferencesCard(controller: c),
    ]);
  }
}

class _AnalyticsTab extends StatefulWidget {
  const _AnalyticsTab({required this.controller});
  final MarketingController controller;
  @override
  State<_AnalyticsTab> createState() => _AnalyticsTabState();
}

class _AnalyticsTabState extends State<_AnalyticsTab> {
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final c = widget.controller;
    if (c.summary == null && !c.busy) {
      final language = context.anum.isArabic ? 'ar' : 'en';
      WidgetsBinding.instance
          .addPostFrameCallback((_) => c.loadAnalytics(language: language));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final c = widget.controller;
    final summary = c.summary;
    final language = l.isArabic ? 'ar' : 'en';
    return AnumPage(
        onRefresh: () => c.loadAnalytics(language: language),
        children: [
          AnumHeader(
              title: l.t('mk.tab.analytics'),
              // The header gives its trailing slot a bounded width; the
              // period picker fills at most that and ellipsises its label.
              trailing: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 160),
                  child: DropdownButton<int>(
                      isExpanded: true,
                      value: c.summaryDays,
                      onChanged: (days) =>
                          c.loadAnalytics(days: days, language: language),
                      items: [
                        for (final days in const [7, 30, 90])
                          DropdownMenuItem(
                              value: days,
                              child: Text(l.f('mk.days', {'count': days}),
                                  overflow: TextOverflow.ellipsis))
                      ]))),
          if (summary == null)
            const Center(child: CircularProgressIndicator())
          else ...[
            if (summary.totals.posts == 0)
              AnumEmpty(
                  icon: Icons.insights_outlined,
                  title: l.t('mk.analyticsEmpty'),
                  message: l.t('mk.analyticsHint'))
            else ...[
              AnumMetricGrid(children: [
                AnumMetric(
                    label: l.t('mk.reach'),
                    value: '${summary.totals.reach}',
                    icon: Icons.visibility_outlined),
                AnumMetric(
                    label: l.t('mk.engagement'),
                    value: '${summary.totals.engagement}',
                    caption: l.f('mk.rate', {
                      'rate': summary.totals.rate == null
                          ? '-'
                          : '${(summary.totals.rate! * 100).toStringAsFixed(1)}%'
                    }),
                    icon: Icons.favorite_outline),
                AnumMetric(
                    label: l.t('mk.clicks'),
                    value: '${summary.totals.clicks}',
                    icon: Icons.ads_click),
                AnumMetric(
                    label: l.t('mk.postsCount'),
                    value: '${summary.totals.posts}',
                    icon: Icons.article_outlined),
              ]),
              for (final entry in summary.platforms.entries)
                ListTile(
                    title: Text(l.t('mk.platform.${entry.key}')),
                    subtitle: Text(l.f('mk.metrics', {
                      'reach': entry.value.reach,
                      'engagement': entry.value.engagement
                    }))),
            ],
            Text(l.f('mk.queue', {
              'upcoming': summary.upcoming,
              'pending': summary.pending,
              'failed': summary.failed
            })),
          ],
          BestTimesCard(controller: c),
          if (c.report != null)
            Card(
                child: Padding(
                    padding: const EdgeInsets.all(AnumSpacing.md),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(c.report!.headline,
                              style: Theme.of(context).textTheme.titleSmall),
                          for (final line in c.report!.lines) Text('• $line'),
                          const SizedBox(height: AnumSpacing.sm),
                          if (c.report!.emailAvailable) ...[
                            Text(l.t('mk.report.approvalNote'),
                                style: Theme.of(context).textTheme.bodySmall),
                            Align(
                                alignment: AlignmentDirectional.centerStart,
                                child: OutlinedButton.icon(
                                    onPressed: c.busy
                                        ? null
                                        : () => c.emailReport(language,
                                            requested:
                                                l.t('mk.report.requested'),
                                            notSent: (reason) => l.f(
                                                'mk.report.notSent',
                                                {'reason': reason})),
                                    icon: const Icon(Icons.outgoing_mail),
                                    label: Text(l.t('mk.report.email')))),
                          ] else
                            Text(l.t('mk.report.connect'),
                                style: Theme.of(context).textTheme.bodySmall),
                        ]))),
        ]);
  }
}
