// The WhatsApp inbox (docs/whatsapp.md, Clients): parsing, the controllers
// against a fake repository, and the screens from a 320 px phone at 200
// percent text to 1440 px, in English and Arabic (right to left).
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/whatsapp/whatsapp.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

final _now = DateTime.now().toUtc();
String _iso(Duration ago) => _now.subtract(ago).toIso8601String();

Map<String, Object?> conversationJson(String id,
        {String name = 'Ahmed',
        bool open = true,
        int unread = 1,
        int awaiting = 0,
        String category = 'price_stock'}) =>
    {
      'id': id,
      'contact_id': 'c_$id',
      'wa_id': '966500000001',
      'contact_name': name,
      'last_message_at': _iso(const Duration(minutes: 5)),
      'last_message_preview': 'How much for 205/55R16?',
      'last_direction': 'inbound',
      'last_inbound_at': _iso(const Duration(minutes: 5)),
      'unread_count': unread,
      'category': category,
      'category_source': 'rules',
      'summary': null,
      'suggestion': null,
      'needs_triage': false,
      'window_open': open,
      'window_closes_at': open
          ? _now.add(const Duration(hours: 23)).toIso8601String()
          : _iso(const Duration(hours: 3)),
      'awaiting_approval': awaiting,
    };

Map<String, Object?> approvalJson() => {
      'id': 'approval_wa',
      'task_id': 'task_wa',
      'action': 'send_whatsapp',
      'reason': 'External or high-impact actions require explicit approval.',
      'risk_level': 'high',
      'status': 'pending',
      'created_at': _iso(Duration.zero),
      'payload_hash': '5d' * 32,
      'expires_at': _now.add(const Duration(hours: 24)).toIso8601String(),
      'target': 'graph.facebook.com',
      'arguments': {'to': '966500000001', 'text': 'Yes, in stock'},
    };

Map<String, Object?> messageJson(String id,
        {bool inbound = true,
        String body = 'How much for 205/55R16?',
        String status = 'received',
        String kind = 'text',
        Map<String, Object?>? media,
        Map<String, Object?>? approval}) =>
    {
      'id': id,
      'conversation_id': 'wacv_1',
      'wamid': 'wamid.$id',
      'direction': inbound ? 'inbound' : 'outbound',
      'kind': kind,
      'body': body,
      'media': media,
      'template': null,
      'status': status,
      'error': null,
      'sent_by': null,
      'approval_id': approval == null ? null : 'approval_wa',
      'timestamp': _iso(const Duration(minutes: 4)),
      'approval': approval,
    };

class FakeWhatsApp implements WhatsAppRepository {
  bool configured = true;
  String role = 'owner';
  int statusOnList = 200;
  List<Map<String, Object?>> conversationRows = [
    conversationJson('wacv_1'),
    conversationJson('wacv_2', name: 'Sara Trading', open: false, unread: 0),
  ];
  List<Map<String, Object?>> messages = [messageJson('m1')];
  final sends = <String>[];
  final approvals = <String?>[];
  final rejects = <String>[];
  final filters = <String>[];

  /// Extra account fields (status, ids, auto-send list ...) for the ops tests.
  Map<String, Object?> accountExtra = {};

  /// Templates the API lists, and what the ops calls do (see
  /// whatsapp_ops_test.dart).
  List<Map<String, Object?>> templateRows = [];
  ApiException? templatesError, saveError, sendTemplateError, mediaError;
  ApiException? checkError, testError, disconnectError;
  String templateOutcome = 'awaiting_approval';
  final refreshes = <bool>[];
  final savedBodies = <Map<String, Object?>>[];
  final templateSends = <Map<String, Object?>>[];
  final mediaIds = <String>[];
  final savedFiles = <String>[];
  int checks = 0, tests = 0, disconnects = 0;
  Uint8List media = Uint8List.fromList(const [1, 2, 3]);

  ApiException? accountError;

  @override
  Future<WaAccount> account() async {
    if (accountError != null) throw accountError!;
    return _account();
  }

  WaAccount _account() => WaAccount.fromJson(JsonReader({
        'configured': configured,
        'can_manage': role == 'owner',
        'display_phone_number': '+966 11 000 0000',
        ...accountExtra,
      }));

