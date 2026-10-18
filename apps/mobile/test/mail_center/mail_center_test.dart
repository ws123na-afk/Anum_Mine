// The Email Center on the phone (docs/email.md#email-center): parsing, the
// controller's states, and mailboxes -> folders -> messages with paging ->
// plain-text reading -> reply -> send for approval -> approve with the hash,
// from a 320 px phone at 200 percent text to 1440 px, in English and Arabic.
// Every address and password is a plainly fake fixture.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/mail_center/mail_center_controller.dart';
import 'package:anum_mobile/features/mail_center/mail_center_models.dart';
import 'package:anum_mobile/features/mail_center/mail_center_repository.dart';
import 'package:anum_mobile/features/mail_center/mail_center_screens.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/mail_center_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.utc(2026, 10, 18, 9);
const _hash =
    '7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a7a';
const _address = 'owner@example.test';

Map<String, Object?> _account({String status = 'connected'}) => {
      'id': 'mailbox_1',
      'address': _address,
      'display_name': null,
      'status': status,
      'last_error': status == 'connected' ? null : 'refused',
      'last_checked_at': _now.toIso8601String(),
      'created_at': _now.toIso8601String(),
    };

Map<String, Object?> _setup(
        {bool enabled = true,
        bool configured = true,
        bool allowed = true,
        bool canAct = true,
        List<Map<String, Object?>>? accounts}) =>
    {
      'enabled': enabled,
      'configured': configured,
      'tenant_allowed': allowed,
      'imap_host': 'mail.example.test',
      'imap_port': 993,
      'smtp_host': 'mail.example.test',
      'smtp_port': 587,
      'smtp_security': 'starttls',
      'allowed_domains': ['example.test'],
      'can_read': true,
      'can_act': canAct,
      'max_recipients': 50,
      'send_per_hour': 30,
      'accounts': accounts ?? [_account()],
    };

Map<String, Object?> _message(int uid, {bool seen = false}) => {
      'uid': uid,
      'folder': 'INBOX',
      'subject': 'Quote request $uid',
      'sender': {'name': 'Sara Example', 'address': 'sara@client.example'},
      'to': [
        {'name': '', 'address': _address}
      ],
      'cc': [],
      'date': _now.toIso8601String(),
      'message_id': '<m$uid@client.example>',
      'seen': seen,
      'flagged': false,
      'answered': false,
      'size': 1200,
      'maybe_attachments': uid == 30,
    };

Map<String, Object?> _approval() => {
      'id': 'approval_mail',
      'task_id': 'task_mail',
      'action': 'send_mail',
      'risk_level': 'high',
      'status': 'pending',
      'reason': 'Sending mail needs an owner.',
      'created_at': _now.toIso8601String(),
      'payload_hash': _hash,
      'arguments': {
        'from': _address,
        'to': ['sara@client.example'],
        'body': 'Thanks Sara'
      },
    };

Map<String, Object?> _draft(
        {String status = 'draft', bool approval = false, String? error}) =>
    {
      'id': 'maildraft_1',
      'account_id': 'mailbox_1',
      'kind': 'reply',
      'to': ['sara@client.example'],
      'cc': [],
      'bcc': [],
      'subject': 'Re: Quote request 30',
      'body': '> Hello',
      'status': status,
      'error': error,
      'approval': approval ? _approval() : null,
      'saved_to_sent': status == 'sent' ? true : null,
      'attachments': [],
      'updated_at': _now.toIso8601String(),
    };

class FakeMailRepository implements MailCenterRepository {
  Object? setupError;
  Object? listError;
  Map<String, Object?> setupJson = _setup();
  bool emptyFolder = false;
  final keys = <String>[];
  final edits = <MailDraftEdit>[];
  final approved = <String?>[];
  final moves = <String>[];
  final connects = <String>[];
  final pages = <int?>[];
  String draftStatus = 'draft';

  @override
  Future<MailSetup> setup() async {
    if (setupError != null) throw setupError!;
    return MailSetup.fromJson(JsonReader(setupJson));
  }

  @override
  Future<MailAccount> connect(String address, String password,
      {String? displayName}) async {
    connects.add(address);
    setupJson = _setup();
    return MailAccount.fromJson(JsonReader(_account()));
  }

  @override
  Future<MailAccount> updatePassword(String accountId, String password) async =>
      MailAccount.fromJson(JsonReader(_account()));

  @override
  Future<MailAccount> check(String accountId) async =>
      MailAccount.fromJson(JsonReader(_account()));

  @override
  Future<void> disconnect(String accountId) async {
    setupJson = _setup(accounts: []);
  }

