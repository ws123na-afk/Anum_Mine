import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import 'calendar_models.dart';

/// Connecting, choosing and disconnecting Google and Microsoft calendars on
/// the phone (docs/calendar.md#connecting-on-the-phone). Owners only; the
/// server checks it again on every request.
///
/// Connecting uses the same OAuth flow as the web: the API makes the
/// provider's address (with PKCE; the client secret never leaves the
/// server), the phone opens it in the system browser, the provider returns
/// to the API, which hands the browser back to the app through its own
/// scheme. The app then finishes the request it started.
abstract interface class CalendarConnectionsRepository {
  /// Providers, connections and whether this person may change them.
  Future<CalendarSyncStatus> setup();

  /// Starts a connection; returns the provider's sign-in address and the
  /// request to finish afterwards.
  Future<ConnectStart> start(String provider);

  /// Finishes a request this person started. A 409 means the sign-in has
  /// not come back yet.
  Future<SyncConnection> complete(String request);
  Future<SyncConnection> syncNow(String connectionId);

  /// Disconnects; returns the server's sentence about what happened.
  Future<String> disconnect(String connectionId);

  /// The account's calendars, fresh from the provider, and which sync.
  Future<CalendarChoices> calendars(String connectionId);

  /// Chooses the calendars that sync besides the primary one.
  Future<CalendarChoices> choose(String connectionId, List<String> selected);
}

class ConnectStart {
  const ConnectStart(this.authorizationUrl, this.request);
  final Uri authorizationUrl;
  final String request;
}

class CalendarChoices {
  const CalendarChoices(this.calendars, {this.maxExtra = 10});
  final List<CalendarChoice> calendars;
  final int maxExtra;

  factory CalendarChoices.fromJson(JsonReader j) =>
      CalendarChoices(j.objects('calendars', CalendarChoice.fromJson),
          maxExtra: j.optInt('max_extra') ?? 10);
}

class ApiCalendarConnectionsRepository
    implements CalendarConnectionsRepository {
  const ApiCalendarConnectionsRepository(this.api);
  final AnumApiClient api;

  static const _base = '/api/v1/calendar/sync';
  static String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<CalendarSyncStatus> setup() async {
    final value = JsonReader(await api.request('GET', _base));
    return CalendarSyncStatus(
        value.objects('connections', SyncConnection.fromJson),
        canManage: value.optBool('can_manage') ?? false,
        providers: value.optObjects('providers', SyncProviderSetup.fromJson));
  }

  @override
  Future<ConnectStart> start(String provider) async {
    final value = JsonReader(await api.request(
        'POST', '$_base/oauth/${_id(provider)}/start',
        body: {'client': 'phone'}));
    return ConnectStart(
        Uri.parse(value.string('authorization_url')), value.string('request'));
  }

  @override
  Future<SyncConnection> complete(String request) async =>
      SyncConnection.fromJson(JsonReader(await api.request(
          'POST', '$_base/oauth/complete',
          body: {'connection': request})));

  @override
  Future<SyncConnection> syncNow(String connectionId) async =>
      SyncConnection.fromJson(JsonReader(await api.request(
              'POST', '$_base/connections/${_id(connectionId)}/sync'))
          .object('connection'));

  @override
  Future<String> disconnect(String connectionId) async =>
      JsonReader(await api.request(
              'DELETE', '$_base/connections/${_id(connectionId)}'))
          .optString('detail') ??
      '';

  @override
  Future<CalendarChoices> calendars(String connectionId) async =>
      CalendarChoices.fromJson(JsonReader(await api.request(
          'GET', '$_base/connections/${_id(connectionId)}/calendars')));

  @override
  Future<CalendarChoices> choose(
          String connectionId, List<String> selected) async =>
      CalendarChoices.fromJson(JsonReader(await api.request(
          'PUT', '$_base/connections/${_id(connectionId)}/calendars',
          body: {'selected': selected})));
}

enum ConnectionsPhase { loading, ready, error, denied }

/// Where a connection started on this phone is.
enum ConnectStep { idle, opening, waiting, finishing }

String _describe(Object e) => e is ApiException ? e.readable : readableError(e);

class CalendarConnectionsController extends ChangeNotifier {
  CalendarConnectionsController(this.repository, {required this.openUrl});

  final CalendarConnectionsRepository repository;

