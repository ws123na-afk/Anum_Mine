import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// The unified inbox (docs/email.md), in the shapes of `MessagePage`,
/// `MessageDetail`, `DraftCreateResult` and `SendApprovalResponse`
/// (services/api/anum_api/email_api.py).
class InboxState {
  final drafts = <Map<String, Object?>>[];
  FakeRequest? askedToSend;

  static Map<String, Object?> message() => {
        'id': 'msg_invoice',
        'account_id': 'acct_ops',
        'account_address': 'ops@foundation.example',
        'provider': 'gmail',
        'thread_id': 'thread_invoice',
        'subject': 'Invoice 4471 is overdue',
        'from_address': 'accounts@gulftyres.example',
        'from_name': 'Gulf Tyres Accounts',
        'to': ['ops@foundation.example'],
        'cc': <String>[],
        'snippet': 'Invoice 4471 for 18 tyres was due on Monday.',
        'labels': ['INBOX'],
        'received_at': Fx.iso(const Duration(hours: -2)),
        'is_read': false,
        'is_archived': false,
        'has_attachments': false,
        'category': 'finance',
        'priority': 'high',
        'summary': 'Gulf Tyres asks when invoice 4471 will be paid.',
        'triage_source': 'model',
      };

  Map<String, Object?> draft({Map<String, Object?>? approval}) => {
        'id': 'draft_reply',
        'account_id': 'acct_ops',
        'message_id': 'msg_invoice',
        'to': ['accounts@gulftyres.example'],
        'cc': <String>[],
        'subject': 'Re: Invoice 4471 is overdue',
        'body': 'Thank you for the reminder. Invoice 4471 will be paid on '
            'Thursday.',
        'status': approval == null ? 'draft' : 'pending_approval',
        'source': 'model',
        'model_provider': 'anthropic',
        'model_name': 'claude-sonnet',
        'saved_in_provider': false,
        'error': null,
        'approval': approval,
        'approval_note': null,
        'created_by': Fx.userId,
        'created_at': Fx.iso(),
        'updated_at': Fx.iso(),
        'sent_at': null,
      };

  static Map<String, Object?> sendApproval() => {
        ...Fx.approval(),
        'id': 'approval_send',
        'task_id': 'task_email_send',
        'action': 'email.send',
        'risk_level': 'medium',
        'reason': 'Sending email on your behalf needs approval.',
        'target': 'accounts@gulftyres.example',
        'arguments': {
          'from': 'ops@foundation.example',
          'to': ['accounts@gulftyres.example'],
          'subject': 'Re: Invoice 4471 is overdue',
        },
        'payload_hash': 'cd' * 32,
      };

  void install(FakeAnumApi api) {
    api.get(
        '/api/v1/email/accounts',
        (_) => FakeResponse.json([
              {
                'id': 'acct_ops',
                'provider': 'gmail',
                'provider_name': 'Gmail',
                'address': 'ops@foundation.example',
                'status': 'connected',
                'visibility': 'workspace',
                'last_sync_at': Fx.iso(const Duration(minutes: -3)),
                'last_error': null,
                'unread_count': 1,
                'message_count': 1,
              }
            ]));
    api.get(
        '/api/v1/email/messages',
        (_) => FakeResponse.json({
              'items': [message()],
              'next_cursor': null,
              'accounts': 1
            }));
    api.get(
        '/api/v1/email/messages/{id}',
        (_) => FakeResponse.json({
              'message': message(),
              'thread': [message()],
              'body': 'Hello, invoice 4471 for 18 tyres was due on Monday. '
                  'Please confirm the payment date.',
              'body_truncated': false,
              'body_error': null,
              'attachments': <Object?>[],
              'drafts': drafts,
            }));
    api.post(
        '/api/v1/email/messages/{id}/read',
        (_) => FakeResponse.json({
              'status': 'done',
              'summary': 'Marked read.',
              'approval': null,
              'message': {...message(), 'is_read': true},
            }));
    api.post('/api/v1/email/messages/{id}/drafts', (_) {
      final created = draft();
      drafts.add(created);
      return FakeResponse.json({
        'status': 'done',
        'summary': 'Drafted a reply.',
        'draft': created,
        'approval': null,
        'warning': null,
      }, status: 201);
    });
    api.post('/api/v1/email/drafts/{id}/send-approval', (r) {
      askedToSend = r;
      final approval = sendApproval();
      return FakeResponse.json(
          {'draft': draft(approval: approval), 'approval': approval});
    });
  }
}

