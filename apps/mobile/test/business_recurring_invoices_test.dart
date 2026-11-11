// Recurring invoices on the phone (docs/business.md#recurring-invoices): the
// list and its states, a schedule's actions, the form's checks and the owner's
// planned-email switches, against a fake repository. Names are SYNTHETIC.
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/invoice_models.dart';
import 'package:anum_mobile/features/business/recurring_invoices.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show app;

Map<String, Object?> scheduleJson(
        {String id = 'binsch_1',
        String status = 'active',
        int created = 0,
        int version = 1}) =>
    {
      'id': id,
      'title': 'Monthly support',
      'account_id': 'bacc_1',
      'account_name': 'Synthetic Client',
      'currency': 'SAR',
      'frequency': 'monthly',
      'interval': 1,
      'start_date': '2026-12-01',
      'end_date': null,
      'max_count': null,
      'status': status,
      'issue_mode': 'request_approval',
      'total': 1150,
      'version': version,
      'created_count': created,
      'due_in_days': 14,
      'next_date': status == 'active' ? '2026-12-01' : null,
      'last_error': '',
      'end_reason': status == 'ended' ? 'owner' : '',
      'lines': [
        {
          'description': 'Support retainer',
          'quantity': 1,
          'unit_price': 1000,
          'tax_rate_pct': 15
        }
      ],
      'upcoming': status == 'active' ? ['2026-12-01', '2027-01-01'] : [],
      'invoices': [
        if (created > 0)
          {
            'id': 'binv_1',
            'number': null,
            'status': 'pending_approval',
            'period_date': '2026-11-01',
            'total': 1150,
            'currency': 'SAR'
          }
      ],
      'invoices_total': created,
    };

class FakeRecurring implements RecurringRepository {
  final calls = <String>[];
  var items = <Map<String, Object?>>[scheduleJson()];
  Map<String, Object?> auto = {
    'auto_reminders': false,
    'email_scheduled': false,
    'language': 'en',
    'can_change': true,
    'last_run_at': null,
    'last_run_summary': null,
  };
  Map<String, Object?>? created;
  bool failList = false;

  InvoiceSchedule _one(Map<String, Object?> json) =>
      InvoiceSchedule.fromJson(JsonReader(json));

  @override
  Future<SchedulePage> list(
      {String? status, required int offset, int limit = 20}) async {
    calls.add('list $status');
    if (failList) throw Exception('The list could not be read.');
    final shown = items
        .where((i) => status == null || status.isEmpty || i['status'] == status)
        .map(_one)
        .toList();
    return SchedulePage(shown, shown.length);
  }

  @override
  Future<InvoiceSchedule> get(String id) async =>
      _one(items.firstWhere((i) => i['id'] == id));

  @override
  Future<InvoiceSchedule> create(Map<String, Object?> body) async {
    created = body;
    final made = scheduleJson(id: 'binsch_2');
    items = [...items, made];
    return _one(made);
  }

  @override
  Future<InvoiceSchedule> update(
          String id, int version, Map<String, Object?> body) async =>
      _one(scheduleJson(id: id, version: version + 1));

  @override
  Future<InvoiceSchedule> change(String id, String action, int version) async {
    calls.add('$action $id $version');
    final status = switch (action) {
      'pause' => 'paused',
      'end' => 'ended',
      _ => 'active'
    };
    final next = scheduleJson(id: id, status: status, version: version + 1);
    items = [for (final i in items) i['id'] == id ? next : i];
    return _one(next);
  }

  @override
  Future<void> delete(String id) async {
    calls.add('delete $id');
    items = items.where((i) => i['id'] != id).toList();
  }

  @override
  Future<(int, String, InvoiceSchedule)> run(String id) async {
    calls.add('run $id');
    return (1, '', _one(scheduleJson(id: id, created: 1, version: 2)));
  }

  @override
  Future<InvoiceAutomation> automation() async =>
      InvoiceAutomation.fromJson(JsonReader(auto));

  @override
  Future<InvoiceAutomation> saveAutomation(
      {required bool reminders,
      required bool emails,
      required String language}) async {
    calls.add('auto $reminders $emails');
    auto = {...auto, 'auto_reminders': reminders, 'email_scheduled': emails};
    return InvoiceAutomation.fromJson(JsonReader(auto));
  }

  @override
  Future<InvoiceAutomation> runAutomation() async =>
      InvoiceAutomation.fromJson(JsonReader(auto));

  @override
  Future<List<ClientAccount>> accounts(String query) async => [
        ClientAccount.fromJson(JsonReader({
          'id': 'bacc_1',
          'name': 'Synthetic Client',
          'contacts': 1,
          'open_leads': 0,
          'do_not_contact': false
        }))
      ];
}

