// The Approvals settings sections on the phone
// (docs/approvals-and-risk.md#approvals-settings): the organization's rules
// read only with the link to the web, the routing worked out from the rules
// and the drafted switches, the reminder for owners and its refusal, each
// section loading and failing on its own, and layouts from 320 to 1024 wide
// at 2x text in English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/admin/admin_controller.dart';
import 'package:anum_mobile/features/admin/admin_repository.dart';
import 'package:anum_mobile/features/admin/approval_policy.dart';
import 'package:anum_mobile/features/admin/approval_settings.dart';
import 'package:anum_mobile/features/admin/policy_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../lists/lists_harness.dart';

const _en = AnumLocalizations(Locale('en'));
const _ar = AnumLocalizations(Locale('ar'));

final _rules = <JsonMap>[
  {
    'id': 'rule_refunds',
    'name': 'Refunds',
    'action_pattern': 'commerce.refund*',
    'minimum_approvers': 1,
    'required_roles': ['owner'],
    'enabled': true,
    'created_at': '2026-10-01T08:00:00Z',
    'conditions': {'amount_above': 1000, 'amount_at_most': 5000},
    'approver_steps': [
      {
        'approvers': ['role:member', 'user:care_lead']
      },
      {
        'approvers': ['owner']
      },
    ],
  },
  {
    'id': 'rule_old',
    'name': 'Old payments',
    'action_pattern': 'payment.*',
    'minimum_approvers': 2,
    'required_roles': ['owner'],
    'enabled': false,
    'created_at': '2026-09-01T08:00:00Z',
  },
];

class _Api {
  String role = 'owner';
  int policyStatus = 200;
  int rulesStatus = 200;
  int reminderStatus = 200;
  int reminderPutStatus = 200;
  List<JsonMap> rules = _rules;
  JsonMap reminder = {
    'reminder_hours': null,
    'updated_by': null,
    'updated_at': null
  };

  ApiResponse _err(int status, String message) =>
      ApiResponse(statusCode: status, body: {
        'error': {'code': 'x', 'message': message}
      });

