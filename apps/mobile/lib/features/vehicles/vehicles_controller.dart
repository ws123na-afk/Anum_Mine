import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'vehicles_data.dart';

/// The Vehicles list: search, active or archived, paging, and what is due.
class VehiclesController extends ChangeNotifier {
  VehiclesController(this.repository) {
    vehicles = RecordsList<Vehicle>((query, filter, token) => repository.list(
        query: query, archived: filter == 'archived', token: token));
  }
  final VehiclesRepository repository;
  late final RecordsList<Vehicle> vehicles;

  List<VehicleDue> due = const [];
  String? dueError;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    await vehicles.load();
    if (vehicles.phase == RecordsPhase.ready) await loadReminders();
  }

  Future<void> loadReminders() async {
    try {
      due = await repository.reminders();
      dueError = null;
    } on Object catch (error) {
      dueError = RecordsFailure.from(error).message;
    }
    _notify();
  }

  Future<Vehicle> create(Map<String, Object?> body) async {
    final vehicle = await repository.create(body);
    vehicles.upsert(vehicle, (v) => v.id == vehicle.id);
    return vehicle;
  }

  @override
  void dispose() {
    _disposed = true;
    vehicles.dispose();
    super.dispose();
  }
}

/// One vehicle: its details and its fuel, trips and service, each paged.
class VehicleDetailController extends ChangeNotifier {
  VehicleDetailController(this.repository, this.vehicleId, {Vehicle? initial})
      : vehicle = initial {
    for (final kind in VehicleRecordKind.values) {
      lists[kind] = RecordsList<VehicleRecord>(
          (_, __, token) => repository.records(kind, vehicleId, token: token));
    }
  }
  final VehiclesRepository repository;
  final String vehicleId;
  final lists = <VehicleRecordKind, RecordsList<VehicleRecord>>{};

  Vehicle? vehicle;
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    try {
      vehicle = await repository.get(vehicleId);
      failure = null;
      phase = RecordsPhase.ready;
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase == RecordsPhase.ready) {
      await Future.wait([for (final list in lists.values) list.load()]);
    }
  }

  /// Adds a record with one idempotency key per attempt the person makes:
  /// a retry after a lost answer sends the same key and is stored once.
  Future<VehicleRecord> add(VehicleRecordKind kind, Map<String, Object?> body,
      {required String idempotencyKey}) async {
    final record = await repository.addRecord(kind, vehicleId, body,
        idempotencyKey: idempotencyKey);
    lists[kind]!.upsert(record, (r) => r.id == record.id);
    await _refreshVehicle();
    return record;
  }

  Future<void> addReading(String km,
      {bool correction = false, String? reason}) async {
    await repository.addReading(vehicleId,
        readingKm: km, correction: correction, reason: reason);
    await _refreshVehicle();
  }

  Future<void> delete(VehicleRecordKind kind, String recordId) async {
    await repository.deleteRecord(kind, recordId);
    lists[kind]!.remove((r) => r.id == recordId);
    await _refreshVehicle();
  }

  Future<void> linked(VehicleRecordKind kind) => lists[kind]!.load();

  Future<void> _refreshVehicle() async {
    try {
      vehicle = await repository.get(vehicleId);
      _notify();
    } on Object {
      // The odometer and due items refresh on the next load.
    }
  }

  @override
  void dispose() {
    _disposed = true;
    for (final list in lists.values) {
      list.dispose();
    }
    super.dispose();
  }
}
