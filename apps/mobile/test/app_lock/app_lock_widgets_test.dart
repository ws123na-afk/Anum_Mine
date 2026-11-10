// The lock screen, the gate and the Settings tiles (docs/mobile.md#app-lock).
import 'dart:async';

import 'package:anum_mobile/features/app_lock/app_lock_controller.dart';
import 'package:anum_mobile/features/app_lock/app_lock_screen.dart';
import 'package:anum_mobile/features/app_lock/app_lock_settings.dart';
import 'package:anum_mobile/features/app_lock/device_auth.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/app_lock_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'app_lock_fakes.dart';

Future<AppLockController> controllerWith(FakeDeviceAuth phone,
    [AppLockSettings? stored, FakeClock? clock]) async {
  final lock = AppLockController(
      authenticator: phone,
      store: MemoryAppLockStore(stored),
      now: (clock ?? FakeClock()).call);
  await lock.restore();
  addTearDown(lock.dispose);
  return lock;
}

/// The app's shell: the cover above a navigator, as `AnumApp` builds it.
Widget shell(AppLockController lock,
        {required Widget home,
        Future<void> Function()? onSignOut,
        VoidCallback? onLeave,
        bool active = true,
        Locale locale = const Locale('en'),
        double textScale = 1}) =>
    MaterialApp(
      locale: locale,
      theme: AnumTheme.light(),
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates,
      ],
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: AppLockCover(
          controller: lock,
          active: active,
          onSignOut: onSignOut ?? () async {},
          onLeave: onLeave,
          child: child!,
        ),
      ),
      home: home,
    );

const wholeApp = AppLockSettings(enabled: true, scope: AppLockScope.wholeApp);

