import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// Business (docs/business.md), modelled on apps/web/e2e/support/businessApi.ts:
/// one qualified lead whose drafted proposal waits for the owner's
/// acceptance approval (`business.accept_proposal`, payload hash bound).
class BusinessState {
  bool approved = false;
  FakeRequest? decision;
  static final hash = 'b7' * 32;

  static const scope = {
    'tenant_id': Fx.tenantId,
    'workspace_id': Fx.workspaceId
  };

  Map<String, Object?> get lead => {
        'id': 'blead_1',
        ...scope,
        'account_id': 'bacct_1',
        'contact_id': 'bcont_1',
        'product_id': 'bprod_1',
        'title': 'Fleet maintenance contract',
        'state': approved ? 'won' : 'offered',
        'owner_id': Fx.userId,
        'source': 'manual',
        'score': <String, Object?>{},
        'score_total': 9,
        'score_known': 4,
        'next_step': 'Wait for the acceptance decision.',
        'lost_reason': '',
        'suppressed_reason': '',
        'qualified_at': Fx.iso(const Duration(days: -3)),
        'won_at': null,
        'created_by': Fx.userId,
        'created_at': Fx.iso(const Duration(days: -7)),
        'updated_at': Fx.iso(const Duration(hours: -1)),
        'account_name': 'Gulf Mixers LLC',
        'account_domain': 'gulfmixers.example',
        'contact_name': 'Sara Haddad',
        'product_name': 'Fleet care',
        'block_reason': null,
      };

  Map<String, Object?> get leadDetail => {
        'lead': lead,
        'account': {
          'id': 'bacct_1',
          ...scope,
          'name': 'Gulf Mixers LLC',
          'website': 'https://gulfmixers.example',
          'domain': 'gulfmixers.example',
          'do_not_contact': false,
          'dnc_reason': '',
        },
        'contact': {
          'id': 'bcont_1',
          'name': 'Sara Haddad',
          'role_title': 'Operations manager'
        },
        'product': null,
        'evidence': [
          {
            'id': 'bevi_1',
            ...scope,
            'lead_id': 'blead_1',
            'url': 'https://gulfmixers.example/news/fleet',
            'title': 'Gulf Mixers adds 40 trucks',
            'published_on': '2026-09-30',
            'excerpt': 'The company expands its mixer fleet in Riyadh.',
            'confidence': 'high',
            'source': 'manual',
            'created_at': Fx.iso(const Duration(days: -6)),
          }
        ],
        'inferences': <Object?>[],
        'activities': [
          {
            'id': 'bact_1',
            'kind': 'state_changed',
            'summary': 'Qualified to Offered.',
            'actor': Fx.userId,
            'created_at': Fx.iso(const Duration(hours: -2)),
          }
        ],
        'drafts': <Object?>[],
        'research_runs': <Object?>[],
        'opportunity_id': 'bopp_1',
        'qualification_gaps': <String>[],
        'allowed_transitions': ['won', 'lost', 'suppressed'],
        'consent_gap': null,
      };

  Map<String, Object?> get approval => {
        ...Fx.approval(
            status: approved ? 'approved' : 'pending',
            decidedBy: approved ? Fx.userId : null),
        'id': 'approval_biz',
        'task_id': 'task_biz',
        'action': 'business.accept_proposal',
        'reason': 'Accepting a proposal always asks an owner.',
        'run_id': 'run_biz',
        'payload_hash': hash,
        'arguments': {
          'title': 'Fleet care proposal',
          'client': 'Gulf Mixers LLC',
          'currency': 'SAR',
          'total': '2500.00',
        },
      };

  Map<String, Object?> get proposal => {
        'id': 'bprop_1',
        ...scope,
        'opportunity_id': 'bopp_1',
        'account_id': 'bacct_1',
        'title': 'Fleet care proposal',
        'status': approved ? 'accepted' : 'pending_approval',
        'current_version': 1,
        'decision_note': '',
        'account_name': 'Gulf Mixers LLC',
        'opportunity_name': 'Fleet maintenance',
        'current': {
          'version': 1,
          'currency': 'SAR',
          'total': 2500.0,
          'subtotal': 2500.0,
          'issues': <String>[],
          'warnings': <String>[],
          'locked': true,
        },
        'approval_state': approved ? 'none' : 'pending',
        'approval': approved ? null : approval,
        'project_id': null,
      };

