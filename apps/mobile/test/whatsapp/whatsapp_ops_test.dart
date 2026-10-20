// WhatsApp phone parity (docs/whatsapp.md#templates-and-setup): Templates,
// template sending from a thread, Setup and attachments. Pure helpers, the
// repository's request shapes, the bytes-capable download, the controllers
// against a fake repository, and the screens at 320 to 1440 px with 200
// percent text, in English and Arabic (right to left).
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/whatsapp/whatsapp.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/whatsapp_ops_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'whatsapp_test.dart' show FakeWhatsApp, messageJson;

final _png = base64Decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==');

Map<String, Object?> template(String name,
        {String language = 'en',
        String status = 'APPROVED',
        String category = 'UTILITY',
        String body = 'Hi {{1}}, your order {{2}} is ready.',
        int params = 2}) =>
    {
      'name': name,
      'language': language,
      'status': status,
      'category': category,
      'body': body,
      'parameter_count': params,
      'header': null,
      'footer': null,
      'buttons': <String>[],
      'auto_send': false,
    };

FakeWhatsApp connected({String role = 'owner'}) => FakeWhatsApp()
  ..role = role
  ..accountExtra = {
    'phone_number_id': '123456789012345',
    'business_account_id': '223456789012345',
    'status': 'connected',
    'verified_name': 'Anum Tyres',
    'quality_rating': 'GREEN',
    'access_token_hint': '...1234',
    'app_secret_set': true,
    'verify_token_set': true,
    'owner_test_number': '966500000009',
    'auto_send_templates': ['order_update'],
    'default_message_retention_days': 90,
    'triage_enabled': true,
  }
  ..templateRows = [
    template('order_update'),
    template('promo_ar',
        language: 'ar',
        status: 'PENDING',
        category: 'MARKETING',
        body: 'مرحبا {{1}}',
        params: 1),
    template('old_sale',
        status: 'REJECTED', category: 'MARKETING', body: 'Sale', params: 0),
  ];

Future<void> pump(WidgetTester tester, Widget home,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  // A fresh app per call: a second screen in one test starts from its own
  // state, with no sheet or route left over from the first.
  await tester.pumpWidget(MaterialApp(
    key: UniqueKey(),
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
    home: home,
  ));
  await tester.pumpAndSettle();
}

Future<void> reveal(WidgetTester tester, Finder finder) async {
  // Drag the page until the target can be hit (lazy lists build it late).
  for (var i = 0; i < 60 && finder.hitTestable().evaluate().isEmpty; i++) {
    await tester.drag(find.byType(Scrollable).first, const Offset(0, -240));
    await tester.pumpAndSettle();
  }
}

class _Recording implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/templates')) {
      return ApiResponse(statusCode: 200, body: {
        'templates': [template('order_update')],
        'fetched_at': DateTime.now().toUtc().toIso8601String(),
      });
    }
    if (path.endsWith('/account/test')) {
      return const ApiResponse(
          statusCode: 200,
          body: {'status': 'sent', 'wamid': 'w', 'to': '966500000009'});
    }
    if (request.method == 'DELETE') {
      return const ApiResponse(statusCode: 204);
    }
    if (path.endsWith('/send')) {
      return ApiResponse(statusCode: 200, body: {
        'outcome': 'sent',
        'message': messageJson('t1', inbound: false, status: 'sent'),
        'detail': 'Sent.',
      });
    }
    return const ApiResponse(
        statusCode: 200, body: {'configured': true, 'can_manage': true});
  }
}

Future<(AnumApiClient, _Recording)> _client() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
    accessToken: 'anum_local_example',
    tokenType: 'bearer',
    expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
    context: const TenantContext(
        tenantId: 'tenant_local',
        workspaceId: 'workspace_foundation',
        userId: 'user_owner',
        roles: ['owner']),
  ));
  final transport = _Recording();
  return (
    AnumApiClient(
        baseUri: Uri.parse('https://api.example/'),
        transport: transport,
        sessions: sessions),
    transport
  );
}

