import '../../data/api_client.dart';
import '../../data/api_models.dart';
import 'push_models.dart';

/// Phone push on the API (docs/notifications.md). Every call is the signed-in
/// user's own: the API keys devices by the caller's tenant, workspace and user.
class PushRepository {
  const PushRepository(this.api);
  final AnumApiClient api;

  /// The tenant, workspace and user of the current session, or null when
  /// signed out.
  Future<TenantContext?> currentContext() async {
    final session = await api.sessions.read();
    if (session == null || session.isExpired) return null;
    return session.context;
  }

  Future<PushServerStatus> status() async => PushServerStatus.fromJson(
      await api.request('GET', '/api/v1/push/status'));

  Future<List<PushDevice>> devices() async {
    final value = await api.request('GET', '/api/v1/me/devices');
    return JsonReader(value).optObjects(
        'data', (item) => PushDevice.fromJson(item.json, item.path));
  }

  /// Registers this phone's token (the same token again refreshes its row).
  Future<PushDevice> register(
          {required String platform,
          required String token,
          String? label}) async =>
      PushDevice.fromJson(
          await api.request('POST', '/api/v1/me/devices', body: {
        'platform': platform,
        'token': token,
        if (label != null) 'label': label,
      }));

  Future<void> remove(String deviceId) async {
    await api.request(
        'DELETE', '/api/v1/me/devices/${Uri.encodeComponent(deviceId)}');
  }
}
