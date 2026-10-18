import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../auth/auth_repository.dart';
import 'push_messaging.dart';
import 'push_models.dart';
import 'push_repository.dart';

/// What Settings › Notifications shows about phone push.
enum PushPhase {
  /// Not looked at yet, or being checked.
  checking,

  /// This build has no Firebase configuration (or the platform has no push).
  unavailableInBuild,

  /// The server has no ANUM_FCM_SERVICE_ACCOUNT_JSON.
  offOnServer,

  /// Push works here; the person has not been asked yet.
  notAsked,

  /// The person (or the phone's settings) blocked notifications for ANUM.
  blocked,

  /// This phone is registered for the current workspace.
  on,

  /// Signed out: nothing to register.
  signedOut,

  /// The last step failed; [PushController.error] says what and how to fix it.
  error,
}

/// Phone push on this device (docs/notifications.md): asks for permission
/// only when the person chooses to (Settings › Notifications, or the offer
/// after their first approval decision), registers the token for the signed-in
/// workspace, unregisters it before sign-out and before a workspace switch,
/// and turns taps and foreground pushes into destinations and notices.
class PushController extends ChangeNotifier
    implements WorkspaceSessionListener {
  PushController(
      {required this.repository, required this.messaging, required this.store});

  final PushRepository repository;
  final PushMessaging messaging;
  final PushLocalStore store;

  /// The language the app shows (`en` or `ar`); pushes to this phone use its
  /// text (docs/notifications.md#phone-push). Null leaves the server's default.
  String? Function() languageOf = () => null;

  /// The app's language changed: register again so pushes follow it.
  Future<void> languageChanged() => _reregister();

  PushPhase phase = PushPhase.checking;
  String? error;
  String? notice;
  bool busy = false;
  PushServerStatus? server;

  /// The caller's phones registered in this workspace (from the API).
  List<PushDevice> devices = const [];

  /// This phone's device row in the current workspace, when registered.
  String? thisDeviceId;

  /// Where the person goes when they tap a notice or a push.
  void Function(PushDestination destination)? onOpen;

  /// Shows an in-app notice for a push that arrived in the foreground.
  void Function(PushNotice notice)? onNotice;

  /// A tapped push that arrived before anyone could open it (cold start).
  PushDestination? pendingDestination;

  bool _initialized = false;
  bool _available = false;
  final List<StreamSubscription<Object?>> _subscriptions = [];

  bool get availableInBuild => _available;

  static String scopeOf(TenantContext context) =>
      '${context.tenantId}/${context.userId}/${context.workspaceId}';

  /// Once, at app start: sets up the SDK and listens for pushes. Never asks
  /// for permission.
  Future<void> start() async {
    if (_initialized) return;
    _initialized = true;
    _available = await messaging.initialize();
    if (!_available) {
      phase = PushPhase.unavailableInBuild;
      notifyListeners();
      return;
    }
    _subscriptions
      ..add(messaging.tokenRefreshes.listen((_) => unawaited(_reregister())))
      ..add(messaging.foregroundPushes.listen(_onForeground))
      ..add(messaging.openedPushes.listen(_onOpened));
    final launch = await messaging.launchPush();
    if (launch != null) _onOpened(launch);
  }

  void _onForeground(IncomingPush push) {
    final destination = PushDestination.fromData(push.data);
    onNotice?.call(PushNotice(
        title: push.title.isEmpty ? 'ANUM' : push.title,
        body: push.body,
        destination: destination));
  }

  void _onOpened(IncomingPush push) {
    final destination = PushDestination.fromData(push.data);
    if (destination == null) return;
    final open = onOpen;
    if (open == null) {
      pendingDestination = destination;
    } else {
      open(destination);
    }
  }

  /// The destination waiting since launch, handed out once.
  PushDestination? takePendingDestination() {
    final destination = pendingDestination;
    pendingDestination = null;
    return destination;
  }

  /// Reads the server's status, this phone's permission and the caller's
  /// devices, and registers when push is allowed. Safe to call repeatedly.
  Future<void> refresh() async {
    if (!_initialized) await start();
    if (!_available) return;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final context = await repository.currentContext();
      if (context == null) {
        phase = PushPhase.signedOut;
        devices = const [];
        thisDeviceId = null;
        return;
      }
      server = await repository.status();
      if (!server!.available) {
        phase = PushPhase.offOnServer;
        devices = const [];
        return;
      }
      final permission = await messaging.permission();
      if (permission == PushPermission.granted) {
        await _register(context);
        phase = PushPhase.on;
      } else {
        phase = permission == PushPermission.denied
            ? PushPhase.blocked
            : PushPhase.notAsked;
        thisDeviceId = await store.deviceId(scopeOf(context));
      }
      devices = await repository.devices();
    } on Object catch (caught) {
      phase = PushPhase.error;
      error = _describe(caught, 'Could not check phone push.');
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  /// "Turn on notifications": asks the phone for permission, then registers.
  Future<void> enable() async {
    if (!_initialized) await start();
    if (!_available) return;
    await store.setOffered();
    busy = true;
    error = null;
    notice = null;
    notifyListeners();
    try {
      final permission = await messaging.requestPermission();
      if (permission != PushPermission.granted) {
        phase = PushPhase.blocked;
        return;
      }
      final context = await repository.currentContext();
      if (context == null) {
        phase = PushPhase.signedOut;
        return;
      }
      await _register(context);
      phase = PushPhase.on;
      notice = 'Notifications are on for this phone in this workspace.';
      devices = await repository.devices();
    } on Object catch (caught) {
      phase = PushPhase.error;
      error = _describe(caught, 'Notifications were not turned on.');
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  /// Whether to offer push after the person's first approval decision: only
  /// once, only when push can work and they were never asked.
  Future<bool> shouldOfferAfterApproval() async {
    if (!_initialized) await start();
    if (!_available || await store.offered()) return false;
    if (await messaging.permission() != PushPermission.notDetermined) {
      return false;
    }
    try {
      final status = server ?? await repository.status();
      server = status;
      if (!status.available) return false;
    } on Object {
      return false;
    }
    await store.setOffered();
    return true;
  }

  /// Removes one of the caller's phones (this one included) from the
  /// workspace.
  Future<void> removeDevice(PushDevice device) async {
    busy = true;
    error = null;
    notice = null;
    notifyListeners();
    try {
      await repository.remove(device.id);
      devices = devices.where((item) => item.id != device.id).toList();
      if (device.id == thisDeviceId) {
        final context = await repository.currentContext();
        if (context != null) await store.setDeviceId(scopeOf(context), null);
        thisDeviceId = null;
        phase = PushPhase.notAsked;
      }
      notice = 'Removed ${device.title}.';
    } on Object catch (caught) {
      error = _describe(caught, '${device.title} was not removed.');
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> _register(TenantContext context) async {
    final token = await messaging.token();
    if (token == null || token.isEmpty) {
      throw const _PushFailure(
          'This phone has no push token yet. Check that Google Play services '
          '(Android) or notifications (iPhone) work, then try again.');
    }
    final device = await repository.register(
        platform: messaging.platform,
        token: token,
        label: messaging.deviceLabel,
        language: languageOf());
    thisDeviceId = device.id;
    await store.setDeviceId(scopeOf(context), device.id);
  }

  Future<void> _reregister() async {
    if (phase != PushPhase.on) return;
    try {
      final context = await repository.currentContext();
      if (context != null) await _register(context);
    } on Object {
      // The next refresh tries again.
    }
  }

  /// Before sign-out or a workspace switch, while the session still works:
  /// this phone stops getting pushes for the workspace it leaves.
  @override
  Future<void> leavingWorkspace() async {
    if (!_available) return;
    try {
      final context = await repository.currentContext();
      if (context == null) return;
      final scope = scopeOf(context);
      final id = await store.deviceId(scope);
      if (id != null) {
        try {
          await repository.remove(id);
        } on ApiException catch (caught) {
          if (caught.statusCode != 404) rethrow;
        }
        await store.setDeviceId(scope, null);
      }
    } on Object {
      // Offline: the server removes the token when FCM reports it unregistered.
    }
    thisDeviceId = null;
    devices = const [];
  }

  /// After a workspace switch: register for the new workspace when allowed.
  @override
  Future<void> enteredWorkspace() => refresh();

  /// After sign-out: the token is invalidated so the next person to sign in on
  /// this phone gets a new one, never this user's pushes.
  Future<void> signedOut() async {
    if (_available) {
      try {
        await messaging.deleteToken();
      } on Object {
        // Nothing registered for it any more anyway.
      }
    }
    phase = _available ? PushPhase.signedOut : PushPhase.unavailableInBuild;
    devices = const [];
    thisDeviceId = null;
    notice = null;
    error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    super.dispose();
  }
}

String _describe(Object error, String what) {
  if (error is _PushFailure) return error.message;
  if (error is ApiException) {
    if (error.statusCode == 409) return '$what ${error.message}';
    if (error.statusCode == 401) {
      return '$what Your session expired; sign in again.';
    }
    final text =
        error.message.endsWith('.') ? error.message : '${error.message}.';
    return '$what $text Try again.';
  }
  return '$what Check your connection and try again.';
}

class _PushFailure implements Exception {
  const _PushFailure(this.message);
  final String message;
}
