import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/payment_picker.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'vehicles_controller.dart';
import 'vehicles_data.dart';
import 'vehicles_strings.dart';

/// Vehicles on the phone (docs/vehicles.md#mobile): the list with what is
/// due, each vehicle's fuel, trips and service, adding records, odometer
/// readings and corrections, and linking a Finance payment to a cost. From
/// 840 px the list and the selected vehicle sit side by side.
class VehiclesScreen extends StatefulWidget {
  const VehiclesScreen({required this.controller, super.key});
  final VehiclesController controller;

  @override
  State<VehiclesScreen> createState() => _VehiclesScreenState();
}

class _VehiclesScreenState extends State<VehiclesScreen> {
  String? _selected;

  VehiclesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.vehicles.phase == RecordsPhase.loading) c.load();
  }

  Future<void> _add() async {
    final s = VehiclesStrings.of(context);
    Vehicle? created;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('add'),
        submitLabel: s.t('add'),
        fields: [
          RecordsInput.text('registration', s.t('registration'),
              required: true, maxLength: 20),
          RecordsInput.text('nickname', s.t('nickname'), maxLength: 60),
          RecordsInput.choice('ownership', s.t('ownership'),
              initial: 'personal',
              options: {
                'personal': s.t('personal'),
                'business': s.t('business')
              }),
          RecordsInput.choice('country', s.t('country'),
              initial: 'AE',
              options: {
                for (final code in ['AE', 'SA']) code: s.t('country.$code')
              }),
          RecordsInput.choice('vehicle_type', s.t('vehicleType'),
              initial: 'car',
              options: {
                for (final type in vehicleTypes) type: s.label('type', type)
              }),
          RecordsInput.choice('fuel_type', s.t('fuelType'),
              initial: 'petrol_95',
              options: {
                for (final fuel in fuelTypes) fuel: s.label('fuel', fuel)
              }),
          RecordsInput.text('make', s.t('make'), maxLength: 60),
          RecordsInput.text('model', s.t('model'), maxLength: 60),
          RecordsInput.number('odometer_km', s.t('startingKm')),
        ],
        onSubmit: (values) async {
          created = await c.create({
            for (final entry in values.entries)
              if (entry.value != null) entry.key: entry.value,
          });
        },
      ),
    );
    if (!mounted || !saved || created == null) return;
    recordsToast(context, s.t('saved'));
    _open(created!);
  }

  void _open(Vehicle vehicle) {
    if (recordsWide(context)) {
      setState(() => _selected = vehicle.id);
      return;
    }
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => VehicleDetailScreen(
          repository: c.repository, vehicleId: vehicle.id, initial: vehicle),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    return ListenableBuilder(
      listenable: Listenable.merge([c, c.vehicles]),
      builder: (context, _) {
        final list = c.vehicles;
        final canAdd = list.phase != RecordsPhase.denied;
        return Scaffold(
          appBar: AppBar(title: Text(s.t('title'))),
          floatingActionButton: canAdd
              ? FloatingActionButton.extended(
                  key: const Key('vehicles-add'),
                  onPressed: _add,
                  icon: const Icon(Icons.add),
                  label: Text(s.t('add')),
                )
              : null,
          body: LayoutBuilder(builder: (context, box) {
            final page = _ListPage(
              controller: c,
              selected: _selected,
              onOpen: _open,
              onAdd: _add,
            );
            if (box.maxWidth < AnumBreakpoints.expanded) return page;
            final selected = _selected;
            return Row(children: [
              SizedBox(width: 420, child: page),
              const VerticalDivider(width: 1),
              Expanded(
                child: selected == null
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(AnumSpacing.lg),
                          child: Text(s.t('subtitle'),
                              textAlign: TextAlign.center),
                        ),
                      )
                    : VehicleDetailHost(
                        key: ValueKey(selected),
                        repository: c.repository,
                        vehicleId: selected,
                        initial: list.items
                            .where((v) => v.id == selected)
                            .firstOrNull,
                      ),
              ),
            ]);
          }),
        );
      },
    );
  }
}

