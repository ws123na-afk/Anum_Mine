// The action browser on the phone (docs/action-browser.md#on-the-phone): the
// runs list paged by next_offset, a run's steps (in Arabic from the step
// detail), its approvals decided with the payload hash shown, the card code
// and "Needs you" steps left to the web, Stop, every state, Arabic, and 320
// to 1024 px at 200 percent.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/action_browser/action_browser_data.dart';
import 'package:anum_mobile/features/action_browser/action_browser_screen.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart'
    show workspaceApprovalFromJson;
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

final _hash = 'e1' * 32;

Map<String, Object?> _run(int n,
        {String status = 'waiting', String? waiting = 'approval'}) =>
    {
      'id': 'abs_$n',
      'task_id': 'task_$n',
      'run_id': 'run_$n',
      'status': status,
      'goal': 'Book a table for two on Friday $n',
      'start_url': 'https://table.example/',
      'sites': ['table.example'],
      'logins': ['table'],
      'cards': ['main'],
      'step_count': 3,
      'current_url': 'https://table.example/checkout',
      'current_title': 'Checkout',
      'waiting': waiting,
      'pending': null,
      'outcome': null,
      'message': status == 'completed' ? 'Booked for 8 pm.' : '',
      'live': true,
      'can_watch': true,
      'can_stop': status == 'waiting' || status == 'running',
      'created_at': '2026-11-10T10:0$n:00Z',
      'updated_at': '2026-11-10T10:0$n:00Z',
      'ended_at': null,
      'steps': [
        {
          'seq': 1,
          'kind': 'opened',
          'summary': 'Opened table.example',
          'url': 'https://table.example/',
          'detail': {'host': 'table.example'},
          'created_at': '2026-11-10T10:00:10Z'
        },
        {
          'seq': 2,
          'kind': 'approval',
          'summary': 'Waiting for your approval: pay AED 240 on table.example',
          'url': null,
          'detail': <String, Object?>{},
          'created_at': '2026-11-10T10:00:20Z'
        },
      ],
    };

Map<String, Object?> _approval(String id, Map<String, Object?> args) => {
      'id': id,
      'task_id': 'task_0',
      'action': 'web.act',
      'reason': 'Waiting for your approval: pay AED 240 on table.example',
      'risk_level': 'high',
      'status': 'pending',
      'created_at': '2026-11-10T10:00:20Z',
      'expires_at': DateTime.now()
          .add(const Duration(hours: 1))
          .toUtc()
          .toIso8601String(),
      'payload_hash': _hash,
      'arguments': args,
    };

class FakeBrowser implements ActionBrowserRepository {
  int total = 23, status = 0;
  bool stopped = false, decided = false;
  final calls = <String>[];

  @override
  Future<BrowserRunPage> runs({int offset = 0, int limit = 20}) async {
    if (status != 0) throw ApiException(status, 'Refused');
    calls.add('runs $offset');
    final end = (offset + limit).clamp(0, total);
    return BrowserRunPage(items: [
      for (var i = offset; i < end; i++)
        BrowserRun.fromJson(JsonReader(_run(i % 10)..['id'] = 'abs_$i'))
    ], nextOffset: end < total ? end : null);
  }

  @override
  Future<BrowserRun> run(String id) async {
    if (status == 404) {
      throw const ApiException(
          404, 'That browser session is not in this workspace.');
    }
    return BrowserRun.fromJson(JsonReader(
        stopped ? _run(0, status: 'stopped', waiting: null) : _run(0)));
  }

  @override
  Future<BrowserRun> stop(String id) async {
    calls.add('stop $id');
    stopped = true;
    return run(id);
  }

  @override
  Future<List<WorkspaceApproval>> approvals(String taskId) async => decided
      ? const []
      : [
          workspaceApprovalFromJson(JsonReader(_approval('appr_pay', {
            'resume': 'step',
            'kind': 'binding_button',
            'site': 'table.example',
            'url': 'https://table.example/checkout',
            'target': 'Pay AED 240',
            'amount_text': 'AED 240',
          }))),
          workspaceApprovalFromJson(JsonReader(_approval('appr_cvc', {
            'resume': 'step',
            'kind': 'card_security_code',
            'site': 'table.example',
            'cvc': 'entered at approval',
          }))),
          workspaceApprovalFromJson(JsonReader(_approval('appr_hand', {
            'resume': 'handoff',
            'site': 'table.example',
          }))),
        ];

  @override
  Future<void> decide(WorkspaceApproval approval,
      {required bool approve}) async {
    calls.add(
        '${approve ? 'approve' : 'reject'} ${approval.id} ${approval.payloadHash}');
    decided = true;
  }
}

Widget _list(ActionBrowserController c, FakeBrowser fake) =>
    ActionBrowserScreen(
        controller: c,
        openRun: (context, run) => Navigator.of(context).push(
            MaterialPageRoute<void>(
                builder: (_) => BrowserRunScreen(
                    controller: BrowserRunController(fake, run.id),
                    pollEvery: null))));

