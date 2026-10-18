import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/developer/developer_screen.dart'
    show LinkOpener;
import 'package:anum_mobile/features/lifecycle/lifecycle_controller.dart';
import 'package:anum_mobile/features/lifecycle/lifecycle_models.dart';
import 'package:anum_mobile/features/lifecycle/lifecycle_repository.dart';
import 'package:anum_mobile/features/lifecycle/lifecycle_screen.dart';
import 'package:anum_mobile/features/settings/settings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _orgId = 'tenant_Acme01';

Map<String, Object?> _export(String id, String status) => {
      'id': id,
      'kind': 'organisation_export',
      'status': status,
      'workspace_id': 'workspace_test',
      'requested_by': 'user_owner',
      'requested_at': '2026-10-18T09:00:00Z',
      'purge_after': null,
      'started_at': null,
      'finished_at': status == 'ready' ? '2026-10-18T09:05:00Z' : null,
      'cancelled_at': null,
      'cancelled_by': null,
      'expires_at': status == 'ready' ? '2026-10-25T09:05:00Z' : null,
      'archive_bytes': status == 'ready' ? 5 * 1024 * 1024 : null,
      'archive_sha256': null,
      'row_count': status == 'ready' ? 1200 : null,
      'file_count': status == 'ready' ? 4 : null,
      'error': null,
    };

Map<String, Object?> _deletion(String kind, String status) => {
      'id': 'job_$kind',
      'kind': kind,
      'status': status,
      'workspace_id': 'workspace_test',
      'requested_by': 'user_owner',
      'requested_at': '2026-10-18T09:00:00Z',
      'purge_after': '2026-10-25T09:00:00Z',
      'started_at': null,
      'finished_at': null,
      'cancelled_at': null,
      'cancelled_by': null,
      'expires_at': null,
      'archive_bytes': null,
      'archive_sha256': null,
      'row_count': null,
      'file_count': null,
      'error': null,
    };

class _Transport implements ApiTransport {
  _Transport({this.owner = true, this.summaryStatus = 200});
  final bool owner;
  int summaryStatus;
  final List<ApiRequest> requests = [];
  List<Map<String, Object?>> exports = [_export('exp_1', 'ready')];
  Map<String, Object?>? orgDeletion;
  Map<String, Object?>? wsDeletion;

  Map<String, Object?> get _summary => {
        'can_manage_organisation': owner,
        'can_delete_workspace': owner,
        'organisation_confirmation': _orgId,
        'workspace_confirmation': 'Acme Sales',
        'cooling_off_hours': 168,
        'export_link_ttl_seconds': 900,
        'export_retention_hours': 168,
        'exports': owner ? exports : <Object?>[],
        'organisation_deletion': orgDeletion,
        'workspace_deletion': wsDeletion,
      };

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/organization/lifecycle')) {
      return summaryStatus == 200
          ? ApiResponse(statusCode: 200, body: _summary)
          : ApiResponse(
              statusCode: summaryStatus,
              body: const {'detail': 'Workspace permission denied'});
    }
    if (path.endsWith('/lifecycle/exports')) {
      final job = _export('exp_2', 'queued');
      exports = [job, ...exports];
      return ApiResponse(statusCode: 202, body: job);
    }
    if (path.endsWith('/exports/exp_1/link')) {
      return const ApiResponse(statusCode: 200, body: {
        'url': '/api/v1/organization/lifecycle/exports/download/example-token',
        'expires_at': '2026-10-18T09:20:00Z',
      });
    }
    if (path.endsWith('/lifecycle/deletion')) {
      if (request.body?['confirm'] != _orgId) {
        return const ApiResponse(
            statusCode: 422,
            body: {'detail': 'Type the organisation id exactly.'});
      }
      orgDeletion = _deletion('organisation_deletion', 'scheduled');
      return ApiResponse(statusCode: 202, body: orgDeletion);
    }
    if (path.endsWith('/lifecycle/deletion/cancel')) {
      final cancelled = {...?orgDeletion, 'status': 'cancelled'};
      orgDeletion = null;
      return ApiResponse(statusCode: 200, body: cancelled);
    }
    if (path.endsWith('/workspaces/current/deletion')) {
      wsDeletion = _deletion('workspace_deletion', 'scheduled');
      return ApiResponse(statusCode: 202, body: wsDeletion);
    }
    if (path.endsWith('/workspaces/current/deletion/cancel')) {
      final cancelled = {...?wsDeletion, 'status': 'cancelled'};
      wsDeletion = null;
      return ApiResponse(statusCode: 200, body: cancelled);
    }
    // Settings, for the way in.
    if (path.endsWith('/me/preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'remember_conversations': true,
        'updated_at': null,
        'conversation_memory': 'on',
      });
    }
    if (path.endsWith('/notification-preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'task_completed': true,
        'approval_required': true,
        'run_failed': true,
        'automation_failed': true,
        'email_enabled': false,
        'desktop_enabled': true,
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<MemorySessionStore> _sessions() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_example',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': _orgId,
      'workspace_id': 'workspace_test',
      'user_id': 'user_owner',
      'roles': ['owner'],
    },
  }));
  return sessions;
}

