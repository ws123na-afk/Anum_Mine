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
  });
  Future<Uint8List> bytes(String fileId);
  Future<void> saveCopy(WorkspaceFile file);
}

abstract interface class FilesRepository {
  Future<List<WorkspaceFile>> list(
      {String query, String sort, String? cursor, int limit});
  Future<FileUsage> usage();
  Future<WorkspaceFile> upload(String path,
      {required String name,
      required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation});
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
      int limit = filesPageSize}) async {
    final body = await api.request('GET',
        filesListPath(query: query, sort: sort, cursor: cursor, limit: limit));
    return JsonReader(body).optObjects('data', parseWorkspaceFile);
  }

  @override
  Future<FileUsage> usage() async => FileUsage.fromJson(
      JsonReader(await api.request('GET', '/api/v1/files/usage')));

  @override
  Future<WorkspaceFile> upload(String path,
          {required String name,
          required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation}) async =>
      parseWorkspaceFile(JsonReader(await transfer.uploadStream(path,
          name: name, onProgress: onProgress, cancellation: cancellation)));

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