const vehicleTypes = [
  'car', 'suv', 'pickup', 'van', 'truck', 'motorcycle', 'bus', 'other' //
];
const fuelTypes = [
  'petrol_91', 'petrol_95', 'petrol_98', 'diesel', 'electric', 'hybrid', //
  'lpg', 'other',
];
const serviceKinds = [
  'maintenance', 'oil', 'tyres', 'battery', 'repair', 'inspection', //
  'insurance', 'registration', 'fine', 'toll', 'parking', 'warranty', 'wash',
  'other',
];

class _ListPage extends StatelessWidget {
  const _ListPage({
    required this.controller,
    required this.selected,
    required this.onOpen,
    required this.onAdd,
  });
  final VehiclesController controller;
  final String? selected;
  final ValueChanged<Vehicle> onOpen;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    final k = KitStrings.of(context);
    final list = controller.vehicles;
    final children = <Widget>[
      AnumHeader(title: s.t('title'), subtitle: s.t('subtitle')),
    ];
    if (list.phase == RecordsPhase.denied ||
        (list.phase != RecordsPhase.ready && !list.narrowed)) {
      children.add(RecordsStateView(
        phase: list.phase,
        failure: list.failure,
        deniedHint: s.t('denied'),
        onRetry: controller.load,
      ));
      return AnumPage(onRefresh: controller.load, children: children);
    }
    children.addAll([
      RecordsSearchField(
        initial: list.query,
        hint: s.t('search'),
        onSearch: (q) => list.load(query: q),
      ),
      RecordsFilterChips(
        options: {'': s.t('active'), 'archived': s.t('archived')},
        selected: list.filter,
        onSelected: (f) => list.load(filter: f),
      ),
    ]);
    if (list.filter.isEmpty && list.query.isEmpty) {
      children.add(_DueCard(controller: controller));
    }
    if (list.phase != RecordsPhase.ready) {
      children.add(RecordsStateView(
          phase: list.phase, failure: list.failure, onRetry: list.load));
    } else if (list.items.isEmpty) {
      children.add(list.narrowed
          ? AnumEmpty(
              title: k.t('noMatch'),
              message: k.t('noMatchText'),
              icon: Icons.search_off,
              action: k.t('clearFilters'),
              onAction: () => list.load(query: '', filter: ''),
            )
          : AnumEmpty(
              key: const Key('vehicles-empty'),
              title: s.t('empty'),
              message: s.t('emptyText'),
              icon: Icons.directions_car_outlined,
              action: s.t('add'),
              onAction: onAdd,
            ));
    } else {
      children.add(AnumSurface(
        child: Column(children: [
          for (final vehicle in list.items)
            _VehicleRow(
              vehicle: vehicle,
              selected: vehicle.id == selected,
              onTap: () => onOpen(vehicle),
            ),
        ]),
      ));
      children.add(RecordsMore(list: list));
    }
    return AnumPage(onRefresh: controller.load, children: children);
  }
}

