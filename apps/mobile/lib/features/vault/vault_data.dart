import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../meetings/meetings_repository.dart' show MeetingUploadTransfer;
import '../records_kit/records_kit.dart';
import '../workspace/files_repository.dart' show UploadCancellation;

/// The document vault (docs/documents.md#api), the parts the phone shows:
/// the list with search and filters, a document with its masked number,
/// expiry, versions and links, uploads from the phone's files, and the
/// trash.

class VaultCategory {
  const VaultCategory(
      {required this.id,
      required this.builtIn,
      this.label,
      this.identity = false});
  factory VaultCategory.fromJson(JsonReader j) => VaultCategory(
      id: j.string('id'),
      label: j.optString('label'),
      builtIn: j.optBool('built_in') ?? false,
      identity: j.optBool('identity') ?? false);
  final String id;
  final String? label;
  final bool builtIn, identity;
}

class VaultMeta {
  const VaultMeta({
    required this.categories,
    required this.canWrite,
    required this.canManage,
    required this.maxUploadBytes,
    required this.acceptedExtensions,
    required this.linkTypes,
    required this.active,
    required this.expiring,
    required this.expired,
    required this.trash,
  });
  factory VaultMeta.fromJson(JsonReader j) {
    final summary = j.optObject('summary');
    return VaultMeta(
      categories: j.optObjects('categories', VaultCategory.fromJson),
      canWrite: j.optBool('can_write') ?? false,
      canManage: j.optBool('can_manage') ?? false,
      maxUploadBytes: j.optInt('max_upload_bytes') ?? 0,
      acceptedExtensions: j.optStrings('accepted_extensions') ?? const [],
      linkTypes: j.optStrings('link_types') ?? const [],
      active: summary?.optInt('active') ?? 0,
      expiring: summary?.optInt('expiring') ?? 0,
      expired: summary?.optInt('expired') ?? 0,
      trash: summary?.optInt('trash') ?? 0,
    );
  }
  final List<VaultCategory> categories;
  final bool canWrite, canManage;
  final int maxUploadBytes;
  final List<String> acceptedExtensions, linkTypes;
  final int active, expiring, expired, trash;
}

class VaultFile {
  const VaultFile(
      {required this.name,
      required this.contentType,
      required this.sizeBytes,
      required this.version,
      this.scanStatus});
  factory VaultFile.fromJson(JsonReader j) => VaultFile(
      name: j.string('name'),
      contentType: j.optString('content_type') ?? '',
      sizeBytes: j.optInt('size_bytes') ?? 0,
      version: j.optInt('version') ?? 1,
      scanStatus: j.optString('scan_status'));
  final String name, contentType;
  final int sizeBytes, version;
  final String? scanStatus;
}

class VaultLink {
  const VaultLink(
      {required this.id,
      required this.targetType,
      required this.targetId,
      required this.label});
  factory VaultLink.fromJson(JsonReader j) => VaultLink(
      id: j.string('id'),
      targetType: j.string('target_type'),
      targetId: j.string('target_id'),
      label: j.optString('label') ?? '');
  final String id, targetType, targetId, label;
}

class VaultDocument {
  const VaultDocument({
    required this.id,
    required this.title,
    required this.category,
    required this.accessClass,
    required this.status,
    required this.expiryState,
    required this.isOwner,
    required this.canEdit,
    required this.rowVersion,
    this.issueDate,
    this.expiryDate,
    this.daysLeft,
    this.issuer,
    this.country,
    this.tags = const [],
    this.description = '',
    this.numberMasked,
    this.purgeAfter,
    this.file,
    this.links = const [],
    this.detail = false,
    this.hasReceipt = false,
  });
  factory VaultDocument.fromJson(JsonReader j) => VaultDocument(
        id: j.string('id'),
        title: j.string('title'),
        category: j.string('category'),
        accessClass: j.optString('access_class') ?? 'private',
        status: j.optString('status') ?? 'active',
        issueDate: j.optString('issue_date'),
        expiryDate: j.optString('expiry_date'),
        expiryState: j.optString('expiry_state') ?? 'none',
        daysLeft: j.optInt('days_left'),
        issuer: j.optString('issuer'),
        country: j.optString('country'),
        tags: j.optStrings('tags') ?? const [],
        description: j.optString('description') ?? '',
        numberMasked: j.optString('number_masked'),
        isOwner: j.optBool('is_owner') ?? false,
        canEdit: j.optBool('can_edit') ?? false,
        rowVersion: j.optInt('row_version') ?? 0,
        purgeAfter: j.optString('purge_after'),
        file: j.optObject('file') == null
            ? null
            : VaultFile.fromJson(j.object('file')),
        links: j.optObjects('links', VaultLink.fromJson),
        detail: j['links'] != null,
        hasReceipt: j['receipt'] != null,
      );
  final String id, title, category, accessClass, status, expiryState;
  final String? issueDate, expiryDate, issuer, country, numberMasked;
  final String? purgeAfter;
  final int? daysLeft;
  final List<String> tags;
  final String description;
  final bool isOwner, canEdit;
  final int rowVersion;
  final VaultFile? file;
  final List<VaultLink> links;

