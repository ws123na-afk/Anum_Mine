// The shared KPI grid and tile (AnumMetricGrid, AnumMetric in
// lib/src/widgets/depth.dart): rows are spread evenly so no tile is left
// alone, and values use a solid colour instead of gradient text.
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/depth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> _pump(WidgetTester tester, double width, int count,
    {double textScale = 1}) async {
  tester.view.physicalSize = Size(width, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: Scaffold(
      body: AnumPage(children: [
        AnumMetricGrid(children: [
          for (var i = 1; i <= count; i++)
            AnumMetric(
              key: ValueKey('kpi$i'),
              label: 'KPI $i',
              value: '$i,000',
              icon: Icons.insights_outlined,
            ),
        ]),
      ]),
    ),
  ));
  await tester.pumpAndSettle();
}

Rect _tile(WidgetTester tester, int i) =>
    tester.getRect(find.byKey(ValueKey('kpi$i')));

/// The tiles grouped by row (same top), in order.
List<List<Rect>> _rows(WidgetTester tester, int count) {
  final rows = <List<Rect>>[];
  for (var i = 1; i <= count; i++) {
    final rect = _tile(tester, i);
    if (rows.isEmpty || (rows.last.first.top - rect.top).abs() > 0.5) {
      rows.add([rect]);
    } else {
      rows.last.add(rect);
    }
  }
  return rows;
}

void main() {
  test('row sizes are balanced, longer rows first', () {
    expect(AnumMetricGrid.rowSizes(5, 4), [3, 2]);
    expect(AnumMetricGrid.rowSizes(5, 2), [2, 2, 1]);
    expect(AnumMetricGrid.rowSizes(4, 4), [4]);
    expect(AnumMetricGrid.rowSizes(4, 2), [2, 2]);
    expect(AnumMetricGrid.rowSizes(3, 4), [3]);
    expect(AnumMetricGrid.rowSizes(8, 4), [4, 4]);
    expect(AnumMetricGrid.rowSizes(7, 4), [4, 3]);
    expect(AnumMetricGrid.rowSizes(9, 4), [3, 3, 3]);
    expect(AnumMetricGrid.rowSizes(0, 4), isEmpty);
  });

  testWidgets('5 tiles at 390 px: 2 + 2, and the fifth spans the row',
      (tester) async {
    await _pump(tester, 390, 5);
    expect(tester.takeException(), isNull);
    final rows = _rows(tester, 5);
    expect(rows.map((r) => r.length), [2, 2, 1]);
    final rowWidth = rows.first.last.right - rows.first.first.left;
    final last = rows.last.single;
    expect(last.width, moreOrLessEquals(rowWidth, epsilon: 0.5));
    expect(last.left, moreOrLessEquals(rows.first.first.left, epsilon: 0.5));
    expect(rows[0][0].width, moreOrLessEquals(rows[0][1].width, epsilon: 0.5));
  });

  testWidgets('5 tiles at 1024 px: 3 + 2, both rows filling the width',
      (tester) async {
    await _pump(tester, 1024, 5);
    expect(tester.takeException(), isNull);
    final rows = _rows(tester, 5);
    expect(rows.map((r) => r.length), [3, 2]);
    for (final row in rows) {
      expect(row.first.left,
          moreOrLessEquals(rows.first.first.left, epsilon: 0.5));
      expect(row.last.right,
          moreOrLessEquals(rows.first.last.right, epsilon: 0.5));
      for (final rect in row) {
        expect(rect.width, moreOrLessEquals(row.first.width, epsilon: 0.5));
      }
    }
  });

  testWidgets('4 tiles stay one row of 4 on wide screens and 2 + 2 on phones',
      (tester) async {
    await _pump(tester, 1024, 4);
    expect(_rows(tester, 4).map((r) => r.length), [4]);
    await _pump(tester, 390, 4);
    expect(_rows(tester, 4).map((r) => r.length), [2, 2]);
  });

  testWidgets('lays out at 320 px with 200% text', (tester) async {
    await _pump(tester, 320, 5, textScale: 2);
    expect(tester.takeException(), isNull);
  });

  testWidgets('values are solid text, not gradient', (tester) async {
    await _pump(tester, 390, 5);
    expect(find.byType(ShaderMask), findsNothing);
    final value = tester.widget<Text>(find.text('1,000'));
    final palette = tester.element(find.text('1,000')).palette;
    expect(value.style?.color, palette.text);
  });
}
