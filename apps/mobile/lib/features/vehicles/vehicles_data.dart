import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';

/// Vehicles, fuel and mileage (docs/vehicles.md#api), the fields the phone
/// shows. Money is never posted here: a cost points at a Finance payment.

class VehiclePayment {
  const VehiclePayment({
    required this.status,
    required this.amount,
    required this.currency,
    required this.recordedAsCash,
    this.statusReason,
    this.source = 'link',
    this.sourceRef,
  });
  factory VehiclePayment.fromJson(JsonReader j) => VehiclePayment(
        status: j.string('status'),
        statusReason: j.optString('status_reason'),
        amount: j.string('amount'),
        currency: j.string('currency'),
        recordedAsCash: j.optBool('recorded_as_cash') ?? false,
        source: j.optString('source') ?? 'link',
        sourceRef: j.optString('source_ref'),
      );
  final String status, amount, currency;
  final String? statusReason, sourceRef;
  final bool recordedAsCash;

  /// `allocation`: a business vehicle's cost paid from a personal card and
  /// allocated to the business in Finance (docs/vehicles.md#business-allocations).
  final String source;
  bool get isAllocation => source == 'allocation';
}

/// One of this business's allocations the caller made that no vehicle
/// record counts yet (`GET /vehicles/allocations`).
class VehicleAllocation {
  const VehicleAllocation({
    required this.id,
    required this.postedOn,
    required this.amount,
    required this.currency,
    required this.category,
    required this.sourceRef,
    this.memo,
  });
  factory VehicleAllocation.fromJson(JsonReader j) => VehicleAllocation(
        id: j.string('allocation_id'),
        postedOn: j.string('posted_on'),
        amount: j.string('amount'),
        currency: j.string('currency'),
        category: j.optString('category') ?? '',
        sourceRef: j.optString('source_ref') ?? '',
        memo: j.optString('memo'),
      );
  final String id, postedOn, amount, currency, category, sourceRef;
  final String? memo;
}

class VehicleDue {
  const VehicleDue({
    required this.vehicleId,
    required this.registration,
    required this.kind,
    this.serviceKind,
    this.dueOn,
    this.daysLeft,
    this.dueKm,
    this.kmLeft,
  });
  factory VehicleDue.fromJson(JsonReader j) => VehicleDue(
        vehicleId: j.string('vehicle_id'),
        registration: j.optString('registration') ?? '',
        kind: j.string('kind'),
        serviceKind: j.optString('service_kind'),
        dueOn: j.optString('due_on'),
        daysLeft: j.optInt('days_left'),
        dueKm: j.optString('due_km'),
        kmLeft: j.optString('km_left'),
      );
  final String vehicleId, registration, kind;
  final String? serviceKind, dueOn, dueKm, kmLeft;
  final int? daysLeft;

  /// Past its date or its kilometres.
  bool get overdue =>
      (daysLeft != null && daysLeft! < 0) ||
      (kmLeft != null && (double.tryParse(kmLeft!) ?? 0) < 0);
}

