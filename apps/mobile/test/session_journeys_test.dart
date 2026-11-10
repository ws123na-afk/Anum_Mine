// The sign-out and session-end journeys (docs/mobile.md#session-end), also
// run by the plain `flutter test` so they are checked on every change. The
// same journeys are registered in integration_test/journeys_test.dart.
import '../integration_test/journeys/session.dart';

void main() => sessionJourneys();
