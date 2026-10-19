import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// Phone calls (docs/calls.md), shaped like apps/web/e2e/support/callsApi.ts:
/// a logged call with notes and an AI summary, and a missed inbound call.
Map<String, Object?> _call(String id, Map<String, Object?> extra) => {
      'id': id,
      'user_id': Fx.userId,
      'contact_id': null,
      'contact_name': null,
      'phone': null,
      'phone_normalized': null,
      'peer_user_id': null,
      'direction': 'outbound',
      'kind': 'phone',
      'status': 'logged',
      'outcome': 'answered',
      'started_at': Fx.iso(const Duration(hours: -3)),
      'ended_at': null,
      'duration_seconds': 95,
      'notes': null,
      'summary': null,
      'follow_up': null,
      'summary_provenance': null,
      'provider': null,
      'recording_file_id': null,
      'transcript': null,
      'created_at': Fx.iso(const Duration(hours: -3)),
      'updated_at': Fx.iso(const Duration(hours: -3)),
      'can_edit': true,
      ...extra,
    };

final _quote = _call('call_quote', {
  'contact_id': 'contact_sara',
  'contact_name': 'Sara Example',
  'phone': '+971501234567',
  'phone_normalized': '+971501234567',
  'notes': 'Wants a quote for 40 tyres by Friday.',
  'summary': 'Sara needs a quote for 40 tyres before Friday.',
  'summary_provenance': {'model': 'claude-sonnet', 'at': Fx.iso()},
});
final _busy = _call('call_busy', {
  'phone': '+97142223333',
  'phone_normalized': '+97142223333',
  'outcome': 'busy',
  'direction': 'inbound',
  'duration_seconds': 0,
  'started_at': Fx.iso(const Duration(days: -1)),
});

void installCalls(FakeAnumApi api) {
  Map<String, Object?> page(List<Object?> items) =>
      {'items': items, 'total': items.length, 'offset': 0, 'limit': 25};
  api.get(
      '/api/v1/calls/setup',
      (_) => const FakeResponse.json({
            'can_read': true,
            'can_log': true,
            'can_manage': true,
            'can_create_tasks': true,
            'model_ready': true,
            'provider_connected': false,
            'transcription_configured': false,
            'capabilities': {
              'browser_calls': false,
              'recording': false,
              'transcription': false,
              'ai_calls': false,
              'inbound_ai': false,
              'problem': 'No voice provider is connected.',
              'ai_problem': 'No voice provider is connected.',
              'cost_per_minute_cents': null,
              'recording_notice': null,
            },
          }));
  api.get(
      '/api/v1/calls/contacts',
      (_) => FakeResponse.json(page([
            {
              'id': 'contact_sara',
              'name': 'Sara Example',
              'phone': '+971501234567',
              'phone_normalized': '+971501234567',
              'company': 'Al Noor Trading',
              'email': null,
              'notes': null,
              'favourite': true,
              'source': 'manual',
              'language': 'auto',
              'ai_consent': null,
              'created_by': Fx.userId,
              'created_at': Fx.iso(const Duration(days: -8)),
              'updated_at': Fx.iso(const Duration(days: -8)),
              'opted_out': false,
              'can_edit': true,
            }
          ])));
  api.get('/api/v1/calls/log', (_) => FakeResponse.json(page([_quote, _busy])));
  api.get(
      '/api/v1/calls/log/{id}',
      (r) => switch (r.params['id']) {
            'call_quote' => FakeResponse.json(_quote),
            'call_busy' => FakeResponse.json(_busy),
            _ => FakeResponse.error(404, 'not_found', 'Call not found'),
          });
}

void callsJourneys() => group('Phone calls', () {
      testWidgets('calls: history, then a call detail', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installCalls(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Phone calls');
        await waitFor(tester, find.text('Recents'), api: api);
        await tapOn(tester, find.text('Recents'));
        await waitFor(tester, find.text('Sara Example'), api: api);
        expect(find.textContaining('+97142223333'), findsWidgets);

        await tapOn(tester, find.text('Sara Example').last);
        await waitFor(tester, find.byKey(const Key('detail-notes')), api: api);
        expect(
            find.text('Wants a quote for 40 tyres by Friday.'), findsWidgets);
        expect(find.textContaining('Sara needs a quote for 40 tyres'),
            findsWidgets);
        expect(api.unmatched.where((r) => r.contains('/calls/')), isEmpty);
      });
    });