class Vehicle {
  const Vehicle({
    required this.id,
    required this.ownership,
    required this.registration,
    required this.vehicleType,
    required this.country,
    required this.currency,
    required this.fuelType,
    required this.version,
    required this.canEdit,
    required this.mine,
    this.vin,
    this.nickname,
    this.make,
    this.model,
    this.year,
    this.odometerKm,
    this.insuranceExpiresOn,
    this.registrationExpiresOn,
    this.inspectionExpiresOn,
    this.notes,
    this.archivedAt,
    this.due = const [],
  });
  factory Vehicle.fromJson(JsonReader j) => Vehicle(
        id: j.string('id'),
        ownership: j.string('ownership'),
        registration: j.string('registration'),
        vin: j.optString('vin'),
        nickname: j.optString('nickname'),
        make: j.optString('make'),
        model: j.optString('model'),
        year: j.optInt('year'),
        vehicleType: j.optString('vehicle_type') ?? 'car',
        country: j.optString('country') ?? '',
        currency: j.optString('currency') ?? '',
        fuelType: j.optString('fuel_type') ?? 'other',
        odometerKm: j.optString('odometer_km'),
        insuranceExpiresOn: j.optString('insurance_expires_on'),
        registrationExpiresOn: j.optString('registration_expires_on'),
        inspectionExpiresOn: j.optString('inspection_expires_on'),
        notes: j.optString('notes'),
        archivedAt: j.optString('archived_at'),
        version: j.integer('version'),
        canEdit: j.optBool('can_edit') ?? false,
        mine: j.optBool('mine') ?? false,
        due: j.optObjects('due', VehicleDue.fromJson),
      );
  final String id, ownership, registration, vehicleType, country, currency;
  final String fuelType;
  final String? vin, nickname, make, model, odometerKm, notes, archivedAt;
  final String? insuranceExpiresOn, registrationExpiresOn, inspectionExpiresOn;
  final int? year;
  final int version;
  final bool canEdit, mine;
  final List<VehicleDue> due;

  String get title => nickname?.isNotEmpty ?? false
      ? '$nickname ($registration)'
      : registration;
  String get makeModel => [make, model, year?.toString()]
      .whereType<String>()
      .where((part) => part.isNotEmpty)
      .join(' ');
}

/// One fuel entry, trip or service record, as the lists show it.
sealed class VehicleRecord {
  const VehicleRecord(
      {required this.id, required this.version, required this.canEdit});
  final String id;
  final int version;
  final bool canEdit;
}

class VehicleFuel extends VehicleRecord {
  const VehicleFuel({
    required super.id,
    required super.version,
    required super.canEdit,
    required this.filledAt,
    required this.litres,
    required this.fuelType,
    required this.currency,
    required this.fullTank,
    this.station,
    this.total,
    this.odometerKm,
    this.payment,
  });
  factory VehicleFuel.fromJson(JsonReader j) => VehicleFuel(
        id: j.string('id'),
        version: j.integer('version'),
        canEdit: j.optBool('can_edit') ?? false,
        filledAt: j.string('filled_at'),
        litres: j.string('litres'),
        fuelType: j.optString('fuel_type') ?? 'other',
        currency: j.optString('currency') ?? '',
        fullTank: j.optBool('full_tank') ?? true,
        station: j.optString('station'),
        total: j.optString('total'),
        odometerKm: j.optString('odometer_km'),
        payment: j.optObject('payment')?.let(VehiclePayment.fromJson),
      );
  final String filledAt, litres, fuelType, currency;
  final bool fullTank;
  final String? station, total, odometerKm;
  final VehiclePayment? payment;
}

class VehicleTrip extends VehicleRecord {
  const VehicleTrip({
    required super.id,
    required super.version,
    required super.canEdit,
    required this.status,
    required this.startedAt,
    required this.purpose,
    this.endedAt,
    this.startOdometerKm,
    this.endOdometerKm,
    this.distanceKm,
    this.origin,
    this.destination,
  });
  factory VehicleTrip.fromJson(JsonReader j) => VehicleTrip(
        id: j.string('id'),
        version: j.integer('version'),
        canEdit: j.optBool('can_edit') ?? false,
        status: j.optString('status') ?? 'closed',
        startedAt: j.string('started_at'),
        endedAt: j.optString('ended_at'),
        startOdometerKm: j.optString('start_odometer_km'),
        endOdometerKm: j.optString('end_odometer_km'),
        distanceKm: j.optString('distance_km'),
        purpose: j.optString('purpose') ?? 'business',
        origin: j.optString('origin'),
        destination: j.optString('destination'),
      );
  final String status, startedAt, purpose;
  final String? endedAt, startOdometerKm, endOdometerKm, distanceKm;
  final String? origin, destination;
}