  @override
  Future<WaTemplateList> templates({bool refresh = false}) async {
    refreshes.add(refresh);
    if (templatesError != null) throw templatesError!;
    final auto = [...?(accountExtra['auto_send_templates'] as List<Object?>?)];
    return WaTemplateList.fromJson(JsonReader({
      'templates': [
        for (final row in templateRows)
          {...row, 'auto_send': auto.contains(row['name'])}
      ],
      'fetched_at': _iso(Duration.zero),
    }));
  }

  @override
  Future<WaSendResult> sendTemplate(
      String id, WaTemplate template, List<String> parameters) async {
    if (sendTemplateError != null) throw sendTemplateError!;
    templateSends.add({
      'name': template.name,
      'language': template.language,
      'parameters': parameters
    });
    final out = messageJson('tpl',
        inbound: false,
        body: fillTemplate(template.body, parameters),
        status: templateOutcome == 'sent' ? 'sent' : 'awaiting_approval',
        approval:
            templateOutcome == 'awaiting_approval' ? approvalJson() : null);
    messages = [...messages, out];
    return WaSendResult.fromJson(JsonReader({
      'outcome': templateOutcome,
      'message': out,
      'detail': templateOutcome == 'failed' ? 'Meta refused it.' : 'Done.',
    }));
  }

  @override
  Future<WaAccount> saveAccount(WaAccountUpdate update) async {
    // The body is captured before the caller clears the secrets.
    savedBodies.add(update.body());
    if (saveError != null) throw saveError!;
    accountExtra = {
      ...accountExtra,
      'phone_number_id': update.phoneNumberId,
      'business_account_id': update.businessAccountId,
      'owner_test_number':
          update.ownerTestNumber.isEmpty ? null : update.ownerTestNumber,
      'triage_enabled': update.triageEnabled,
      'auto_send_templates': update.autoSendTemplates,
      'status': accountExtra['status'] ?? 'connected',
      'access_token_hint': '...1234',
      'app_secret_set': true,
      'verify_token_set': true,
    };
    configured = true;
    return account();
  }

  @override
  Future<WaAccount> checkAccount() async {
    checks++;
    if (checkError != null) throw checkError!;
    return account();
  }

  @override
  Future<String> sendTest() async {
    tests++;
    if (testError != null) throw testError!;
    return '966500000009';
  }

  @override
  Future<void> disconnect() async {
    disconnects++;
    if (disconnectError != null) throw disconnectError!;
    configured = false;
    accountExtra = {};
  }

  @override
  Future<Uint8List> mediaBytes(String messageId) async {
    mediaIds.add(messageId);
    if (mediaError != null) throw mediaError!;
    return media;
  }

  @override
  Future<bool> saveMedia(String fileName, Uint8List bytes) async {
    savedFiles.add(fileName);
    return true;
  }

  @override
  Future<List<WaConversation>> conversations(
      {int limit = 30,
      String? before,
      String? query,
      WaFilter filter = WaFilter.all}) async {
    if (statusOnList == 403) {
      throw const ApiException(403, 'Workspace permission denied');
    }
    if (statusOnList != 200) {
      throw ApiException(statusOnList, 'The database is not reachable.');
    }
    filters.add(filter.api);
    return conversationRows
        .where((row) =>
            filter != WaFilter.unread || (row['unread_count'] as int) > 0)
        .where(
            (row) => query == null || query.isEmpty || '$row'.contains(query))
        .map((row) => WaConversation.fromJson(JsonReader(row)))
        .toList();
  }

  @override
  Future<WaThread> thread(String id, {int limit = 30, String? before}) async =>
      WaThread.fromJson(JsonReader({
        'conversation': conversationRows.firstWhere((row) => row['id'] == id),
        'messages': messages,
        'has_more': false,
      }));

  @override
  Future<WaConversation> markRead(String id) async =>
      WaConversation.fromJson(JsonReader({
        ...conversationRows.firstWhere((row) => row['id'] == id),
        'unread_count': 0
      }));

