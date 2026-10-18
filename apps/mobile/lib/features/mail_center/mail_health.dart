import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_screens.dart';

/// The platform operator's mail health (docs/email.md#operator-view):
/// counts, states and timestamps per organisation the operator runs, the
/// Email Center's settings and Mailcow's read-only status. Never a message,
/// address or password. Everyone else gets 403 from the API.

class MailHealthCounts {
  const MailHealthCounts({
    this.sent = 0,
    this.sendFailed = 0,
    this.sendUnknown = 0,
    this.sendRateLimited = 0,
    this.connectRateLimited = 0,
    this.authFailed = 0,
    this.serverOffline = 0,
  });

  factory MailHealthCounts.fromJson(JsonReader? j) => j == null
      ? const MailHealthCounts()
      : MailHealthCounts(
          sent: j.optInt('sent') ?? 0,
          sendFailed: j.optInt('send_failed') ?? 0,
          sendUnknown: j.optInt('send_unknown') ?? 0,
          sendRateLimited: j.optInt('send_rate_limited') ?? 0,
          connectRateLimited: j.optInt('connect_rate_limited') ?? 0,
          authFailed: j.optInt('auth_failed') ?? 0,
          serverOffline: j.optInt('server_offline') ?? 0,
        );

  final int sent,
      sendFailed,
      sendUnknown,
      sendRateLimited,
      connectRateLimited,
      authFailed,
      serverOffline;

  int get rateLimited => sendRateLimited + connectRateLimited;
}

class MailOrgHealth {
  const MailOrgHealth({
    required this.tenantId,
    required this.displayName,
    required this.health,
    required this.counts,
    this.mailboxes = 0,
    this.connected = 0,
    this.authFailed = 0,
    this.offline = 0,
    this.error = 0,
    this.stale = 0,
    this.lastSyncAt,
  });

  factory MailOrgHealth.fromJson(JsonReader j) => MailOrgHealth(
        tenantId: j.string('tenant_id'),
        displayName: j.optString('display_name') ?? j.string('tenant_id'),
        health: j.optString('health') ?? 'quiet',
        counts: MailHealthCounts.fromJson(j.optObject('counts')),
        mailboxes: j.optInt('mailboxes') ?? 0,
        connected: j.optInt('connected') ?? 0,
        authFailed: j.optInt('auth_failed') ?? 0,
        offline: j.optInt('offline') ?? 0,
        error: j.optInt('error') ?? 0,
        stale: j.optInt('stale') ?? 0,
        lastSyncAt: j.optDate('last_sync_at'),
      );

  final String tenantId, displayName, health;
  final MailHealthCounts counts;
  final int mailboxes, connected, authFailed, offline, error, stale;
  final DateTime? lastSyncAt;

  int get notConnected => authFailed + offline + error;
}

class MailHealthView {
  const MailHealthView(
      {required this.enabled,
      required this.days,
      required this.mailboxes,
      required this.totals,
      required this.organisations});

  factory MailHealthView.fromJson(JsonReader j) => MailHealthView(
        enabled: j.optBool('enabled') ?? false,
        days: j.optInt('days') ?? 7,
        mailboxes: j.optInt('mailboxes') ?? 0,
        totals: MailHealthCounts.fromJson(j.optObject('totals')),
        organisations: j.optObjects('organisations', MailOrgHealth.fromJson),
      );

  final bool enabled;
  final int days, mailboxes;
  final MailHealthCounts totals;
  final List<MailOrgHealth> organisations;
}

class MailServerStatus {
  const MailServerStatus({
    required this.enabled,
    required this.configured,
    required this.mailcowState,
    this.imapHost,
    this.smtpHost,
    this.allowedTenants = 0,
    this.sendPerHour = 0,
    this.mailcowMessage,
    this.version,
    this.containersNotRunning = const [],
    this.queueMessages,
  });

