// Approval integrity in the mobile client (docs/approvals-and-risk.md):
// exact arguments shown, the displayed payload hash sent back, expiry and
// the decider visible in history.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart';
import 'package:anum_mobile/features/workspace/approvals_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_controller.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/features/workspace/workspace_repository.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final _hash = '${'a' * 52}${'b' * 12}';

Map<String, Object?> _approvalJson(
        {String status = 'pending',
        String? decidedBy,
        String? decidedAt,
        String? expiresAt,
        String? decisionReason}) =>
    {
      'id': 'approval_1',
      'task_id': 'task_1',
      'action': 'external.action',
      'risk_level': 'high',
      'status': status,
      'reason': 'External or high-impact actions require explicit approval.',
      'created_at': '2026-10-06T10:00:00Z',
      'run_id': 'run_1',
      'step_id': 'step_1',
      'arguments': {
        'action': 'Publish the final update',
        'api_key': '[REDACTED]',
        'headers': {'accept': 'application/json'},
        'to': ['a@example.com'],
      },
      'payload_hash': _hash,
      'expires_at': expiresAt ??
          DateTime.now()
              .toUtc()
              .add(const Duration(hours: 5))
              .toIso8601String(),
      'decided_at': decidedAt,
      'decided_by': decidedBy,
      'decision_reason': decisionReason,
      'requested_by': 'user_test',
      'target': 'hooks.example',
    };

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/approve')) {
      return ApiResponse(statusCode: 200, body: {
        'approval': _approvalJson(
            status: 'approved',
            decidedBy: 'user_test',
            decidedAt: '2026-10-06T11:00:00Z',
            decisionReason: (request.body as Map?)?['reason'] as String?),
      });
    }
    final lists = <String, List<Object?>>{
      '/api/v1/approvals': [_approvalJson()],
    };
    return ApiResponse(
        statusCode: 200, body: {'data': lists[path] ?? const []});
  }
}

class _NoFiles implements WorkspaceFileTransfer {
  @override
  Future<void> download(WorkspaceFile file) async {}
  @override
  Future<JsonMap> upload(String path) => throw UnimplementedError();
}

class _FakeRepository implements WorkspaceRepository {
  _FakeRepository(this.approvals);
  final List<WorkspaceApproval> approvals;
  final decided = <(String, String?, bool)>[];
  final reasons = <String?>[];

  @override
  Future<WorkspaceSnapshot> loadWorkspace() async => WorkspaceSnapshot(
      tasks: const [],
      approvals: approvals,
      automations: const [],
      files: const [],
      memories: const []);

