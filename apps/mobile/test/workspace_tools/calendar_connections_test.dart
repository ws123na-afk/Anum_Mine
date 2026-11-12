// Calendar connections on the phone (docs/calendar.md#connecting-on-the-phone):
// connecting through the system browser and the app's own return, choosing
// the calendars that sync, Sync now and Disconnect, every state, and the
// layout at every width, at 200 percent text and in Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/calendar/calendar_connections.dart';
import 'package:anum_mobile/features/calendar/calendar_connections_screen.dart';
import 'package:anum_mobile/features/calendar/calendar_models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'harness.dart';

const _google = SyncProviderSetup(
    provider: 'google', name: 'Google Calendar', configured: true);
const _microsoft = SyncProviderSetup(
    provider: 'microsoft',
    name: 'Microsoft 365 Calendar',
    missing: ['ANUM_CALENDAR_MICROSOFT_CLIENT_ID']);

SyncConnection connected(
        {String status = 'connected',
        List<CalendarChoice> calendars = const []}) =>
    SyncConnection(
        id: 'cal_1',
        provider: 'google',
        providerName: 'Google Calendar',
        email: 'owner@example.com',
        status: status,
        eventsSynced: 4,
        lastError: status == 'connected' ? null : 'Token has been expired',
        calendars: calendars);

class FakeConnections implements CalendarConnectionsRepository {
  bool canManage = true;
  int setupFails = 0;
  int listFails = 0;
  bool listEmpty = false;
  List<SyncConnection> connections = [];
  final calls = <String>[];

  /// What `complete` answers, in order: 409 (not back yet), 400, or done.
  final answers = <int>[];

  var calendarsList = const [
    CalendarChoice(
        id: 'owner@example.com', name: 'Owner', primary: true, selected: true),
    CalendarChoice(id: 'team', name: 'Team'),
    CalendarChoice(id: 'holidays', name: 'UAE holidays'),
  ];

  @override
  Future<CalendarSyncStatus> setup() async {
    if (setupFails != 0) throw ApiException(setupFails, 'Calendar is down');
    return CalendarSyncStatus(connections,
        canManage: canManage, providers: const [_google, _microsoft]);
  }

  @override
  Future<ConnectStart> start(String provider) async {
    calls.add('start $provider');
    return ConnectStart(
        Uri.parse('https://accounts.example.com/o/oauth2/auth?state=s'),
        'coc_1');
  }

  @override
  Future<SyncConnection> complete(String request) async {
    calls.add('complete $request');
    final answer = answers.isEmpty ? 201 : answers.removeAt(0);
    if (answer == 409) {
      throw const ApiException(409,
          "The provider has not sent you back yet. Finish the sign-in in the provider's window.");
    }
    if (answer != 201) {
      throw ApiException(
          answer, 'Google Calendar did not grant access: access_denied');
    }
    connections = [connected()];
    return connections.single;
  }

  @override
  Future<SyncConnection> syncNow(String connectionId) async {
    calls.add('sync $connectionId');
    return connections.single;
  }

  @override
  Future<String> disconnect(String connectionId) async {
    calls.add('disconnect $connectionId');
    connections = [];
    return 'Google Calendar is disconnected. The events stay in ANUM.';
  }

  @override
  Future<CalendarChoices> calendars(String connectionId) async {
    calls.add('calendars $connectionId');
    if (listFails != 0) {
      throw ApiException(listFails,
          'Google Calendar did not let ANUM list this account\'s calendars.');
    }
    return CalendarChoices(listEmpty ? const [] : calendarsList, maxExtra: 1);
  }

  @override
  Future<CalendarChoices> choose(
      String connectionId, List<String> selected) async {
    calls.add('choose $connectionId ${selected.join(',')}');
    connections = [
      connected(calendars: [
        for (final c in calendarsList)
          CalendarChoice(
              id: c.id,
              name: c.name,
              primary: c.primary,
              selected: c.primary || selected.contains(c.id))
      ])
    ];
    return CalendarChoices(connections.single.calendars);
  }
}