class _VehicleRow extends StatelessWidget {
  const _VehicleRow(
      {required this.vehicle, required this.selected, required this.onTap});
  final Vehicle vehicle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    final overdue = vehicle.due.where((d) => d.overdue).length;
    return Semantics(
      selected: selected,
      button: true,
      child: AnumRow(
        key: Key('vehicle-${vehicle.id}'),
        icon: Icons.directions_car_outlined,
        tone: overdue > 0
            ? AnumTone.stop
            : vehicle.due.isNotEmpty
                ? AnumTone.warn
                : null,
        title: vehicle.title,
        subtitle: [
          vehicle.makeModel,
          s.t(vehicle.ownership == 'business' ? 'business' : 'personal'),
          if (vehicle.odometerKm != null)
            s.t('km', {'km': vehicle.odometerKm!}),
        ].where((part) => part.isNotEmpty).join(' · '),
        detail: vehicle.due.isEmpty
            ? null
            : vehicle.due.map((due) => dueLabel(context, due)).join(' · '),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}

/// "Insurance in 12 days", "Service 300 km overdue".
String dueLabel(BuildContext context, VehicleDue due) {
  final s = VehiclesStrings.of(context);
  final what = due.kind == 'service' && due.serviceKind != null
      ? s.label('service', due.serviceKind!)
      : s.label('due', due.kind);
  String? when;
  if (due.daysLeft != null) {
    final days = due.daysLeft!;
    when = days == 0
        ? s.t('dueToday')
        : days < 0
            ? s.t('dueOverdue', {'days': -days})
            : s.t('dueIn', {'days': days});
  } else if (due.kmLeft != null) {
    final km = due.kmLeft!;
    when = km.startsWith('-')
        ? s.t('dueKmOver', {'km': km.substring(1)})
        : s.t('dueKm', {'km': km});
  }
  return when == null ? what : '$what $when';
}

class _DueCard extends StatelessWidget {
  const _DueCard({required this.controller});
  final VehiclesController controller;

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    final due = controller.due;
    return RecordsSection(
      title: s.t('reminders'),
      children: [
        if (controller.dueError != null)
          Text(s.t('remindersFailed', {'message': controller.dueError!}),
              style: TextStyle(color: context.palette.stop))
        else if (due.isEmpty)
          Text(s.t('remindersEmpty'),
              style: TextStyle(color: context.palette.muted))
        else
          for (final item in due.take(6))
            AnumRow(
              icon: Icons.event_outlined,
              tone: item.overdue ? AnumTone.stop : AnumTone.warn,
              title: item.registration,
              subtitle: dueLabel(context, item),
              detail: item.dueOn == null ? null : recordsDate(context, item.dueOn),
            ),
      ],
    );
  }
}

/// One vehicle on its own page (phones).
class VehicleDetailScreen extends StatelessWidget {
  const VehicleDetailScreen({
    required this.repository,
    required this.vehicleId,
    this.initial,
    super.key,
  });
  final VehiclesRepository repository;
  final String vehicleId;
  final Vehicle? initial;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
            title: Text(initial?.title ?? VehiclesStrings.of(context).t('title'))),
        body: VehicleDetailHost(
            repository: repository, vehicleId: vehicleId, initial: initial),
      );
}

/// Owns the detail controller of one vehicle for as long as it is shown.
class VehicleDetailHost extends StatefulWidget {
  const VehicleDetailHost({
    required this.repository,
    required this.vehicleId,
    this.initial,
    super.key,
  });
  final VehiclesRepository repository;
  final String vehicleId;
  final Vehicle? initial;

  @override
  State<VehicleDetailHost> createState() => _VehicleDetailHostState();
}

