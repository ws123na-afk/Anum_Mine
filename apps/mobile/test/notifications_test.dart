// The phone's Notifications screen (docs/team-work.md#notifications-page):
// read-state and kind filters with counts, cursor paging, mark read and
// unread, delete with a confirmation, mark all read, opening the target,
// every state, both catalogs, and layouts from 320 to 1440 wide at 200%
// text in Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/team/notifications_strings.dart';
import 'package:anum_mobile/features/team/team_repository.dart';
import 'package:anum_mobile/features/team/team_screens.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

JsonMap _row(int n, String kind, {bool read = false, String? work}) => {
      'id': 'ntf_${n.toString().padLeft(3, '0')}',
      'user_id': 'user_owner',
      'kind': kind,
      'title': 'Notice $n',
      'body': 'Body of notice $n.',
      'work_item_id': work,
      'read_at': read ? '2026-10-11T11:00:00Z' : null,
      'email_status': 'not_requested',
      'email_attempts': 0,
      'email_error': null,
      'push_status': 'not_requested',
      'created_at': DateTime.utc(2026, 10, 11, 10)
          .subtract(Duration(minutes: n))
          .toIso8601String(),
    };

JsonMap _envelope(String code, String message) => {
      'error': {'code': code, 'message': message, 'correlation_id': 'c'}
    };

/// A stateful notifications API: filters, keyset cursor, read, unread,
/// delete and read-all act on the same rows.
class NotificationTransport implements ApiTransport {
  NotificationTransport(
      {int count = 3, this.denied = false, this.failList = false}) {
    rows = [
      for (var i = 1; i <= count; i++)
        _row(
            i,
            i == 1
                ? 'work_assigned'
                : i == 2
                    ? 'calendar_reminder'
                    : 'brand_new_kind',
            read: i == 3,
            work: i == 1 ? 'work_1' : null),
    ];
  }
  late List<JsonMap> rows;
  final bool denied;
  bool failList;
  bool failDelete = false;
  final requests = <ApiRequest>[];

  JsonMap? _find(String id) {
    for (final r in rows) {
      if (r['id'] == id) return r;
    }
    return null;
  }

  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    final path = r.uri.path;
    if (denied) {
      return ApiResponse(
          statusCode: 403,
          body: _envelope(
              'forbidden', 'You are not a member of this workspace.'));
    }
    if (path == '/api/v1/notifications' && r.method == 'GET') {
      if (failList) {
        return ApiResponse(
            statusCode: 503,
            body: _envelope('unavailable', 'The database is not reachable.'));
      }
      final q = r.uri.queryParameters;
      var list = rows.where((x) {
        final read = x['read_at'] != null;
        if (q['state'] == 'unread' && read) return false;
        if (q['state'] == 'read' && !read) return false;
        if (q['kind'] != null && x['kind'] != q['kind']) return false;
        return true;
      }).toList();
      final cursor = q['cursor'];
      if (cursor != null) {
        final at = list.indexWhere((x) => x['id'] == cursor);
        list = list.sublist(at + 1);
      }
      final limit = int.parse(q['limit'] ?? '30');
      final page = list.take(limit).toList();
      return ApiResponse(statusCode: 200, body: {
        'items': page,
        'unread': rows.where((x) => x['read_at'] == null).length,
        'next_cursor': list.length > limit ? page.last['id'] : null,
      });
    }
    if (path == '/api/v1/notifications/kinds') {
      final counts = <String, List<int>>{};
      for (final x in rows) {
        final c = counts.putIfAbsent(x['kind']! as String, () => [0, 0]);
        c[0]++;
        if (x['read_at'] == null) c[1]++;
      }
      return ApiResponse(statusCode: 200, body: {
        'items': [
          for (final e in counts.entries)
            {'kind': e.key, 'total': e.value[0], 'unread': e.value[1]}
        ]
      });
    }
    if (path == '/api/v1/notifications/read-all') {
      for (final x in rows) {
        x['read_at'] ??= '2026-10-11T12:00:00Z';
      }
      return const ApiResponse(statusCode: 200, body: {'updated': 1});
    }
    final match = RegExp(r'^/api/v1/notifications/([^/]+)(?:/(read|unread))?$')
        .firstMatch(path);
    if (match != null) {
      final row = _find(match.group(1)!);
      if (row == null) {
        return ApiResponse(
            statusCode: 404,
            body: _envelope('not_found', 'Notification not found'));
      }
      if (r.method == 'DELETE') {
        if (failDelete) {
          return ApiResponse(
              statusCode: 503,
              body: _envelope('unavailable', 'Try again soon.'));
        }
        rows.remove(row);
        return const ApiResponse(statusCode: 204, body: null);
      }
      row['read_at'] = match.group(2) == 'read' ? '2026-10-11T12:00:00Z' : null;
      return ApiResponse(statusCode: 200, body: row);
    }
    if (path == '/api/v1/work-items/work_1') {
      return const ApiResponse(statusCode: 404, body: null);
    }
    return const ApiResponse(statusCode: 200, body: {'data': []});
  }
}

