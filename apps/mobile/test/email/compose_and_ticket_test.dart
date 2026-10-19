// A new email from the unified inbox (docs/email.md) and "Create ticket" from
// an email or a WhatsApp conversation (docs/business.md#tickets-from-an-inbox):
// the wire bodies, validation, the send approval with its payload hash, the
// drafting approval, leaving with unsaved edits, and the layout from a 320 px
// phone at 200 percent text to 1024 px, in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/business/support_controller.dart';
import 'package:anum_mobile/features/business/support_repository.dart';
import 'package:anum_mobile/features/email/compose_email_screen.dart';
import 'package:anum_mobile/features/email/email_controller.dart';
import 'package:anum_mobile/features/email/email_models.dart';
import 'package:anum_mobile/features/email/email_repository.dart';
import 'package:anum_mobile/features/whatsapp/whatsapp.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/inbox_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import '../business_support_test.dart' show FakeSupport;
import '../whatsapp/whatsapp_test.dart' show FakeWhatsApp;
import 'inbox_test.dart' show FakeEmailRepository, pumpInbox;

const _hash =
    '9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d';

class _Transport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final draft = {
      'id': 'edr_new',
      'kind': 'new',
      'account_id': 'emb_1',
      'message_id': null,
      'to': ['sara@example.com'],
      'cc': ['ali@example.com'],
      'bcc': ['boss@example.com'],
      'subject': 'Quote',
      'body': 'Hello',
      'status': 'draft',
      'source': 'person',
    };
    if (request.uri.path.endsWith('/tickets/from-message')) {
      return const ApiResponse(statusCode: 409, body: {
        'detail':
            'This email is already linked to open ticket T-000007 (btkt_7).'
      });
    }
    if (request.method == 'PATCH') {
      return ApiResponse(statusCode: 200, body: draft);
    }
    return ApiResponse(statusCode: 201, body: {
      'status': 'done',
      'summary': 'Draft saved.',
      'draft': draft,
      'approval': null,
      'warning': null,
    });
  }
}

Future<AnumApiClient> _api(_Transport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'example',
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

List<EmailAccount> _accounts({bool reconnect = false}) => [
      EmailAccount.fromJson(JsonReader({
        'id': 'emb_1',
        'provider': 'gmail',
        'address': 'shop@example.com',
        'status': reconnect ? 'reauth_required' : 'connected',
      })),
      EmailAccount.fromJson(JsonReader({
        'id': 'emb_2',
        'provider': 'outlook',
        'address': 'old@example.com',
        'status': 'reauth_required',
      })),
    ];

Widget _app(Widget home,
        {Locale locale = const Locale('en'), double scale = 1}) =>
    MaterialApp(
      theme: AnumTheme.dark(),
      locale: locale,
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
              disableAnimations: true, textScaler: TextScaler.linear(scale)),
          child: child!),
      home: home,
    );

/// The compose screen pushed over a page, so leaving it can be tested.
Future<void> _pumpCompose(WidgetTester tester, FakeEmailRepository repo,
    {Size size = const Size(390, 844),
    double scale = 1,
    Locale locale = const Locale('en'),
    List<EmailAccount>? accounts}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(_app(
    Builder(
      builder: (context) => Scaffold(
        body: Center(
          child: TextButton(
            onPressed: () => Navigator.push(
                context,
                MaterialPageRoute<void>(
                    builder: (_) => ComposeEmailScreen(
                        repository: repo, accounts: accounts ?? _accounts()))),
            child: const Text('open compose'),
          ),
        ),
      ),
    ),
    locale: locale,
    scale: scale,
  ));
  await tester.tap(find.text('open compose'));
  await tester.pumpAndSettle();
}

Future<void> _tapKey(WidgetTester tester, String key) async {
  // The form is a lazy list: at 200% text the buttons start unbuilt.
  await tester.scrollUntilVisible(find.byKey(Key(key)), 200,
      scrollable: find
          .descendant(
              of: find.byType(ComposeEmailScreen),
              matching: find.byType(Scrollable))
          .first);
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key(key)));
  await tester.pumpAndSettle();
}

