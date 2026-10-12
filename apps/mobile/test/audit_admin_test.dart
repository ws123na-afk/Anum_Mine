// Audit log and member administration parity in the mobile client
// (docs/governance-and-scale.md#audit-log, docs/identity.md#client-screens):
// search, confirmed role changes, resend, last-owner protection, and the
// read-only audit log with filters, paging, detail and every state; layouts
// from 320 to 1440 wide at 200% text.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/admin/admin.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

JsonMap _member(String id, String role, {bool active = true, String? email}) =>
    {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': id,
      'role': role,
      'active': active,
      'created_at': '2026-10-01T10:00:00Z',
      'updated_at': '2026-10-01T10:00:00Z',
      'email': email,
    };

JsonMap _invitation({String status = 'pending'}) => {
      'id': 'invitation_1',
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'role': 'viewer',
      'invitee_user_id': 'user_new',
      'invitee_email': null,
      'status': status,
      'created_by_user_id': 'user_owner',
      'expires_at':
          DateTime.now().toUtc().add(const Duration(days: 7)).toIso8601String(),
      'accepted_by_user_id': null,
      'accepted_at': null,
      'revoked_at': null,
      'created_at': '2026-10-06T10:00:00Z',
    };

JsonMap _audit(int i) => {
      'id': 'audit_$i',
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'actor': i.isEven ? 'user_owner' : 'user_b',
      'action':
          i.isEven ? 'workspace_member.role_change' : 'policy_pack.created',
      'target': 'target_$i',
      'outcome': 'success',
      'correlation_id': 'corr_$i',
      'created_at': DateTime.utc(2026, 9, 30, 12)
          .subtract(Duration(minutes: i))
          .toIso8601String(),
      'metadata': {'role': 'viewer', 'api_key': '[REDACTED]'},
    };

JsonMap _envelope(String code, String message) => {
      'error': {'code': code, 'message': message, 'correlation_id': 'corr'}
    };

