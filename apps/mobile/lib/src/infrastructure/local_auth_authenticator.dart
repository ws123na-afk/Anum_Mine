import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';

import '../../features/app_lock/app_lock_controller.dart';
import '../../features/app_lock/device_auth.dart';
import 'mobile_adapters.dart' show deviceOnlyKeychain, readDeviceOnly;

/// The app lock's check through the `local_auth` plugin
/// (docs/mobile.md#app-lock): the system prompt with biometrics first and the
/// phone's PIN, pattern or passcode as the fallback (`biometricOnly: false`).
/// The only file that imports the plugin.
class LocalAuthDeviceAuthenticator implements DeviceAuthenticator {
  LocalAuthDeviceAuthenticator({LocalAuthentication? plugin})
      : _plugin = plugin ?? LocalAuthentication();

  final LocalAuthentication _plugin;

  @override
  Future<DeviceAuthSupport> support() async {
    try {
      // True when the phone has a screen lock or enrolled biometrics.
      if (await _plugin.isDeviceSupported()) return DeviceAuthSupport.available;
      // No credential at all: biometric hardware without a screen lock, or a
      // phone that never had one set.
      return DeviceAuthSupport.notEnrolled;
    } on MissingPluginException {
      return DeviceAuthSupport.unsupported;
    } on Object {
      return DeviceAuthSupport.unsupported;
    }
  }

  @override
  Future<DeviceAuthResult> authenticate({required String reason}) async {
    try {
      final passed = await _plugin.authenticate(
        localizedReason: reason,
        biometricOnly: false,
        // A prompt interrupted by the app going to the background fails
        // instead of resuming on its own.
        persistAcrossBackgrounding: false,
      );
      return passed ? DeviceAuthResult.success : DeviceAuthResult.failed;
    } on LocalAuthException catch (error) {
      return switch (error.code) {
        LocalAuthExceptionCode.userCanceled ||
        LocalAuthExceptionCode.systemCanceled ||
        LocalAuthExceptionCode.timeout ||
        LocalAuthExceptionCode.userRequestedFallback =>
          DeviceAuthResult.cancelled,
        LocalAuthExceptionCode.temporaryLockout => DeviceAuthResult.lockedOut,
        LocalAuthExceptionCode.biometricLockout =>
          DeviceAuthResult.lockedOutUntilDeviceUnlock,
        LocalAuthExceptionCode.noCredentialsSet ||
        LocalAuthExceptionCode.noBiometricsEnrolled =>
          DeviceAuthResult.notEnrolled,
        _ => DeviceAuthResult.unavailable,
      };
    } on Object {
      return DeviceAuthResult.unavailable;
    }
  }

  @override
  Future<void> cancel() async {
    try {
      await _plugin.stopAuthentication();
    } on Object {
      // Nothing to close.
    }
  }
}

/// The app lock's settings in platform secure storage (device only, never in
/// a backup): on or off, the delay and the scope. No secret.
class SecureAppLockStore implements AppLockStore {
  SecureAppLockStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'anum.app-lock';
  final FlutterSecureStorage _storage;

  @override
  Future<AppLockSettings?> read() async {
    final value = await readDeviceOnly(_storage, _key);
    if (value == null) return null;
    final decoded = jsonDecode(value);
    if (decoded is! Map<String, Object?>) {
      throw const FormatException('Unreadable app lock settings');
    }
    return AppLockSettings.fromJson(decoded);
  }

  @override
  Future<void> write(AppLockSettings settings) => _storage.write(
      key: _key,
      value: jsonEncode(settings.toJson()),
      iOptions: deviceOnlyKeychain);

  @override
  Future<void> clear() async {
    // The keychain query includes the item's class, so both are named.
    await _storage.delete(key: _key, iOptions: deviceOnlyKeychain);
    await _storage.delete(key: _key);
  }
}
