import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// The owner desk's action browser (docs/action-browser.md#on-the-phone):
/// the runs list from `GET /api/v1/action-browser/runs`, one run's steps,
/// and its pay step approved with the payload hash it showed.
class BrowserRunState {
  bool approved = false;
  FakeRequest? decision;
  static final hash = 'd3' * 32;

  Map<String, Object?> get session => {
        'id': 'abs_1',
        'task_id': 'task_browser',
        'run_id': 'run_browser',
        'status': approved ? 'running' : 'waiting',
        'goal': 'Book a table for two on Friday at https://table.example',
        'start_url': 'https://table.example/',
        'sites': ['table.example'],
        'logins': ['table'],
        'cards': <String>[],
        'step_count': 2,
        'current_url': 'https://table.example/checkout',
        'current_title': 'Checkout',
        'waiting': approved ? null : 'approval',
        'pending': null,
        'outcome': null,
        'message': '',
        'live': true,
        'can_watch': true,
        'can_stop': true,
        'created_at': Fx.iso(const Duration(minutes: -10)),
        'updated_at': Fx.iso(const Duration(minutes: -1)),
        'ended_at': null,
        'steps': [
          {
            'seq': 1,
            'kind': 'opened',
            'summary': 'Opened table.example',
            'url': 'https://table.example/',
            'detail': {'host': 'table.example'},
            'created_at': Fx.iso(const Duration(minutes: -9)),
          },
          {
            'seq': 2,
            'kind': 'approval',
            'summary': 'Waiting for your approval: book on table.example',
            'url': 'https://table.example/checkout',
            'detail': <String, Object?>{},
            'created_at': Fx.iso(const Duration(minutes: -1)),
          },
        ],
      };

  Map<String, Object?> get approval => {
        ...Fx.approval(
            status: approved ? 'approved' : 'pending',
            decidedBy: approved ? Fx.userId : null),
        'id': 'approval_browser',
        'task_id': 'task_browser',
        'run_id': 'run_browser',
        'action': 'web.act',
        'reason': 'Waiting for your approval: book on table.example',
        'payload_hash': hash,
        'arguments': {
          'resume': 'step',
          'kind': 'binding_button',
          'site': 'table.example',
          'url': 'https://table.example/checkout',
          'target': 'Confirm booking',
        },
      };

  void install(FakeAnumApi api) {
    api.get(
        '/api/v1/action-browser/runs',
        (_) => FakeResponse.json({
              'items': [
                {...session, 'steps': <Object?>[]}
              ],
              'limit': 20,
              'offset': 0,
              'next_offset': null,
            }));
    api.get('/api/v1/action-browser/sessions/{id}',
        (_) => FakeResponse.json(session));
    // The run screen polls the live picture while the run is active.
    api.get(
        '/api/v1/action-browser/sessions/{id}/frame',
        (_) => FakeResponse.bytes(base64Decode(_onePixelPng),
            headers: const {'content-type': 'image/png'}));
    api.get('/api/v1/approvals', (_) => FakeResponse.json([approval]));
    seedApprovalPages(api, () => [approval]);
    api.post('/api/v1/approvals/{id}/approve', (r) {
      if (r.body['payload_hash'] != hash) {
        return FakeResponse.error(409, 'approval_payload_mismatch',
            'The step changed since it was shown.');
      }
      decision = r;
      approved = true;
      return FakeResponse.json({
        'approval': approval,
        'task': Fx.task('task_browser', 'Book a table', 'running'),
        'run': null,
      });
    });
  }
}

void ownerDeskJourneys() => group('Owner desk', () {
      testWidgets(
          'action browser: runs list, a run, approve its step with the hash',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final state = BrowserRunState()..install(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Action browser');
        await waitFor(tester, find.byKey(const Key('ab-run-abs_1')), api: api);
        expect(find.text('Waiting for your approval'), findsWidgets);
        await tapOn(tester, find.byKey(const Key('ab-run-abs_1')));

        await waitFor(tester, find.text('Opened table.example'), api: api);
        await waitFor(
            tester, find.byKey(const Key('ab-approve-approval_browser')),
            api: api);
        expect(find.text('Confirm booking'), findsOneWidget);
        await tapOn(
            tester, find.byKey(const Key('ab-approve-approval_browser')));
        await waitFor(tester, find.text('Approve this step?'), api: api);
        await tapOn(tester, find.byKey(const Key('ab-approve-confirm')));

        await waitFor(tester, find.text('Approved. The agent continues.'),
            api: api);
        expect(
            state.decision?.path, '/api/v1/approvals/approval_browser/approve');
        expect(state.decision?.body['payload_hash'], BrowserRunState.hash);
        expect(api.unmatched.where((r) => r.contains('/action-browser/')),
            isEmpty);
      });
    });

/// A 1x1 PNG: the live picture the run screen polls for.
const _onePixelPng =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
