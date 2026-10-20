import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'privacy_models.dart';
import 'privacy_repository.dart';
import 'privacy_strings.dart';

enum PrivacyPhase { loading, ready, permissionDenied, offline, error }

final privacyPermissionMessage = PrivacyStrings.english.t('permission');

/// Settings -> Privacy: the summary plus "Download my data" and "Delete my
/// data" (the in-app deletion path the app stores require).
class PrivacyController extends ChangeNotifier {
  PrivacyController(this.repository);
  final PrivacyRepository repository;

  /// The language of the sentences this controller writes; the screen sets
  /// it from the app's locale.
  PrivacyStrings strings = PrivacyStrings.english;

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
      loadError = describe(error, 'load', strings);
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
          ? strings.t('export.cancelled')
          : strings.t('export.saved', {'size': formatBytes(bytes)});
    } on Object catch (error) {
      exportFailed = true;
      exportMessage = describe(error, 'export', strings);
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
      deleteMessage =
          '${result.sentenceIn(strings)} ${result.accountNote}'.trim();
      ok = true;
      summary = await repository.summary();
    } on Object catch (error) {
      if (!ok) {
        deleteFailed = true;
        deleteMessage = describe(error, 'delete', strings);
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

  /// What failed and how to fix it, in [s]'s language.
  static String describe(Object error, String action,
      [PrivacyStrings s = PrivacyStrings.english]) {
    if (error is ApiException) {
      if (error.isPermissionDenied) return s.t('permission');
      if (error.statusCode == 401) return s.t('error.signIn');
      if (error.statusCode == 413 ||
          (error.statusCode == 422 && action == 'delete')) {
        return error.message;
      }
    }
    if (_offline(error)) return s.t('error.offline');
    final what = s.t(switch (action) {
      'load' => 'error.load',
      'export' => 'error.export',
      _ => 'error.delete',
    });
    final reason = error is ApiException ? error.message : error.toString();
    final fix = s.t(action == 'delete' ? 'error.fixDelete' : 'error.fixOther');
    return s.t('error.generic', {'what': what, 'reason': reason, 'fix': fix});
  }
}
