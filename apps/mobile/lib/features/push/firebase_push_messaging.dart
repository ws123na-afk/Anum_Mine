import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'push_messaging.dart';

/// Firebase Cloud Messaging (Android, and iOS through APNs). The Firebase
/// project comes from the owner-provided, gitignored google-services.json and
/// GoogleService-Info.plist (docs/mobile.md#phone-push); without them
/// [initialize] answers false and push stays off.
///
/// firebase_messaging 17 deprecates `getToken`, `deleteToken` and
/// `onTokenRefresh` in favour of Firebase Installation ID registration
/// (`register` and `onRegistered`). The API still sends to FCM registration
/// tokens (docs/mobile.md#phone-push), so these calls stay on the token API
/// until the server learns to address a device by installation ID.
class FirebasePushMessaging implements PushMessaging {
  bool _ready = false;

  FirebaseMessaging get _messaging => FirebaseMessaging.instance;

  @override
  Future<bool> initialize() async {
    if (kIsWeb ||
        (defaultTargetPlatform != TargetPlatform.android &&
            defaultTargetPlatform != TargetPlatform.iOS)) {
      return false;
    }
    try {
      if (Firebase.apps.isEmpty) await Firebase.initializeApp();
      // In the foreground the app shows its own notice instead of the system's.
      await _messaging.setForegroundNotificationPresentationOptions(
          alert: false, badge: true, sound: false);
      _ready = true;
    } on Object {
      // No Firebase configuration in this build (or the SDK failed): off.
      _ready = false;
    }
    return _ready;
  }

  @override
  String get platform =>
      defaultTargetPlatform == TargetPlatform.iOS ? 'ios' : 'android';

  @override
  String get deviceLabel =>
      defaultTargetPlatform == TargetPlatform.iOS ? 'iPhone' : 'Android phone';

  static PushPermission _map(AuthorizationStatus status) => switch (status) {
        AuthorizationStatus.authorized ||
        AuthorizationStatus.provisional =>
          PushPermission.granted,
        AuthorizationStatus.denied ||
        AuthorizationStatus.deniedPermanently =>
          PushPermission.denied,
        AuthorizationStatus.notDetermined => PushPermission.notDetermined,
      };

  @override
  Future<PushPermission> permission() async {
    if (!_ready) return PushPermission.denied;
    return _map(
        (await _messaging.getNotificationSettings()).authorizationStatus);
  }

  @override
  Future<PushPermission> requestPermission() async {
    if (!_ready) return PushPermission.denied;
    final settings = await _messaging.requestPermission(
        alert: true, badge: true, sound: true);
    return _map(settings.authorizationStatus);
  }

  @override
  Future<String?> token() async {
    if (!_ready) return null;
    if (defaultTargetPlatform == TargetPlatform.iOS) {
      // FCM needs the APNs token first; it can take a moment after launch.
      for (var attempt = 0; attempt < 5; attempt++) {
        if (await _messaging.getAPNSToken() != null) break;
        await Future<void>.delayed(const Duration(seconds: 1));
      }
    }
    // ignore: deprecated_member_use
    return _messaging.getToken();
  }

  @override
  Future<void> deleteToken() async {
    // ignore: deprecated_member_use
    if (_ready) await _messaging.deleteToken();
  }

  @override
  Stream<String> get tokenRefreshes =>
      // ignore: deprecated_member_use
      _ready ? _messaging.onTokenRefresh : const Stream.empty();

  static IncomingPush _incoming(RemoteMessage message) => IncomingPush(
        title: message.notification?.title ?? '',
        body: message.notification?.body ?? '',
        data: Map<String, Object?>.from(message.data),
      );

  @override
  Stream<IncomingPush> get foregroundPushes => _ready
      ? FirebaseMessaging.onMessage.map(_incoming)
      : const Stream.empty();

  @override
  Stream<IncomingPush> get openedPushes => _ready
      ? FirebaseMessaging.onMessageOpenedApp.map(_incoming)
      : const Stream.empty();

  @override
  Future<IncomingPush?> launchPush() async {
    if (!_ready) return null;
    final message = await _messaging.getInitialMessage();
    return message == null ? null : _incoming(message);
  }
}

/// [PushLocalStore] in shared preferences: device row ids are not secrets
/// (the push token itself is never stored by the app).
class SharedPreferencesPushLocalStore implements PushLocalStore {
  static const _devicePrefix = 'anum.push.device.';
  static const _offeredKey = 'anum.push.offered';

  @override
  Future<String?> deviceId(String scope) async =>
      (await SharedPreferences.getInstance()).getString('$_devicePrefix$scope');

  @override
  Future<void> setDeviceId(String scope, String? deviceId) async {
    final preferences = await SharedPreferences.getInstance();
    if (deviceId == null) {
      await preferences.remove('$_devicePrefix$scope');
    } else {
      await preferences.setString('$_devicePrefix$scope', deviceId);
    }
  }

  @override
  Future<bool> offered() async =>
      (await SharedPreferences.getInstance()).getBool(_offeredKey) ?? false;

  @override
  Future<void> setOffered() async =>
      (await SharedPreferences.getInstance()).setBool(_offeredKey, true);
}
