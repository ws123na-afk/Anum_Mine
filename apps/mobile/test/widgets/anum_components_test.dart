import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:anum_mobile/src/widgets/anum_components.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> pumpAt(
  WidgetTester tester,
  Widget child, {
  required Size size,
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(theme: AnumTheme.light(), home: Scaffold(body: child)));
  await tester.pump();
}

void main() {
  for (final size in <Size>[const Size(360, 800), const Size(1024, 1366)]) {
    testWidgets('operational card fits ${size.width.toInt()}px viewport', (tester) async {
      await pumpAt(
        tester,
        const Padding(
          padding: EdgeInsets.all(16),
          child: AnumOperationalCard(
            title: 'Prepare a governed quarterly operations review',
            metadata: 'Workspace / Operations / Updated just now',
            status: AnumStatus.approval,
          ),
        ),
        size: size,
      );

      expect(find.text('Prepare a governed quarterly operations review'), findsOneWidget);
      expect(find.bySemanticsLabel('Status: Approval required'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('feedback exposes its message and usable retry action', (tester) async {
    var retries = 0;
    await pumpAt(
      tester,
      AnumFeedback(
        kind: AnumFeedbackKind.offline,
        message: 'The workspace is offline.',
        onRetry: () => retries++,
      ),
      size: const Size(360, 800),
    );

    expect(find.text('The workspace is offline.'), findsOneWidget);
    final retryFinder = find.widgetWithText(OutlinedButton, 'Retry');
    final retrySize = tester.getSize(retryFinder);
    expect(retrySize.width, greaterThanOrEqualTo(48));
    expect(retrySize.height, greaterThanOrEqualTo(48));
    await tester.tap(retryFinder);
    expect(retries, 1);
  });

  testWidgets('every status has a spoken label', (tester) async {
    await pumpAt(
      tester,
      Wrap(children: AnumStatus.values.map((status) => AnumStatusBadge(status: status)).toList()),
      size: const Size(1024, 768),
    );

    for (final label in <String>[
      'Running',
      'Approval required',
      'Completed',
      'Failed',
      'Queued',
      'Paused',
    ]) {
      expect(find.bySemanticsLabel('Status: $label'), findsOneWidget);
    }
  });
}
