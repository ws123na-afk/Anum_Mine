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

  bool get inTrash => status == 'deleted';
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
