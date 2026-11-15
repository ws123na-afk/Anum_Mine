import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'vehicles_strings.dart';

/// Fleet costs and the monthly client report on the phone
/// (docs/vehicles.md#fleet): the month's business-vehicle costs per vehicle
/// and per km, the highest cost per km, renewals due, and the report made on
/// the server as a PDF or Excel file in English or Arabic, handed to the
/// system save dialog. Nothing is sent to anyone. The import is on the web.

class FleetMoney {
  const FleetMoney(this.currency, this.total, this.costPerKm, this.previous,
      {this.recorded = '0.00'});
  factory FleetMoney.fromJson(JsonReader j) => FleetMoney(
        j.string('currency'),
        j.string('total'),
        j.optString('cost_per_km'),
        j.optString('previous_total') ?? '0.00',
        recorded: j.optString('recorded') ?? '0.00',
      );
  final String currency, total, previous, recorded;
  final String? costPerKm;
}

class FleetRow {
  const FleetRow(this.vehicleId, this.registration, this.nickname,
      this.distanceKm, this.costs);
  factory FleetRow.fromJson(JsonReader j) => FleetRow(
        j.string('vehicle_id'),
        j.string('registration'),
        j.optString('nickname'),
        j.string('distance_km'),
        j.optObjects('costs', FleetMoney.fromJson),
      );
  final String vehicleId, registration, distanceKm;
  final String? nickname;
  final List<FleetMoney> costs;
}

class FleetRenewal {
  const FleetRenewal(this.registration, this.kind, this.dueOn, this.daysLeft);
  factory FleetRenewal.fromJson(JsonReader j) => FleetRenewal(
      j.string('registration'),
      j.string('kind'),
      j.string('due_on'),
      j.optInt('days_left') ?? 0);
  final String registration, kind, dueOn;
  final int daysLeft;
}

class FleetSummary {
  const FleetSummary({
    required this.month,
    required this.vehicles,
    required this.distanceKm,
    required this.totals,
    required this.rows,
    required this.worst,
    required this.renewals,
    required this.tyresStatus,
  });
  factory FleetSummary.fromJson(JsonReader j) => FleetSummary(
        month: j.string('month'),
        vehicles: j.optInt('vehicles') ?? 0,
        distanceKm: j.string('distance_km'),
        totals: j.optObjects('totals', FleetMoney.fromJson),
        rows: j.optObjects('rows', FleetRow.fromJson),
        worst: j.optObjects(
            'worst',
            (w) => FleetRow(w.string('vehicle_id'), w.string('registration'),
                    w.optString('nickname'), w.string('distance_km'), [
                  FleetMoney(w.string('currency'), w.string('total'),
                      w.optString('cost_per_km'), '0.00')
                ])),
        renewals: j.optObjects('renewals', FleetRenewal.fromJson),
        tyresStatus: j.optObject('tyres')?.optString('status') ?? 'denied',
      );
  final String month, distanceKm, tyresStatus;
  final int vehicles;
  final List<FleetMoney> totals;
  final List<FleetRow> rows, worst;
  final List<FleetRenewal> renewals;
}

/// The two fleet calls; replaced in tests through the fake API client.
class FleetApi {
  const FleetApi(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/vehicles/fleet';

  Future<FleetSummary> costs(String month) async => FleetSummary.fromJson(
      JsonReader(await api.request('GET', '$_base/costs?month=$month')));

  /// True when saved, false when the person cancelled the save dialog.
  Future<bool> report(
          {required String month,
          required String language,
          required String format,
          required String company,
          required String notes,
          required String dialogTitle}) =>
      api.download('POST', '$_base/report',
          body: {
            'month': month,
            'language': language,
            'format': format,
            'company_name': company,
            'notes': notes,
          },
          fileName: 'fleet-report-$month-$language.$format',
          dialogTitle: dialogTitle);
}

String monthOf(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}';

String shiftMonth(String month, int by) {
  final parts = month.split('-').map(int.parse).toList();
  return monthOf(DateTime(parts[0], parts[1] + by));
}

class FleetCostsScreen extends StatefulWidget {
  const FleetCostsScreen({required this.fleet, this.now, super.key});
  final FleetApi fleet;
  final DateTime? now;

