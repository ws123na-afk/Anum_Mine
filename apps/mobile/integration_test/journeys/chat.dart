import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// The Assistant chat (docs/conversations.md): a question streams back as
/// `turn`, `delta` and `done` server-sent events, as `POST
/// /api/v1/conversations/{id}/ask` sends them.
void installChat(FakeAnumApi api) {
  final conversation = {
    'id': 'conv_1',
    'title': '',
    'created_at': Fx.iso(),
    'updated_at': Fx.iso(),
  };
  final turns = <Map<String, Object?>>[];
  api.get('/api/v1/conversations', (_) => const FakeResponse.json(<Object?>[]));
  api.post('/api/v1/conversations',
      (_) => FakeResponse.json(conversation, status: 201));
  api.get('/api/v1/conversations/{id}',
      (_) => FakeResponse.json({...conversation, 'turns': turns}));
  api.post('/api/v1/conversations/{id}/ask', (r) {
    final question = r.body['content'] as String;
    final user = {
      'id': 'turn_user_1',
      'role': 'user',
      'content': question,
      'status': 'complete',
      'created_at': Fx.iso(),
    };
    final answer = {
      'id': 'turn_assistant_1',
      'role': 'assistant',
      'content': 'Three invoices are overdue: Gulf Tyres, Noor Logistics '
          'and Desert Freight.',
      'status': 'complete',
      'created_at': Fx.iso(),
    };
    turns
      ..add(user)
      ..add(answer);
    return FakeResponse.events([
      ('turn', {'user_turn': user}),
      ('delta', {'text': 'Three invoices are overdue: '}),
      ('delta', {'text': 'Gulf Tyres, Noor Logistics and Desert Freight.'}),
      ('done', {'assistant_turn': answer}),
    ]);
  });
}

void chatJourneys() => group('Assistant chat', () {
      testWidgets('chat: ask a question and read the streamed answer',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installChat(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await waitFor(tester, find.text('Chat'), api: api);
        await tapOn(tester, find.text('Chat'));
        await waitFor(tester, find.byKey(const Key('chat-input')), api: api);
        await tester.enterText(
            find.byKey(const Key('chat-input')), 'Which invoices are overdue?');
        await tapOn(tester, find.byKey(const Key('chat-send')));

        await waitFor(tester, find.text('Which invoices are overdue?'),
            api: api);
        await waitFor(
            tester,
            find.textContaining(
                'Three invoices are overdue: Gulf Tyres, Noor Logistics'),
            api: api);
        final ask = api.sent('POST', '/api/v1/conversations/conv_1/ask').single;
        expect(ask.body, {'content': 'Which invoices are overdue?'});
        expect(find.byKey(const Key('chat-stop')), findsNothing);
      });
    });
