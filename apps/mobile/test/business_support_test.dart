import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/business_controller.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_screen.dart';
import 'package:anum_mobile/features/business/support_controller.dart';
import 'package:anum_mobile/features/business/support_models.dart';
import 'package:anum_mobile/features/business/support_repository.dart';
import 'package:anum_mobile/features/business/support_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show FakeBusiness, app;

// Business support tickets on the phone (docs/business.md#support-tickets):
// list states and paging, filters, opening a ticket, status moves with the
// resolve dialog, replies waiting for approval, layout and Arabic. Wire
// shapes as /api/v1/business/support sends them.

const me = 'user_owner';

Map<String, Object?> ticketJson(
  int i, {
  String status = 'new',
  String source = 'email',
  bool escalated = false,
  String? assignee,
}) =>
    {
      'id': 'btkt_$i',
      'reference': 'T-${i.toString().padLeft(6, '0')}',
      'title': 'Report export fails $i',
      'description': i == 0 ? 'The monthly export stops at 50%.' : '',
      'severity': i.isEven ? 'high' : 'normal',
      'status': status,
      'source': source,
      'requester_name': 'Sara Khan',
      'requester_email': 'sara@gulfmixers.example',
      'requester_phone': null,
      'requester_verified': true,
      'entitlement': 'none',
      'entitlement_note': '',
      'escalated': escalated,
      'escalation_reason': escalated ? 'Customer is blocked' : '',
      'resolution_summary': '',
      'resolution_code': null,
      'account_name': 'Gulf Mixers',
      'contact_name': 'Sara Khan',
      'project_id': null,
      'project_name': null,
      'support_plan_name': null,
      'assignee_id': assignee,
      'reported_at': '2026-10-18T06:00:00Z',
      'response_due_at': '2026-10-18T10:00:00Z',
      'resolution_due_at': '2026-10-22T14:00:00Z',
      'first_responded_at': null,
      'resolved_at': null,
      'closed_at': null,
      'created_at': '2026-10-18T06:00:00Z',
      'updated_at': '2026-10-18T06:00:00Z',
      'sla': {
        'response': i == 1 ? 'breached' : 'on_track',
        'resolution': 'on_track',
        'breached': i == 1,
      },
      'allowed': switch (status) {
        'new' => ['triaged', 'in_progress', 'waiting_on_customer', 'resolved'],
        'resolved' => ['closed', 'in_progress'],
        'closed' => ['in_progress'],
        _ => ['triaged', 'waiting_on_customer', 'resolved'],
      },
    };

class FakeSupport implements SupportRepository {
  FakeSupport({
    this.total = 3,
    this.deny = false,
    this.failList = false,
    this.canWrite = true,
  });
  int total;
  final bool deny, canWrite;
  bool failList;
  final requests = <String>[];
  Completer<void>? gate;

  String status = 'new';
  bool escalated = false;
  String? assignee;
  final replies = <Map<String, Object?>>[];
  final activities = <Map<String, Object?>>[
    {
      'kind': 'ticket_opened',
      'summary': 'Ticket T-000000 opened (high, from email).',
      'actor': me,
      'created_at': '2026-10-18T06:00:00Z',
    },
  ];

  Map<String, Object?> detailJson() => {
        ...ticketJson(0,
            status: status, escalated: escalated, assignee: assignee),
        'replies': replies,
        'activities': activities,
        'can_reply': status != 'closed',
        'reply_channel': 'email',
      };

  TicketDetail detail() => TicketDetail.fromJson(JsonReader.of(detailJson()));

