import '../../data/api_models.dart';
import '../workspace/files_models.dart';
import '../workspace/work_status.dart';
import '../workspace/workspace_models.dart';
import 'file_strings.dart';

/// File versions and the storage report (docs/files.md#versions): parsing and
/// the checks made before a new version is sent. Pure, so tests exercise it
/// without a widget.

const versionsPageSize = 10;

/// One earlier version of a file. [downloadable] is false for a quarantined
/// version (the malware scanner found something in it).
class FileVersion {
  const FileVersion({
    required this.id,
    required this.fileId,
    required this.version,
    required this.contentType,
    required this.sizeBytes,
    required this.createdBy,
    required this.createdAt,
    required this.replacedBy,
    required this.replacedAt,
    required this.downloadable,
    this.scanSignature,
  });

  final String id, fileId, contentType, createdBy, replacedBy;
  final int version, sizeBytes;
  final DateTime createdAt, replacedAt;
  final bool downloadable;
  final String? scanSignature;
}

FileVersion parseFileVersion(JsonReader j) => FileVersion(
      id: j.string('id'),
      fileId: j.string('file_id'),
      version: j.integer('version'),
      contentType: j.string('content_type'),
      sizeBytes: j.integer('size_bytes'),
      createdBy: j.optString('created_by') ?? '',
      createdAt: j.date('created_at'),
      replacedBy: j.optString('replaced_by') ?? '',
      replacedAt: j.date('replaced_at'),
      downloadable: j.optBool('downloadable') ?? true,
      scanSignature: j.optString('scan_signature'),
    );

/// `GET /api/v1/files/{id}/versions`: the current file and one page of the
/// older versions, newest first.
class FileVersionPage {
  const FileVersionPage(
      {required this.file, required this.items, required this.total});
  final WorkspaceFile file;
  final List<FileVersion> items;
  final int total;

  factory FileVersionPage.fromJson(JsonReader j) => FileVersionPage(
        file: parseWorkspaceFile(j.object('file')),
        items: j.optObjects('items', parseFileVersion),
        total: j.integer('total'),
      );
}

String versionsPath(String fileId, int offset,
        {int limit = versionsPageSize}) =>
    Uri(
        path: '/api/v1/files/${Uri.encodeComponent(fileId)}/versions',
        queryParameters: {
          'limit': '$limit',
          'offset': '${offset < 0 ? 0 : offset}'
        }).toString();

/// Appends a page, keeping the first copy of a version a racing reload
/// already listed.
List<FileVersion> mergeVersions(
    List<FileVersion> current, List<FileVersion> page) {
  final seen = current.map((v) => v.id).toSet();
  return [...current, ...page.where((v) => !seen.contains(v.id))];
}

String _extension(String name) {
  final dot = name.lastIndexOf('.');
  return dot > 0 ? name.substring(dot).toLowerCase() : '';
}

/// Bytes left under the quota; never negative.
int bytesLeft(FileUsage usage) {
  final left = usage.quotaBytes - usage.usedBytes;
  return left < 0 ? 0 : left;
}

/// A reason to refuse a new version before sending it, or null to send. The
/// server counts bytes the file already holds once, so this only warns early;
/// it still enforces the quota itself.
String? versionProblem(String name, int size, WorkspaceFile current,
    FileUsage? usage, FileStrings s) {
  if (size == 0) return s.t('ver.empty0', {'name': name});
  if (usage == null) return null;
  if (size > usage.maxUploadBytes) {
    return s.t('ver.tooLarge', {
      'name': name,
      'size': formatBytes(size),
      'max': formatBytes(usage.maxUploadBytes)
    });
  }
  if (_extension(name) != _extension(current.name)) {
    final extension = _extension(current.name);
    return s.t('ver.sameType',
        {'extension': extension.isEmpty ? s.t('ver.noExtension') : extension});
  }
  if (usage.usedBytes + size > usage.quotaBytes) {
    return s.t('ver.quota', {
      'name': name,
      'size': formatBytes(size),
      'left': formatBytes(bytesLeft(usage)),
      'quota': formatBytes(usage.quotaBytes)
    });
  }
  return null;
}

class StorageKindRow {
  const StorageKindRow(
      {required this.kind, required this.fileCount, required this.bytes});
  final String kind;
  final int fileCount, bytes;
}

class StorageFileRow {
  const StorageFileRow({
    required this.file,
    required this.versionCount,
    required this.versionBytes,
    required this.totalBytes,
  });
  final WorkspaceFile file;
  final int versionCount, versionBytes, totalBytes;
}

/// `GET /api/v1/files/storage`: usage against the quota, by kind, and the
/// files that take the most space.
class StorageReport {
  const StorageReport(
      {required this.usage, required this.byKind, required this.largest});
  final FileUsage usage;
  final List<StorageKindRow> byKind;
  final List<StorageFileRow> largest;

  factory StorageReport.fromJson(JsonReader j) => StorageReport(
        usage: FileUsage.fromJson(j.object('usage')),
        byKind: j.optObjects(
            'by_kind',
            (k) => StorageKindRow(
                kind: k.string('kind'),
                fileCount: k.integer('file_count'),
                bytes: k.integer('bytes'))),
        largest: j.optObjects(
            'largest',
            (r) => StorageFileRow(
                file: parseWorkspaceFile(r.object('file')),
                versionCount: r.integer('version_count'),
                versionBytes: r.integer('version_bytes'),
                totalBytes: r.integer('total_bytes'))),
      );

  bool get isEmpty => usage.fileCount == 0;
}

/// The tone of the quota meter: amber from 80%, red from 95%.
enum QuotaLevel { ok, warn, nearFull, full }

QuotaLevel quotaLevel(FileUsage usage) {
  if (usage.quotaBytes <= 0) return QuotaLevel.ok;
  final used = usage.usedBytes / usage.quotaBytes;
  if (used >= 1) return QuotaLevel.full;
  if (used >= .95) return QuotaLevel.nearFull;
  if (used >= .8) return QuotaLevel.warn;
  return QuotaLevel.ok;
}
