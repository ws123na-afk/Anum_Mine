// End-to-end user journeys on a phone-sized screen (docs/mobile-journeys.md).
//
// The whole app runs against a fake HTTP client that answers like the API
// (support/fake_api.dart, support/fixtures.dart), so every screen, the real
// HTTP transport, the event streams and the file uploads are exercised.
//
// Run headless, as CI does:
//   flutter test integration_test -d flutter-tester
//
// One entry file: the flutter-tester device starts the app once per file and
// cannot start it again in the same run, so every journey is registered here.
import 'package:integration_test/integration_test.dart';

import 'journeys/business.dart';
import 'journeys/calls.dart';
import 'journeys/chat.dart';
import 'journeys/email.dart';
import 'journeys/files.dart';
import 'journeys/settings_language.dart';
import 'journeys/task_approval.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  taskApprovalJourneys();
  chatJourneys();
  emailJourneys();
  businessJourneys();
  callsJourneys();
  settingsLanguageJourneys();
  filesJourneys();
}