class VehicleService extends VehicleRecord {
  const VehicleService({
    required super.id,
    required super.version,
    required super.canEdit,
    required this.kind,
    required this.serviceOn,
    required this.currency,
    this.odometerKm,
    this.workshop,
    this.description,
    this.nextDueOn,
    this.nextDueKm,
    this.receiptTotal,
    this.payment,
  });
  factory VehicleService.fromJson(JsonReader j) => VehicleService(
        id: j.string('id'),
        version: j.integer('version'),
        canEdit: j.optBool('can_edit') ?? false,
        kind: j.string('kind'),
        serviceOn: j.string('service_on'),
        currency: j.optString('currency') ?? '',
        odometerKm: j.optString('odometer_km'),
        workshop: j.optString('workshop'),
        description: j.optString('description'),
        nextDueOn: j.optString('next_due_on'),
        nextDueKm: j.optString('next_due_km'),
        receiptTotal: j.optString('receipt_total'),
        payment: j.optObject('payment')?.let(VehiclePayment.fromJson),
      );
  final String kind, serviceOn, currency;
  final String? odometerKm, workshop, description, nextDueOn, nextDueKm;
  final String? receiptTotal;
  final VehiclePayment? payment;
}

/// One of the caller's own Finance payments, offered when linking a cost.
class VehiclePaymentCandidate {
  const VehiclePaymentCandidate({
    required this.canonicalId,
    required this.postedOn,
    required this.merchant,
    required this.amount,
    required this.currency,
    required this.remaining,
    required this.linkedToVehicle,
  });
  factory VehiclePaymentCandidate.fromJson(JsonReader j) =>
      VehiclePaymentCandidate(
        canonicalId: j.string('canonical_id'),
        postedOn: j.string('posted_on'),
        merchant: j.optString('merchant') ?? '',
        amount: j.string('amount'),
        currency: j.string('currency'),
        remaining: j.optString('remaining') ?? j.string('amount'),
        linkedToVehicle: j.optBool('linked_to_vehicle') ?? false,
      );
  final String canonicalId, postedOn, merchant, amount, currency, remaining;
  final bool linkedToVehicle;
}

extension _Let<T> on T {
  R let<R>(R Function(T value) apply) => apply(this);
}

/// Which list a record belongs to (the API path segment).
enum VehicleRecordKind {
  fuel('fuel', 'fuel_entry'),
  trips('trips', null),
  service('service', 'service_record');

  const VehicleRecordKind(this.path, this.costKind);
  final String path;

  /// The cost kind for linking a payment; trips carry no money.
  final String? costKind;
}

abstract interface class VehiclesRepository {
  Future<RecordsChunk<Vehicle>> list(
      {String query, bool archived, String? token});
  Future<Vehicle> get(String id);
  Future<Vehicle> create(Map<String, Object?> body);
  Future<List<VehicleDue>> reminders();
  Future<RecordsChunk<VehicleRecord>> records(
      VehicleRecordKind kind, String vehicleId,
      {String? token});
  Future<VehicleRecord> addRecord(
      VehicleRecordKind kind, String vehicleId, Map<String, Object?> body,
      {required String idempotencyKey});
  Future<void> deleteRecord(VehicleRecordKind kind, String recordId);
  Future<void> addReading(String vehicleId,
      {required String readingKm, bool correction, String? reason});
  Future<List<VehiclePaymentCandidate>> paymentCandidates(
      {String query, String? around, String? amount});
  Future<VehiclePayment> linkPayment(String costKind, String recordId,
      {required String canonicalId, required String idempotencyKey});
  Future<List<VehicleAllocation>> allocations({String? around, String? amount});
  Future<VehiclePayment> useAllocation(
      String costKind, String recordId, String allocationId);
}