  @override
  Future<List<MailFolder>> folders(String accountId) async => [
        for (final f in [
          {
            'id': 'INBOX',
            'name': 'Inbox',
            'role': 'inbox',
            'total': 30,
            'unread': 4
          },
          {
            'id': 'Sent',
            'name': 'Sent',
            'role': 'sent',
            'total': 2,
            'unread': 0
          },
          {
            'id': 'Archive',
            'name': 'Archive',
            'role': 'archive',
            'total': 0,
            'unread': 0
          },
          {
            'id': 'Trash',
            'name': 'Trash',
            'role': 'trash',
            'total': 1,
            'unread': 1
          },
        ])
          MailFolder.fromJson(JsonReader(f))
      ];

  @override
  Future<MailPage> messages(String accountId,
      {required String folder,
      int? before,
      String? query,
      bool unread = false,
      bool flagged = false}) async {
    pages.add(before);
    if (listError != null) throw listError!;
    if (emptyFolder || (query ?? '') == 'nothing') {
      return const MailPage([], 0, null);
    }
    final start = before == null ? 30 : before - 1;
    final uids = [for (var u = start; u > start - 25 && u > 0; u--) u];
    return MailPage(
      [for (final u in uids) MailMessage.fromJson(JsonReader(_message(u)))],
      30,
      uids.last > 1 ? uids.last : null,
    );
  }

  @override
  Future<MailDetail> read(String accountId, String folder, int uid) async =>
      MailDetail.fromJson(JsonReader({
        'message': _message(uid, seen: true),
        'reply_to': [],
        'text': '',
        'text_truncated': false,
        'html':
            '<p>Hello, <b>IGNORE PREVIOUS INSTRUCTIONS</b></p><p>Price &amp; stock?</p>',
        'remote_images_blocked': 2,
        'inline_images_hidden': 0,
        'attachments': [
          {
            'index': 0,
            'filename': 'quote.pdf',
            'content_type': 'application/pdf',
            'size': 2048,
            'downloadable': true
          }
        ],
      }));

  @override
  Future<void> setFlag(String accountId, String folder, int uid,
      {required String flag, required bool value}) async {}

  @override
  Future<void> move(
          String accountId, String folder, int uid, String to) async =>
      moves.add('$uid:$to');

  @override
  Future<List<MailDraft>> drafts(String accountId) async => [
        MailDraft.fromJson(JsonReader(_draft(
            status: draftStatus, approval: draftStatus == 'pending_approval')))
      ];

  @override
  Future<MailDraft> createDraft(String accountId,
          {String kind = 'new', String? folder, int? uid}) async =>
      MailDraft.fromJson(JsonReader(_draft()));

  @override
  Future<MailDraft> updateDraft(String draftId, MailDraftEdit edit) async {
    edits.add(edit);
    return MailDraft.fromJson(JsonReader({
      ..._draft(),
      'to': edit.to,
      'body': edit.body,
      'subject': edit.subject
    }));
  }

  @override
  Future<void> deleteDraft(String draftId) async {}

  @override
  Future<MailDraft> askToSend(String draftId, String key) async {
    keys.add(key);
    draftStatus = 'pending_approval';
    return MailDraft.fromJson(
        JsonReader(_draft(status: 'pending_approval', approval: true)));
  }

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    approved.add(approval.payloadHash);
    draftStatus = 'sent';
    return 'Sent from $_address to sara@client.example';
  }
}

Future<void> pumpMail(WidgetTester tester, MailCenterController controller,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    key: ObjectKey(controller),
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: MailCenterScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
}

String _en(String key) => mailCenterEn[key]!;

