import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_screens.dart';

/// Mail domains on the Mailcow server, read only (docs/email.md#mail-domains):
/// each domain's Mailcow state and a live DNS check of MX, SPF, DKIM and DMARC
/// with the records to set. Changes are made on the web, where every one
/// waits for an approval. Platform operators only; everyone else gets 403.

class MailDomainSummary {
  const MailDomainSummary({
    required this.domain,
    required this.inMailcow,
    required this.allowed,
    this.mailboxes = 0,
    this.aliases = 0,
  });

  factory MailDomainSummary.fromJson(JsonReader j) => MailDomainSummary(
        domain: j.string('domain'),
        inMailcow: j.optBool('in_mailcow') ?? false,
        allowed: j.optBool('allowed') ?? false,
        mailboxes: j.optInt('mailboxes') ?? 0,
        aliases: j.optObjects('aliases', (a) => a.string('address')).length,
      );

  final String domain;
  final bool inMailcow, allowed;
  final int mailboxes, aliases;
}

class MailDomainsOverview {
  const MailDomainsOverview({
    required this.mailcowState,
    required this.domains,
    this.message,
    this.pending = 0,
  });

  factory MailDomainsOverview.fromJson(JsonReader j) => MailDomainsOverview(
        mailcowState: j.optString('mailcow_state') ?? 'off',
        message: j.optString('message'),
        domains: j.optObjects('domains', MailDomainSummary.fromJson),
        pending: j.optObjects('pending', (p) => p.optString('domain')).length,
      );

  final String mailcowState;
  final String? message;
  final List<MailDomainSummary> domains;
  final int pending;
}

class DomainCheck {
  const DomainCheck(
      {required this.status, required this.message, this.found = const []});

  factory DomainCheck.fromJson(JsonReader? j) => j == null
      ? const DomainCheck(status: 'error', message: '')
      : DomainCheck(
          status: j.optString('status') ?? 'error',
          message: j.optString('message') ?? '',
          found: j.optStrings('found') ?? const [],
        );

  final String status, message;
  final List<String> found;
}

class DomainRecord {
  const DomainRecord(
      {required this.kind,
      required this.type,
      required this.name,
      required this.value});

  factory DomainRecord.fromJson(JsonReader j) => DomainRecord(
        kind: j.string('kind'),
        type: j.string('type'),
        name: j.string('name'),
        value: j.string('value'),
      );

  final String kind, type, name, value;
}

class DomainDnsReport {
  const DomainDnsReport({required this.checks, required this.records});

  factory DomainDnsReport.fromJson(JsonReader j) => DomainDnsReport(
        checks: {
          for (final kind in dnsKinds)
            kind: DomainCheck.fromJson(j.optObject(kind)),
        },
        records: j.optObjects('records', DomainRecord.fromJson),
      );

  final Map<String, DomainCheck> checks;
  final List<DomainRecord> records;
}

const dnsKinds = ['mx', 'spf', 'dkim', 'dmarc'];

abstract interface class MailDomainsRepository {
  Future<MailDomainsOverview> domains();
  Future<DomainDnsReport> dns(String domain);
}

class ApiMailDomainsRepository implements MailDomainsRepository {
  const ApiMailDomainsRepository(this.api);
  final AnumApiClient api;

  @override
  Future<MailDomainsOverview> domains() async => MailDomainsOverview.fromJson(
      JsonReader(await api.request('GET', '/api/v1/platform/mail/domains')));

  @override
  Future<DomainDnsReport> dns(String domain) async =>
      DomainDnsReport.fromJson(JsonReader(await api.request('GET',
          '/api/v1/platform/mail/domains/${Uri.encodeComponent(domain)}/dns')));
}

class MailDomainsScreen extends StatefulWidget {
  const MailDomainsScreen({super.key, required this.repository});
  final MailDomainsRepository repository;

  @override
  State<MailDomainsScreen> createState() => _MailDomainsScreenState();
}

