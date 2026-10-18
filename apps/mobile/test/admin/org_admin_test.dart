// Organization admin screens in the mobile client: contact opt-outs
// (docs/privacy.md#contact-suppression), this workspace's lifecycle
// (docs/identity.md#workspace-lifecycle), setup plans (docs/setup-plan.md)
// and the read-only org chart (docs/org-chart.md). Wire contract, every
// state, the API's 403, and layouts from 320 px at 200% text to 1440 px in
// English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/admin/admin.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/settings/settings_controller.dart';
import 'package:anum_mobile/features/settings/settings_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/org_admin_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// ------------------------------------------------------------------ fixtures

JsonMap entryJson(int i, {String channel = 'whatsapp'}) => {
      'id': 'supp_$i',
      'masked': '+971•••${(1000 + i)}',
      'kind': 'phone',
      'channel': channel,
      'purpose': i.isEven ? 'all' : 'marketing',
      'source': i == 0 ? 'whatsapp_keyword' : 'manual',
      'reason': i == 0 ? '' : 'Asked by phone $i',
      'created_by': i == 0 ? 'system:inbound' : 'user_owner',
      'created_at': '2026-10-0${1 + i % 9}T10:00:00Z',
    };

JsonMap workspaceJson({String name = 'Tyre Shop', bool archived = false}) => {
      'id': 'workspace_test',
      'tenant_id': 'tenant_test',
      'name': name,
      'created_at': '2026-09-01T10:00:00Z',
      'updated_at': '2026-10-01T10:00:00Z',
      'archived_at': archived ? '2026-10-10T10:00:00Z' : null,
      'archived_by': archived ? 'user_owner' : null,
    };

JsonMap planSummaryJson() => {
      'id': 'shahzeb_business_group',
      'version': 2,
      'name': 'Business group setup',
      'summary': 'Agents and rules for the whole group.',
      'currency': 'AED',
      'timezone': 'Asia/Dubai',
      'business_hours': 'Sun to Thu 9:00 to 18:00',
      'pilot_agents': ['Sales lead'],
      'weeks': [
        {'week': 1, 'title': 'Pilot'},
        {'week': 2, 'title': 'Support'},
      ],
      'counts': {'agents': 20, 'skills': 5},
      'applied_items': 0,
      'last_applied_at': null,
    };

JsonMap previewJson(String phase) => {
      'plan': planSummaryJson(),
      'phase': phase,
      'overwrite_edited': false,
      'access': {
        'owner': true,
        'organization_admin': false,
        'can_apply': true,
        'notes': [
          'Rules for the whole organization need an organization administrator.'
        ],
      },
      'budget_aed': 1500.0,
      'budget_usd': 408.4,
      'items': [
        for (var i = 0; i < 20; i++)
          {
            'key': 'agent_$i',
            'section': 'agents',
            'kind': 'agent',
            'name': 'Agent $i',
            'action': i < 15 ? 'create' : 'present',
            'reason_code': i < 15 ? 'new' : 'already_applied',
            'reason': 'Creates the agent.',
            'week': i < 3 ? 1 : 2,
            'facts': i == 0 ? ['Autonomy: draft only'] : <String>[],
          },
        {
          'key': 'rule_1',
          'section': 'governance',
          'kind': 'rule',
          'name': 'Refunds need approval',
          'action': 'skipped',
          'reason_code': 'needs_org_admin',
          'reason': 'Needs org admin.',
          'week': null,
          'facts': <String>[],
        },
      ],
      'sections': <Object>[],
      'approval_thresholds': [
        {
          'label': 'Refunds over AED 500',
          'plan': 'Finance lead approves',
          'support': 'partial',
          'anum': 'Approvals by role only.'
        }
      ],
      'memory_rules': <Object>[],
    };

// ---------------------------------------------------------------- fake repo

class FakeOrgAdmin implements OrgAdminRepository {
  int suppressionTotal = 60;
  bool deny = false;
  bool fail = false;
  bool canManage = true;
  bool archived = false;
  String name = 'Tyre Shop';
  bool noPlans = false;
  bool emptyChart = false;
  bool peopleFailed = false;
  final calls = <String>[];

  void _guard() {
    if (deny) throw const ApiException(403, 'Only owners may do this.');
    if (fail) throw const ApiException(503, 'Service unavailable.');
  }