void main() {
  test('every app lock sentence exists in English and Arabic', () {
    expect(appLockAr.keys.toSet(), appLockEn.keys.toSet());
    for (final key in appLockEn.keys) {
      for (final text in [appLockEn[key]!, appLockAr[key]!]) {
        expect(text.trim(), isNotEmpty, reason: key);
        expect(text, isNot(contains('—')), reason: key);
        expect(text, isNot(contains('–')), reason: key);
      }
    }
    // One sentence per way a check can end without unlocking.
    for (final result in DeviceAuthResult.values) {
      if (result == DeviceAuthResult.success) continue;
      expect(appLockEn, contains('lock.failed.${result.name}'));
    }
    for (final delay in AppLockSettings.lockAfterChoices) {
      expect(appLockEn, contains('lock.after.${delay.inSeconds}'));
    }
  });

  group('lock screen', () {
    testWidgets('covers the app; nothing behind is visible or reachable',
        (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.cancelled);
      final lock = await controllerWith(phone, wholeApp);
      var taps = 0;
      await tester.pumpWidget(shell(lock,
          home: Scaffold(
              body: Center(
                  child: FilledButton(
                      onPressed: () => taps++,
                      child: const Text('Balance AED 9,000'))))));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('app-lock-screen')), findsOneWidget);
      expect(find.text('ANUM is locked'), findsOneWidget);
      expect(find.text('ANUM 360'), findsOneWidget);
      // Offstage: not painted, not hit-testable, not in the semantics tree.
      expect(find.text('Balance AED 9,000'), findsNothing);
      expect(
          find.text('Balance AED 9,000', skipOffstage: false), findsOneWidget);
      await tester
          .tapAt(tester.getCenter(find.byKey(const Key('app-lock-screen'))));
      expect(taps, 0);
      expect(find.bySemanticsLabel(RegExp('Balance')), findsNothing);
    });

    testWidgets(
        'asks once by itself; a cancelled check stays locked and says so',
        (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.cancelled);
      final lock = await controllerWith(phone, wholeApp);
      await tester.pumpWidget(shell(lock, home: const Text('Home')));
      await tester.pumpAndSettle();
      expect(phone.prompts, ['Unlock ANUM']);
      expect(lock.locked, isTrue);
      expect(find.text('Still locked: the check was cancelled. Try again.'),
          findsOneWidget);
      // No second prompt on its own.
      await tester.pump(const Duration(seconds: 5));
      expect(phone.prompts, hasLength(1));

      phone.answer = DeviceAuthResult.success;
      await tester.tap(find.byKey(const Key('app-lock-unlock')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-screen')), findsNothing);
      expect(find.text('Home'), findsOneWidget);
    });

    testWidgets('each way a check fails has its own explanation',
        (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.failed);
      final lock = await controllerWith(phone, wholeApp);
      await tester.pumpWidget(shell(lock, home: const Text('Home')));
      await tester.pumpAndSettle();
      const expected = {
        DeviceAuthResult.failed: 'did not recognise you',
        DeviceAuthResult.lockedOut: 'wait about 30 seconds',
        DeviceAuthResult.lockedOutUntilDeviceUnlock:
            'unlock it with its PIN, pattern or passcode',
        DeviceAuthResult.notEnrolled: 'no screen lock, fingerprint or face',
        DeviceAuthResult.unavailable: 'could not run the check',
      };
      for (final entry in expected.entries) {
        phone.answer = entry.key;
        await tester.tap(find.byKey(const Key('app-lock-unlock')));
        await tester.pumpAndSettle();
        expect(find.textContaining(entry.value), findsOneWidget,
            reason: entry.key.name);
        expect(lock.locked, isTrue);
        expect(find.byKey(const Key('app-lock-screen')), findsOneWidget);
      }
    });

    testWidgets('while the phone checks, the buttons wait', (tester) async {
      final phone = FakeDeviceAuth()..hold = Completer();
      final lock = await controllerWith(phone, wholeApp);
      await tester.pumpWidget(shell(lock, home: const Text('Home')));
      await tester.pump();
      await tester.pump();
      expect(find.text('Waiting for the phone to check'), findsOneWidget);
      final unlock =
          tester.widget<FilledButton>(find.byKey(const Key('app-lock-unlock')));
      expect(unlock.onPressed, isNull);
      phone.hold!.complete(DeviceAuthResult.success);
      await tester.pumpAndSettle();
      expect(find.text('Home'), findsOneWidget);
    });

    testWidgets('Sign out asks first, then signs the app out', (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.cancelled);
      final lock = await controllerWith(phone, wholeApp);
      var signedOut = 0;
      await tester.pumpWidget(shell(lock,
          home: const Text('Home'), onSignOut: () async => signedOut++));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const Key('app-lock-sign-out')));
      await tester.pumpAndSettle();
      expect(find.text('Sign out?'), findsOneWidget);
      await tester.tap(find.byKey(const Key('app-lock-sign-out-cancel')));
      await tester.pumpAndSettle();
      expect(signedOut, 0);
      expect(find.byKey(const Key('app-lock-screen')), findsOneWidget);

      await tester.tap(find.byKey(const Key('app-lock-sign-out')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('app-lock-sign-out-confirm')));
      await tester.pumpAndSettle();
      expect(signedOut, 1);
    });

    testWidgets('nobody signed in: the sign-in screens are never covered',
        (tester) async {
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(phone, wholeApp);
      await tester
          .pumpWidget(shell(lock, home: const Text('Sign in'), active: false));
      await tester.pumpAndSettle();
      expect(find.text('Sign in'), findsOneWidget);
      expect(find.byKey(const Key('app-lock-screen')), findsNothing);
      expect(phone.prompts, isEmpty);
    });

    testWidgets('before the settings are read a plain cover shows',
        (tester) async {
      final lock = AppLockController(
          authenticator: FakeDeviceAuth(), store: MemoryAppLockStore());
      addTearDown(lock.dispose);
      await tester.pumpWidget(shell(lock, home: const Text('Home')));
      expect(find.byKey(const Key('app-lock-starting')), findsOneWidget);
      expect(find.text('Home'), findsNothing);
      await lock.restore();
      await tester.pumpAndSettle();
      expect(find.text('Home'), findsOneWidget);
    });

    for (final (width, height) in [
      (320.0, 568.0),
      (390.0, 844.0),
      (844.0, 390.0)
    ]) {
      testWidgets(
          'fits ${width.toInt()}x${height.toInt()} in Arabic, right to left, at 200% text',
          (tester) async {
        tester.view
          ..physicalSize = Size(width, height)
          ..devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        final phone =
            FakeDeviceAuth(answer: DeviceAuthResult.lockedOutUntilDeviceUnlock);
        final lock = await controllerWith(phone, on);
        lock.sensitiveScreenShown();
        await tester.pumpWidget(shell(lock,
            home: const Text('Home'),
            onLeave: () {},
            locale: const Locale('ar'),
            textScale: 2));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text('هذا القسم من أنوم مقفل'), findsOneWidget);
        expect(
            Directionality.of(
                tester.element(find.byKey(const Key('app-lock-unlock')))),
            TextDirection.rtl);
        // The longest explanation, every button, all reachable by scrolling.
        expect(find.byKey(const Key('app-lock-failure')), findsOneWidget);
        for (final key in const [
          'app-lock-unlock',
          'app-lock-back',
          'app-lock-sign-out'
        ]) {
          await tester.ensureVisible(find.byKey(Key(key)));
          await tester.pumpAndSettle();
          final box = tester.getRect(find.byKey(Key(key)));
          expect(box.left, greaterThanOrEqualTo(0), reason: key);
          expect(box.right, lessThanOrEqualTo(width), reason: key);
          expect(box.height, greaterThanOrEqualTo(44), reason: key);
        }
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('Finance and Books only', () {
    Widget home(AppLockController lock) => Builder(
          builder: (context) => Scaffold(
            body: Column(children: [
              const Text('Home'),
              TextButton(
                  onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute<void>(
                          builder: (_) => const AppLockGate(
                              area: AppLockArea.finance,
                              child: Scaffold(
                                  body: Text('Net worth AED 1,250,000'))))),
                  child: const Text('Open Finance')),
            ]),
          ),
        );

    testWidgets('the rest of the app stays open; Finance waits for the check',
        (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.cancelled);
      final lock = await controllerWith(phone, on);
      final nav = GlobalKey<NavigatorState>();
      await tester.pumpWidget(MaterialApp(
        navigatorKey: nav,
        localizationsDelegates: const [
          AnumLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        builder: (context, child) => AppLockCover(
            controller: lock,
            active: true,
            onSignOut: () async {},
            onLeave: () => nav.currentState!.popUntil((r) => r.isFirst),
            child: child!),
        home: home(lock),
      ));
      await tester.pumpAndSettle();
      expect(find.text('Home'), findsOneWidget);
      expect(find.byKey(const Key('app-lock-screen')), findsNothing);
      expect(phone.prompts, isEmpty);

      await tester.tap(find.text('Open Finance'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-screen')), findsOneWidget);
      expect(find.text('This part of ANUM is locked'), findsOneWidget);
      expect(find.text('Net worth AED 1,250,000'), findsNothing);
      expect(phone.prompts, hasLength(1));

      // "Go back" leaves Finance without unlocking it.
      await tester.tap(find.byKey(const Key('app-lock-back')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-screen')), findsNothing);
      expect(find.text('Home'), findsOneWidget);
      expect(lock.locked, isTrue);

      phone.answer = DeviceAuthResult.success;
      await tester.tap(find.text('Open Finance'));
      await tester.pumpAndSettle();
      expect(find.text('Net worth AED 1,250,000'), findsOneWidget);
    });

    testWidgets('a dialog left open above Finance is covered on return',
        (tester) async {
      final clock = FakeClock();
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(
          phone,
          const AppLockSettings(enabled: true, lockAfter: Duration.zero),
          clock);
      await lock.unlock(reason: 'u');
      await tester.pumpWidget(shell(lock,
          home: AppLockGate(
              area: AppLockArea.finance,
              child: Builder(
                  builder: (context) => Scaffold(
                      body: TextButton(
                          onPressed: () => showDialog<void>(
                              context: context,
                              builder: (_) => const AlertDialog(
                                  content: Text('IBAN AE07 0331 2345'))),
                          child: const Text('Show account')))))));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Show account'));
      await tester.pumpAndSettle();
      expect(find.text('IBAN AE07 0331 2345'), findsOneWidget);

      phone.answer = DeviceAuthResult.cancelled;
      lock.paused();
      lock.resumed();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-screen')), findsOneWidget);
      expect(find.text('IBAN AE07 0331 2345'), findsNothing);
    });

    testWidgets('without the app\'s lock a gate lets its screen through',
        (tester) async {
      await tester.pumpWidget(const MaterialApp(
          home:
              AppLockGate(area: AppLockArea.finance, child: Text('Finance'))));
      expect(find.text('Finance'), findsOneWidget);
    });
  });

  group('Settings', () {
    Future<void> pumpTiles(WidgetTester tester, AppLockController lock,
        {Locale locale = const Locale('en')}) async {
      tester.view
        ..physicalSize = const Size(390, 1400)
        ..devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        locale: locale,
        supportedLocales: const [Locale('en'), Locale('ar')],
        localizationsDelegates: const [
          AnumLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates,
        ],
        home: Scaffold(
            body: SingleChildScrollView(
                child: Card(child: AppLockSettingsTiles(controller: lock)))),
      ));
      await tester.pumpAndSettle();
    }

    testWidgets('off by default; turning on asks the phone once',
        (tester) async {
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(phone);
      await pumpTiles(tester, lock);
      expect(find.text('App lock'), findsOneWidget);
      expect(
          tester
              .widget<SwitchListTile>(find.byKey(const Key('app-lock-switch')))
              .value,
          isFalse);
      // The delay and the scope appear once it is on.
      expect(find.byKey(const Key('app-lock-after')), findsNothing);

      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(phone.prompts, ['Confirm it is you to turn on the app lock']);
      expect(lock.enabled, isTrue);
      expect(find.text('App lock is on.'), findsOneWidget);
      expect(find.byKey(const Key('app-lock-after')), findsOneWidget);
      expect(find.text('1 minute'), findsOneWidget);
      expect(
          find.text('Finance, Books and approval decisions'), findsOneWidget);
    });

    testWidgets('a cancelled check leaves it off and says why', (tester) async {
      final phone = FakeDeviceAuth(answer: DeviceAuthResult.cancelled);
      final lock = await controllerWith(phone);
      await pumpTiles(tester, lock);
      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(lock.enabled, isFalse);
      expect(find.textContaining('Not changed'), findsOneWidget);
    });

    testWidgets('no screen lock on the phone: explains and shows the steps',
        (tester) async {
      final phone = FakeDeviceAuth(supported: DeviceAuthSupport.notEnrolled);
      final lock = await controllerWith(phone);
      await pumpTiles(tester, lock);
      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(lock.enabled, isFalse);
      expect(find.text('Set a screen lock first'), findsOneWidget);
      expect(find.textContaining('none is set up'), findsOneWidget);

      await tester.tap(find.byKey(const Key('app-lock-how-to')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-help')), findsOneWidget);
      expect(find.textContaining('Android: open the phone\'s Settings'),
          findsOneWidget);
      expect(
          find.textContaining('iPhone or iPad: open Settings'), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('app-lock-help')), findsNothing);
    });

    testWidgets('a phone that cannot check says so', (tester) async {
      final phone = FakeDeviceAuth(supported: DeviceAuthSupport.unsupported);
      final lock = await controllerWith(phone);
      await pumpTiles(tester, lock);
      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(find.textContaining('cannot check a fingerprint'), findsOneWidget);
      expect(lock.enabled, isFalse);
    });

    testWidgets('each "Lock after" choice can be picked', (tester) async {
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(phone, on);
      await lock.unlock(reason: 'u');
      await pumpTiles(tester, lock);
      for (final (label, delay) in const [
        ('Immediately', Duration.zero),
        ('5 minutes', Duration(minutes: 5)),
        ('15 minutes', Duration(minutes: 15)),
        ('1 minute', Duration(minutes: 1)),
      ]) {
        await tester.tap(find.byKey(const Key('app-lock-after')));
        await tester.pumpAndSettle();
        await tester.tap(find.text(label).last);
        await tester.pumpAndSettle();
        expect(lock.settings.lockAfter, delay, reason: label);
      }
      expect(phone.prompts.where((p) => p.contains('change')), hasLength(4));
    });

    testWidgets('the scope can be the whole app or the sensitive areas',
        (tester) async {
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(phone, on);
      await lock.unlock(reason: 'u');
      await pumpTiles(tester, lock);
      await tester.tap(find.byKey(const Key('app-lock-scope')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('The whole app').last);
      await tester.pumpAndSettle();
      expect(lock.settings.scope, AppLockScope.wholeApp);

      // A change the person cannot confirm is not made.
      phone.answer = DeviceAuthResult.cancelled;
      await tester.tap(find.byKey(const Key('app-lock-scope')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Finance, Books and approval decisions').last);
      await tester.pumpAndSettle();
      expect(lock.settings.scope, AppLockScope.wholeApp);
      expect(find.textContaining('Not changed'), findsOneWidget);
    });

    testWidgets('turning off needs the check too', (tester) async {
      final phone = FakeDeviceAuth();
      final lock = await controllerWith(phone, on);
      await lock.unlock(reason: 'u');
      await pumpTiles(tester, lock);
      phone.answer = DeviceAuthResult.failed;
      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(lock.enabled, isTrue);
      phone.answer = DeviceAuthResult.success;
      await tester.tap(find.byKey(const Key('app-lock-switch')));
      await tester.pumpAndSettle();
      expect(lock.enabled, isFalse);
      expect(find.text('App lock is off.'), findsOneWidget);
    });

    testWidgets('reads right to left in Arabic', (tester) async {
      final lock = await controllerWith(FakeDeviceAuth(), on);
      await pumpTiles(tester, lock, locale: const Locale('ar'));
      expect(find.text('قفل التطبيق'), findsOneWidget);
      expect(find.text('القفل بعد'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
