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
}
