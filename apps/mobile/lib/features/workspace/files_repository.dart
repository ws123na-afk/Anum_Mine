import 'dart:async';
import 'dart:typed_data';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'files_models.dart';
import 'workspace_models.dart';

/// Cancels one upload: the transfer closes its connection and throws
/// [UploadCancelledException]; nothing is stored.
class UploadCancellation {
  final _cancelled = Completer<void>();
  bool get isCancelled => _cancelled.isCompleted;
  Future<void> get whenCancelled => _cancelled.future;
  void cancel() {
    if (!_cancelled.isCompleted) _cancelled.complete();
  }
}

class UploadCancelledException implements Exception {
  const UploadCancelledException();
  @override
  String toString() => 'Upload cancelled; nothing was stored.';
}

/// Bytes on the wire (docs/files.md): a streamed upload with progress and
/// cancel, the bytes of a file for a preview, and a download through a
/// short-lived link.
abstract interface class FileStreamTransfer {
  Future<JsonMap> uploadStream(
    String path, {
    required String name,
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
    String? folderId,
  });
  Future<Uint8List> bytes(String fileId);
  Future<void> saveCopy(WorkspaceFile file);
}

abstract interface class FilesRepository {
  Future<List<WorkspaceFile>> list(
      {String query,
      String sort,
      String? cursor,
      int limit,
      String? folder,
      String? tag});
  Future<FileUsage> usage();

  // Folders, tags and bulk actions (docs/files.md#folders, #tags, #bulk-actions).
  Future<List<FileFolder>> folders();
  Future<List<FileTag>> tags();
  Future<FileFolder> createFolder(String name, String? parentId);
  Future<int> bulkMove(List<String> fileIds, String? folderId);
  Future<int> bulkDelete(List<String> fileIds);
  Future<WorkspaceFile> tagFile(String id, List<String> tagIds);
  Future<WorkspaceFile> untagFile(String id, String tagId);
  Future<FileTag> createTag(String name);

  // Folder and tag management (docs/files.md#folders, #tags): the creator or
  // an owner; the API answers 403 to anyone else.
  Future<FileFolder> renameFolder(String id, String name);

  /// Moves a folder under [parentId] (null: the root).
  Future<FileFolder> moveFolder(String id, String? parentId);

  /// [contents] is `refuse` (only an empty folder), `move_up` or `delete`.
  Future<void> deleteFolder(String id, String contents);
  Future<FileTag> renameTag(String id, String name);
  Future<void> deleteTag(String id);

  /// The files as one zip through the system save dialog
  /// (`POST /files/bulk/download`); false when the dialog was closed.
  Future<bool> downloadZip(List<String> fileIds, String dialogTitle);
  Future<WorkspaceFile> upload(String path,
      {required String name,
      required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation,
      String? folderId});
  Future<WorkspaceFile> rename(String id, String name);
  Future<WorkspaceFile> reindex(String id);
  Future<void> delete(String id);
  Future<FilePreviewText> previewText(String id);
  Future<Uint8List> bytes(String id);
  Future<void> download(WorkspaceFile file);
}

class ApiFilesRepository implements FilesRepository {
  const ApiFilesRepository(this.api, {required this.transfer});
  final AnumApiClient api;
  final FileStreamTransfer transfer;

  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<List<WorkspaceFile>> list(
      {String query = '',
      String sort = 'created_at:desc',
      String? cursor,
      int limit = filesPageSize,
      String? folder = everyFolder,
      String? tag}) async {
    final body = await api.request(
        'GET',
        filesListPath(
            query: query,
            sort: sort,
            cursor: cursor,
            limit: limit,
            folder: folder,
            tag: tag));
    return JsonReader(body).optObjects('data', parseWorkspaceFile);
  }

  @override
  Future<List<FileFolder>> folders() async =>
      JsonReader(await api.request('GET', '/api/v1/files/folders'))
          .optObjects('items', parseFileFolder);

  @override
  Future<List<FileTag>> tags() async =>
      JsonReader(await api.request('GET', '/api/v1/files/tags'))
          .optObjects('items', parseFileTag);

