// Type scale guard (docs/design/visual-identity.md#type): a font size is
// written once, in lib/src/theme/anum_type.dart, and everything else uses
// AnumType.* (or a TextTheme style). Mirrors apps/web/test/typeScale.test.ts.
//
// The guard reads every complete `fontSize:` expression (across lines, with
// every ternary branch) and accepts only AnumType.* tokens, parentheses,
// ternary punctuation and plain identifiers. A number, or arithmetic of any
// kind, is a raw size.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

// Raw sizes that are justified: 'path' -> the exact expression, with a reason.
const _exceptions = <String, String>{
  // The initials scale with the avatar's own diameter, so there is no fixed
  // step to name.
  'lib/src/widgets/brand.dart': 'size * 0.36',
};

/// Every `fontSize:` expression in [source] as (offset, expression).
List<(int, String)> fontSizeExpressions(String source) {
  final found = <(int, String)>[];
  for (final m in RegExp(r'\bfontSize\s*:').allMatches(source)) {
    var depth = 0;
    var i = m.end;
    final start = i;
    while (i < source.length) {
      final c = source[i];
      if (c == '(' || c == '[' || c == '{') {
        depth++;
      } else if (c == ')' || c == ']' || c == '}') {
        if (depth == 0) break;
        depth--;
      } else if (c == ',' && depth == 0) {
        break;
      }
      i++;
    }
    found.add((m.start, source.substring(start, i).trim()));
  }
  return found;
}

/// True when [expression] is built only from tokens, not raw numbers or math.
bool usesTokensOnly(String expression) {
  final rest = expression
      .replaceAll(RegExp(r'//[^\n]*'), '')
      .replaceAll(RegExp(r'AnumType\.[A-Za-z0-9_]+'), 'T');
  if (RegExp(r'[0-9]').hasMatch(rest)) return false;
  if (RegExp(r'[*+/%~<>=!&|-]').hasMatch(rest)) return false;
  return true;
}

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
      final source = file.readAsStringSync();
      for (final (offset, expression) in fontSizeExpressions(source)) {
        if (usesTokensOnly(expression)) continue;
        if (_exceptions[path] == expression) continue;
        final line = '\n'.allMatches(source.substring(0, offset)).length + 1;
        offenders.add('$path:$line: fontSize: $expression');
      }
    }
    expect(offenders, isEmpty,
        reason: 'use AnumType.* from lib/src/theme/anum_type.dart '
            '(docs/design/visual-identity.md#type)');
  });

  test('the guard flags raw sizes in every shape', () {
    bool flagged(String code) =>
        fontSizeExpressions(code).any((e) => !usesTokensOnly(e.$2));
    expect(flagged('TextStyle(fontSize: 14)'), isTrue);
    expect(flagged('TextStyle(fontSize: 12.5, height: 1)'), isTrue);
    expect(flagged('TextStyle(fontSize: compact ? AnumType.sm : 12)'), isTrue);
    expect(flagged('TextStyle(fontSize: compact ? 12 : AnumType.sm)'), isTrue);
    expect(
        flagged('TextStyle(\n  fontSize: compact\n      ? AnumType.sm\n'
            '      : 13,\n)'),
        isTrue);
    expect(flagged('TextStyle(fontSize: size * factor)'), isTrue);
    expect(flagged('TextStyle(fontSize: base.fontSize! + scale)'), isTrue);
    expect(flagged('TextStyle(fontSize: (a ? 1 : 2).toDouble())'), isTrue);
    expect(flagged('TextStyle(fontSize: AnumType.sm)'), isFalse);
    expect(flagged('TextStyle(fontSize: a ? AnumType.sm : AnumType.x2l)'),
        isFalse);
    expect(flagged('_ui(fontSize: fontSize)'), isFalse);
  });

  test('every recorded exception still exists', () {
    for (final entry in _exceptions.entries) {
      final expressions =
          fontSizeExpressions(File(entry.key).readAsStringSync())
              .map((e) => e.$2);
      expect(expressions, contains(entry.value),
          reason: 'delete the stale exception for ${entry.key}');
    }
  });

  test('the token scale is ascending, without duplicates, and matches web', () {
    final source = File('lib/src/theme/anum_type.dart').readAsStringSync();
    final sizes = RegExp(r'static const double \w+ = ([\d.]+);')
        .allMatches(source)
        .map((m) => double.parse(m.group(1)!))
        .toList();
    expect(sizes.length, greaterThan(15));
    expect(sizes.toSet().length, sizes.length, reason: 'duplicate size');
    expect([...sizes]..sort(), sizes, reason: 'keep the scale ascending');
    // Whole-step names carry the web values (apps/web/src/typography.css).
    const web = {
      'xxs': 11,
      'xs': 12,
      'sm': 13,
      'md': 14,
      'base': 15,
      'lg': 16,
      'xl': 18,
      'x2l': 20,
      'x3l': 22,
      'x4l': 24,
      'x5l': 28,
      'x6l': 32,
    };
    web.forEach((name, px) {
      expect(source, contains('static const double $name = $px;'),
          reason: name);
    });
  });
}
