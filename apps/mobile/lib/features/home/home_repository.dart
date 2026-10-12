import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'home_models.dart';

/// Home over the API (docs/home.md): one summary call, the caller's own pins
/// and quick commands, and the proactive insights with dismiss and snooze.
abstract interface class HomeRepository {
  Future<HomeSummary> summary();
  Future<String?> setDisplayName(String? name);

  Future<List<HomePin>> pins();
  Future<HomePin> addPin(
      {required PinKind kind,
      required String label,
      required String target,
      String? icon});
  Future<HomePin> updatePin(String id, {String? label, String? target});
  Future<void> removePin(String id);
  Future<List<HomePin>> reorderPins(List<String> ids);

  /// The caller's workspaces, to pin one of them.
  Future<List<PinnableWorkspace>> workspaces();

  Future<CommandList> commands();
  Future<QuickCommand> addCommand(
      {required CommandMode mode,
      required String label,
      required String prompt});
  Future<QuickCommand> updateCommand(String id,
      {CommandMode? mode, String? label, String? prompt});
  Future<void> removeCommand(String id);

  Future<InsightList> insights({bool includeHidden = false});
  Future<Insight> dismiss(String key);
  Future<Insight> snooze(String key, {int hours = 24});
  Future<void> restore(String key);
}

class ApiHomeRepository implements HomeRepository {
  const ApiHomeRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/home';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<HomeSummary> summary() async => HomeSummary.fromJson(
      JsonReader(await api.request('GET', '$_base/summary')));

  @override
  Future<String?> setDisplayName(String? name) async => JsonReader(await api
          .request('PUT', '$_base/profile', body: {'display_name': name}))
      .optString('display_name');

  @override
  Future<List<HomePin>> pins() async =>
      JsonReader(await api.request('GET', '$_base/pins'))
          .optObjects('data', HomePin.fromJson);

  @override
  Future<HomePin> addPin(
          {required PinKind kind,
          required String label,
          required String target,
          String? icon}) async =>
      HomePin.fromJson(
          JsonReader(await api.request('POST', '$_base/pins', body: {
        'kind': kind.name,
        'label': label,
        'target': target,
        if (icon != null) 'icon': icon,
      })));

  @override
  Future<HomePin> updatePin(String id, {String? label, String? target}) async =>
      HomePin.fromJson(JsonReader(
          await api.request('PATCH', '$_base/pins/${_id(id)}', body: {
        if (label != null) 'label': label,
        if (target != null) 'target': target,
      })));

  @override
  Future<void> removePin(String id) async {
    await api.request('DELETE', '$_base/pins/${_id(id)}');
  }

  @override
  Future<List<HomePin>> reorderPins(List<String> ids) async => JsonReader(
          await api.request('PUT', '$_base/pins/order', body: {'ids': ids}))
      .optObjects('data', HomePin.fromJson);

  @override
  Future<List<PinnableWorkspace>> workspaces() async =>
      JsonReader(await api.request('GET', '/api/v1/me/workspace-memberships'))
          .optObjects(
              'data',
              (r) => PinnableWorkspace(
                  r.string('workspace_id'), r.optString('workspace_name')));

  @override
  Future<CommandList> commands() async => CommandList.fromJson(
      JsonReader(await api.request('GET', '$_base/commands')));

  // The user's own quick commands live in the command module
  // (docs/command.md); Home lists them with the built-in ones.
  static const _quick = '/api/v1/quick-commands';

  @override
  Future<QuickCommand> addCommand(
          {required CommandMode mode,
          required String label,
          required String prompt}) async =>
      QuickCommand.fromQuickCommandsApi(JsonReader(await api.request(
          'POST', _quick,
          body: {'category': mode.name, 'title': label, 'text': prompt})));

  @override
  Future<QuickCommand> updateCommand(String id,
          {CommandMode? mode, String? label, String? prompt}) async =>
      QuickCommand.fromQuickCommandsApi(
          JsonReader(await api.request('PUT', '$_quick/${_id(id)}', body: {
        if (mode != null) 'category': mode.name,
        if (label != null) 'title': label,
        if (prompt != null) 'text': prompt,
      })));

  @override
  Future<void> removeCommand(String id) async {
    await api.request('DELETE', '$_quick/${_id(id)}');
  }

  @override
  Future<InsightList> insights({bool includeHidden = false}) async =>
      InsightList.fromJson(JsonReader(await api.request('GET',
          '$_base/insights${includeHidden ? '?include_hidden=true' : ''}')));

  @override
  Future<Insight> dismiss(String key) async => Insight.fromJson(JsonReader(
      await api.request('POST', '$_base/insights/${_id(key)}/dismiss')));

  @override
  Future<Insight> snooze(String key, {int hours = 24}) async =>
      Insight.fromJson(JsonReader(await api.request(
          'POST', '$_base/insights/${_id(key)}/snooze',
          body: {'hours': hours})));

  @override
  Future<void> restore(String key) async {
    await api.request('DELETE', '$_base/insights/${_id(key)}/state');
  }
}

/// Home without an API client (a workspace repository that is not the API's,
/// in tests): every call says Home is unavailable, so the screen shows its
/// error state instead of invented data.
class UnavailableHomeRepository implements HomeRepository {
  const UnavailableHomeRepository();

  static Never _unavailable() =>
      throw const ApiException(503, 'Home is not available in this build.');

  @override
  Future<HomeSummary> summary() async => _unavailable();
  @override
  Future<String?> setDisplayName(String? name) async => _unavailable();
  @override
  Future<List<HomePin>> pins() async => _unavailable();
  @override
  Future<HomePin> addPin(
          {required PinKind kind,
          required String label,
          required String target,
          String? icon}) async =>
      _unavailable();
  @override
  Future<HomePin> updatePin(String id, {String? label, String? target}) async =>
      _unavailable();
  @override
  Future<void> removePin(String id) async => _unavailable();
  @override
  Future<List<HomePin>> reorderPins(List<String> ids) async => _unavailable();
  @override
  Future<List<PinnableWorkspace>> workspaces() async => _unavailable();
  @override
  Future<CommandList> commands() async => _unavailable();
  @override
  Future<QuickCommand> addCommand(
          {required CommandMode mode,
          required String label,
          required String prompt}) async =>
      _unavailable();
  @override
  Future<QuickCommand> updateCommand(String id,
          {CommandMode? mode, String? label, String? prompt}) async =>
      _unavailable();
  @override
  Future<void> removeCommand(String id) async => _unavailable();
  @override
  Future<InsightList> insights({bool includeHidden = false}) async =>
      _unavailable();
  @override
  Future<Insight> dismiss(String key) async => _unavailable();
  @override
  Future<Insight> snooze(String key, {int hours = 24}) async => _unavailable();
  @override
  Future<void> restore(String key) async => _unavailable();
}
