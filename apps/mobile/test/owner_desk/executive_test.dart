// The Executive desk on the phone (docs/ANUM_MINE_EXECUTIVE_PA.md#mobile):
// the overview's three groups kept apart, the switch into a workspace, the
// grants an owner requests, approves by the exact hash, narrows and revokes,
// today's briefing, every state, Arabic, and 320 to 1024 px at 200 percent.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/executive/executive_data.dart';
import 'package:anum_mobile/features/executive/executive_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

final _hash = 'ab' * 32;

Map<String, Object?> _overview({bool truncated = false}) => {
      'version': 1,
      'tenant_id': 'tenant_a',
      'home_workspace_id': 'ws_home',
      'generated_at': '2026-11-10T08:00:00Z',
      'truncated': truncated,
      'workspaces': [
        {
          'workspace_id': 'ws_ops',
          'name': 'Operations',
          'access': 'granted',
          'grant_id': 'grant_1',
          'scopes': ['tasks', 'approvals', 'cost'],
          'expires_at': null,
          'as_of': '2026-11-10T08:00:00Z',
          'summary': {
            'tasks': {
              'by_status': {'running': 3, 'waiting_approval': 2},
              'blocked_agents': 0
            },
            'approvals': {'pending': 4, 'oldest_pending_at': null},
            'cost': {
              'month': '2026-11',
              'estimated_cost_usd': '12.50',
              'calls': 9,
              'unpriced_calls': 0,
              'currency': 'USD'
            },
          },
        },
        {
          'workspace_id': 'ws_home',
          'name': 'Home office',
          'access': 'not_granted',
          'reason': 'no_grant',
          'open_with': 'workspace_switch',
        },
        {
          'workspace_id': 'ws_sales',
          'name': 'Sales',
          'access': 'not_granted',
          'reason': 'grant_ended',
        },
        {'workspace_id': 'ws_gone', 'access': 'unavailable', 'reason': 'x'},
      ],
    };

class FakeExecutive implements ExecutiveRepository {
  bool owner = true, fail = false, empty = false;
  int status = 0;
  String current = 'ws_home';
  final grantRows = <Map<String, Object?>>[];
  final calls = <String>[];

  @override
  Future<String?> currentWorkspaceId() async => current;

  @override
  Future<ExecutiveOverview> overview() async {
    if (fail) throw const ApiException(502, 'The API did not answer');
    if (status != 0) throw ApiException(status, 'Refused');
    final body = _overview();
    if (empty) body['workspaces'] = <Object?>[];
    return ExecutiveOverview.fromJson(JsonReader(body));
  }

  @override
  Future<ExecutiveFigures?> ownSummary() async =>
      ExecutiveFigures.fromJson(JsonReader({
        'tasks': {
          'by_status': {'running': 1},
          'blocked_agents': 0
        },
        'approvals': {'pending': 1, 'oldest_pending_at': null},
      }));

  @override
  Future<Briefing> briefing(String lang) async => Briefing.fromJson(JsonReader({
        'title': lang == 'ar' ? 'إحاطة الإثنين' : 'Monday briefing',
        'sections': [
          {
            'key': 'approvals',
            'title': lang == 'ar' ? 'الموافقات' : 'Approvals',
            'headline': '2 waiting',
            'state': 'ok',
            'count': 2,
            'truncated': false,
            'items': [
              {'label': 'Send invoice', 'detail_text': 'high risk'}
            ],
            'note_texts': <String>[],
          },
          {
            'key': 'cost',
            'title': 'Cost',
            'headline': '',
            'state': 'restricted',
            'items': <Object?>[],
          },
        ],
      }));

  Map<String, Object?> _grant(String id, String status, List<String> scopes) =>
      {
        'id': id,
        'workspace_id': 'ws_home',
        'grantee_user_id': 'user_cfo',
        'scopes': scopes,
        'status': status,
        'stored_status': status,
        'payload_hash': _hash,
        'revision': 2,
        'requested_by': 'user_owner',
        'requested_at': '2026-11-09T10:00:00Z',
        'request_expires_at': '2026-11-16T10:00:00Z',
        'expires_at': null,
      };

  @override
  Future<List<ExecutiveGrant>> grants() async {
    if (!owner) throw const ApiException(403, 'Only an owner');
    return [for (final g in grantRows) ExecutiveGrant.fromJson(JsonReader(g))];
  }

  @override
  Future<void> createGrant(
      {required String grantee,
      required List<String> scopes,
      DateTime? expiresAt}) async {
    calls.add('create $grantee ${scopes.join(',')}');
    grantRows.add(_grant('grant_new', 'pending', scopes));
  }

  @override
  Future<void> approveGrant(ExecutiveGrant grant) async {
    calls.add('approve ${grant.id} ${grant.payloadHash}');
    grantRows.first['status'] = 'active';
  }

  @override
  Future<void> rejectGrant(ExecutiveGrant grant) async =>
      calls.add('reject ${grant.id}');

  @override
  Future<void> narrowGrant(ExecutiveGrant grant, List<String> scopes) async {
    calls.add('narrow ${grant.id} r${grant.revision} ${scopes.join(',')}');
    grantRows.first['scopes'] = scopes;
  }

  @override
  Future<void> revokeGrant(ExecutiveGrant grant) async {
    calls.add('revoke ${grant.id}');
    grantRows.first['status'] = 'revoked';
  }
}

