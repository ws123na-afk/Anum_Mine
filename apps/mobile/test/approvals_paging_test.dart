// Approvals at scale in the mobile client
// (docs/approvals-and-risk.md#paging-filters-and-search): the API calls, the
// paging controller and the screen's filter, search, load more and states.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/approvals_paging.dart';
import 'package:anum_mobile/features/workspace/approvals_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/approvals_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _hash = 'c' * 64;
final _base = DateTime.utc(2026, 10, 6, 12);

WorkspaceApproval _approval(int n,
        {String status = 'pending', String action = 'external.action'}) =>
    WorkspaceApproval(
      id: 'approval_${status}_$n',
      taskId: 'task_1',
      action: action,
      reason: 'Reason number $n',
      risk: 'high',
      status: status,
      createdAt: _base.subtract(Duration(minutes: n)),
      decidedAt: status == 'pending' ? null : _base,
      decidedBy: status == 'pending' ? null : 'owner_a',
      payloadHash: _hash,
      expiresAt: status == 'pending'
          ? DateTime.now().add(const Duration(hours: 5))
          : null,
    );

Map<String, Object?> _json(WorkspaceApproval a) => {
      'id': a.id,
      'task_id': a.taskId,
      'action': a.action,
      'risk_level': a.risk,
      'status': a.status,
      'reason': a.reason,
      'created_at': a.createdAt.toIso8601String(),
      'payload_hash': a.payloadHash,
      'decided_by': a.decidedBy,
      'decided_at': a.decidedAt?.toIso8601String(),
    };

/// A repository with both the workspace and the paging routes, over [all].
class _PagedRepository
    implements WorkspaceRepository, ApprovalsPagingRepository {
  _PagedRepository(this.all);
  final List<WorkspaceApproval> all;
  final pageCalls = <({ApprovalFilter filter, String query, String? cursor})>[];
  Object? failPage;
  int summaryCalls = 0;

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
      tasks: const [],
      approvals: all.where((a) => a.status == 'pending').toList(),
      automations: const [],
      files: const [],
      memories: const []);

  @override
  Future<ApprovalsPage> loadApprovalsPage(
      {required ApprovalFilter filter,
      String query = '',
      String? cursor,
      int limit = approvalsPageSize}) async {
    pageCalls.add((filter: filter, query: query, cursor: cursor));
    final failure = failPage;
    if (failure != null) throw failure;
    return LocalApprovalsPaging(() => all).loadApprovalsPage(
        filter: filter, query: query, cursor: cursor, limit: limit);
  }

  @override
  Future<ApprovalSummary> loadApprovalSummary() async {
    summaryCalls++;
    return LocalApprovalsPaging(() => all).loadApprovalSummary();
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

List<WorkspaceApproval> _bulk() => [
      for (var i = 1; i <= 30; i++) _approval(i),
      for (var i = 1; i <= 40; i++)
        _approval(i + 100,
            status: i.isEven ? 'approved' : 'rejected',
            action: i == 8 ? 'refund.issue' : 'external.action'),
    ];

Future<void> _pump(WidgetTester tester, WorkspaceController controller,
    {Size size = const Size(1000, 40000), Locale? locale}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    home: Scaffold(body: ApprovalsScreen(controller: controller)),
  ));
  await tester.pumpAndSettle();
}

Future<WorkspaceController> _controller(_PagedRepository repository) async {
  final controller = WorkspaceController(repository);
  await controller.load();
  return controller;
}

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path == '/api/v1/approvals/page') {
      return ApiResponse(statusCode: 200, body: {
        'items': [_json(_approval(1)), _json(_approval(2, status: 'approved'))],
        'next_cursor': 'abc123',
      });
    }
    if (path == '/api/v1/approvals/summary') {
      return const ApiResponse(
          statusCode: 200,
          body: {'pending': 4, 'approved': 3, 'rejected': 2, 'expired': 1});
    }
    return const ApiResponse(statusCode: 200, body: {'data': []});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

Future<AnumApiClient> _api(_Transport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_test',
          roles: ['owner'])));
  return AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions);
}