  /// Read from the detail route (links present).
  final bool detail;

  /// The person confirmed scanned receipt values (total, currency, date).
  final bool hasReceipt;

  bool get inTrash => status == 'deleted';
}

/// One record a confirmed receipt may belong to
/// (docs/documents.md#receipt-matches): a payment of the person's own
/// Finance ledger or a purchase with no receipt yet. A proposal only.
class VaultMatch {
  const VaultMatch(
      {required this.kind,
      required this.targetId,
      required this.on,
      required this.amount,
      required this.currency,
      required this.label,
      required this.confidence,
      this.merchantAgrees});
  factory VaultMatch.fromJson(JsonReader j) => VaultMatch(
      kind: j.string('kind'),
      targetId: j.string('target_id'),
      on: j.string('on'),
      amount: j.string('amount'),
      currency: j.string('currency'),
      label: j.optString('label') ?? '',
      merchantAgrees: j.optBool('merchant_agrees'),
      confidence: j.optString('confidence') ?? '');
  final String kind, targetId, on, amount, currency, label, confidence;
  final bool? merchantAgrees;

  /// The confidence as a whole percentage, or null.
  int? get percent {
    final n = double.tryParse(confidence);
    return n == null || n <= 0 ? null : (n.clamp(0, 1) * 100).round();
  }
}

/// `GET /documents/{id}/matches`.
class VaultMatches {
  const VaultMatches(
      {required this.canEdit,
      this.reason,
      this.payments = const [],
      this.purchases = const [],
      this.paymentsUnavailable,
      this.purchasesUnavailable,
      this.linkedPayments = const [],
      this.linkedPurchases = const [],
      this.dismissed = const []});
  factory VaultMatches.fromJson(JsonReader j) => VaultMatches(
      canEdit: j.optBool('can_edit') ?? false,
      reason: j.optString('reason'),
      payments: j.optObjects('payments', VaultMatch.fromJson),
      purchases: j.optObjects('purchases', VaultMatch.fromJson),
      paymentsUnavailable: j.optString('payments_unavailable'),
      purchasesUnavailable: j.optString('purchases_unavailable'),
      linkedPayments: j.optStrings('linked_payments') ?? const [],
      linkedPurchases: j.optStrings('linked_purchases') ?? const [],
      dismissed: j.optObjects('dismissed', VaultDismissedMatch.fromJson));
  final bool canEdit;

  /// Matches the person said are wrong; not proposed again until restored.
  final List<VaultDismissedMatch> dismissed;
  final String? reason, paymentsUnavailable, purchasesUnavailable;
  final List<VaultMatch> payments, purchases;
  final List<String> linkedPayments, linkedPurchases;

  bool get hasProposals => payments.isNotEmpty || purchases.isNotEmpty;
  bool get linked => linkedPayments.isNotEmpty || linkedPurchases.isNotEmpty;
}

class VaultDismissedMatch {
  const VaultDismissedMatch(
      {required this.kind,
      required this.targetId,
      required this.label,
      required this.dismissedBy});
  factory VaultDismissedMatch.fromJson(JsonReader j) => VaultDismissedMatch(
      kind: j.string('kind'),
      targetId: j.string('target_id'),
      label: j.optString('label') ?? '',
      dismissedBy: j.optString('dismissed_by') ?? '');
  final String kind, targetId, label, dismissedBy;
}

/// The vehicles an insurance or registration document may belong to
/// (docs/documents.md#vehicle-papers).
class VaultVehiclePapers {
  const VaultVehiclePapers(
      {required this.canEdit,
      this.field,
      this.expiryDate,
      this.reason,
      this.vehiclesUnavailable,
      this.vehicles = const [],
      this.reminderFrom,
      this.reminderDays});
  factory VaultVehiclePapers.fromJson(JsonReader j) => VaultVehiclePapers(
      canEdit: j.optBool('can_edit') ?? false,
      field: j.optString('field'),
      expiryDate: j.optString('expiry_date'),
      reason: j.optString('reason'),
      vehiclesUnavailable: j.optString('vehicles_unavailable'),
      vehicles: j.optObjects('vehicles', VaultVehicleChoice.fromJson),
      reminderFrom: j.optString('reminder_from'),
      reminderDays: j.optInt('reminder_days'));
  final bool canEdit;
  final String? field, expiryDate, reason, vehiclesUnavailable, reminderFrom;
  final int? reminderDays;
  final List<VaultVehicleChoice> vehicles;
}

