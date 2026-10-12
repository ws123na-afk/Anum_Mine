import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'integrations_models.dart';

/// `/api/v1/integrations` (docs/tools-and-integrations.md). Reading needs
/// `integration:read` (every role); turning one on or off, testing it and
/// storing or removing the workspace credential are for workspace owners.
/// The app never talks to a provider itself and never shows a stored secret.
abstract interface class IntegrationsRepository {
  Future<({String? userId, String? role})> viewer();
  Future<List<Integration>> list();
  Future<void> setEnabled(String id, bool enabled);
  Future<Integration> test(String id);
  Future<void> storeCredential(String id, String secret);
  Future<void> removeCredential(String id);
}

class ApiIntegrationsRepository implements IntegrationsRepository {
  const ApiIntegrationsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/integrations';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<({String? userId, String? role})> viewer() async {
    final session = await api.sessions.read();
    try {
      final value = JsonReader(
          await api.request('GET', '/api/v1/workspace-memberships/current'));
      return (
        userId: value.optString('user_id') ?? session?.context.userId,
        role: value['active'] == false ? 'viewer' : value.optString('role'),
      );
    } on ApiException {
      return (userId: session?.context.userId, role: null);
    }
  }

  @override
  Future<List<Integration>> list() async =>
      JsonReader(await api.request('GET', _base))
          .optObjects('data', Integration.fromJson);

  @override
  Future<void> setEnabled(String id, bool enabled) async {
    // The configuration is replaced as a whole: keep the saved endpoint note.
    final current =
        JsonReader(await api.request('GET', '$_base/${_id(id)}/configuration'));
    final endpoint = current.optString('endpoint');
    await api.request('PUT', '$_base/${_id(id)}/configuration', body: {
      'enabled': enabled,
      if (endpoint != null) 'endpoint': endpoint,
    });
  }

  @override
  Future<Integration> test(String id) async => Integration.fromJson(
      JsonReader(await api.request('POST', '$_base/${_id(id)}/test')));

  @override
  Future<void> storeCredential(String id, String secret) async {
    await api.request('PUT', '$_base/${_id(id)}/credential',
        body: {'secret': secret});
  }

  @override
  Future<void> removeCredential(String id) async {
    await api.request('DELETE', '$_base/${_id(id)}/credential');
  }
}
