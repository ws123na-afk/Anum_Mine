import '../../data/api_models.dart';
import 'work_status.dart';
import 'workspace_models.dart';

/// Workspace files (docs/files.md): parsing, upload checks, labels and the
/// index state, shared by the Files tab and its tests.

WorkspaceFile parseWorkspaceFile(JsonReader j) {
  final index = j.optObject('index');
  return WorkspaceFile(
    id: j.string('id'),
    name: j.string('name'),
    contentType: j.string('content_type'),
    sizeBytes: j.integer('size_bytes'),
    createdAt: j.date('created_at'),
    createdBy: j.optString('created_by') ?? '',
    updatedAt: j.optDate('updated_at'),
    kind: j.optString('kind'),
    previewable: j.optBool('previewable') ?? false,
    canManage: j.optBool('can_manage') ?? false,
    index: index == null
        ? null
        : FileIndexState(
            status: index.string('status'),
            error: index.optString('error'),
            chunkCount: index.optInt('chunk_count') ?? 0),
  );
}

class FileUsage {
  const FileUsage({
    required this.usedBytes,
    required this.fileCount,
    required this.quotaBytes,
    required this.maxUploadBytes,
    required this.accepted,
    required this.acceptedExtensions,
    required this.canUpload,
  });

  factory FileUsage.fromJson(JsonReader j) => FileUsage(
        usedBytes: j.integer('used_bytes'),
        fileCount: j.integer('file_count'),
        quotaBytes: j.integer('quota_bytes'),
        maxUploadBytes: j.integer('max_upload_bytes'),
        accepted: j.string('accepted'),
        acceptedExtensions: [
          for (final value in j.optList('accepted_extensions') ?? const [])
            if (value is String) value
        ],
        canUpload: j.optBool('can_upload') ?? false,
      );

  final int usedBytes, fileCount, quotaBytes, maxUploadBytes;
  final String accepted;
  final List<String> acceptedExtensions;
  final bool canUpload;

  double get fraction =>
      quotaBytes <= 0 ? 0 : (usedBytes / quotaBytes).clamp(0, 1).toDouble();

  String get summary =>
      '${formatBytes(usedBytes)} of ${formatBytes(quotaBytes)} used · '
      '$fileCount ${fileCount == 1 ? 'file' : 'files'}';
}

class FilePreviewText {
  const FilePreviewText({required this.text, required this.truncated});
  final String text;
  final bool truncated;
}

/// Sort choices, as `sort:order` for `GET /api/v1/files`.
const fileSortOptions = <(String, String)>[
  ('created_at:desc', 'Newest first'),
  ('created_at:asc', 'Oldest first'),
  ('name:asc', 'Name, A to Z'),
  ('name:desc', 'Name, Z to A'),
  ('size:desc', 'Largest first'),
  ('size:asc', 'Smallest first'),
];

const filesPageSize = 50;

/// `GET /api/v1/files` for one page: search, sort and the keyset cursor.
String filesListPath(
    {String query = '',
    String sort = 'created_at:desc',
    String? cursor,
    int limit = filesPageSize}) {
  final parts = sort.split(':');
  return Uri(path: '/api/v1/files', queryParameters: {
    'limit': '$limit',
    'sort': parts.first,
    'order': parts.length > 1 ? parts[1] : 'desc',
    if (query.trim().isNotEmpty) 'q': query.trim(),
    if (cursor != null) 'cursor': cursor,
  }).toString();
}

String _extension(String name) {
  final dot = name.lastIndexOf('.');
  return dot > 0 ? name.substring(dot).toLowerCase() : '';
}

/// A reason to refuse an upload before sending it, or null to send.
String? uploadProblem(String name, int size, FileUsage? usage) {
  if (size == 0) return '$name is empty. Choose a file with content.';
  if (usage == null) return null;
  if (size > usage.maxUploadBytes) {
    return '$name is ${formatBytes(size)}; files can be at most '
        '${formatBytes(usage.maxUploadBytes)}. Split or compress it.';
  }
  if (!usage.acceptedExtensions.contains(_extension(name))) {
    return '$name cannot be uploaded: this workspace accepts ${usage.accepted}.';
  }
  if (usage.usedBytes + size > usage.quotaBytes) {
    return '$name (${formatBytes(size)}) does not fit: '
        '${formatBytes(usage.usedBytes)} of ${formatBytes(usage.quotaBytes)} '
        'is used. Delete files you no longer need.';
  }
  return null;
}