void main() {
  test('step text in Arabic and approval facts never carry a secret', () {
    const opened = BrowserStep(
        seq: 1,
        kind: 'opened',
        summary: 'Opened a.example',
        detail: {'host': 'a.example'});
    expect(browserStepText(opened, arabic: false), 'Opened a.example');
    expect(browserStepText(opened, arabic: true), 'فتح a.example');
    const waiting = BrowserStep(
        seq: 2,
        kind: 'approval',
        summary: 'Waiting for your approval: pay AED 5');
    expect(
        browserStepText(waiting, arabic: true), 'بانتظار موافقتك: pay AED 5');
    final run = BrowserRun.fromJson(JsonReader(_run(0)));
    expect(run.savedItems, 2);
    expect(run.statusKey, 'ab.status_waiting_approval');
    final handoff = BrowserStepFacts.of(workspaceApprovalFromJson(
        JsonReader(_approval('a', {'resume': 'handoff'}))));
    expect(handoff.handoff, isTrue);
    expect(
        BrowserStepFacts.of(workspaceApprovalFromJson(
                JsonReader(_approval('b', {'start_url': 'https://x.example'}))))
            .kindKey,
        'ab.kind_scope');
  });

  testWidgets('runs page by page; a run approves with its hash and stops',
      (tester) async {
    final fake = FakeBrowser();
    await pumpDesk(tester, _list(ActionBrowserController(fake), fake));
    expect(find.byKey(const Key('ab-run-abs_0')), findsOneWidget);
    expect(find.text('Waiting for your approval'), findsWidgets);
    await tapDesk(tester, find.byKey(const Key('ab-more')));
    expect(fake.calls, ['runs 0', 'runs 20']);
    expect(find.byKey(const Key('ab-run-abs_22')), findsOneWidget);
    expect(find.byKey(const Key('ab-more')), findsNothing);

    await tapDesk(tester, find.byKey(const Key('ab-run-abs_0')));
    expect(find.text('Opened table.example'), findsOneWidget);
    expect(find.text('Saved logins and cards it may use: 2'), findsOneWidget);
    // The phone shows no handle and no card data.
    expect(find.textContaining('main'), findsNothing);
    expect(find.text('Pay AED 240'), findsOneWidget);
    // The card security code and a "Needs you" step are left to the web.
    expect(find.byKey(const Key('ab-approve-appr_cvc')), findsNothing);
    expect(find.byKey(const Key('ab-web-only-appr_cvc')), findsOneWidget);
    expect(find.byKey(const Key('ab-approve-appr_hand')), findsNothing);
    expect(find.byKey(const Key('ab-reject-appr_cvc')), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ab-approve-appr_pay')));
    expect(find.text('Approve this step?'), findsOneWidget);
    await tapDesk(tester, find.byKey(const Key('ab-approve-confirm')));
    expect(fake.calls.last, 'approve appr_pay $_hash');
    expect(find.text('Approved. The agent continues.'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('ab-stop')));
    await tapDesk(tester, find.byKey(const Key('ab-stop-confirm')));
    expect(fake.calls.last, 'stop abs_0');
    expect(find.text('Stopped. The browser is closed.'), findsOneWidget);
    expect(find.byKey(const Key('ab-stop')), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('empty, denied, error and a run that is gone', (tester) async {
    final fake = FakeBrowser()..total = 0;
    final c = ActionBrowserController(fake);
    await pumpDesk(tester, _list(c, fake));
    expect(find.byKey(const Key('ab-empty')), findsOneWidget);
    fake.status = 403;
    await c.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ab-denied')), findsOneWidget);
    fake.status = 500;
    await c.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('ab-error')), findsOneWidget);

    fake.status = 404;
    await pumpDesk(
        tester,
        BrowserRunScreen(
            controller: BrowserRunController(fake, 'abs_x'), pollEvery: null));
    expect(find.byKey(const Key('ab-run-gone')), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left', (tester) async {
    final fake = FakeBrowser();
    await pumpDesk(
        tester,
        BrowserRunScreen(
            controller: BrowserRunController(fake, 'abs_0'), pollEvery: null),
        locale: const Locale('ar'));
    expect(find.text('فتح table.example'), findsOneWidget);
    expect(find.text('بانتظار موافقتك'), findsWidgets);
    expect(Directionality.of(tester.element(find.byType(BrowserRunScreen))),
        TextDirection.rtl);
  });

  for (final (size, scale, locale) in deskSizes) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      final fake = FakeBrowser();
      await pumpDesk(tester, _list(ActionBrowserController(fake), fake),
          size: size, textScale: scale, locale: locale);
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -60000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await pumpDesk(
          tester,
          BrowserRunScreen(
              controller: BrowserRunController(fake, 'abs_0'), pollEvery: null),
          size: size,
          textScale: scale,
          locale: locale);
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -60000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
