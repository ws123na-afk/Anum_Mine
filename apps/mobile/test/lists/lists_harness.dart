// Shared set-up for the ANUM 360 list screens' tests: the app theme, the
// English and Arabic catalogs, a size, a text scale and a direction.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const listWidths = [320.0, 390.0, 768.0, 1024.0, 1440.0];

Future<void> pumpList(WidgetTester tester, Widget screen,
    {double width = 390,
    double height = 1400,
    double text = 1,
    String locale = 'en',
    bool light = false,
    bool scaffold = true}) async {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: light ? AnumTheme.light() : AnumTheme.dark(),
    locale: Locale(locale),
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(text)),
        child: child!),
    home: scaffold ? Scaffold(body: screen) : screen,
  ));
  await tester.pumpAndSettle();
}

/// The two accessibility guidelines every list screen must meet.
Future<void> expectTapTargets(WidgetTester tester) async {
  final handle = tester.ensureSemantics();
  await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
  await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
  handle.dispose();
}

/// An API transport answering from a handler, recording every request.
class ScriptedTransport implements ApiTransport {
  ScriptedTransport(this.handler);
  final ApiResponse Function(ApiRequest request) handler;
  final requests = <ApiRequest>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    return handler(request);
  }
}

Future<AnumApiClient> scriptedClient(ScriptedTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 't',
          workspaceId: 'w',
          userId: 'user_me',
          roles: ['owner'])));
  return AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions);
}
