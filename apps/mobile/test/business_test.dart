import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/business_controller.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_repository.dart';
import 'package:anum_mobile/features/business/business_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/business_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

// Business on the phone (docs/business.md): summary, leads with paging and a
// lead's detail and state moves, pipeline, proposals waiting for acceptance
// and project milestones. Wire shapes as /api/v1/business sends them.

Map<String, Object?> leadJson(int i, {String state = 'discovered'}) => {
      'id': 'blead_$i',
      'title': 'Lead $i',
      'state': state,
      'account_name': 'Company $i',
      'score_total': 7,
      'score_known': 2,
      'product_name': 'Reporting Quick Win',
      'block_reason': i == 1 ? 'The account is marked do not contact.' : null,
    };

Map<String, Object?> detailJson(String state) => {
      'lead': leadJson(0, state: state),
      'account': {'website': 'https://gulfmixers.example'},
      'contact': {'name': 'Sara Khan'},
      'evidence': [
        {
          'id': 'bevi_1',
          'url': 'https://gulfmixers.example/fleet',
          'title': 'Our mixer fleet',
          'confidence': 'high',
          'excerpt': 'We run 40 mixers.',
          'published_on': null,
        }
      ],
      'inferences': [
        {
          'statement': 'Runs a large fleet',
          'status': 'proposed',
          'basis_evidence_ids': ['bevi_1']
        }
      ],
      'activities': [
        {
          'summary': 'Lead added.',
          'actor': 'owner',
          'created_at': '2026-10-18T09:00:00Z'
        }
      ],
      'qualification_gaps': <String>[],
      'allowed_transitions': state == 'discovered'
          ? ['researched', 'qualified', 'lost', 'suppressed']
          : ['contacted', 'lost', 'suppressed'],
      'consent_gap': null,
    };

class FakeBusiness implements BusinessRepository {
  FakeBusiness({this.deny = false, this.totalLeads = 25, this.empty = false});
  final bool deny, empty;
  final int totalLeads;
  final requests = <String>[];
  String state = 'discovered';

  @override
  Future<BusinessSetup> setup() async {
    requests.add('setup');
    if (deny) {
      throw const ApiException(
          403, 'Business is for workspace owners and members.');
    }
    return BusinessSetup.fromJson(JsonReader.of({
      'permissions': {'write': true, 'manage': true, 'decide': true},
      'sending_enabled': false,
      'web_search': false,
    }));
  }

  @override
  Future<BusinessKpi> kpi(int days) async {
    requests.add('kpi $days');
    return BusinessKpi.fromJson(JsonReader.of({
      'days': days,
      'leads_created': 4,
      'funnel': [
        {'stage': 'created', 'reached': 4, 'of': 4, 'rate_pct': 100.0},
        {'stage': 'qualified', 'reached': 2, 'of': 4, 'rate_pct': 50.0},
        {'stage': 'won', 'reached': 1, 'of': 4, 'rate_pct': 25.0},
      ],
      'open_opportunities': 1,
      'pipeline_assumed': [
        {'currency': 'SAR', 'amount': 4000.0}
      ],
      'contracted': [
        {'currency': 'SAR', 'amount': 2500.0}
      ],
      'cash_collected': [
        {'currency': 'SAR', 'amount': 1250.0}
      ],
      'proposals_accepted': 1,
      'notes': ['Pipeline values are the owner\'s assumptions, not forecasts.'],
    }));
  }

  @override
  Future<BusinessPage<BusinessLead>> leads(
      {String? query,
      String? state,
      required int offset,
      int limit = 20}) async {
    requests.add('leads $query $state $offset');
    final total = empty ? 0 : totalLeads;
    final count = (total - offset).clamp(0, limit);
    return BusinessPage([
      for (var i = 0; i < count; i++)
        BusinessLead.fromJson(JsonReader.of(leadJson(offset + i)))
    ], total);
  }

  @override
  Future<LeadDetail> lead(String id) async =>
      LeadDetail.fromJson(JsonReader.of(detailJson(state)));

  @override
  Future<LeadDetail> transition(String id, String to, String reason) async {
    requests.add('transition $id $to $reason');
    if (to == 'qualified') state = 'qualified';
    return LeadDetail.fromJson(JsonReader.of(detailJson(state)));
  }

  @override
  Future<void> logNote(
          String id, String kind, String summary, String key) async =>
      requests.add('note $id $kind $summary');

  @override
  Future<BusinessPage<BusinessOpportunity>> opportunities(
          {required int offset, int limit = 20}) async =>
      BusinessPage([
        BusinessOpportunity.fromJson(JsonReader.of({
          'id': 'bopp_1',
          'name': 'Fleet reporting',
          'stage': 'quote',
          'account_name': 'Company 0',
          'currency': 'SAR',
          'value_amount': 4000.0,
          'probability_pct': 40.0,
          'weighted_value': 1600.0,
        }))
      ], 1);

  @override
  Future<BusinessPage<BusinessProposal>> proposals(
          {required int offset, int limit = 20}) async =>
      BusinessPage([
        BusinessProposal.fromJson(JsonReader.of({
          'id': 'bprop_1',
          'title': 'Fleet reporting pilot',
          'status': 'pending_approval',
          'account_name': 'Company 0',
          'current_version': 2,
          'current': {'currency': 'SAR', 'total': 2500.0, 'issues': <String>[]},
          'approval_state': 'pending',
          'decision_note': '',
        }))
      ], 1);