class _VehicleDetailHostState extends State<VehicleDetailHost> {
  late final VehicleDetailController _c = VehicleDetailController(
      widget.repository, widget.vehicleId,
      initial: widget.initial)
    ..load();
  VehicleRecordKind _tab = VehicleRecordKind.fuel;

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  Future<void> _reading({bool correction = false, String? km}) async {
    final s = VehiclesStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t(correction ? 'decreaseAction' : 'addReading'),
        intro: correction ? s.t('correctionHelp') : null,
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.number('reading_km', s.t('reading'),
              required: true, initial: km),
          RecordsInput.toggle('correction', s.t('correction'),
              initial: correction, help: s.t('correctionHelp')),
          RecordsInput.text('reason', s.t('reason'),
              help: s.t('reasonHelp'), maxLength: 300),
        ],
        onSubmit: (values) => _c.addReading(values['reading_km']! as String,
            correction: values['correction'] == true,
            reason: values['reason'] as String?),
        onError: (error, values) => isOdometerDecrease(error) &&
                values['correction'] != true
            ? _CorrectionAction(onPressed: () {
                Navigator.of(context).pop(false);
                _reading(
                    correction: true, km: values['reading_km'] as String?);
              })
            : null,
      ),
    );
    if (saved && mounted) recordsToast(context, s.t('saved'));
  }

  /// The odometer refusal from a fuel entry or trip offers a correction
  /// with the kilometres the person typed.
  Widget? _decrease(Object error, RecordsValues values, String field) =>
      isOdometerDecrease(error)
          ? _CorrectionAction(onPressed: () {
              Navigator.of(context).pop(false);
              _reading(correction: true, km: values[field] as String?);
            })
          : null;

  Future<void> _addRecord(VehicleRecordKind kind) async {
    final s = VehiclesStrings.of(context);
    final vehicle = _c.vehicle;
    final today = isoDay(DateTime.now());
    // One key for every attempt in this form: a retry is stored once.
    final key = newIdempotencyKey('veh');
    final RecordsForm form;
    switch (kind) {
      case VehicleRecordKind.fuel:
        form = RecordsForm(
          title: s.t('addFuel'),
          submitLabel: s.t('addFuel'),
          fields: [
            RecordsInput.date('day', s.t('filledAt'),
                required: true, initial: today),
            RecordsInput.number('litres', s.t('litres'), required: true),
            RecordsInput.number('total', s.t('total')),
            RecordsInput.number('price_per_litre', s.t('pricePerLitre')),
            RecordsInput.number('odometer_km', s.t('odometerKm')),
            RecordsInput.choice('fuel_type', s.t('fuelType'),
                initial: vehicle?.fuelType,
                options: {for (final f in fuelTypes) f: s.label('fuel', f)}),
            RecordsInput.text('station', s.t('station'), maxLength: 120),
            RecordsInput.toggle('full_tank', s.t('fullTank'), initial: true),
          ],
          onSubmit: (v) => _c.add(
              kind,
              {
                'filled_at': instantOf(v['day']! as String, null),
                'litres': v['litres'],
                if (v['total'] != null) 'total': v['total'],
                if (v['price_per_litre'] != null)
                  'price_per_litre': v['price_per_litre'],
                if (v['odometer_km'] != null) 'odometer_km': v['odometer_km'],
                if (v['fuel_type'] != null) 'fuel_type': v['fuel_type'],
                if (v['station'] != null) 'station': v['station'],
                'full_tank': v['full_tank'],
              },
              idempotencyKey: key),
          onError: (e, v) => _decrease(e, v, 'odometer_km'),
        );
      case VehicleRecordKind.trips:
        final now = TimeOfDay.now();
        String hhmm(TimeOfDay t) =>
            '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
        form = RecordsForm(
          title: s.t('addTrip'),
          submitLabel: s.t('addTrip'),
          fields: [
            RecordsInput.date('day', s.t('filledAt'),
                required: true, initial: today),
            RecordsInput.time('start_time', s.t('startTime'),
                required: true, initial: hhmm(now)),
            RecordsInput.number('start_odometer_km', s.t('startKm')),
            RecordsInput.time('end_time', s.t('endTime'),
                initial: hhmm(now)),
            RecordsInput.number('end_odometer_km', s.t('endKm')),
            RecordsInput.choice('purpose', s.t('purpose'),
                initial: 'business',
                options: {
                  for (final p in ['business', 'personal', 'commute'])
                    p: s.label('purpose', p)
                }),
            RecordsInput.text('notes', s.t('notes'), maxLength: 500),
          ],
          onSubmit: (v) {
            final day = v['day']! as String;
            final ended = v['end_time'] as String?;
            return _c.add(
                kind,
                {
                  'started_at': instantOf(day, v['start_time'] as String?),
                  if (ended != null) 'ended_at': instantOf(day, ended),
                  if (v['start_odometer_km'] != null)
                    'start_odometer_km': v['start_odometer_km'],
                  if (v['end_odometer_km'] != null)
                    'end_odometer_km': v['end_odometer_km'],
                  'purpose': v['purpose'],
                  if (v['notes'] != null) 'notes': v['notes'],
                },
                idempotencyKey: key);
          },
          onError: (e, v) => _decrease(e, v,
              v['end_odometer_km'] != null ? 'end_odometer_km' : 'start_odometer_km'),
        );
      case VehicleRecordKind.service:
        form = RecordsForm(
          title: s.t('addService'),
          submitLabel: s.t('addService'),
          fields: [
            RecordsInput.choice('kind', s.t('serviceKind'),
                initial: 'maintenance',
                options: {
                  for (final k in serviceKinds) k: s.label('service', k)
                }),
            RecordsInput.date('service_on', s.t('serviceOn'),
                required: true, initial: today),
            RecordsInput.number('odometer_km', s.t('odometerKm')),
            RecordsInput.text('workshop', s.t('workshop'), maxLength: 120),
            RecordsInput.multiline('description', s.t('description'),
                maxLength: 500),
            RecordsInput.number('receipt_total', s.t('receiptTotal')),
            RecordsInput.date('next_due_on', s.t('nextDueOn')),
            RecordsInput.number('next_due_km', s.t('nextDueKm')),
          ],
          onSubmit: (v) => _c.add(
              kind,
              {
                for (final entry in v.entries)
                  if (entry.value != null) entry.key: entry.value,
              },
              idempotencyKey: key),
          onError: (e, v) => _decrease(e, v, 'odometer_km'),
        );
    }
    final saved = await openRecordsForm(context, form);
    if (saved && mounted) recordsToast(context, s.t('saved'));
  }

  Future<void> _link(VehicleRecordKind kind, VehicleRecord record) async {
    final s = VehiclesStrings.of(context);
    final around = switch (record) {
      VehicleFuel(:final filledAt) => filledAt.substring(0, 10),
      VehicleService(:final serviceOn) => serviceOn,
      _ => null,
    };
    final linked = await showRecordsSheet<bool>(
      context,
      (_) => PaymentPickerSheet(
        text: PaymentPickerText(
          title: s.t('linkTitle'),
          help: s.t('linkHelp'),
          search: s.t('linkSearch'),
          empty: s.t('linkEmpty'),
          remaining: s.t('linkRemaining'),
          already: s.t('linkAlready'),
        ),
        load: (query) async => [
          for (final c in await _c.repository
              .paymentCandidates(query: query, around: around))
            PaymentChoice(
              id: c.canonicalId,
              postedOn: c.postedOn,
              merchant: c.merchant,
              amount: c.amount,
              currency: c.currency,
              remaining: c.remaining,
              alreadyLinked: c.linkedToVehicle,
            ),
        ],
        link: (choice, key) => _c.repository.linkPayment(
            kind.costKind!, record.id,
            canonicalId: choice.id, idempotencyKey: key),
      ),
    );
    if (linked ?? false) {
      await _c.linked(kind);
      if (mounted) recordsToast(context, s.t('linked'));
    }
  }

  Future<void> _delete(VehicleRecordKind kind, VehicleRecord record) async {
    final s = VehiclesStrings.of(context);
    final k = KitStrings.of(context);
    final sure = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        content: Text(s.t('deleteConfirm')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(k.t('cancel'))),
          FilledButton(
              key: const Key('confirm-delete'),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(k.t('delete'))),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    try {
      await _c.delete(kind, record.id);
      if (mounted) recordsToast(context, s.t('deleted'));
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([_c, ..._c.lists.values]),
        builder: (context, _) {
          final s = VehiclesStrings.of(context);
          final vehicle = _c.vehicle;
          if (_c.phase != RecordsPhase.ready || vehicle == null) {
            return AnumPage(children: [
              RecordsStateView(
                phase: _c.phase,
                failure: _c.failure,
                deniedHint: s.t('denied'),
                onRetry: _c.load,
              ),
            ]);
          }
          final list = _c.lists[_tab]!;
          return AnumPage(
            onRefresh: _c.load,
            children: [
              _Summary(vehicle: vehicle, onReading: _reading),
              SegmentedButton<VehicleRecordKind>(
                segments: [
                  for (final kind in VehicleRecordKind.values)
                    ButtonSegment(
                        value: kind, label: Text(s.t('tab.${kind.path}'))),
                ],
                selected: {_tab},
                onSelectionChanged: (value) =>
                    setState(() => _tab = value.first),
              ),
              if (vehicle.canEdit)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: FilledButton.tonalIcon(
                    key: Key('vehicle-add-${_tab.path}'),
                    onPressed: () => _addRecord(_tab),
                    icon: const Icon(Icons.add),
                    label: Text(s.t(switch (_tab) {
                      VehicleRecordKind.fuel => 'addFuel',
                      VehicleRecordKind.trips => 'addTrip',
                      VehicleRecordKind.service => 'addService',
                    })),
                  ),
                ),
              if (list.phase != RecordsPhase.ready)
                RecordsStateView(
                    phase: list.phase,
                    failure: list.failure,
                    deniedHint: s.t('denied'),
                    onRetry: list.load)
              else if (list.items.isEmpty)
                AnumEmpty(
                  title: s.t('${_tab.path}Empty'),
                  message: s.t('${_tab.path}EmptyText'),
                  icon: Icons.receipt_long_outlined,
                  action: vehicle.canEdit
                      ? s.t(switch (_tab) {
                          VehicleRecordKind.fuel => 'addFuel',
                          VehicleRecordKind.trips => 'addTrip',
                          VehicleRecordKind.service => 'addService',
                        })
                      : null,
                  onAction: vehicle.canEdit ? () => _addRecord(_tab) : null,
                )
              else ...[
                AnumSurface(
                  child: Column(children: [
                    for (final record in list.items)
                      _RecordRow(
                        record: record,
                        onLink: _tab.costKind != null && record.canEdit
                            ? () => _link(_tab, record)
                            : null,
                        onDelete: record.canEdit
                            ? () => _delete(_tab, record)
                            : null,
                      ),
                  ]),
                ),
                RecordsMore(list: list),
              ],
            ],
          );
        },
      );
}