  ApiResponse answer(ApiRequest r) {
    final path = r.uri.path;
    if (path.endsWith('/approval-policy')) {
      if (policyStatus != 200) return _err(policyStatus, 'Policy unavailable');
      return ApiResponse(statusCode: 200, body: {
        'two_person_rule': false,
        'medium_risk_requires_approval': true,
        'updated_by': null,
        'updated_at': null,
      });
    }
    if (path.endsWith('/workspace-memberships/current')) {
      return ApiResponse(statusCode: 200, body: {
        'user_id': 'user_me',
        'role': role,
        'active': true,
      });
    }
    if (path.endsWith('/workspace-members')) {
      return const ApiResponse(statusCode: 200, body: {'data': <Object>[]});
    }
    if (path.endsWith('/organization/approval-rules')) {
      if (rulesStatus != 200) return _err(rulesStatus, 'Rules unavailable');
      return ApiResponse(statusCode: 200, body: {'data': rules});
    }
    if (path.endsWith('/approval-reminders/settings')) {
      if (r.method == 'PUT') {
        if (reminderPutStatus == 403) {
          return _err(403,
              'Only a workspace owner can change when waiting approvals send a reminder.');
        }
        reminder = {
          'reminder_hours': r.body!['reminder_hours'],
          'updated_by': 'user_me',
          'updated_at': '2026-11-11T09:00:00Z'
        };
        return ApiResponse(statusCode: 200, body: reminder);
      }
      if (reminderStatus != 200) {
        return _err(reminderStatus, 'Reminder unavailable');
      }
      return ApiResponse(statusCode: 200, body: reminder);
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  }
}

Future<(ApprovalPolicyController, ScriptedTransport)> _controller(
    _Api fake) async {
  final transport = ScriptedTransport(fake.answer);
  final api = await scriptedClient(transport);
  return (ApprovalPolicyController(ApiAdminRepository(api)), transport);
}

Future<ApprovalPolicyController> _pump(WidgetTester tester, _Api fake,
    {double width = 390,
    double text = 1,
    String locale = 'en',
    Uri? link}) async {
  final (c, _) = await _controller(fake);
  await tester.pumpWidget(const SizedBox());
  await pumpList(
      tester,
      ApprovalPolicyScreen(
          controller: c, workspaceId: 'workspace_test', governanceLink: link),
      width: width,
      height: 3000,
      text: text,
      locale: locale,
      scaffold: false);
  return c;
}

void main() {
  group('words', () {
    test('bands, steps and the routing summary follow the web', () {
      final rules = [
        for (final r in _rules) ApprovalRuleView.fromJson(JsonReader(r))
      ];
      expect(ruleBand(rules.first.conditions, _en),
          'Above AED 1,000.00, up to AED 5,000.00');
      expect(ruleBand(rules.last.conditions, _en), isNull);
      expect(ruleSteps(rules.first, _en),
          ['Step 1: Members or care_lead', 'Step 2: Owners']);
      expect(ruleSteps(rules.last, _en), ['Owners decide (owner), 2 needed']);
      final summary = routingSummary(
          rules,
          const ApprovalPolicy(
              twoPersonRule: true, mediumRiskRequiresApproval: false),
          _en);
      expect(summary.bands.single, startsWith('Refunds: Above AED 1,000.00'));
      expect(summary.off, 1);
      expect(summary.policy, [
        _en.t('ad.apr.routing.twoPersonOn'),
        _en.t('ad.apr.routing.mediumOff')
      ]);
      expect(routingSummary(rules, null, _ar).policy, isEmpty);
      expect(
          ruleSteps(rules.first, _ar).first, 'الخطوة 1: الأعضاء أو care_lead');
    });

    test('the web link needs an absolute web address', () {
      expect(governanceWebLink('https://app.example.com/').toString(),
          'https://app.example.com/#governance');
      expect(governanceWebLink(''), isNull);
      expect(governanceWebLink('javascript:alert(1)'), isNull);
    });
  });

  group('controllers', () {
    test('sections load on their own; an owner saves the reminder', () async {
      final fake = _Api()..policyStatus = 503;
      final (c, transport) = await _controller(fake);
      await c.loadAll();
      expect(c.phase, AdminPhase.error);
      expect(c.rules.phase, SectionPhase.ready);
      expect(c.rules.rules, hasLength(2));
      expect(c.reminders.phase, SectionPhase.ready);
      c.reminders.choose(4);
      await c.reminders.save(_en);
      expect(transport.requests.last.method, 'PUT');
      expect(transport.requests.last.body, {'reminder_hours': 4});
      expect(c.reminders.notice, 'Approvals now remind owners after 4 hours.');
      c.reminders.choose(null);
      await c.reminders.save(_en);
      expect(transport.requests.last.body, {'reminder_hours': null});
      expect(c.reminders.notice, 'Approval reminders are off.');
      c.dispose();
    });

    test('a refused save turns the reminder read only with the API sentence',
        () async {
      final fake = _Api()..reminderPutStatus = 403;
      final (c, _) = await _controller(fake);
      await c.loadAll();
      c.reminders.choose(6);
      await c.reminders.save(_en);
      expect(c.reminders.denied, contains('Only a workspace owner'));
      expect(c.reminders.draft, isNull);
      c.dispose();
    });
  });

  group('screen', () {
    testWidgets('rules, routing from the drafted switches, and the reminder',
        (tester) async {
      final c = await _pump(tester, _Api(),
          link: Uri.parse('https://app.example.com/#governance'));
      expect(
          find.byKey(const Key('approval-rule-rule_refunds')), findsOneWidget);
      expect(find.text('Rules: 2, on: 1'), findsOneWidget);
      expect(find.text('Step 1: Members or care_lead'), findsOneWidget);
      expect(find.byKey(const Key('approval-rules-web')), findsOneWidget);
      expect(
          find.textContaining('Refunds: Above AED 1,000.00'), findsOneWidget);
      expect(find.text('Rules switched off, which do not route: 1'),
          findsOneWidget);
      expect(
          find.text('• ${_en.t('ad.apr.routing.mediumOn')}'), findsOneWidget);
      // Drafting a switch changes the routing before saving.
      c.toggle(PolicySwitch.twoPersonRule, true);
      await tester.pumpAndSettle();
      expect(find.text('• ${_en.t('ad.apr.routing.twoPersonOn')}'),
          findsOneWidget);
      expect(find.byKey(const Key('routing-drafted')), findsOneWidget);
      // The reminder: pick 4 hours and save.
      await tester.ensureVisible(find.byKey(const Key('reminder-hours')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('reminder-hours')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('4 hours').last);
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const Key('reminder-save')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('reminder-save')));
      await tester.pumpAndSettle();
      expect(find.text('Approvals now remind owners after 4 hours.'),
          findsOneWidget);
      c.dispose();
    });

    testWidgets('members read; no rules; errors retry on their own',
        (tester) async {
      final fake = _Api()
        ..role = 'member'
        ..rules = []
        ..reminderStatus = 503;
      final c = await _pump(tester, fake);
      expect(find.byKey(const Key('approval-rules-empty')), findsOneWidget);
      expect(find.byKey(const Key('routing-no-bands')), findsOneWidget);
      expect(find.byKey(const Key('approval-rules-web-hint')), findsOneWidget);
      expect(find.byKey(const Key('approval-reminders-error')), findsOneWidget);
      fake.reminderStatus = 200;
      await tester.ensureVisible(find.text('Try again'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('reminder-read-only')), findsOneWidget);
      expect(find.byKey(const Key('reminder-save')), findsNothing);
      fake.rulesStatus = 503;
      await c.rules.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('approval-rules-error')), findsOneWidget);
      expect(find.byKey(const Key('routing-rules-unknown')), findsOneWidget);
      c.dispose();
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0]) {
      testWidgets('lays out at $width wide, 2x text, English and Arabic',
          (tester) async {
        for (final locale in ['en', 'ar']) {
          final c = await _pump(tester, _Api(),
              width: width, text: 2, locale: locale);
          expect(tester.takeException(), isNull);
          if (locale == 'ar') {
            expect(find.text('قواعد الموافقة'), findsOneWidget);
            expect(find.text('تذكير الموافقين'), findsOneWidget);
          }
          await tester.pumpWidget(const SizedBox());
          c.dispose();
        }
      });
    }
  });
}
