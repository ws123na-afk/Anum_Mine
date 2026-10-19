import 'dart:io';

import 'package:anum_mobile/features/help/help_library.dart';
import 'package:flutter_test/flutter_test.dart';

/// The phone bundles copies of docs/help (docs/help.md). After editing an
/// article, run `node scripts/sync-help.mjs` from the repository root.
void main() {
  test('the bundled help articles match docs/help', () {
    Map<String, String> read(String dir) => {
          for (final file in Directory(dir).listSync().whereType<File>())
            if (file.path.endsWith('.md'))
              file.uri.pathSegments.last: file.readAsStringSync(),
        };
    final docs = read('../../docs/help');
    final assets = read('assets/help');
    expect(assets.keys.toSet(), docs.keys.toSet(),
        reason: 'Run node scripts/sync-help.mjs');
    for (final name in docs.keys) {
      expect(assets[name], docs[name],
          reason: '$name: run node scripts/sync-help.mjs');
      expect(parseHelpArticle(name, docs[name]!), isNotNull, reason: name);
    }
  });
}