  @override
  Future<FileFolder> createFolder(String name, String? parentId) async =>
      parseFileFolder(JsonReader(await api.request(
          'POST', '/api/v1/files/folders',
          body: {'name': name, 'parent_id': parentId})));

  @override
  Future<int> bulkMove(List<String> fileIds, String? folderId) async =>
      JsonReader(await api.request('POST', '/api/v1/files/bulk/move',
              body: {'file_ids': fileIds, 'folder_id': folderId}))
          .integer('count');

  @override
  Future<int> bulkDelete(List<String> fileIds) async =>
      JsonReader(await api.request('POST', '/api/v1/files/bulk/delete',
              body: {'file_ids': fileIds}))
          .integer('count');

  @override
  Future<WorkspaceFile> tagFile(String id, List<String> tagIds) async =>
      parseWorkspaceFile(JsonReader(await api.request(
          'POST', '/api/v1/files/${_id(id)}/tags',
          body: {'tag_ids': tagIds})));

  @override
  Future<WorkspaceFile> untagFile(String id, String tagId) async =>
      parseWorkspaceFile(JsonReader(await api.request(
          'DELETE', '/api/v1/files/${_id(id)}/tags/${_id(tagId)}')));

  @override
  Future<FileTag> createTag(String name) async => parseFileTag(JsonReader(
      await api.request('POST', '/api/v1/files/tags', body: {'name': name})));

  @override
  Future<FileFolder> renameFolder(String id, String name) async =>
      parseFileFolder(JsonReader(await api.request(
          'PATCH', '/api/v1/files/folders/${_id(id)}',
          body: {'name': name})));

  @override
  Future<FileFolder> moveFolder(String id, String? parentId) async =>
      parseFileFolder(JsonReader(await api.request(
          'PATCH', '/api/v1/files/folders/${_id(id)}',
          body: {'parent_id': parentId})));

  @override
  Future<void> deleteFolder(String id, String contents) async {
    await api.request('DELETE',
        '/api/v1/files/folders/${_id(id)}?contents=${Uri.encodeQueryComponent(contents)}');
  }

  @override
  Future<FileTag> renameTag(String id, String name) async => parseFileTag(
      JsonReader(await api.request('PATCH', '/api/v1/files/tags/${_id(id)}',
          body: {'name': name})));

  @override
  Future<void> deleteTag(String id) async {
    await api.request('DELETE', '/api/v1/files/tags/${_id(id)}');
  }

  @override
  Future<bool> downloadZip(List<String> fileIds, String dialogTitle) =>
      api.download('POST', '/api/v1/files/bulk/download',
          body: {'file_ids': fileIds},
          fileName: 'files.zip',
          dialogTitle: dialogTitle);

  @override
  Future<FileUsage> usage() async => FileUsage.fromJson(
      JsonReader(await api.request('GET', '/api/v1/files/usage')));

  @override
  Future<WorkspaceFile> upload(String path,
          {required String name,
          required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation,
          String? folderId}) async =>
      parseWorkspaceFile(JsonReader(await transfer.uploadStream(path,
          name: name,
          onProgress: onProgress,
          cancellation: cancellation,
          folderId: folderId)));

  @override
  Future<WorkspaceFile> rename(String id, String name) async =>
      parseWorkspaceFile(JsonReader(await api
          .request('PATCH', '/api/v1/files/${_id(id)}', body: {'name': name})));

  @override
  Future<WorkspaceFile> reindex(String id) async =>
      parseWorkspaceFile(JsonReader(
          await api.request('POST', '/api/v1/files/${_id(id)}/reindex')));

  @override
  Future<void> delete(String id) async {
    await api.request('DELETE', '/api/v1/files/${_id(id)}');
  }

  @override
  Future<FilePreviewText> previewText(String id) async {
    final j = JsonReader(
        await api.request('GET', '/api/v1/files/${_id(id)}/preview'));
    return FilePreviewText(
        text: j.string('text'), truncated: j.optBool('truncated') ?? false);
  }

  @override
  Future<Uint8List> bytes(String id) => transfer.bytes(id);

  @override
  Future<void> download(WorkspaceFile file) => transfer.saveCopy(file);
}