class _CorrectionAction extends StatelessWidget {
  const _CorrectionAction({required this.onPressed});
  final VoidCallback onPressed;
  @override
  Widget build(BuildContext context) => Align(
        alignment: AlignmentDirectional.centerStart,
        child: OutlinedButton.icon(
          key: const Key('odometer-correction'),
          onPressed: onPressed,
          icon: const Icon(Icons.build_outlined),
          label: Text(VehiclesStrings.of(context).t('decreaseAction')),
        ),
      );
}

class _Summary extends StatelessWidget {
  const _Summary({required this.vehicle, required this.onReading});
  final Vehicle vehicle;
  final Future<void> Function({bool correction, String? km}) onReading;

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    return RecordsSection(
      title: vehicle.title,
      children: [
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          AnumPill(
              label: s.t(
                  vehicle.ownership == 'business' ? 'business' : 'personal')),
          AnumPill(label: s.label('type', vehicle.vehicleType)),
          AnumPill(label: s.label('fuel', vehicle.fuelType)),
          if (vehicle.mine) AnumPill(label: s.t('mine'), tone: AnumTone.info),
          for (final due in vehicle.due)
            AnumPill(
              label: dueLabel(context, due),
              tone: due.overdue ? AnumTone.stop : AnumTone.warn,
              icon: Icons.notifications_active_outlined,
            ),
        ]),
        const SizedBox(height: AnumSpacing.xs),
        RecordsField(s.t('odometer'),
            vehicle.odometerKm == null ? null : s.t('km', {'km': vehicle.odometerKm!})),
        if (vehicle.makeModel.isNotEmpty) RecordsField(s.t('make'), vehicle.makeModel),
        RecordsField(s.t('country'), s.label('country', vehicle.country)),
        RecordsField(s.t('vin'), vehicle.vin),
        RecordsField(
            s.t('insurance'), recordsDate(context, vehicle.insuranceExpiresOn)),
        RecordsField(s.t('registrationExpires'),
            recordsDate(context, vehicle.registrationExpiresOn)),
        RecordsField(
            s.t('inspection'), recordsDate(context, vehicle.inspectionExpiresOn)),
        if (!vehicle.canEdit)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Text(s.t('readOnly'),
                style: TextStyle(color: context.palette.muted)),
          )
        else ...[
          const SizedBox(height: AnumSpacing.xs),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('vehicle-reading'),
              onPressed: () => onReading(),
              icon: const Icon(Icons.speed),
              label: Text(s.t('addReading')),
            ),
          ),
        ],
      ],
    );
  }
}

