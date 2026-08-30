import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('router declares the complete onboarding and workbench surface', () {
    final source = Directory('lib')
        .listSync(recursive: true)
        .whereType<File>()
        .where((file) => file.path.endsWith('.dart'))
        .map((file) => file.readAsStringSync())
        .join('\n');
    for (final route in <String>[
      '/splash',
      '/sign-in',
      '/workspace-setup',
      '/model-setup',
      '/home',
      '/tasks',
      '/tasks/:id',
      '/voice',
      '/approvals',
      '/automations',
      '/files',
    ]) {
      expect(source, contains(route), reason: 'Missing required route $route');
    }
  });

  test('root application keeps credentials out of source', () {
    final files = Directory('lib').listSync(recursive: true).whereType<File>();
    final source = files.map((file) => file.readAsStringSync()).join('\n');
    expect(source, isNot(contains(RegExp(r'sk-[A-Za-z0-9]{20,}'))));
    expect(source, isNot(contains('BEGIN PRIVATE KEY')));
  });

  test('workbench exposes every implemented supervision destination', () {
    final source = File('lib/features/workspace/workspace_home.dart').readAsStringSync();
    for (final label in <String>['tasks', 'voice', 'approvals', 'automation', 'resources']) {
      expect(source, contains("label: l.t('$label')"), reason: 'Missing navigation destination $label');
    }
    expect(source, contains('pendingApprovals'), reason: 'Approvals must expose a visible pending count');
    expect(source, contains("tooltip: l.t('refresh')"), reason: 'Icon-only refresh requires an accessible name');
  });

  test('voice flow keeps visual review and permission recovery explicit', () {
    final source = File('lib/features/voice/voice_screen.dart').readAsStringSync();
    expect(source, contains('Review command'));
    expect(source, contains('visual approval'));
    expect(source, contains('Open app settings'));
    expect(source, contains('Discard transcript'));
    expect(source, contains('Arabic (Saudi Arabia)'));
  });

  test('mobile API configuration is build-time and secure sessions are used', () {
    final app = File('lib/src/anum_app.dart').readAsStringSync();
    expect(app, contains("String.fromEnvironment(\n      'ANUM_API_URL'"));
    expect(app, contains('SecureSessionStore()'));
    expect(app, isNot(contains('apiKey: \'sk-')));
  });

  test('declared routes are wired to navigation and guarded by authentication', () {
    final source = File('lib/src/anum_app.dart').readAsStringSync();
    expect(source, contains('onGenerateRoute: _onGenerateRoute'));
    expect(source, contains('_AuthenticatedDestination'));
    expect(source, contains("name.startsWith('/tasks/')"));
    expect(source, contains('Uri.decodeComponent'));
  });

  test('app restores navigation and supports English and Arabic directionality', () {
    final app = File('lib/src/anum_app.dart').readAsStringSync();
    final workspace = File('lib/features/workspace/workspace_home.dart').readAsStringSync();
    expect(app, contains("restorationScopeId: 'anum_mobile'"));
    expect(app, contains("Locale('ar')"));
    expect(app, contains('GlobalMaterialLocalizations.delegates'));
    expect(workspace, contains('RestorationMixin'));
    expect(workspace, contains('RestorableInt'));
    expect(workspace, contains('NavigationRail'));
    expect(workspace, contains('constraints.maxWidth >= 840'));
  });
}
