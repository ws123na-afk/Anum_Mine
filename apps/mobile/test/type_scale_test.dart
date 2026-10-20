// Type scale guard (docs/design/visual-identity.md#type): a font size is
// written once, in lib/src/theme/anum_type.dart, and everything else uses
// AnumType.* (or a TextTheme style). Mirrors apps/web/test/typeScale.test.ts.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

// Raw sizes that are justified. Format 'path:snippet', with a reason.
const _exceptions = <String, String>{
  // The initials scale with the avatar's own diameter, so there is no fixed
  // step to name.
  'lib/src/widgets/brand.dart': 'fontSize: size * 0.36',
};

final _rawSize =
    RegExp(r'fontSize:\s*(?:[\d.]|[^,)\n]*[?*+/-]\s*[\d.])[^,)\n]*');

List<File> _dartFiles(String dir) => Directory(dir)
    .listSync(recursive: true)
    .whereType<File>()
    .where((f) => f.path.endsWith('.dart'))
    .toList();

void main() {
  test('no Dart source writes a raw fontSize outside the token file', () {
    final offenders = <String>[];
    for (final file in [..._dartFiles('lib'), ..._dartFiles('test')]) {
      final path = file.path.replaceAll(r'\', '/');
      if (path == 'lib/src/theme/anum_type.dart' ||
          path == 'test/type_scale_test.dart') {
        continue;
      }
      final lines = file.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final match = _rawSize.firstMatch(lines[i]);
        if (match == null) continue;
        if (_exceptions[path] == match.group(0)!.trim()) continue;
        offenders.add('$path:${i + 1}: ${lines[i].trim()}');
      }
    }
    expect(offenders, isEmpty,
        reason: 'use AnumType.* from lib/src/theme/anum_type.dart '
            '(docs/design/visual-identity.md#type)');
  });

  test('every recorded exception still exists', () {
    for (final entry in _exceptions.entries) {
      expect(File(entry.key).readAsStringSync(), contains(entry.value),
          reason: 'delete the stale exception for ${entry.key}');
    }
  });

  test('the token scale is ascending and has no duplicate sizes', () {
    final source = File('lib/src/theme/anum_type.dart').readAsStringSync();
    final sizes = RegExp(r'static const double \w+ = ([\d.]+);')
        .allMatches(source)
        .map((m) => double.parse(m.group(1)!))
        .toList();
    expect(sizes.length, greaterThan(15));
    expect(sizes.toSet().length, sizes.length, reason: 'duplicate size');
    expect([...sizes]..sort(), sizes, reason: 'keep the scale ascending');
  });
}
