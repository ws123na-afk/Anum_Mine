// Screen privacy (docs/mobile.md#screen-privacy, owner decision 12 Oct 2026):
// the committed native projects keep the protections. The behaviour itself
// needs a device (see the real-device checklist).
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android marks the main window secure before Flutter starts', () {
    final activity =
        File('android/app/src/main/kotlin/com/anum/anum_mobile/MainActivity.kt')
            .readAsStringSync();
    final flag = activity.indexOf('WindowManager.LayoutParams.FLAG_SECURE');
    final superCall = activity.indexOf('super.onCreate');
    expect(flag, isNonNegative);
    expect(activity, contains('window.setFlags('));
    expect(flag, lessThan(superCall));
  });

  test('iOS covers the app in the switcher and while the screen is captured',
      () {
    final delegate = File('ios/Runner/AppDelegate.swift').readAsStringSync();
    expect(delegate, contains('privacyCover.start()'));
    for (final signal in [
      'UIScene.willDeactivateNotification',
      'UIScene.didActivateNotification',
      'UIScreen.capturedDidChangeNotification',
      'sceneCaptureState',
    ]) {
      expect(delegate, contains(signal), reason: signal);
    }
  });
}
