import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/project_money.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show app;

// A project's change orders, ledger and profitability on the phone
// (docs/business.md#change-orders): empty figures are "-", a member sends a
// change order and an owner approves it with its content hash, costs are
// recorded, and failures and refusals explain themselves.

const hash = 'c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4c4';

BusinessProject project() => BusinessProject.fromJson(JsonReader.of({
      'id': 'bproj_1',
      'name': 'Fleet reporting pilot',
      'status': 'active',
      'account_name': 'Gulf Mixers LLC',
      'currency': 'SAR',
      'contract_total': 2500,
      'paid_value': 0,
      'milestones': <Object>[],
    }));

class FakeMoney implements ProjectMoneyRepository {
  FakeMoney({this.fail = false, this.deny = false});
  final bool fail, deny;
  final calls = <String>[];
  final changes = <Map<String, Object?>>[];
  final entries = <Map<String, Object?>>[];
  double contract = 2500;

  Map<String, Object?> page(List<Map<String, Object?>> items) =>
      {'items': items, 'total': items.length};

  @override
  Future<Profitability> profitability(String projectId) async {
    calls.add('profitability');
    if (deny) {
      throw const ApiException(
          403, 'Business is for workspace owners and members.');
    }
    if (fail) throw const ApiException(500, 'Database unavailable');
    final labour = entries.fold<double>(0, (s, e) => s + (e['amount']! as num));
    final approved = changes.where((c) => c['status'] == 'approved');
    final delta =
        approved.fold<double>(0, (s, c) => s + (c['total_delta']! as num));
    return Profitability.fromJson(JsonReader.of({
      'currency': 'SAR',
      'accepted_revenue': 2500,
      'change_orders_revenue': delta,
      'revenue': 2500 + delta,
      'contract_total': contract,
      'cash_collected': null,
      'labour_cost': entries.isEmpty ? null : labour,
      'direct_costs': null,
      'gross_margin': entries.isEmpty ? null : 2500 + delta - labour,
      'gross_margin_pct': entries.isEmpty ? null : 75.0,
      'notes': ['Revenue and cash collected are kept apart.'],
    }));
  }

  @override
  Future<BusinessPage<ChangeOrder>> changeOrders(String projectId) async =>
      BusinessPage.fromJson(JsonReader.of(page(changes)), ChangeOrder.fromJson);

  Map<String, Object?> _find(String id) =>
      changes.firstWhere((c) => c['id'] == id);

  @override
  Future<ChangeOrder> createChangeOrder(
      String projectId, Map<String, Object?> body) async {
    calls.add('create');
    final lines = body['lines']! as List;
    final delta =
        lines.fold<double>(0, (s, l) => s + ((l as Map)['unit_price']! as num));
    final change = <String, Object?>{
      'id': 'bchg_${changes.length + 1}',
      'number': changes.length + 1,
      'title': body['title'],
      'status': 'draft',
      'currency': 'SAR',
      'timeline_days': body['timeline_days'],
      'total_delta': delta,
      'content_hash': null,
      'issues': <String>[],
      'stale': false,
      'decision_note': '',
    };
    changes.add(change);
    return ChangeOrder.fromJson(JsonReader.of(change));
  }

  @override
  Future<ChangeOrder> submitChangeOrder(String id) async {
    calls.add('submit');
    final change = _find(id)
      ..['status'] = 'pending_approval'
      ..['content_hash'] = hash;
    return ChangeOrder.fromJson(JsonReader.of(change));
  }

  @override
  Future<ChangeOrder> approveChangeOrder(String id, String contentHash) async {
    calls.add('approve:$contentHash');
    final change = _find(id)
      ..['status'] = 'approved'
      ..['result_version'] = 2;
    contract += (change['total_delta']! as num).toDouble();
    return ChangeOrder.fromJson(JsonReader.of(change));
  }

  @override
  Future<ChangeOrder> rejectChangeOrder(String id, String reason) async {
    calls.add('reject:$reason');
    final change = _find(id)
      ..['status'] = 'rejected'
      ..['decision_note'] = reason;
    return ChangeOrder.fromJson(JsonReader.of(change));
  }

  @override
  Future<ChangeOrder> withdrawChangeOrder(String id, String reason) async {
    final change = _find(id)..['status'] = 'withdrawn';
    return ChangeOrder.fromJson(JsonReader.of(change));
  }

  @override
  Future<BusinessPage<UsageEntry>> usage(String projectId) async =>
      BusinessPage.fromJson(JsonReader.of(page(entries)), UsageEntry.fromJson);

  @override
  Future<UsageEntry> createUsage(Map<String, Object?> body) async {
    calls.add('usage');
    final entry = <String, Object?>{
      'id': 'busg_${entries.length + 1}',
      'kind': body['kind'],
      'description': body['description'],
      'occurred_on': body['occurred_on'],
      'amount': (body['quantity']! as num) * (body['unit_cost']! as num),
      'currency': body['currency'],
      'billable': body['billable'],
    };
    entries.add(entry);
    return UsageEntry.fromJson(JsonReader.of(entry));
  }
}