  /// Opens the provider's sign-in in the system browser; false when it
  /// could not. Replaced in tests.
  final Future<bool> Function(Uri uri) openUrl;

  ConnectionsPhase phase = ConnectionsPhase.loading;
  String? error;
  CalendarSyncStatus? status;

  /// The action in progress: `sync:ID`, `disconnect:ID`.
  String? busy;

  /// What to say after an action: a message key and its values
  /// (`calconn.raw` carries the server's own sentence).
  ({String key, Map<String, Object> args})? notice;
  bool noticeIsError = false;

  ConnectStep step = ConnectStep.idle;
  String? connectingProvider;
  String? _request;

  /// Why the last connection attempt stopped, said on the screen.
  String? connectError;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }

  void clearNotice() => notice = null;

  Future<void> load() async {
    phase = ConnectionsPhase.loading;
    _notify();
    try {
      status = await repository.setup();
      error = null;
      phase = ConnectionsPhase.ready;
    } on ApiException catch (e) {
      error = e.readable;
      phase = e.statusCode == 403
          ? ConnectionsPhase.denied
          : ConnectionsPhase.error;
    } on Object catch (e) {
      error = _describe(e);
      phase = ConnectionsPhase.error;
    }
    _notify();
  }

  /// Opens the provider's sign-in for [provider]; the screen finishes it
  /// when the app comes back (or when the person says they are done).
  Future<void> connect(SyncProviderSetup provider) async {
    if (step != ConnectStep.idle) return;
    step = ConnectStep.opening;
    connectingProvider = provider.name;
    connectError = null;
    _notify();
    try {
      final started = await repository.start(provider.provider);
      _request = started.request;
      final opened = await openUrl(started.authorizationUrl);
      if (!opened) {
        _request = null;
        step = ConnectStep.idle;
        connectError = 'calconn.browserFailed';
      } else {
        step = ConnectStep.waiting;
      }
    } on Object catch (e) {
      _request = null;
      step = ConnectStep.idle;
      connectError = _describe(e);
    }
    _notify();
  }

  /// Finishes the request this phone started. [auto] is the app coming
  /// back to the front: a sign-in not finished yet then keeps waiting
  /// quietly instead of saying so.
  Future<SyncConnection?> finish({bool auto = false}) async {
    final request = _request;
    if (request == null || step == ConnectStep.finishing) return null;
    step = ConnectStep.finishing;
    connectError = null;
    _notify();
    try {
      final connection = await repository.complete(request);
      _request = null;
      step = ConnectStep.idle;
      connectingProvider = null;
      notice = (key: 'calconn.connected', args: {'email': connection.email});
      noticeIsError = false;
      await load();
      return connection;
    } on ApiException catch (e) {
      if (e.statusCode == 409) {
        step = ConnectStep.waiting;
        if (!auto) connectError = e.readable;
      } else {
        // Refused, expired, used or failed at the provider: start again.
        _request = null;
        step = ConnectStep.idle;
        connectError = e.readable;
      }
    } on Object catch (e) {
      step = ConnectStep.waiting;
      if (!auto) connectError = _describe(e);
    }
    _notify();
    return null;
  }

  void cancelConnect() {
    _request = null;
    step = ConnectStep.idle;
    connectingProvider = null;
    connectError = null;
    _notify();
  }

  Future<void> _run(String key, Future<void> Function() work) async {
    busy = key;
    _notify();
    try {
      await work();
    } on Object catch (e) {
      notice = (key: 'calconn.raw', args: {'text': _describe(e)});
      noticeIsError = true;
    } finally {
      busy = null;
      _notify();
    }
  }

  Future<void> syncNow(SyncConnection connection) => _run(
        'sync:${connection.id}',
        () async {
          final after = await repository.syncNow(connection.id);
          notice = after.healthy
              ? (key: 'calsync.synced', args: {'email': connection.email})
              : (key: 'calconn.raw', args: {'text': after.lastError ?? ''});
          noticeIsError = !after.healthy;
          await load();
        },
      );

  Future<void> disconnect(SyncConnection connection) => _run(
        'disconnect:${connection.id}',
        () async {
          final detail = await repository.disconnect(connection.id);
          notice = detail.isEmpty
              ? (key: 'calconn.disconnected', args: {'email': connection.email})
              : (key: 'calconn.raw', args: {'text': detail});
          noticeIsError = false;
          await load();
        },
      );
}