  @override
  State<FleetCostsScreen> createState() => _FleetCostsScreenState();
}

class _FleetCostsScreenState extends State<FleetCostsScreen> {
  late final String _thisMonth = monthOf(widget.now ?? DateTime.now());
  late String _month = _thisMonth;
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  FleetSummary? _data;
  final _company = TextEditingController();
  final _notes = TextEditingController();
  String _language = 'en';
  String? _busy;
  String? _reportError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _company.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final data = await widget.fleet.costs(_month);
      if (!mounted) return;
      setState(() {
        _data = data;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  void _go(int by) {
    _month = shiftMonth(_month, by);
    _load();
  }

  Future<void> _report(String format) async {
    final s = VehiclesStrings.of(context);
    if (_company.text.trim().isEmpty) {
      setState(() => _reportError = s.t('fleet.companyRequired'));
      return;
    }
    setState(() {
      _busy = format;
      _reportError = null;
    });
    try {
      final saved = await widget.fleet.report(
          month: _month,
          language: _language,
          format: format,
          company: _company.text.trim(),
          notes: _notes.text,
          dialogTitle: s.t('fleet.report'));
      if (mounted) {
        recordsToast(context, s.t(saved ? 'fleet.saved' : 'fleet.notSaved'));
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() => _reportError = s.t('fleet.reportFailed',
            {'message': RecordsFailure.from(error).message}));
      }
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  String _money(String value, String currency) =>
      formatMoneyText(value, currency, locale: Localizations.localeOf(context));

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    final data = _data;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('fleet.title'))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(AnumSpacing.md),
          children: [
            Row(children: [
              IconButton(
                key: const Key('fleet-previous'),
                tooltip: s.t('fleet.previous'),
                onPressed: () => _go(-1),
                icon: const Icon(Icons.chevron_left),
              ),
              Expanded(
                  child: Text(_month,
                      textAlign: TextAlign.center,
                      style: Theme.of(context).textTheme.titleMedium)),
              IconButton(
                key: const Key('fleet-next'),
                tooltip: s.t('fleet.next'),
                onPressed: _month == _thisMonth ? null : () => _go(1),
                icon: const Icon(Icons.chevron_right),
              ),
            ]),
            if (_phase != RecordsPhase.ready || data == null)
              RecordsStateView(
                  phase: _phase,
                  failure: _failure,
                  deniedHint: s.t('denied'),
                  onRetry: _load)
            else if (data.vehicles == 0)
              Padding(
                padding: const EdgeInsets.all(AnumSpacing.lg),
                child: Text(s.t('fleet.empty'),
                    key: const Key('fleet-empty'), textAlign: TextAlign.center),
              )
            else ...[
              RecordsSection(title: s.t('fleet.summary'), children: [
                Text(s.t('fleet.vehiclesKm', {
                  'vehicles': data.vehicles,
                  'km': data.distanceKm.split('.').first
                })),
                for (final total in data.totals)
                  Text(
                      s.t('fleet.total', {
                        'amount': _money(total.total, total.currency),
                        'perKm': total.costPerKm ?? '-',
                        'before': _money(total.previous, total.currency),
                      }),
                      key: Key('fleet-total-${total.currency}')),
                Text(s.t('fleet.basis'),
                    style: Theme.of(context).textTheme.bodySmall),
              ]),
              const SizedBox(height: AnumSpacing.sm),
              RecordsSection(title: s.t('fleet.perVehicle'), children: [
                for (final row in data.rows)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                        [row.registration, row.nickname]
                            .whereType<String>()
                            .join(' · '),
                        textDirection: TextDirection.ltr),
                    subtitle: Text([
                      s.t('km', {'km': row.distanceKm.split('.').first}),
                      for (final cost in row.costs)
                        '${_money(cost.total, cost.currency)} (${s.t('fleet.perKm', {
                              'value': cost.costPerKm ?? '-'
                            })})',
                    ].join(' · ')),
                  ),
              ]),
              const SizedBox(height: AnumSpacing.sm),
              RecordsSection(title: s.t('fleet.worst'), children: [
                if (data.worst.isEmpty) Text(s.t('fleet.worstNone')),
                for (final row in data.worst)
                  Text('${row.registration} · ${s.t('fleet.perKm', {
                        'value': row.costs.first.costPerKm ?? '-'
                      })}'),
              ]),
              const SizedBox(height: AnumSpacing.sm),
              RecordsSection(title: s.t('fleet.renewals'), children: [
                if (data.renewals.isEmpty) Text(s.t('fleet.renewalsNone')),
                for (final r in data.renewals)
                  Text(
                      '${s.label('fleet.renewal', r.kind)} · ${r.registration} · ${r.dueOn}${r.daysLeft < 0 ? ' · ${s.t('fleet.overdue')}' : ''}'),
              ]),
              const SizedBox(height: AnumSpacing.sm),
              _reportSection(s),
            ],
          ],
        ),
      ),
    );
  }

  Widget _reportSection(VehiclesStrings s) => RecordsSection(
        title: s.t('fleet.report'),
        children: [
          TextField(
            key: const Key('fleet-company'),
            controller: _company,
            maxLength: 120,
            decoration: InputDecoration(
                labelText: s.t('fleet.company'), errorText: _reportError),
          ),
          SegmentedButton<String>(
            segments: [
              ButtonSegment(value: 'en', label: Text(s.t('fleet.english'))),
              ButtonSegment(value: 'ar', label: Text(s.t('fleet.arabic'))),
            ],
            selected: {_language},
            onSelectionChanged: (value) =>
                setState(() => _language = value.first),
          ),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('fleet-notes'),
            controller: _notes,
            maxLength: 4000,
            minLines: 2,
            maxLines: 6,
            decoration: InputDecoration(labelText: s.t('fleet.notes')),
          ),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
            FilledButton.icon(
              key: const Key('fleet-pdf'),
              onPressed: _busy == null ? () => _report('pdf') : null,
              icon: const Icon(Icons.picture_as_pdf_outlined),
              label: Text(s.t('fleet.pdf')),
            ),
            OutlinedButton.icon(
              key: const Key('fleet-xlsx'),
              onPressed: _busy == null ? () => _report('xlsx') : null,
              icon: const Icon(Icons.table_chart_outlined),
              label: Text(s.t('fleet.xlsx')),
            ),
          ]),
          const SizedBox(height: AnumSpacing.xs),
          Text(s.t('fleet.never'),
              style: Theme.of(context).textTheme.bodySmall),
        ],
      );
}