const owner = BusinessPermissions(write: true, manage: true, decide: true);
const member = BusinessPermissions(write: true, manage: false, decide: false);

Widget screen(FakeMoney repo, BusinessPermissions permissions,
        {Locale locale = const Locale('en'), double textScale = 1}) =>
    app(
      ProjectMoneyScreen(
          repository: repo, project: project(), permissions: permissions),
      locale: locale,
      textScale: textScale,
    );

void main() {
  testWidgets(
      'an owner drafts, sends and approves a change order; costs give a margin',
      (tester) async {
    tester.view.physicalSize = const Size(390, 3000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeMoney();
    await tester.pumpWidget(screen(repo, owner));
    await tester.pumpAndSettle();
    expect(find.text('Profitability'), findsOneWidget);
    expect(find.text('No change orders'), findsOneWidget);
    expect(find.text('No costs recorded'), findsOneWidget);
    expect(find.text('-'), findsWidgets); // nothing collected or recorded yet

    await tester.tap(find.byKey(const Key('biz-change-new')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-change-save')));
    await tester.pumpAndSettle();
    expect(
        find.text('Say what changes (at least 2 characters).'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('biz-change-title')), 'Fuel dashboard');
    await tester.enterText(
        find.byKey(const Key('biz-change-item')), 'Fuel dashboard');
    await tester.enterText(find.byKey(const Key('biz-change-price')), '800');
    await tester.enterText(find.byKey(const Key('biz-change-days')), '10');
    await tester.tap(find.byKey(const Key('biz-change-save')));
    await tester.pumpAndSettle();
    expect(find.text('Change order 1: Fuel dashboard'), findsOneWidget);
    expect(find.text('Price +SAR 800.00, timeline 10 days'), findsOneWidget);

    await tester.tap(find.byKey(const Key('biz-change-submit-bchg_1')));
    await tester.pumpAndSettle();
    expect(find.text('Waiting for an owner'), findsOneWidget);
    await tester.tap(find.byKey(const Key('biz-change-approve-bchg_1')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('approve:$hash'));
    expect(find.text('Approved'), findsOneWidget);
    expect(find.text('SAR 3300.00'), findsWidgets);

    await tester.tap(find.byKey(const Key('biz-usage-new')));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('biz-usage-description')), 'Build the dashboard');
    await tester.enterText(find.byKey(const Key('biz-usage-quantity')), '7.5');
    await tester.enterText(find.byKey(const Key('biz-usage-cost')), '80');
    await tester.tap(find.byKey(const Key('biz-usage-save')));
    await tester.pumpAndSettle();
    expect(find.text('Build the dashboard'), findsOneWidget);
    expect(find.text('SAR 600.00'), findsWidgets);
    expect(find.textContaining('+SAR 2700.00'), findsOneWidget);
  });

  testWidgets('a member sends but an owner decides', (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeMoney();
    await repo.createChangeOrder('bproj_1', {
      'title': 'Training',
      'lines': [
        {'unit_price': 400}
      ],
      'timeline_days': 0,
    });
    await repo.submitChangeOrder('bchg_1');
    await tester.pumpWidget(screen(repo, member));
    await tester.pumpAndSettle();
    expect(
        find.text('A workspace owner approves or rejects it.'), findsOneWidget);
    expect(find.byKey(const Key('biz-change-approve-bchg_1')), findsNothing);
  });

  testWidgets('an owner rejects with a reason', (tester) async {
    tester.view.physicalSize = const Size(390, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final repo = FakeMoney();
    await repo.createChangeOrder('bproj_1', {
      'title': 'Training',
      'lines': [
        {'unit_price': 400}
      ],
      'timeline_days': 0,
    });
    await repo.submitChangeOrder('bchg_1');
    await tester.pumpWidget(screen(repo, owner));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(OutlinedButton, 'Reject'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('biz-change-reason')), 'Client declined');
    await tester.tap(find.widgetWithText(FilledButton, 'Reject'));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('reject:Client declined'));
    expect(find.text('Rejected'), findsOneWidget);
  });

  testWidgets('failed and denied loads explain themselves', (tester) async {
    await tester.pumpWidget(screen(FakeMoney(fail: true), owner));
    await tester.pumpAndSettle();
    expect(find.textContaining('Database unavailable'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(screen(FakeMoney(deny: true), owner));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.lock_outline), findsOneWidget);
  });

  for (final width in [320.0, 768.0]) {
    testWidgets('lays out at $width wide with 200% text, in Arabic too',
        (tester) async {
      tester.view.physicalSize = Size(width, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(screen(FakeMoney(), owner, textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester
          .pumpWidget(screen(FakeMoney(), owner, locale: const Locale('ar')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('الربحية'), findsOneWidget);
    });
  }
}