  BusinessProject project(String status) =>
      BusinessProject.fromJson(JsonReader.of({
        'id': 'bproj_1',
        'name': 'Fleet reporting pilot',
        'status': 'active',
        'account_name': 'Company 0',
        'currency': 'SAR',
        'contract_total': 2500.0,
        'paid_value': 1250.0,
        'milestones': [
          {
            'id': 'bmile_1',
            'title': 'Deposit',
            'amount': 1250.0,
            'status': status,
            'paid_amount': 1250.0
          }
        ],
      }));

  @override
  Future<BusinessPage<BusinessProject>> projects(
          {required int offset, int limit = 20}) async =>
      BusinessPage([project('planned')], 1);

  @override
  Future<BusinessProject> updateMilestone(String id, String status) async {
    requests.add('milestone $id $status');
    return project(status);
  }
}

Widget app(Widget home,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
    MaterialApp(
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
          data: MediaQuery.of(context)
              .copyWith(textScaler: TextScaler.linear(textScale)),
          child: child!),
      home: home,
    );

void main() {
  test('the English and Arabic catalogs have the same keys', () {
    expect(businessAr.keys.toSet(), businessEn.keys.toSet());
    for (final key in businessEn.keys) {
      final placeholders = RegExp(r'\{(\w+)\}');
      expect(placeholders.allMatches(businessAr[key]!).map((m) => m[1]).toSet(),
          placeholders.allMatches(businessEn[key]!).map((m) => m[1]).toSet(),
          reason: key);
    }
  });

  test('the controller loads, pages leads and keeps a 403 apart', () async {
    final repo = FakeBusiness();
    final c = BusinessController(repo);
    await c.load();
    expect(c.setupState, BusinessLoad.ready);
    expect(c.kpi!.leadsCreated, 4);
    expect(c.leads.items, hasLength(20));
    expect(c.leads.hasMore, isTrue);
    await c.more(c.leads);
    expect(c.leads.items, hasLength(25));
    await c.search('Company 3', 'qualified');
    expect(repo.requests.last, 'leads Company 3 qualified 0');
    final denied = BusinessController(FakeBusiness(deny: true));
    await denied.load();
    expect(denied.setupState, BusinessLoad.denied);
  });

  for (final width in [320.0, 390.0, 768.0, 1024.0]) {
    testWidgets('the summary lays out at $width wide with 200% text',
        (tester) async {
      tester.view.physicalSize = Size(width, 1000);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(app(
          BusinessScreen(controller: BusinessController(FakeBusiness())),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Leads created'), findsOneWidget);
      expect(find.text('SAR 2500.00'), findsOneWidget);
      expect(find.textContaining('Sending is off'), findsOneWidget);
    });
  }

  testWidgets('leads list, a lead, qualify and record a note', (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeBusiness(totalLeads: 3);
    await tester
        .pumpWidget(app(BusinessScreen(controller: BusinessController(repo))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-leads')));
    await tester.pumpAndSettle();
    expect(find.text('Company 0'), findsOneWidget);
    expect(find.text('7/30, 2 of 6 known'), findsNWidgets(3));
    await tester.tap(find.byKey(const Key('biz-lead-blead_0')));
    await tester.pumpAndSettle();
    expect(find.text('Our mixer fleet'), findsOneWidget);
    expect(find.text('Runs a large fleet'), findsOneWidget);
    await tester.tap(find.byKey(const Key('biz-move-qualified')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('transition blead_0 qualified '));
    expect(find.byKey(const Key('biz-move-contacted')), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('biz-note')), 'Called the office');
    await tester.tap(find.text('Record'));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('note blead_0 note Called the office'));
  });

  testWidgets('a reason is asked before marking a lead lost', (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeBusiness();
    final c = BusinessController(repo);
    await c.load();
    await tester.pumpWidget(app(LeadScreen(controller: c, leadId: 'blead_0')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-move-lost')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'Chose another vendor');
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(repo.requests,
        contains('transition blead_0 lost Chose another vendor'));
  });

  testWidgets('pipeline, proposals waiting for acceptance and milestones',
      (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeBusiness();
    var openedApprovals = false;
    await tester.pumpWidget(app(BusinessScreen(
        controller: BusinessController(repo),
        onOpenApprovals: () => openedApprovals = true)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-pipeline')));
    await tester.pumpAndSettle();
    expect(find.textContaining('probability 40% (your assumptions)'),
        findsOneWidget);
    await tester.tap(find.byKey(const Key('biz-tab-proposals')));
    await tester.pumpAndSettle();
    expect(find.text('Waiting for acceptance'), findsOneWidget);
    await tester.tap(find.text('Open Approvals'));
    expect(openedApprovals, isTrue);
    await tester.tap(find.byKey(const Key('biz-tab-projects')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-milestone-bmile_1')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Accepted').last);
    await tester.pumpAndSettle();
    expect(repo.requests, contains('milestone bmile_1 accepted'));
  });

  testWidgets('empty, denied and Arabic right to left', (tester) async {
    tester.view.physicalSize = const Size(390, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(app(BusinessScreen(
        controller: BusinessController(FakeBusiness(empty: true)))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-leads')));
    await tester.pumpAndSettle();
    expect(find.text('No leads yet'), findsOneWidget);

    await tester.pumpWidget(app(BusinessScreen(
        key: const Key('denied'),
        controller: BusinessController(FakeBusiness(deny: true)))));
    await tester.pumpAndSettle();
    expect(
        find.textContaining('holds contacts\' personal data'), findsOneWidget);

    await tester.pumpWidget(app(
        BusinessScreen(
            key: const Key('arabic'),
            controller: BusinessController(FakeBusiness())),
        locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.text('عملاء محتملون جدد'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('عملاء محتملون جدد'))),
        TextDirection.rtl);
  });
}
