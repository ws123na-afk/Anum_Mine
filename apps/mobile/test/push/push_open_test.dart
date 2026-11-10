// A tapped push opens only in the organization and workspace it belongs to
// (docs/notifications.md#phone-push).
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/push/push_messaging.dart';
import 'package:anum_mobile/features/push/push_models.dart';
import 'package:anum_mobile/src/anum_app.dart';
import 'package:anum_mobile/src/app_environment.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../integration_test/support/fake_api.dart';
import '../../integration_test/support/fixtures.dart';
import '../../integration_test/support/harness.dart';
import 'push_fakes.dart';

const session = TenantContext(
    tenantId: 'tenant_local',
    workspaceId: 'workspace_foundation',
    userId: 'user_owner',
    roles: ['owner']);

PushDestination approvalIn(String workspace, [String? tenant]) =>
    PushDestination.fromData({
      'anum_kind': 'approval_required',
      'workspace_id': workspace,
      if (tenant != null) 'tenant_id': tenant,
      'approval_id': 'approval_pending',
    })!;

void main() {
  group('PushDestination.isFor', () {
    test('needs the same workspace and the same organization', () {
      expect(approvalIn('workspace_foundation', 'tenant_local').isFor(session),
          isTrue);
      expect(approvalIn('workspace_other', 'tenant_local').isFor(session),
          isFalse);
      // The same workspace id in another organization is another workspace.
      expect(approvalIn('workspace_foundation', 'tenant_other').isFor(session),
          isFalse);
    });

    test('an older push without the organization checks the workspace', () {
      expect(approvalIn('workspace_foundation').isFor(session), isTrue);
      expect(approvalIn('workspace_other').isFor(session), isFalse);
    });
  });

  group('the notice', () {
    const memberships = [
      CallerMembership(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_foundation',
          role: 'owner',
          workspaceName: 'Foundation'),
      CallerMembership(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_riyadh',
          role: 'member',
          workspaceName: 'Riyadh branch'),
    ];

    test('names the workspace, never its id', () {
      final text = pushOtherWorkspaceNotice(
          approvalIn('workspace_riyadh', 'tenant_local'), memberships,
          arabic: false);
      expect(text,
          'That notification is for the workspace "Riyadh branch". Switch to it in Settings to open it.');
      expect(
          pushOtherWorkspaceNotice(
              approvalIn('workspace_riyadh', 'tenant_local'), memberships,
              arabic: true),
          contains('"Riyadh branch"'));
    });

    test('a workspace the person cannot see is "another workspace"', () {
      for (final destination in [
        approvalIn('workspace_secret', 'tenant_local'),
        approvalIn('workspace_foundation', 'tenant_other'),
      ]) {
        for (final arabic in [false, true]) {
          final text = pushOtherWorkspaceNotice(destination, memberships,
              arabic: arabic);
          expect(text, isNot(contains('workspace_')));
          expect(text, isNot(contains('tenant_')));
          expect(text, isNot(contains('Foundation')));
        }
      }
      expect(
          pushOtherWorkspaceNotice(approvalIn('workspace_secret'), const [],
              arabic: false),
          'That notification is for another workspace. Switch to it in Settings to open it.');
    });
  });

  group('in the app', () {
    Future<FakePushMessaging> launch(
        WidgetTester tester, FakeAnumApi api) async {
      SharedPreferences.setMockInitialValues({'anum.onboarding.seen': true});
      FlutterSecureStorage.setMockInitialValues({});
      tester.view
        ..physicalSize = phone * 3
        ..devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      addTearDown(api.close);
      final messaging = FakePushMessaging(current: PushPermission.denied);
      await tester.pumpWidget(AnumApp(
        environment: AnumEnvironment(
          apiUrl: 'http://localhost:8000/',
          httpClient: () => api.client,
          pushMessaging: messaging,
        ),
      ));
      await pumpFor(tester);
      await signIn(tester, api);
      await waitFor(tester, find.text("Today's Focus"), api: api);
      return messaging;
    }

    FakeAnumApi apiWithTwoWorkspaces() {
      final api = FakeAnumApi();
      seedBaseline(api);
      WorkspaceState().install(api);
      api.get(
          '/api/v1/me/workspace-memberships',
          (_) => FakeResponse.json({
                'data': [
                  {
                    'tenant_id': Fx.tenantId,
                    'workspace_id': Fx.workspaceId,
                    'role': 'owner',
                    'workspace_name': 'Foundation',
                  },
                  {
                    'tenant_id': Fx.tenantId,
                    'workspace_id': 'workspace_riyadh',
                    'role': 'member',
                    'workspace_name': 'Riyadh branch',
                  },
                ]
              }));
      return api;
    }

    IncomingPush approvalPush(String workspace, String tenant) =>
        IncomingPush(data: {
          'anum_kind': 'approval_required',
          'workspace_id': workspace,
          'tenant_id': tenant,
          'approval_id': 'approval_pending',
        });

    testWidgets('a push for another organization does not open here',
        (tester) async {
      final api = apiWithTwoWorkspaces();
      final messaging = await launch(tester, api);
      // Same workspace id, another organization.
      messaging.opened.add(approvalPush(Fx.workspaceId, 'tenant_other'));
      await waitFor(tester,
          find.textContaining('That notification is for another workspace'),
          api: api);
      expect(find.textContaining(Fx.workspaceId), findsNothing);
      expect(find.textContaining('tenant_other'), findsNothing);
      expect(find.text("Today's Focus"), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a push for another workspace names it', (tester) async {
      final api = apiWithTwoWorkspaces();
      final messaging = await launch(tester, api);
      messaging.opened.add(approvalPush('workspace_riyadh', Fx.tenantId));
      await waitFor(
          tester,
          find.text('That notification is for the workspace "Riyadh branch". '
              'Switch to it in Settings to open it.'),
          api: api);
      expect(find.textContaining('workspace_riyadh'), findsNothing);
    });

    testWidgets('a push for this workspace opens Approvals', (tester) async {
      final api = apiWithTwoWorkspaces();
      final messaging = await launch(tester, api);
      messaging.opened.add(approvalPush(Fx.workspaceId, Fx.tenantId));
      await waitFor(tester, find.text('Publish the release notes'), api: api);
      expect(find.textContaining('That notification is for'), findsNothing);
    });
  });
}