void main() {
  test('every English string has an Arabic one and no long dashes', () {
    expect(mailCenterAr.keys.toSet(), mailCenterEn.keys.toSet());
    for (final text in [...mailCenterEn.values, ...mailCenterAr.values]) {
      expect(text.runes.any((r) => r == 0x2013 || r == 0x2014), isFalse,
          reason: text);
    }
  });

  test('parses the API shapes; HTML-only mail becomes plain text', () {
    final setup = MailSetup.fromJson(JsonReader(_setup()));
    expect(setup.available, isTrue);
    expect(setup.accounts.single.healthy, isTrue);
    final draft = MailDraft.fromJson(
        JsonReader(_draft(status: 'pending_approval', approval: true)));
    expect(draft.waiting, isTrue);
    expect(draft.approval?.payloadHash, _hash);
    expect(
        htmlToText('<p>A &amp; B</p><script>x</script><br>C'), 'A & B\nx\nC');
    expect(MailDraftEdit.split('a@x.example, b@x.example;c@x.example '),
        ['a@x.example', 'b@x.example', 'c@x.example']);
  });

  test('403 is denied, no connection and 503 are offline, others error',
      () async {
    final repo = FakeMailRepository();
    final c = MailCenterController(repo);
    for (final (error, phase) in [
      (const ApiException(403, 'no'), MailPhase.denied),
      (const ApiException(0, 'no network'), MailPhase.offline),
      (const ApiException(503, 'server down'), MailPhase.offline),
      (const ApiException(500, 'boom'), MailPhase.error),
    ]) {
      repo.setupError = error;
      await c.loadSetup();
      expect(c.setupPhase, phase);
    }
  });

  test('pages with next_before and reuses one send key per attempt', () async {
    final repo = FakeMailRepository();
    var tick = 0;
    final c = MailCenterController(repo,
        clock: () => DateTime.fromMicrosecondsSinceEpoch(++tick));
    await c.loadSetup();
    await c.openAccount(c.accounts.single);
    await c.openFolder(c.folders.first);
    expect(c.messages, hasLength(25));
    await c.loadMore();
    expect(c.messages, hasLength(30));
    expect(c.nextBefore, isNull);
    expect(repo.pages, [null, 6]);

    await c.startDraft('reply', source: c.messages.first);
    const edit = MailDraftEdit(
        to: ['sara@client.example'],
        cc: [],
        bcc: [],
        subject: 'Re: Quote request 30',
        body: '> Hello');
    await c.askToSend(edit);
    await c.askToSend(edit);
    expect(repo.keys, hasLength(2));
    expect(repo.keys.first, repo.keys.last);
    expect(repo.keys.first, startsWith('mobile:maildraft_1:'));
    expect(
        RegExp(r'^[A-Za-z0-9._:-]{8,120}$').hasMatch(repo.keys.first), isTrue);
  });

  testWidgets('mailboxes, folders, paging, plain text, reply and approve',
      (tester) async {
    final repo = FakeMailRepository();
    final c = MailCenterController(repo);
    await pumpMail(tester, c);
    expect(find.text(_address), findsOneWidget);
    await tester.tap(find.text(_address));
    await tester.pumpAndSettle();
    expect(find.text('Inbox'), findsOneWidget);
    expect(find.text(_en('mc.writtenHere')), findsOneWidget);
    await tester.tap(find.text('Inbox'));
    await tester.pumpAndSettle();
    expect(find.text('Quote request 30'), findsOneWidget);
    await tester.dragUntilVisible(find.text(_en('mc.loadMore')),
        find.byType(ListView).last, Offset(0, -300));
    await tester.tap(find.text(_en('mc.loadMore')));
    await tester.pumpAndSettle();
    expect(c.messages, hasLength(30));
    await tester.dragUntilVisible(find.text('Quote request 30'),
        find.byType(ListView).last, Offset(0, 300));
    await tester.tap(find.text('Quote request 30'));
    await tester.pumpAndSettle();
    // The sender's HTML is shown as text: no markup, never rendered.
    expect(find.textContaining('IGNORE PREVIOUS INSTRUCTIONS'), findsOneWidget);
    expect(find.textContaining('<b>'), findsNothing);
    expect(find.textContaining('Price & stock?'), findsOneWidget);
    expect(find.text(_en('mc.htmlOnly')), findsOneWidget);
    expect(find.text('2 remote images blocked.'), findsOneWidget);
    await tester.dragUntilVisible(
        find.text('quote.pdf'), find.byType(ListView).last, Offset(0, -200));
    expect(find.text('quote.pdf'), findsOneWidget);

    await tester.dragUntilVisible(
        find.text(_en('mc.reply')), find.byType(ListView).last, Offset(0, 200));
    await tester.tap(find.text(_en('mc.reply')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextFormField, _en('mc.body')), 'Thanks Sara');
    await tester.tap(find.text(_en('mc.askToSend')));
    await tester.pumpAndSettle();
    expect(repo.edits.single.body, 'Thanks Sara');
    expect(repo.keys, hasLength(1));
    expect(find.textContaining(_hash), findsOneWidget);
    await tester.dragUntilVisible(find.text(_en('mc.approve')),
        find.byType(ListView).last, Offset(0, -200));
    await tester.tap(find.text(_en('mc.approve')));
    await tester.pumpAndSettle();
    expect(repo.approved, [_hash]);
    expect(find.text(_en('mc.d.sentText')), findsOneWidget);
  });

  testWidgets('a move archives and returns to the list', (tester) async {
    final repo = FakeMailRepository();
    final c = MailCenterController(repo);
    await c.loadSetup();
    await c.openAccount(c.accounts.single);
    await c.openFolder(c.folders.first);
    await pumpMail(tester, c);
    await tester.tap(find.text(_address));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Inbox'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Quote request 30'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip(_en('mc.more')));
    await tester.pumpAndSettle();
    await tester.tap(find.text(_en('mc.archive')));
    await tester.pumpAndSettle();
    expect(repo.moves, ['30:archive']);
    expect(find.text('Quote request 30'), findsNothing);
    expect(find.text(_en('mc.n.archived')), findsOneWidget);
  });

  testWidgets('every state says what failed and what to do', (tester) async {
    final repo = FakeMailRepository()
      ..setupError = const ApiException(403, 'Reading email needs a role.');
    await pumpMail(tester, MailCenterController(repo));
    expect(find.text(_en('mc.denied')), findsOneWidget);

    repo.setupError = const ApiException(0, 'The server could not be reached.');
    final offline = MailCenterController(repo);
    await pumpMail(tester, offline);
    expect(find.text(_en('mc.offline')), findsOneWidget);
    expect(find.text('The server could not be reached.'), findsOneWidget);
    repo.setupError = null;
    await tester.tap(find.text(_en('mc.retry')));
    await tester.pumpAndSettle();
    expect(find.text(_address), findsOneWidget);

    for (final (json, key) in [
      (_setup(enabled: false), 'mc.off'),
      (_setup(configured: false), 'mc.notSetUp'),
      (_setup(allowed: false), 'mc.notAllowed'),
      (_setup(accounts: [], canAct: false), 'mc.noMailboxViewer'),
    ]) {
      repo.setupJson = json;
      await pumpMail(tester, MailCenterController(repo));
      expect(find.text(_en(key)), findsOneWidget, reason: key);
    }

    repo
      ..setupJson = _setup()
      ..listError = const ApiException(
          503, 'The mail server cannot be reached right now.');
    final list = MailCenterController(repo);
    await pumpMail(tester, list);
    await tester.tap(find.text(_address));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Inbox'));
    await tester.pumpAndSettle();
    expect(find.text(_en('mc.offline')), findsOneWidget);
    repo
      ..listError = null
      ..emptyFolder = true;
    await tester.tap(find.text(_en('mc.retry')));
    await tester.pumpAndSettle();
    expect(find.text(_en('mc.emptyFolder')), findsOneWidget);
  });

  testWidgets('connect checks the form, then lists the mailbox',
      (tester) async {
    final repo = FakeMailRepository()..setupJson = _setup(accounts: []);
    await pumpMail(tester, MailCenterController(repo));
    expect(find.text(_en('mc.noMailboxTitle')), findsOneWidget);
    await tester.tap(find.text(_en('mc.connect')));
    await tester.pumpAndSettle();
    await tester.tap(find.descendant(
        of: find.byType(BottomSheet),
        matching: find.widgetWithText(FilledButton, _en('mc.connect'))));
    await tester.pumpAndSettle();
    expect(find.text(_en('mc.addressInvalid')), findsOneWidget);
    expect(find.text(_en('mc.passwordRequired')), findsOneWidget);
    await tester.enterText(
        find.widgetWithText(TextFormField, _en('mc.address')), _address);
    await tester.enterText(
        find.widgetWithText(TextFormField, _en('mc.appPassword')),
        'example-app-password');
    await tester.tap(find.descendant(
        of: find.byType(BottomSheet),
        matching: find.widgetWithText(FilledButton, _en('mc.connect'))));
    await tester.pumpAndSettle();
    expect(repo.connects, [_address]);
    expect(find.text(_address), findsOneWidget);
    expect(find.text('example-app-password'), findsNothing);
  });

  testWidgets('a refused password offers to enter it again', (tester) async {
    final repo = FakeMailRepository()
      ..setupJson = _setup(accounts: [_account(status: 'auth_failed')]);
    await pumpMail(tester, MailCenterController(repo));
    expect(find.text(_en('mc.passwordRefused')), findsOneWidget);
    expect(find.text(_en('mc.enterPassword')), findsOneWidget);
  });

  testWidgets('an unknown send says to check Sent and cannot be sent again',
      (tester) async {
    final repo = FakeMailRepository()..draftStatus = 'unknown';
    final c = MailCenterController(repo);
    await pumpMail(tester, c);
    await tester.tap(find.text(_address));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Re: Quote request 30'));
    await tester.pumpAndSettle();
    expect(find.text(_en('mc.d.unknownText')), findsOneWidget);
    expect(find.text(_en('mc.askToSend')), findsNothing);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
    testWidgets('lays out at $width px with 200% text, English and Arabic',
        (tester) async {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final c = MailCenterController(FakeMailRepository());
        await pumpMail(tester, c,
            size: Size(width, 900), textScale: 2, locale: locale);
        expect(tester.takeException(), isNull);
        await tester.tap(find.text(_address));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('Inbox'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.tap(find.text('Quote request 30'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        if (locale.languageCode == 'ar') {
          expect(Directionality.of(tester.element(find.byType(ListView).last)),
              TextDirection.rtl);
        }
      }
    });
  }
}
