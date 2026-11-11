import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../owner_desk/desk_widgets.dart' show deskText;
import '../../src/localization/anum_localizations.dart';

/// Ready to sell on the phone (docs/selling-readiness.md#checklist): the
/// platform owner's checklist read from real state by
/// `GET /api/v1/platform/readiness`, and the mail domain's SPF and DMARC
/// lookup (`POST /api/v1/platform/readiness/dns`). Both are platform owner
/// only (`platform:operate`); everyone else sees who can.

class ReadinessItem {
  const ReadinessItem(
      {required this.key,
      required this.state,
      required this.fix,
      this.detail = const {}});
  final String key, state, fix;
  final Map<String, Object?> detail;

  factory ReadinessItem.fromJson(JsonReader j) => ReadinessItem(
        key: j.string('key'),
        state: switch (j.optString('state')) {
          'done' => 'done',
          'warning' => 'warning',
          'unknown' => 'unknown',
          _ => 'missing',
        },
        fix: j.optString('fix') ?? '',
        detail: j.optMap('detail') ?? const {},
      );

  bool get done => state == 'done';

  /// The console tab on the web where this is fixed, or null.
  String? get webTab => switch (fix) {
        'payments' => 'settings',
        'company' || 'legal' => 'company',
        'plans' => 'plans',
        'mail' => 'mail',
        _ => null,
      };
}

class Readiness {
  const Readiness(
      {required this.items,
      required this.done,
      required this.total,
      required this.ready,
      this.mailDomain,
      this.checkedAt});
  final List<ReadinessItem> items;
  final int done, total;
  final bool ready;
  final String? mailDomain;
  final DateTime? checkedAt;

  factory Readiness.fromJson(JsonReader j) {
    final items = j.objects('items', ReadinessItem.fromJson);
    return Readiness(
      items: items,
      done: j.optInt('done') ?? items.where((i) => i.done).length,
      total: j.optInt('total') ?? items.length,
      ready: j.optBool('ready') ?? false,
      mailDomain: j.optString('mail_domain'),
      checkedAt: j.optDate('checked_at'),
    );
  }

  /// Missing first, then the ones to check, the unrecorded, and done last.
  List<ReadinessItem> get sorted {
    const rank = {'missing': 0, 'warning': 1, 'unknown': 2, 'done': 3};
    final list = [...items];
    // A stable sort keeps the server's order inside each state.
    final indexed = [for (var i = 0; i < list.length; i++) (i, list[i])];
    indexed.sort((a, b) {
      final r = rank[a.$2.state]!.compareTo(rank[b.$2.state]!);
      return r != 0 ? r : a.$1.compareTo(b.$1);
    });
    return [for (final x in indexed) x.$2];
  }
}

class DnsRecordCheck {
  const DnsRecordCheck({required this.status, this.record, this.error});
  final String status;
  final String? record, error;
}

class DnsResult {
  const DnsResult(
      {required this.domain,
      required this.spf,
      required this.dmarc,
      this.checkedAt});
  final String domain;
  final DnsRecordCheck spf, dmarc;
  final DateTime? checkedAt;

  factory DnsResult.fromJson(JsonReader j) {
    DnsRecordCheck read(JsonReader r) => DnsRecordCheck(
        status: r.optString('status') ?? 'missing',
        record: r.optString('record'),
        error: r.optString('error'));
    return DnsResult(
        domain: j.optString('domain') ?? '',
        spf: read(j.object('spf')),
        dmarc: read(j.object('dmarc')),
        checkedAt: j.optDate('checked_at'));
  }
}

final _domain = RegExp(r'^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$');

/// A domain the server accepts for the lookup (letters, digits, hyphens and
/// at least one dot), lower case and without a final dot; null otherwise.
String? cleanDomain(String text) {
  var v = text.trim().toLowerCase();
  if (v.endsWith('.')) v = v.substring(0, v.length - 1);
  return v.length >= 3 && v.length <= 253 && _domain.hasMatch(v) ? v : null;
}