  factory MailServerStatus.fromJson(JsonReader j) {
    final server = j.object('server');
    final mailcow = j.optObject('mailcow');
    return MailServerStatus(
      enabled: server.optBool('enabled') ?? false,
      configured: server.optBool('configured') ?? false,
      imapHost: server.optString('imap_host'),
      smtpHost: server.optString('smtp_host'),
      allowedTenants: server.optInt('allowed_tenants') ?? 0,
      sendPerHour: server.optInt('send_per_hour') ?? 0,
      mailcowState: mailcow?.optString('state') ?? 'off',
      mailcowMessage: mailcow?.optString('message'),
      version: mailcow?.optString('version'),
      containersNotRunning:
          mailcow?.optStrings('containers_not_running') ?? const [],
      queueMessages: mailcow?.optInt('queue_messages'),
    );
  }

  final bool enabled, configured;
  final String? imapHost, smtpHost, mailcowMessage, version;
  final int allowedTenants, sendPerHour;
  final String mailcowState;
  final List<String> containersNotRunning;
  final int? queueMessages;
}

abstract interface class MailHealthRepository {
  Future<MailHealthView> health(int days);
  Future<MailServerStatus> server();
}

class ApiMailHealthRepository implements MailHealthRepository {
  const ApiMailHealthRepository(this.api);
  final AnumApiClient api;

  @override
  Future<MailHealthView> health(int days) async =>
      MailHealthView.fromJson(JsonReader(
          await api.request('GET', '/api/v1/platform/mail/health?days=$days')));

  @override
  Future<MailServerStatus> server() async => MailServerStatus.fromJson(
      JsonReader(await api.request('GET', '/api/v1/platform/mail/server')));
}

/// Organisations needing attention first, then by name.
List<MailOrgHealth> sortedForAttention(List<MailOrgHealth> list) {
  const order = {'attention': 0, 'ok': 1, 'quiet': 2};
  return [...list]..sort((a, b) {
      final byHealth = (order[a.health] ?? 3).compareTo(order[b.health] ?? 3);
      return byHealth != 0
          ? byHealth
          : a.displayName.toLowerCase().compareTo(b.displayName.toLowerCase());
    });
}

class MailHealthScreen extends StatefulWidget {
  const MailHealthScreen({super.key, required this.repository});
  final MailHealthRepository repository;

  @override
  State<MailHealthScreen> createState() => _MailHealthScreenState();
}

