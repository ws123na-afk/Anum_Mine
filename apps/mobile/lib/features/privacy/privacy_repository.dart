import 'dart:convert';

import '../../data/api_client.dart';
import 'privacy_models.dart';

/// Saves the export file on the device (the file picker's save dialog in the
/// app, a fake in tests).
abstract interface class PrivacyExportSaver {
  /// Returns false when the person cancelled the save dialog.
  Future<bool> save(String fileName, List<int> bytes);
}

/// The Privacy screen's data (docs/privacy.md).
abstract interface class PrivacyRepository {
  Future<PrivacySummary> summary();

  /// Downloads the caller's data and saves it; returns the saved size in
  /// bytes, or null when the save was cancelled.
  Future<int?> exportMyData();
  Future<PrivacyDeleteResult> deleteMyData(String confirmation);
}

class ApiPrivacyRepository implements PrivacyRepository {
  ApiPrivacyRepository(this.api,
      {required this.saver, DateTime Function()? clock})
      : _clock = clock ?? DateTime.now;
  final AnumApiClient api;
  final PrivacyExportSaver saver;
  final DateTime Function() _clock;

  @override
  Future<PrivacySummary> summary() async =>
      PrivacySummary.fromJson(await api.request('GET', '/api/v1/privacy'));

  @override
  Future<int?> exportMyData() async {
    final body = await api.request('GET', '/api/v1/privacy/export');
    final bytes = utf8.encode(const JsonEncoder.withIndent(' ').convert(body));
    final day = _clock().toUtc().toIso8601String().substring(0, 10);
    return await saver.save('anum-my-data-$day.json', bytes)
        ? bytes.length
        : null;
  }

  @override
  Future<PrivacyDeleteResult> deleteMyData(String confirmation) async =>
      PrivacyDeleteResult.fromJson(await api.request(
          'POST', '/api/v1/privacy/delete',
          body: {'confirm': confirmation.trim()}));
}