void main() {
  group('API repository', () {
    test('sends the filter, search and cursor and maps the page and counts',
        () async {
      final transport = _Transport();
      final api = await _api(transport);
      final repository = ApiWorkspaceRepository(api, fileTransfer: _NoFiles());
      final page = await repository.loadApprovalsPage(
          filter: ApprovalFilter.decided,
          query: ' refund & more ',
          cursor: 'c1');
      final request = transport.requests.single;
      expect(request.uri.path, '/api/v1/approvals/page');
      expect(request.uri.queryParameters, {
        'status': 'decided',
        'limit': '25',
        'query': 'refund & more',
        'cursor': 'c1',
      });
      expect(page.items.map((a) => a.id),
          ['approval_pending_1', 'approval_approved_2']);
      expect(page.nextCursor, 'abc123');
      final summary = await repository.loadApprovalSummary();
      expect(summary.pending, 4);
      expect(summary.decided, 6);
      expect(summary.totalFor(ApprovalFilter.all), 10);
    });

    test('the workspace snapshot asks only for pending approvals', () async {
      final transport = _Transport();
      final api = await _api(transport);
      await ApiWorkspaceRepository(api, fileTransfer: _NoFiles())
          .loadWorkspace();
      final list = transport.requests
          .firstWhere((r) => r.uri.path == '/api/v1/approvals');
      expect(list.uri.queryParameters, {'status': 'pending', 'limit': '200'});
    });
  });

  group('LocalApprovalsPaging', () {
    test('orders pending first then newest, filters and searches', () async {
      final source = LocalApprovalsPaging(_bulk);
      final first =
          await source.loadApprovalsPage(filter: ApprovalFilter.all, limit: 25);
      expect(first.items.length, 25);
      expect(first.items.every((a) => a.status == 'pending'), isTrue);
      expect(first.nextCursor, '25');
      final second = await source.loadApprovalsPage(
          filter: ApprovalFilter.all, cursor: first.nextCursor, limit: 25);
      expect(second.items.first.status, 'pending');
      expect(second.items.last.status, isNot('pending'));
      final decided = await source.loadApprovalsPage(
          filter: ApprovalFilter.decided, limit: 100);
      expect(decided.items.length, 40);
      final found = await source.loadApprovalsPage(
          filter: ApprovalFilter.all, query: 'REFUND');
      expect(found.items.map((a) => a.action), ['refund.issue']);
      final summary = await source.loadApprovalSummary();
      expect(
          (summary.pending, summary.approved, summary.rejected), (30, 20, 20));
    });
  });

  group('ApprovalsListController', () {
    test('loads, filters, searches and pages without duplicates', () async {
      final repository = _PagedRepository(_bulk());
      final list = ApprovalsListController(repository);
      await list.load();
      expect(list.phase, ApprovalsPhase.ready);
      expect(list.items.length, 25);
      expect(list.hasMore, isTrue);
      await list.loadMore();
      expect(list.items.length, 50);
      expect(list.items.map((a) => a.id).toSet().length, 50);
      await list.loadMore();
      expect(list.items.length, 70);
      expect(list.hasMore, isFalse);
      await list.setFilter(ApprovalFilter.decided);
      expect(list.items.length, 25);
      await list.setQuery('  refund ');
      expect(list.query, 'refund');
      expect(list.items.single.action, 'refund.issue');
      expect(repository.pageCalls.last.filter, ApprovalFilter.decided);
      list.dispose();
    });

    test('an error keeps the screen usable: denied, error, quiet failure',
        () async {
      final repository = _PagedRepository(_bulk());
      final list = ApprovalsListController(repository);
      repository.failPage = const ApiException(403, 'No access');
      await list.load();
      expect(list.phase, ApprovalsPhase.denied);
      repository.failPage = const ApiException(500, 'Store is busy');
      await list.load();
      expect(list.phase, ApprovalsPhase.error);
      expect(list.error, 'Store is busy');
      repository.failPage = null;
      await list.load();
      expect(list.phase, ApprovalsPhase.ready);
      repository.failPage = const ApiException(500, 'Store is busy');
      await list.load(quiet: true);
      expect(list.phase, ApprovalsPhase.ready);
      expect(list.items, isNotEmpty);
      await list.loadMore();
      expect(list.pagingError, 'Store is busy');
      expect(list.items.length, 25);
      list.dispose();
    });
  });

  group('Approvals screen', () {
    testWidgets('shows pending first with a count and loads more history',
        (tester) async {
      final repository = _PagedRepository(_bulk());
      final controller = await _controller(repository);
      await _pump(tester, controller);

      expect(find.text('25 of 70 shown'), findsOneWidget);
      expect(find.text('Approve'), findsNWidgets(25));
      expect(find.text('Decisions'), findsNothing);

      await tester.ensureVisible(find.byKey(const Key('approvals-load-more')));
      await tester.tap(find.byKey(const Key('approvals-load-more')));
      await tester.pumpAndSettle();
      expect(find.text('50 of 70 shown'), findsOneWidget);
      expect(find.text('Approve'), findsNWidgets(30));
      expect(find.text('Decisions'), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('approvals-load-more')));
      await tester.tap(find.byKey(const Key('approvals-load-more')));
      await tester.pumpAndSettle();
      expect(find.text('70 of 70 shown'), findsOneWidget);
      expect(find.byKey(const Key('approvals-load-more')), findsNothing);
    });

    testWidgets('filter tabs narrow the list through the repository',
        (tester) async {
      final repository = _PagedRepository(_bulk());
      final controller = await _controller(repository);
      await _pump(tester, controller);

      await tester.tap(find.byKey(const Key('approvals-filter-decided')));
      await tester.pumpAndSettle();
      expect(find.text('Approve'), findsNothing);
      expect(find.text('Decisions'), findsOneWidget);
      expect(repository.pageCalls.last.filter, ApprovalFilter.decided);

      await tester.tap(find.byKey(const Key('approvals-filter-pending')));
      await tester.pumpAndSettle();
      expect(find.text('Approve'), findsNWidgets(25));
      expect(find.text('Decisions'), findsNothing);
    });

    testWidgets('search is debounced, sent, and an empty result can be cleared',
        (tester) async {
      final repository = _PagedRepository(_bulk());
      final controller = await _controller(repository);
      await _pump(tester, controller);
      final before = repository.pageCalls.length;

      await tester.enterText(
          find.byKey(const Key('approvals-search')), 'refund');
      await tester.pump(const Duration(milliseconds: 100));
      expect(repository.pageCalls.length, before);
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pumpAndSettle();
      expect(repository.pageCalls.last.query, 'refund');
      expect(find.textContaining('refund.issue'), findsWidgets);
      expect(find.text('Approve'), findsNothing);

      await tester.enterText(find.byKey(const Key('approvals-search')), 'zzz');
      await tester.pump(const Duration(milliseconds: 400));
      await tester.pumpAndSettle();
      expect(find.text('No approvals match "zzz"'), findsOneWidget);
      await tester.tap(find.text('Clear search').last);
      await tester.pumpAndSettle();
      expect(find.text('Approve'), findsNWidgets(25));
    });

    testWidgets('empty queue, empty history, error with Retry and denied',
        (tester) async {
      final repository = _PagedRepository([]);
      final controller = await _controller(repository);
      await _pump(tester, controller);
      expect(find.text('Nothing is waiting for you'), findsOneWidget);

      await tester.tap(find.byKey(const Key('approvals-filter-decided')));
      await tester.pumpAndSettle();
      expect(find.text('No decisions yet'), findsOneWidget);

      repository.failPage = const ApiException(500, 'The store is busy.');
      await tester.tap(find.byKey(const Key('approvals-filter-all')));
      await tester.pumpAndSettle();
      expect(find.text('Could not load approvals'), findsOneWidget);
      expect(find.textContaining('The store is busy.'), findsOneWidget);
      repository.failPage = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('Nothing is waiting for you'), findsOneWidget);

      repository.failPage = const ApiException(403, 'Forbidden');
      await tester.tap(find.byKey(const Key('approvals-filter-pending')));
      await tester.pumpAndSettle();
      expect(find.text('No access to approvals'), findsOneWidget);
    });

    testWidgets('reads in Arabic and fits a phone without overflow',
        (tester) async {
      final repository = _PagedRepository(_bulk());
      final controller = await _controller(repository);
      await _pump(tester, controller,
          size: const Size(390, 844), locale: const Locale('ar'));
      expect(find.text(approvalsAr['apv.title']!), findsOneWidget);
      expect(find.text(approvalsAr['apv.filter.decided']!), findsOneWidget);
      expect(find.text('25 ${'من'} 70'), findsNothing);
      expect(
          find.text(approvalsAr['apv.shown']!
              .replaceAll('{shown}', '25')
              .replaceAll('{total}', '70')),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    test('Arabic strings have the English keys and placeholders', () {
      expect(approvalsAr.keys.toSet(), approvalsEn.keys.toSet());
      final holders = RegExp(r'\{[a-z]+\}');
      for (final key in approvalsEn.keys) {
        expect(holders.allMatches(approvalsAr[key]!).map((m) => m[0]).toSet(),
            holders.allMatches(approvalsEn[key]!).map((m) => m[0]).toSet(),
            reason: key);
      }
    });
  });
}