class ApiVehiclesRepository implements VehiclesRepository {
  const ApiVehiclesRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/vehicles';
  static const pageSize = 20;
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<RecordsChunk<Vehicle>> list(
          {String query = '', bool archived = false, String? token}) async =>
      RecordsChunk.offsetPage(
          JsonReader(await api.request(
              'GET',
              '$_base${recordsQuery({
                    'q': query,
                    'archived': archived ? 'true' : null,
                    'offset': token ?? 0,
                    'limit': pageSize,
                  })}')),
          Vehicle.fromJson);

  @override
  Future<Vehicle> get(String id) async => Vehicle.fromJson(
      JsonReader(await api.request('GET', '$_base/${_id(id)}')));

  @override
  Future<Vehicle> create(Map<String, Object?> body) async => Vehicle.fromJson(
      JsonReader(await api.request('POST', _base, body: body)));

  @override
  Future<List<VehicleDue>> reminders() async =>
      JsonReader(await api.request('GET', '$_base/reminders'))
          .optObjects('items', VehicleDue.fromJson);

  static VehicleRecord _record(VehicleRecordKind kind, JsonReader j) =>
      switch (kind) {
        VehicleRecordKind.fuel => VehicleFuel.fromJson(j),
        VehicleRecordKind.trips => VehicleTrip.fromJson(j),
        VehicleRecordKind.service => VehicleService.fromJson(j),
      };

  @override
  Future<RecordsChunk<VehicleRecord>> records(
          VehicleRecordKind kind, String vehicleId, {String? token}) async =>
      RecordsChunk.offsetPage(
          JsonReader(await api.request(
              'GET',
              '$_base/${_id(vehicleId)}/${kind.path}'
                  '${recordsQuery({
                    'offset': token ?? 0,
                    'limit': pageSize
                  })}')),
          (j) => _record(kind, j));

  @override
  Future<VehicleRecord> addRecord(
          VehicleRecordKind kind, String vehicleId, Map<String, Object?> body,
          {required String idempotencyKey}) async =>
      _record(
          kind,
          JsonReader(await api.request(
              'POST', '$_base/${_id(vehicleId)}/${kind.path}',
              body: body, headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<void> deleteRecord(VehicleRecordKind kind, String recordId) =>
      api.request('DELETE', '$_base/${kind.path}/${_id(recordId)}');

  @override
  Future<void> addReading(String vehicleId,
          {required String readingKm,
          bool correction = false,
          String? reason}) =>
      api.request('POST', '$_base/${_id(vehicleId)}/odometer', body: {
        'reading_km': readingKm,
        'correction': correction,
        if (correction) 'reason': reason,
      });

  @override
  Future<List<VehiclePaymentCandidate>> paymentCandidates(
          {String query = '', String? around, String? amount}) async =>
      JsonReader(await api.request(
              'GET',
              '$_base/payments${recordsQuery({
                    'q': query,
                    'around': around,
                    'amount': amount
                  })}'))
          .optObjects('items', VehiclePaymentCandidate.fromJson);

  @override
  Future<VehiclePayment> linkPayment(String costKind, String recordId,
          {required String canonicalId,
          required String idempotencyKey}) async =>
      VehiclePayment.fromJson(JsonReader(await api.request(
              'POST', '$_base/costs/$costKind/${_id(recordId)}/payment',
              body: {'canonical_id': canonicalId},
              headers: {'Idempotency-Key': idempotencyKey}))
          .object('payment'));

  @override
  Future<List<VehicleAllocation>> allocations(
          {String? around, String? amount}) async =>
      JsonReader(await api.request(
              'GET',
              '$_base/allocations${recordsQuery({
                    'around': around,
                    'amount': amount
                  })}'))
          .optObjects('items', VehicleAllocation.fromJson);

  @override
  Future<VehiclePayment> useAllocation(
          String costKind, String recordId, String allocationId) async =>
      VehiclePayment.fromJson(JsonReader(await api.request(
              'POST', '$_base/costs/$costKind/${_id(recordId)}/allocation',
              body: {'allocation_id': allocationId}))
          .object('payment'));
}

/// The refusal code when a reading or trip would make the odometer go down.
const odometerDecrease = 'odometer_decrease';

/// Whether [error] is the "odometer cannot go down" refusal.
bool isOdometerDecrease(Object error) =>
    error is ApiException && error.code == odometerDecrease;