class _MailDomainsScreenState extends State<MailDomainsScreen> {
  MailPhase _phase = MailPhase.idle;
  String? _error;
  MailDomainsOverview? _view;
  final Map<String, DomainDnsReport> _dns = {};
  final Map<String, String> _dnsErrors = {};
  final Set<String> _checking = {};

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
      final view = await widget.repository.domains();
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
    }
  }

  Future<void> _check(String domain) async {
    setState(() {
      _checking.add(domain);
      _dnsErrors.remove(domain);
    });
    try {
      final report = await widget.repository.dns(domain);
      if (mounted) setState(() => _dns[domain] = report);
    } on Object catch (e) {
      if (mounted) setState(() => _dnsErrors[domain] = describe(e));
    } finally {
      if (mounted) setState(() => _checking.remove(domain));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(l.t('mc.dom.title')),
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
          icon: Icons.lock_outline, title: l.t('mc.dom.denied'), text: _error);
    }
    final state = stateFor(context, _phase, _error,
        onRetry: _load, hasData: _view != null);
    if (state != null) return state;
    final view = _view!;
    final theme = Theme.of(context);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          Text(l.t('mc.dom.text'), style: theme.textTheme.bodySmall),
          const SizedBox(height: AnumSpacing.sm),
          if (_phase == MailPhase.loading) const LinearProgressIndicator(),
          if (view.mailcowState == 'off')
            Text(l.t('mc.dom.mailcowOff'))
          else if (view.mailcowState != 'ok')
            Text(l.f('mc.dom.mailcowProblem', {'reason': view.message ?? '-'}),
                style: TextStyle(color: theme.colorScheme.error)),
          if (view.pending > 0)
            Text(l.f('mc.dom.pending', {'count': view.pending})),
          const SizedBox(height: AnumSpacing.sm),
          if (view.domains.isEmpty)
            MailState(
                icon: Icons.dns_outlined,
                title: l.t('mc.dom.none'),
                text: l.t('mc.dom.noneText')),
          for (final d in view.domains) _domainCard(context, l, d),
        ],
      ),
    );
  }

  Widget _domainCard(
      BuildContext context, AnumLocalizations l, MailDomainSummary d) {
    final theme = Theme.of(context);
    final report = _dns[d.domain];
    final failed = _dnsErrors[d.domain];
    final checking = _checking.contains(d.domain);
    return Card(
      margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(d.domain,
                textDirection: TextDirection.ltr,
                style: theme.textTheme.titleMedium),
            Text([
              l.t(d.inMailcow ? 'mc.dom.inMailcow' : 'mc.dom.notInMailcow'),
              if (d.inMailcow)
                l.f('mc.dom.mailboxes',
                    {'count': d.mailboxes, 'aliases': d.aliases}),
              if (!d.allowed) l.t('mc.dom.notAllowed'),
            ].join(' · ')),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                onPressed: checking ? null : () => _check(d.domain),
                icon: const Icon(Icons.travel_explore_outlined),
                label: Text(l.t('mc.dom.check')),
              ),
            ),
            if (checking) Text(l.t('mc.dom.checking')),
            if (failed != null)
              Text(l.f('mc.dom.dnsFailed', {'reason': failed}),
                  style: TextStyle(color: theme.colorScheme.error)),
            if (report != null) ..._report(context, l, report),
          ],
        ),
      ),
    );
  }

  List<Widget> _report(
      BuildContext context, AnumLocalizations l, DomainDnsReport report) {
    final theme = Theme.of(context);
    return [
      for (final kind in dnsKinds)
        ListTile(
          contentPadding: EdgeInsets.zero,
          dense: true,
          leading: Icon(
            switch (report.checks[kind]!.status) {
              'ok' => Icons.check_circle_outline,
              'warning' => Icons.info_outline,
              'error' => Icons.help_outline,
              _ => Icons.error_outline,
            },
            color: switch (report.checks[kind]!.status) {
              'danger' || 'missing' => theme.colorScheme.error,
              _ => null,
            },
            semanticLabel: l.t('mc.dom.status.${report.checks[kind]!.status}'),
          ),
          title: Text(
              '${kind.toUpperCase()}: ${l.t('mc.dom.status.${report.checks[kind]!.status}')}'),
          subtitle: Text(report.checks[kind]!.message),
        ),
      Text(l.t('mc.dom.records'), style: theme.textTheme.titleSmall),
      for (final r in report.records)
        Padding(
          padding: const EdgeInsets.only(top: AnumSpacing.xs),
          child: SelectableText('${r.type}  ${r.name}\n${r.value}',
              textDirection: TextDirection.ltr,
              style:
                  theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace')),
        ),
    ];
  }
}