  @override
  Future<WaSuggestion> draft(String id) async =>
      WaSuggestion.fromJson(JsonReader({
        'text': 'Thank you for asking. [Tyre size] costs [price].',
        'category': 'price_stock',
        'summary': 'Asks the price of 205/55R16.',
        'source': 'model',
        'provider': 'anthropic',
        'model': 'claude-haiku-5-5',
        'untrusted_messages': 1,
      }));

  @override
  Future<WaSendResult> sendText(String id, String text) async {
    sends.add(text);
    final out = messageJson('out',
        inbound: false,
        body: text,
        status: 'awaiting_approval',
        approval: approvalJson());
    messages = [...messages, out];
    return WaSendResult.fromJson(JsonReader({
      'outcome': 'awaiting_approval',
      'message': out,
      'detail': 'Waiting.'
    }));
  }

  @override
  Future<String?> approve(WorkspaceApproval approval) async {
    approvals.add(approval.payloadHash);
    messages = [
      for (final m in messages)
        m['id'] == 'out' ? {...m, 'status': 'sent', 'approval': null} : m
    ];
    return 'Sent the WhatsApp reply.';
  }

  @override
  Future<void> reject(WorkspaceApproval approval) async {
    rejects.add(approval.id);
    messages = [
      for (final m in messages)
        m['id'] == 'out'
            ? {
                ...m,
                'status': 'not_sent',
                'approval': null,
                'error': 'Not sent: the approval was rejected.'
              }
            : m
    ];
  }

  @override
  Future<String?> currentRole() async => role;
}

