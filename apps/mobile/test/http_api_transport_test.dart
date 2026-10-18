// HttpApiTransport (lib/src/infrastructure/mobile_adapters.dart): a hung or
// unreachable server becomes the app's "could not reach" error
// (ApiException with status 0) instead of an endless spinner, and a proxy's
// HTML error page keeps its status code.
import 'dart:async';
import 'dart:io';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

ApiRequest _get() =>
    ApiRequest(method: 'GET', uri: Uri.parse('https://api.example/x'));

void main() {
  test('a JSON answer is decoded', () async {
    final transport = HttpApiTransport(
        client: MockClient((_) async => http.Response('{"ok": true}', 200)));
    final response = await transport.send(_get());
    expect(response.statusCode, 200);
    expect(response.body, {'ok': true});
  });

  test('a server that never answers times out as status 0', () async {
    final never = Completer<http.Response>();
    final transport = HttpApiTransport(
        client: MockClient((_) => never.future),
        timeout: const Duration(milliseconds: 20));
    await expectLater(
        transport.send(_get()),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 0)
            .having((e) => e.message, 'message', contains('in time'))));
  });

  test('a connection failure is status 0, not a raw ClientException', () async {
    final transport = HttpApiTransport(
        client: MockClient((_) async => throw http.ClientException('reset')));
    await expectLater(
        transport.send(_get()),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 0)
            .having((e) => e.message, 'message', contains('reached'))));
  });

  test('a TLS failure reports only the failing hostname', () async {
    final transport = HttpApiTransport(
        client: MockClient((_) async =>
            throw const HandshakeException('TLS alert internal error')));
    await expectLater(
        transport.send(_get()),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 0)
            .having((e) => e.message, 'message', contains('api.example'))
            .having((e) => e.message, 'message', contains('TLS handshake'))));
  });

  test('an HTML 502 from the proxy keeps its status code', () async {
    final transport = HttpApiTransport(
        client: MockClient(
            (_) async => http.Response('<html>Bad gateway</html>', 502)));
    final response = await transport.send(_get());
    expect(response.statusCode, 502);
    expect(response.body, isNull);
  });

  test('a 200 that is not JSON is still an error', () async {
    final transport = HttpApiTransport(
        client: MockClient((_) async => http.Response('<html></html>', 200)));
    await expectLater(transport.send(_get()), throwsFormatException);
  });
}