class VaultVehicleChoice {
  const VaultVehicleChoice(
      {required this.vehicleId,
      required this.label,
      this.currentExpiry,
      this.wouldChange = true,
      this.matchesRegistration = false,
      this.linked = false});
  factory VaultVehicleChoice.fromJson(JsonReader j) => VaultVehicleChoice(
      vehicleId: j.string('vehicle_id'),
      label: j.optString('label') ?? '',
      currentExpiry: j.optString('current_expiry'),
      wouldChange: j.optBool('would_change') ?? true,
      matchesRegistration: j.optBool('matches_registration') ?? false,
      linked: j.optBool('linked') ?? false);
  final String vehicleId, label;
  final String? currentExpiry;
  final bool wouldChange, matchesRegistration, linked;
}

class VaultVersion {
  const VaultVersion({
    required this.id,
    required this.version,
    required this.sizeBytes,
    required this.createdAt,
    required this.downloadable,
  });
  factory VaultVersion.fromJson(JsonReader j) => VaultVersion(
      id: j.string('id'),
      version: j.integer('version'),
      sizeBytes: j.optInt('size_bytes') ?? 0,
      createdAt: j.string('created_at'),
      downloadable: j.optBool('downloadable') ?? true);
  final String id, createdAt;
  final int version, sizeBytes;
  final bool downloadable;
}

class VaultVersions {
  const VaultVersions(this.currentVersion, this.items, this.total);
  factory VaultVersions.fromJson(JsonReader j) => VaultVersions(
      j.optInt('current_version') ?? 1,
      j.optObjects('items', VaultVersion.fromJson),
      j.optInt('total') ?? 0);
  final int currentVersion, total;
  final List<VaultVersion> items;
}

/// Saves a document's bytes (the current file or an older version) where
/// the person chooses on the phone.
abstract interface class VaultFileSaver {
  Future<void> save(String documentId, String name, {String? versionId});
}

abstract interface class VaultRepository {
  Future<VaultMeta> meta();
  Future<RecordsChunk<VaultDocument>> list({
    String query,
    String category,
    String expiry,
    bool trash,
    String? token,
  });
  Future<VaultDocument> get(String id);
  Future<VaultDocument> trash(String id);
  Future<VaultDocument> restore(String id);
  Future<String> revealNumber(String id);
  Future<VaultVersions> versions(String id);
  Future<VaultDocument> restoreVersion(String id, String versionId);
  Future<VaultLink> addLink(String id,
      {required String targetType, required String targetId, String label});
  Future<void> removeLink(String id, String linkId);
  Future<VaultMatches> matches(String id);
  Future<VaultMatches> acceptMatch(String id, String kind, String targetId);

  /// Dismiss (or, with `restore`, forget dismissing) a proposed match.
  Future<VaultMatches> dismissMatch(String id, String kind, String targetId,
      {bool restore = false});
  Future<VaultVehiclePapers> vehiclePapers(String id);
  Future<VaultVehiclePapers> confirmVehicle(String id, String vehicleId);
  Future<VaultDocument> upload(
    String path, {
    required String name,
    required String title,
    required String category,
    required String accessClass,
    bool allowDuplicate,
    void Function(int sent, int total)? onProgress,
  });
  Future<VaultDocument> uploadVersion(String id, String path,
      {void Function(int sent, int total)? onProgress});
  Future<void> save(String id, String name, {String? versionId});
}

class ApiVaultRepository implements VaultRepository {
  const ApiVaultRepository(this.api, {this.transfer, this.saver});
  final AnumApiClient api;

  /// Streams a file from the phone to the API (the app's file transfer).
  final MeetingUploadTransfer? transfer;
  final VaultFileSaver? saver;
  static const _base = '/api/v1/documents';
  static const pageSize = 20;
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<VaultMeta> meta() async =>
      VaultMeta.fromJson(JsonReader(await api.request('GET', '$_base/meta')));