class _MailHealthScreenState extends State<MailHealthScreen> {
  MailPhase _phase = MailPhase.idle;
  String? _error;
  MailHealthView? _view;
  MailServerStatus? _server;
  String? _serverError;
  int _days = 7;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _phase = MailPhase.loading;
      _error = null;
    });
    try {
      final view = await widget.repository.health(_days);
      if (!mounted) return;
      setState(() {
        _view = view;
        _phase = MailPhase.ready;
      });
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = phaseFor(e);
        _error = describe(e);
      });
      return;
    }
    try {
      final server = await widget.repository.server();
      if (mounted) setState(() => _server = server);
    } on Object catch (e) {
      if (mounted) setState(() => _serverError = describe(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('mc.op.title')),
        actions: [
          IconButton(
            onPressed: _phase == MailPhase.loading ? null : _load,
            tooltip: l.t('refresh'),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: mailPage(_body(context, l)),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l) {
    if (_phase == MailPhase.denied) {
      return MailState(
          icon: Icons.lock_outline, title: l.t('mc.op.denied'), text: _error);
    }
    final state = stateFor(context, _phase, _error,
        onRetry: _load, hasData: _view != null);
    if (state != null) return state;
    final view = _view!;
    final theme = Theme.of(context);
    final q = _query.trim().toLowerCase();
    final orgs = sortedForAttention([
      for (final o in view.organisations)
        if (q.isEmpty ||
            o.displayName.toLowerCase().contains(q) ||
            o.tenantId.toLowerCase().contains(q))
          o
    ]);
    final t = view.totals;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          Text(l.t('mc.op.text'), style: theme.textTheme.bodySmall),
          const SizedBox(height: AnumSpacing.sm),
          if (!view.enabled)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: Text(l.t('mc.off')),
            ),
          SegmentedButton<int>(
            segments: [
              for (final d in const [1, 7, 30, 90])
                ButtonSegment(
                    value: d, label: Text(l.f('mc.op.days', {'days': d}))),
            ],
            selected: {_days},
            onSelectionChanged: _phase == MailPhase.loading
                ? null
                : (value) {
                    _days = value.first;
                    _load();
                  },
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (_phase == MailPhase.loading) const LinearProgressIndicator(),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(AnumSpacing.sm),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(l.t('mc.op.totals'), style: theme.textTheme.titleSmall),
                  Text(l.f('mc.op.totalsText', {
                    'mailboxes': view.mailboxes,
                    'sent': t.sent,
                    'failed': t.sendFailed,
                    'unknown': t.sendUnknown,
                    'limited': t.rateLimited,
                  })),
                ],
              ),
            ),
          ),
          _serverCard(context, l),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            onChanged: (value) => setState(() => _query = value),
            decoration: InputDecoration(
              labelText: l.t('mc.op.search'),
              prefixIcon: const Icon(Icons.search),
            ),
          ),
          const SizedBox(height: AnumSpacing.sm),
          if (view.organisations.isEmpty)
            MailState(
                icon: Icons.domain_disabled_outlined, title: l.t('mc.op.none'))
          else if (orgs.isEmpty)
            MailState(icon: Icons.search_off, title: l.t('mc.op.noMatch')),
          for (final o in orgs)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: ListTile(
                leading: Icon(
                  switch (o.health) {
                    'attention' => Icons.warning_amber_outlined,
                    'ok' => Icons.check_circle_outline,
                    _ => Icons.circle_outlined,
                  },
                  color:
                      o.health == 'attention' ? theme.colorScheme.error : null,
                  semanticLabel: l.t('mc.op.health.${o.health}'),
                ),
                title: Text(o.displayName,
                    maxLines: 1, overflow: TextOverflow.ellipsis),
                subtitle: Text([
                  l.t('mc.op.health.${o.health}'),
                  l.f('mc.op.mailboxes',
                      {'count': o.mailboxes, 'off': o.notConnected}),
                  o.lastSyncAt == null
                      ? l.t('mc.neverChecked')
                      : l.f('mc.lastChecked',
                          {'time': mailWhen(context, o.lastSyncAt!)}),
                  if (o.stale > 0) l.f('mc.op.stale', {'count': o.stale}),
                  l.f('mc.op.sends', {
                    'sent': o.counts.sent,
                    'failed': o.counts.sendFailed,
                    'unknown': o.counts.sendUnknown,
                    'limited': o.counts.rateLimited,
                  }),
                ].join(' · ')),
              ),
            ),
        ],
      ),
    );
  }

  Widget _serverCard(BuildContext context, AnumLocalizations l) {
    final theme = Theme.of(context);
    final server = _server;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('mc.op.server'), style: theme.textTheme.titleSmall),
            if (server == null && _serverError != null)
              Text(l.f('mc.ai.failed', {'reason': _serverError!}),
                  style: TextStyle(color: theme.colorScheme.error))
            else if (server == null)
              const LinearProgressIndicator()
            else ...[
              Text(l.f('mc.op.serverText', {
                'state': server.enabled
                    ? (server.configured ? l.t('mc.op.on') : l.t('mc.notSetUp'))
                    : l.t('mc.op.offShort'),
                'imap': server.imapHost ?? '-',
                'smtp': server.smtpHost ?? '-',
                'orgs': server.allowedTenants,
                'perHour': server.sendPerHour,
              })),
              Text(l.t('mc.op.mailcow.${server.mailcowState}')),
              if (server.mailcowMessage != null) Text(server.mailcowMessage!),
              if (server.mailcowState == 'ok')
                Text(l.f('mc.op.mailcowOk', {
                  'version': server.version ?? '-',
                  'queue': server.queueMessages ?? 0,
                  'down': server.containersNotRunning.isEmpty
                      ? l.t('mc.op.allRunning')
                      : server.containersNotRunning.join(', '),
                })),
            ],
          ],
        ),
      ),
    );
  }
}
