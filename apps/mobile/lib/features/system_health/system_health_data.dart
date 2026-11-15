import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';

/// System health on the phone (docs/system-health.md#on-the-phone): a summary
/// of `GET /api/v1/platform/health` for the platform owner. Each check's
/// state, one line from its numbers and what to do; the full detail is on the
/// web's platform console. Read-only; platform operators only (everyone else
/// sees who can).

const healthKeys = [
  'backup_local',
  'backup_offsite',
  'restore_drill',
  'ai_model',
  'disk',
  'memory',
  'email',
  'scheduler',
  'errors',
  'restarts',
  'notices',
];

class HealthItem {
  const HealthItem(
      {required this.key, required this.state, this.detail = const {}});
  final String key, state;
  final Map<String, Object?> detail;

  factory HealthItem.fromJson(JsonReader j) => HealthItem(
        key: j.string('key'),
        state: switch (j.optString('state')) {
          'green' => 'green',
          'amber' => 'amber',
          'red' => 'red',
          _ => 'unknown',
        },
        detail: j.optMap('detail') ?? const {},
      );

  bool get green => state == 'green';
}

class SystemHealth {
  const SystemHealth(
      {required this.items,
      required this.overall,
      this.checkedAt,
      this.startedAt});
  final List<HealthItem> items;
  final String overall;
  final DateTime? checkedAt, startedAt;

  factory SystemHealth.fromJson(JsonReader j) => SystemHealth(
        items: j
            .objects('items', HealthItem.fromJson)
            .where((i) => healthKeys.contains(i.key))
            .toList(),
        overall: j.optString('overall') ?? 'amber',
        checkedAt: j.optDate('checked_at'),
        startedAt: j.optDate('started_at'),
      );

  /// Red first, then amber, then not known, green last; the server's order
  /// inside each state.
  List<HealthItem> get sorted {
    const rank = {'red': 0, 'amber': 1, 'unknown': 2, 'green': 3};
    final indexed = [for (var i = 0; i < items.length; i++) (i, items[i])];
    indexed.sort((a, b) {
      final r = rank[a.$2.state]!.compareTo(rank[b.$2.state]!);
      return r != 0 ? r : a.$1.compareTo(b.$1);
    });
    return [for (final x in indexed) x.$2];
  }

  /// Checks that need the owner (red or amber).
  int get attention =>
      items.where((i) => i.state == 'red' || i.state == 'amber').length;
}

/// The one line under a check, in the page language.
String healthLine(
    AnumLocalizations l, HealthItem item, String Function(DateTime) when) {
  final d = item.detail;
  String date(Object? v) {
    final parsed = v is String ? DateTime.tryParse(v) : null;
    return parsed == null ? '-' : when(parsed);
  }

  String pct(Object? v) =>
      v is num ? (v == v.roundToDouble() ? '${v.toInt()}' : '$v') : '-';

  switch (item.key) {
    case 'backup_local' || 'backup_offsite' || 'restore_drill':
      if (d['recorded'] == false) return l.t('sh.backup.notRecorded');
      if (d['ever'] != true) return l.t('sh.backup.never');
      return l.f('sh.backup.last', {'when': date(d['last_success_at'])});
    case 'ai_model':
      if (d['placeholder'] == true) return l.t('sh.model.placeholder');
      return l.f(
          d['key_missing'] == true ? 'sh.model.keyMissing' : 'sh.model.real',
          {'provider': '${d['provider'] ?? '-'}'});
    case 'disk':
      return d['readable'] == false
          ? l.t('sh.unreadable')
          : l.f('sh.free', {'pct': pct(d['free_pct'])});
    case 'memory':
      final values = [d['available_pct'], d['api_free_pct']].whereType<num>();
      return values.isEmpty
          ? l.t('sh.unreadable')
          : l.f(
              'sh.free', {'pct': pct(values.reduce((a, b) => a < b ? a : b))});
    case 'email':
      return l.t(d['configured'] == true ? 'sh.email.on' : 'sh.email.off');
    case 'scheduler':
      if (d['enabled'] != true) return l.t('sh.scheduler.off');
      if (d['starting'] == true) return l.t('sh.scheduler.starting');
      return d['last_pass_at'] is String
          ? l.f('sh.scheduler.last', {'when': date(d['last_pass_at'])})
          : l.t('sh.scheduler.none');
    case 'errors':
      return l
          .f('sh.errors', {'count': (d['count_24h'] as num?)?.toInt() ?? 0});
    case 'restarts':
      if (d['configured'] == false) return l.t('sh.restarts.notTracked');
      return d['last_unexpected_at'] is String
          ? l.f('sh.restarts.last', {'when': date(d['last_unexpected_at'])})
          : l.t('sh.restarts.none');
    case 'notices':
      return l.t(d['configured'] == true ? 'sh.notices.on' : 'sh.notices.off');
  }
  return '-';
}

abstract interface class SystemHealthRepository {
  Future<SystemHealth> read();
}

class ApiSystemHealthRepository implements SystemHealthRepository {
  const ApiSystemHealthRepository(this.api);
  final AnumApiClient api;

  @override
  Future<SystemHealth> read() async => SystemHealth.fromJson(
      JsonReader(await api.request('GET', '/api/v1/platform/health')));
}

enum HealthPhase { loading, ready, denied, error }

class SystemHealthController extends ChangeNotifier {
  SystemHealthController(this.repository);
  final SystemHealthRepository repository;

  HealthPhase phase = HealthPhase.loading;
  String? error;
  SystemHealth? health;

  Future<void> load() async {
    phase = HealthPhase.loading;
    error = null;
    notifyListeners();
    try {
      health = await repository.read();
      phase = HealthPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? HealthPhase.denied : HealthPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = HealthPhase.error;
      error = readableError(e);
    }
    notifyListeners();
  }
}
