/// The phone's push channel, behind an interface so the controller and the
/// screens are tested with fakes. The Firebase implementation lives in
/// `firebase_push_messaging.dart`, the only file that imports Firebase.
enum PushPermission { notDetermined, granted, denied }

/// A push message: the system's title and body and the ANUM data (ids only).
class IncomingPush {
  const IncomingPush({required this.data, this.title = '', this.body = ''});
  final String title;
  final String body;
  final Map<String, Object?> data;
}

abstract interface class PushMessaging {
  /// Sets up the push SDK. False when this build has no Firebase
  /// configuration (google-services.json / GoogleService-Info.plist), when
  /// the platform has no push, or when setup fails: push then stays off.
  Future<bool> initialize();

  /// `android` or `ios`, as the API expects.
  String get platform;

  /// A label for the device list, e.g. "Android phone".
  String get deviceLabel;

  Future<PushPermission> permission();

  /// Shows the system prompt (Android 13+, iOS) unless already answered.
  Future<PushPermission> requestPermission();

  /// The current push token, or null when the platform has none yet.
  Future<String?> token();

  /// Invalidates this phone's token, so the next user gets a new one.
  Future<void> deleteToken();

  Stream<String> get tokenRefreshes;

  /// Pushes that arrive while the app is in the foreground (the system does
  /// not show them; the app does).
  Stream<IncomingPush> get foregroundPushes;

  /// Pushes the user tapped while the app was in the background.
  Stream<IncomingPush> get openedPushes;

  /// The push the user tapped to launch the app, if any.
  Future<IncomingPush?> launchPush();
}

/// Push off: platforms without push, and tests.
class DisabledPushMessaging implements PushMessaging {
  const DisabledPushMessaging();
  @override
  Future<bool> initialize() async => false;
  @override
  String get platform => 'android';
  @override
  String get deviceLabel => 'Phone';
  @override
  Future<PushPermission> permission() async => PushPermission.denied;
  @override
  Future<PushPermission> requestPermission() async => PushPermission.denied;
  @override
  Future<String?> token() async => null;
  @override
  Future<void> deleteToken() async {}
  @override
  Stream<String> get tokenRefreshes => const Stream.empty();
  @override
  Stream<IncomingPush> get foregroundPushes => const Stream.empty();
  @override
  Stream<IncomingPush> get openedPushes => const Stream.empty();
  @override
  Future<IncomingPush?> launchPush() async => null;
}

/// Small device-local facts about push: which device row this phone has in
/// each workspace, and whether the "turn on notifications" offer was shown.
abstract interface class PushLocalStore {
  Future<String?> deviceId(String scope);
  Future<void> setDeviceId(String scope, String? deviceId);
  Future<bool> offered();
  Future<void> setOffered();
}

class MemoryPushLocalStore implements PushLocalStore {
  final Map<String, String> devices = {};
  bool wasOffered = false;
  @override
  Future<String?> deviceId(String scope) async => devices[scope];
  @override
  Future<void> setDeviceId(String scope, String? deviceId) async {
    if (deviceId == null) {
      devices.remove(scope);
    } else {
      devices[scope] = deviceId;
    }
  }

  @override
  Future<bool> offered() async => wasOffered;
  @override
  Future<void> setOffered() async => wasOffered = true;
}