void main() {
  test('the compose catalogs have the same keys and placeholders', () {
    expect(inboxAr.keys.toSet(), inboxEn.keys.toSet());
    final placeholders = RegExp(r'\{(\w+)\}');
    for (final key in inboxEn.keys) {
      expect(placeholders.allMatches(inboxAr[key]!).map((m) => m[1]).toSet(),
          placeholders.allMatches(inboxEn[key]!).map((m) => m[1]).toSet(),
          reason: key);
    }
  });

  test('splits To, Cc and Bcc on commas and semicolons', () {
    final parts = splitAddresses(' a@b.example; c@d.example ,,nope, x@y');
    expect(parts.valid, ['a@b.example', 'c@d.example']);
    expect(parts.invalid, ['nope', 'x@y']);
  });

  test('a new draft and a ticket from a message use the documented wire',
      () async {
    final transport = _Transport();
    final api = await _api(transport);
    final repo = ApiEmailRepository(api);
    const fields = EmailDraftFields(
        accountId: 'emb_1',
        to: ['sara@example.com'],
        cc: ['ali@example.com'],
        bcc: ['boss@example.com'],
        subject: 'Quote',
        body: 'Hello');
    final draft = await repo.createDraft(fields);
    expect(transport.requests.last.method, 'POST');
    expect(transport.requests.last.uri.path, '/api/v1/email/drafts');
    expect(transport.requests.last.body, {
      'account_id': 'emb_1',
      'to': ['sara@example.com'],
      'cc': ['ali@example.com'],
      'bcc': ['boss@example.com'],
      'subject': 'Quote',
      'body': 'Hello',
    });
    expect(draft.isNew, isTrue);
    expect(draft.messageId, isNull);
    expect(draft.accountId, 'emb_1');
    expect(draft.cc, ['ali@example.com']);
    expect(draft.bcc, ['boss@example.com']);

    await repo.updateDraft('edr_new', fields: fields);
    expect(transport.requests.last.method, 'PATCH');
    expect(transport.requests.last.body!.containsKey('account_id'), isFalse);
    expect(transport.requests.last.body!['bcc'], ['boss@example.com']);
    await repo.updateDraft('edr_1', body: 'Only the text');
    expect(transport.requests.last.body, {'body': 'Only the text'});

    final support = ApiSupportRepository(api);
    await expectLater(
        support.fromMessage(
            source: 'email',
            refId: 'eml_1',
            title: ' Hi there ',
            severity: 'high'),
        throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'status', 409)
            .having((e) => e.readable, 'message', contains('T-000007'))));
    expect(transport.requests.last.uri.path,
        '/api/v1/business/support/tickets/from-message');
    expect(transport.requests.last.body, {
      'source': 'email',
      'ref_id': 'eml_1',
      'title': 'Hi there',
      'severity': 'high',
    });
  });

  testWidgets('the inbox offers New email and opens the compose screen',
      (tester) async {
    await pumpInbox(tester, EmailController(FakeEmailRepository()));
    expect(find.text('New email'), findsOneWidget);
    await tester.tap(find.byKey(const Key('inbox-compose')));
    await tester.pumpAndSettle();
    expect(find.byType(ComposeEmailScreen), findsOneWidget);
    expect(find.text('shop@example.com'), findsWidgets);
  });

  testWidgets('a bad address or a missing To stops before the API',
      (tester) async {
    final repo = FakeEmailRepository();
    await _pumpCompose(tester, repo);
    // Mailboxes waiting for a reconnect are not offered.
    await tester.tap(find.byKey(const Key('compose-from')));
    await tester.pumpAndSettle();
    expect(find.text('old@example.com'), findsNothing);
    await tester.tap(find.text('shop@example.com').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('are not listed'), findsOneWidget);

    await tester.enterText(find.byKey(const Key('compose-to')),
        'sara@example.com; not-an-address');
    await _tapKey(tester, 'compose-save');
    expect(find.text('Not an e-mail address: not-an-address'), findsOneWidget);
    expect(repo.created, isEmpty);

    await tester.enterText(find.byKey(const Key('compose-to')), '');
    await tester.enterText(find.byKey(const Key('compose-body')), 'Hello');
    await _tapKey(tester, 'compose-ask');
    expect(find.text('Add at least one address in To before asking to send.'),
        findsOneWidget);
    expect(repo.created, isEmpty);
    expect(repo.asked, isEmpty);
  });

  testWidgets(
      'save creates the draft, ask shows the approval with its hash, '
      'approve sends', (tester) async {
    final repo = FakeEmailRepository();
    await _pumpCompose(tester, repo);
    await tester.enterText(
        find.byKey(const Key('compose-to')), 'sara@example.com');
    await tester.enterText(
        find.byKey(const Key('compose-cc')), 'ali@example.com,');
    await tester.enterText(
        find.byKey(const Key('compose-bcc')), 'boss@example.com');
    await tester.enterText(find.byKey(const Key('compose-subject')), 'Quote');
    await tester.enterText(
        find.byKey(const Key('compose-body')), 'Here is the quote.');
    await _tapKey(tester, 'compose-save');
    expect(repo.created.single.toCreate(), {
      'account_id': 'emb_1',
      'to': ['sara@example.com'],
      'cc': ['ali@example.com'],
      'bcc': ['boss@example.com'],
      'subject': 'Quote',
      'body': 'Here is the quote.',
    });
    expect(find.text('Draft saved.'), findsOneWidget);

    // An edit after saving is saved before asking.
    await tester.enterText(
        find.byKey(const Key('compose-body')), 'Here is the new quote.');
    await _tapKey(tester, 'compose-ask');
    expect(repo.created, hasLength(1));
    expect(repo.fieldEdits.single.body, 'Here is the new quote.');
    expect(repo.asked, ['edr_new']);
    expect(find.byKey(const Key('compose-send-approval')), findsOneWidget);
    expect(find.textContaining('9d9d9d9d9d9d…'), findsOneWidget);
    expect(find.text('Cc: ali@example.com'), findsOneWidget);
    expect(find.text('Bcc: boss@example.com'), findsOneWidget);
    expect(find.text('Subject: Quote'), findsOneWidget);
    expect(find.text('Only workspace owners can approve sending.'),
        findsOneWidget);
    expect(find.byKey(const Key('compose-ask')), findsNothing);

    await tester.ensureVisible(find.text('Approve and send'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Approve and send'));
    await tester.pumpAndSettle();
    expect(repo.approved, [_hash]);
    expect(find.text('Sent to sara@example.com'), findsOneWidget);
    expect(find.text('This email was sent.'), findsOneWidget);
    // Sent: leaving asks nothing.
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('open compose'), findsOneWidget);
  });

  testWidgets('a member approving sees the API refusal', (tester) async {
    final repo = FakeEmailRepository()
      ..approveError = const ApiException(
          403, 'Only a workspace owner can approve this action.');
    await _pumpCompose(tester, repo);
    await tester.enterText(
        find.byKey(const Key('compose-to')), 'sara@example.com');
    await tester.enterText(find.byKey(const Key('compose-body')), 'Hello');
    await _tapKey(tester, 'compose-ask');
    await tester.ensureVisible(find.text('Approve and send'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Approve and send'));
    await tester.pumpAndSettle();
    expect(find.text('Only a workspace owner can approve this action.'),
        findsOneWidget);
    expect(find.byKey(const Key('compose-send-approval')), findsOneWidget);
  });

  testWidgets('drafting that waits for approval shows the drafting approval',
      (tester) async {
    final repo = FakeEmailRepository()..draftingWaits = true;
    await _pumpCompose(tester, repo);
    await tester.enterText(
        find.byKey(const Key('compose-to')), 'sara@example.com');
    await _tapKey(tester, 'compose-save');
    expect(repo.created, hasLength(1));
    expect(find.byKey(const Key('compose-draft-approval')), findsOneWidget);
    expect(find.text('Draft saved.'), findsNothing);
    await _tapKey(tester, 'compose-draft-approval');
    await tester.tap(find.text('Approve drafting'));
    await tester.pumpAndSettle();
    expect(repo.approved, [_hash]);
    expect(find.byKey(const Key('compose-draft-approval')), findsNothing);
    expect(find.textContaining('Drafting was approved.'), findsOneWidget);
  });

  testWidgets(
      'after the paused drafting is approved, saving again edits that draft, not a copy',
      (tester) async {
    final repo = FakeEmailRepository()..draftingWaits = true;
    await _pumpCompose(tester, repo);
    await tester.enterText(
        find.byKey(const Key('compose-to')), 'sara@example.com');
    await tester.enterText(find.byKey(const Key('compose-subject')), 'Quote');
    await _tapKey(tester, 'compose-save');
    await _tapKey(tester, 'compose-draft-approval');
    await tester.tap(find.text('Approve drafting'));
    await tester.pumpAndSettle();
    expect(repo.created, hasLength(1));
    expect(repo.serverDrafts, hasLength(1));
    await tester.enterText(find.byKey(const Key('compose-subject')), 'Quote 2');
    await tester.pumpAndSettle();
    await _tapKey(tester, 'compose-save');
    expect(repo.created, hasLength(1), reason: 'no second draft is created');
    expect(repo.fieldEdits, hasLength(1));
    expect(repo.fieldEdits.single.subject, 'Quote 2');
    expect(find.byKey(const Key('compose-discard')), findsOneWidget);
  });

  testWidgets(
      'a saved new email is listed in the inbox and can be reopened and discarded',
      (tester) async {
    final repo = FakeEmailRepository();
    final saved = await repo.createDraft(const EmailDraftFields(
        accountId: 'emb_1',
        to: ['sara@example.com'],
        subject: 'Quote',
        body: 'Hello Sara'));
    repo.serverDrafts = [saved];
    await pumpInbox(tester, EmailController(repo));
    expect(find.text('Drafts to finish (1)'), findsOneWidget);
    await tester.tap(find.byKey(ValueKey('new-draft-${saved.id}')));
    await tester.pumpAndSettle();
    expect(find.byType(ComposeEmailScreen), findsOneWidget);
    expect(find.text('Hello Sara'), findsOneWidget);
    expect(find.text('sara@example.com'), findsOneWidget);
    await _tapKey(tester, 'compose-discard');
    await tester.tap(find.text('Discard draft').last);
    await tester.pumpAndSettle();
    expect(repo.discarded, [saved.id]);
    expect(find.byType(ComposeEmailScreen), findsNothing);
    expect(find.text('Drafts to finish (1)'), findsNothing);
  });

  testWidgets('a reopened draft can be sent for approval', (tester) async {
    final repo = FakeEmailRepository();
    final saved = await repo.createDraft(const EmailDraftFields(
        accountId: 'emb_1',
        to: ['sara@example.com'],
        subject: 'Quote',
        body: 'Hello Sara'));
    repo.serverDrafts = [saved];
    await pumpInbox(tester, EmailController(repo));
    await tester.tap(find.byKey(ValueKey('new-draft-${saved.id}')));
    await tester.pumpAndSettle();
    await _tapKey(tester, 'compose-ask');
    expect(repo.asked, [saved.id]);
    expect(find.byKey(const Key('compose-send-approval')), findsOneWidget);
  });

  testWidgets('leaving with unsaved edits asks before discarding',
      (tester) async {
    final repo = FakeEmailRepository();
    await _pumpCompose(tester, repo);
    await tester.enterText(find.byKey(const Key('compose-subject')), 'Hi');
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('Discard this email?'), findsOneWidget);
    await tester.tap(find.text('Keep editing'));
    await tester.pumpAndSettle();
    expect(find.byType(ComposeEmailScreen), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Discard'));
    await tester.pumpAndSettle();
    expect(find.text('open compose'), findsOneWidget);
  });

  testWidgets('with every mailbox waiting for a reconnect, it says so',
      (tester) async {
    await _pumpCompose(tester, FakeEmailRepository(),
        accounts: _accounts(reconnect: true));
    expect(find.textContaining('Every mailbox needs reconnecting'),
        findsOneWidget);
    expect(find.byKey(const Key('compose-ask')), findsNothing);
  });

  for (final width in [320.0, 1024.0]) {
    testWidgets('compose lays out at $width px with 200% text, en and ar',
        (tester) async {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        await tester.pumpWidget(const SizedBox());
        final repo = FakeEmailRepository();
        await _pumpCompose(tester, repo,
            size: Size(width, 900), scale: 2, locale: locale);
        expect(tester.takeException(), isNull);
        await tester.enterText(
            find.byKey(const Key('compose-to')), 'sara@example.com');
        await tester.enterText(find.byKey(const Key('compose-body')), 'Hello');
        await _tapKey(tester, 'compose-ask');
        expect(tester.takeException(), isNull);
        expect(repo.asked, ['edr_new']);
        await tester.scrollUntilVisible(
            find.byKey(const Key('compose-send-approval')), 200,
            scrollable: find
                .descendant(
                    of: find.byType(ComposeEmailScreen),
                    matching: find.byType(Scrollable))
                .first);
        expect(tester.takeException(), isNull);
        if (locale.languageCode == 'ar') {
          expect(find.text('الإرسال ينتظر الموافقة على هذه الرسالة بعينها.'),
              findsOneWidget);
          expect(
              Directionality.of(
                  tester.element(find.byType(ComposeEmailScreen))),
              TextDirection.rtl);
        }
      }
    });
  }

  group('create ticket', () {
    testWidgets('from an email: the reference, then a 409 names the open one',
        (tester) async {
      final support = FakeSupport();
      final controller = EmailController(FakeEmailRepository(),
          support: SupportController(support));
      await pumpInbox(tester, controller);
      await tester.tap(find.text('Sara Customer'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('inbox-create-ticket')));
      await tester.pumpAndSettle();
      expect(find.text('Create a support ticket'), findsOneWidget);
      // The subject is the default title.
      expect(find.widgetWithText(TextField, 'Do you have 205/55R16?'),
          findsOneWidget);
      await tester.tap(find.byKey(const Key('ticket-from-message-severity')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('High').last);
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Create ticket'));
      await tester.pumpAndSettle();
      expect(support.fromMessages, ['email eml_1 Do you have 205/55R16? high']);
      expect(find.text('Ticket T-000000 created.'), findsOneWidget);
      expect(find.text('Open'), findsOneWidget);
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();
      expect(find.text('T-000000'), findsWidgets);
      await tester.pageBack();
      await tester.pumpAndSettle();

      support.fromMessageError = const ApiException(409,
          'This email is already linked to open ticket T-000000 (btkt_0).');
      await tester.tap(find.byKey(const Key('inbox-create-ticket')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('ticket-from-message-title')), 'ab');
      await tester.tap(find.widgetWithText(FilledButton, 'Create ticket'));
      await tester.pumpAndSettle();
      expect(find.text('Give the ticket a title of at least 3 characters.'),
          findsOneWidget);
      expect(support.fromMessages, hasLength(1));
      await tester.enterText(
          find.byKey(const Key('ticket-from-message-title')), 'Stock question');
      await tester.tap(find.widgetWithText(FilledButton, 'Create ticket'));
      await tester.pumpAndSettle();
      expect(
          find.text(
              'This email is already linked to open ticket T-000000 (btkt_0).'),
          findsOneWidget);
      expect(find.text('Create a support ticket'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(find.text('Create a support ticket'), findsNothing);
    });

    testWidgets('without support wired, the email has no ticket action',
        (tester) async {
      await pumpInbox(tester, EmailController(FakeEmailRepository()));
      await tester.tap(find.text('Sara Customer'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('inbox-create-ticket')), findsNothing);
    });

    testWidgets('from a WhatsApp conversation, in Arabic too', (tester) async {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final support = FakeSupport();
        tester.view.physicalSize = const Size(320, 800);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.reset);
        await tester.pumpWidget(_app(
            WhatsAppScreen(
                controller: WhatsAppController(FakeWhatsApp(),
                    support: SupportController(support))),
            locale: locale,
            scale: 2));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Ahmed'));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const Key('wa-create-ticket')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        final ar = locale.languageCode == 'ar';
        await tester.tap(find.widgetWithText(
            FilledButton, ar ? 'إنشاء تذكرة' : 'Create ticket'));
        await tester.pumpAndSettle();
        expect(support.fromMessages, ['whatsapp wacv_1 null normal']);
        expect(
            find.text(
                ar ? 'أُنشئت التذكرة T-000000.' : 'Ticket T-000000 created.'),
            findsOneWidget);
        await tester.pumpWidget(const SizedBox());
      }
    });
  });
}
