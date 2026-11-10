// The app lock's rules (docs/mobile.md#app-lock).
import 'dart:async';

import 'package:anum_mobile/features/app_lock/app_lock_controller.dart';
import 'package:anum_mobile/features/app_lock/device_auth.dart';
import 'package:flutter_test/flutter_test.dart';

import 'app_lock_fakes.dart';

void main() {
  late FakeDeviceAuth phone;
  late MemoryAppLockStore store;
  late FakeClock clock;

  AppLockController build([AppLockSettings? stored]) {
    store = MemoryAppLockStore(stored);
    return AppLockController(
        authenticator: phone, store: store, now: clock.call);
  }

  setUp(() {
    phone = FakeDeviceAuth();
    clock = FakeClock();
  });

  group('settings', () {
    test('off by default, sensitive areas, one minute', () {
      const settings = AppLockSettings();
      expect(settings.enabled, isFalse);
      expect(settings.scope, AppLockScope.sensitive);
      expect(settings.lockAfter, const Duration(minutes: 1));
      expect(AppLockSettings.lockAfterChoices.map((d) => d.inMinutes),
          [0, 1, 5, 15]);
    });

    test('round-trip, and anything unreadable falls to the stricter value', () {
      for (final scope in AppLockScope.values) {
        for (final delay in AppLockSettings.lockAfterChoices) {
          final settings =
              AppLockSettings(enabled: true, lockAfter: delay, scope: scope);
          expect(AppLockSettings.fromJson(settings.toJson()), settings);
        }
      }
      final odd = AppLockSettings.fromJson(
          {'enabled': true, 'lock_after_seconds': 99999, 'scope': 'nothing'});
      expect(odd.lockAfter, Duration.zero);
      expect(odd.scope, AppLockScope.wholeApp);
      expect(AppLockSettings.fromJson(const {}).enabled, isFalse);
      // Only a flag, a delay and a scope are stored: no secret.
      expect(const AppLockSettings(enabled: true).toJson().keys,
          {'enabled', 'lock_after_seconds', 'scope'});
    });
  });

  group('turning it on', () {
    test('needs one successful check and is then stored, unlocked', () async {
      final lock = build();
      await lock.restore();
      expect(lock.enabled, isFalse);
      expect(await lock.enable(reason: 'turn on'), AppLockChange.saved);
      expect(phone.prompts, ['turn on']);
      expect(lock.enabled, isTrue);
      expect(lock.locked, isFalse);
      expect(store.value, const AppLockSettings(enabled: true));
    });

    for (final result in [
      DeviceAuthResult.cancelled,
      DeviceAuthResult.failed,
      DeviceAuthResult.lockedOut,
      DeviceAuthResult.unavailable,
    ]) {
      test('stays off when the check ends ${result.name}', () async {
        phone.answer = result;
        final lock = build();
        await lock.restore();
        expect(await lock.enable(reason: 'r'), AppLockChange.notConfirmed);
        expect(lock.enabled, isFalse);
        expect(lock.lastFailure, result);
        expect(store.value, isNull);
      });
    }

    test('a phone without a screen lock is told so, without a prompt',
        () async {
      phone.supported = DeviceAuthSupport.notEnrolled;
      final lock = build();
      await lock.restore();
      expect(await lock.enable(reason: 'r'), AppLockChange.notEnrolled);
      expect(phone.prompts, isEmpty);
      expect(lock.enabled, isFalse);
    });

    test('a phone that cannot check is told so', () async {
      phone.supported = DeviceAuthSupport.unsupported;
      final lock = build();
      await lock.restore();
      expect(await lock.enable(reason: 'r'), AppLockChange.unsupported);
      expect(lock.enabled, isFalse);
    });

    test('a setting that cannot be stored stays off', () async {
      final lock = AppLockController(
          authenticator: phone, store: FailingStore(), now: clock.call)
        ..restored = true;
      expect(await lock.enable(reason: 'r'), AppLockChange.failed);
      expect(lock.enabled, isFalse);
    });
  });

  group('changing it', () {
    test('turning off, the delay and the scope each need a check', () async {
      final lock = build(on);
      await lock.restore();
      await lock.unlock(reason: 'unlock');
      phone.prompts.clear();

      phone.answer = DeviceAuthResult.cancelled;
      expect(await lock.disable(reason: 'c'), AppLockChange.notConfirmed);
      expect(await lock.setLockAfter(Duration.zero, reason: 'c'),
          AppLockChange.notConfirmed);
      expect(await lock.setScope(AppLockScope.wholeApp, reason: 'c'),
          AppLockChange.notConfirmed);
      expect(lock.settings, on);
      expect(store.value, on);
      expect(phone.prompts, hasLength(3));

      phone.answer = DeviceAuthResult.success;
      for (final delay in AppLockSettings.lockAfterChoices) {
        expect(
            await lock.setLockAfter(delay, reason: 'c'), AppLockChange.saved);
        expect(lock.settings.lockAfter, delay);
        expect(store.value!.lockAfter, delay);
      }
      expect(await lock.setScope(AppLockScope.wholeApp, reason: 'c'),
          AppLockChange.saved);
      expect(store.value!.scope, AppLockScope.wholeApp);
      expect(await lock.disable(reason: 'c'), AppLockChange.saved);
      expect(lock.enabled, isFalse);
      expect(store.value, isNull);
    });
  });

  group('scope', () {
    test('sensitive: Finance and Books, not the rest', () async {
      final lock = build(on);
      await lock.restore();
      expect(lock.locked, isTrue);
      expect(lock.blocks(AppLockArea.finance), isTrue);
      expect(lock.blocks(AppLockArea.books), isTrue);
      expect(lock.blocks(AppLockArea.app), isFalse);
      // The window is covered only while one of them is open.
      expect(lock.coversWindow, isFalse);
      lock.sensitiveScreenShown();
      expect(lock.coversWindow, isTrue);
      lock.sensitiveScreenGone();
      expect(lock.coversWindow, isFalse);
    });

    test('whole app: everything', () async {
      final lock = build(
          const AppLockSettings(enabled: true, scope: AppLockScope.wholeApp));
      await lock.restore();
      for (final area in AppLockArea.values) {
        expect(lock.blocks(area), isTrue, reason: area.name);
      }
      expect(lock.coversWindow, isTrue);
    });

    test('off: nothing', () async {
      final lock = build();
      await lock.restore();
      for (final area in AppLockArea.values) {
        expect(lock.blocks(area), isFalse);
      }
      expect(lock.coversWindow, isFalse);
    });
  });

  group('cold start', () {
    test('everything is closed until the settings are read', () {
      final lock = build(on);
      expect(lock.restored, isFalse);
      for (final area in AppLockArea.values) {
        expect(lock.blocks(area), isTrue);
      }
      expect(lock.coversWindow, isTrue);
    });

    test('a lock that is on starts locked; off starts open', () async {
      final lock = build(on);
      await lock.restore();
      expect(lock.locked, isTrue);
      final open = build();
      await open.restore();
      expect(open.locked, isFalse);
    });

    test('settings that cannot be read lock the whole app', () async {
      final lock = AppLockController(
          authenticator: phone, store: FailingStore(), now: clock.call);
      await lock.restore();
      expect(lock.enabled, isTrue);
      expect(lock.locked, isTrue);
      expect(lock.settings.scope, AppLockScope.wholeApp);
      expect(lock.settings.lockAfter, Duration.zero);
    });
  });

  group('unlocking', () {
    test('only a successful check unlocks', () async {
      final lock = build(on);
      await lock.restore();
      for (final result in DeviceAuthResult.values) {
        if (result == DeviceAuthResult.success) continue;
        phone.answer = result;
        expect(await lock.unlock(reason: 'u'), result);
        expect(lock.locked, isTrue, reason: result.name);
        expect(lock.lastFailure, result);
      }
      phone.answer = DeviceAuthResult.success;
      expect(await lock.unlock(reason: 'u'), DeviceAuthResult.success);
      expect(lock.locked, isFalse);
      expect(lock.lastFailure, isNull);
    });

    test('a check that breaks stays locked', () async {
      final lock = build(on);
      await lock.restore();
      phone.error = StateError('plugin');
      expect(await lock.unlock(reason: 'u'), DeviceAuthResult.unavailable);
      expect(lock.locked, isTrue);
      expect(lock.authenticating, isFalse);
    });

    test('a second prompt is not opened over the first', () async {
      final lock = build(on);
      await lock.restore();
      phone.hold = Completer();
      final first = lock.unlock(reason: 'u');
      expect(lock.authenticating, isTrue);
      expect(await lock.unlock(reason: 'u'), DeviceAuthResult.unavailable);
      expect(phone.prompts, hasLength(1));
      phone.hold!.complete(DeviceAuthResult.success);
      expect(await first, DeviceAuthResult.success);
      expect(lock.locked, isFalse);
    });
  });

  group('coming back', () {
    Future<AppLockController> unlocked(Duration lockAfter) async {
      final lock = build(AppLockSettings(enabled: true, lockAfter: lockAfter));
      await lock.restore();
      await lock.unlock(reason: 'u');
      expect(lock.locked, isFalse);
      return lock;
    }

    for (final delay in AppLockSettings.lockAfterChoices) {
      test('"${delay.inMinutes} min": locks once away that long', () async {
        final lock = await unlocked(delay);
        if (delay > Duration.zero) {
          lock.paused();
          clock.advance(delay - const Duration(seconds: 1));
          lock.resumed();
          expect(lock.locked, isFalse, reason: 'one second short');
        }
        lock.paused();
        clock.advance(delay);
        lock.resumed();
        expect(lock.locked, isTrue);
      });
    }

    test('"Immediately" locks on every return', () async {
      final lock = await unlocked(Duration.zero);
      lock.paused();
      lock.resumed();
      expect(lock.locked, isTrue);
    });

    test('a resume without having left does not lock', () async {
      final lock = await unlocked(Duration.zero);
      lock.resumed();
      expect(lock.locked, isFalse);
    });

    test('the clock set back while away locks', () async {
      final lock = await unlocked(const Duration(minutes: 15));
      lock.paused();
      clock.advance(const Duration(hours: -2));
      lock.resumed();
      expect(lock.locked, isTrue);
    });

    test('the system prompt pausing the app is not time away', () async {
      final lock =
          build(const AppLockSettings(enabled: true, lockAfter: Duration.zero));
      await lock.restore();
      phone.hold = Completer();
      final unlocking = lock.unlock(reason: 'u');
      // Older Android opens the screen-lock page as its own screen.
      lock.paused();
      lock.resumed();
      phone.hold!.complete(DeviceAuthResult.success);
      await unlocking;
      expect(lock.locked, isFalse);
      lock.resumed();
      expect(lock.locked, isFalse);
    });

    test('leaving the phone with a prompt open is no way in', () async {
      final lock = await unlocked(const Duration(minutes: 1));
      // An approval decision opens the prompt; the person walks away.
      clock.advance(const Duration(minutes: 2));
      phone.hold = Completer();
      final deciding = lock.confirmSensitiveAction(reason: 'd');
      lock.paused();
      clock.advance(const Duration(hours: 1));
      // The system closes the prompt of an app in the background.
      phone.hold!.complete(DeviceAuthResult.cancelled);
      expect(await deciding, isFalse);
      lock.resumed();
      expect(lock.locked, isTrue);
    });

    test(
        'a failed check after a long time away locks, even when the app '
        'came back before the prompt ended', () async {
      final lock = await unlocked(const Duration(minutes: 1));
      clock.advance(const Duration(minutes: 2));
      phone.hold = Completer();
      final deciding = lock.confirmSensitiveAction(reason: 'd');
      lock.paused();
      clock.advance(const Duration(minutes: 10));
      lock.resumed();
      expect(lock.locked, isFalse, reason: 'the prompt is still open');
      phone.hold!.complete(DeviceAuthResult.failed);
      expect(await deciding, isFalse);
      expect(lock.locked, isTrue);
    });

    test('with the lock off nothing ever locks', () async {
      final lock = build();
      await lock.restore();
      lock.paused();
      clock.advance(const Duration(days: 1));
      lock.resumed();
      expect(lock.locked, isFalse);
    });
  });

  group('approval decisions', () {
    test('lock off: never asks', () async {
      final lock = build();
      await lock.restore();
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, isEmpty);
    });

    test('a fresh unlock covers decisions until "Lock after" has passed',
        () async {
      final lock = build(const AppLockSettings(
          enabled: true, lockAfter: Duration(minutes: 5)));
      await lock.restore();
      await lock.unlock(reason: 'u');
      phone.prompts.clear();

      clock.advance(const Duration(minutes: 4, seconds: 59));
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, isEmpty);

      clock.advance(const Duration(seconds: 1));
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, ['d']);
      // That check counts as the new unlock.
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, ['d']);
    });

    test('"Immediately" asks for every decision', () async {
      final lock =
          build(const AppLockSettings(enabled: true, lockAfter: Duration.zero));
      await lock.restore();
      await lock.unlock(reason: 'u');
      phone.prompts.clear();
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, ['d', 'd']);
    });

    test('a cancelled or failed check refuses the decision', () async {
      final lock = build(on);
      await lock.restore();
      for (final result in [
        DeviceAuthResult.cancelled,
        DeviceAuthResult.failed,
        DeviceAuthResult.lockedOut,
        DeviceAuthResult.unavailable,
      ]) {
        phone.answer = result;
        expect(await lock.confirmSensitiveAction(reason: 'd'), isFalse);
      }
    });

    test('while locked it asks, and a pass unlocks', () async {
      final lock = build(on);
      await lock.restore();
      expect(await lock.confirmSensitiveAction(reason: 'd'), isTrue);
      expect(phone.prompts, ['d']);
      expect(lock.locked, isFalse);
    });

    test('before the settings are read nothing is confirmed', () async {
      final lock = build(on);
      expect(await lock.confirmSensitiveAction(reason: 'd'), isFalse);
    });

    test('which requests are decisions', () {
      for (final path in [
        '/api/v1/approvals/approval_1/approve',
        '/api/v1/approvals/approval_1/reject',
        '/api/v1/business/change-orders/bchg_1/approve',
        '/api/v1/business/change-orders/bchg_1/reject',
        '/api/v1/finance/execution/intents/int_1/approve',
        '/api/v1/finance/execution/intents/int_1/reject',
        '/api/v1/business/books/bills/bill_1/approve',
        '/api/v1/business/books/adjustments/adj_1/approve',
        '/api/v1/finance/guardian/changes/chg_1/reject',
        '/api/v1/finance/salary/months/2026-11/decision',
        '/api/v1/approvals/approval_1/approve?x=1',
      ]) {
        expect(isApprovalDecision('POST', path), isTrue, reason: path);
      }
      for (final (method, path) in [
        ('GET', '/api/v1/approvals'),
        ('GET', '/api/v1/approvals/approval_1/approve'),
        ('POST', '/api/v1/tasks'),
        ('POST', '/api/v1/finance/suggestions/reject'),
        ('POST', '/api/v1/business/invoices/inv_1/request-approval'),
        ('PUT', '/api/v1/approval-policy'),
      ]) {
        expect(isApprovalDecision(method, path), isFalse, reason: path);
      }
    });
  });

  group('sign-out', () {
    test('turns the lock off, forgets it and closes an open prompt', () async {
      final lock = build(on);
      await lock.restore();
      phone.hold = Completer();
      unawaited(lock.unlock(reason: 'u'));
      lock.reset();
      await Future<void>.delayed(Duration.zero);
      expect(phone.cancels, 1);
      expect(lock.enabled, isFalse);
      expect(lock.locked, isFalse);
      expect(store.value, isNull);
      for (final area in AppLockArea.values) {
        expect(lock.blocks(area), isFalse);
      }
      phone.hold!.complete(DeviceAuthResult.cancelled);
    });

    test('a start-up read that ends after sign-out does not bring it back',
        () async {
      final lock = build(on);
      final reading = lock.restore();
      lock.reset();
      await reading;
      expect(lock.enabled, isFalse);
      expect(lock.locked, isFalse);
      expect(lock.restored, isTrue);
    });
  });
}