void main() {
  group('helpers', () {
    test('a template fills numbered and named parameters, keeping gaps', () {
      expect(fillTemplate('Hi {{1}}, order {{2}}.', ['Sara', '77']),
          'Hi Sara, order 77.');
      expect(fillTemplate('Hi {{1}}, order {{ 2 }}.', ['Sara']),
          'Hi Sara, order {{ 2 }}.');
      expect(fillTemplate('{{name}} and {{city}}', ['A', 'B']), 'A and B');
      expect(fillTemplate('No parameters', []), 'No parameters');
    });

    test('the account update omits empty secrets and never prints them', () {
      final update = WaAccountUpdate(
          phoneNumberId: ' 123456789012345 ',
          businessAccountId: '223456789012345',
          ownerTestNumber: '+966 50 000 0009',
          autoSendTemplates: ['b', 'a', 'a'],
          accessToken: 'example-token-value');
      final body = update.body();
      expect(body['phone_number_id'], '123456789012345');
      expect(body['owner_test_number'], '966500000009');
      expect(body['auto_send_templates'], ['a', 'b']);
      expect(body['access_token'], 'example-token-value');
      expect(body.containsKey('app_secret'), isFalse);
      expect(body.containsKey('verify_token'), isFalse);
      expect(body.containsKey('message_retention_days'), isFalse);
      expect(update.toString(), isNot(contains('example-token-value')));
      update.clearSecrets();
      expect(update.accessToken, isEmpty);
    });

    test('a saved account is resent whole, with only the list changed', () {
      final account = WaAccount.fromJson(JsonReader({
        'configured': true,
        'can_manage': true,
        'phone_number_id': '123456789012345',
        'business_account_id': '223456789012345',
        'owner_test_number': '966500000009',
        'triage_enabled': false,
        'message_retention_days': 30,
        'auto_send_templates': ['x'],
      }));
      final body =
          WaAccountUpdate.of(account, autoSendTemplates: ['x', 'y']).body();
      expect(body['triage_enabled'], false);
      expect(body['message_retention_days'], 30);
      expect(body['auto_send_templates'], ['x', 'y']);
      expect(body.containsKey('access_token'), isFalse);
    });

    test('verify tokens are random and URL safe; the webhook URL joins', () {
      final token = randomVerifyToken();
      expect(token, hasLength(32));
      expect(RegExp(r'^[A-Za-z0-9_-]{32}$').hasMatch(token), isTrue);
      expect(randomVerifyToken(), isNot(token));
      expect(
          webhookUrl(
              Uri.parse('https://api.example/'), '/api/v1/whatsapp/webhook'),
          'https://api.example/api/v1/whatsapp/webhook');
    });

    test('attachments are named from the sender, else the kind and type', () {
      WaMessage m(Map<String, Object?>? media, String kind) =>
          WaMessage.fromJson(
              JsonReader(messageJson('m9', kind: kind, media: media)));
      expect(
          m({'id': 'x', 'filename': 'invoice.pdf'}, 'document').media!.filename,
          'invoice.pdf');
      expect(
          mediaFileName(m({'id': 'x', 'filename': 'invoice.pdf'}, 'document')),
          'invoice.pdf');
      expect(mediaFileName(m({'id': 'x', 'mime_type': 'image/png'}, 'image')),
          'image-m9.png');
      expect(mediaFileName(m({'id': 'x'}, 'audio')), 'audio-m9');
      expect(m({'id': 'x', 'mime_type': 'image/png'}, 'image').media!.isImage,
          isTrue);
      expect(
          m({'id': 'x', 'mime_type': 'application/pdf'}, 'document')
              .media!
              .isImage,
          isFalse);
    });

    test('Arabic text picks right to left', () {
      expect(waTextDirection('مرحبا Sara'), TextDirection.rtl);
      expect(waTextDirection('Hello مرحبا'), TextDirection.ltr);
      expect(waTextDirection('123'), isNull);
    });

    test('every English key has an Arabic key and no dashes', () {
      expect(waOpsAr.keys.toSet(), waOpsEn.keys.toSet());
      for (final value in [...waOpsEn.values, ...waOpsAr.values]) {
        expect(value.contains(String.fromCharCode(0x2014)), isFalse);
        expect(value.contains(String.fromCharCode(0x2013)), isFalse);
      }
      for (final key in waOpsEn.keys) {
        expect(key, isNot(endsWith('.one')));
        expect(key, isNot(endsWith('.other')));
        final placeholders = RegExp(r'\{(\w+)\}');
        expect(
            placeholders
                .allMatches(waOpsAr[key]!)
                .map((m) => m.group(1))
                .toSet(),
            placeholders
                .allMatches(waOpsEn[key]!)
                .map((m) => m.group(1))
                .toSet(),
            reason: key);
      }
    });
  });

  group('repository', () {
    test('requests match the API routes and bodies', () async {
      final (api, transport) = await _client();
      final downloads = <String>[];
      final repo = ApiWhatsAppRepository(api,
          download: (path) async {
            downloads.add(path);
            return _png;
          },
          save: (name, bytes) async => true);

      await repo.templates();
      await repo.templates(refresh: true);
      expect(transport.requests[0].uri.toString(),
          'https://api.example/api/v1/whatsapp/templates');
      expect(transport.requests[1].uri.toString(),
          'https://api.example/api/v1/whatsapp/templates?refresh=true');

      final tpl = WaTemplate.fromJson(JsonReader(template('order_update')));
      await repo.sendTemplate('wacv_1', tpl, [' Sara ', '77']);
      final send = transport.requests[2];
      expect(send.method, 'POST');
      expect(send.uri.path, '/api/v1/whatsapp/conversations/wacv_1/send');
      expect(send.body, {
        'template': {
          'name': 'order_update',
          'language': 'en',
          'parameters': ['Sara', '77'],
        },
      });

      final update = WaAccountUpdate(
          phoneNumberId: '123456789012345',
          businessAccountId: '223456789012345',
          appSecret: 'example-secret');
      await repo.saveAccount(update);
      final put = transport.requests[3];
      expect(put.method, 'PUT');
      expect(put.uri.path, '/api/v1/whatsapp/account');
      expect(put.body!['app_secret'], 'example-secret');

      await repo.checkAccount();
      expect(transport.requests[4].uri.path, '/api/v1/whatsapp/account/check');
      expect(await repo.sendTest(), '966500000009');
      expect(transport.requests[5].uri.path, '/api/v1/whatsapp/account/test');
      await repo.disconnect();
      expect(transport.requests[6].method, 'DELETE');

      expect(await repo.mediaBytes('wamsg 1'), _png);
      expect(downloads.single, '/api/v1/whatsapp/messages/wamsg%201/media');
    });

    test('without file access, media says so instead of failing silently',
        () async {
      final (api, _) = await _client();
      final repo = ApiWhatsAppRepository(api);
      expect(() => repo.mediaBytes('m'), throwsUnsupportedError);
      expect(() => repo.saveMedia('a', _png), throwsUnsupportedError);
    });
  });

  group('the bytes download', () {
    Future<HttpMailFileTransfer> transfer(http.Client client) async {
      final sessions = MemorySessionStore();
      await sessions.write(LocalSession(
        accessToken: 'anum_local_example',
        tokenType: 'bearer',
        expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
        context: const TenantContext(
            tenantId: 'tenant_local',
            workspaceId: 'workspace_foundation',
            userId: 'user_owner',
            roles: ['owner']),
      ));
      return HttpMailFileTransfer(
          baseUri: Uri.parse('https://api.example/'),
          sessions: sessions,
          files: HttpWorkspaceFileTransfer(
              baseUri: Uri.parse('https://api.example/'), sessions: sessions),
          client: client);
    }

    test('returns the raw bytes with the session headers', () async {
      late http.BaseRequest seen;
      final t = await transfer(MockClient((request) async {
        seen = request;
        return http.Response.bytes(_png, 200,
            headers: {'content-type': 'image/png'});
      }));
      expect(await t.download('/api/v1/whatsapp/messages/m1/media'), _png);
      expect(seen.url.toString(),
          'https://api.example/api/v1/whatsapp/messages/m1/media');
      expect(seen.headers['authorization'], 'Bearer anum_local_example');
    });

    test('404, 413 and 502 carry the server sentence', () async {
      for (final (code, detail) in [
        (404, 'This message has no media.'),
        (413, 'The file could not be downloaded. Too large.'),
        (502, 'The file could not be downloaded. Meta did not answer.'),
      ]) {
        final t = await transfer(MockClient((_) async => http.Response(
            jsonEncode({'detail': detail}), code,
            headers: {'content-type': 'application/json'})));
        await expectLater(
            t.download('/api/v1/whatsapp/messages/m1/media'),
            throwsA(isA<ApiException>()
                .having((e) => e.statusCode, 'status', code)
                .having((e) => e.message, 'message', detail)));
      }
    });

    test('a proxy page that is not JSON still fails with a sentence', () async {
      final t = await transfer(MockClient(
          (_) async => http.Response('<html>bad gateway</html>', 502)));
      await expectLater(
          t.download('/api/v1/whatsapp/messages/m1/media'),
          throwsA(isA<ApiException>()
              .having((e) => e.statusCode, 'status', 502)
              .having((e) => e.message, 'message', 'The download failed.')));
    });
  });

  group('templates controller', () {
    test('loads, searches, pages, refreshes from Meta and reports states',
        () async {
      final fake = connected();
      fake.templateRows = [
        for (var i = 0; i < 45; i++) template('tpl_$i', body: 'Body $i'),
        template('special_offer', body: 'Big sale', params: 0),
      ];
      final c = WaTemplatesController(fake);
      await c.load();
      expect(c.phase, WaPhase.ready);
      expect(c.canManage, isTrue);
      expect(c.visible, hasLength(WaTemplatesController.pageSize));
      expect(c.hasMoreToShow, isTrue);
      c.showMore();
      expect(c.visible, hasLength(40));
      c.search('SPECIAL');
      expect(c.visible.single.name, 'special_offer');
      expect(c.shown, WaTemplatesController.pageSize);
      c.search('big sale');
      expect(c.visible.single.name, 'special_offer');
      c.search('zzz');
      expect(c.visible, isEmpty);

      await c.load(refresh: true);
      expect(fake.refreshes, [false, true]);
      expect(c.noticeKey, 'waOps.templates.refreshed');

      fake.templatesError = const ApiException(403, 'Needs WhatsApp access.');
      await c.load();
      expect(c.phase, WaPhase.denied);
      expect(c.error, 'Needs WhatsApp access.');
      fake.templatesError =
          const ApiException(502, 'Templates could not be loaded from Meta.');
      await c.load();
      expect(c.phase, WaPhase.error);
      expect(c.error, contains('from Meta'));
      fake
        ..templatesError = null
        ..configured = false;
      await c.load();
      expect(c.phase, WaPhase.notConnected);
    });

    test('auto-send saves the whole account and mirrors the saved list',
        () async {
      final fake = connected();
      final c = WaTemplatesController(fake);
      await c.load();
      final promo = c.templates.firstWhere((t) => t.name == 'old_sale');
      await c.setAutoSend(promo, true);
      final body = fake.savedBodies.single;
      expect(body['auto_send_templates'], ['old_sale', 'order_update']);
      expect(body['phone_number_id'], '123456789012345');
      expect(body['business_account_id'], '223456789012345');
      expect(body['owner_test_number'], '966500000009');
      expect(body.containsKey('access_token'), isFalse);
      expect(
          c.templates.firstWhere((t) => t.name == 'old_sale').autoSend, isTrue);
      expect(c.noticeKey, 'waOps.templates.autoOn');
      final order = c.templates.firstWhere((t) => t.name == 'order_update');
      await c.setAutoSend(order, false);
      expect(fake.savedBodies.last['auto_send_templates'], ['old_sale']);
    });

    test('a refused change shows the server text and leaves the switch',
        () async {
      final fake = connected();
      final c = WaTemplatesController(fake);
      await c.load();
      fake.saveError =
          const ApiException(403, 'Only workspace owners can change WhatsApp.');
      final order = c.templates.firstWhere((t) => t.name == 'order_update');
      await c.setAutoSend(order, false);
      expect(c.notice, 'Only workspace owners can change WhatsApp.');
      expect(c.noticeFailed, isTrue);
      expect(c.templates.firstWhere((t) => t.name == 'order_update').autoSend,
          isTrue);
      expect(c.busy, isNull);
    });
  });

  group('setup controller', () {
    WaAccountUpdate update({String token = '', String verify = ''}) =>
        WaAccountUpdate(
            phoneNumberId: '123456789012345',
            businessAccountId: '223456789012345',
            accessToken: token,
            verifyToken: verify);

    test('secrets are cleared after a save, also when it fails', () async {
      final fake = connected();
      final c = WaSetupController(fake);
      await c.load();
      final ok = update(token: 'example-token-value');
      expect(await c.save(ok), isTrue);
      expect(fake.savedBodies.single['access_token'], 'example-token-value');
      expect(ok.accessToken, isEmpty);
      expect(c.noticeKey, 'waOps.setup.saved');

      fake.saveError = const ApiException(403, 'Only owners can do this.');
      final refused = update(token: 'example-token-value');
      expect(await c.save(refused), isFalse);
      expect(refused.accessToken, isEmpty);
      expect(c.notice, 'Only owners can do this.');
      expect(c.noticeFailed, isTrue);
    });

    test('a connection Meta rejects is reported as a problem', () async {
      final fake = connected()
        ..accountExtra = {
          ...connected().accountExtra,
          'status': 'error',
          'last_error': 'Token expired.',
        };
      final c = WaSetupController(fake);
      await c.load();
      await c.save(update());
      expect(c.noticeKey, 'waOps.setup.savedProblem');
      expect(c.noticeValues['message'], 'Token expired.');
      expect(c.noticeFailed, isTrue);
    });

    test('form problems are found before anything is sent', () async {
      final fake = connected()
        ..configured = false
        ..accountExtra = {};
      final c = WaSetupController(fake);
      await c.load();
      expect(
          c.validate(WaAccountUpdate(
              phoneNumberId: 'abc', businessAccountId: '223456789012345')),
          'waOps.err.phoneId');
      expect(
          c.validate(WaAccountUpdate(
              phoneNumberId: '123456789012345', businessAccountId: '12')),
          'waOps.err.wabaId');
      expect(c.validate(update(token: 'example-token-value')),
          'waOps.err.secrets');
      expect(
          c.validate(WaAccountUpdate(
              phoneNumberId: '123456789012345',
              businessAccountId: '223456789012345',
              accessToken: 'a',
              appSecret: 'b',
              verifyToken: 'short')),
          'waOps.err.verifyToken');
      expect(
          c.validate(WaAccountUpdate(
              phoneNumberId: '123456789012345',
              businessAccountId: '223456789012345',
              accessToken: 'a',
              appSecret: 'b',
              verifyToken: 'a' * 16,
              ownerTestNumber: 'call me')),
          'waOps.err.testNumber');
      expect(await c.save(update()), isFalse);
      expect(fake.savedBodies, isEmpty);
      expect(c.noticeKey, 'waOps.err.secrets');
    });

    test('check, test message and disconnect report what happened', () async {
      final fake = connected();
      final c = WaSetupController(fake);
      await c.load();
      await c.check();
      expect(c.noticeKey, 'waOps.setup.checkOk');
      fake.checkError = const ApiException(409, 'WhatsApp is not connected.');
      await c.check();
      expect(c.notice, 'WhatsApp is not connected.');
      await c.sendTest();
      expect(c.noticeKey, 'waOps.setup.testSent');
      expect(c.noticeValues['number'], '966500000009');
      fake.testError = const ApiException(
          502, 'The test message was not sent. Meta refused it.');
      await c.sendTest();
      expect(c.notice, contains('Meta refused it'));
      expect(await c.disconnect(), isTrue);
      expect(c.account!.configured, isFalse);
      fake.disconnectError = const ApiException(403, 'Owners only.');
      expect(await c.disconnect(), isFalse);
      expect(c.notice, 'Owners only.');
    });
  });

  group('thread controller', () {
    test('a template shows each outcome and the server messages', () async {
      final fake = connected();
      final thread = WhatsAppThreadController(fake, 'wacv_1', owner: true);
      await thread.load(first: true);
      final tpl = WaTemplate.fromJson(JsonReader(template('order_update')));

      expect(await thread.sendTemplate(tpl, ['Sara', '77']), isTrue);
      expect(fake.templateSends.single['parameters'], ['Sara', '77']);
      expect(thread.noticeKey, 'waAwaitingOwner');
      expect(thread.messages.last.body, 'Hi Sara, your order 77 is ready.');
      expect(thread.messages.last.approval, isNotNull);

      fake.templateOutcome = 'sent';
      expect(await thread.sendTemplate(tpl, ['Sara', '77']), isTrue);
      expect(thread.noticeKey, 'waSent');

      fake.templateOutcome = 'failed';
      expect(await thread.sendTemplate(tpl, ['Sara', '77']), isFalse);
      expect(thread.notice, 'Meta refused it.');
      expect(thread.noticeFailed, isTrue);

      for (final (code, text) in [
        (409, 'WhatsApp is not usable: WHATSAPP_APP_SECRET'),
        (422, 'The template order_update needs 2 parameter(s); 1 given.'),
        (502, 'Templates could not be loaded from Meta. Timeout.'),
      ]) {
        fake.sendTemplateError = ApiException(code, text);
        expect(await thread.sendTemplate(tpl, ['x']), isFalse);
        expect(thread.notice, text);
        expect(thread.busy, isNull);
      }
    });

    test('attachments fetch bytes, save them and say why they failed',
        () async {
      final fake = connected()
        ..messages = [
          messageJson('img1', kind: 'image', body: '', media: {
            'id': 'media_1',
            'mime_type': 'image/png',
            'caption': 'Photo'
          }),
        ];
      final thread = WhatsAppThreadController(fake, 'wacv_1', owner: false);
      await thread.load(first: true);
      final message = thread.messages.single;
      expect(await thread.fetchMedia(message), isNotNull);
      expect(fake.mediaIds, ['img1']);
      expect(await thread.saveMedia(message, _png), isTrue);
      expect(fake.savedFiles, ['image-img1.png']);
      expect(thread.noticeKey, 'waOps.mediaSaved');
      for (final (code, text) in [
        (404, 'This message has no media.'),
        (413, 'The file could not be downloaded. Too large.'),
        (502, 'The file could not be downloaded. Meta did not answer.'),
      ]) {
        fake.mediaError = ApiException(code, text);
        expect(await thread.fetchMedia(message), isNull);
        expect(thread.notice, text);
        expect(thread.noticeFailed, isTrue);
      }
    });
  });

  group('templates screen', () {
    testWidgets('an owner sees status, category, language and the switch',
        (tester) async {
      final fake = connected();
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)));
      expect(find.text('order_update'), findsOneWidget);
      expect(find.text('approved'), findsOneWidget);
      expect(find.text('utility'), findsOneWidget);
      expect(find.text('2 parameters'), findsOneWidget);
      expect(find.textContaining('Hi {{1}}'), findsOneWidget);
      final auto = find.byKey(const Key('wa-auto-order_update|en'));
      expect(tester.widget<SwitchListTile>(auto).value, isTrue);
      // Only approved templates have the switch.
      expect(find.byKey(const Key('wa-auto-old_sale|en')), findsNothing);
      expect(find.byKey(const Key('wa-auto-promo_ar|ar')), findsNothing);

      await tester.tap(auto);
      await tester.pumpAndSettle();
      expect(fake.savedBodies.single['auto_send_templates'], isEmpty);
      expect(find.text('order_update now waits for approval.'), findsOneWidget);
    });

    testWidgets('a refused switch shows the server text', (tester) async {
      final fake = connected()
        ..saveError =
            const ApiException(403, 'Only workspace owners can change this.');
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)));
      await tester.tap(find.byKey(const Key('wa-auto-order_update|en')));
      await tester.pumpAndSettle();
      expect(
          find.text('Only workspace owners can change this.'), findsOneWidget);
    });

    testWidgets('a member reads the list without the switch', (tester) async {
      final fake = connected(role: 'member');
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)));
      expect(find.text('order_update'), findsOneWidget);
      expect(find.byType(SwitchListTile), findsNothing);
      expect(find.text('Auto-send'), findsOneWidget);
    });

    testWidgets('search narrows the list and offers a way back',
        (tester) async {
      final fake = connected();
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)));
      await tester.enterText(
          find.byKey(const Key('wa-templates-search')), 'nothing like this');
      await tester.pumpAndSettle();
      expect(find.text('No template matches'), findsOneWidget);
      await tester.tap(find.text('Show all conversations'));
      await tester.pumpAndSettle();
      expect(find.text('order_update'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('wa-templates-search')), 'promo');
      await tester.pumpAndSettle();
      expect(find.text('promo_ar'), findsOneWidget);
      expect(find.text('order_update'), findsNothing);
    });

    testWidgets('a long list pages and refresh asks Meta again',
        (tester) async {
      final fake = connected()
        ..templateRows = [
          for (var i = 0; i < 45; i++) template('tpl_$i', body: 'Body $i'),
        ];
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)));
      expect(find.text('tpl_30'), findsNothing);
      await reveal(tester, find.byKey(const Key('wa-templates-more')));
      expect(find.text('Show more (25 left)'), findsOneWidget);
      await tester.tap(find.byKey(const Key('wa-templates-more')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('wa-templates-refresh')));
      await tester.pumpAndSettle();
      expect(fake.refreshes, [false, true]);
      expect(find.text('Templates refreshed from Meta.'), findsOneWidget);
    });

    testWidgets('empty, denied, error and not connected each say what to do',
        (tester) async {
      final fake = connected()..templateRows = [];
      var setups = 0;
      await pump(
          tester,
          WhatsAppTemplatesScreen(
              controller: WaTemplatesController(fake),
              onOpenSetup: () => setups++));
      expect(find.text('No templates yet'), findsOneWidget);
      await tester.tap(find.text('Refresh from Meta').last);
      await tester.pumpAndSettle();
      expect(fake.refreshes, [false, true]);

      final denied = connected()
        ..templatesError = const ApiException(403, 'Not allowed for guests.');
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(denied)));
      expect(find.text('Templates are for owners and members'), findsOneWidget);
      expect(find.text('Not allowed for guests.'), findsOneWidget);

      final broken = connected()
        ..templatesError = const ApiException(
            502, 'Templates could not be loaded from Meta. Timeout.');
      await pump(tester,
          WhatsAppTemplatesScreen(controller: WaTemplatesController(broken)));
      expect(find.text('Templates could not be loaded'), findsOneWidget);
      expect(find.textContaining('Timeout'), findsOneWidget);
      broken.templatesError = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('order_update'), findsOneWidget);

      final none = connected()..configured = false;
      await pump(
          tester,
          WhatsAppTemplatesScreen(
              controller: WaTemplatesController(none),
              onOpenSetup: () => setups++));
      await tester.tap(find.text('Open Setup'));
      expect(setups, 1);
    });
  });

  group('setup screen', () {
    testWidgets('shows the status and the webhook URL', (tester) async {
      final fake = connected()
        ..accountExtra = {
          ...connected().accountExtra,
          'deployment_problems': ['WHATSAPP_APP_SECRET'],
          'last_webhook_at': DateTime.now().toUtc().toIso8601String(),
        };
      await pump(
          tester,
          WhatsAppSetupScreen(
              controller: WaSetupController(fake),
              apiBase: Uri.parse('https://api.example/')));
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('Anum Tyres'), findsOneWidget);
      expect(find.text('green'), findsOneWidget);
      expect(find.textContaining('WHATSAPP_APP_SECRET'), findsOneWidget);
      expect(find.text('https://api.example/api/v1/whatsapp/webhook'),
          findsOneWidget);
    });

    testWidgets('secret fields are obscured, kept off suggestions and cleared',
        (tester) async {
      final fake = connected();
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      for (final key in [
        'wa-setup-token',
        'wa-setup-secret',
        'wa-setup-verify'
      ]) {
        await reveal(tester, find.byKey(Key(key)));
        final field = tester.widget<TextField>(find.byKey(Key(key)));
        expect(field.obscureText, isTrue, reason: key);
        expect(field.autocorrect, isFalse, reason: key);
        expect(field.enableSuggestions, isFalse, reason: key);
        expect(field.enableIMEPersonalizedLearning, isFalse, reason: key);
        expect(field.autofillHints, isEmpty, reason: key);
      }
      await tester.enterText(
          find.byKey(const Key('wa-setup-token')), 'example-token-value');
      await tester.enterText(
          find.byKey(const Key('wa-setup-secret')), 'example-secret-value');
      await reveal(tester, find.byKey(const Key('wa-setup-save')));
      await tester.tap(find.byKey(const Key('wa-setup-save')));
      await tester.pumpAndSettle();
      final body = fake.savedBodies.single;
      expect(body['access_token'], 'example-token-value');
      expect(body['app_secret'], 'example-secret-value');
      expect(body.containsKey('verify_token'), isFalse);
      expect(body['phone_number_id'], '123456789012345');
      for (final key in ['wa-setup-token', 'wa-setup-secret']) {
        expect(tester.widget<TextField>(find.byKey(Key(key))).controller!.text,
            isEmpty);
      }
      expect(find.textContaining('Anum Tyres is connected'), findsOneWidget);
      expect(find.textContaining('example-token-value'), findsNothing);
    });

    testWidgets('a refused save shows the server text and still clears secrets',
        (tester) async {
      final fake = connected()
        ..saveError = const ApiException(
            409, 'This phone number id is already connected elsewhere.');
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      await reveal(tester, find.byKey(const Key('wa-setup-token')));
      await tester.enterText(
          find.byKey(const Key('wa-setup-token')), 'example-token-value');
      await reveal(tester, find.byKey(const Key('wa-setup-save')));
      await tester.tap(find.byKey(const Key('wa-setup-save')));
      await tester.pumpAndSettle();
      expect(find.text('This phone number id is already connected elsewhere.'),
          findsOneWidget);
      expect(
          tester
              .widget<TextField>(find.byKey(const Key('wa-setup-token')))
              .controller!
              .text,
          isEmpty);
    });

    testWidgets('a form problem keeps what was typed and sends nothing',
        (tester) async {
      final fake = connected();
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      await tester.enterText(
          find.byKey(const Key('wa-setup-phone')), 'not a number');
      await reveal(tester, find.byKey(const Key('wa-setup-save')));
      await tester.tap(find.byKey(const Key('wa-setup-save')));
      await tester.pumpAndSettle();
      expect(fake.savedBodies, isEmpty);
      expect(find.textContaining('5 to 20 digit'), findsWidgets);
    });

    testWidgets('check, test message and a confirmed disconnect',
        (tester) async {
      final fake = connected();
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      await tester.tap(find.byKey(const Key('wa-setup-check')));
      await tester.pumpAndSettle();
      expect(fake.checks, 1);
      expect(find.text('The connection works.'), findsOneWidget);
      await tester.tap(find.byKey(const Key('wa-setup-test')));
      await tester.pumpAndSettle();
      expect(find.text('Test message sent to 966500000009.'), findsOneWidget);

      await tester.tap(find.byKey(const Key('wa-setup-disconnect')));
      await tester.pumpAndSettle();
      expect(find.text('Disconnect WhatsApp?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(fake.disconnects, 0);
      await tester.tap(find.byKey(const Key('wa-setup-disconnect')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('wa-disconnect-confirm')));
      await tester.pumpAndSettle();
      expect(fake.disconnects, 1);
      expect(find.text('Not connected'), findsOneWidget);
      expect(find.text('Connect your number'), findsOneWidget);
    });

    testWidgets('the test message needs a saved test number', (tester) async {
      final fake = connected()
        ..accountExtra = {
          ...connected().accountExtra,
          'owner_test_number': null
        };
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      expect(
          tester
              .widget<OutlinedButton>(find.byKey(const Key('wa-setup-test')))
              .onPressed,
          isNull);
    });

    testWidgets('a member reads the status and cannot change it',
        (tester) async {
      final fake = connected(role: 'member');
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(fake)));
      expect(find.text('Connected'), findsOneWidget);
      expect(find.text('Only owners change the connection'), findsOneWidget);
      expect(find.byKey(const Key('wa-setup-save')), findsNothing);
      expect(find.byKey(const Key('wa-setup-check')), findsNothing);
      expect(find.byKey(const Key('wa-setup-token')), findsNothing);
    });

    testWidgets('denied and failed loads say why; a retry recovers',
        (tester) async {
      final denied = connected()
        ..accountError = const ApiException(403, 'No WhatsApp access here.');
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(denied)));
      expect(find.text('Setup is for owners and members'), findsOneWidget);
      expect(find.text('No WhatsApp access here.'), findsOneWidget);

      final broken = connected()
        ..accountError =
            const ApiException(503, 'The database is not reachable.');
      await pump(
          tester, WhatsAppSetupScreen(controller: WaSetupController(broken)));
      expect(find.text('Setup could not be loaded'), findsOneWidget);
      expect(find.text('The database is not reachable.'), findsOneWidget);
      broken.accountError = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Connected'), findsOneWidget);
    });
  });

  group('send a template from a thread', () {
    Future<void> openThread(WidgetTester tester, FakeWhatsApp fake,
        {bool owner = true,
        Size size = const Size(390, 844),
        double scale = 1,
        Locale locale = const Locale('en'),
        String id = 'wacv_1'}) async {
      await pump(
          tester,
          WhatsAppThreadScreen(
              controller: WhatsAppThreadController(fake, id, owner: owner),
              title: 'Ahmed'),
          size: size,
          textScale: scale,
          locale: locale);
    }

    Future<void> openSheet(WidgetTester tester) async {
      await reveal(tester, find.byKey(const Key('wa-send-template-open')));
      await tester.tap(find.byKey(const Key('wa-send-template-open')));
      await tester.pumpAndSettle();
    }

    Future<void> choose(WidgetTester tester, String label) async {
      await tester.tap(find.byKey(const Key('wa-send-template')));
      await tester.pumpAndSettle();
      await tester.tap(find.text(label).last);
      await tester.pumpAndSettle();
    }

    testWidgets('fills the parameters, previews and waits for approval',
        (tester) async {
      final fake = connected();
      await openThread(tester, fake);
      await openSheet(tester);
      // Only approved templates are offered.
      await tester.tap(find.byKey(const Key('wa-send-template')));
      await tester.pumpAndSettle();
      expect(find.text('old_sale (en)'), findsNothing);
      expect(find.text('promo_ar (ar)'), findsNothing);
      await tester.tap(find.textContaining('order_update (en)').last);
      await tester.pumpAndSettle();

      await tester.enterText(find.byKey(const Key('wa-send-param-0')), 'Sara');
      await tester.pumpAndSettle();
      expect(tester.widget<Text>(find.byKey(const Key('wa-send-preview'))).data,
          'Hi Sara, your order {{2}} is ready.');
      // An empty value is refused before anything is sent.
      await tester.tap(find.byKey(const Key('wa-send-submit')));
      await tester.pumpAndSettle();
      expect(find.text('Fill in every value before sending.'), findsOneWidget);
      expect(fake.templateSends, isEmpty);

      await tester.enterText(find.byKey(const Key('wa-send-param-1')), '77');
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('wa-send-submit')));
      await tester.pumpAndSettle();
      expect(fake.templateSends.single['parameters'], ['Sara', '77']);
      expect(find.byKey(const Key('wa-send-submit')), findsNothing);
      expect(find.text('Hi Sara, your order 77 is ready.'), findsOneWidget);
      expect(find.textContaining('Waiting for approval'), findsOneWidget);
    });

    testWidgets('an auto-send template says it goes out now', (tester) async {
      final fake = connected()..templateOutcome = 'sent';
      await openThread(tester, fake, id: 'wacv_2');
      await openSheet(tester);
      await choose(tester, 'order_update (en) · Auto-send');
      expect(find.textContaining('goes out at once'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('wa-send-param-0')), 'Sara');
      await tester.enterText(find.byKey(const Key('wa-send-param-1')), '77');
      await tester.tap(find.text('Send now'));
      await tester.pumpAndSettle();
      expect(fake.templateSends, hasLength(1));
      expect(find.text('Sent.'), findsOneWidget);
    });

    testWidgets('a refused send keeps the sheet open with the server text',
        (tester) async {
      final fake = connected()
        ..sendTemplateError = const ApiException(
            422, 'The template order_update needs 2 parameter(s); 1 given.');
      await openThread(tester, fake);
      await openSheet(tester);
      await choose(tester, 'order_update (en) · Auto-send');
      await tester.enterText(find.byKey(const Key('wa-send-param-0')), 'Sara');
      await tester.enterText(find.byKey(const Key('wa-send-param-1')), '77');
      await tester.tap(find.byKey(const Key('wa-send-submit')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('wa-send-problem')), findsOneWidget);
      expect(tester.widget<Text>(find.byKey(const Key('wa-send-problem'))).data,
          'The template order_update needs 2 parameter(s); 1 given.');
      expect(find.byKey(const Key('wa-send-submit')), findsOneWidget);
      fake.sendTemplateError = null;
      fake.templateOutcome = 'failed';
      await tester.tap(find.byKey(const Key('wa-send-submit')));
      await tester.pumpAndSettle();
      expect(find.text('Meta refused it.'), findsWidgets);
    });

    testWidgets('no approved template and a failed list both offer a next step',
        (tester) async {
      final fake = connected()
        ..templateRows = [template('old', status: 'PENDING')];
      await openThread(tester, fake, id: 'wacv_2');
      await openSheet(tester);
      expect(find.textContaining('no approved templates'), findsOneWidget);
      expect(find.text('Refresh from Meta'), findsOneWidget);

      final broken = connected()
        ..templatesError =
            const ApiException(502, 'Templates could not be loaded from Meta.');
      await openThread(tester, broken, id: 'wacv_2');
      await openSheet(tester);
      expect(find.textContaining('could not be loaded'), findsWidgets);
      broken.templatesError = null;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('wa-send-template')), findsOneWidget);
    });

    testWidgets('a closed window points to templates and free text stays',
        (tester) async {
      final fake = connected();
      await openThread(tester, fake, id: 'wacv_2');
      await reveal(tester, find.byKey(const Key('wa-send-template-open')));
      expect(find.textContaining('Send a template below'), findsOneWidget);
      expect(find.text('Send for approval'), findsNothing);
      // The open-window thread still has the text reply.
      await openThread(tester, fake);
      await reveal(tester, find.text('Send for approval'));
    });
  });

  group('attachments in a thread', () {
    FakeWhatsApp withMedia() => connected()
      ..messages = [
        messageJson('img1', kind: 'image', body: '', media: {
          'id': 'media_1',
          'mime_type': 'image/png',
          'caption': 'Tyre photo',
        }),
        messageJson('doc1', kind: 'document', body: '', media: {
          'id': 'media_2',
          'mime_type': 'application/pdf',
          'filename': 'invoice.pdf',
        }),
      ];

    Future<void> thread(WidgetTester tester, FakeWhatsApp fake) => pump(
        tester,
        WhatsAppThreadScreen(
            controller: WhatsAppThreadController(fake, 'wacv_1', owner: false),
            title: 'Ahmed'));

    testWidgets('an image opens in a viewer', (tester) async {
      final fake = withMedia()..media = Uint8List.fromList(_png);
      await thread(tester, fake);
      expect(find.text('Image'), findsOneWidget);
      await tester.tap(find.byKey(const Key('wa-media-open-img1')));
      await tester.pumpAndSettle();
      expect(fake.mediaIds, ['img1']);
      expect(find.byType(WhatsAppMediaViewer), findsOneWidget);
      expect(find.byType(Image), findsOneWidget);
      expect(find.text('Tyre photo'), findsWidgets);
      await tester.tap(find.byKey(const Key('wa-media-save')));
      await tester.pumpAndSettle();
      expect(fake.savedFiles, ['image-img1.png']);
    });

    testWidgets('a document goes to the platform save dialog', (tester) async {
      final fake = withMedia();
      await thread(tester, fake);
      await tester.tap(find.byKey(const Key('wa-media-open-doc1')));
      await tester.pumpAndSettle();
      expect(fake.mediaIds, ['doc1']);
      expect(fake.savedFiles, ['invoice.pdf']);
      expect(find.byType(WhatsAppMediaViewer), findsNothing);
      expect(find.text('Attachment saved.'), findsOneWidget);
    });

    testWidgets('404, 413 and 502 show the server sentence in the thread',
        (tester) async {
      final fake = withMedia();
      await thread(tester, fake);
      for (final (code, text) in [
        (404, 'This message has no media.'),
        (413, 'The file could not be downloaded. Too large.'),
        (502, 'The file could not be downloaded. Meta did not answer.'),
      ]) {
        fake.mediaError = ApiException(code, text);
        await tester.tap(find.byKey(const Key('wa-media-open-doc1')));
        await tester.pumpAndSettle();
        expect(find.text(text), findsOneWidget);
      }
      expect(fake.savedFiles, isEmpty);
    });
  });

  group('layout', () {
    for (final width in [320.0, 390.0, 768.0, 1440.0]) {
      for (final locale in const [Locale('en'), Locale('ar')]) {
        final label = '$width px, 200% text, ${locale.languageCode}';

        testWidgets('templates at $label', (tester) async {
          final fake = connected();
          await pump(tester,
              WhatsAppTemplatesScreen(controller: WaTemplatesController(fake)),
              size: Size(width, 900), textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
          expect(find.text('order_update'), findsOneWidget);
          if (locale.languageCode == 'ar') {
            expect(find.text('القوالب'), findsOneWidget);
            expect(Directionality.of(tester.element(find.text('القوالب'))),
                TextDirection.rtl);
          }
        });

        testWidgets('setup at $label', (tester) async {
          final fake = connected();
          await pump(
              tester,
              WhatsAppSetupScreen(
                  controller: WaSetupController(fake),
                  apiBase: Uri.parse('https://api.example/')),
              size: Size(width, 900),
              textScale: 2,
              locale: locale);
          expect(tester.takeException(), isNull);
          await reveal(tester, find.byKey(const Key('wa-setup-save')));
          expect(tester.takeException(), isNull);
          final member = connected(role: 'member');
          await pump(tester,
              WhatsAppSetupScreen(controller: WaSetupController(member)),
              size: Size(width, 900), textScale: 2, locale: locale);
          expect(tester.takeException(), isNull);
        });

        testWidgets('template sheet and attachments at $label', (tester) async {
          final fake = connected()
            ..messages = [
              messageJson('doc1', kind: 'document', body: '', media: {
                'id': 'media_2',
                'mime_type': 'application/pdf',
                'filename': 'invoice-with-a-very-long-name-2026.pdf',
              }),
            ];
          await pump(
              tester,
              WhatsAppThreadScreen(
                  controller:
                      WhatsAppThreadController(fake, 'wacv_1', owner: true),
                  title: 'Ahmed'),
              size: Size(width, 900),
              textScale: 2,
              locale: locale);
          expect(tester.takeException(), isNull);
          await reveal(tester, find.byKey(const Key('wa-send-template-open')));
          await tester.tap(find.byKey(const Key('wa-send-template-open')));
          await tester.pumpAndSettle();
          await tester.tap(find.byKey(const Key('wa-send-template')));
          await tester.pumpAndSettle();
          await tester.tap(find.textContaining('order_update (en)').last);
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await tester.enterText(
              find.byKey(const Key('wa-send-param-0')), 'Sara');
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        });
      }
    }
  });

  group('inbox entry points', () {
    testWidgets('the app bar opens Templates and Setup', (tester) async {
      final fake = connected();
      await pump(tester, WhatsAppScreen(controller: WhatsAppController(fake)));
      await tester.tap(find.byKey(const Key('wa-open-templates')));
      await tester.pumpAndSettle();
      expect(find.text('order_update'), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('wa-open-setup')));
      await tester.pumpAndSettle();
      expect(find.text('Connection'), findsOneWidget);
    });

    testWidgets('not connected offers Setup to owners only', (tester) async {
      final fake = connected()..configured = false;
      await pump(tester, WhatsAppScreen(controller: WhatsAppController(fake)));
      expect(find.text('Open Setup'), findsOneWidget);
      final member = connected(role: 'member')..configured = false;
      await pump(
          tester, WhatsAppScreen(controller: WhatsAppController(member)));
      expect(find.text('Open Setup'), findsNothing);
    });
  });
}
