// The email summary (docs/email.md): tab queries, counts, priority pills,
// empty / denied / no-mailbox states and View all emails.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/email/email_controller.dart';
import 'package:anum_mobile/features/email/email_models.dart';
import 'package:anum_mobile/features/email/email_repository.dart';
import 'package:anum_mobile/features/email/email_summary.dart';
import 'package:anum_mobile/features/email/email_summary_screen.dart';
import 'package:anum_mobile/features/workspace/workspace_models.dart';
import 'package:flutter_test/flutter_test.dart';

import 'harness.dart';

EmailMessage message(String id, String sender, String priority) =>
    EmailMessage.fromJson(JsonReader({
      'id': id,
      'account_id': 'emb_1',
      'subject': 'Tyre inspection report $id',
      'from_address': 'sender@example.com',
      'from_name': sender,
      'received_at': DateTime.utc(2026, 10, 11, 9).toIso8601String(),
      'priority': priority,
    }));

class FakeSummary implements EmailSummaryRepository {
  int fail = 0;
  int accounts = 1;
  final asked = <SummaryTab>[];

  @override
  Future<InboxCounts> counts() async {
    if (fail != 0) throw ApiException(fail, 'Email needs the member role.');
    return InboxCounts(accounts: accounts, unread: 12, urgentUnread: 3);
  }

  @override
  Future<EmailPage> messages(SummaryTab tab, {String? before}) async {
    asked.add(tab);
    if (tab == SummaryTab.replied) return const EmailPage([], null);
    return EmailPage([
      message('m1', 'Sajid Kamboh', 'high'),
      message('m2', 'Vinay Kumar', 'normal'),
      message('m3', 'HR Team', 'low'),
    ], null);
  }
}

class UnusedInbox implements EmailRepository {
  @override
  dynamic noSuchMethod(Invocation invocation) =>
      throw UnimplementedError('${invocation.memberName}');
}

EmailSummaryScreen screen(FakeSummary repo,
        {SummaryTab tab = SummaryTab.all}) =>
    EmailSummaryScreen(
        controller: EmailSummaryController(repo, tab: tab),
        inbox: EmailController(UnusedInbox()));

void main() {
  test('tabs map to the API filters', () {
    expect(SummaryTab.urgent.params, {'urgent': 'true'});
    expect(SummaryTab.replied.params, {'replied': 'true'});
    expect(SummaryTab.other.params, {'urgent': 'false', 'replied': 'false'});
    expect(SummaryTabQuery.parse('urgent'), SummaryTab.urgent);
    expect(SummaryTabQuery.parse('nonsense'), SummaryTab.all);
    expect(
        InboxCounts.fromJson(JsonReader({'high_priority_unread': 2}))
            .urgentUnread,
        2);
    expect(WorkspaceApproval, isNotNull);
  });

  testWidgets('layouts at every width, 200% text and Arabic', (tester) async {
    await checkLayouts(
        tester, () => screen(FakeSummary()), find.text('Sajid Kamboh'));
  });

  testWidgets('tabs, pills, empty tab and View all emails', (tester) async {
    final repo = FakeSummary();
    final opened = await pumpScreen(tester, screen(repo), height: 1200);
    expect(find.text('3 urgent · 12 unread'), findsOneWidget);
    expect(find.text('Urgent (3)'), findsOneWidget);
    expect(find.text('High'), findsOneWidget);
    expect(find.text('Medium'), findsOneWidget);
    expect(find.text('SK'), findsOneWidget);
    await tester.tap(find.text('Replied'));
    await tester.pump();
    await tester.pump();
    expect(repo.asked.last, SummaryTab.replied);
    expect(find.text('No replies sent yet'), findsOneWidget);
    await tester.tap(find.text('Show all messages'));
    await tester.pump();
    await tester.pump();
    expect(repo.asked.last, SummaryTab.all);
    await tester.tap(find.text('View all emails'));
    await tester.pump();
    expect(opened, ['/inbox']);
  });

  testWidgets('denied and no mailbox', (tester) async {
    await pumpScreen(tester, screen(FakeSummary()..fail = 403));
    expect(
        find.text('Email is for workspace owners and members'), findsOneWidget);
    await pumpScreen(tester, screen(FakeSummary()..accounts = 0));
    expect(find.text('No mailbox connected'), findsOneWidget);
  });
}
