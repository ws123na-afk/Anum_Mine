import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('authentication exposes every approved journey state', () {
    final source = File('lib/features/auth/auth_screens.dart').readAsStringSync();
    for (final label in <String>[
      'Your work, coordinated.',
      'Sign in to ANUM',
      'Set up your workspace',
      'Connect a model',
      'You are offline',
      'Access denied',
      'Session expired',
    ]) {
      expect(source, contains(label));
    }
    expect(source, contains('EdgeInsetsDirectional'));
    expect(source, contains('AlignmentDirectional'));
    expect(source, contains("'openai_compatible'"));
  });

  test('authentication repository covers recovery and workspace rotation APIs', () {
    final source = File('lib/features/auth/auth_repository.dart').readAsStringSync();
    for (final path in <String>['/auth/local/otp/request','/auth/local/otp/verify','/auth/local/password/forgot','/auth/local/password/reset','/auth/local/workspace/switch']) {
      expect(source, contains(path));
    }
    expect(source, contains("'password': password"));
    expect(source, contains('sessions.write(session)'));
  });

  test('settings exposes account security and preference controls', () {
    final source = File('lib/features/settings/settings_screen.dart').readAsStringSync();
    for (final label in <String>[
      'Profile and session',
      'Model connection',
      'Notifications',
      'Security',
      'Test connection',
      'Sign out',
      'Permission required',
      'You are offline',
    ]) {
      expect(source, contains(label));
    }
    expect(source, contains('SwitchListTile'));
    expect(source, contains('EdgeInsetsDirectional'));
  });
}
