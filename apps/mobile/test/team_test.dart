// Team work in the mobile app (docs/team-work.md): wire contract, every
// state, permissions from the API, and layouts from 320 to 1440 wide at
// 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/team/team_repository.dart';
import 'package:anum_mobile/features/team/team_screens.dart';
import 'package:anum_mobile/features/team/team_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

JsonMap _item(String id, String title,
        {String status = 'todo', bool overdue = false, int version = 1}) =>
    {
      'id': id,
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'title': title,
      'description': 'Ask for the October price list.',
      'status': status,
      'priority': 'high',
      'assignee_type': 'user',
      'assignee_id': 'user_owner',
      'assignee_name': 'Omar Owner',
      'created_by': 'user_ahmed',
      'created_by_name': 'Ahmed Saleh',
      'due_at': '2026-10-15T14:00:00Z',
      'overdue': overdue,
      'version': version,
      'checklist_total': 1,
      'checklist_done': 0,
      'comment_count': 0,
      'can_edit': true,
      'source': 'manual',
      'checklist': [
        {'id': 'chk_1', 'label': 'Get quote', 'done': false}
      ],
      'comments': [],
    };

JsonMap _envelope(String code, String message) => {
      'error': {'code': code, 'message': message, 'correlation_id': 'c'}
    };

