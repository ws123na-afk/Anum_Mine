import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../workspace/files_models.dart';
import '../workspace/files_repository.dart';
import '../workspace/workspace_models.dart';
import 'file_versions_models.dart';

/// Bytes on the wire for versions (docs/files.md#versions): a streamed
/// upload of the new content with progress and cancel, and a download of an
/// older version saved with the system dialog.
abstract interface class FileVersionTransfer {
  Future<JsonMap> uploadVersion(
    String fileId,
    String path, {
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  });
  Future<void> saveVersion(WorkspaceFile file, FileVersion version);
}

abstract interface class FileVersionsRepository {
  Future<FileVersionPage> versions(String fileId, {int offset});
  Future<WorkspaceFile> uploadVersion(String fileId, String path,
      {required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation});
  Future<WorkspaceFile> restore(String fileId, String versionId);
  Future<void> delete(String fileId, String versionId);
  Future<void> download(WorkspaceFile file, FileVersion version);
  Future<StorageReport> storage();
}

class ApiFileVersionsRepository implements FileVersionsRepository {
  const ApiFileVersionsRepository(this.api, {required this.transfer});
  final AnumApiClient api;
  final FileVersionTransfer transfer;

  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<FileVersionPage> versions(String fileId, {int offset = 0}) async =>
      FileVersionPage.fromJson(
          JsonReader(await api.request('GET', versionsPath(fileId, offset))));

  @override
  Future<WorkspaceFile> uploadVersion(String fileId, String path,
          {required void Function(int sent, int total) onProgress,
          required UploadCancellation cancellation}) async =>
      parseWorkspaceFile(JsonReader(await transfer.uploadVersion(fileId, path,
          onProgress: onProgress, cancellation: cancellation)));

  @override
  Future<WorkspaceFile> restore(String fileId, String versionId) async =>
      parseWorkspaceFile(JsonReader(await api.request('POST',
          '/api/v1/files/${_id(fileId)}/versions/${_id(versionId)}/restore')));

  @override
  Future<void> delete(String fileId, String versionId) async {
    await api.request(
        'DELETE', '/api/v1/files/${_id(fileId)}/versions/${_id(versionId)}');
  }

  @override
  Future<void> download(WorkspaceFile file, FileVersion version) =>
      transfer.saveVersion(file, version);

  @override
  Future<StorageReport> storage() async => StorageReport.fromJson(
      JsonReader(await api.request('GET', '/api/v1/files/storage')));
}