  @override
  Future<WorkspaceApproval> decideApproval(WorkspaceApproval approval,
      {required bool approve, String? reason}) async {
    decided.add((approval.id, approval.payloadHash, approve));
    reasons.add(reason);
    return approval;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

WorkspaceApproval _approval(
        {String status = 'pending',
        String? hash,
        DateTime? expiresAt,
        String? decidedBy,
        DateTime? decidedAt,
        String? decisionReason}) =>
    WorkspaceApproval(
      id: 'approval_$status',
      taskId: 'task_1',
      action: 'external.action',
      reason: 'Needs approval.',
      risk: 'high',
      status: status,
      createdAt: DateTime.utc(2026, 10, 6, 10),
      arguments: const {
        'action': 'Publish the final update',
        'planned_response': 'Posting the release notes',
        'api_key': '[REDACTED]',
      },
      payloadHash: hash ?? _hash,
      expiresAt: expiresAt,
      decidedBy: decidedBy,
      decidedAt: decidedAt,
      decisionReason: decisionReason,
    );

Future<ApiWorkspaceRepository> _repository(_Transport transport) async {
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
  return ApiWorkspaceRepository(
      AnumApiClient(
          baseUri: Uri.parse('http://localhost:8000'),
          transport: transport,
          sessions: sessions),
      fileTransfer: _NoFiles());
}

void main() {
  test('approvals map the exact arguments, hash, expiry and decider', () async {
    final transport = _Transport();
    final repository = await _repository(transport);
    final snapshot = await repository.loadWorkspace();
    final approval = snapshot.approvals.single;
    expect(approval.action, 'external.action');
    expect(approval.arguments['action'], 'Publish the final update');
    expect(approval.payloadHash, _hash);
    expect(approval.expiresAt, isNotNull);
    expect(approval.decidedBy, isNull);

    final decided = await repository.decideApproval(approval, approve: true);
    final request = transport.requests.last;
    expect(request.method, 'POST');
    expect(request.uri.path, '/api/v1/approvals/approval_1/approve');
    expect(request.body, {'payload_hash': _hash});
    expect(decided.status, 'approved');
    expect(decided.decidedBy, 'user_test');
    expect(decided.decidedAt, isNotNull);
    expect(approval.target, 'hooks.example');
    expect(approval.requestedBy, 'user_test');

    final withReason = await repository.decideApproval(approval,
        approve: true, reason: '  Checked with legal\r\n ');
    expect(transport.requests.last.body,
        {'payload_hash': _hash, 'reason': 'Checked with legal'});
    expect(withReason.decisionReason, 'Checked with legal');

    await repository.decideApproval(approval, approve: true, reason: '   ');
    expect(transport.requests.last.body, {'payload_hash': _hash});
  });

  test('reasons are trimmed, capped and optional', () {
    expect(normalizeReason(null), isNull);
    expect(normalizeReason('  '), isNull);
    expect(normalizeReason('x' * 600)!.length, approvalReasonMaxChars);
    expect(decisionBody(_approval().copyWithoutHash(), 'No'), {'reason': 'No'});
  });

  test('arguments flatten into sorted readable rows with redaction marked', () {
    final rows = approvalArguments(const {
      'to': ['a@example.com', 'b@example.com'],
      'headers': {'accept': 'application/json'},
      'api_key': '[REDACTED]',
      'empty': <String, Object?>{},
      'none': null,
    });
    expect(rows.map((r) => r.key), [
      'api_key',
      'empty',
      'headers.accept',
      'none',
      'to[0]',
      'to[1]',
    ]);
    expect(rows.first.redacted, isTrue);
    expect(rows[1].value, '{}');
    expect(rows[3].value, 'null');
  });

  test('expiry, approvability and decision summaries', () {
    final now = DateTime.utc(2026, 10, 6, 12);
    final lapsed =
        _approval(expiresAt: now.subtract(const Duration(seconds: 1)));
    expect(lapsed.effectiveStatus(now), 'expired');
    expect(lapsed.canApprove(now), isFalse);
    expect(expiryLabel(lapsed, now: now), 'Expired');

    final open = _approval(expiresAt: now.add(const Duration(hours: 5)));
    expect(open.canApprove(now), isTrue);
    expect(expiryLabel(open, now: now), 'Expires in 5 h');
    expect(open.copyWithoutHash().canApprove(now), isFalse);

    final approved = _approval(
        status: 'approved',
        decidedBy: 'user_owner',
        decidedAt: DateTime.utc(2026, 10, 6, 11));
    expect(decisionSummary(approved, now: now),
        startsWith('Approved by user_owner · '));
    expect(decisionSummary(_approval(status: 'expired'), now: now),
        'Expired without a decision');
    expect(shortHash(_hash), '${'a' * 12}…${'b' * 6}');
    expect(shortHash(null), 'not bound');
  });

  testWidgets('pending card shows the exact call and approving sends its hash',
      (tester) async {
    final repository = _FakeRepository([
      _approval(expiresAt: DateTime.now().add(const Duration(hours: 5))),
      _approval(
          status: 'rejected',
          decidedBy: 'user_owner',
          decidedAt: DateTime.utc(2026, 10, 6, 11)),
    ]);
    final controller = WorkspaceController(repository);
    await controller.load();
    tester.view.physicalSize = const Size(1200, 4000);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      home: Scaffold(body: ApprovalsScreen(controller: controller)),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Exactly what it will send'), findsOneWidget);
    expect(find.text('Publish the final update'), findsOneWidget);
    expect(find.text('Posting the release notes'), findsOneWidget);
    expect(find.text('[REDACTED]'), findsOneWidget);
    expect(find.textContaining('Payload hash aaaaaaaaaaaa…bbbbbb'),
        findsOneWidget);
    expect(find.textContaining('Rejected by user_owner'), findsOneWidget);

    await tester.tap(find.text('Approve'));
    await tester.pumpAndSettle();
    expect(find.textContaining('payload hash aaaaaaaaaaaa'), findsOneWidget);
    await tester.tap(find.text('Approve').last);
    await tester.pumpAndSettle();

    expect(repository.decided, [('approval_pending', _hash, true)]);
    expect(repository.reasons, [null]);
  });

  testWidgets('the typed reason is sent and the target is shown',
      (tester) async {
    final repository = _FakeRepository([
      WorkspaceApproval(
        id: 'approval_target',
        taskId: 'task_1',
        action: 'external.action',
        reason: 'Needs approval.',
        risk: 'high',
        status: 'pending',
        createdAt: DateTime.utc(2026, 10, 6, 10),
        payloadHash: _hash,
        expiresAt: DateTime.now().add(const Duration(hours: 5)),
        target: 'hooks.example',
      ),
      _approval(
          status: 'approved',
          decidedBy: 'user_owner',
          decidedAt: DateTime.utc(2026, 10, 6, 11),
          decisionReason: 'Second pair of eyes'),
    ]);
    final controller = WorkspaceController(repository);
    await controller.load();
    tester.view.physicalSize = const Size(1200, 4000);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      home: Scaffold(body: ApprovalsScreen(controller: controller)),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Sends to hooks.example'), findsOneWidget);
    expect(find.textContaining('Reason: Second pair of eyes'), findsOneWidget);
    await tester.enterText(
        find.widgetWithText(TextField, 'Reason (optional)'), ' Wrong list ');
    await tester.tap(find.text('Reject'));
    await tester.pumpAndSettle();
    expect(repository.decided, [('approval_target', _hash, false)]);
    expect(repository.reasons, ['Wrong list']);
  });

  testWidgets('an approval without a hash cannot be approved', (tester) async {
    final controller =
        WorkspaceController(_FakeRepository([_approval().copyWithoutHash()]));
    await controller.load();
    tester.view.physicalSize = const Size(1200, 4000);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      home: Scaffold(body: ApprovalsScreen(controller: controller)),
    ));
    await tester.pumpAndSettle();
    final approve = tester.widget<FilledButton>(find.ancestor(
        of: find.text('Approve'),
        matching: find.byWidgetPredicate((w) => w is FilledButton)));
    expect(approve.onPressed, isNull);
    expect(find.textContaining('cannot be approved'), findsOneWidget);
  });
}

extension on WorkspaceApproval {
  WorkspaceApproval copyWithoutHash() => WorkspaceApproval(
        id: id,
        taskId: taskId,
        action: action,
        reason: reason,
        risk: risk,
        status: status,
        createdAt: createdAt,
        arguments: arguments,
        expiresAt: expiresAt,
      );
}
