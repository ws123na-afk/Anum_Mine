import '../../data/json_read.dart';

/// Tyre planning shapes from `/api/v1/tyre` (docs/tyre-planning.md).

/// What the caller may do, and whether there is data yet.
class TyreAccess {
  const TyreAccess({
    required this.connected,
    required this.synced,
    required this.canOperate,
    this.syncedAt,
  });
  final bool connected, synced, canOperate;
  final DateTime? syncedAt;

  factory TyreAccess.fromJson(JsonReader json) {
    final sync = json.optObject('sync');
    return TyreAccess(
      connected: json.optMap('connection') != null,
      synced: sync != null,
      canOperate: json.optBool('can_operate') ?? false,
      syncedAt: sync?.optDate('synced_at'),
    );
  }
}

class TyreSummary {
  const TyreSummary({
    required this.vehicles,
    required this.tyresFitted,
    required this.averageTreadMm,
    required this.belowMinimum,
    required this.inWarningBand,
    required this.pressureCompliantPct,
    required this.overdueInspections,
    required this.dueNext30Days,
    required this.dueNext90Days,
    required this.costPer1000Km,
    required this.currency,
    required this.minTreadMm,
  });
  final int vehicles, tyresFitted, belowMinimum, inWarningBand;
  final int overdueInspections, dueNext30Days, dueNext90Days;
  final double? averageTreadMm, pressureCompliantPct, costPer1000Km;
  final String currency;
  final double minTreadMm;

  factory TyreSummary.fromJson(JsonReader json) => TyreSummary(
        vehicles: json.integer('vehicles'),
        tyresFitted: json.integer('tyres_fitted'),
        averageTreadMm: json.optNumber('average_tread_mm'),
        belowMinimum: json.integer('below_minimum'),
        inWarningBand: json.integer('in_warning_band'),
        pressureCompliantPct: json.optNumber('pressure_compliant_pct'),
        overdueInspections: json.integer('overdue_inspections'),
        dueNext30Days: json.integer('due_next_30_days'),
        dueNext90Days: json.integer('due_next_90_days'),
        costPer1000Km: json.optNumber('cost_per_1000_km'),
        currency: json.optString('currency') ?? '',
        minTreadMm: json.optNumber('min_tread_mm') ?? 3,
      );
}

class TyreForecast {
  const TyreForecast({
    required this.tyreId,
    required this.label,
    required this.where,
    required this.state,
    required this.confidence,
    this.size,
    this.treadMm,
    this.wearPer30Days,
    this.projectedRemoval,
  });
  final String tyreId, label, where, state, confidence;
  final String? size;
  final double? treadMm, wearPer30Days;
  final DateTime? projectedRemoval;
  bool get due => state == 'due';

  factory TyreForecast.fromJson(JsonReader json) {
    final where = [
      json.optString('vehicle_label') ?? json.optString('vehicle_id'),
      json.optString('position'),
    ].whereType<String>().join(' · ');
    final removal = json.optString('projected_removal');
    return TyreForecast(
      tyreId: json.string('tyre_id'),
      label: json.optString('serial') ?? json.string('tyre_id'),
      where: where,
      state: json.string('state'),
      confidence: json.string('confidence'),
      size: json.optString('size'),
      treadMm: json.optNumber('current_tread_mm'),
      wearPer30Days: json.optNumber('wear_mm_per_30_days'),
      projectedRemoval: removal == null ? null : DateTime.tryParse(removal),
    );
  }
}

class TyreAlert {
  const TyreAlert({
    required this.id,
    required this.kind,
    required this.severity,
    required this.status,
    required this.subject,
    required this.message,
  });
  final String id, kind, severity, status, subject, message;

  factory TyreAlert.fromJson(JsonReader json) => TyreAlert(
        id: json.string('id'),
        kind: json.string('kind'),
        severity: json.string('severity'),
        status: json.string('status'),
        subject: json.string('subject_label'),
        message: json.string('message'),
      );

  TyreAlert acknowledged() => TyreAlert(
        id: id,
        kind: kind,
        severity: severity,
        status: 'acknowledged',
        subject: subject,
        message: message,
      );
}

class TyreDashboard {
  const TyreDashboard({
    required this.summary,
    required this.syncedAt,
    required this.criticalAlerts,
    required this.openAlerts,
    required this.dueSoon,
  });
  final TyreSummary summary;
  final DateTime syncedAt;
  final int criticalAlerts, openAlerts;
  final List<TyreForecast> dueSoon;

  factory TyreDashboard.fromJson(JsonReader json) {
    final counts = json.optMap('alert_counts') ?? const {};
    int count(String key) => (counts[key] as num?)?.toInt() ?? 0;
    return TyreDashboard(
      summary: TyreSummary.fromJson(json.object('summary')),
      syncedAt: json.date('synced_at'),
      criticalAlerts: count('critical'),
      openAlerts: count('critical') + count('warning') + count('info'),
      dueSoon: json.optObjects('due_soon', TyreForecast.fromJson),
    );
  }
}

class TyrePage<T> {
  const TyrePage({
    required this.items,
    required this.total,
    required this.offset,
  });
  final List<T> items;
  final int total, offset;
  bool get hasMore => offset + items.length < total;
}
