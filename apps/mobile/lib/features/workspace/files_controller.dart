import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../files/file_versions_repository.dart';
import 'files_models.dart';
import 'files_repository.dart';
import 'workspace_models.dart';

enum FilesPhase { loading, ready, denied, error }

enum UploadState { uploading, failed, cancelled }

class FileUpload {
  FileUpload(this.key, this.name, this.size, this.cancellation);
  final String key, name;
  final int size;
  final UploadCancellation cancellation;
  int sent = 0;
  UploadState state = UploadState.uploading;
  String? error;
  double get fraction => size <= 0 ? 0 : (sent / size).clamp(0, 1).toDouble();
}

const filesReadDenied =
    'Seeing files needs a viewer, member or owner role in this workspace. '
    'Ask a workspace owner for access.';
const filesManageNote =
    'Only the person who uploaded this file or a workspace owner can rename or delete it.';

/// The Files tab's state (docs/files.md): one keyset-paged list with search
/// and sort, the storage used, uploads with progress and cancel, and the
/// actions on one file. [onChanged] refreshes the rest of the workspace
/// (e.g. the files "Sources used" can open).
class FilesController extends ChangeNotifier {
  FilesController(this.repository, {this.onChanged, this.versions});
  final FilesRepository repository;

  /// File versions and the storage screen (docs/files.md#versions); null
  /// when the app wires no versions API.
  final FileVersionsRepository? versions;
  final Future<void> Function()? onChanged;

  FilesPhase phase = FilesPhase.loading;
  List<WorkspaceFile> items = const [];
  bool hasMore = false, paging = false;
  String query = '', sort = 'created_at:desc';
  FileUsage? usage;
  String? error, notice;
  final List<FileUpload> uploads = [];
  int _counter = 0;

  bool get canUpload => usage?.canUpload ?? true;

  Future<void> load() async {
    if (phase != FilesPhase.ready) phase = FilesPhase.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.list(query: query, sort: sort);
      items = page;
      hasMore = page.length == filesPageSize;
      phase = FilesPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? FilesPhase.denied : FilesPhase.error;
      error = e.message;
    } catch (e) {
      phase = FilesPhase.error;
      error = '$e';
    }
    notifyListeners();
    await loadUsage();
  }

  Future<void> loadUsage() async {
    try {
      usage = await repository.usage();
    } catch (_) {
      usage = null;
    }
    notifyListeners();
  }

  Future<void> loadMore() async {
    if (items.isEmpty || paging) return;
    paging = true;
    notifyListeners();
    try {
      final page = await repository.list(
          query: query, sort: sort, cursor: items.last.id);
      final seen = items.map((f) => f.id).toSet();
      items = [...items, ...page.where((f) => !seen.contains(f.id))];
      hasMore = page.length == filesPageSize;
    } on ApiException catch (e) {
      if (e.statusCode == 400) {
        notice =
            'The list changed while you were reading it, so it was reloaded.';
        paging = false;
        await load();
        return;
      }
      notice = 'Could not load more files: ${e.message}';
    } finally {
      paging = false;
      notifyListeners();
    }
  }

  Future<void> search(String value) async {
    query = value.trim();
    await load();
  }

  Future<void> setSort(String value) async {
    sort = value;
    await load();
  }

  /// Uploads one picked file; refusals the API would make are shown first.
  Future<WorkspaceFile?> upload(
      {required String path, required String name, required int size}) async {
    notice = null;
    usage ??= await repository
        .usage()
        .then<FileUsage?>((u) => u, onError: (Object _) => null);
    final upload =
        FileUpload('${_counter++}', name, size, UploadCancellation());
    uploads.add(upload);
    final refusal = uploadProblem(name, size, usage);
    if (refusal != null) {
      upload
        ..state = UploadState.failed
        ..error = refusal;
      notifyListeners();
      return null;
    }
    notifyListeners();
    try {
      final created = await repository.upload(path,
          name: name,
          cancellation: upload.cancellation, onProgress: (sent, total) {
        upload.sent = sent;
        notifyListeners();
      });
      uploads.remove(upload);
      notice =
          'Uploaded ${created.name}. ${indexDescription(created.index).$3}';
      await load();
      await onChanged?.call();
      return created;
    } on UploadCancelledException {
      upload
        ..state = UploadState.cancelled
        ..error = 'Upload cancelled; nothing was stored.';
    } on ApiException catch (e) {
      upload
        ..state = UploadState.failed
        ..error = e.isPermissionDenied
            ? 'Uploading needs a member or owner role in this workspace.'
            : e.message;
    } catch (e) {
      upload
        ..state = UploadState.failed
        ..error = 'The upload failed: $e. Check the connection and try again.';
    }
    notifyListeners();
    return null;
  }

  void cancelUpload(FileUpload upload) => upload.cancellation.cancel();

  void dismissUpload(FileUpload upload) {
    uploads.remove(upload);
    notifyListeners();
  }

  /// A new version or a restore changed [file]: show it and refresh the storage.
  void fileChanged(WorkspaceFile file) => _replace(file);

  void _replace(WorkspaceFile file) {
    items = [for (final f in items) f.id == file.id ? file : f];
    notifyListeners();
  }

  /// Renames; returns the error sentence, or null when it worked.
  Future<String?> rename(WorkspaceFile file, String name) async {
    final problem = renameProblem(file.name, name);
    if (problem != null) return problem;
    try {
      _replace(await repository.rename(file.id, name.trim()));
      await onChanged?.call();
      return null;
    } on ApiException catch (e) {
      return e.isPermissionDenied ? filesManageNote : e.message;
    }
  }

  Future<(WorkspaceFile?, String?)> reindex(WorkspaceFile file) async {
    try {
      final updated = await repository.reindex(file.id);
      _replace(updated);
      return (updated, null);
    } on ApiException catch (e) {
      return (
        null,
        e.isPermissionDenied
            ? 'Re-indexing needs a member or owner role in this workspace.'
            : e.message
      );
    }
  }

  Future<String?> delete(WorkspaceFile file) async {
    try {
      await repository.delete(file.id);
      items = items.where((f) => f.id != file.id).toList();
      notice = 'Deleted ${file.name}.';
      notifyListeners();
      await loadUsage();
      await onChanged?.call();
      return null;
    } on ApiException catch (e) {
      return e.isPermissionDenied ? filesManageNote : e.message;
    }
  }
}
