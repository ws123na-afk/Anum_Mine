import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/accounts_data.dart';
import 'package:anum_mobile/features/business/business_controller.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_screen.dart';
import 'package:anum_mobile/features/business/catalog_data.dart';
import 'package:anum_mobile/features/business/deals_data.dart';
import 'package:anum_mobile/features/business/deals_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_catalog_accounts_test.dart' show FakeAccounts, FakeCatalog;
import 'business_test.dart' show FakeBusiness, app, leadJson;

// Lead and opportunity editing on the phone (docs/business.md): create, edit
// and delete with the server's own answers, owner against member controls,
// validation, Arabic and phone widths.

Map<String, Object?> oppJson({String stage = 'qualified'}) => {
      'id': 'bopp_1',
      'name': 'Fleet reporting',
      'stage': stage,
      'account_name': 'Company 0',
      'currency': 'SAR',
      'value_amount': 4000.0,
      'probability_pct': 40.0,
      'weighted_value': 1600.0,
      'assumption_note': 'Two similar wins',
      'next_step': 'Send the quote',
      'expected_decision_on': '2026-12-01',
      'lost_reason': '',
    };

class MemberBusiness extends FakeBusiness {
  @override
  Future<BusinessSetup> setup() async => BusinessSetup.fromJson(JsonReader.of({
        'permissions': {'write': true, 'manage': false, 'decide': false},
        'sending_enabled': false,
        'web_search': false,
      }));
}

class FakeDeals implements DealsRepository {
  final calls = <String>[];
  final bodies = <Map<String, Object?>>[];
  String stage = 'qualified';
  String? refuse;

  void maybeRefuse() {
    if (refuse != null) throw ApiException(409, refuse!);
  }

  LeadDetail detail() => LeadDetail.fromJson(JsonReader.of({
        'lead': leadJson(0, state: 'qualified'),
        'account': {'website': null},
        'contact': null,
        'evidence': <Object?>[],
        'inferences': <Object?>[],
        'activities': <Object?>[],
        'qualification_gaps': <String>[],
        'allowed_transitions': <String>[],
      }));

  @override
  Future<LeadDetail> createLead(Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('createLead');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<LeadDetail> updateLead(String id, Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('updateLead $id');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<void> deleteLead(String id) async {
    maybeRefuse();
    calls.add('deleteLead $id');
  }

  @override
  Future<BusinessOpportunity> createOpportunity(
      Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('createOpportunity');
    bodies.add(fields);
    return BusinessOpportunity.fromJson(JsonReader.of(oppJson()));
  }

  @override
  Future<BusinessOpportunity> updateOpportunity(
      String id, Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('updateOpportunity $id');
    bodies.add(fields);
    return BusinessOpportunity.fromJson(JsonReader.of(oppJson(stage: stage)));
  }

  @override
  Future<BusinessOpportunity> moveStage(
      String id, String to, String reason) async {
    maybeRefuse();
    calls.add('move $id $to $reason');
    stage = to;
    return BusinessOpportunity.fromJson(JsonReader.of(oppJson(stage: to)));
  }
}

Future<void> phone(WidgetTester tester, {double width = 390}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = Size(width, 844);
  addTearDown(tester.view.reset);
}

Future<void> tap(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key(key)));
  await tester.pumpAndSettle();
}

Future<void> enter(WidgetTester tester, String key, String text) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(Key(key)), text);
}

BusinessController make(FakeDeals deals, {bool member = false}) =>
    BusinessController(
      member ? MemberBusiness() : FakeBusiness(),
      catalog: CatalogController(FakeCatalog()),
      accounts: AccountsController(FakeAccounts()),
      deals: deals,
    );

Future<BusinessController> boot(
  WidgetTester tester,
  FakeDeals deals, {
  bool member = false,
  Widget Function(BusinessController c)? home,
  Locale locale = const Locale('en'),
}) async {
  final c = make(deals, member: member);
  await c.load();
  await tester.pumpWidget(app(
      home == null ? BusinessScreen(controller: c) : home(c),
      locale: locale));
  await tester.pumpAndSettle();
  return c;
}

BusinessLead lead({String state = 'qualified'}) =>
    BusinessLead.fromJson(JsonReader.of({
      ...leadJson(0, state: state),
      'account_id': 'bacct_1',
      'product_id': 'bprod_0',
      'contact_id': null,
      'next_step': 'Call on Sunday',
    }));

