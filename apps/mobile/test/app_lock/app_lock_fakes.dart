import 'dart:async';

import 'package:anum_mobile/features/app_lock/app_lock_controller.dart';
import 'package:anum_mobile/features/app_lock/device_auth.dart';

/// The phone's check under test control: each prompt answers with the next
/// queued result (or [answer] when the queue is empty).
class FakeDeviceAuth implements DeviceAuthenticator {
  FakeDeviceAuth(
      {this.supported = DeviceAuthSupport.available,
      this.answer = DeviceAuthResult.success});

  DeviceAuthSupport supported;
  DeviceAuthResult answer;
  final queue = <DeviceAuthResult>[];

  /// The reasons shown, one per prompt.
  final prompts = <String>[];
  int cancels = 0;

  /// When set, a prompt stays open until it is completed.
  Completer<DeviceAuthResult>? hold;

  /// Throws instead of answering (a plugin that breaks).
  Object? error;

  @override
  Future<DeviceAuthSupport> support() async => supported;

  @override
  Future<DeviceAuthResult> authenticate({required String reason}) async {
    prompts.add(reason);
    if (error != null) throw error!;
    final held = hold;
    if (held != null) return held.future;
    return queue.isEmpty ? answer : queue.removeAt(0);
  }

  @override
  Future<void> cancel() async => cancels++;
}

/// A clock the test moves.
class FakeClock {
  DateTime now = DateTime.utc(2026, 11, 10, 9);
  DateTime call() => now;
  void advance(Duration by) => now = now.add(by);
}

class FailingStore implements AppLockStore {
  @override
  Future<AppLockSettings?> read() async => throw StateError('keystore');
  @override
  Future<void> write(AppLockSettings settings) async =>
      throw StateError('keystore');
  @override
  Future<void> clear() async => throw StateError('keystore');
}

const on = AppLockSettings(enabled: true);