Future<void> pumpInbox(WidgetTester tester, WhatsAppController controller,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
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
      child: child!,
    ),
    home: WhatsAppScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('models', () {
    test('a conversation reads its window, label and suggestion', () {
      final open =
          WaConversation.fromJson(JsonReader(conversationJson('a', name: ' ')));
      expect(open.label, '+966500000001');
      expect(
          open.windowLeft(DateTime.now())!.inHours, inInclusiveRange(22, 23));
      final closed = WaConversation.fromJson(
          JsonReader(conversationJson('b', open: false)));
      expect(closed.windowLeft(DateTime.now()), isNull);
      expect(closed.read().unread, 0);
    });

    test(
        'a message carries its pending approval; merging keeps order without duplicates',
        () {
      final waiting = WaMessage.fromJson(JsonReader(messageJson('x',
          inbound: false,
          status: 'awaiting_approval',
          approval: approvalJson())));
      expect(waiting.approval!.payloadHash, '5d' * 32);
      expect(approvable(waiting.approval!, DateTime.now()), isTrue);
      final a = WaMessage.fromJson(JsonReader({
        ...messageJson('a'),
        'timestamp': _iso(const Duration(minutes: 9))
      }));
      final b = WaMessage.fromJson(JsonReader(messageJson('b')));
      expect(mergeMessages([b], [a, b]).map((m) => m.id), ['a', 'b']);
    });
  });

  group('controller', () {
    test('loads, filters, searches and reports each state', () async {
      final fake = FakeWhatsApp();
      final controller = WhatsAppController(fake);
      await controller.load();
      expect(controller.phase, WaPhase.ready);
      expect(controller.items, hasLength(2));
      expect(controller.owner, isTrue);
      await controller.setFilter(WaFilter.unread);
      expect(controller.items.map((c) => c.id), ['wacv_1']);
      expect(fake.filters.last, 'unread');
      await controller.search('nothing like this');
      expect(controller.items, isEmpty);

      fake.configured = false;
      await controller.load();
      expect(controller.phase, WaPhase.notConnected);
      fake
        ..configured = true
        ..statusOnList = 403;
      await controller.load();
      expect(controller.phase, WaPhase.denied);
      fake.statusOnList = 503;
      await controller.load();
      expect(controller.phase, WaPhase.error);
      expect(controller.error, contains('not reachable'));
    });

    test('a reply waits for approval and an owner approves it with the hash',
        () async {
      final fake = FakeWhatsApp();
      final thread = WhatsAppThreadController(fake, 'wacv_1', owner: true);
      await thread.load(first: true);
      expect(thread.conversation!.unread, 0);
      await thread.draft();
      expect(thread.suggestion!.fromModel, isTrue);
      expect(await thread.send('  Yes, in stock  '), isTrue);
      expect(fake.sends, ['Yes, in stock']);
      expect(thread.noticeKey, 'waAwaitingOwner');
      final waiting = thread.messages.last;
      await thread.decide(waiting, approve: true);
      expect(fake.approvals, ['5d' * 32]);
      expect(thread.messages.last.status, 'sent');
    });

    test('a member is told an owner approves; a rejection shows as not sent',
        () async {
      final fake = FakeWhatsApp();
      final thread = WhatsAppThreadController(fake, 'wacv_1', owner: false);
      await thread.load(first: true);
      await thread.send('Hello');
      expect(thread.noticeKey, 'waAwaitingMember');
      await thread.decide(thread.messages.last, approve: false);
      expect(fake.rejects, ['approval_wa']);
      expect(thread.messages.last.status, 'not_sent');
    });
  });

  group('screens', () {
    testWidgets('list, thread, draft, send for approval and approve',
        (tester) async {
      final fake = FakeWhatsApp();
      await pumpInbox(tester, WhatsAppController(fake));
      expect(find.text('Ahmed'), findsOneWidget);
      expect(find.text('Templates only'), findsOneWidget);
      await tester.tap(find.text('Ahmed'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Free-form replies for'), findsOneWidget);
      await tester.tap(find.text('Suggest a reply'));
      await tester.pumpAndSettle();
      expect(find.textContaining('read as untrusted text'), findsOneWidget);
      await tester.tap(find.text('Use this draft'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Send for approval'), 200,
          scrollable: find.byType(Scrollable).last);
      await tester.ensureVisible(find.text('Send for approval'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Send for approval'));
      await tester.pumpAndSettle();
      expect(fake.sends.single, startsWith('Thank you for asking.'));
      await tester.scrollUntilVisible(find.text('Approve & send'), 200,
          scrollable: find.byType(Scrollable).last);
      expect(find.textContaining('graph.facebook.com'), findsOneWidget);
      await tester.ensureVisible(find.text('Approve & send'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Approve & send'));
      await tester.pumpAndSettle();
      expect(fake.approvals, ['5d' * 32]);
    });

    testWidgets('a closed window explains that only templates can be sent',
        (tester) async {
      await pumpInbox(tester, WhatsAppController(FakeWhatsApp()));
      await tester.tap(find.text('Sara Trading'));
      await tester.pumpAndSettle();
      expect(
          find.text('24-hour window closed: templates only'), findsOneWidget);
      await tester.scrollUntilVisible(
          find.textContaining('only approved templates'), 200,
          scrollable: find.byType(Scrollable).last);
      expect(find.text('Send for approval'), findsNothing);
    });

    testWidgets('not connected, denied and error states say what to do',
        (tester) async {
      final fake = FakeWhatsApp()..configured = false;
      final controller = WhatsAppController(fake);
      await pumpInbox(tester, controller);
      expect(find.text('WhatsApp is not connected yet'), findsOneWidget);
      fake
        ..configured = true
        ..statusOnList = 403;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('WhatsApp is for owners and members'), findsOneWidget);
      fake.statusOnList = 503;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('The database is not reachable.'), findsOneWidget);
      fake.statusOnList = 200;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Ahmed'), findsOneWidget);
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        testWidgets('lays out at $width px, 200% text, ${locale.languageCode}',
            (tester) async {
          final fake = FakeWhatsApp()
            ..messages = [
              messageJson('m1'),
              messageJson('out',
                  inbound: false,
                  body: 'نعم متوفر',
                  status: 'awaiting_approval',
                  approval: approvalJson()),
            ];
          await pumpInbox(tester, WhatsAppController(fake),
              size: Size(width, 900), textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
          await tester.tap(find.text('Ahmed'));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          if (locale.languageCode == 'ar') {
            expect(find.text('الرد المقترح'), findsOneWidget);
            expect(Directionality.of(tester.element(find.text('الرد المقترح'))),
                TextDirection.rtl);
          }
        });
      }
    }
  });
}
