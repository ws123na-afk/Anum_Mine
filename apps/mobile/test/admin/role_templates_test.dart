// Role templates on the phone's Members screen
// (docs/governance-and-scale.md#role-templates): the member's template and
// what applies, giving and removing a template with a confirmation, owners
// without the choice, templates that fail to load leaving the list usable,
// and layouts from 320 to 1024 wide at 2x text in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/admin/admin_controller.dart';
import 'package:anum_mobile/features/admin/admin_repository.dart';
import 'package:anum_mobile/features/admin/members_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../lists/lists_harness.dart';

JsonMap _member(String id, String role,
        {String? template, String? templateName, List<String>? effective}) =>
    {
      'user_id': id,
      'workspace_id': 'w',
      'role': role,
      'active': true,
      'created_at': '2026-10-01T08:00:00Z',
      'updated_at': '2026-10-01T08:00:00Z',
      'role_template_id': template,
      'role_template_name': templateName,
      'effective_permissions': effective,
    };

class _Api {
  int templatesStatus = 200;
  final puts = <(String, Object?)>[];
  List<JsonMap> members = [
    _member('user_me', 'owner'),
    _member('user_b', 'member',
        template: 'role_read',
        templateName: 'Read only',
        effective: ['task:read']),
    _member('user_c', 'viewer'),
  ];

  ApiResponse answer(ApiRequest r) {
    final path = r.uri.path;
    if (path == '/api/v1/workspace-members') {
      return ApiResponse(statusCode: 200, body: {'data': members});
    }
    if (path == '/api/v1/workspace-invitations') {
      return const ApiResponse(statusCode: 200, body: {'data': <Object>[]});
    }
    if (path.endsWith('/email-status')) {
      return const ApiResponse(
          statusCode: 200, body: {'configured': false, 'link_base': null});
    }
    if (path == '/api/v1/role-templates') {
      if (templatesStatus != 200) {
        return ApiResponse(statusCode: templatesStatus, body: const {
          'error': {'code': 'x', 'message': 'Governance store unavailable'}
        });
      }
      return const ApiResponse(statusCode: 200, body: {
        'data': [
          {
            'id': 'role_read',
            'name': 'Read only',
            'permissions': ['task:read', 'task:run'],
            'tenant_id': 't',
            'created_at': '2026-10-01T08:00:00Z'
          },
          {
            'id': 'role_ops',
            'name': 'Operations',
            'permissions': ['task:read', 'task:create'],
            'tenant_id': 't',
            'created_at': '2026-10-01T08:00:00Z'
          },
        ]
      });
    }
    if (path.endsWith('/role-template') && r.method == 'PUT') {
      final user = r.uri.pathSegments[r.uri.pathSegments.length - 2];
      puts.add((user, r.body!['role_template_id']));
      members = [
        for (final m in members)
          m['user_id'] == user
              ? _member(user, m['role']! as String,
                  template: r.body!['role_template_id'] as String?,
                  templateName:
                      r.body!['role_template_id'] == null ? null : 'Operations',
                  effective: r.body!['role_template_id'] == null
                      ? null
                      : ['task:read'])
              : m
      ];
      return ApiResponse(
          statusCode: 200,
          body: members.firstWhere((m) => m['user_id'] == user));
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<MembersController> _pump(WidgetTester tester, _Api fake,
    {double width = 390, double text = 1, String locale = 'en'}) async {
  final api = await scriptedClient(ScriptedTransport(fake.answer));
  final repo = ApiAdminRepository(api);
  final c = MembersController(repo);
  await tester.pumpWidget(const SizedBox());
  await pumpList(tester,
      MembersScreen(controller: c, repository: repo, currentWorkspaceId: 'w'),
      width: width, height: 2400, text: text, locale: locale, scaffold: false);
  return c;
}

void main() {
  test('the member reads its template; the repository sends the PUT', () async {
    final fake = _Api();
    final api = await scriptedClient(ScriptedTransport(fake.answer));
    final repo = ApiAdminRepository(api);
    final members = await repo.members();
    expect(members[1].roleTemplateName, 'Read only');
    expect(members[1].effectivePermissions, ['task:read']);
    expect(members[2].roleTemplateId, isNull);
    final templates = await repo.roleTemplates();
    expect(templates.map((t) => t.name), ['Read only', 'Operations']);
    await repo.setRoleTemplate('user_c', 'role_ops');
    await repo.setRoleTemplate('user_b', null);
    expect(fake.puts, [('user_c', 'role_ops'), ('user_b', null)]);
  });

  testWidgets('give a template, see what applies, take it away',
      (tester) async {
    final fake = _Api();
    final c = await _pump(tester, fake);
    // What the member's template does, including what the role cuts away.
    expect(
        find.textContaining(
            'Template: Read only · Permissions that apply: 1 · Outside the member role, so they do not apply: task:run'),
        findsOneWidget);
    // Owners have no template choice.
    await tester.tap(find.byTooltip('Actions for user_c'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('member-template-user_c')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('template-role_ops')));
    await tester.pumpAndSettle();
    expect(find.text('Give user_c the template Operations?'), findsOneWidget);
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(fake.puts.single, ('user_c', 'role_ops'));
    expect(find.text('user_c now has the role template Operations.'),
        findsOneWidget);

    await tester.tap(find.byTooltip('Actions for user_b'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('member-template-user_b')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('template-none')));
    await tester.pumpAndSettle();
    expect(find.text('Remove the role template from user_b?'), findsOneWidget);
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(fake.puts.last, ('user_b', null));
    expect(c.notice, 'user_b no longer has a role template.');
  });

  testWidgets('owners have no template choice; a failed load hides it',
      (tester) async {
    final fake = _Api();
    await _pump(tester, fake);
    await tester.tap(find.byTooltip('Actions for user_me'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('member-template-user_me')), findsNothing);
    await tester.tapAt(const Offset(5, 5));
    await tester.pumpAndSettle();
    final failing = _Api()..templatesStatus = 503;
    final c = await _pump(tester, failing);
    expect(c.templates, isNull);
    expect(c.templatesError, 'Governance store unavailable');
    expect(find.text('user_b'), findsOneWidget);
    await tester.tap(find.byTooltip('Actions for user_b'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('member-template-user_b')), findsNothing);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0]) {
    testWidgets(
        'the template sheet fits at $width wide, 2x, English and Arabic',
        (tester) async {
      for (final locale in ['en', 'ar']) {
        await _pump(tester, _Api(), width: width, text: 2, locale: locale);
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.byTooltip('Actions for user_c'));
        await tester.pumpAndSettle();
        await tester.tap(find.byTooltip('Actions for user_c'));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const Key('member-template-user_c')));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        if (locale == 'ar') {
          expect(find.text('بلا قالب (الدور وحده)'), findsOneWidget);
        }
      }
    });
  }
}