  @override
  Future<SupportSetup> setup() async {
    requests.add('setup');
    if (deny) {
      throw const ApiException(403,
          'Support tickets are for workspace owners and members: ask an owner to make you a member.',
          code: 'forbidden');
    }
    return SupportSetup.fromJson(JsonReader.of({
      'policy': {
        'timezone': 'Asia/Riyadh',
        'work_days': [6, 0, 1, 2, 3],
        'start_hour': 9,
        'end_hour': 17,
        'targets': {
          'critical': {'response_hours': 2.0, 'resolution_hours': 16.0},
          'high': {'response_hours': 4.0, 'resolution_hours': 40.0},
          'normal': {'response_hours': 8.0, 'resolution_hours': 80.0},
          'low': {'response_hours': 16.0, 'resolution_hours': 160.0},
        },
        'note': '',
      },
      'severities': ticketSeverities,
      'statuses': ticketStatuses,
      'open_statuses': openTicketStatuses,
      'transitions': <String, Object?>{},
      'resolution_codes': resolutionCodes,
      'entitlements': ['project', 'support_plan', 'none'],
      'can_write': canWrite,
      'can_manage': true,
      'can_decide': true,
      'can_link_email': true,
      'can_link_whatsapp': true,
    }));
  }

  @override
  Future<List<SupportAssignee>> assignees() async =>
      [const SupportAssignee(me, 'owner', true)];

  @override
  Future<SupportSummary> summary() async =>
      SupportSummary.fromJson(JsonReader.of({
        'open': total,
        'new': total,
        'unassigned': total,
        'escalated': 0,
        'breached': total > 1 ? 1 : 0,
        'waiting_on_customer': 0,
        'truncated': false,
      }));

  @override
  Future<BusinessPage<SupportTicket>> tickets(TicketFilter filter,
      {required int offset, int limit = 20}) async {
    requests
        .add('tickets ${Uri(queryParameters: filter.toQuery()).query} $offset');
    if (gate != null) await gate!.future;
    if (failList) {
      throw const ApiException(503, 'The Business store is unavailable.');
    }
    final count = (total - offset).clamp(0, limit);
    return BusinessPage([
      for (var i = 0; i < count; i++)
        SupportTicket.fromJson(JsonReader.of(ticketJson(offset + i,
            escalated: offset + i == 2, assignee: offset + i == 0 ? me : null)))
    ], total);
  }

  @override
  Future<TicketDetail> create(Map<String, Object?> fields) async {
    requests.add('create ${fields['title']} ${fields['severity']} '
        '${fields['requester_email']} ${fields['entitlement']} ${fields['assignee_id']}');
    assignee = fields['assignee_id'] as String?;
    total += 1;
    return detail();
  }

  /// What `fromMessage` was called with, and the refusal to answer, if any.
  final fromMessages = <String>[];
  ApiException? fromMessageError;

  @override
  Future<TicketDetail> fromMessage({
    required String source,
    required String refId,
    String? title,
    String? severity,
  }) async {
    fromMessages.add('$source $refId $title $severity');
    if (fromMessageError case final error?) throw error;
    total += 1;
    return detail();
  }

  @override
  Future<TicketDetail> ticket(String id) async {
    requests.add('ticket $id');
    return detail();
  }

  @override
  Future<TicketDetail> update(String id, Map<String, Object?> fields) async {
    requests.add('update $id $fields');
    if (fields.containsKey('assignee_id')) {
      assignee = fields['assignee_id'] as String?;
    }
    return detail();
  }

  @override
  Future<void> delete(String id) async => requests.add('delete $id');

  @override
  Future<TicketDetail> changeStatus(String id, String to,
      {String? resolutionCode, String? resolutionSummary, String? note}) async {
    requests.add('status $id $to $resolutionCode $resolutionSummary');
    status = to;
    return detail();
  }

  @override
  Future<TicketDetail> escalate(String id, bool on, String reason) async {
    requests.add('escalate $id $on $reason');
    escalated = on;
    return detail();
  }

  @override
  Future<TicketDetail> addNote(String id, String text, bool responded) async {
    requests.add('note $id $text $responded');
    activities.add({
      'kind': responded ? 'response_recorded' : 'note',
      'summary': text,
      'actor': me,
      'created_at': '2026-10-18T07:00:00Z',
    });
    return detail();
  }

  @override
  Future<TicketDetail> addReply(String id, String body) async {
    requests.add('reply $id $body');
    replies.add({
      'id': 'brpl_${replies.length}',
      'channel': 'email',
      'body': body,
      'status': 'draft',
      'error': '',
      'created_at': '2026-10-18T07:00:00Z',
      'approval': null,
    });
    return detail();
  }

