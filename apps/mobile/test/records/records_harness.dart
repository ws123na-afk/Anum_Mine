// Shared harness for the record modules' tests (vault, attendance,
// vehicles, purchases, business books): a fake API transport that answers
// by method and path with the API's real JSON shapes, a session, and a pump
// that sets the viewport, text scale and language.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

/// Answers one request, or null to fall through to 404.
typedef FakeRoute = ApiResponse? Function(ApiRequest request);

class FakeApi implements ApiTransport {
  FakeApi(this.route);
  FakeRoute route;
  final requests = <ApiRequest>[];

  /// Every request fails with [status] and [message] when set.
  int? failWith;
  String failMessage = 'Server error';
  String? failCode;

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    if (failWith != null) {
      return ApiResponse(statusCode: failWith!, body: {
        'detail': failMessage,
        if (failCode != null) 'code': failCode,
      });
    }
    return route(request) ??
        const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }

  Iterable<ApiRequest> sent(String method, String path) =>
      requests.where((r) => r.method == method && r.uri.path == path);

  AnumApiClient client() => AnumApiClient(
      baseUri: Uri.parse('https://api.example.test/'),
      transport: this,
      sessions: FakeSessions());
}

ApiResponse ok(JsonMap body, [int status = 200]) =>
    ApiResponse(statusCode: status, body: body);

/// A JSON list as the HTTP transport hands it over.
ApiResponse okList(List<Object?> items) =>
    ApiResponse(statusCode: 200, body: {'data': items});

ApiResponse refuse(int status, String detail, [String? code]) =>
    ApiResponse(statusCode: status, body: {
      'detail': detail,
      if (code != null) 'code': code,
    });

class FakeSessions implements SessionStore {
  @override
  Future<void> clear() async {}
  @override
  Future<LocalSession?> read() async => LocalSession(
      accessToken: 'example',
      tokenType: 'bearer',
      context: const TenantContext(
          tenantId: 'tenant_a',
          workspaceId: 'workspace_a',
          userId: 'user_me',
          roles: ['member']),
      expiresAt: DateTime.now().add(const Duration(hours: 1)));
  @override
  Future<void> write(LocalSession session) async {}
}

const phone = Size(390, 844);
const narrow = Size(320, 640);
const tablet = Size(1024, 768);

Future<void> pumpModule(
  WidgetTester tester,
  Widget screen, {
  Size size = phone,
  double textScale = 1,
  Locale locale = const Locale('en'),
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    key: UniqueKey(),
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: screen,
  ));
  await tester.pumpAndSettle();
}

/// No layout overflow or other exception was thrown while rendering.
void expectClean(WidgetTester tester) => expect(tester.takeException(), isNull);

/// Scrolls the first scrollable until [finder] is built, then taps it.
Future<void> tapVisible(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

/// Lets a snack bar time out so it no longer covers the page's last
/// controls.
Future<void> clearToasts(WidgetTester tester) async {
  await tester.pump(const Duration(seconds: 5));
  await tester.pumpAndSettle();
}