void main() {
  late List<Uri> opened;
  late bool browserOpens;

  CalendarConnectionsController controllerFor(FakeConnections repo) =>
      CalendarConnectionsController(repo, openUrl: (uri) async {
        opened.add(uri);
        return browserOpens;
      });

  setUp(() {
    opened = [];
    browserOpens = true;
  });

  Future<void> pump(WidgetTester tester, FakeConnections repo,
      {double width = 390, Locale locale = const Locale('en')}) async {
    await pumpScreen(
        tester, CalendarConnectionsScreen(controller: controllerFor(repo)),
        width: width, height: 1600, locale: locale);
    await tester.pumpAndSettle();
  }

  testWidgets(
      'an owner connects through the browser; the app finishes on return',
      (tester) async {
    final repo = FakeConnections()..answers.addAll([409, 201]);
    await pump(tester, repo);
    expect(find.text('No calendar connected yet'), findsOneWidget);
    expect(find.textContaining('ANUM_CALENDAR_MICROSOFT_CLIENT_ID'),
        findsOneWidget);
    expect(find.byKey(const Key('calconn-connect-microsoft')), findsNothing);

    await tester.tap(find.byKey(const Key('calconn-connect-google')));
    await tester.pumpAndSettle();
    expect(repo.calls, ['start google']);
    expect(opened.single.host, 'accounts.example.com');
    expect(find.textContaining('Finish signing in to Google Calendar'),
        findsOneWidget);

    // Back in the app before the sign-in finished: it keeps waiting quietly.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pumpAndSettle();
    expect(repo.calls.last, 'complete coc_1');
    expect(find.byKey(const Key('calconn-progress')), findsOneWidget);
    expect(find.byKey(const Key('calconn-waiting-error')), findsNothing);

    // Then signed in: "I have signed in" finishes it.
    await tester.tap(find.byKey(const Key('calconn-finish')));
    await tester.pumpAndSettle();
    expect(find.text('Connected owner@example.com. Its events are syncing.'),
        findsOneWidget);
    expect(find.byKey(const Key('calconn-cal_1')), findsOneWidget);
    expect(find.byKey(const Key('calconn-progress')), findsNothing);
  });

  testWidgets('a declined sign-in or a browser that will not open says so',
      (tester) async {
    final repo = FakeConnections()..answers.add(400);
    browserOpens = false;
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('calconn-connect-google')));
    await tester.pumpAndSettle();
    expect(find.textContaining('The browser could not open'), findsOneWidget);

    browserOpens = true;
    await tester.tap(find.byKey(const Key('calconn-connect-google')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('calconn-finish')));
    await tester.pumpAndSettle();
    expect(find.textContaining('did not grant access: access_denied'),
        findsOneWidget);
    expect(find.byKey(const Key('calconn-progress')), findsNothing);
    // Cancel while waiting forgets the request.
    await tester.tap(find.byKey(const Key('calconn-connect-google')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('calconn-cancel-connect')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('calconn-progress')), findsNothing);
  });

  testWidgets('choosing calendars: the primary always syncs, a limit, saved',
      (tester) async {
    final repo = FakeConnections()..connections = [connected()];
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('calconn-choose-cal_1')));
    await tester.pumpAndSettle();
    final primary = tester.widget<CheckboxListTile>(
        find.byKey(const Key('calchoice-owner@example.com')));
    expect(primary.value, isTrue);
    expect(primary.onChanged, isNull);
    await tester.tap(find.text('Team'));
    await tester.tap(find.text('UAE holidays'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('calchoice-save')));
    await tester.pumpAndSettle();
    expect(find.text('Choose at most 1 calendars besides the primary one.'),
        findsOneWidget);
    await tester.tap(find.text('UAE holidays'));
    await tester.tap(find.byKey(const Key('calchoice-save')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('choose cal_1 team'));
    expect(find.text('Also synced, read only: Team'), findsOneWidget);
  });

  testWidgets('a calendar list that fails or is empty offers a retry',
      (tester) async {
    final repo = FakeConnections()
      ..connections = [connected()]
      ..listFails = 409;
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('calconn-choose-cal_1')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('calchoice-error')), findsOneWidget);
    repo
      ..listFails = 0
      ..listEmpty = true;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('calchoice-empty')), findsOneWidget);
  });

  testWidgets('sync now, reconnect and disconnect after a confirmation',
      (tester) async {
    final repo = FakeConnections()
      ..connections = [connected(status: 'reauth_required')];
    await pump(tester, repo);
    expect(find.textContaining('presses Connect Google Calendar again'),
        findsOneWidget);
    expect(find.byKey(const Key('calconn-sync-cal_1')), findsNothing);
    await tester.tap(find.byKey(const Key('calconn-reconnect-cal_1')));
    await tester.pumpAndSettle();
    expect(repo.calls, ['start google']);
    await tester.tap(find.byKey(const Key('calconn-cancel-connect')));
    await tester.pumpAndSettle();

    repo.connections = [connected()];
    await pump(tester, repo);
    await tester.tap(find.byKey(const Key('calconn-sync-cal_1')));
    await tester.pumpAndSettle();
    expect(repo.calls.last, 'sync cal_1');
    await tester.tap(find.byKey(const Key('calconn-disconnect-cal_1')));
    await tester.pumpAndSettle();
    expect(find.text('Disconnect this calendar?'), findsOneWidget);
    await tester.tap(find.byKey(const Key('calconn-disconnect-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls.last, 'disconnect cal_1');
    expect(find.text('No calendar connected yet'), findsOneWidget);
  });

  testWidgets('a member sees the status and who can change it', (tester) async {
    final repo = FakeConnections()
      ..canManage = false
      ..connections = [connected()];
    await pump(tester, repo);
    expect(find.byKey(const Key('calconn-owners-only')), findsOneWidget);
    expect(find.byKey(const Key('calconn-connect-google')), findsNothing);
    expect(find.byKey(const Key('calconn-sync-cal_1')), findsNothing);
    expect(find.byKey(const Key('calconn-disconnect-cal_1')), findsNothing);
  });

  testWidgets('a refused or failed load says what happened', (tester) async {
    final repo = FakeConnections()..setupFails = 403;
    await pump(tester, repo);
    expect(find.byKey(const Key('calconn-denied')), findsOneWidget);
    repo.setupFails = 503;
    await pump(tester, repo);
    expect(find.byKey(const Key('calconn-error')), findsOneWidget);
    repo.setupFails = 0;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('No calendar connected yet'), findsOneWidget);
  });

  testWidgets('connections fit every width, 200% text and Arabic',
      (tester) async {
    final repo = FakeConnections()
      ..connections = [
        connected(calendars: const [
          CalendarChoice(id: 'p', name: 'Owner', primary: true, selected: true),
          CalendarChoice(id: 'team', name: 'Team', selected: true),
        ])
      ];
    await checkLayouts(
        tester,
        () => CalendarConnectionsScreen(controller: controllerFor(repo)),
        find.byKey(const Key('calconn-cal_1')));
    await pump(tester, repo, width: 320, locale: const Locale('ar'));
    expect(find.text('ربط التقويمات'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