  @override
  Future<SuppressionPage> suppressions(
      {String query = '',
      String? channel,
      int offset = 0,
      int limit = 50}) async {
    calls.add('list q=$query ch=$channel off=$offset');
    _guard();
    final all = [
      for (var i = 0; i < suppressionTotal; i++)
        if (query.isEmpty || '+971•••${1000 + i}'.contains(query)) i
    ];
    return SuppressionPage.fromJson(JsonReader({
      'items': [for (final i in all.skip(offset).take(limit)) entryJson(i)],
      'total': all.length,
      'can_manage': true,
      'can_export': false,
    }));
  }

  @override
  Future<({SuppressionEntry entry, bool created})> addSuppression(
      SuppressionDraft draft) async {
    calls.add('add ${draft.recipient} ${draft.channel} ${draft.reason}');
    _guard();
    suppressionTotal += 1;
    return (
      entry: SuppressionEntry.fromJson(JsonReader(entryJson(99))),
      created: true
    );
  }

  @override
  Future<void> removeSuppression(String id, String reason) async {
    calls.add('remove $id $reason');
    _guard();
    suppressionTotal -= 1;
  }

  @override
  Future<WorkspaceInfo> currentWorkspace() async {
    _guard();
    return WorkspaceInfo.fromJson(
        JsonReader(workspaceJson(name: name, archived: archived)));
  }

  @override
  Future<WorkspacePermissions> workspacePermissions() async =>
      WorkspacePermissions.fromJson(JsonReader({
        'workspace_id': 'workspace_test',
        'role': canManage ? 'owner' : 'viewer',
        'organization_admin': false,
        'can_manage_workspace': canManage,
      }));

  @override
  Future<WorkspaceInfo> renameWorkspace(String name) async {
    calls.add('rename $name');
    this.name = name;
    return currentWorkspace();
  }

  @override
  Future<WorkspaceInfo> archiveWorkspace(String confirmName) async {
    calls.add('archive $confirmName');
    archived = true;
    return currentWorkspace();
  }

  @override
  Future<WorkspaceInfo> restoreWorkspace() async {
    calls.add('restore');
    archived = false;
    return currentWorkspace();
  }

  @override
  Future<List<SetupPlanSummary>> setupPlans() async {
    _guard();
    return noPlans
        ? const []
        : [SetupPlanSummary.fromJson(JsonReader(planSummaryJson()))];
  }

  @override
  Future<SetupPlanPreview> setupPlanPreview(String id,
      {String phase = 'pilot'}) async {
    calls.add('preview $id $phase');
    _guard();
    return SetupPlanPreview.fromJson(JsonReader(previewJson(phase)));
  }

  @override
  Future<OrgChartData> orgChart() async {
    _guard();
    if (emptyChart) return const OrgChartData(members: []);
    return OrgChartData(peopleFailed: peopleFailed, members: [
      for (var i = 0; i < 11; i++)
        OrgMember(
            kind: OrgMemberKind.agent,
            id: 'a$i',
            name: 'Sales agent $i',
            department: 'Sales',
            description: 'Follows up leads',
            active: i != 1,
            attention: i == 2),
      const OrgMember(
          kind: OrgMemberKind.agent,
          id: 'x',
          name: 'Marketing writer',
          department: 'Brand Studio'),
      const OrgMember(
          kind: OrgMemberKind.agent,
          id: 'y',
          name: 'Loose agent',
          department: null),
      const OrgMember(
          kind: OrgMemberKind.assistant,
          id: 's',
          name: 'Support desk',
          department: 'support',
          active: false),
      const OrgMember(
          kind: OrgMemberKind.person,
          id: 'u',
          name: 'Aisha',
          department: 'finance',
          jobTitle: 'Accountant',
          role: 'member'),
    ]);
  }
}

// ---------------------------------------------------------------- transport

class ScriptTransport implements ApiTransport {
  ScriptTransport(this.answer);
  final ApiResponse Function(ApiRequest request) answer;
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    return answer(request);
  }
}

Future<ApiOrgAdminRepository> repo(ScriptTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'token',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_test',
          workspaceId: 'workspace_test',
          userId: 'user_owner',
          roles: ['owner'])));
  return ApiOrgAdminRepository(AnumApiClient(
      baseUri: Uri.parse('http://localhost:8000'),
      transport: transport,
      sessions: sessions));
}

// -------------------------------------------------------------------- pump