  @override
  Future<TicketDetail> editReply(String id, String replyId, String body) async {
    requests.add('edit $replyId $body');
    replies.firstWhere((r) => r['id'] == replyId)['body'] = body;
    return detail();
  }

  @override
  Future<TicketDetail> discardReply(String id, String replyId) async {
    requests.add('discard $replyId');
    replies.firstWhere((r) => r['id'] == replyId)['status'] = 'discarded';
    return detail();
  }

  @override
  Future<(TicketDetail, String)> requestApproval(
      String id, String replyId) async {
    requests.add('ask $replyId');
    final reply = replies.firstWhere((r) => r['id'] == replyId);
    reply['status'] = 'pending_approval';
    reply['approval'] = {
      'id': 'appr_1',
      'status': 'pending',
      'payload_hash': 'abcdef0123456789abcdef',
    };
    return (detail(), 'The reply waits for an owner\'s approval in Approvals.');
  }
}

void sized(WidgetTester tester, double width, [double height = 2400]) {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Future<void> pumpList(WidgetTester tester, SupportController c,
    {Locale locale = const Locale('en'), double textScale = 1}) async {
  await tester.pumpWidget(
      app(SupportScreen(controller: c), locale: locale, textScale: textScale));
  await tester.pumpAndSettle();
}

void main() {
  test('the filter becomes the API query', () {
    const f = TicketFilter();
    expect(
        Uri(queryParameters: f.toQuery()).query, 'open_only=true&sort=updated');
    final g = f.copyWith(
      query: () => 'export',
      status: () => 'in_progress',
      severities: {'high', 'critical'},
      mine: true,
      escalated: true,
      sort: 'due',
    );
    expect(
      Uri(queryParameters: g.toQuery()).query,
      'q=export&status=in_progress&severity=critical&severity=high'
      '&assignee=me&escalated=true&sort=due',
    );
    expect(
      f.copyWith(unassigned: true).toQuery()['unassigned'],
      'true',
    );
    expect(slaOverall(const TicketSla('met', 'due_soon', false)), 'due_soon');
    expect(
        slaOverall(const TicketSla('breached', 'on_track', true)), 'breached');
  });

  testWidgets('loading, then tickets with their badges', (tester) async {
    sized(tester, 390);
    final repo = FakeSupport()..gate = Completer<void>();
    final c = SupportController(repo);
    await tester.pumpWidget(app(SupportScreen(controller: c)));
    await tester.pump();
    await tester.pump();
    expect(find.text('Loading tickets...'), findsOneWidget);
    repo.gate!.complete();
    await tester.pumpAndSettle();
    expect(find.text('Report export fails 0'), findsOneWidget);
    expect(find.text('T-000001'), findsOneWidget);
    expect(find.text('SLA breached'), findsOneWidget);
    expect(find.text('Escalated'), findsWidgets);
    expect(find.textContaining('Assigned to you'), findsOneWidget);
    expect(find.textContaining('From email'), findsWidgets);
    expect(find.textContaining('SLA targets count working hours only'),
        findsOneWidget);
    expect(find.textContaining('There is no 24/7 cover'), findsOneWidget);
    // Filters go to the API.
    await tester.tap(find.byKey(const Key('support-filter-mine')));
    await tester.pumpAndSettle();
    expect(repo.requests.last,
        'tickets open_only=true&assignee=me&sort=updated 0');
    await tester.tap(find.byKey(const Key('support-filter-critical')));
    await tester.pumpAndSettle();
    expect(repo.requests.last, contains('severity=critical'));
    await tester.enterText(find.byKey(const Key('support-search')), 'export');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();
    expect(repo.requests.last, contains('q=export'));
  });

  testWidgets('empty list offers New ticket', (tester) async {
    sized(tester, 390);
    final c = SupportController(FakeSupport(total: 0));
    await pumpList(tester, c);
    expect(find.text('No tickets yet'), findsOneWidget);
    final action = find.descendant(
        of: find.byKey(const Key('support-empty')),
        matching: find.text('New ticket'));
    expect(action, findsOneWidget);
    await tester.tap(action);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('support-form-title')), findsOneWidget);
  });

  testWidgets('an error says what failed and retries', (tester) async {
    sized(tester, 390);
    final repo = FakeSupport(failList: true);
    final c = SupportController(repo);
    await pumpList(tester, c);
    expect(
        find.text(
            'Could not load the tickets: The Business store is unavailable.'),
        findsOneWidget);
    repo.failList = false;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Report export fails 0'), findsOneWidget);
  });

  testWidgets('denied shows the API sentence on who can', (tester) async {
    sized(tester, 390);
    await pumpList(tester, SupportController(FakeSupport(deny: true)));
    expect(find.textContaining('ask an owner to make you a member'),
        findsOneWidget);
    expect(find.byKey(const Key('support-new')), findsNothing);
  });

  testWidgets('load more pages the list', (tester) async {
    sized(tester, 390, 6000);
    final repo = FakeSupport(total: 25);
    final c = SupportController(repo);
    await pumpList(tester, c);
    expect(c.tickets.items, hasLength(20));
    expect(find.text('Load more (20 of 25)'), findsOneWidget);
    await tester.tap(find.byKey(const Key('support-more')));
    await tester.pumpAndSettle();
    expect(c.tickets.items, hasLength(25));
    expect(repo.requests.last, endsWith(' 20'));
    expect(find.byKey(const Key('support-more')), findsNothing);
  });

  testWidgets('opening a ticket by hand', (tester) async {
    sized(tester, 390, 3000);
    final repo = FakeSupport();
    final c = SupportController(repo);
    await pumpList(tester, c);
    await tester.tap(find.byKey(const Key('support-new')));
    await tester.pumpAndSettle();
    // The title is required.
    await tester.tap(find.byKey(const Key('support-form-submit')));
    await tester.pumpAndSettle();
    expect(find.text('Give the ticket a title of at least 3 characters.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('support-form-title')), 'Export broken');
    await tester.enterText(
        find.byKey(const Key('support-form-email')), 'sara@gulfmixers.example');
    await tester.tap(find.byKey(const Key('support-form-severity')));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Critical: respond in 2 h').last);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byKey(const Key('support-form-submit')));
    await tester.tap(find.byKey(const Key('support-form-submit')));
    await tester.pumpAndSettle();
    expect(
        repo.requests,
        contains(
            'create Export broken critical sara@gulfmixers.example none $me'));
    // Lands on the new ticket.
    expect(find.byKey(const Key('support-detail-status')), findsOneWidget);
  });

  testWidgets('status moves, the resolve dialog checks, escalation and notes',
      (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeSupport();
    final c = SupportController(repo);
    await c.load();
    await tester
        .pumpWidget(app(TicketScreen(controller: c, ticketId: 'btkt_0')));
    await tester.pumpAndSettle();
    expect(find.text('The monthly export stops at 50%.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('support-move-in_progress')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('status btkt_0 in_progress null null'));
    expect(find.text('Moved to In progress.'), findsOneWidget);

    await tester.tap(find.byKey(const Key('support-move-resolved')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('support-resolve-confirm')));
    await tester.pump();
    expect(find.text('Choose a resolution.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('support-resolve-code')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Fixed').last);
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byKey(const Key('support-resolve-summary')), 'Too short');
    await tester.tap(find.byKey(const Key('support-resolve-confirm')));
    await tester.pump();
    expect(find.textContaining('10 characters'), findsOneWidget);
    expect(repo.requests.where((r) => r.contains(' resolved ')), isEmpty);
    await tester.enterText(find.byKey(const Key('support-resolve-summary')),
        'Rebuilt the export job.');
    await tester.tap(find.byKey(const Key('support-resolve-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests,
        contains('status btkt_0 resolved fixed Rebuilt the export job.'));
    expect(find.byKey(const Key('support-move-closed')), findsOneWidget);
    expect(find.text('Reopen'), findsOneWidget);

    // Reopen, escalate with a reason, record a response.
    await tester.tap(find.text('Reopen'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('support-escalate')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('support-dialog-confirm')));
    await tester.pump();
    expect(find.textContaining('at least 3 characters'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('support-dialog-text')), 'Customer is blocked');
    await tester.tap(find.byKey(const Key('support-dialog-confirm')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('escalate btkt_0 true Customer is blocked'));
    expect(find.byKey(const Key('support-deescalate')), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('support-note')), 'Called Sara back');
    await tester.ensureVisible(find.byKey(const Key('support-note-responded')));
    await tester.tap(find.byKey(const Key('support-note-responded')));
    await tester.pump();
    await tester.ensureVisible(find.byKey(const Key('support-note-save')));
    await tester.tap(find.byKey(const Key('support-note-save')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('note btkt_0 Called Sara back true'));
  });

  testWidgets('a reply draft, edit, then ask for approval waits in Approvals',
      (tester) async {
    sized(tester, 390, 4000);
    final repo = FakeSupport();
    final c = SupportController(repo);
    await c.load();
    var opened = false;
    await tester.pumpWidget(app(TicketScreen(
        controller: c,
        ticketId: 'btkt_0',
        onOpenApprovals: () => opened = true)));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('support-reply')),
        'We are rebuilding the export and will update you tomorrow.');
    await tester.tap(find.byKey(const Key('support-reply-save')));
    await tester.pumpAndSettle();
    expect(
        repo.requests, contains(startsWith('reply btkt_0 We are rebuilding')));
    expect(find.text('Draft'), findsOneWidget);

    await tester.tap(find.byKey(const Key('support-reply-edit-brpl_0')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('support-dialog-text')),
        'We are rebuilding the export; next update by 12:00.');
    await tester.tap(find.byKey(const Key('support-dialog-confirm')));
    await tester.pumpAndSettle();
    expect(
        repo.requests, contains(startsWith('edit brpl_0 We are rebuilding')));

    await tester.tap(find.byKey(const Key('support-reply-ask-brpl_0')));
    await tester.pumpAndSettle();
    expect(repo.requests, contains('ask brpl_0'));
    expect(
        find.byKey(const Key('support-reply-waiting-brpl_0')), findsOneWidget);
    expect(
        find.textContaining('Waiting for an owner\'s approval'), findsWidgets);
    expect(find.text('Approval check code abcdef012345'), findsOneWidget);
    expect(find.byKey(const Key('support-reply-ask-brpl_0')), findsNothing);
    await tester.tap(find.text('Open Approvals'));
    expect(opened, isTrue);
  });

  for (final width in [320.0, 1024.0]) {
    testWidgets('list and ticket lay out at $width wide with 200% text',
        (tester) async {
      sized(tester, width, 1400);
      final repo = FakeSupport()..escalated = true;
      final c = SupportController(repo);
      await pumpList(tester, c, textScale: 2);
      expect(tester.takeException(), isNull);
      expect(find.text('Report export fails 0'), findsOneWidget);
      await tester.ensureVisible(find.text('Report export fails 0'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Report export fails 0'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text('Service targets'), findsOneWidget);
      await tester.scrollUntilVisible(
          find.byKey(const Key('support-note-save')), 300,
          scrollable: find.byType(Scrollable).first);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('Arabic renders right to left', (tester) async {
    sized(tester, 390);
    await pumpList(tester, SupportController(FakeSupport()),
        locale: const Locale('ar'));
    expect(tester.takeException(), isNull);
    expect(find.text('تذاكر الدعم'), findsOneWidget);
    expect(find.text('تذكرة جديدة'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('تذكرة جديدة'))),
        TextDirection.rtl);
  });

  testWidgets('Support is a Business section', (tester) async {
    sized(tester, 390);
    final support = SupportController(FakeSupport());
    await tester.pumpWidget(app(BusinessScreen(
        controller: BusinessController(FakeBusiness(), support: support))));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('biz-tab-support')));
    await tester.pumpAndSettle();
    expect(find.text('Report export fails 0'), findsOneWidget);
    expect(support.state, BusinessLoad.ready);
  });
}