class TeamTransport implements ApiTransport {
  TeamTransport(
      {this.empty = false, this.denied = false, this.conflict = false});
  final bool empty;
  final bool denied;
  final bool conflict;
  final requests = <ApiRequest>[];
  int version = 1;
  String status = 'todo';

  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    final path = r.uri.path;
    if (denied) {
      return ApiResponse(
          statusCode: 403,
          body: _envelope('forbidden', 'Platform operator role required.'));
    }
    if (path == '/api/v1/work-items' && r.method == 'GET') {
      return ApiResponse(statusCode: 200, body: {
        'items': empty
            ? []
            : [
                _item('work_1', 'Call the supplier', overdue: true),
                _item('work_2', 'Count winter tyre stock',
                    status: 'in_progress'),
              ],
        'total': empty ? 0 : 2,
        'limit': 30,
        'offset': 0,
        'next_offset': null,
      });
    }
    if (path == '/api/v1/work-items/work_1' && r.method == 'GET') {
      return ApiResponse(
          statusCode: 200,
          body: _item('work_1', 'Call the supplier',
              status: status, version: version));
    }
    if (path == '/api/v1/work-items/work_1' && r.method == 'PATCH') {
      if (conflict) {
        return ApiResponse(
            statusCode: 409,
            body: _envelope('conflict', 'Someone changed this work item.'));
      }
      status = r.body!['status']! as String;
      version += 1;
      return ApiResponse(
          statusCode: 200,
          body: _item('work_1', 'Call the supplier',
              status: status, version: version));
    }
    if (path == '/api/v1/team/commands/parse') {
      return const ApiResponse(statusCode: 200, body: {
        'understood': true,
        'problem': null,
        'person': 'Ahmed',
        'title': 'Call the supplier',
        'due_at': '2026-10-15T14:00:00Z',
        'due_text': 'Thursday',
        'candidates': [
          {'type': 'user', 'id': 'user_ahmed', 'name': 'Ahmed Saleh'}
        ],
        'assignee': {'type': 'user', 'id': 'user_ahmed', 'name': 'Ahmed Saleh'},
      });
    }
    if (path == '/api/v1/team/commands') {
      return ApiResponse(statusCode: 201, body: {
        ..._item('work_3', 'Call the supplier'),
        'assignee_name': 'Ahmed Saleh'
      });
    }
    if (path == '/api/v1/notifications') {
      return const ApiResponse(statusCode: 200, body: {
        'items': [
          {
            'id': 'ntf_1',
            'user_id': 'user_owner',
            'kind': 'work_assigned',
            'title': 'New work: Call the supplier',
            'body': 'Ahmed Saleh assigned you "Call the supplier".',
            'work_item_id': 'work_1',
            'read_at': null,
            'email_status': 'not_requested',
            'email_attempts': 0,
            'email_error': null,
            'push_status': 'queued',
            'created_at': '2026-10-11T10:00:00Z',
          }
        ],
        'unread': 1,
        'next_cursor': null,
      });
    }
    if (path == '/api/v1/work-items/workload') {
      return const ApiResponse(statusCode: 200, body: {
        'rows': [
          {
            'assignee_type': 'user',
            'assignee_id': 'user_owner',
            'name': 'Omar Owner',
            'open': 2,
            'in_progress': 1,
            'blocked': 0,
            'overdue': 1,
            'due_this_week': 1,
            'estimate_hours': 34,
            'capacity_hours': 40,
            'load_percent': 85,
          }
        ],
        'unassigned_open': 0,
        'generated_at': '2026-10-11T10:00:00Z',
      });
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

Widget _app(Widget child, {Locale locale = const Locale('en')}) => MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    home: child);

void main() {
  test('English and Arabic team catalogs have the same keys and placeholders',
      () {
    expect(TeamText.ar.keys.toSet(), TeamText.en.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in TeamText.en.keys) {
      Set<String> names(String v) =>
          placeholder.allMatches(v).map((m) => m.group(1)!).toSet();
      expect(names(TeamText.ar[key]!), names(TeamText.en[key]!), reason: key);
    }
  });

  testWidgets('the menu drawer page opens team work', (tester) async {
    final repo = await _repo(TeamTransport());
    await tester.pumpWidget(_app(TeamHubScreen(repository: repo)));
    await tester.pumpAndSettle();
    expect(find.text('Team work'), findsWidgets);
    await tester.tap(find.text('My tasks'));
    await tester.pumpAndSettle();
    expect(find.text('Call the supplier'), findsOneWidget);
  });

  testWidgets('My tasks lists the API work and opens an item', (tester) async {
    final transport = TeamTransport();
    final repo = await _repo(transport);
    await tester.pumpWidget(_app(WorkListScreen(repository: repo)));
    await tester.pumpAndSettle();
    expect(find.text('Call the supplier'), findsOneWidget);
    expect(find.textContaining('Overdue'), findsOneWidget);
    expect(transport.requests.first.uri.queryParameters['scope'], 'mine');
    await tester.tap(find.text('Call the supplier'));
    await tester.pumpAndSettle();
    expect(find.text('Get quote'), findsOneWidget);
    await tester.tap(find.text('To do').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Done').last);
    await tester.pumpAndSettle();
    final patch = transport.requests.lastWhere((r) => r.method == 'PATCH');
    expect(patch.body, {'version': 1, 'status': 'done'});
  });

  testWidgets('empty and permission-denied states', (tester) async {
    final repo = await _repo(TeamTransport(empty: true));
    await tester.pumpWidget(_app(WorkListScreen(repository: repo)));
    await tester.pumpAndSettle();
    expect(find.textContaining('Nothing assigned to you.'), findsOneWidget);

    final denied = await _repo(TeamTransport(denied: true));
    await tester.pumpWidget(_app(OrganisationsScreen(repository: denied)));
    await tester.pumpAndSettle();
    expect(find.textContaining('Platform operators only.'), findsOneWidget);
    expect(find.textContaining('Platform operator role required.'),
        findsOneWidget);
  });

  testWidgets('a conflicting change is explained and reloaded', (tester) async {
    final transport = TeamTransport(conflict: true);
    final repo = await _repo(transport);
    await tester
        .pumpWidget(_app(WorkDetailScreen(repository: repo, itemId: 'work_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('To do').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Blocked').last);
    await tester.pumpAndSettle();
    expect(
        find.textContaining('Someone else changed this item'), findsOneWidget);
  });

  testWidgets('Ask ANUM previews, then assigns', (tester) async {
    final transport = TeamTransport();
    final repo = await _repo(transport);
    await tester.pumpWidget(_app(AskAnumScreen(repository: repo)));
    await tester.enterText(
        find.byType(TextField), 'ask Ahmed to call the supplier by Thursday');
    await tester.tap(find.text('Check'));
    await tester.pumpAndSettle();
    expect(find.text('Call the supplier'), findsOneWidget);
    await tester.tap(find.text('Assign and notify'));
    await tester.pumpAndSettle();
    final run = transport.requests.last;
    expect(run.uri.path, '/api/v1/team/commands');
    expect(run.body!['assignee'], {'type': 'user', 'id': 'user_ahmed'});
  });

  testWidgets('notifications and workload read the API', (tester) async {
    final repo = await _repo(TeamTransport());
    await tester.pumpWidget(_app(NotificationsScreen(repository: repo)));
    await tester.pumpAndSettle();
    expect(find.text('New work: Call the supplier'), findsOneWidget);
    expect(find.byTooltip('Mark all read'), findsOneWidget);
    await tester.pumpWidget(_app(WorkloadScreen(repository: repo)));
    await tester.pumpAndSettle();
    expect(find.text('85% of capacity'), findsOneWidget);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('team screens lay out at $width wide with 200% text',
        (tester) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final repo = await _repo(TeamTransport());
      for (final screen in <Widget>[
        WorkListScreen(repository: repo),
        WorkDetailScreen(repository: repo, itemId: 'work_1'),
        NotificationsScreen(repository: repo),
        WorkloadScreen(repository: repo),
        AssistantsScreen(repository: repo),
        OrganisationsScreen(repository: repo),
      ]) {
        await tester.pumpWidget(MediaQuery(
            data: MediaQueryData(
                size: Size(width, 900), textScaler: const TextScaler.linear(2)),
            child: _app(screen, locale: const Locale('ar'))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: '$screen at $width');
      }
    });
  }
}