/// The lines under a checklist item, in the page language (`detailText` on
/// the web), from the item's real state.
List<String> readinessLines(
    AnumLocalizations l, ReadinessItem item, String Function(DateTime) when) {
  final d = item.detail;
  final lines = <String>[];
  String? date(Object? v) {
    final parsed = v is String ? DateTime.tryParse(v) : null;
    return parsed == null ? null : when(parsed);
  }

  switch (item.key) {
    case 'payment_provider':
      final active = d['active'];
      if (active == 'stripe' || active == 'moyasar') {
        final currencies = (d['currencies'] as List?)?.join(', ') ?? '';
        lines.add(l.f('selling.ready.provider.active.$active',
            {'currencies': currencies}));
      }
      lines.add(l.f('selling.ready.provider.stripe', {
        'state': l.t('selling.ready.providerState.${d['stripe'] ?? 'off'}')
      }));
      lines.add(l.f('selling.ready.provider.moyasar', {
        'state': l.t('selling.ready.providerState.${d['moyasar'] ?? 'off'}')
      }));
      if (d['moyasar_optional'] != false) {
        lines.add(l.t('selling.ready.provider.moyasarOptional'));
      }
      if (item.state == 'warning') {
        lines.add(l.t('selling.ready.provider.testKeys'));
      }
      lines.add(l.t(d['bank_transfer'] == true
          ? 'selling.ready.provider.bankOn'
          : 'selling.ready.provider.bankOff'));
    case 'company_details':
      final missing = (d['missing'] as List?) ?? const [];
      if (missing.isNotEmpty) {
        lines.add(l.f('selling.ready.company.missing', {
          'fields':
              missing.map((k) => l.t('selling.company.field.$k')).join(', ')
        }));
      }
    case 'mail_sending':
      lines.add(l.t(d['mailbox_connected'] == true
          ? 'selling.ready.mail.mailboxOn'
          : 'selling.ready.mail.mailboxOff'));
      lines.add(l.t(d['smtp_configured'] == true
          ? 'selling.ready.mail.smtpOn'
          : 'selling.ready.mail.smtpOff'));
      lines.add(l.t('selling.ready.mail.spfNote'));
    case 'backups_local' || 'backups_offsite':
      final ok = d['ok'];
      if (d['recorded'] != true) {
        lines.add(l.t('selling.ready.backup.notRecorded'));
      } else if (ok == null) {
        lines.add(l.t('selling.ready.backup.never'));
      } else {
        final finished = date(d['finished_at']);
        if (finished != null) {
          lines.add(l.f(
              ok == true
                  ? 'selling.ready.backup.lastOk'
                  : 'selling.ready.backup.lastFailed',
              {'when': finished}));
        }
        if (ok != true) {
          final success = date(d['last_success_at']);
          lines.add(success == null
              ? l.t('selling.ready.backup.noSuccess')
              : l.f('selling.ready.backup.lastSuccess', {'when': success}));
          if (item.key == 'backups_offsite') {
            lines.add(l.t('selling.ready.backup.offsiteHint'));
          }
        }
      }
    case 'legal_pages':
      final missing = (d['missing'] as Map?) ?? const {};
      final total =
          missing.values.whereType<num>().fold<num>(0, (a, b) => a + b).toInt();
      lines.add(total > 0
          ? l.f('selling.ready.legal.missing', {'count': total})
          : l.t('selling.ready.legal.filled'));
      lines.add(l.f('selling.ready.legal.published', {
        'count': ((d['published'] as List?) ?? const []).length,
        'total': 5
      }));
    case 'plans':
      lines.add(l.f('selling.ready.plans.counts', {
        'active': (d['active'] as num?)?.toInt() ?? 0,
        'published': (d['published'] as num?)?.toInt() ?? 0,
      }));
    case 'ai_model':
      lines.add(d['placeholder'] == true
          ? l.t('selling.ready.model.placeholder')
          : d['key_missing'] == true
              ? l.f('selling.ready.model.keyMissing',
                  {'provider': '${d['provider']}'})
              : l.f('selling.ready.model.real',
                  {'provider': '${d['provider']}'}));
    case 'public_issuer':
      if (d['configured'] != true) {
        lines.add(l.t('selling.ready.issuer.notSet'));
      } else if (d['is_you'] != true) {
        lines.add(l.t('selling.ready.issuer.other'));
      }
      if (d['web_app_url'] != true) {
        lines.add(l.t('selling.ready.issuer.noWebUrl'));
      }
  }
  return lines;
}

abstract interface class SellingRepository {
  Future<Readiness> readiness();
  Future<DnsResult> checkDns(String domain);
}

class ApiSellingRepository implements SellingRepository {
  const ApiSellingRepository(this.api);
  final AnumApiClient api;

  @override
  Future<Readiness> readiness() async => Readiness.fromJson(
      JsonReader(await api.request('GET', '/api/v1/platform/readiness')));

  @override
  Future<DnsResult> checkDns(String domain) async => DnsResult.fromJson(
      JsonReader(await api.request('POST', '/api/v1/platform/readiness/dns',
          body: {'domain': domain})));
}

enum SellingPhase { loading, ready, denied, error }

class SellingController extends ChangeNotifier {
  SellingController(this.repository);
  final SellingRepository repository;

  SellingPhase phase = SellingPhase.loading;
  String? error;
  Readiness? readiness;

  bool checking = false;
  DnsResult? dns;

  /// A catalog key for a DNS problem, or the API's own sentence in [dnsRaw].
  String? dnsProblem;
  String? dnsRaw;

  Future<void> load() async {
    phase = SellingPhase.loading;
    error = null;
    notifyListeners();
    try {
      readiness = await repository.readiness();
      phase = SellingPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? SellingPhase.denied : SellingPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = SellingPhase.error;
      error = readableError(e);
    }
    notifyListeners();
  }

  Future<void> check(String text) async {
    final domain = cleanDomain(text);
    dnsRaw = null;
    if (domain == null) {
      dnsProblem = 'selling.dns.badDomain';
      notifyListeners();
      return;
    }
    checking = true;
    dnsProblem = null;
    notifyListeners();
    try {
      dns = await repository.checkDns(domain);
    } on Object catch (e) {
      dnsProblem = 'selling.dns.failed';
      dnsRaw = e is ApiException ? e.readable : readableError(e);
    }
    checking = false;
    notifyListeners();
  }

  /// The DNS problem in the page language.
  String? dnsProblemText(AnumLocalizations l) => dnsProblem == null
      ? null
      : deskText(
          l, dnsProblem!, dnsRaw == null ? const {} : {'message': dnsRaw!});
}
