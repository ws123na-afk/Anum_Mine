import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/privacy/privacy_controller.dart';
import 'package:anum_mobile/features/privacy/privacy_models.dart';
import 'package:anum_mobile/features/privacy/privacy_repository.dart';
import 'package:anum_mobile/features/privacy/privacy_screen.dart';
import 'package:anum_mobile/features/settings/settings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

const _counts = {
  'conversations': 2,
  'conversation_turns': 6,
  'private_memories': 1,
  'retrieval_index_entries': 1,
  'voice_sessions': 1,
  'voice_transcript_segments': 3,
  'notification_preferences': 1,
  'user_preferences': 1,
};
const _shared = {
  'tasks_created': 4,
  'approvals_decided': 1,
  'shared_memories_created': 0,
  'files_uploaded': 2,
};

Map<String, Object?> _summary(Map<String, int> private,
        {String policy = 'https://example.com/privacy',
        String memory = 'on'}) =>
    {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'role': 'member',
      'private': private,
      'shared': _shared,
      'retention': {
        'conversation_days': 30,
        'voice_transcript_days': 30,
        'voice_transcript_choices': ['session', '30_days', 'permanent'],
        'private_memories': 'Kept until you delete them.',
        'task_memories': 'Kept with their task.',
        'audit_records': 'Kept as the security record.',
      },
      'model_provider': {
        'deployment': 'anthropic',
        'workspace': null,
        'conversation_memory_enabled': memory != 'off_on_server',
        'conversation_memory': memory,
      },
      'privacy_policy_url': policy,
      'delete_confirmation': 'DELETE MY DATA',
      'export_max_bytes': 26214400,
      'account_note': 'Ask your administrator to close your account.',
      'shared_note': 'Shared records stay.',
    };

class _PrivacyTransport implements ApiTransport {
  _PrivacyTransport(
      {this.summaryStatus = 200,
      this.exportStatus = 200,
      this.policy = 'https://example.com/privacy',
      this.memory = 'on'});
  final int summaryStatus;
  final String memory;
  final String policy;
  final int exportStatus;
  final List<ApiRequest> requests = [];
  Map<String, int> current = Map.of(_counts);

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    final path = request.uri.path;
    if (path.endsWith('/api/v1/privacy')) {
      return summaryStatus == 200
          ? ApiResponse(
              statusCode: 200,
              body: _summary(current, policy: policy, memory: memory))
          : ApiResponse(
              statusCode: summaryStatus,
              body: const {'detail': 'Workspace permission denied'});
    }
    // Settings, for the way from Privacy back to "Remember things from my chats".
    if (path.endsWith('/me/preferences')) {
      return ApiResponse(statusCode: 200, body: {
        'remember_conversations': memory != 'off_by_user',
        'updated_at': null,
        'conversation_memory': memory,
      });
    }
    if (path.endsWith('/notification-preferences')) {
      return const ApiResponse(statusCode: 200, body: {
        'task_completed': true,
        'approval_required': true,
        'run_failed': true,
        'automation_failed': true,
        'email_enabled': false,
        'desktop_enabled': true,
      });
    }
    if (path.endsWith('/privacy/export')) {
      return exportStatus == 200
          ? const ApiResponse(statusCode: 200, body: {
              'format': 'anum.privacy-export/v1',
              'conversations': [],
            })
          : const ApiResponse(statusCode: 413, body: {
              'detail':
                  'Your data is larger than this server’s export limit (25 MB). Ask your administrator to raise ANUM_PRIVACY_EXPORT_MAX_BYTES.'
            });
    }
    if (path.endsWith('/privacy/delete')) {
      if (request.body?['confirm'] != 'DELETE MY DATA') {
        return const ApiResponse(statusCode: 422, body: {
          'detail':
              'Type DELETE MY DATA exactly to confirm. Nothing was deleted.'
        });
      }
      final deleted = current;
      current = {for (final key in _counts.keys) key: 0};
      return ApiResponse(statusCode: 200, body: {
        'deleted': deleted,
        'kept': _shared,
        'account_note': 'Ask your administrator to close your account.',
        'shared_note': 'Shared records stay.',
      });
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

class _Saver implements PrivacyExportSaver {
  _Saver({this.accept = true});
  final bool accept;
  final saved = <String, List<int>>{};
  @override
  Future<bool> save(String fileName, List<int> bytes) async {
    if (accept) saved[fileName] = bytes;
    return accept;
  }
}

Future<MemorySessionStore> _sessions() async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession.fromJson({
    'access_token': 'anum_local_example',
    'token_type': 'bearer',
    'expires_at':
        DateTime.now().toUtc().add(const Duration(hours: 1)).toIso8601String(),
    'context': {
      'tenant_id': 'tenant_test',
      'workspace_id': 'workspace_test',
      'user_id': 'user_test',
      'roles': ['member'],
    },
  }));
  return sessions;
}

