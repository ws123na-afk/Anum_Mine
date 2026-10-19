import 'package:http/http.dart' as http;

import '../features/push/push_messaging.dart';

/// What the app talks to outside itself. A real build passes nothing and
/// gets the configured API over the network, Firebase push and the platform
/// stores. The journey tests in `integration_test/` (docs/mobile-journeys.md)
/// pass a fake HTTP client that answers like the API, so every screen, the
/// real HTTP transport, streams and uploads run unchanged against it.
class AnumEnvironment {
  const AnumEnvironment({this.apiUrl, this.httpClient, this.pushMessaging});

  /// The API origin; null keeps `ANUM_API_URL` (or the build's default).
  final String? apiUrl;

  /// Makes the HTTP client every API call, stream, upload and download uses;
  /// null uses a real `package:http` client.
  final http.Client Function()? httpClient;

  /// Phone push; null uses Firebase Cloud Messaging.
  final PushMessaging? pushMessaging;
}