void sized(WidgetTester tester, double width, [double height = 3000]) {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('the list, a schedule and pausing it', (tester) async {
    sized(tester, 390);
    final repo = FakeRecurring();
    await tester.pumpWidget(
        app(RecurringInvoicesScreen(repository: repo, canWrite: true)));
    await tester.pumpAndSettle();
    expect(find.text('Monthly support'), findsOneWidget);
    expect(find.textContaining('Every month'), findsOneWidget);
    expect(find.byKey(const Key('rinv-auto')), findsOneWidget);
    await tester.tap(find.byKey(const Key('rinv-binsch_1')));
    await tester.pumpAndSettle();
    expect(find.text('Synthetic Client'), findsOneWidget);
    expect(find.text('2026-12-01, 2027-01-01'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('rinv-pause')));
    await tester.tap(find.byKey(const Key('rinv-pause')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('pause binsch_1 1'));
    expect(find.byKey(const Key('rinv-resume')), findsOneWidget);
    expect(find.text('Paused. Nothing is drafted until you resume it.'),
        findsOneWidget);
  });

  testWidgets('drafting what is due lists the invoice it made', (tester) async {
    sized(tester, 390);
    final repo = FakeRecurring();
    String? opened;
    await tester.pumpWidget(app(ScheduleScreen(
        repository: repo,
        scheduleId: 'binsch_1',
        canWrite: true,
        onOpenInvoice: (id) => opened = id)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('rinv-run')));
    await tester.pumpAndSettle();
    expect(find.text('Drafted 1 invoices.'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const Key('rinv-invoice-binv_1')));
    await tester.tap(find.byKey(const Key('rinv-invoice-binv_1')));
    expect(opened, 'binv_1');
    // It drafted something: delete is gone, end asks first.
    expect(find.byKey(const Key('rinv-delete')), findsNothing);
    await tester.ensureVisible(find.byKey(const Key('rinv-end')));
    await tester.tap(find.byKey(const Key('rinv-end')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('rinv-confirm')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('end binsch_1 2'));
  });

  testWidgets('the form checks its fields and sends the lines', (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeRecurring();
    await tester.pumpWidget(app(ScheduleFormScreen(repository: repo)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('rinv-save')));
    await tester.tap(find.byKey(const Key('rinv-save')));
    await tester.pumpAndSettle();
    expect(find.text('Choose the client to invoice.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('rinv-account')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Synthetic Client').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('rinv-save')));
    await tester.tap(find.byKey(const Key('rinv-save')));
    await tester.pumpAndSettle();
    expect(find.text('Add at least one line.'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('rinv-line-0-description')), 'Support retainer');
    await tester.enterText(find.byKey(const Key('rinv-line-0-price')), '1000');
    await tester.enterText(find.byKey(const Key('rinv-line-0-tax')), '15');
    await tester.ensureVisible(find.byKey(const Key('rinv-save')));
    await tester.tap(find.byKey(const Key('rinv-save')));
    await tester.pumpAndSettle();
    expect(repo.created?['account_id'], 'bacc_1');
    expect(repo.created?['lines'], [
      {
        'description': 'Support retainer',
        'quantity': 1.0,
        'unit_price': 1000.0,
        'tax_rate_pct': 15.0
      }
    ]);
    expect(repo.created?['issue_mode'], 'draft');
  });

  testWidgets('an owner turns the planned reminders on', (tester) async {
    sized(tester, 390);
    final repo = FakeRecurring()..items = [];
    await tester.pumpWidget(
        app(RecurringInvoicesScreen(repository: repo, canWrite: true)));
    await tester.pumpAndSettle();
    expect(find.textContaining('No recurring invoices yet'), findsOneWidget);
    await tester.tap(find.byKey(const Key('rinv-auto-reminders')));
    await tester.pumpAndSettle();
    expect(repo.calls, contains('auto true false'));
    expect(find.byKey(const Key('rinv-auto-run')), findsOneWidget);
  });

  testWidgets('a failed list says why and retries; Arabic lays out',
      (tester) async {
    sized(tester, 390);
    final repo = FakeRecurring()..failList = true;
    await tester.pumpWidget(app(
        RecurringInvoicesScreen(repository: repo, canWrite: false),
        locale: const Locale('ar')));
    await tester.pumpAndSettle();
    expect(find.textContaining('could not be read'), findsOneWidget);
    expect(find.byKey(const Key('rinv-new')), findsNothing);
    repo.failList = false;
    await tester.tap(find.byType(OutlinedButton).first);
    await tester.pumpAndSettle();
    expect(find.text('Monthly support'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