class Transport implements ApiTransport {
  Transport({this.auditForbidden = false, this.auditTotal = 75});
  final bool auditForbidden;
  final int auditTotal;
  final requests = <ApiRequest>[];
  List<JsonMap> members = [
    _member('user_owner', 'owner'),
    _member('user_b', 'member', email: 'bea@example.com'),
    _member('user_c', 'viewer'),
  ];

  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    final path = r.uri.path;
    ApiResponse ok(Object body) => ApiResponse(
        statusCode: 200, body: body is JsonMap ? body : {'data': body});
    if (path.endsWith('/workspace-members')) return ok(members);
    if (path.endsWith('/workspace-invitations')) return ok([_invitation()]);
    if (path.endsWith('/invitation_1/resend')) {
      return ok({'invitation': _invitation(), 'token': 'anum_inv_resent'});
    }
    if (path.endsWith('/user_b/role')) {
      members = [
        members[0],
        _member('user_b', r.body!['role']! as String, email: 'bea@example.com'),
        members[2]
      ];
      return ok(members[1]);
    }
    if (path.endsWith('/audit-records/actions')) {
      return ok({
        'actions': ['policy_pack.created', 'workspace_member.role_change']
      });
    }
    if (path.endsWith('/audit-records')) {
      if (auditForbidden) {
        return ApiResponse(
            statusCode: 403,
            body: _envelope('forbidden',
                'Audit log access requires the owner role in this workspace or the organization administrator role.'));
      }
      final q = r.uri.queryParameters;
      final all = [for (var i = 0; i < auditTotal; i++) _audit(i)].where((x) =>
          (q['action'] == null || x['action'] == q['action']) &&
          (q['q'] == null || '${x['actor']}'.contains(q['q']!)));
      final list = all.toList();
      final start = int.tryParse(q['cursor'] ?? '') ?? 0;
      final limit = int.parse(q['limit'] ?? '50');
      final page = list.skip(start).take(limit).toList();
      final next = start + limit < list.length ? '${start + limit}' : null;
      return ok({
        'items': page,
        'next_cursor': next,
        'retention':
            'Audit records are kept for as long as the workspace exists.'
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<AnumApiClient> _api(ApiTransport transport) async {
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
  return AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions);
}

Widget _app(Widget child, {double textScale = 1}) => MaterialApp(
    theme: AnumTheme.dark(),
    builder: (context, inner) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(textScale)),
        child: inner!),
    home: child);

void main() {
  group('models', () {
    test('audit page, filter query and labels', () {
      final page = AuditPage.fromJson({
        'items': [_audit(0)],
        'next_cursor': '',
      });
      expect(page.nextCursor, isNull);
      expect(page.retention, defaultAuditRetention);
      expect(page.items.single.metadata['api_key'], '[REDACTED]');
      expect(auditActionLabel('workspace_member.role_change'),
          'Workspace member role change');
      final query = AuditFilter(
              text: ' bea ',
              action: 'workspace.*',
              since: DateTime.utc(2026, 9, 1),
              until: DateTime.utc(2026, 9, 2))
          .toQuery(cursor: 'abc');
      expect(query, {
        'q': 'bea',
        'action': 'workspace.*',
        'since': '2026-09-01T00:00:00.000Z',
        'until': '2026-09-02T00:00:00.000Z',
        'limit': '50',
        'cursor': 'abc',
      });
      expect(const AuditFilter().isEmpty, isTrue);
    });

    test('member search covers user id and email', () {
      final members = [
        WorkspaceMember.fromJson(
            _member('user_b', 'member', email: 'bea@example.com')),
        WorkspaceMember.fromJson(_member('user_c', 'viewer')),
      ];
      expect(filterMembers(members, 'BEA@').map((m) => m.userId), ['user_b']);
      expect(filterMembers(members, ' ').length, 2);
      expect(members.first.email, 'bea@example.com');
    });
  });

  group('members screen', () {
    testWidgets('search, confirmed role change, last-owner lock and resend',
        (tester) async {
      tester.view.physicalSize = const Size(1200, 2600);
      addTearDown(tester.view.reset);
      final transport = Transport();
      final repo = ApiAdminRepository(await _api(transport));
      await tester.pumpWidget(_app(MembersScreen(
          controller: MembersController(repo),
          repository: repo,
          currentWorkspaceId: 'workspace_test')));
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('members-search')), 'bea@');
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('member-user_b')), findsOneWidget);
      expect(find.byKey(const Key('member-user_c')), findsNothing);
      await tester.enterText(find.byKey(const Key('members-search')), 'zzz');
      await tester.pumpAndSettle();
      expect(find.text('No members match'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('members-search')), '');
      await tester.pumpAndSettle();

      // The only active owner cannot be demoted or deactivated from the menu.
      await tester.tap(find.byTooltip('Actions for user_owner'));
      await tester.pumpAndSettle();
      final deactivate = tester.widget<PopupMenuItem<String>>(
          find.widgetWithText(PopupMenuItem<String>, 'Deactivate'));
      expect(deactivate.enabled, isFalse);
      await tester.tapAt(const Offset(5, 5));
      await tester.pumpAndSettle();

      // A role change waits for the confirmation; Cancel changes nothing.
      await tester.tap(find.byTooltip('Actions for user_b'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Make Viewer'));
      await tester.pumpAndSettle();
      expect(find.text('Make user_b viewer?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(transport.requests.where((r) => r.method == 'PUT'), isEmpty);
      await tester.tap(find.byTooltip('Actions for user_b'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Make Viewer'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Change role'));
      await tester.pumpAndSettle();
      expect(find.text('user_b is now viewer.'), findsOneWidget);
      expect(transport.requests.where((r) => r.method == 'PUT').single.body,
          {'role': 'viewer'});

      await tester.ensureVisible(
          find.byTooltip('Actions for the invitation to user_new'));
      await tester.pumpAndSettle();
      await tester
          .tap(find.byTooltip('Actions for the invitation to user_new'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Resend (new token)'));
      await tester.pumpAndSettle();
      expect(find.text('anum_inv_resent'), findsOneWidget);
      expect(find.textContaining('The previous one no longer works'),
          findsOneWidget);
    });
  });

  group('audit log screen', () {
    testWidgets('loads newest first, pages, filters and opens a record',
        (tester) async {
      tester.view.physicalSize = const Size(1200, 2600);
      addTearDown(tester.view.reset);
      final transport = Transport();
      final controller =
          AuditLogController(ApiAdminRepository(await _api(transport)));
      await tester.pumpWidget(_app(AuditLogScreen(controller: controller)));
      await tester.pumpAndSettle();

      expect(find.text('50 records loaded'), findsOneWidget);
      expect(find.textContaining('Retention. Audit records are kept'),
          findsOneWidget);
      await tester.scrollUntilVisible(find.text('Load older records'), 300,
          scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(find.text('Load older records'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Load older records'));
      await tester.pumpAndSettle();
      expect(controller.records.length, 75);
      expect(controller.nextCursor, isNull);
      expect(
          transport.requests
              .where((r) => r.uri.path.endsWith('/audit-records'))
              .map((r) => r.uri.queryParameters['cursor']),
          [null, '50']);

      await tester.scrollUntilVisible(
          find.byKey(const Key('audit-audit_0')), -300,
          scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(find.byKey(const Key('audit-audit_0')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('audit-audit_0')));
      await tester.pumpAndSettle();
      expect(find.text('corr_0'), findsOneWidget);
      expect(find.text('[REDACTED]'), findsOneWidget);
      Navigator.of(tester.element(find.text('corr_0'))).pop();
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('audit-search')), 'nobody');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(find.text('No records match these filters'), findsOneWidget);
      await tester.tap(find.text('Reset filters').last);
      await tester.pumpAndSettle();
      expect(find.text('50 records loaded'), findsOneWidget);
    });

    testWidgets('members and viewers see who can read it', (tester) async {
      final controller = AuditLogController(
          ApiAdminRepository(await _api(Transport(auditForbidden: true))));
      await tester.pumpWidget(_app(AuditLogScreen(controller: controller)));
      await tester.pumpAndSettle();
      expect(find.text('Owner or organization administrator access required'),
          findsOneWidget);
      expect(find.textContaining('The API answered: Audit log access requires'),
          findsOneWidget);
    });

    testWidgets('an empty log explains what will appear', (tester) async {
      final controller = AuditLogController(
          ApiAdminRepository(await _api(Transport(auditTotal: 0))));
      await tester.pumpWidget(_app(AuditLogScreen(controller: controller)));
      await tester.pumpAndSettle();
      expect(find.text('Nothing recorded yet'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      testWidgets(
          'audit log and members lay out at ${width.toInt()} wide, 200% text',
          (tester) async {
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = Size(width, 900);
        addTearDown(tester.view.reset);
        final repo = ApiAdminRepository(await _api(Transport()));
        await tester.pumpWidget(_app(
            AuditLogScreen(controller: AuditLogController(repo)),
            textScale: 2));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(_app(
            MembersScreen(
                controller: MembersController(repo),
                repository: repo,
                currentWorkspaceId: 'workspace_test'),
            textScale: 2));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      });
    }
  });
}