Future<AnumApiClient> _api(_Transport transport) async => AnumApiClient(
    baseUri: Uri.parse('https://api.example.com'),
    transport: transport,
    sessions: await _sessions());

Future<LifecycleController> _controller(_Transport transport,
        {Duration poll = const Duration(seconds: 5)}) async =>
    LifecycleController(ApiLifecycleRepository(await _api(transport)),
        pollInterval: poll);

Future<void> _show(WidgetTester tester, LifecycleController controller,
    {LinkOpener? open, Size size = const Size(390, 3000)}) async {
  await tester.binding.setSurfaceSize(size);
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      home: LifecycleScreen(
          controller: controller, openLink: open ?? (_) async => true)));
  await tester.pumpAndSettle();
}

Finder _scrollable() => find.byType(Scrollable).first;

void main() {
  test('the summary and jobs read snake_case with nullable fields', () {
    final transport = _Transport()
      ..orgDeletion = _deletion('organisation_deletion', 'scheduled');
    final summary = LifecycleSummary.fromJson(transport._summary);
    expect(summary.canManageOrganisation, isTrue);
    expect(summary.organisationConfirmation, _orgId);
    expect(summary.exports.single.isReady, isTrue);
    expect(summary.exports.single.archiveBytes, 5 * 1024 * 1024);
    expect(summary.organisationDeletion!.purgeAfter,
        DateTime.utc(2026, 10, 25, 9));
    expect(summary.workspaceDeletion, isNull);
    expect(summary.exportInProgress, isFalse);
    expect(formatHours(168), '7 days');
    expect(formatHours(5), '5 hours');
    expect(confirmationMatches(_orgId, ' $_orgId'), isFalse);
    expect(confirmationMatches(_orgId, _orgId.toLowerCase()), isFalse);
    expect(confirmationMatches(_orgId, _orgId), isTrue);
    expect(formatWhen(null), '-');
  });

  testWidgets('renders the summary for an owner', (tester) async {
    await _show(tester, await _controller(_Transport()));
    expect(find.text('Export the organisation'), findsOneWidget);
    expect(find.text('Ready to download'), findsOneWidget);
    expect(find.textContaining('Size 5.0 MB'), findsOneWidget);
    expect(find.text('Request export'), findsOneWidget);
    expect(find.text('Delete organisation'), findsOneWidget);
    expect(find.text('Delete workspace'), findsOneWidget);
  });

  testWidgets(
      'schedules organisation deletion only after typing the exact id, then cancels',
      (tester) async {
    final transport = _Transport();
    await _show(tester, await _controller(transport));

    await tester.tap(find.byKey(const Key('lifecycle-delete-organisation')));
    await tester.pumpAndSettle();
    final confirm = find.byKey(const Key('lifecycle-confirm-button'));
    FilledButton button() => tester.widget<FilledButton>(confirm);
    expect(button().onPressed, isNull);

    for (final wrong in [_orgId.toLowerCase(), '$_orgId ', 'tenant_Acme']) {
      await tester.enterText(find.byKey(const Key('lifecycle-confirm')), wrong);
      await tester.pump();
      expect(button().onPressed, isNull, reason: wrong);
    }
    await tester.enterText(find.byKey(const Key('lifecycle-confirm')), _orgId);
    await tester.pump();
    expect(button().onPressed, isNotNull);
    await tester.tap(confirm);
    await tester.pumpAndSettle();

    final schedule = transport.requests
        .singleWhere((r) => r.uri.path.endsWith('/lifecycle/deletion'));
    expect(schedule.body, {'confirm': _orgId});
    final banner = find.byKey(const Key('lifecycle-organisation-banner'));
    expect(banner, findsOneWidget);
    expect(
        find.descendant(
            of: banner,
            matching: find.textContaining('will be deleted on 25 Oct 2026')),
        findsOneWidget);
    expect(find.text('Delete organisation'), findsNothing);
    // No new export while the organisation is scheduled for deletion.
    expect(
        tester
            .widget<FilledButton>(
                find.byKey(const Key('lifecycle-request-export')))
            .onPressed,
        isNull);

    await tester.tap(find.byKey(const Key('lifecycle-cancel-organisation')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Cancel deletion').last);
    await tester.pumpAndSettle();
    expect(
        transport.requests
            .where((r) => r.uri.path.endsWith('/lifecycle/deletion/cancel')),
        hasLength(1));
    expect(banner, findsNothing);
    expect(
        find.textContaining('Organisation deletion cancelled'), findsOneWidget);
    expect(find.text('Delete organisation'), findsOneWidget);
  });

  testWidgets('workspace deletion needs the exact name', (tester) async {
    final transport = _Transport();
    await _show(tester, await _controller(transport));
    await tester.tap(find.byKey(const Key('lifecycle-delete-workspace')));
    await tester.pumpAndSettle();
    final confirm = find.byKey(const Key('lifecycle-confirm-button'));
    await tester.enterText(
        find.byKey(const Key('lifecycle-confirm')), 'acme sales');
    await tester.pump();
    expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
    await tester.enterText(
        find.byKey(const Key('lifecycle-confirm')), 'Acme Sales');
    await tester.pump();
    await tester.tap(confirm);
    await tester.pumpAndSettle();
    expect(
        transport.requests
            .singleWhere(
                (r) => r.uri.path.endsWith('/workspaces/current/deletion'))
            .body,
        {'confirm_name': 'Acme Sales'});
    expect(find.byKey(const Key('lifecycle-workspace-banner')), findsOneWidget);
  });

  testWidgets('a member without rights sees the facts and who can act',
      (tester) async {
    final transport = _Transport(owner: false)
      ..orgDeletion = _deletion('organisation_deletion', 'scheduled');
    await _show(tester, await _controller(transport));
    expect(
        find.byKey(const Key('lifecycle-organisation-banner')), findsOneWidget);
    expect(find.byType(FilledButton), findsNothing);
    expect(find.byType(OutlinedButton), findsNothing);
    expect(find.text(organisationRightsMessage), findsNWidgets(2));
    expect(find.text(workspaceRightsMessage), findsOneWidget);
    expect(find.textContaining('Ask an owner to cancel it'), findsOneWidget);
  });

  testWidgets('a load error offers Retry, and a 403 is explained',
      (tester) async {
    final transport = _Transport(summaryStatus: 500);
    final controller = await _controller(transport);
    await _show(tester, controller);
    expect(find.text('Data export and deletion unavailable'), findsOneWidget);
    transport.summaryStatus = 200;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Export the organisation'), findsOneWidget);

    final denied = await _controller(_Transport(summaryStatus: 403));
    await denied.load();
    expect(denied.phase, LifecyclePhase.permissionDenied);
  });

  testWidgets('a ready export requests a link and opens it, or copies it',
      (tester) async {
    final transport = _Transport();
    final opened = <Uri>[];
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String?;
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await _show(tester, await _controller(transport), open: (uri) async {
      opened.add(uri);
      return true;
    });

    await tester.tap(find.text('Open download link'));
    await tester.pumpAndSettle();
    const url =
        'https://api.example.com/api/v1/organization/lifecycle/exports/download/example-token';
    expect(transport.requests.where((r) => r.uri.path.endsWith('/link')),
        hasLength(1));
    expect(opened, [Uri.parse(url)]);

    await tester.tap(find.text('Copy download link'));
    await tester.pumpAndSettle();
    expect(copied, url);
    expect(find.textContaining('Download link copied'), findsOneWidget);
  });

  testWidgets('polls while an export is being prepared, and stops on dispose',
      (tester) async {
    final transport = _Transport();
    final controller =
        await _controller(transport, poll: const Duration(seconds: 5));
    await _show(tester, controller);
    await tester.tap(find.text('Request export'));
    // The queued export shows a spinner, so the page never settles.
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Waiting to start'), findsOneWidget);
    expect(controller.polling, isTrue);
    final before = transport.requests.length;

    transport.exports = [_export('exp_2', 'ready'), _export('exp_1', 'ready')];
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(transport.requests.length, before + 1);
    expect(find.text('Waiting to start'), findsNothing);
    expect(controller.polling, isFalse);

    // A new export polls again; leaving the page cancels the timer.
    await tester.tap(find.text('Request export'));
    // The queued export shows a spinner, so the page never settles.
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(controller.polling, isTrue);
    await tester.pumpWidget(const SizedBox());
    expect(controller.polling, isFalse);
  });

  testWidgets('fits 320 and 1440 wide, also at 200% text', (tester) async {
    for (final width in const [320.0, 1440.0]) {
      for (final scale in const [1.0, 2.0]) {
        final transport = _Transport()
          ..wsDeletion = _deletion('workspace_deletion', 'scheduled');
        final controller = await _controller(transport);
        await tester.binding.setSurfaceSize(Size(width, 900));
        await tester.pumpWidget(MaterialApp(
            key: ValueKey('$width $scale'),
            theme: AnumTheme.dark(),
            home: MediaQuery.withClampedTextScaling(
                minScaleFactor: scale,
                maxScaleFactor: scale,
                child: LifecycleScreen(controller: controller))));
        await tester.pumpAndSettle();
        for (final target in [
          find.text('Copy download link'),
          find.byKey(const Key('lifecycle-delete-organisation')),
          find.byKey(const Key('lifecycle-cancel-workspace')),
        ]) {
          await tester.scrollUntilVisible(target, 200,
              maxScrolls: 100, scrollable: _scrollable());
          await tester.pumpAndSettle();
          final rect = tester.getRect(target);
          expect(rect.left, greaterThanOrEqualTo(0), reason: '$width $scale');
          expect(rect.right, lessThanOrEqualTo(width), reason: '$width $scale');
        }
        expect(tester.takeException(), isNull, reason: '$width $scale');
      }
    }
    await tester.binding.setSurfaceSize(null);
  });

  testWidgets('Settings opens Data export and deletion', (tester) async {
    final transport = _Transport();
    final api = await _api(transport);
    final settings = SettingsController(
        AuthRepository(api: api, sessions: api.sessions),
        lifecycle: ApiLifecycleRepository(api));
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
        theme: AnumTheme.dark(), home: SettingsScreen(controller: settings)));
    await tester.pumpAndSettle();
    final tile = find.byKey(const Key('settings-lifecycle'));
    await tester.scrollUntilVisible(tile, 200, scrollable: _scrollable());
    await tester.ensureVisible(tile);
    await tester.pumpAndSettle();
    await tester.tap(tile);
    await tester.pumpAndSettle();
    expect(find.byType(LifecycleScreen), findsOneWidget);
    expect(find.text('Export the organisation'), findsOneWidget);
  });
}
