import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('Flutter test harness renders the ANUM brand', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: Text('ANUM'))),
    );

    expect(find.text('ANUM'), findsOneWidget);
  });
}
