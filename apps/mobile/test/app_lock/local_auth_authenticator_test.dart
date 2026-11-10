// How the `local_auth` plugin's answers become the app lock's results
// (docs/mobile.md#app-lock). The prompt itself needs a phone.
import 'dart:convert';

import 'package:anum_mobile/features/app_lock/app_lock_controller.dart';
import 'package:anum_mobile/features/app_lock/device_auth.dart';
import 'package:anum_mobile/src/infrastructure/local_auth_authenticator.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:local_auth/local_auth.dart';

class FakePlugin extends LocalAuthentication {
  bool supported = true;
  Object? supportError;
  bool passes = true;
  Object? error;
  int stops = 0;
  bool? biometricOnly;
  bool? persist;
  String? reason;

  @override
  Future<bool> isDeviceSupported() async {
    if (supportError != null) throw supportError!;
    return supported;
  }

  @override
  Future<bool> authenticate({
    required String localizedReason,
    Iterable<Object> authMessages = const [],
    bool biometricOnly = false,
    bool sensitiveTransaction = true,
    bool persistAcrossBackgrounding = false,
  }) async {
    reason = localizedReason;
    this.biometricOnly = biometricOnly;
    persist = persistAcrossBackgrounding;
    if (error != null) throw error!;
    return passes;
  }

  @override
  Future<bool> stopAuthentication() async {
    stops++;
    return true;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late FakePlugin plugin;
  late LocalAuthDeviceAuthenticator phone;

  setUp(() {
    plugin = FakePlugin();
    phone = LocalAuthDeviceAuthenticator(plugin: plugin);
  });

  test('a phone with a screen lock is available; without one, not enrolled',
      () async {
    expect(await phone.support(), DeviceAuthSupport.available);
    plugin.supported = false;
    expect(await phone.support(), DeviceAuthSupport.notEnrolled);
  });

  test('a build without the plugin is unsupported, never an error', () async {
    plugin.supportError = MissingPluginException('no local_auth');
    expect(await phone.support(), DeviceAuthSupport.unsupported);
    plugin.supportError = PlatformException(code: 'boom');
    expect(await phone.support(), DeviceAuthSupport.unsupported);
  });

  test(
      'the prompt allows the screen lock as the fallback and does not '
      'resume by itself after the app was in the background', () async {
    expect(await phone.authenticate(reason: 'Unlock ANUM'),
        DeviceAuthResult.success);
    expect(plugin.reason, 'Unlock ANUM');
    expect(plugin.biometricOnly, isFalse);
    expect(plugin.persist, isFalse);
  });

  test('not recognised is failed', () async {
    plugin.passes = false;
    expect(await phone.authenticate(reason: 'r'), DeviceAuthResult.failed);
  });

  test('every plugin error maps to a result; none unlocks', () async {
    const expected = {
      LocalAuthExceptionCode.userCanceled: DeviceAuthResult.cancelled,
      LocalAuthExceptionCode.systemCanceled: DeviceAuthResult.cancelled,
      LocalAuthExceptionCode.timeout: DeviceAuthResult.cancelled,
      LocalAuthExceptionCode.userRequestedFallback: DeviceAuthResult.cancelled,
      LocalAuthExceptionCode.temporaryLockout: DeviceAuthResult.lockedOut,
      LocalAuthExceptionCode.biometricLockout:
          DeviceAuthResult.lockedOutUntilDeviceUnlock,
      LocalAuthExceptionCode.noCredentialsSet: DeviceAuthResult.notEnrolled,
      LocalAuthExceptionCode.noBiometricsEnrolled: DeviceAuthResult.notEnrolled,
      LocalAuthExceptionCode.noBiometricHardware: DeviceAuthResult.unavailable,
      LocalAuthExceptionCode.authInProgress: DeviceAuthResult.unavailable,
      LocalAuthExceptionCode.uiUnavailable: DeviceAuthResult.unavailable,
      LocalAuthExceptionCode.deviceError: DeviceAuthResult.unavailable,
      LocalAuthExceptionCode.unknownError: DeviceAuthResult.unavailable,
    };
    for (final code in LocalAuthExceptionCode.values) {
      plugin.error = LocalAuthException(code: code);
      final result = await phone.authenticate(reason: 'r');
      expect(result, isNot(DeviceAuthResult.success), reason: code.name);
      if (expected.containsKey(code)) {
        expect(result, expected[code], reason: code.name);
      }
    }
    plugin.error = StateError('anything else');
    expect(await phone.authenticate(reason: 'r'), DeviceAuthResult.unavailable);
  });

  test('cancel closes the prompt', () async {
    await phone.cancel();
    expect(plugin.stops, 1);
  });

  group('SecureAppLockStore', () {
    test('keeps only the flag, the delay and the scope; clear removes them',
        () async {
      FlutterSecureStorage.setMockInitialValues({});
      final store = SecureAppLockStore();
      expect(await store.read(), isNull);
      const settings = AppLockSettings(
          enabled: true,
          lockAfter: Duration(minutes: 15),
          scope: AppLockScope.wholeApp);
      await store.write(settings);
      expect(await store.read(), settings);
      final raw = await const FlutterSecureStorage().read(key: 'anum.app-lock');
      expect(jsonDecode(raw!), {
        'enabled': true,
        'lock_after_seconds': 900,
        'scope': 'wholeApp',
      });
      await store.clear();
      expect(await store.read(), isNull);
    });

    test('unreadable settings fail the read (the lock then closes the app)',
        () async {
      FlutterSecureStorage.setMockInitialValues({'anum.app-lock': 'not json'});
      await expectLater(SecureAppLockStore().read(), throwsFormatException);
      FlutterSecureStorage.setMockInitialValues({'anum.app-lock': '[1]'});
      await expectLater(SecureAppLockStore().read(), throwsFormatException);
    });
  });
}