void main() {
  testWidgets('a lead is created for a new company', (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    await boot(tester, deals);
    await tap(tester, 'biz-tab-leads');
    await tap(tester, 'lead-new');
    await tester.tap(find.byKey(const Key('lead-account')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A new company').last);
    await tester.pumpAndSettle();
    await tap(tester, 'desk-save');
    expect(
        find.text('Enter a title of at least 2 characters.'), findsOneWidget);
    await enter(tester, 'lead-title', 'Fleet reporting');
    await enter(tester, 'lead-company-name', 'Gulf Mixers');
    await enter(tester, 'lead-company-website', 'https://gulfmixers.example');
    await tap(tester, 'desk-save');
    expect(deals.calls, ['createLead']);
    expect(deals.bodies.single['title'], 'Fleet reporting');
    expect(deals.bodies.single['account'],
        {'name': 'Gulf Mixers', 'website': 'https://gulfmixers.example'});
    expect(deals.bodies.single.containsKey('account_id'), isFalse);
  });

  testWidgets('a lead for an existing company can name a contact and an offer',
      (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    await boot(tester, deals);
    await tap(tester, 'biz-tab-leads');
    await tap(tester, 'lead-new');
    await tester.tap(find.byKey(const Key('lead-account')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Company 0').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('lead-contact')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sara Khan 1').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('lead-product')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Reporting Quick Win 0').last);
    await tester.pumpAndSettle();
    await enter(tester, 'lead-title', 'Renewal');
    await tap(tester, 'desk-save');
    final body = deals.bodies.single;
    expect(body['account_id'], 'bacct_1');
    expect(body['contact_id'], 'bcont_1');
    expect(body['product_id'], 'bprod_0');
    expect(body.containsKey('account'), isFalse);
  });

  testWidgets('the server refusing a lead stays on the form', (tester) async {
    await phone(tester);
    final deals = FakeDeals()
      ..refuse = 'This account already has an open lead for that offer.';
    await boot(tester, deals);
    await tap(tester, 'biz-tab-leads');
    await tap(tester, 'lead-new');
    await tester.tap(find.byKey(const Key('lead-account')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A new company').last);
    await tester.pumpAndSettle();
    await enter(tester, 'lead-title', 'Fleet reporting');
    await enter(tester, 'lead-company-name', 'Gulf Mixers');
    await tap(tester, 'desk-save');
    expect(find.byKey(const Key('desk-form-error')), findsOneWidget);
    expect(find.textContaining('already has an open lead'), findsOneWidget);
    expect(find.byKey(const Key('lead-title')), findsOneWidget);
  });

  testWidgets('a lead is edited', (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    final c = make(deals);
    await c.load();
    await tester.pumpWidget(app(LeadFormScreen(controller: c, lead: lead())));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('lead-account')), findsNothing);
    await enter(tester, 'lead-title', 'Fleet reporting v2');
    await enter(tester, 'lead-next-step', 'Send the quote');
    await tap(tester, 'desk-save');
    expect(deals.calls, ['updateLead blead_0']);
    expect(deals.bodies.single['title'], 'Fleet reporting v2');
    expect(deals.bodies.single['next_step'], 'Send the quote');
    expect(deals.bodies.single['product_id'], 'bprod_0');
  });

  testWidgets('an owner deletes a lead; a refusal is shown first',
      (tester) async {
    await phone(tester);
    final deals = FakeDeals()
      ..refuse = 'This lead has an opportunity. Mark the lead lost instead.';
    final repo = FakeBusiness()..state = 'qualified';
    final c = BusinessController(repo, deals: deals);
    await c.load();
    await tester.pumpWidget(app(LeadScreen(controller: c, leadId: 'blead_0')));
    await tester.pumpAndSettle();
    await tap(tester, 'lead-delete');
    await tap(tester, 'desk-confirm');
    expect(find.textContaining('Mark the lead lost instead'), findsOneWidget);
    deals.refuse = null;
    await tap(tester, 'lead-delete');
    await tap(tester, 'desk-confirm');
    expect(deals.calls, contains('deleteLead blead_0'));
  });

  testWidgets('a member can edit a lead but not delete it', (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    final repo = MemberBusiness()..state = 'qualified';
    final c = BusinessController(repo, deals: deals);
    await c.load();
    await tester.pumpWidget(app(LeadScreen(controller: c, leadId: 'blead_0')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('lead-edit')), findsOneWidget);
    expect(find.byKey(const Key('lead-delete')), findsNothing);
  });

  testWidgets('an opportunity opens from a qualified lead', (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    final repo = FakeBusiness()..state = 'qualified';
    final c = BusinessController(repo, deals: deals);
    await c.load();
    await tester.pumpWidget(app(LeadScreen(controller: c, leadId: 'blead_0')));
    await tester.pumpAndSettle();
    await tap(tester, 'lead-open-opportunity');
    await enter(tester, 'opp-probability', '140');
    await tap(tester, 'desk-save');
    expect(find.text('Enter a number from 0 to 100.'), findsOneWidget);
    await enter(tester, 'opp-probability', '40');
    await enter(tester, 'opp-value', '4000');
    await enter(tester, 'opp-decision', '1 Dec');
    await tap(tester, 'desk-save');
    expect(find.text('Use the form 2026-12-31.'), findsOneWidget);
    await enter(tester, 'opp-decision', '2026-12-01');
    await tap(tester, 'desk-save');
    final body = deals.bodies.single;
    expect(body['lead_id'], 'blead_0');
    expect(body['value_amount'], 4000.0);
    expect(body['probability_pct'], 40.0);
    expect(body['expected_decision_on'], '2026-12-01');
    // Opened on the new opportunity.
    expect(find.byKey(const Key('opp-edit')), findsOneWidget);
  });

  testWidgets('a discovered lead has no opportunity button', (tester) async {
    await phone(tester);
    final c = BusinessController(FakeBusiness(), deals: FakeDeals());
    await c.load();
    await tester.pumpWidget(app(LeadScreen(controller: c, leadId: 'blead_0')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('lead-open-opportunity')), findsNothing);
  });

  testWidgets('an opportunity is edited and moved, lost needs a reason',
      (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    final c = make(deals);
    await c.load();
    final item = BusinessOpportunity.fromJson(JsonReader.of(oppJson()));
    await tester
        .pumpWidget(app(OpportunityScreen(controller: c, opportunity: item)));
    await tester.pumpAndSettle();
    expect(find.text('Send the quote'), findsNothing);
    expect(find.textContaining('Send the quote'), findsOneWidget);
    await tap(tester, 'opp-edit');
    await enter(tester, 'opp-value', '4500');
    await tap(tester, 'desk-save');
    expect(deals.bodies.single['value_amount'], 4500.0);

    await tap(tester, 'opp-move-quote');
    expect(deals.calls.last, 'move bopp_1 quote ');
    expect(find.byKey(const Key('opp-move-quote')), findsNothing);

    await tap(tester, 'opp-move-lost');
    await tester.enterText(find.byKey(const Key('desk-reason')), 'No budget');
    await tester.pump();
    await tap(tester, 'desk-reason-ok');
    expect(deals.calls.last, 'move bopp_1 lost No budget');
  });

  testWidgets('a won opportunity is final and a refusal is shown',
      (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    final c = make(deals);
    await c.load();
    await tester.pumpWidget(app(OpportunityScreen(
        key: UniqueKey(),
        controller: c,
        opportunity: BusinessOpportunity.fromJson(
            JsonReader.of(oppJson(stage: 'won'))))));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('opp-won')), findsOneWidget);
    expect(find.byKey(const Key('opp-edit')), findsNothing);
    expect(find.byKey(const Key('opp-move-lost')), findsNothing);

    await tester.pumpWidget(app(OpportunityScreen(
        key: UniqueKey(),
        controller: c,
        opportunity: BusinessOpportunity.fromJson(JsonReader.of(oppJson())))));
    await tester.pumpAndSettle();
    deals.refuse = 'The opportunity is already at demo.';
    await tap(tester, 'opp-move-demo');
    expect(find.byKey(const Key('opp-error')), findsOneWidget);
  });

  testWidgets('the pipeline opens an opportunity', (tester) async {
    await phone(tester);
    final deals = FakeDeals();
    await boot(tester, deals);
    await tap(tester, 'biz-tab-pipeline');
    await tester.tap(find.text('Fleet reporting'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('opp-edit')), findsOneWidget);
  });

  for (final width in [320.0, 390.0]) {
    testWidgets('arabic forms fit at $width', (tester) async {
      await phone(tester, width: width);
      final deals = FakeDeals();
      final c = make(deals);
      await c.load();
      await tester.pumpWidget(
          app(LeadFormScreen(controller: c), locale: const Locale('ar')));
      await tester.pumpAndSettle();
      expect(find.text('عميل محتمل جديد'), findsWidgets);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(app(
          OpportunityScreen(
              controller: c,
              opportunity:
                  BusinessOpportunity.fromJson(JsonReader.of(oppJson()))),
          locale: const Locale('ar')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tap(tester, 'opp-edit');
      expect(find.text('تعديل الفرصة'), findsWidgets);
      expect(tester.takeException(), isNull);
    });
  }
}
