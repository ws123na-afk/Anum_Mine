import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('authentication exposes every approved journey state', () {
    final source = [
      File('lib/features/auth/auth_screens.dart').readAsStringSync(),
      // ANUM 360 splash, onboarding, Login and Create account.
      File('lib/features/auth/welcome_screens.dart').readAsStringSync(),
    ].join('\n');
    for (final label in <String>[
      "'authRestoring'",
      "'loginTitle'",
      "'signupTitle'",
      "'onboarding1Title'",
      // Sign-in and first run read their words from the catalog
      // (auth_strings.dart), in English and Arabic.
      "'auth.signIn.title'",
      "'auth.setup.title'",
      "'auth.model.title'",
      "'auth.failure.offline'",
      "'auth.failure.permission'",
      "'auth.failure.session'",
    ]) {
      expect(source, contains(label));
    }
    final catalog =
        File('lib/src/localization/auth_strings.dart').readAsStringSync();
    for (final label in <String>[
      'Sign in to ANUM',
      'Set up your workspace',
      'Connect a model',
      'You are offline',
      'Access denied',
      'Session expired',
    ]) {
      expect(catalog, contains(label));
    }
    expect(source, contains('EdgeInsetsDirectional'));
    expect(source, contains('AlignmentDirectional'));
    expect(source, contains("'openai_compatible'"));
  });

  test('authentication repository covers recovery and workspace rotation APIs',
      () {
    final source =
        File('lib/features/auth/auth_repository.dart').readAsStringSync();
    for (final path in <String>[
      '/auth/local/otp/request',
      '/auth/local/otp/verify',
      '/auth/local/password/forgot',
      '/auth/local/password/reset',
      '/auth/local/workspace/switch'
    ]) {
      expect(source, contains(path));
    }
    expect(source, contains("'password': password"));
    expect(source, contains('sessions.write(session)'));
  });

  test('settings exposes account security and preference controls', () {
    final source =
        File('lib/features/settings/settings_screen.dart').readAsStringSync();
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
