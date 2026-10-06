// Members, invitations and model budgets in the mobile client
// (docs/identity.md, docs/model-gateway.md): wire contract, validation,
// permission from the API's 403, the one-time token, and the 402 budget stop.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/admin/admin.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _token = 'anum_inv_testTokenValue123';

JsonMap _member(String id, String role, {bool active = true}) => {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': id,
      'role': role,
      'active': active,
      'created_at': '2026-10-01T10:00:00Z',
      'updated_at': '2026-10-01T10:00:00Z',
    };

JsonMap _invitation({String status = 'pending', String id = 'invitation_1'}) =>
    {
      'id': id,
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'role': 'viewer',
      'invitee_user_id': 'user_new',
      'invitee_email': null,
      'status': status,
      'created_by_user_id': 'user_owner',
      'expires_at': DateTime.now()
          .toUtc()
          .add(const Duration(days: 7, minutes: 5))
          .toIso8601String(),
      'accepted_by_user_id': null,
      'accepted_at': null,
      'revoked_at': null,
      'created_at': '2026-10-06T10:00:00Z',
    };

JsonMap _envelope(String code, String message) => {
      'error': {'code': code, 'message': message, 'correlation_id': 'corr'}
    };

final JsonMap _budgets = {
  'period_start': '2026-10-01',
  'resets_on': '2026-11-01',
  'tenant': {
    'budget': null,
    'usage': {
      'input_tokens': 100,
      'output_tokens': 50,
      'estimated_cost_usd': 0.5,
      'calls': 2,
      'unpriced_calls': 0
    },
    'total_tokens': 150,
    'exceeded': false,
  },
  'workspace': {
    'budget': {
      'scope': 'workspace',
      'monthly_cost_limit_usd': 2.0,
      'monthly_token_limit': 150,
      'updated_at': '2026-10-02T10:00:00Z',
      'updated_by': 'user_owner'
    },
    'usage': {
      'input_tokens': 100,
      'output_tokens': 50,
      'estimated_cost_usd': 1.7,
      'calls': 2,
      'unpriced_calls': 1
    },
    'total_tokens': 150,
    'exceeded': true,
  },
};

class AdminTransport implements ApiTransport {
  AdminTransport({this.forbidden = false});
  final bool forbidden;
  final requests = <ApiRequest>[];
  List<JsonMap> members = [
    _member('user_owner', 'owner'),
    _member('user_b', 'member')
  ];
  List<JsonMap> invitations = [_invitation()];
  JsonMap budgets = Map.of(_budgets);