class _RecordRow extends StatelessWidget {
  const _RecordRow({required this.record, this.onLink, this.onDelete});
  final VehicleRecord record;
  final VoidCallback? onLink, onDelete;

  String _payment(BuildContext context, VehiclePayment? payment) {
    final s = VehiclesStrings.of(context);
    if (payment == null) return s.t('noPayment');
    if (payment.status == 'needs_review') return s.t('paymentReview');
    final amount = formatMoneyText(payment.amount, payment.currency,
        locale: Localizations.localeOf(context));
    return s.t(payment.recordedAsCash ? 'paymentCash' : 'paymentLinked',
        {'amount': amount});
  }

  @override
  Widget build(BuildContext context) {
    final s = VehiclesStrings.of(context);
    final locale = Localizations.localeOf(context);
    final (String title, String subtitle, String? detail, VehiclePayment? pay,
        bool payable) = switch (record) {
      VehicleFuel f => (
          s.t('litresValue', {'litres': f.litres}),
          [
            recordsDate(context, f.filledAt),
            s.label('fuel', f.fuelType),
            if (f.total != null) formatMoneyText(f.total!, f.currency, locale: locale),
            if (f.odometerKm != null) s.t('km', {'km': f.odometerKm!}),
            if (f.station != null) f.station!,
          ].join(' · '),
          _payment(context, f.payment),
          f.payment,
          true,
        ),
      VehicleTrip t => (
          t.distanceKm == null
              ? s.label('purpose', t.purpose)
              : s.t('distance', {'km': t.distanceKm!}),
          [
            recordsDateTime(context, t.startedAt),
            s.label('purpose', t.purpose),
            if (t.startOdometerKm != null || t.endOdometerKm != null)
              '${t.startOdometerKm ?? '-'} → ${t.endOdometerKm ?? '-'}',
          ].join(' · '),
          t.status == 'open' ? s.t('tripOpen') : null,
          null,
          false,
        ),
      VehicleService v => (
          s.label('service', v.kind),
          [
            recordsDate(context, v.serviceOn),
            if (v.workshop != null) v.workshop!,
            if (v.receiptTotal != null)
              formatMoneyText(v.receiptTotal!, v.currency, locale: locale),
            if (v.odometerKm != null) s.t('km', {'km': v.odometerKm!}),
            if (v.nextDueOn != null)
              '${s.t('nextDueOn')} ${recordsDate(context, v.nextDueOn)}',
          ].join(' · '),
          _payment(context, v.payment),
          v.payment,
          true,
        ),
    };
    return AnumRow(
      key: Key('record-${record.id}'),
      icon: switch (record) {
        VehicleFuel() => Icons.local_gas_station_outlined,
        VehicleTrip() => Icons.route_outlined,
        VehicleService() => Icons.build_outlined,
      },
      tone: pay?.status == 'needs_review' ? AnumTone.warn : null,
      title: title,
      subtitle: subtitle,
      detail: detail,
      trailing: (onLink == null || !payable || pay != null) && onDelete == null
          ? null
          : PopupMenuButton<String>(
              key: Key('record-menu-${record.id}'),
              tooltip: s.t('details'),
              onSelected: (value) =>
                  value == 'link' ? onLink?.call() : onDelete?.call(),
              itemBuilder: (_) => [
                if (onLink != null && payable && pay == null)
                  PopupMenuItem(
                      key: const Key('menu-link'),
                      value: 'link',
                      child: Text(s.t('linkPayment'))),
                if (onDelete != null)
                  PopupMenuItem(
                      key: const Key('menu-delete'),
                      value: 'delete',
                      child: Text(s.t('deleteRecord'))),
              ],
            ),
    );
  }
}