void emailJourneys() => group('Email', () {
      testWidgets(
          'email: list, open, draft a reply and ask approval to send it',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final inbox = InboxState()..install(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Inbox');
        await waitFor(tester, find.text('Invoice 4471 is overdue'), api: api);
        await tapOn(tester, find.text('Invoice 4471 is overdue'));

        await waitFor(
            tester, find.textContaining('Please confirm the payment date'),
            api: api);
        await tapOn(tester, find.text('Draft with AI'));
        await waitFor(tester, find.text('Drafted by AI (claude-sonnet)'),
            api: api);
        expect(find.textContaining('will be paid on Thursday'), findsOneWidget);

        await tapOn(tester, find.text('Ask approval to send'));
        await waitFor(tester,
            find.text('Sending waits for approval of this exact message.'),
            api: api);
        expect(inbox.askedToSend?.path,
            '/api/v1/email/drafts/draft_reply/send-approval');
        expect(
            find.textContaining('Payload hash: cdcdcdcdcdcd'), findsOneWidget);
        expect(find.text('Approve and send'), findsOneWidget);
        // Nothing was sent: sending waits for the owner's approval.
        expect(api.log.where((r) => r.path.endsWith('/approve')), isEmpty);
        expect(api.unmatched.where((r) => r.contains('/email/')), isEmpty);
      });

      testWidgets(
          'email: drafting that waits for approval shows the approval, '
          'and approving it brings the draft', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final inbox = InboxState()..install(api);
        final draftApproval = {
          ...Fx.approval(),
          'id': 'approval_draft',
          'task_id': 'task_email_draft',
          'action': 'email.draft_reply',
          'risk_level': 'medium',
          'reason': 'Workspace policy: drafting email needs approval.',
          'arguments': {'message_id': 'msg_invoice'},
          'payload_hash': 'ef' * 32,
        };
        FakeRequest? decision;
        // `DraftCreateResult` with status waiting_approval: no draft yet.
        api.post(
            '/api/v1/email/messages/{id}/drafts',
            (_) => FakeResponse.json({
                  'status': 'waiting_approval',
                  'summary': 'Drafting waits for approval (workspace policy).',
                  'draft': null,
                  'approval': draftApproval,
                  'warning': null,
                }, status: 201));
        api.post('/api/v1/approvals/{id}/approve', (r) {
          decision = r;
          inbox.drafts.add(inbox.draft());
          return FakeResponse.json({
            'approval': {...draftApproval, 'status': 'approved'},
            'task': Fx.task(
                'task_email_draft', 'Draft an email reply', 'completed'),
            'run': {
              'id': 'run_email_draft',
              'task_id': 'task_email_draft',
              'status': 'completed',
              'result': 'Drafted a reply.',
              'steps': <Object?>[],
            },
          });
        });
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Inbox');
        await waitFor(tester, find.text('Invoice 4471 is overdue'), api: api);
        await tapOn(tester, find.text('Invoice 4471 is overdue'));
        await waitFor(tester, find.text('Draft with AI'), api: api);
        await tapOn(tester, find.text('Draft with AI'));

        await waitFor(tester, find.byKey(const Key('inbox-draft-approval')),
            api: api);
        expect(
            find.textContaining('Payload hash: efefefefefef'), findsOneWidget);
        await tapOn(tester, find.text('Approve drafting'));

        await waitFor(tester, find.text('Drafted by AI (claude-sonnet)'),
            api: api);
        expect(decision?.path, '/api/v1/approvals/approval_draft/approve');
        expect(decision?.body['payload_hash'], 'ef' * 32);
        expect(find.byKey(const Key('inbox-draft-approval')), findsNothing);
      });
    });