void main() {
  test('the overview is read like the web: no leak, no repeat, null not 0', () {
    final overview = ExecutiveOverview.fromJson(JsonReader(_overview()));
    final ops = overview.of(ExecutiveAccess.granted).single;
    expect(ops.figures.tasks, 5);
    expect(ops.figures.blocked, 2);
    expect(ops.figures.meetings, isNull);
    expect(ops.figures.costUsd, '12.50');
    expect(overview.of(ExecutiveAccess.notGranted).first.canSwitch, isTrue);
    expect(overview.of(ExecutiveAccess.notGranted).last.canSwitch, isFalse);
    final leak = _overview();
    (leak['workspaces'] as List)[1] = {
      'workspace_id': 'ws_home',
      'access': 'not_granted',
      'reason': 'no_grant',
      'summary': <String, Object?>{},
    };
    expect(() => ExecutiveOverview.fromJson(JsonReader(leak)),
        throwsFormatException);
    final repeat = _overview();
    (repeat['workspaces'] as List).add((repeat['workspaces'] as List).first);
    expect(() => ExecutiveOverview.fromJson(JsonReader(repeat)),
        throwsFormatException);
  });

  testWidgets(
      'an owner reads the desk, switches, requests, approves, narrows and revokes',
      (tester) async {
    final fake = FakeExecutive();
    final switched = <String>[];
    final controller = ExecutiveController(fake, switchWorkspace: (id) async {
      switched.add(id);
      fake.current = id;
    });
    await pumpDesk(tester, ExecutiveScreen(controller: controller));

    expect(find.text('Granted to you'), findsOneWidget);
    expect(find.text('Operations'), findsOneWidget);
    expect(find.text('USD 12.50'), findsOneWidget);
    expect(find.text('Grant ended'), findsOneWidget);
    expect(find.text('Could not be read'), findsOneWidget);
    // The current workspace shows its own summary; another without a grant
    // shows nothing at all.
    expect(
        find.text(
            'No summary is shared. Nothing from this workspace is shown here.'),
        findsOneWidget);
    expect(find.text('Monday briefing'), findsOneWidget);
    expect(find.text('Send invoice: high risk'), findsOneWidget);
    expect(find.text('Not for your role'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ex-switch-ws_ops')));
    expect(switched, ['ws_ops']);
    expect(find.text('Now working in Operations.'), findsOneWidget);

    // A grant request: nothing until the hash is approved.
    expect(find.byKey(const Key('ex-grants-empty')), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ex-grant-new')));
    await tester.enterText(find.byKey(const Key('ex-grantee')), 'user_cfo');
    await tapDesk(tester, find.byKey(const Key('ex-scope-cost')));
    await tapDesk(tester, find.byKey(const Key('ex-grant-submit')));
    expect(fake.calls, ['create user_cfo counts,cost']);
    expect(
        find.text(
            'Request created. It gives no access until an owner approves the exact hash below.'),
        findsOneWidget);
    expect(find.text(_hash), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ex-grant-approve-grant_new')));
    expect(find.byType(AlertDialog), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ex-grant-approve-confirm')));
    expect(fake.calls.last, 'approve grant_new $_hash');
    expect(find.text('Grant approved.'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ex-grant-narrow-grant_new')));
    expect(find.byKey(const Key('ex-narrow-tasks')), findsNothing);
    await tapDesk(tester, find.byKey(const Key('ex-narrow-counts')));
    await tapDesk(tester, find.byKey(const Key('ex-narrow-save')));
    expect(fake.calls.last, 'narrow grant_new r2 cost');

    await tapDesk(tester, find.byKey(const Key('ex-grant-revoke-grant_new')));
    await tapDesk(tester, find.byKey(const Key('ex-grant-revoke-confirm')));
    expect(fake.calls.last, 'revoke grant_new');
    expect(tester.takeException(), isNull);
  });

  testWidgets('members, denied, not connected, empty and error states',
      (tester) async {
    final fake = FakeExecutive()..owner = false;
    final controller = ExecutiveController(fake);
    await pumpDesk(tester, ExecutiveScreen(controller: controller));
    expect(find.byKey(const Key('ex-grants-owner-only')), findsOneWidget);
    expect(find.byKey(const Key('ex-switch-ws_ops')), findsNothing);

    fake.status = 403;
    await controller.load();
    await tester.pumpAndSettle();
    expect(
        find.text('You cannot open the executive desk here'), findsOneWidget);

    fake.status = 503;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('The executive desk is not available on this server'),
        findsOneWidget);
    expect(find.byKey(const Key('ex-own')), findsOneWidget);

    fake
      ..status = 0
      ..empty = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('No workspaces to show yet'), findsOneWidget);

    fake
      ..empty = false
      ..fail = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ex-error')), findsOneWidget);
    fake.fail = false;
    await tapDesk(tester, find.text('Try again').first);
    expect(find.text('Operations'), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left, briefing in Arabic',
      (tester) async {
    await pumpDesk(tester,
        ExecutiveScreen(controller: ExecutiveController(FakeExecutive())),
        locale: const Locale('ar'));
    expect(find.text('المكتب التنفيذي'), findsWidgets);
    expect(find.text('ممنوحة لك'), findsOneWidget);
    expect(find.text('إحاطة الإثنين'), findsOneWidget);
    expect(Directionality.of(tester.element(find.byType(ExecutiveScreen))),
        TextDirection.rtl);
  });

  for (final (size, scale, locale) in deskSizes) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      final fake = FakeExecutive()
        ..grantRows.add({
          'id': 'grant_1',
          'workspace_id': 'ws_home',
          'grantee_user_id': 'user_cfo',
          'scopes': ['counts', 'tasks'],
          'status': 'pending',
          'stored_status': 'pending',
          'payload_hash': _hash,
          'revision': 1,
          'requested_by': 'user_owner',
          'requested_at': '2026-11-09T10:00:00Z',
          'request_expires_at': '2026-11-16T10:00:00Z',
        });
      await pumpDesk(
          tester, ExecutiveScreen(controller: ExecutiveController(fake)),
          size: size, textScale: scale, locale: locale);
      // Walk the whole page so every card is built at this size.
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -20000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
