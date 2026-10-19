import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

void taskApprovalJourneys() => group('Sign-in, Home and task approval', () {
      testWidgets(
          'sign in, Home, open the waiting task and approve it with the payload hash',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final workspace = WorkspaceState()..install(api);
        await launchApp(tester, api);

        await signIn(tester, api);
        expect(api.sent('POST', '/api/v1/auth/local/session').single.body, {
          'tenant_id': Fx.tenantId,
          'workspace_id': Fx.workspaceId,
          'user_id': Fx.userId,
        });

        // Home: the focus list names the task that waits for a decision.
        await waitFor(tester, find.text("Today's Focus"), api: api);
        await waitFor(tester, find.text('Review the release'), api: api);
        await tapOn(tester, find.text('Review the release'));

        // The task: its approval card shows exactly what will run and its hash.
        await waitFor(tester, find.byKey(const Key('task-approval')), api: api);
        expect(find.textContaining('Publish the release notes'), findsWidgets);
        expect(find.textContaining('Payload hash'), findsOneWidget);
        await tapOn(
            tester,
            find.descendant(
                of: find.byKey(const Key('task-approval')),
                matching: find.widgetWithText(FilledButton, 'Approve')));
        await waitFor(tester, find.text('Approve this action?'), api: api);
        await tapOn(
            tester,
            find.descendant(
                of: find.byType(AlertDialog),
                matching: find.widgetWithText(FilledButton, 'Approve')));

        // The decision carried the hash that was displayed, and the task moved on.
        await waitFor(
            tester, find.text('Release notes published to #announcements.'),
            api: api);
        expect(workspace.decision?.path,
            '/api/v1/approvals/approval_pending/approve');
        expect(workspace.decision?.body['payload_hash'], Fx.payloadHash);
        expect(find.byKey(const Key('task-approval')), findsNothing);
        expect(api.unmatched.where((r) => r.contains('approval')), isEmpty);
      });

      testWidgets(
          'sign-in: a wrong password and a refused one-time code say why',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        api.post(
            '/api/v1/auth/local/session',
            (_) => FakeResponse.error(
                401, 'invalid_credentials', 'The password is not correct.'));
        api.post(
            '/api/v1/auth/local/otp/request',
            (_) => FakeResponse.error(403, 'otp_disabled',
                'One-time codes are turned off for this workspace.'));
        await launchApp(tester, api);

        await signIn(tester, api);
        await waitFor(
            tester, find.textContaining('The password is not correct.'),
            api: api);
        await tapOn(tester, find.text('Try again'));
        await waitFor(tester, find.text('Sign in to ANUM'), api: api);

        await tapOn(tester, find.text('Use one-time code'));
        await waitFor(
            tester,
            find.text(
                'No code was sent: One-time codes are turned off for this '
                'workspace.'),
            api: api);
        expect(find.text('Sign in to ANUM'), findsOneWidget);
      });
    });