  @override
  Future<ApiResponse> send(ApiRequest r) async {
    requests.add(r);
    final path = r.uri.path;
    ApiResponse ok(Object body) => ApiResponse(
        statusCode: 200, body: body is JsonMap ? body : {'data': body});
    if (forbidden &&
        (path.endsWith('/workspace-members') ||
            path.endsWith('/workspace-invitations') ||
            path.endsWith('/model-budgets')) &&
        r.method == 'GET') {
      return ApiResponse(
          statusCode: 403,
          body: _envelope('forbidden', 'permission_denied: membership:manage'));
    }
    if (path.endsWith('/workspace-members')) return ok(members);
    if (path.endsWith('/workspace-invitations') && r.method == 'GET') {
      return ok(invitations);
    }
    if (path.endsWith('/workspace-invitations') && r.method == 'POST') {
      final created = {..._invitation(id: 'invitation_new'), ...?r.body};
      invitations = [created, ...invitations];
      return ok({'invitation': created, 'token': _token});
    }
    if (path.endsWith('/invitation_1/revoke')) {
      invitations = [_invitation(status: 'revoked')];
      return ok(invitations.first);
    }
    if (path.endsWith('/workspace-invitations/accept')) {
      return ok({
        'invitation': _invitation(status: 'accepted'),
        'membership': {
          ..._member('user_new', 'viewer'),
          'workspace_id': 'workspace_sales'
        },
      });
    }
    if (path.contains('/workspace-members/user_owner/')) {
      return ApiResponse(
          statusCode: 409,
          body: _envelope('conflict',
              'The last active owner cannot be demoted or deactivated'));
    }
    if (path.endsWith('/user_b/deactivate')) {
      members = [members.first, _member('user_b', 'member', active: false)];
      return ok(members.last);
    }
    if (path.endsWith('/user_b/role')) {
      members = [members.first, _member('user_b', r.body!['role']! as String)];
      return ok(members.last);
    }
    if (path.endsWith('/model-budgets')) return ok(budgets);
    if (path.endsWith('/model-budgets/workspace')) {
      final workspace = Map.of(budgets['workspace']! as JsonMap)
        ..['budget'] = {
          'scope': 'workspace',
          ...r.body!,
          'updated_at': '2026-10-06T12:00:00Z',
          'updated_by': 'user_owner'
        }
        ..['exceeded'] = false;
      budgets = {...budgets, 'workspace': workspace};
      return ok(budgets);
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

Widget _app(Widget child) => MaterialApp(theme: AnumTheme.dark(), home: child);

void main() {
  group('API errors', () {
    test('the error envelope keeps code and message; 402 and 403 are typed',
        () {
      final budget = ApiException.fromResponse(ApiResponse(
          statusCode: 402,
          body: _envelope('model_budget_exceeded',
              'This workspace has used its monthly model budget (tokens). It resets on 2026-11-01 (UTC); an owner can raise it in Settings.')));
      expect(budget.isBudgetExceeded, isTrue);
      expect(budget.code, 'model_budget_exceeded');
      expect(budget.message, contains('2026-11-01'));
      final denied = ApiException.fromResponse(ApiResponse(
          statusCode: 403, body: _envelope('forbidden', 'permission_denied')));
      expect(denied.isPermissionDenied, isTrue);
      expect(denied.toString(), contains('(403,'));
      expect(
          ApiException.fromResponse(
                  const ApiResponse(statusCode: 404, body: {'detail': 'Gone'}))
              .message,
          'Gone');
      expect(
          ApiException.fromResponse(const ApiResponse(statusCode: 500)).message,
          'Request failed');
    });
  });

  group('models', () {
    test('invitation drafts are validated like the API', () {
      final (draft, error) = validateInvitation(
          role: 'member', userId: ' u1 ', email: 'a@b.co', ttlHours: '');
      expect(error, isNull);
      expect(draft!.toJson(), {
        'role': 'member',
        'invitee_user_id': 'u1',
        'invitee_email': 'a@b.co',
        'ttl_hours': 168
      });
      expect(
          validateInvitation(
                  role: 'viewer', userId: '', email: '', ttlHours: '')
              .$2,
          isNotNull);
      expect(
          validateInvitation(
                  role: 'viewer', userId: '', email: 'nope', ttlHours: '')
              .$2,
          'Enter a valid email address.');
      expect(
          validateInvitation(
                  role: 'viewer', userId: 'u', email: '', ttlHours: '721')
              .$2,
          isNotNull);
    });

    test('tokens and web links parse; anything else does not', () {
      expect(parseInvitationInput(' $_token ')?.token, _token);
      final link = parseInvitationInput(
          'https://anum.example/#invitation=$_token&workspace=workspace_sales');
      expect(link?.token, _token);
      expect(link?.workspaceId, 'workspace_sales');
      expect(parseInvitationInput('https://a.example/#invitation=bad'), isNull);
      expect(parseInvitationInput('hello'), isNull);
    });

    test('meters, tones and limits', () {
      final view = BudgetOverview.fromJson(_budgets).workspace;
      final [cost, tokens] = view.meters;
      expect(cost.percent!.round(), 85);
      expect(cost.tone, MeterTone.warn);
      expect(cost.usedText, r'$1.70');
      expect(tokens.tone, MeterTone.stop);
      expect(tokens.limitText, '150');
      final tenant = BudgetOverview.fromJson(_budgets).tenant.meters.first;
      expect(tenant.percent, isNull);
      expect(tenant.percentText, 'No limit set');
      expect(
          const BudgetMeter(label: 'x', used: 0, limit: 0, format: formatUsd)
              .tone,
          MeterTone.stop);
      final (limits, error) = parseLimits(r'$1,250.5', '5,000,000');
      expect(error, isNull);
      expect(limits!.toJson(),
          {'monthly_cost_limit_usd': 1250.5, 'monthly_token_limit': 5000000});
      expect(parseLimits('', '').$1!.toJson(),
          {'monthly_cost_limit_usd': null, 'monthly_token_limit': null});
      expect(parseLimits('-1', '').$2, isNotNull);
      expect(parseLimits('', '1.5').$2, isNotNull);
      expect(formatUsd(1234567.891), r'$1,234,567.89');
      expect(formatUsd(0.001), r'< $0.01');
      expect(formatResetDate('2026-11-01'), '1 November 2026 (UTC)');
      expect(
          expiryLabel(DateTime.utc(2026, 10, 6, 10),
              now: DateTime.utc(2026, 10, 6, 12)),
          'Expired 2 h ago');
    });

    test('last active owner and sorting', () {
      final members = [
        WorkspaceMember.fromJson(_member('z', 'viewer')),
        WorkspaceMember.fromJson(_member('b', 'member', active: false)),
        WorkspaceMember.fromJson(_member('a', 'owner')),
      ];
      expect(sortMembers(members).map((m) => m.userId), ['a', 'z', 'b']);
      expect(isLastActiveOwner(members, members[2]), isTrue);
      expect(isLastActiveOwner(members, members[0]), isFalse);
    });
  });

  group('repository and controllers', () {
    test('accepting sends the link workspace as x-workspace-id', () async {
      final transport = AdminTransport();
      final repo = ApiAdminRepository(await _api(transport));
      final accept = AcceptInvitationController(repo,
          currentWorkspaceId: 'workspace_test');
      expect(await accept.accept('nonsense'), isFalse);
      expect(transport.requests, isEmpty);
      expect(
          await accept.accept(
              'https://anum.example/#invitation=$_token&workspace=workspace_sales'),
          isTrue);
      final request = transport.requests.single;
      expect(request.body, {'token': _token});
      expect(request.headers['x-workspace-id'], 'workspace_sales');
      expect(request.headers['authorization'], 'Bearer token');
      expect(accept.message,
          'You joined workspace_sales as viewer. Switch to that workspace to work in it.');
    });

    test('a 403 makes the members screen forbidden with the API answer',
        () async {
      final controller = MembersController(
          ApiAdminRepository(await _api(AdminTransport(forbidden: true))));
      await controller.load();
      expect(controller.phase, AdminPhase.forbidden);
      expect(controller.loadMessage, 'permission_denied: membership:manage');
      final budgets = BudgetsController(
          ApiAdminRepository(await _api(AdminTransport(forbidden: true))));
      await budgets.load();
      expect(budgets.phase, AdminPhase.forbidden);
    });

    test('last-owner 409 is shown; role change and invite work', () async {
      final transport = AdminTransport();
      final controller =
          MembersController(ApiAdminRepository(await _api(transport)));
      await controller.load();
      expect(controller.members.first.userId, 'user_owner');
      expect(
          await controller.setActive(controller.members.first, active: false),
          isFalse);
      expect(controller.actionFailed, isTrue);
      expect(controller.notice,
          'The last active owner cannot be demoted or deactivated');
      expect(await controller.changeRole(controller.members.last, 'viewer'),
          isTrue);
      expect(controller.members.last.role, 'viewer');
      expect(
          await controller.invite(const InvitationDraft(
              role: 'owner', email: 'ana@example.com', ttlHours: 48)),
          isTrue);
      expect(controller.created?.token, _token);
      expect(transport.requests.last.method, 'GET');
      final post = transport.requests.firstWhere((r) =>
          r.method == 'POST' && r.uri.path.endsWith('/workspace-invitations'));
      expect(post.body, {
        'role': 'owner',
        'invitee_email': 'ana@example.com',
        'ttl_hours': 48
      });
    });

    test('a task run refused with 402 shows the budget message, not an error',
        () async {
      final controller = WorkspaceController(ApiWorkspaceRepository(
          await _api(_TaskTransport()),
          fileTransfer: _NoFiles()));
      final task = await controller.createTask('Summarise the week');
      expect(task, isNull);
      expect(controller.budgetMessage, contains('resets on 2026-11-01'));
      expect(controller.message, isNull);
      expect(controller.phase.name, isNot('error'));
    });
  });

  group('screens', () {
    testWidgets('members: list, last owner hint, invite shows token once',
        (tester) async {
      tester.view.physicalSize = const Size(1200, 2400);
      addTearDown(tester.view.reset);
      final repo = ApiAdminRepository(await _api(AdminTransport()));
      final controller = MembersController(repo);
      await tester.pumpWidget(_app(MembersScreen(
          controller: controller,
          repository: repo,
          currentWorkspaceId: 'workspace_test')));
      await tester.pumpAndSettle();

      expect(find.text('user_owner'), findsOneWidget);
      expect(
          find.text(
              'Last active owner: the API refuses to demote or deactivate them.'),
          findsOneWidget);
      expect(find.text('user_new'), findsOneWidget);

      await tester.ensureVisible(find.text('Invite'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Invite'));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.widgetWithText(TextField, 'Email'), 'ana@example.com');
      await tester.ensureVisible(find.text('Create invitation'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Create invitation'));
      await tester.pumpAndSettle();

      expect(find.byKey(const Key('invitation-token')), findsOneWidget);
      expect(find.text(_token), findsOneWidget);
      String? copied;
      tester.binding.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') {
          copied = (call.arguments as Map)['text'] as String;
        }
        return null;
      });
      await tester.ensureVisible(find.text('Copy token'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Copy token'));
      await tester.pump();
      expect(copied, _token);
      await tester.ensureVisible(find.text('Done'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Done'));
      await tester.pumpAndSettle();
      expect(find.text(_token), findsNothing);
    });

    testWidgets('members: non-owners see the 403 explanation', (tester) async {
      final repo =
          ApiAdminRepository(await _api(AdminTransport(forbidden: true)));
      await tester.pumpWidget(_app(MembersScreen(
          controller: MembersController(repo),
          repository: repo,
          currentWorkspaceId: 'workspace_test')));
      await tester.pumpAndSettle();
      expect(find.text('Owner access required'), findsOneWidget);
      expect(find.textContaining('permission_denied: membership:manage'),
          findsOneWidget);
      expect(find.text('Accept an invitation'), findsOneWidget);
    });

    testWidgets('budgets: usage, reset date and saving limits', (tester) async {
      tester.view.physicalSize = const Size(1200, 3000);
      addTearDown(tester.view.reset);
      final transport = AdminTransport();
      final controller =
          BudgetsController(ApiAdminRepository(await _api(transport)));
      await tester.pumpWidget(_app(BudgetsScreen(controller: controller)));
      await tester.pumpAndSettle();

      expect(find.textContaining('Resets on 1 November 2026 (UTC)'),
          findsOneWidget);
      expect(find.text('Used up'), findsOneWidget);
      expect(find.text('85% used'), findsOneWidget);
      expect(find.text('100% used'), findsOneWidget);
      expect(find.textContaining('Unpriced calls add tokens'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('workspace-tokens')), 'abc');
      await tester.ensureVisible(find.text('Save limits').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save limits').last);
      await tester.pumpAndSettle();
      expect(find.textContaining('Token limit must be a whole number'),
          findsOneWidget);

      await tester.enterText(find.byKey(const Key('workspace-cost')), '25');
      await tester.enterText(
          find.byKey(const Key('workspace-tokens')), '5000000');
      await tester.ensureVisible(find.text('Save limits').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save limits').last);
      await tester.pumpAndSettle();
      expect(find.text('Budget saved.'), findsOneWidget);
      expect(transport.requests.last.body,
          {'monthly_cost_limit_usd': 25.0, 'monthly_token_limit': 5000000});
      expect(find.text('Used up'), findsNothing);
    });
  });
}

class _TaskTransport implements ApiTransport {
  @override
  Future<ApiResponse> send(ApiRequest r) async {
    final path = r.uri.path;
    if (path.endsWith('/tasks') && r.method == 'POST') {
      return const ApiResponse(statusCode: 200, body: {
        'id': 'task_1',
        'title': 'Summarise the week',
        'prompt': 'Summarise the week',
        'status': 'created',
        'tenant_id': 'tenant_test',
        'workspace_id': 'workspace_test',
        'created_at': '2026-10-06T10:00:00Z',
        'updated_at': '2026-10-06T10:00:00Z',
      });
    }
    if (path.endsWith('/run')) {
      return ApiResponse(
          statusCode: 402,
          body: _envelope('model_budget_exceeded',
              'This workspace has used its monthly model budget (tokens). It resets on 2026-11-01 (UTC); an owner can raise it in Settings.'));
    }
    return const ApiResponse(statusCode: 200, body: {'data': []});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) async => const {};
}
