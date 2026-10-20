import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../workspace/files_controller.dart' show FileUpload;
import '../workspace/files_models.dart';
import '../workspace/files_repository.dart';
import '../workspace/workspace_models.dart';
import 'file_strings.dart';
import 'file_versions_models.dart';
import 'file_versions_repository.dart';

enum VersionsPhase { loading, ready, denied, error }

/// The versions of one file (docs/files.md#versions): the current version,
/// one page of older versions at a time, and upload, restore, download and
/// delete for those allowed. [onFileChanged] hands the updated file to the
/// Files tab; [onUsageChanged] refreshes the storage figures, because every
/// version counts against the workspace quota.
class FileVersionsController extends ChangeNotifier {
  FileVersionsController(
    this.repository,
    this.file, {
    this.usageOf,
    this.onFileChanged,
    this.onUsageChanged,
  });

  final FileVersionsRepository repository;
  WorkspaceFile file;

  /// The workspace usage as the Files tab holds it now (read at each upload, so a
  /// refresh after other changes is never missed).
  final FileUsage? Function()? usageOf;
  final void Function(WorkspaceFile file)? onFileChanged;
  final Future<void> Function()? onUsageChanged;

  VersionsPhase phase = VersionsPhase.loading;
  List<FileVersion> items = const [];
  int total = 0;
  bool paging = false, busy = false;
  String? error, note, loadError;
  FileVersion? working;
  FileUpload? upload;

  bool get canChange => file.canManage;

  Future<void> load(FileStrings s) async {
    if (phase != VersionsPhase.ready) phase = VersionsPhase.loading;
    loadError = null;
    notifyListeners();
    try {
      final page = await repository.versions(file.id);
      items = page.items;
      total = page.total;
      file = page.file;
      phase = VersionsPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? VersionsPhase.denied : VersionsPhase.error;
      loadError = s.t('ver.loadFailed', {'message': e.message});
    } catch (e) {
      phase = VersionsPhase.error;
      loadError = s.t('ver.loadFailed', {'message': '$e'});
    }
    notifyListeners();
  }

  Future<void> loadMore(FileStrings s) async {
    if (paging || items.length >= total) return;
    paging = true;
    error = null;
    notifyListeners();
    try {
      final page = await repository.versions(file.id, offset: items.length);
      items = mergeVersions(items, page.items);
      total = page.total;
    } catch (e) {
      error = s.t('ver.loadMoreFailed', {'message': _message(e)});
    } finally {
      paging = false;
      notifyListeners();
    }
  }

  String _message(Object e) => e is ApiException ? e.message : '$e';

  Future<void> _afterChange(WorkspaceFile updated, FileStrings s) async {
    file = updated;
    onFileChanged?.call(updated);
    await onUsageChanged?.call();
    await load(s);
  }

  /// Uploads the picked file as a new version. Returns whether it worked.
  Future<bool> uploadVersion(
      String path, String name, int size, FileStrings s) async {
    error = null;
    note = null;
    final refusal = versionProblem(name, size, file, usageOf?.call(), s);
    if (refusal != null) {
      error = refusal;
      notifyListeners();
      return false;
    }
    final current = FileUpload('version', name, size, UploadCancellation());
    upload = current;
    busy = true;
    notifyListeners();
    try {
      final updated = await repository.uploadVersion(file.id, path,
          cancellation: current.cancellation, onProgress: (sent, _) {
        current.sent = sent;
        notifyListeners();
      });
      upload = null;
      note = s.t(
          'ver.uploaded', {'version': updated.version, 'name': updated.name});
      await _afterChange(updated, s);
      return true;
    } on UploadCancelledException {
      error = s.t('ver.cancelled');
    } on ApiException catch (e) {
      error = e.isPermissionDenied
          ? s.t('ver.manageDenied')
          : s.t('ver.uploadFailed', {'name': file.name, 'message': e.message});
      if (e.statusCode == 409) await load(s);
    } catch (e) {
      error = s.t('ver.uploadFailed', {'name': file.name, 'message': '$e'});
    } finally {
      upload = null;
      busy = false;
      notifyListeners();
    }
    return false;
  }

  void cancelUpload() => upload?.cancellation.cancel();

  Future<void> restore(FileVersion version, FileStrings s) =>
      _run(version, () async {
        final updated = await repository.restore(file.id, version.id);
        note = s.t('ver.restored',
            {'version': version.version, 'now': updated.version});
        await _afterChange(updated, s);
      },
          (message) => s.t('ver.restoreFailed',
              {'version': version.version, 'message': message}),
          s);

  Future<void> delete(FileVersion version, FileStrings s) =>
      _run(version, () async {
        await repository.delete(file.id, version.id);
        note = s.t('ver.deleted', {'version': version.version});
        await onUsageChanged?.call();
        await load(s);
      },
          (message) => s.t('ver.deleteFailed',
              {'version': version.version, 'message': message}),
          s);

  Future<void> download(FileVersion version, FileStrings s) =>
      _run(version, () async {
        await repository.download(file, version);
      },
          (message) => s.t('ver.downloadFailed',
              {'version': version.version, 'message': message}),
          s);

  Future<void> _run(FileVersion version, Future<void> Function() work,
      String Function(String message) failed, FileStrings s) async {
    busy = true;
    working = version;
    error = null;
    note = null;
    notifyListeners();
    try {
      await work();
    } on ApiException catch (e) {
      error =
          e.isPermissionDenied ? s.t('ver.manageDenied') : failed(e.message);
      if (e.statusCode == 404 || e.statusCode == 409) await load(s);
    } catch (e) {
      error = failed('$e');
    } finally {
      busy = false;
      working = null;
      notifyListeners();
    }
  }
}