Future<void> pump(WidgetTester tester, Widget home,
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
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: home,
  ));
  await tester.pumpAndSettle();
}

void main() {
  group('strings', () {
    test('English and Arabic have the same keys and placeholders', () {
      expect(orgAdminAr.keys.toSet(), orgAdminEn.keys.toSet());
      final names = RegExp(r'\{(\w+)\}');
      Set<String> of(String text) =>
          names.allMatches(text).map((m) => m.group(1)!).toSet();
      for (final key in orgAdminEn.keys) {
        expect(of(orgAdminAr[key]!), of(orgAdminEn[key]!), reason: key);
      }
    });

    test('no em or en dashes', () {
      for (final text in [...orgAdminEn.values, ...orgAdminAr.values]) {
        expect(text.contains('\u2013') || text.contains('\u2014'), isFalse,
            reason: text);
      }
    });
  });

  group('rules', () {
    test('contact and channel checks match the API', () {
      expect(contactKind('+971 50 123 4567'), 'phone');
      expect(contactKind('050 123 4567'), 'phone');
      expect(contactKind('<a@b.co>'), 'email');
      expect(contactKind('a@b'), isNull);
      expect(contactKind('1234'), isNull);
      expect(
          suppressionProblem(const SuppressionDraft(
              recipient: 'a@b.co', channel: 'sms', reason: 'asked')),
          SuppressionProblem.channel);
      expect(
          suppressionProblem(const SuppressionDraft(
              recipient: '+971501234567', channel: 'email', reason: 'asked')),
          SuppressionProblem.channel);
      expect(
          suppressionProblem(
              const SuppressionDraft(recipient: '+971501234567', reason: 'no')),
          SuppressionProblem.reason);
      expect(
          suppressionProblem(const SuppressionDraft(
              recipient: '+971501234567',
              channel: 'whatsapp',
              reason: 'asked')),
          isNull);
    });

    test('workspace names and archive confirmation', () {
      expect(cleanWorkspaceName('  Tyre   Shop '), 'Tyre Shop');
      expect(cleanWorkspaceName('   '), isNull);
      expect(cleanWorkspaceName('x' * 161), isNull);
      expect(archiveConfirmed(' Tyre Shop ', 'Tyre Shop'), isTrue);
      expect(archiveConfirmed('tyre shop', 'Tyre Shop'), isFalse);
    });

    test('org chart groups known, free-text and missing departments', () {
      final groups = groupOrgChart(const [
        OrgMember(
            kind: OrgMemberKind.person,
            id: 'p',
            name: 'P',
            department: 'finance'),
        OrgMember(
            kind: OrgMemberKind.agent, id: 'n', name: 'N', department: null),
        OrgMember(
            kind: OrgMemberKind.agent,
            id: 'b',
            name: 'B',
            department: 'Brand  studio'),
        OrgMember(
            kind: OrgMemberKind.agent,
            id: 'c',
            name: 'C',
            department: 'brand studio'),
        OrgMember(
            kind: OrgMemberKind.agent, id: 's', name: 'S', department: 'Sales'),
        OrgMember(
            kind: OrgMemberKind.agent,
            id: 't',
            name: 'T',
            department: 'Tyre engineering'),
      ]);
      expect(groups.map((g) => g.key).toList(), [
        'sales',
        'tyre_engineering',
        'finance',
        'text:brand studio',
        'none'
      ]);
      expect(groups[3].members.length, 2);
    });
  });

  group('repository wire contract', () {
    test('suppressions: query, 201 against 200, delete with a reason',
        () async {
      var status = 201;
      final transport = ScriptTransport((r) => switch (r.method) {
            'GET' => ApiResponse(statusCode: 200, body: {
                'items': [entryJson(1)],
                'total': 1,
                'can_manage': true,
                'can_export': true
              }),
            'POST' => ApiResponse(statusCode: status, body: entryJson(1)),
            _ => const ApiResponse(statusCode: 204),
          });
      final api = await repo(transport);
      final page =
          await api.suppressions(query: ' +971 ', channel: 'sms', offset: 50);
      expect(page.canExport, isTrue);
      expect(transport.requests.last.uri.queryParameters,
          {'limit': '50', 'offset': '50', 'q': '+971', 'channel': 'sms'});
      const draft =
          SuppressionDraft(recipient: '+971501234567', reason: 'asked');
      expect((await api.addSuppression(draft)).created, isTrue);
      expect(transport.requests.last.body, draft.toJson());
      status = 200;
      expect((await api.addSuppression(draft)).created, isFalse);
      await api.removeSuppression('supp 1', 'Asked to be messaged again');
      expect(transport.requests.last.method, 'DELETE');
      expect(transport.requests.last.uri.path,
          '/api/v1/contact-suppressions/supp%201');
      expect(transport.requests.last.body,
          {'reason': 'Asked to be messaged again'});
    });

    test('workspace lifecycle routes and bodies', () async {
      final transport = ScriptTransport((r) => ApiResponse(
          statusCode: 200,
          body: r.uri.path.endsWith('permissions')
              ? {'role': 'owner', 'can_manage_workspace': true}
              : workspaceJson()));
      final api = await repo(transport);
      expect((await api.workspacePermissions()).canManageWorkspace, isTrue);
      await api.renameWorkspace('New name');
      expect(transport.requests.last.method, 'PATCH');
      expect(transport.requests.last.body, {'name': 'New name'});
      await api.archiveWorkspace('Tyre Shop');
      expect(transport.requests.last.uri.path,
          '/api/v1/workspaces/current/archive');
      expect(transport.requests.last.body, {'confirm_name': 'Tyre Shop'});
      await api.restoreWorkspace();
      expect(transport.requests.last.uri.path,
          '/api/v1/workspaces/current/restore');
    });

    test('setup plans list (a JSON list) and preview', () async {
      final transport = ScriptTransport((r) => ApiResponse(
          statusCode: 200,
          body: r.uri.path.endsWith('preview')
              ? previewJson(r.uri.queryParameters['phase']!)
              : {
                  'data': [planSummaryJson()]
                }));
      final api = await repo(transport);
      final plans = await api.setupPlans();
      expect(plans.single.totalItems, 25);
      final preview = await api.setupPlanPreview(plans.single.id, phase: 'all');
      expect(preview.phase, 'all');
      expect(transport.requests.last.uri.queryParameters,
          {'phase': 'all', 'overwrite_edited': 'false'});
      expect((preview.changes, preview.present, preview.skipped), (15, 5, 1));
    });

    test('org chart pages agents and survives missing people', () async {
      final transport = ScriptTransport((r) {
        if (r.uri.path == '/api/v1/agents') {
          final second = r.uri.queryParameters['cursor'] == 'next';
          return ApiResponse(statusCode: 200, body: {
            'items': [
              {
                'id': second ? 'a2' : 'a1',
                'name': second ? 'Two' : 'One',
                'department': 'sales',
                'status': second ? 'paused' : 'active',
                'unavailable_tools': second ? <String>[] : ['gmail.send'],
              }
            ],
            'next_cursor': second ? null : 'next',
            'counts': {'all': 2},
          });
        }
        if (r.uri.path == '/api/v1/assistants') {
          return const ApiResponse(statusCode: 200, body: {
            'data': [
              {
                'id': 's',
                'name': 'Desk',
                'department': 'support',
                'enabled': true
              }
            ]
          });
        }
        return const ApiResponse(
            statusCode: 403, body: {'detail': 'Not allowed'});
      });
      final data = await (await repo(transport)).orgChart();
      expect(data.members.map((m) => m.name), ['One', 'Two', 'Desk']);
      expect(data.members[0].attention, isTrue);
      expect(data.members[1].active, isFalse);
      expect(data.peopleFailed, isTrue);
      expect(data.assistantsFailed, isFalse);
      expect(data.truncated, isFalse);
    });

    test('org chart: a 403 on agents is the screen\'s permission state',
        () async {
      final api = await repo(ScriptTransport(
          (_) => const ApiResponse(statusCode: 403, body: {'detail': 'No'})));
      expect(api.orgChart(), throwsA(isA<ApiException>()));
    });
  });

  group('contact opt-outs screen', () {
    testWidgets('lists, pages, searches, adds and removes', (tester) async {
      final fake = FakeOrgAdmin();
      await pump(
          tester, SuppressionsScreen(controller: SuppressionsController(fake)));
      expect(find.text('Contacts on the list: 60'), findsOneWidget);
      await tester.ensureVisible(find.text('Show more'));
      await tester.pumpAndSettle();
      // Past the floating Add button.
      await tester.drag(find.byType(Scrollable).first, const Offset(0, -300));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Show more'));
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'list q= ch=null off=50');
      expect(find.text('Show more'), findsNothing);

      await tester.enterText(find.byKey(const Key('supp-search')), '1005');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(find.text('Contacts that match: 1'), findsOneWidget);

      await tester.enterText(find.byKey(const Key('supp-search')), 'nobody');
      await tester.tap(find.text('Find'));
      await tester.pumpAndSettle();
      expect(find.text('No contact matches'), findsOneWidget);
      await tester.tap(find.text('Clear').last);
      await tester.pumpAndSettle();
      expect(find.text('Contacts on the list: 60'), findsOneWidget);

      // Add: the form checks before sending.
      await tester.tap(find.byKey(const Key('supp-add')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('supp-contact')), 'a@b.co');
      await tester.enterText(find.byKey(const Key('supp-reason')), 'ok');
      await tester.tap(find.byKey(const Key('supp-save')));
      await tester.pumpAndSettle();
      expect(
          find.text('Give a reason of at least 3 characters.'), findsOneWidget);
      expect(fake.calls.where((c) => c.startsWith('add')), isEmpty);
      await tester.enterText(
          find.byKey(const Key('supp-contact')), '+971 50 123 4567');
      await tester.enterText(
          find.byKey(const Key('supp-reason')), 'Asked by phone');
      await tester.tap(find.byKey(const Key('supp-save')));
      await tester.pumpAndSettle();
      expect(fake.calls.firstWhere((c) => c.startsWith('add')),
          'add +971 50 123 4567 all Asked by phone');
      expect(find.text('+971•••1099 is on the list.'), findsOneWidget);
      expect(find.text('Contacts on the list: 61'), findsOneWidget);

      // Remove needs a reason.
      await tester.tap(find.byTooltip('Remove +971•••1000 from the list'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('supp-remove-confirm')));
      await tester.pumpAndSettle();
      expect(
          find.text('Give a reason of at least 3 characters.'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('supp-remove-reason')), 'Customer asked again');
      await tester.tap(find.byKey(const Key('supp-remove-confirm')));
      await tester.pumpAndSettle();
      expect(fake.calls.contains('remove supp_0 Customer asked again'), isTrue);
      expect(
          find.text('+971•••1000 was removed from the list.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty, denied and error states', (tester) async {
      final fake = FakeOrgAdmin()..suppressionTotal = 0;
      final controller = SuppressionsController(fake);
      await pump(tester, SuppressionsScreen(controller: controller));
      expect(find.text('Nobody has opted out yet'), findsOneWidget);
      expect(find.text('Add an opt-out'), findsWidgets);
      fake.deny = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('Owners and organization administrators only'),
          findsOneWidget);
      expect(
          find.text('The API said: Only owners may do this.'), findsOneWidget);
      expect(find.byKey(const Key('supp-add')), findsNothing);
      fake
        ..deny = false
        ..fail = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('The list could not be loaded'), findsOneWidget);
      fake.fail = false;
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Nobody has opted out yet'), findsOneWidget);
    });
  });

  group('workspace screen', () {
    testWidgets('owner renames, archives with the typed name and restores',
        (tester) async {
      final fake = FakeOrgAdmin();
      await pump(
          tester,
          WorkspaceSettingsScreen(
              controller: WorkspaceSettingsController(fake)));
      expect(find.text('Tyre Shop'), findsWidgets);
      expect(find.text('Active'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('ws-name')), '   ');
      await tester.pump();
      await tester.tap(find.byKey(const Key('ws-save-name')));
      await tester.pumpAndSettle();
      expect(find.text('Enter a workspace name.'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('ws-name')), ' Tyre   Hub ');
      await tester.tap(find.byKey(const Key('ws-save-name')));
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'rename Tyre Hub');
      expect(find.text('Workspace renamed to Tyre Hub.'), findsOneWidget);

      await tester.ensureVisible(find.byKey(const Key('ws-archive')));
      await tester.tap(find.byKey(const Key('ws-archive')));
      await tester.pumpAndSettle();
      final confirm = find.byKey(const Key('ws-archive-confirm'));
      expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
      await tester.enterText(
          find.byKey(const Key('ws-archive-name')), 'tyre hub');
      await tester.pump();
      expect(find.text('The name does not match yet.'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('ws-archive-name')), 'Tyre Hub');
      await tester.pump();
      await tester.tap(confirm);
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'archive Tyre Hub');
      expect(find.text('Archived'), findsOneWidget);
      expect(find.byKey(const Key('ws-name')), findsNothing);
      await tester.ensureVisible(find.byKey(const Key('ws-restore')));
      await tester.tap(find.byKey(const Key('ws-restore')));
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'restore');
      expect(find.text('Workspace restored. It is writable again.'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('a viewer sees why the controls are missing; denied and error',
        (tester) async {
      final fake = FakeOrgAdmin()..canManage = false;
      final controller = WorkspaceSettingsController(fake);
      await pump(tester, WorkspaceSettingsScreen(controller: controller));
      expect(find.textContaining('Ask a workspace owner.'), findsOneWidget);
      expect(find.byKey(const Key('ws-name')), findsNothing);
      expect(find.byKey(const Key('ws-archive')), findsNothing);
      expect(find.text('Your role here: viewer'), findsOneWidget);
      fake.deny = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(
          find.text('You are not a member of this workspace'), findsOneWidget);
      fake
        ..deny = false
        ..fail = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('The workspace could not be loaded'), findsOneWidget);
    });
  });

  group('setup plans', () {
    testWidgets('list opens a preview with phases, search, sections and paging',
        (tester) async {
      final fake = FakeOrgAdmin();
      await pump(
          tester, SetupPlansScreen(controller: SetupPlansController(fake)));
      expect(find.text('Business group setup'), findsOneWidget);
      expect(find.text('Not applied here yet. 25 items.'), findsOneWidget);
      await tester.tap(find.text('Business group setup'));
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'preview shahzeb_business_group pilot');
      expect(find.text('Items: 21'), findsOneWidget);
      await tester.ensureVisible(find.text('Show 9 more'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Show 9 more'));
      await tester.pumpAndSettle();
      expect(find.text('Show 9 more'), findsNothing);
      await tester.ensureVisible(find.byKey(const Key('plan-search')));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('plan-search')), 'refunds');
      await tester.pumpAndSettle();
      expect(find.text('Items: 1'), findsOneWidget);
      // A fixed reason reads in the app's language.
      expect(find.textContaining('Needs an organization administrator.'),
          findsOneWidget);
      await tester.enterText(find.byKey(const Key('plan-search')), 'zzz');
      await tester.pumpAndSettle();
      expect(find.text('No item matches'), findsOneWidget);
      await tester.tap(find.text('Clear filters'));
      await tester.pumpAndSettle();
      expect(find.text('Items: 21'), findsOneWidget);
      await tester.ensureVisible(
          find.widgetWithText(ChoiceChip, 'Rules and approvals'));
      await tester.tap(find.widgetWithText(ChoiceChip, 'Rules and approvals'));
      await tester.pumpAndSettle();
      expect(find.text('Items: 1'), findsOneWidget);
      await tester.ensureVisible(find.text('Whole plan'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Whole plan'));
      await tester.pumpAndSettle();
      expect(fake.calls.last, 'preview shahzeb_business_group all');
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty, denied and error states', (tester) async {
      final fake = FakeOrgAdmin()..noPlans = true;
      final controller = SetupPlansController(fake);
      await pump(tester, SetupPlansScreen(controller: controller));
      expect(find.text('No setup plan is available on this server'),
          findsOneWidget);
      fake.deny = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.textContaining('Only a workspace owner or an organization'),
          findsOneWidget);
      fake
        ..deny = false
        ..fail = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('The setup plan could not be loaded'), findsOneWidget);
    });
  });

  group('org chart', () {
    testWidgets('departments, statuses, search, paging and a partial load',
        (tester) async {
      final fake = FakeOrgAdmin()..peopleFailed = true;
      await pump(tester, OrgChartScreen(controller: OrgChartController(fake)),
          size: const Size(1024, 1400));
      expect(find.text('Sales'), findsOneWidget);
      expect(find.text('Brand Studio'), findsOneWidget);
      expect(find.text('No department yet'), findsOneWidget);
      expect(find.text('Paused'), findsOneWidget);
      expect(find.text('Needs a connection'), findsOneWidget);
      expect(find.text('Off'), findsOneWidget);
      expect(find.textContaining('People could not be loaded'), findsOneWidget);
      expect(find.text('Show 3 more'), findsOneWidget);
      await tester.tap(find.text('Show 3 more'));
      await tester.pumpAndSettle();
      expect(find.text('Sales agent 10'), findsOneWidget);
      await tester.enterText(find.byKey(const Key('org-search')), 'accountant');
      await tester.pumpAndSettle();
      expect(find.text('Aisha'), findsOneWidget);
      expect(find.text('Sales'), findsNothing);
      await tester.enterText(
          find.byKey(const Key('org-search')), 'nobody here');
      await tester.pumpAndSettle();
      expect(find.text('Nobody matches'), findsOneWidget);
      await tester.tap(find.text('Clear search'));
      await tester.pumpAndSettle();
      expect(find.text('Sales'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('empty, denied and error states', (tester) async {
      final fake = FakeOrgAdmin()..emptyChart = true;
      final controller = OrgChartController(fake);
      await pump(tester, OrgChartScreen(controller: controller));
      expect(find.text('Your AI team is empty'), findsOneWidget);
      fake.deny = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.textContaining("cannot see this workspace's agents"),
          findsOneWidget);
      fake
        ..deny = false
        ..fail = true;
      await controller.load();
      await tester.pumpAndSettle();
      expect(find.text('The org chart could not be loaded'), findsOneWidget);
    });
  });

  testWidgets('Settings opens each screen from Workspace administration',
      (tester) async {
    final transport = ScriptTransport((r) {
      final path = r.uri.path;
      if (path.endsWith('/me/preferences')) {
        return const ApiResponse(statusCode: 200, body: {
          'remember_conversations': true,
          'updated_at': null,
          'conversation_memory': 'on',
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
      if (path == '/api/v1/contact-suppressions') {
        return const ApiResponse(
            statusCode: 403, body: {'detail': 'Owners only.'});
      }
      return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
    });
    final orgRepo = await repo(transport);
    final api = orgRepo.api;
    final settings = SettingsController(
        AuthRepository(api: api, sessions: api.sessions),
        admin: ApiAdminRepository(api),
        orgAdmin: orgRepo);
    await pump(tester, SettingsScreen(controller: settings));
    for (final (key, title) in [
      ('open-workspace-settings', 'This workspace'),
      ('open-suppressions', 'Contact opt-outs'),
      ('open-setup-plans', 'Setup plans'),
      ('open-org-chart', 'Org chart'),
    ]) {
      final tile = find.byKey(Key(key));
      await tester.scrollUntilVisible(tile, 200,
          scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(tile);
      await tester.pumpAndSettle();
      await tester.tap(tile);
      await tester.pumpAndSettle();
      expect(find.widgetWithText(AppBar, title), findsOneWidget);
      if (key == 'open-suppressions') {
        expect(find.text('Owners and organization administrators only'),
            findsOneWidget);
      }
      await tester.pageBack();
      await tester.pumpAndSettle();
    }
  });

  group('layouts', () {
    final screens = <String, Widget Function(FakeOrgAdmin)>{
      'opt-outs': (f) =>
          SuppressionsScreen(controller: SuppressionsController(f)),
      'workspace': (f) =>
          WorkspaceSettingsScreen(controller: WorkspaceSettingsController(f)),
      'plans': (f) => SetupPlansScreen(controller: SetupPlansController(f)),
      'plan detail': (f) => SetupPlanDetailScreen(
          controller: SetupPlanDetailController(f, 'shahzeb_business_group'),
          title: 'Business group setup'),
      'org chart': (f) => OrgChartScreen(controller: OrgChartController(f)),
    };
    for (final MapEntry(key: name, value: build) in screens.entries) {
      for (final (size, scale, locale) in [
        (const Size(320, 640), 2.0, const Locale('en')),
        (const Size(320, 640), 2.0, const Locale('ar')),
        (const Size(390, 844), 1.0, const Locale('ar')),
        (const Size(768, 1024), 1.0, const Locale('en')),
        (const Size(1440, 900), 1.0, const Locale('en')),
      ]) {
        testWidgets(
            '$name fits at ${size.width.toInt()} px, text x$scale, ${locale.languageCode}',
            (tester) async {
          await pump(tester, build(FakeOrgAdmin()),
              size: size, textScale: scale, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale.languageCode == 'ar') {
            expect(
                Directionality.of(tester.element(find.byType(Scaffold).first)),
                TextDirection.rtl);
          }
        });
      }
    }
  });
}