Future<TeamRepository> _repo(ApiTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_owner',
          roles: ['owner'])));
  return ApiTeamRepository(AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions));
}

Widget _app(Widget child,
        {Locale locale = const Locale('en'), List<String>? routes}) =>
    MaterialApp(
        theme: AnumTheme.dark(),
        locale: locale,
        supportedLocales: const [Locale('en'), Locale('ar')],
        localizationsDelegates: const [
          AnumLocalizations.delegate,
          ...GlobalMaterialLocalizations.delegates
        ],
        onGenerateRoute: (settings) {
          routes?.add(settings.name!);
          return MaterialPageRoute<void>(
              settings: settings,
              builder: (_) => Scaffold(body: Text('route ${settings.name}')));
        },
        home: child);

void main() {
  test('English and Arabic notification catalogs match, every kind is named',
      () {
    expect(NotificationText.ar.keys.toSet(), NotificationText.en.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in NotificationText.en.keys) {
      Set<String> names(String v) =>
          placeholder.allMatches(v).map((m) => m.group(1)!).toSet();
      expect(names(NotificationText.ar[key]!), names(NotificationText.en[key]!),
          reason: key);
    }
    for (final kind in NotificationText.knownKinds) {
      expect(NotificationText.en.containsKey('kind.$kind'), isTrue,
          reason: kind);
    }
    expect(const NotificationText(false).kind('brand_new_kind'), 'Other');
  });

  test('kinds open their module, as the web does', () {
    expect(NotificationsScreen.routeFor('calendar_reminder'), '/calendar');
    expect(NotificationsScreen.routeFor('approval_reminder'), '/approvals');
    expect(NotificationsScreen.routeFor('document_expiry'), '/vault');
    expect(NotificationsScreen.routeFor('missed_call'), '/calls');
    expect(NotificationsScreen.routeFor('finance_sms'), '/finance');
    expect(NotificationsScreen.routeFor('budget_alert'), isNull);
  });

  testWidgets('lists, counts kinds and filters by read state and kind',
      (tester) async {
    final transport = NotificationTransport();
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(transport))));
    await tester.pumpAndSettle();
    expect(find.text('Notice 1'), findsOneWidget);
    expect(find.text('2 unread'), findsOneWidget);
    expect(find.text('Every kind 3'), findsOneWidget);
    expect(find.text('Assigned to you 1'), findsOneWidget);
    // A kind the phone does not know yet reads as Other.
    expect(find.text('Other 1', skipOffstage: false), findsOneWidget);

    await tester.tap(find.text('Unread'));
    await tester.pumpAndSettle();
    expect(transport.requests.last.uri.queryParameters['state'], 'unread');
    expect(find.text('Notice 3'), findsNothing);

    await tester.tap(find.text('Calendar reminders 1'));
    await tester.pumpAndSettle();
    final list = transport.requests
        .lastWhere((r) => r.uri.path == '/api/v1/notifications');
    expect(list.uri.queryParameters['kind'], 'calendar_reminder');
    expect(list.uri.queryParameters['state'], 'unread');
    expect(find.text('Notice 2'), findsOneWidget);
    expect(find.text('Notice 1'), findsNothing);

    // Read and a kind with nothing in it: the empty filter state leads back.
    await tester.tap(find.text('Read'));
    await tester.pumpAndSettle();
    expect(find.text('No notifications match this filter.'), findsOneWidget);
    await tester.tap(find.text('Show every notification'));
    await tester.pumpAndSettle();
    expect(find.text('Notice 3'), findsOneWidget);
    expect(transport.requests.last.uri.queryParameters.containsKey('kind'),
        isFalse);
  });

  testWidgets('pages with the cursor and keeps the loaded rows',
      (tester) async {
    final transport = NotificationTransport(count: 45);
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(transport))));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Load more'), 400,
        scrollable: find.byType(Scrollable).last);
    await tester.ensureVisible(find.text('Load more'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Load more'));
    await tester.pumpAndSettle();
    expect(transport.requests.last.uri.queryParameters['cursor'], 'ntf_030');
    await tester.scrollUntilVisible(find.text('Notice 45'), 400,
        scrollable: find.byType(Scrollable).last);
    expect(find.text('Load more'), findsNothing);
  });

  testWidgets('mark unread, mark read, delete after a confirmation',
      (tester) async {
    final transport = NotificationTransport();
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(transport))));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('More actions for Notice 3'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Mark unread'));
    await tester.pumpAndSettle();
    expect(transport.requests.any((r) => r.uri.path.endsWith('ntf_003/unread')),
        isTrue);
    expect(find.text('3 unread'), findsOneWidget);

    await tester.tap(find.byTooltip('More actions for Notice 3'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Mark read'));
    await tester.pumpAndSettle();
    expect(find.text('2 unread'), findsOneWidget);

    await tester.tap(find.byTooltip('More actions for Notice 2'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    expect(find.text('Delete this notification?'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(transport.requests.any((r) => r.method == 'DELETE'), isFalse);

    await tester.tap(find.byTooltip('More actions for Notice 2'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(find.text('Notification deleted.'), findsOneWidget);
    expect(find.text('Notice 2'), findsNothing);
    expect(find.text('1 unread'), findsOneWidget);

    // A refused delete keeps the row and says what failed.
    transport.failDelete = true;
    await tester.tap(find.byTooltip('More actions for Notice 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await tester.pumpAndSettle();
    expect(find.textContaining('That did not work'), findsOneWidget);
    expect(find.text('Notice 1'), findsOneWidget);
  });

  testWidgets('screen readers get the row actions without the menu',
      (tester) async {
    final handle = tester.ensureSemantics();
    final transport = NotificationTransport();
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(transport))));
    await tester.pumpAndSettle();
    final node = tester.getSemantics(find.byKey(const Key('ntf-ntf_002')));
    final actions = node.getSemanticsData().customSemanticsActionIds ?? [];
    final labels =
        actions.map((id) => CustomSemanticsAction.getAction(id)!.label).toSet();
    expect(labels, containsAll(['Mark read', 'Delete']));
    handle.dispose();
  });

  testWidgets('opening one marks it read and goes where it points',
      (tester) async {
    final transport = NotificationTransport();
    final routes = <String>[];
    await tester.pumpWidget(_app(
        NotificationsScreen(repository: await _repo(transport)),
        routes: routes));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Notice 2'));
    await tester.pumpAndSettle();
    expect(transport.requests.any((r) => r.uri.path.endsWith('ntf_002/read')),
        isTrue);
    expect(routes, ['/calendar']);
  });

  testWidgets('mark all read reloads the list', (tester) async {
    final transport = NotificationTransport();
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(transport))));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Mark all read'));
    await tester.pumpAndSettle();
    expect(find.text('All read'), findsOneWidget);
    expect(find.byTooltip('Mark all read'), findsNothing);
  });

  testWidgets('empty, error with retry and permission denied', (tester) async {
    await tester.pumpWidget(_app(NotificationsScreen(
        repository: await _repo(NotificationTransport(count: 0)))));
    await tester.pumpAndSettle();
    expect(find.textContaining('You are all caught up'), findsOneWidget);
    expect(find.text('Refresh'), findsOneWidget);

    final failing = NotificationTransport(failList: true);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(_app(
        NotificationsScreen(repository: await _repo(failing)),
        locale: const Locale('ar')));
    await tester.pumpWidget(
        _app(NotificationsScreen(repository: await _repo(failing))));
    await tester.pumpAndSettle();
    expect(find.textContaining('Could not load notifications'), findsOneWidget);
    failing.failList = false;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Notice 1'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(_app(NotificationsScreen(
        key: const Key('denied'),
        repository: await _repo(NotificationTransport(denied: true)))));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('You cannot read notifications'), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('lays out at $width wide with 200% text in Arabic',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MediaQuery(
          data: MediaQueryData(
              size: Size(width, 900), textScaler: const TextScaler.linear(2)),
          child: _app(
              NotificationsScreen(
                  repository: await _repo(NotificationTransport(count: 12))),
              locale: const Locale('ar'))));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('الإشعارات'), findsOneWidget);
    });
  }
}
