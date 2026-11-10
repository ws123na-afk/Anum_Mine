// Every app-wide object the app creates is either reset on sign-out or
// declared to hold nothing of the signed-in person (docs/mobile.md#per-user-state).
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  final source = File('lib/src/anum_app.dart').readAsStringSync();
  final state = source.substring(source.indexOf('class _AnumAppState'),
      source.indexOf('class _AuthenticatedDestination'));

  Set<String> keysOf(String name) {
    final start = state.indexOf(name);
    expect(start, isNot(-1), reason: '$name is missing from _AnumAppState');
    final block = state.substring(start, state.indexOf('};', start));
    return {
      for (final m
          in RegExp(r"^\s*'(\w+)':", multiLine: true).allMatches(block))
        m.group(1)!
    };
  }

  test('each app-wide field is reset on sign-out or declared without user data',
      () {
    final fields = {
      for (final m
          in RegExp(r'^  late final [\w.<>?, ]+ (\w+);', multiLine: true)
              .allMatches(state))
        m.group(1)!
    };
    final reset = keysOf('late final Map<String, VoidCallback> _userData');
    final exempt = keysOf('static const _noUserData');
    expect(fields.length, greaterThan(20),
        reason: 'the field pattern no longer matches _AnumAppState');
    expect(reset.intersection(exempt), isEmpty);
    expect(fields.difference({...reset, ...exempt}), isEmpty,
        reason: 'Add each new field to _userData (with its reset) or to '
            '_noUserData (with the reason) in lib/src/anum_app.dart.');
    expect({...reset, ...exempt}.difference(fields), isEmpty,
        reason: 'These names are no longer fields of _AnumAppState.');
  });

  test('sign-out runs every reset', () {
    expect(state, contains('for (final reset in _userData.values)'));
  });
}
