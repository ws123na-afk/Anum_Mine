import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'privacy_models.dart';
import 'privacy_repository.dart';

enum PrivacyPhase { loading, ready, permissionDenied, offline, error }

const privacyPermissionMessage =
    'Only active members of this workspace can see or change their data here. '
    'Ask a workspace owner to restore your membership, or switch to a workspace you belong to.';

/// Settings -> Privacy: the summary plus "Download my data" and "Delete my
/// data" (the in-app deletion path the app stores require).
class PrivacyController extends ChangeNotifier {
  PrivacyController(this.repository);
  final PrivacyRepository repository;

  PrivacyPhase phase = PrivacyPhase.loading;
  PrivacySummary? summary;
  String? loadError;
  bool exporting = false, deleting = false;
  String? exportMessage, deleteMessage;
  bool exportFailed = false, deleteFailed = false;

  Future<void> load() async {
    phase = PrivacyPhase.loading;
    notifyListeners();
    try {
      summary = await repository.summary();
      phase = PrivacyPhase.ready;
    } on Object catch (error) {
      loadError = describe(error, 'load');
      phase = error is ApiException && error.isPermissionDenied
          ? PrivacyPhase.permissionDenied
          : _offline(error)
              ? PrivacyPhase.offline
              : PrivacyPhase.error;
    }
    notifyListeners();
  }

  Future<void> exportMyData() async {
    exporting = true;
    exportMessage = null;
    notifyListeners();
    try {
      final bytes = await repository.exportMyData();
      exportFailed = false;
      exportMessage = bytes == null
          ? 'Download cancelled. Nothing was saved.'
          : 'Saved your data (${formatBytes(bytes)}).';
    } on Object catch (error) {
      exportFailed = true;
      exportMessage = describe(error, 'export');
    }
    exporting = false;
    notifyListeners();
  }

  /// Deletes after the typed phrase matched; returns whether it succeeded.
  Future<bool> deleteMyData(String typed) async {
    deleting = true;
    deleteMessage = null;
    notifyListeners();
    var ok = false;
    try {
      final result = await repository.deleteMyData(typed);
      deleteFailed = false;
      deleteMessage = '${result.sentence} ${result.accountNote}'.trim();
      ok = true;
      summary = await repository.summary();
    } on Object catch (error) {
      if (!ok) {
        deleteFailed = true;
        deleteMessage = describe(error, 'delete');
      }
    }
    deleting = false;
    notifyListeners();
    return ok;
  }

  static bool _offline(Object error) {
    final text = error.toString().toLowerCase();
    return text.contains('socket') || text.contains('network');
  }

  /// What failed and how to fix it.
  static String describe(Object error, String action) {
    if (error is ApiException) {
      if (error.isPermissionDenied) return privacyPermissionMessage;
      if (error.statusCode == 401) {
        return 'Your sign-in expired. Sign in again, then retry.';
      }
      if (error.statusCode == 413 ||
          (error.statusCode == 422 && action == 'delete')) {
        return error.message;
      }
    }
    if (_offline(error)) {
      return 'You are offline. Reconnect, then try again.';
    }
    final what = switch (action) {
      'load' => 'Could not load your privacy summary',
      'export' => 'Could not prepare your download',
      _ => 'Could not delete your data',
    };
    final reason = error is ApiException ? error.message : error.toString();
    final fix = action == 'delete'
        ? 'Reload to see what is still stored, then try again.'
        : 'Check your connection and try again.';
    return '$what: $reason. $fix';
  }
}
