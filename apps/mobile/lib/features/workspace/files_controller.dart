import '../../data/readable_error.dart';
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

  // Folders, tags and the selection (docs/files.md#folders, #tags, #bulk-actions).
  List<FileFolder> folders = const [];
  List<FileTag> tags = const [];

  /// The open folder: null is the root (files in no folder). A search looks
  /// in every folder.
  String? folder;
  String? tagFilter;
  bool selecting = false;
  final Set<String> selected = {};

  bool get searching => query.isNotEmpty;
  FileFolder? get currentFolder => folderTrail(folders, folder).lastOrNull;
  List<FileFolder> get trail => folderTrail(folders, folder);
  List<FileFolder> get subfolders => folderChildren(folders, folder);
  List<WorkspaceFile> get selectedFiles =>
      items.where((f) => selected.contains(f.id)).toList();

  /// Forgets the list, the usage and the uploads (sign-out); uploads still
  /// running are cancelled.
  void reset() {
    for (final upload in uploads) {
      if (upload.state == UploadState.uploading) upload.cancellation.cancel();
    }
    uploads.clear();
    phase = FilesPhase.loading;
    items = const [];
    hasMore = paging = false;
    query = '';
    sort = 'created_at:desc';
    usage = null;
    error = notice = null;
    folders = const [];
    tags = const [];
    folder = tagFilter = null;
    selecting = false;
    selected.clear();
    notifyListeners();
  }

  bool get canUpload => usage?.canUpload ?? true;

  Future<void> load() async {
    if (phase != FilesPhase.ready) phase = FilesPhase.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.list(
          query: query,
          sort: sort,
          folder: searching ? everyFolder : folder,
          tag: tagFilter);
      items = page;
      hasMore = page.length == filesPageSize;
      phase = FilesPhase.ready;
      selected.removeWhere((id) => !items.any((f) => f.id == id));
    } on ApiException catch (e) {
      if (e.statusCode == 404 && (folder != null || tagFilter != null)) {
        // The folder or tag was deleted meanwhile: back to the root.
        notice = 'This folder no longer exists. Showing all documents.';
        folder = tagFilter = null;
        await load();
        return;
      }
      phase = e.isPermissionDenied ? FilesPhase.denied : FilesPhase.error;
      error = e.message;
    } catch (e) {
      phase = FilesPhase.error;
      error = readableError(e);
    }
    notifyListeners();
    await loadUsage();
    await loadOrganize();
  }

  /// The folder tree and the tags; a failure keeps the list usable.
  Future<void> loadOrganize() async {
    try {
      folders = await repository.folders();
      tags = await repository.tags();
    } on ApiException catch (e) {
      if (!e.isPermissionDenied) {
        notice = 'Could not load the folders and tags: ${e.message}';
      }
    } catch (e) {
      notice = 'Could not load the folders and tags: ${readableError(e)}';
    }
    notifyListeners();
  }

  Future<void> openFolder(String? id) async {
    folder = id;
    query = '';
    selected.clear();
    notice = null;
    await load();
  }

  Future<void> setTagFilter(String? id) async {
    tagFilter = id;
    selected.clear();
    await load();
  }

  void toggleSelecting() {
    selecting = !selecting;
    if (!selecting) selected.clear();
    notifyListeners();
  }

  void toggleSelected(WorkspaceFile file) {
    if (!selected.remove(file.id)) selected.add(file.id);
    notifyListeners();
  }

  void selectAll() {
    selected.addAll(items.map((f) => f.id));
    notifyListeners();
  }

  void clearSelection() {
    selected.clear();
    notifyListeners();
  }

  /// Whether the selection may be moved or deleted by this person: every file
  /// must be theirs unless they are an owner (the API refuses otherwise).
  bool get selectionManageable =>
      canUpload &&
      selectedFiles.isNotEmpty &&
      selectedFiles.every((f) => f.canManage);

  /// Creates a folder in the open folder; returns the error sentence or null.
  Future<String?> createFolder(String name) async {
    final problem = folderNameProblem(name, subfolders);
    if (problem != null) return problem;
    try {
      await repository.createFolder(name.trim(), folder);
      notice = 'Folder ${name.trim()} created.';
      await loadOrganize();
      return null;
    } on ApiException catch (e) {
      return e.isPermissionDenied
          ? 'Creating folders needs a member or owner role in this workspace.'
          : e.message;
    }
  }

  /// Moves the selected files into [target] (null: the root); all or nothing.
  Future<String?> moveSelected(String? target) async {
    final ids = selected.toList();
    try {
      final moved = await repository.bulkMove(ids, target);
      final name = target == null
          ? 'All documents'
          : folders
                  .where((f) => f.id == target)
                  .map((f) => f.name)
                  .firstOrNull ??
              'the folder';
      notice = 'Moved $moved ${moved == 1 ? 'file' : 'files'} to $name.';
      selected.clear();
      await load();
      await onChanged?.call();
      return null;
    } on ApiException catch (e) {
      return 'Nothing was moved: ${e.message}';
    }
  }

  /// Deletes the selected files; all or nothing.
  Future<String?> deleteSelected() async {
    final ids = selected.toList();
    try {
      final count = await repository.bulkDelete(ids);
      notice = 'Deleted $count ${count == 1 ? 'file' : 'files'}.';
      selected.clear();
      await load();
      await onChanged?.call();
      return null;
    } on ApiException catch (e) {
      return 'Nothing was deleted: ${e.message}';
    }
  }

  /// Adds an existing tag, or creates [newName] and adds it, to one file.
  Future<String?> addTag(WorkspaceFile file,
      {String? tagId, String? newName}) async {
    try {
      var id = tagId;
      if (id == null) {
        final name = (newName ?? '').trim();
        if (name.isEmpty) return 'Enter a tag name.';
        if (name.length > 64) return 'Tag names are at most 64 characters.';
        id = (await repository.createTag(name)).id;
      }
      _replace(await repository.tagFile(file.id, [id]));
      await loadOrganize();
      return null;
    } on ApiException catch (e) {
      return e.isPermissionDenied
          ? 'Tagging files needs a member or owner role in this workspace.'
          : e.message;
    }
  }

  Future<String?> removeTag(WorkspaceFile file, FileTag tag) async {
    try {
      _replace(await repository.untagFile(file.id, tag.id));
      await loadOrganize();
      return null;
    } on ApiException catch (e) {
      return e.message;
    }
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
          query: query,
          sort: sort,
          cursor: items.last.id,
          folder: searching ? everyFolder : folder,
          tag: tagFilter);
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
          folderId: searching ? null : folder,
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
