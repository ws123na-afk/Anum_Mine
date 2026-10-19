import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'lifecycle_models.dart';

/// Data export and deletion of the organisation and the current workspace
/// (docs/organisation-lifecycle.md). Every permission check is the API's.
abstract interface class LifecycleRepository {
  Future<LifecycleSummary> summary();
  Future<LifecycleJob> requestExport();

  /// A signed link to a ready export's archive.
  Future<LifecycleExportLink> exportLink(String jobId);
  Future<LifecycleJob> scheduleOrganisationDeletion(String confirm);
  Future<LifecycleJob> cancelOrganisationDeletion();
  Future<LifecycleJob> scheduleWorkspaceDeletion(String confirmName);
  Future<LifecycleJob> cancelWorkspaceDeletion();
}

class ApiLifecycleRepository implements LifecycleRepository {
  ApiLifecycleRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/organization/lifecycle';

  /// Posts and reads the job it answers with (bare, or under `job`).
  Future<LifecycleJob> _job(String path, [Map<String, Object?>? body]) async {
    final json = JsonReader(await api.request('POST', path, body: body));
    return LifecycleJob.fromJson(
        json.optObject('job') == null ? json : json.object('job'));
  }

  @override
  Future<LifecycleSummary> summary() async =>
      LifecycleSummary.fromJson(await api.request('GET', _base));

  @override
  Future<LifecycleJob> requestExport() => _job('$_base/exports');

  @override
  Future<LifecycleExportLink> exportLink(String jobId) async {
    final json = JsonReader(await api.request(
        'POST', '$_base/exports/${Uri.encodeComponent(jobId)}/link'));
    // The API may answer with a path; it is resolved against the API address.
    final raw = json.string('url');
    final parsed = Uri.parse(raw);
    final url = parsed.hasScheme
        ? parsed
        : api.baseUri.resolve(raw.replaceFirst(RegExp(r'^/'), ''));
    return LifecycleExportLink(url: url, expiresAt: json.optDate('expires_at'));
  }

  @override
  Future<LifecycleJob> scheduleOrganisationDeletion(String confirm) =>
      _job('$_base/deletion', {'confirm': confirm});

  @override
  Future<LifecycleJob> cancelOrganisationDeletion() =>
      _job('$_base/deletion/cancel');

  @override
  Future<LifecycleJob> scheduleWorkspaceDeletion(String confirmName) => _job(
      '/api/v1/workspaces/current/deletion', {'confirm_name': confirmName});

  @override
  Future<LifecycleJob> cancelWorkspaceDeletion() =>
      _job('/api/v1/workspaces/current/deletion/cancel');
}