Future<ApiPrivacyRepository> _repository(_PrivacyTransport transport,
    {_Saver? saver}) async {
  final sessions = await _sessions();
  return ApiPrivacyRepository(
      AnumApiClient(
          baseUri: Uri.parse('http://127.0.0.1:8000'),
          transport: transport,
          sessions: sessions),
      saver: saver ?? _Saver(),
      clock: () => DateTime.utc(2026, 10, 11, 9));
}

void main() {
  test('the summary reads counts, retention and the provider', () {
    final summary = PrivacySummary.fromJson(_summary(_counts));
    expect(totalOf(summary.private), 16);
    expect(totalOf(summary.shared), 7);
    expect(summary.retention.first.$2,
        'Deleted after 30 days without activity, or when you delete them.');
    expect(summary.providerSentence, contains('Anthropic (Claude)'));
    expect(summary.providerSentence, isNot(contains('memor')));
    expect(summary.matches(' DELETE MY DATA '), isTrue);
    expect(summary.matches('delete my data'), isFalse);
    expect(providerLabel('ollama'), contains('own server'));
    expect(providerLabel('openai_compatible'), contains('OpenAI-compatible'));
  });

  test(
      'a deletion lists email, WhatsApp and autonomy counts and the server notes',
      () {
    final result = PrivacyDeleteResult.fromJson({
      'deleted': {'email_mailboxes': 1, 'email_messages': 4, 'email_drafts': 2},
      'kept': {'whatsapp_messages_sent': 3, 'autonomy_actions_requested': 1},
      'account_note': 'Ask your administrator.',
      'notes': ['Remove ANUM in your Zoho Mail account settings.'],
    });
    expect(
        result.deleted.map((item) => item.label),
        containsAll(<String>[
          'Private mailboxes you connected',
          'Email details synced from those mailboxes',
          'Email drafts you never sent',
        ]));
    expect(
        result.kept.map((item) => item.key),
        containsAll(
            <String>['whatsapp_messages_sent', 'autonomy_actions_requested']));
    expect(result.sentence,
        'Deleted 7 private items. 4 shared workspace records stay. Remove ANUM in your Zoho Mail account settings.');
    // Older servers send no notes.
    expect(PrivacyDeleteResult.fromJson({'deleted': {}, 'kept': {}}).sentence,
        'There was nothing private to delete.');
  });

  test('the memory sentence follows the caller\'s own state, not the flag', () {
    PrivacySummary of(String memory) =>
        PrivacySummary.fromJson(_summary(_counts, memory: memory));
    final on = of('on');
    expect(on.memorySentence,
        contains('After each reply, a second short call to the same provider'));
    expect(on.offerChatMemorySwitch, isFalse);
    for (final state in const [
      'off_by_user',
      'off_on_server',
      'off_for_organization',
      'not_for_role'
    ]) {
      expect(of(state).memorySentence,
          contains('no facts are picked out of your chats'),
          reason: state);
      expect(of(state).memorySentence, isNot(contains('After each reply')),
          reason: state);
    }
    // The deployment flag is still true here: the caller turned it off.
    final mine = of('off_by_user');
    expect(mine.memoryExtraction, isTrue);
    expect(mine.memorySentence,
        startsWith('You turned off Remember things from my chats'));
    expect(mine.offerChatMemorySwitch, isTrue);
    expect(of('off_on_server').memorySentence,
        contains('ANUM_CONVERSATION_MEMORY_ENABLED'));
    expect(of('off_for_organization').memorySentence,
        allOf(contains('still added'), contains('organization administrator')));
    expect(of('not_for_role').memorySentence,
        allOf(contains('still added'), contains('change your role')));
    for (final state in const [
      'off_on_server',
      'off_for_organization',
      'not_for_role'
    ]) {
      expect(of(state).offerChatMemorySwitch, isFalse, reason: state);
    }
    // An older server without the field: no claim either way.
    final old = _summary(_counts);
    (old['model_provider'] as Map).remove('conversation_memory');
    expect(
        PrivacySummary.fromJson(old).memorySentence, contains('did not say'));
  });

  testWidgets('off by the caller: Privacy leads back to the switch in Settings',
      (tester) async {
    final transport = _PrivacyTransport(memory: 'off_by_user');
    final sessions = await _sessions();
    final api = AnumApiClient(
        baseUri: Uri.parse('http://127.0.0.1:8000'),
        transport: transport,
        sessions: sessions);
    final settings = SettingsController(
        AuthRepository(api: api, sessions: sessions),
        privacy: ApiPrivacyRepository(api, saver: _Saver()));
    await tester.binding.setSurfaceSize(const Size(390, 844));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
        theme: AnumTheme.dark(), home: SettingsScreen(controller: settings)));
    await tester.pumpAndSettle();

    final privacy = find.text('Your data and privacy');
    await tester.scrollUntilVisible(privacy, 200,
        scrollable: find.byType(Scrollable).first);
    // The Settings list has grown (phone push); bring the tile fully on screen first.
    await tester.ensureVisible(privacy);
    await tester.pumpAndSettle();
    await tester.tap(privacy);
    await tester.pumpAndSettle();

    final memory = find.byKey(const Key('privacy-memory'));
    await tester.scrollUntilVisible(memory, 200,
        scrollable: find
            .descendant(
                of: find.byType(PrivacyScreen),
                matching: find.byType(Scrollable))
            .first);
    expect(tester.widget<Text>(memory).data,
        startsWith('You turned off Remember things from my chats'));
    final change = find.text('Change Remember things from my chats');
    await tester.scrollUntilVisible(change, 100,
        scrollable: find
            .descendant(
                of: find.byType(PrivacyScreen),
                matching: find.byType(Scrollable))
            .first);
    await tester.ensureVisible(change);
    await tester.pumpAndSettle();
    await tester.tap(change);
    await tester.pumpAndSettle();

    expect(find.byType(PrivacyScreen), findsNothing);
    final toggle =
        find.widgetWithText(SwitchListTile, 'Remember things from my chats');
    expect(toggle, findsOneWidget);
    expect(tester.widget<SwitchListTile>(toggle).value, isFalse);
    final box = tester.getRect(toggle);
    expect(box.top, greaterThanOrEqualTo(0));
    expect(box.bottom, lessThanOrEqualTo(844));
  });

  testWidgets('the memory sentence fits every width and 200% text',
      (tester) async {
    for (final memory in const ['on', 'off_by_user', 'off_for_organization']) {
      for (final width in const [320.0, 390.0, 768.0, 1024.0, 1440.0]) {
        for (final scale in const [1.0, 2.0]) {
          final controller = PrivacyController(
              await _repository(_PrivacyTransport(memory: memory)));
          await tester.binding.setSurfaceSize(Size(width, 900));
          await tester.pumpWidget(MaterialApp(
              key: ValueKey('$memory $width $scale'),
              theme: AnumTheme.dark(),
              home: MediaQuery.withClampedTextScaling(
                  minScaleFactor: scale,
                  maxScaleFactor: scale,
                  child: PrivacyScreen(
                      controller: controller, onOpenChatMemory: () {}))));
          await tester.pumpAndSettle();
          final text = find.byKey(const Key('privacy-memory'));
          // The count rows grow with every module: at 320 px and 200% text
          // the sentence sits past the default 50 scrolls.
          await tester.scrollUntilVisible(text, 200,
              maxScrolls: 100, scrollable: find.byType(Scrollable).first);
          await tester.pumpAndSettle();
          final rect = tester.getRect(text);
          expect(rect.left, greaterThanOrEqualTo(0));
          expect(rect.right, lessThanOrEqualTo(width));
          final change = find.text('Change Remember things from my chats');
          expect(
              change, memory == 'off_by_user' ? findsOneWidget : findsNothing,
              reason: '$memory $width $scale');
          if (memory == 'off_by_user') {
            await tester.ensureVisible(change);
            await tester.pumpAndSettle();
            expect(tester.getRect(change).right, lessThanOrEqualTo(width));
            expect(
                tester
                    .getSize(find.ancestor(
                        of: change, matching: find.byType(TextButton)))
                    .height,
                greaterThanOrEqualTo(40));
          }
          expect(tester.takeException(), isNull,
              reason: '$memory $width $scale');
        }
      }
    }
    await tester.binding.setSurfaceSize(null);
  });

  test('download saves the export as a dated JSON file', () async {
    final saver = _Saver();
    final controller =
        PrivacyController(await _repository(_PrivacyTransport(), saver: saver));
    await controller.exportMyData();
    expect(controller.exportFailed, isFalse);
    expect(controller.exportMessage, startsWith('Saved your data'));
    final bytes = saver.saved['anum-my-data-2026-10-11.json']!;
    expect(jsonDecode(utf8.decode(bytes))['format'], 'anum.privacy-export/v1');
  });

  test('a cancelled save and an export over the cap are explained', () async {
    final cancelled = PrivacyController(
        await _repository(_PrivacyTransport(), saver: _Saver(accept: false)));
    await cancelled.exportMyData();
    expect(cancelled.exportMessage, 'Download cancelled. Nothing was saved.');

    final tooLarge = PrivacyController(
        await _repository(_PrivacyTransport(exportStatus: 413)));
    await tooLarge.exportMyData();
    expect(tooLarge.exportFailed, isTrue);
    expect(tooLarge.exportMessage, contains('ANUM_PRIVACY_EXPORT_MAX_BYTES'));
  });

  test('a 403 shows who can help', () async {
    final controller = PrivacyController(
        await _repository(_PrivacyTransport(summaryStatus: 403)));
    await controller.load();
    expect(controller.phase, PrivacyPhase.permissionDenied);
  });

  testWidgets('delete needs the typed phrase, then reports what went',
      (tester) async {
    final transport = _PrivacyTransport();
    final controller = PrivacyController(await _repository(transport));
    await tester.binding.setSurfaceSize(const Size(390, 3800));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester
        .pumpWidget(MaterialApp(home: PrivacyScreen(controller: controller)));
    await tester.pumpAndSettle();

    expect(find.text('What ANUM stores about you'), findsOneWidget);
    expect(find.text('https://example.com/privacy'), findsOneWidget);
    final button = find.widgetWithText(FilledButton, 'Delete my data');
    await tester.scrollUntilVisible(button, 200,
        scrollable: find.byType(Scrollable).first);
    expect(tester.widget<FilledButton>(button).onPressed, isNull);

    await tester.enterText(
        find.byKey(const Key('privacy-confirm')), 'delete my data');
    await tester.pump();
    expect(tester.widget<FilledButton>(button).onPressed, isNull);

    await tester.enterText(
        find.byKey(const Key('privacy-confirm')), 'DELETE MY DATA');
    await tester.pump();
    await tester.ensureVisible(button);
    await tester.pumpAndSettle();
    expect(tester.widget<FilledButton>(button).onPressed, isNotNull);
    await tester.tap(button);
    await tester.pumpAndSettle();

    await tester.scrollUntilVisible(
        find.textContaining('Deleted 16 private items.'), 200,
        scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('Deleted 16 private items.'), findsOneWidget);
    expect(find.textContaining('7 shared workspace records stay.'),
        findsOneWidget);
    await tester.scrollUntilVisible(
        find.text('Nothing private is stored for you here yet.'), -200,
        scrollable: find.byType(Scrollable).first);
    expect(find.text('Nothing private is stored for you here yet.'),
        findsOneWidget);
    final delete =
        transport.requests.singleWhere((r) => r.uri.path.endsWith('/delete'));
    expect(delete.body, {'confirm': 'DELETE MY DATA'});
  });

  testWidgets('the policy opens in the browser, https only, copy otherwise',
      (tester) async {
    final opened = <Uri>[];
    Future<void> show(String policy, {bool browser = true}) async {
      final controller = PrivacyController(
          await _repository(_PrivacyTransport(policy: policy)));
      await tester.pumpWidget(MaterialApp(
          home: PrivacyScreen(
              key: ValueKey('$policy $browser'),
              controller: controller,
              openLink: (uri) async {
                opened.add(uri);
                return browser;
              })));
      await tester.pumpAndSettle();
      final tile = find.text('Read the privacy policy');
      await tester.scrollUntilVisible(tile, 200,
          scrollable: find.byType(Scrollable).first);
      await tester.tap(tile);
      await tester.pumpAndSettle();
    }

    await tester.binding.setSurfaceSize(const Size(390, 3800));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String?;
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));

    await show('https://example.com/privacy');
    expect(opened, [Uri.parse('https://example.com/privacy')]);

    await show('http://example.com/privacy');
    expect(opened, hasLength(1), reason: 'plain http is never opened');
    expect(find.textContaining('not an https address'), findsOneWidget);
    expect(copied, 'http://example.com/privacy');

    await show('https://example.com/privacy', browser: false);
    expect(opened, hasLength(2));
    expect(find.textContaining('The link was copied'), findsOneWidget);
  });
}