  void install(FakeAnumApi api) {
    Map<String, Object?> page(List<Map<String, Object?>> items) =>
        {'items': items, 'total': items.length, 'limit': 20, 'offset': 0};
    api.get(
        '/api/v1/business/setup',
        (_) => const FakeResponse.json({
              'permissions': {
                'read': true,
                'write': true,
                'manage': true,
                'decide': true
              },
              'sending_enabled': false,
              'web_search': false,
              'model_connected': true,
            }));
    api.get(
        '/api/v1/business/kpi',
        (_) => const FakeResponse.json({
              'days': 30,
              'leads_created': 1,
              'funnel': [
                {'stage': 'created', 'reached': 1, 'of': 1, 'rate_pct': 100.0},
                {
                  'stage': 'qualified',
                  'reached': 1,
                  'of': 1,
                  'rate_pct': 100.0
                },
              ],
              'open_opportunities': 1,
              'pipeline_assumed': [
                {'currency': 'SAR', 'amount': 2500.0}
              ],
              'contracted': <Object?>[],
              'cash_collected': <Object?>[],
              'proposals_accepted': 0,
              'notes': [
                "Pipeline values and probabilities are the owner's assumptions, "
                    'not forecasts.'
              ],
            }));
    api.get('/api/v1/business/leads', (_) => FakeResponse.json(page([lead])));
    api.get(
        '/api/v1/business/leads/{id}', (_) => FakeResponse.json(leadDetail));
    api.get('/api/v1/business/proposals',
        (_) => FakeResponse.json(page([proposal])));
    api.get('/api/v1/approvals', (_) => FakeResponse.json([approval]));
    api.get(
        '/api/v1/tasks',
        (_) => FakeResponse.json([
              Fx.task('task_biz', 'Accept the Fleet care proposal',
                  approved ? 'completed' : 'waiting_approval')
            ]));
    api.post('/api/v1/approvals/{id}/approve', (r) {
      if (r.body['payload_hash'] != hash) {
        return FakeResponse.error(409, 'approval_payload_mismatch',
            'The proposal changed since it was shown.');
      }
      decision = r;
      approved = true;
      return FakeResponse.json({
        'approval': approval,
        'task':
            Fx.task('task_biz', 'Accept the Fleet care proposal', 'completed'),
        'run': null,
      });
    });
  }
}

void businessJourneys() => group('Business', () {
      testWidgets(
          'business: lead list, lead detail, then approve the drafted proposal',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final business = BusinessState()..install(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Business');
        await waitFor(tester, find.byKey(const Key('biz-tab-leads')), api: api);
        await tapOn(tester, find.byKey(const Key('biz-tab-leads')));
        await waitFor(tester, find.text('Gulf Mixers LLC'), api: api);
        await tapOn(tester, find.text('Gulf Mixers LLC'));

        // Lead detail: state, evidence and the moves the API allows.
        await waitFor(tester, find.text('Fleet maintenance contract'),
            api: api);
        await waitFor(tester, find.text('Gulf Mixers adds 40 trucks'),
            api: api);
        expect(find.byKey(const Key('biz-move-lost')), findsOneWidget);
        await tester.pageBack();
        await pumpFor(tester);

        // The proposal waits for acceptance; Approvals decides it.
        await tapOn(tester, find.byKey(const Key('biz-tab-proposals')));
        await waitFor(tester, find.text('Fleet care proposal'), api: api);
        await tapOn(tester, find.text('Open Approvals'));
        await waitFor(tester, find.text('Tool: business.accept_proposal'),
            api: api);
        await tapOn(tester, find.widgetWithText(FilledButton, 'Approve'));
        await waitFor(tester, find.text('Approve this action?'), api: api);
        await tapOn(
            tester,
            find.descendant(
                of: find.byType(AlertDialog),
                matching: find.widgetWithText(FilledButton, 'Approve')));

        await waitFor(tester, find.text('Nothing is waiting for you'),
            api: api);
        expect(
            business.decision?.path, '/api/v1/approvals/approval_biz/approve');
        expect(business.decision?.body['payload_hash'], BusinessState.hash);
        expect(api.unmatched.where((r) => r.contains('/business/')), isEmpty);
      });
    });