  @override
  Future<RecordsChunk<VaultDocument>> list({
    String query = '',
    String category = '',
    String expiry = '',
    bool trash = false,
    String? token,
  }) async {
    final page = JsonReader(await api.request(
        'GET',
        '$_base${recordsQuery({
              'q': query,
              'category': category,
              'expiry': expiry,
              'status': trash ? 'deleted' : null,
              'cursor': token,
              'limit': pageSize,
            })}'));
    return RecordsChunk(page.objects('items', VaultDocument.fromJson),
        next: page.optString('next_cursor'));
  }

  @override
  Future<VaultDocument> get(String id) async => VaultDocument.fromJson(
      JsonReader(await api.request('GET', '$_base/${_id(id)}')));

  @override
  Future<VaultDocument> trash(String id) async => VaultDocument.fromJson(
      JsonReader(await api.request('DELETE', '$_base/${_id(id)}')));

  @override
  Future<VaultDocument> restore(String id) async => VaultDocument.fromJson(
      JsonReader(await api.request('POST', '$_base/${_id(id)}/restore')));

  @override
  Future<String> revealNumber(String id) async =>
      JsonReader(await api.request('POST', '$_base/${_id(id)}/number'))
          .string('number');

  @override
  Future<VaultVersions> versions(String id) async => VaultVersions.fromJson(
      JsonReader(await api.request('GET', '$_base/${_id(id)}/versions')));

  @override
  Future<VaultDocument> restoreVersion(String id, String versionId) async =>
      VaultDocument.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/versions/${_id(versionId)}/restore')));

  @override
  Future<VaultLink> addLink(String id,
          {required String targetType,
          required String targetId,
          String label = ''}) async =>
      VaultLink.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/links', body: {
        'target_type': targetType,
        'target_id': targetId,
        'label': label
      })));

  @override
  Future<void> removeLink(String id, String linkId) =>
      api.request('DELETE', '$_base/${_id(id)}/links/${_id(linkId)}');

  @override
  Future<VaultMatches> matches(String id) async => VaultMatches.fromJson(
      JsonReader(await api.request('GET', '$_base/${_id(id)}/matches')));

  @override
  Future<VaultMatches> acceptMatch(
          String id, String kind, String targetId) async =>
      VaultMatches.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/matches/accept',
          body: {'kind': kind, 'target_id': targetId})));

  @override
  Future<VaultMatches> dismissMatch(String id, String kind, String targetId,
          {bool restore = false}) async =>
      VaultMatches.fromJson(JsonReader(await api.request('POST',
          '$_base/${_id(id)}/matches/${restore ? 'restore' : 'dismiss'}',
          body: {'kind': kind, 'target_id': targetId})));

  @override
  Future<VaultVehiclePapers> vehiclePapers(String id) async =>
      VaultVehiclePapers.fromJson(JsonReader(
          await api.request('GET', '$_base/${_id(id)}/vehicle-proposals')));

  @override
  Future<VaultVehiclePapers> confirmVehicle(
          String id, String vehicleId) async =>
      VaultVehiclePapers.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/vehicle',
          body: {'vehicle_id': vehicleId})));

  MeetingUploadTransfer _transfer() {
    final found = transfer;
    if (found == null) {
      throw const ApiException(0, 'Uploading files is not available here.');
    }
    return found;
  }

  @override
  Future<VaultDocument> upload(
    String path, {
    required String name,
    required String title,
    required String category,
    required String accessClass,
    bool allowDuplicate = false,
    void Function(int sent, int total)? onProgress,
  }) async =>
      VaultDocument.fromJson(JsonReader(await _transfer().uploadTo(
        'api/v1/documents',
        {
          'name': name,
          'title': title,
          'category': category,
          'access_class': accessClass,
          if (allowDuplicate) 'allow_duplicate': 'true',
        },
        path,
        onProgress: onProgress ?? (_, __) {},
        cancellation: UploadCancellation(),
      )));

  @override
  Future<VaultDocument> uploadVersion(String id, String path,
          {void Function(int sent, int total)? onProgress}) async =>
      VaultDocument.fromJson(JsonReader(await _transfer().uploadTo(
        'api/v1/documents/${_id(id)}/versions',
        const {},
        path,
        onProgress: onProgress ?? (_, __) {},
        cancellation: UploadCancellation(),
      )));

  @override
  Future<void> save(String id, String name, {String? versionId}) {
    final found = saver;
    if (found == null) {
      throw const ApiException(0, 'Saving files is not available here.');
    }
    return found.save(id, name, versionId: versionId);
  }
}

/// The same bytes are already in the vault (the API answers 409).
bool isDuplicate(Object error) =>
    error is ApiException && error.statusCode == 409;
