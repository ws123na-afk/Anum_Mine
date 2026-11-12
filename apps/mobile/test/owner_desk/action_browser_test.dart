// The action browser on the phone (docs/action-browser.md#on-the-phone): the
// runs list paged by next_offset, a run's steps (in Arabic from the step
// detail), its approvals decided with the payload hash shown, the card code
// step left to the web, the live picture and the owner's input for a "Needs
// you" step, the saved logins and cards (secrets sent once, never kept),
// Stop, every state, Arabic, and 320 to 1024 px at 200 percent.
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/action_browser/action_browser_data.dart';
import 'package:anum_mobile/features/action_browser/action_browser_screen.dart';
import 'package:anum_mobile/features/action_browser/credentials_screen.dart';
import 'package:anum_mobile/features/workspace/api_workspace_repository.dart'
    show workspaceApprovalFromJson;
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

final _hash = 'e1' * 32;

// A 1 x 1 PNG standing in for the masked live picture.
final _png = base64Decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==');

Map<String, Object?> _run(int n,
        {String status = 'waiting',
        String? waiting = 'approval',
        bool watch = true}) =>
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
      'can_watch': watch,
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
  String? waiting = 'approval';
  bool watch = true;
  String? refuseInput;
  final calls = <String>[];
  final sentBodies = <Map<String, Object?>>[];
  int credentialStatus = 0;
  final creds = <BrowserCredential>[
    const BrowserCredential(
        id: 'cred_1',
        kind: 'login',
        handle: 'table',
        hint: 'j***@example.com on table.example',
        site: 'table.example'),
    const BrowserCredential(
        id: 'cred_2', kind: 'card', handle: 'main', hint: 'Card ending 4242'),
  ];

  @override
  Future<Uint8List?> frame(String sessionId) async {
    calls.add('frame $sessionId');
    return _png;
  }

  @override
  Future<void> input(String sessionId, Map<String, Object> event) async {
    if (refuseInput != null) throw ApiException(409, refuseInput!);
    calls.add(
        'input ${event['kind']} ${event['text'] ?? event['key'] ?? '${event['x']},${event['y']}'}');
  }

  @override
  Future<List<BrowserCredential>> credentials() async {
    if (credentialStatus != 0) {
      throw ApiException(credentialStatus,
          'Only a workspace owner manages saved logins and cards.');
    }
    return List.of(creds);
  }

  @override
  Future<BrowserCredential> saveCredential(
      String? id, Map<String, Object?> body) async {
    sentBodies.add(body);
    if (body['handle'] == 'taken') {
      throw const ApiException(409, 'That handle is already used.');
    }
    final saved = BrowserCredential(
        id: id ?? 'cred_new',
        kind: body['kind']! as String,
        handle: body['handle']! as String,
        hint: 'masked');
    creds.removeWhere((c) => c.id == saved.id);
    creds.add(saved);
    return saved;
  }

  @override
  Future<void> deleteCredential(String id) async {
    calls.add('delete $id');
    creds.removeWhere((c) => c.id == id);
  }

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
    return BrowserRun.fromJson(JsonReader(stopped
        ? _run(0, status: 'stopped', waiting: null, watch: watch)
        : _run(0, waiting: waiting, watch: watch)));
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
                    pollEvery: null,
                    framePollEvery: null))));

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
    // The card security code is left to the web; an owner approves a
    // "Needs you" step here, after doing it in the live picture.
    expect(find.byKey(const Key('ab-approve-appr_cvc')), findsNothing);
    expect(find.byKey(const Key('ab-web-only-appr_cvc')), findsOneWidget);
    expect(find.byKey(const Key('ab-approve-appr_hand')), findsOneWidget);
    expect(find.byKey(const Key('ab-picture')), findsOneWidget);
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

  test('page points, card numbers and drafts follow the web', () {
    expect(browserPagePoint(320, 100, 640, 400), (x: 640, y: 200));
    expect(browserPagePoint(-1, 0, 640, 400), isNull);
    expect(luhn('4242424242424242'), isTrue);
    expect(luhn('4242424242424241'), isFalse);
    final login = CredentialDraft(kind: 'login', handle: 'Bad Handle');
    expect(login.problem(editing: false), 'ab.handleRule');
    login.handle = 'table';
    expect(login.problem(editing: false), 'ab.siteNeeded');
    login
      ..site = 'table.example'
      ..username = 'me@example.com'
      ..password = ' pass phrase ';
    expect(login.problem(editing: false), isNull);
    expect(login.body()['password'], ' pass phrase ');
    login.forgetSecrets();
    expect(login.password, isEmpty);
    // Changing a card: empty secret fields keep the saved ones.
    final card = CredentialDraft(kind: 'card', handle: 'main', expiry: '13/29');
    expect(card.problem(editing: true), 'ab.expiryBad');
    card.expiry = '';
    expect(card.problem(editing: true), isNull);
    expect(card.body().containsKey('number'), isFalse);
  });

  testWidgets('Needs you: the owner types, presses keys and clicks the picture',
      (tester) async {
    final fake = FakeBrowser()..waiting = 'handoff';
    final c = BrowserRunController(fake, 'abs_0');
    await pumpDesk(tester,
        BrowserRunScreen(controller: c, pollEvery: null, framePollEvery: null));
    expect(find.byKey(const Key('ab-handoff')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('ab-type')), '123456');
    await tapDesk(tester, find.byKey(const Key('ab-type-send')));
    expect(fake.calls, contains('input text 123456'));
    expect(
        tester
            .widget<TextField>(find.byKey(const Key('ab-type')))
            .controller!
            .text,
        isEmpty);
    await tapDesk(tester, find.byKey(const Key('ab-key-Enter')));
    expect(fake.calls, contains('input key Enter'));
    await tester.ensureVisible(find.byKey(const Key('ab-picture-tap')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('ab-picture-tap')));
    await tester.pumpAndSettle();
    expect(fake.calls.where((call) => call.startsWith('input click')),
        hasLength(1));
    fake.refuseInput =
        'The browser takes your input only while it waits for you (Needs you).';
    await tapDesk(tester, find.byKey(const Key('ab-key-Tab')));
    expect(find.byKey(const Key('ab-input-error')), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'a member sees no picture and leaves a Needs you step to an owner',
      (tester) async {
    final fake = FakeBrowser()
      ..waiting = 'handoff'
      ..watch = false;
    await pumpDesk(
        tester,
        BrowserRunScreen(
            controller: BrowserRunController(fake, 'abs_0'),
            pollEvery: null,
            framePollEvery: null));
    expect(find.text('Only a workspace owner sees the live picture.'),
        findsOneWidget);
    expect(find.byKey(const Key('ab-handoff')), findsNothing);
    expect(find.byKey(const Key('ab-approve-appr_hand')), findsNothing);
    expect(fake.calls.where((call) => call.startsWith('frame')), isEmpty);
  });

  testWidgets(
      'saved logins and cards: list, add, change, delete; secrets go once',
      (tester) async {
    final fake = FakeBrowser();
    final c = CredentialsController(fake);
    await pumpDesk(tester, CredentialsScreen(controller: c));
    expect(find.text('j***@example.com on table.example'), findsOneWidget);
    expect(find.text('Card ending 4242'), findsOneWidget);

    await tapDesk(tester, find.byKey(const Key('cred-add-login')));
    await tester.enterText(
        find.byKey(const Key('cred-field-handle')), 'airline');
    await tapDesk(tester, find.byKey(const Key('cred-save')));
    expect(find.text('Give the site this login is for.'), findsOneWidget);
    expect(fake.sentBodies, isEmpty);
    await tester.enterText(
        find.byKey(const Key('cred-field-site')), 'air.example');
    await tester.enterText(
        find.byKey(const Key('cred-field-username')), 'me@example.com');
    await tester.enterText(
        find.byKey(const Key('cred-field-password')), 'example-pass');
    await tapDesk(tester, find.byKey(const Key('cred-save')));
    expect(fake.sentBodies.single, {
      'kind': 'login',
      'handle': 'airline',
      'site': 'air.example',
      'username': 'me@example.com',
      'password': 'example-pass'
    });
    expect(find.text('Saved airline.'), findsOneWidget);
    expect(find.textContaining('example-pass'), findsNothing);

    // Changing the card: the number field starts empty and is not sent.
    await tapDesk(tester, find.byKey(const Key('cred-edit-cred_2')));
    expect(find.text('Leave a secret field empty to keep the saved one.'),
        findsOneWidget);
    await tester.enterText(find.byKey(const Key('cred-field-expiry')), '08/29');
    await tapDesk(tester, find.byKey(const Key('cred-save')));
    expect(fake.sentBodies.last,
        {'kind': 'card', 'handle': 'main', 'expiry': '08/29'});

    await tapDesk(tester, find.byKey(const Key('cred-delete-cred_1')));
    await tapDesk(tester, find.byKey(const Key('cred-delete-confirm')));
    expect(fake.calls, contains('delete cred_1'));
    expect(find.text('Deleted table.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('saved logins and cards: denied, error, empty', (tester) async {
    final fake = FakeBrowser()..credentialStatus = 403;
    final c = CredentialsController(fake);
    await pumpDesk(tester, CredentialsScreen(controller: c));
    expect(find.byKey(const Key('cred-denied')), findsOneWidget);
    fake.credentialStatus = 503;
    await c.load();
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('cred-error')), findsOneWidget);
    fake
      ..credentialStatus = 0
      ..creds.clear();
    await tapDesk(tester, find.text('Try again'));
    expect(find.byKey(const Key('cred-empty')), findsOneWidget);
    expect(find.byKey(const Key('cred-add-card')), findsOneWidget);
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
            controller: BrowserRunController(fake, 'abs_x'),
            pollEvery: null,
            framePollEvery: null));
    expect(find.byKey(const Key('ab-run-gone')), findsOneWidget);
  });

  testWidgets('reads in Arabic, right to left', (tester) async {
    final fake = FakeBrowser();
    await pumpDesk(
        tester,
        BrowserRunScreen(
            controller: BrowserRunController(fake, 'abs_0'),
            pollEvery: null,
            framePollEvery: null),
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
              controller: BrowserRunController(fake, 'abs_0'),
              pollEvery: null,
              framePollEvery: null),
          size: size,
          textScale: scale,
          locale: locale);
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -60000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await pumpDesk(
          tester, CredentialsScreen(controller: CredentialsController(fake)),
          size: size, textScale: scale, locale: locale);
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -60000));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