/// Why a new name would be refused, or null. The extension (the type) stays.
String? renameProblem(String current, String next) {
  final name = next.trim();
  if (name.isEmpty) return 'Enter a name.';
  if (name.length > 255) return 'Names are at most 255 characters.';
  if (RegExp(r'[\\/\x00-\x1f\x7f]').hasMatch(name) ||
      name == '.' ||
      name == '..') {
    return 'Names cannot contain / or \\ or control characters.';
  }
  if (_extension(name) != _extension(current)) {
    return _extension(current).isEmpty
        ? 'Do not add an extension: renaming does not change the file type.'
        : 'Keep the ${_extension(current)} extension: renaming does not change the file type.';
  }
  if (name == current) return 'Enter a different name.';
  return null;
}

const _skipReasons = {
  'not_text': 'this file type is not indexed',
  'too_large': 'it has more than 2 MiB of text',
  'not_utf8': 'it is not UTF-8 text',
  'empty': 'it has no text',
  'encrypted': 'it is password protected',
  'malformed': 'it is damaged or could not be read',
  'too_complex': 'it is too complex to read safely',
};

/// The retrieval index state as a label, a tone and a sentence.
(String, AnumIndexTone, String) indexDescription(FileIndexState? index) {
  if (index == null) {
    return (
      'Index unknown',
      AnumIndexTone.neutral,
      'The index state could not be read.'
    );
  }
  final reason = (index.error ?? '').replaceAll('_', ' ');
  return switch (index.status) {
    'indexed' => (
        'Indexed',
        AnumIndexTone.ok,
        'Tasks can use this file (${index.chunkCount} '
            '${index.chunkCount == 1 ? 'passage' : 'passages'}).'
      ),
    'stale' => (
        'Re-index needed',
        AnumIndexTone.warn,
        'Indexed with an earlier embedding model; re-index so tasks find it.'
      ),
    'failed' => (
        'Indexing failed',
        AnumIndexTone.stop,
        'Indexing failed${reason.isEmpty ? '' : ' ($reason)'}. Re-index to try again.'
      ),
    'skipped' => (
        'Not indexed',
        AnumIndexTone.neutral,
        'Not indexed: ${_skipReasons[index.error] ?? (reason.isEmpty ? 'not indexable' : reason)}.'
      ),
    _ => (
        'Not indexed yet',
        AnumIndexTone.warn,
        'Not indexed yet. Re-index so tasks can use it.'
      ),
  };
}

enum AnumIndexTone { ok, warn, stop, neutral }

bool canReindex(FileIndexState? index) =>
    index == null ||
    index.status == 'failed' ||
    index.status == 'stale' ||
    index.status == 'pending';

/// How the detail screen previews a file, or null for download only.
enum FilePreviewMode { text, image }

FilePreviewMode? previewModeOf(WorkspaceFile file) {
  if (!file.previewable) return null;
  return switch (file.kind) {
    'text' || 'markdown' || 'data' => FilePreviewMode.text,
    'image' => FilePreviewMode.image,
    _ => null,
  };
}

String fileTypeLabel(String contentType) {
  final media = contentType.split(';').first.trim().toLowerCase();
  const known = {
    'text/plain': 'Text',
    'text/markdown': 'Markdown',
    'text/csv': 'CSV',
    'application/json': 'JSON',
    'application/yaml': 'YAML',
    'application/xml': 'XML',
    'image/png': 'PNG image',
    'image/jpeg': 'JPEG image',
    'image/gif': 'GIF image',
    'image/webp': 'WebP image',
    'application/pdf': 'PDF',
    'application/zip': 'ZIP archive',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
        'Word document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        'Excel workbook',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation':
        'PowerPoint deck',
  };
  final label = known[media];
  if (label != null) return label;
  final parts = media.split('/');
  final sub = parts.length > 1
      ? parts[1].replaceFirst(RegExp(r'^(x-|vnd\.)'), '').split('+').first
      : '';
  return (sub.isEmpty ? (parts.first.isEmpty ? 'file' : parts.first) : sub)
      .toUpperCase();
}
