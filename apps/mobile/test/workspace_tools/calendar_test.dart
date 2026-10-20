// Calendar & next actions and the meeting screen (docs/calendar.md):
// parsing, the controller, the day strip and timeline, Join/Call, next steps,
// attendees with their replies, add / cancel / delete, at every width, at 200
// percent text and in Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/calendar/calendar_controller.dart';
import 'package:anum_mobile/features/calendar/calendar_models.dart';
import 'package:anum_mobile/features/calendar/calendar_repository.dart';
import 'package:anum_mobile/features/calendar/calendar_screen.dart';
import 'package:anum_mobile/features/calendar/event_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'harness.dart';

final _today = DateTime(2026, 10, 7);

Map<String, Object?> eventJson(String id,
        {String title = 'PMV Team Meeting',
        int hour = 10,
        String kind = 'meeting',
        bool canManage = true,
        String status = 'scheduled',
        Map<String, Object?> extra = const {}}) =>
    <String, Object?>{
      'id': id,
      'title': title,
      'kind': kind,
      'status': status,
      'starts_at': DateTime(2026, 10, 7, hour).toUtc().toIso8601String(),
      'ends_at': DateTime(2026, 10, 7, hour + 1).toUtc().toIso8601String(),
      'location': 'Microsoft Teams',
      if (kind == 'meeting') 'conference_url': 'https://teams.example.com/x',
      if (kind == 'call') 'phone': '+966 50 000 0000',
      'can_manage': canManage,
      'attendees': [
        {
          'id': 'attendee_1',
          'name': 'Sajid Kamboh',
          'role': 'Fleet Manager',
          'response': 'invited'
        },
        {
          'id': 'attendee_2',
          'name': 'Vinay Kumar',
          'role': 'Workshop Manager',
          'response': 'accepted'
        },
      ],
    }..addAll(extra);

class FakeCalendar implements CalendarRepository {
  int fail = 0;
  bool canWrite = true;
  bool empty = false;
  final store = <String, Map<String, Object?>>{
    'event_1': eventJson('event_1'),
    'event_2':
        eventJson('event_2', title: 'Supplier Call', hour: 14, kind: 'call'),
  };
  CalendarSyncStatus status = const CalendarSyncStatus([], canManage: true);
  bool syncFails = false;
  String syncOutcome = 'connected';
  final syncs = <String>[];
  final invitations = <String>[];
  final tasks = <String>[];
  final responses = <String>[];
  final created = <EventDraft>[];
  final changes = <Map<String, Object?>>[];

  @override
  Future<EventPage> events(DateTime start, DateTime end) async {
    if (fail != 0) throw ApiException(fail, 'Calendar is down');
    return EventPage(
        empty
            ? const []
            : [
                for (final e in store.values)
                  CalendarEvent.fromJson(JsonReader(e))
              ],
        canWrite: canWrite);
  }

  @override
  Future<CalendarEvent> event(String id) async {
    final found = store[id];
    if (found == null) throw const ApiException(404, 'Event not found');
    return CalendarEvent.fromJson(JsonReader(found));
  }

  @override
  Future<CalendarEvent> create(EventDraft draft) async {
    created.add(draft);
    final json = {
      ...eventJson('event_3', title: draft.title),
      'attendees': <Object?>[]
    };
    store['event_3'] = json;
    return CalendarEvent.fromJson(JsonReader(json));
  }

  @override
  Future<CalendarEvent> update(String id, Map<String, Object?> changes) async {
    this.changes.add(changes);
    store[id] = {...store[id]!, ...changes};
    return CalendarEvent.fromJson(JsonReader(store[id]!));
  }

  @override
  Future<CalendarEvent> respond(
      String id, String attendeeId, String response) async {
    responses.add('$attendeeId:$response');
    final json = Map<String, Object?>.from(store[id]!);
    json['attendees'] = [
      for (final a in (json['attendees'] as List).cast<Map<String, Object?>>())
        a['id'] == attendeeId ? {...a, 'response': response} : a
    ];
    store[id] = json;
    return CalendarEvent.fromJson(JsonReader(json));
  }

  @override
  Future<void> delete(String id) async => store.remove(id);

  @override
  Future<CalendarSyncStatus> sync() async {
    if (syncFails) throw const ApiException(503, 'Database unavailable');
    return status;
  }

  @override
  Future<SyncConnection> syncNow(String connectionId) async {
    syncs.add(connectionId);
    return SyncConnection(
        id: connectionId,
        provider: 'google',
        providerName: 'Google Calendar',
        email: 'owner@example.com',
        status: syncOutcome,
        lastSyncAt: DateTime.now(),
        lastError: syncOutcome == 'connected' ? null : 'Backend Error');
  }

  @override
  Future<List<String>> requestInvitations(String eventId) async {
    invitations.add(eventId);
    return ['sara@example.com'];
  }

  @override
  Future<List<NextStep>> nextSteps() async => [
        NextStep.fromJson(JsonReader({
          'id': 'agenda:event_1',
          'title': 'Prepare for PMV Team Meeting',
          'detail': 'Wed 07 Oct 07:00 UTC',
          'icon': 'agenda',
          'action': {
            'kind': 'task',
            'title': 'Prepare: PMV Team Meeting',
            'prompt': 'Prepare a short briefing'
          },
        })),
        NextStep.fromJson(JsonReader({
          'id': 'email:urgent',
          'title': 'Reply to 3 urgent emails',
          'detail': 'Unread and marked high priority',
          'icon': 'email',
          'action': {'kind': 'open', 'destination': 'email_urgent'},
        })),
      ];

  @override
  Future<String> createTask(String title, String prompt) async {
    tasks.add(title);
    return 'task_9';
  }
}

CalendarController controllerFor(FakeCalendar repo) =>
    CalendarController(repo, clock: () => DateTime(2026, 10, 7, 8));

void main() {
  test('models: initials, links, draft json', () {
    final event = CalendarEvent.fromJson(JsonReader(eventJson('e')));
    expect(event.attendees.first.initials, 'SK');
    expect(event.joinLink.toString(), 'https://teams.example.com/x');
    final call =
        CalendarEvent.fromJson(JsonReader(eventJson('c', kind: 'call')));
    expect(call.callLink.toString(), 'tel:+966500000000');
    final insecure = CalendarEvent.fromJson(JsonReader(
        {...eventJson('x'), 'conference_url': 'http://example.com'}));
    expect(insecure.joinLink, isNull);
    final json = EventDraft.from(event).toJson();
    expect(json['attendees'], hasLength(2));
    expect(json['location'], 'Microsoft Teams');
    expect(() => CalendarEvent.fromJson(JsonReader({'id': 'x'})),
        throwsA(isA<JsonShapeException>()));
  });

  test('controller: week, selected day, states and actions', () async {
    final repo = FakeCalendar();
    final c = controllerFor(repo);
    await c.load();
    expect(c.phase, CalendarPhase.ready);
    expect(c.days.first, _today);
    expect(
        c.dayEvents.map((e) => e.title), ['PMV Team Meeting', 'Supplier Call']);
    expect(c.steps, hasLength(2));
    c.selectDay(_today.add(const Duration(days: 1)));
    expect(c.dayEvents, isEmpty);
    expect(await c.runStep(c.steps.first), 'task_9');
    expect(await c.runStep(c.steps.last), isNull);
    final cancelled = await c.save('event_1', {'status': 'cancelled'});
    expect(cancelled!.cancelled, isTrue);
    expect(c.events.map((e) => e.id), ['event_2']);
    repo.fail = 403;
    await c.load();
    expect(c.phase, CalendarPhase.denied);
    repo.fail = 500;
    await c.load();
    expect(c.phase, CalendarPhase.error);
  });

  testWidgets('calendar layouts at every width, 200% text and Arabic',
      (tester) async {
    await checkLayouts(
        tester,
        () => CalendarScreen(
            controller: controllerFor(FakeCalendar()),
            openLink: (_) async => true),
        find.text('PMV Team Meeting'));
  });

  testWidgets('join, call, next steps and an empty day', (tester) async {
    final repo = FakeCalendar();
    final links = <Uri>[];
    final opened = await pumpScreen(
        tester,
        CalendarScreen(
            controller: controllerFor(repo),
            openLink: (uri) async {
              links.add(uri);
              return uri.scheme == 'https';
            }),
        height: 1400);
    await tester.tap(find.text('Join'));
    await tester.pump();
    await tester.tap(find.text('Call'));
    await tester.pump();
    expect(links.map((u) => u.scheme), ['https', 'tel']);
    expect(find.textContaining('Could not open tel:'), findsOneWidget);

    await tester.tap(find.text('Prepare for PMV Team Meeting'));
    await tester.pumpAndSettle();
    expect(repo.tasks, ['Prepare: PMV Team Meeting']);
    expect(opened.last, '/tasks/task_9');
    Navigator.of(tester.element(find.text('route /tasks/task_9'))).pop();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Reply to 3 urgent emails'));
    await tester.pumpAndSettle();
    expect(opened.last, '/email/summary?tab=urgent');
    Navigator.of(tester.element(find.textContaining('route /email'))).pop();
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('Next week'));
    await tester.pumpAndSettle();
    final empty = FakeCalendar()..empty = true;
    await pumpScreen(
        tester,
        CalendarScreen(
            controller: controllerFor(empty), openLink: (_) async => true));
    expect(find.text('Nothing scheduled'), findsOneWidget);
    expect(find.text('Add event'), findsWidgets);
    final viewer = FakeCalendar()
      ..empty = true
      ..canWrite = false;
    await pumpScreen(
        tester,
        CalendarScreen(
            controller: controllerFor(viewer), openLink: (_) async => true));
    expect(find.text('Add event'), findsNothing);
    final denied = FakeCalendar()..fail = 403;
    await pumpScreen(
        tester,
        CalendarScreen(
            controller: controllerFor(denied), openLink: (_) async => true));
    expect(find.text('The calendar is for members of this workspace'),
        findsOneWidget);
  });

  testWidgets('meeting scheduled: attendees, record a reply, cancel, delete',
      (tester) async {
    final repo = FakeCalendar();
    final c = controllerFor(repo);
    await c.load();
    await checkLayouts(
        tester,
        () => EventScreen(
            controller: c, eventId: 'event_1', openLink: (_) async => true),
        find.text('Sajid Kamboh'));

    await pumpScreen(
        tester,
        EventScreen(
            controller: c, eventId: 'event_1', openLink: (_) async => true));
    await tester.pumpAndSettle();
    expect(find.text('Meeting scheduled'), findsOneWidget);
    expect(find.text('Attendees (2)'), findsOneWidget);
    expect(find.text('Invited'), findsOneWidget);
    expect(find.text('Accepted'), findsOneWidget);
    await tester.tap(find.text('Sajid Kamboh'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Declined'));
    await tester.pumpAndSettle();
    expect(repo.responses, ['attendee_1:declined']);
    expect(find.text('Declined'), findsOneWidget);

    await tester.tap(find.byTooltip('More actions'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel event').last);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Cancel event'));
    await tester.pumpAndSettle();
    expect(repo.changes.last, {'status': 'cancelled'});
    expect(find.text('Cancelled'), findsWidgets);

    await tester.tap(find.byTooltip('More actions'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Delete event').last);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete event'));
    await tester.pumpAndSettle();
    expect(repo.store.containsKey('event_1'), isFalse);
  });

  testWidgets('a missing event says so; viewers cannot record replies',
      (tester) async {
    final repo = FakeCalendar();
    repo.store['event_v'] = eventJson('event_v', canManage: false);
    final c = controllerFor(repo);
    await pumpScreen(
        tester,
        EventScreen(
            controller: c, eventId: 'event_gone', openLink: (_) async => true));
    await tester.pumpAndSettle();
    expect(find.text('Event not found'), findsOneWidget);
    await pumpScreen(
        tester,
        EventScreen(
            controller: c, eventId: 'event_v', openLink: (_) async => true));
    await tester.pumpAndSettle();
    expect(find.byTooltip('More actions'), findsNothing);
    await tester.tap(find.text('Sajid Kamboh'));
    await tester.pumpAndSettle();
    expect(find.text('Reply from Sajid Kamboh'), findsNothing);
  });

  group('calendar sync (docs/calendar.md#sync)', () {
    SyncConnection google({String status = 'connected', DateTime? at}) =>
        SyncConnection(
            id: 'cal_1',
            provider: 'google',
            providerName: 'Google Calendar',
            email: 'owner@example.com',
            status: status,
            lastSyncAt:
                at ?? DateTime.now().subtract(const Duration(minutes: 5)),
            lastError: status == 'connected' ? null : 'Token has been expired');

    testWidgets('without a connection the calendar says where events live',
        (tester) async {
      final repo = FakeCalendar();
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      expect(find.textContaining('Events live in ANUM.'), findsOneWidget);
      expect(find.text('Sync now'), findsNothing);
    });

    testWidgets('a connected calendar shows when it synced; an owner syncs now',
        (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google()], canManage: true);
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      expect(
          find.text(
              'Synced with Google Calendar (owner@example.com). Synced 5 min ago.'),
          findsOneWidget);
      await tester.tap(find.text('Sync now'));
      await tester.pumpAndSettle();
      expect(repo.syncs, ['cal_1']);
      expect(find.text('Synced owner@example.com.'), findsOneWidget);
    });

    testWidgets('a member sees the status but cannot sync', (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google()], canManage: false);
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      expect(
          find.textContaining('Synced with Google Calendar'), findsOneWidget);
      expect(find.text('Sync now'), findsNothing);
    });

    testWidgets('a revoked grant says what is wrong and who fixes it',
        (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google(status: 'reauth_required')],
            canManage: true);
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      expect(find.text('Google Calendar sync needs attention'), findsOneWidget);
      expect(
          find.text('What went wrong: Token has been expired'), findsOneWidget);
      expect(
          find.textContaining('connects it again on the web'), findsOneWidget);
      expect(find.text('Sync now'), findsNothing);
    });

    testWidgets('a failed sync shows the reason and the fix', (tester) async {
      final repo = FakeCalendar()
        ..syncOutcome = 'error'
        ..status = CalendarSyncStatus([google()], canManage: true);
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      await tester.tap(find.text('Sync now'));
      await tester.pumpAndSettle();
      expect(repo.syncs, ['cal_1']);
      expect(find.textContaining('Backend Error'), findsWidgets);
    });

    testWidgets('the calendar still works when the sync status cannot load',
        (tester) async {
      final repo = FakeCalendar()..syncFails = true;
      await pumpScreen(
          tester,
          CalendarScreen(
              controller: controllerFor(repo), openLink: (_) async => true),
          height: 1400);
      expect(find.text('PMV Team Meeting'), findsWidgets);
      expect(find.textContaining('Events live in ANUM.'), findsNothing);
    });

    testWidgets('an event shows where it lives and asks for invitations',
        (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google()], canManage: true);
      repo.store['event_1'] = eventJson('event_1', extra: {
        'connection_id': 'cal_1',
        'sync_state': 'synced',
        'invitations_pending': true,
        'attendees': [
          {
            'id': 'attendee_1',
            'name': 'Sara Ali',
            'email': 'sara@example.com',
            'response': 'invited'
          },
        ],
      });
      final c = controllerFor(repo);
      await c.load();
      await pumpScreen(
          tester,
          EventScreen(
              controller: c, eventId: 'event_1', openLink: (_) async => true),
          height: 1400);
      await tester.pumpAndSettle();
      expect(find.text('Synced with Google Calendar'), findsOneWidget);
      expect(find.textContaining('have not been approved'), findsOneWidget);
      await tester.tap(find.text('Ask to send invitations'));
      await tester.pumpAndSettle();
      expect(repo.invitations, ['event_1']);
      expect(
          find.text(
              'Waiting for an owner to approve inviting sara@example.com. Nothing has been sent.'),
          findsOneWidget);
      expect(find.text('Ask to send invitations'), findsNothing);
      await tester.tap(find.text('Review approvals'));
      await tester.pumpAndSettle();
      expect(find.text('route /approvals'), findsOneWidget);
    });

    testWidgets('a viewer cannot ask for invitations', (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google()], canManage: false);
      repo.store['event_1'] = eventJson('event_1', canManage: false, extra: {
        'connection_id': 'cal_1',
        'sync_state': 'pending_push',
        'invitations_pending': true,
        'attendees': [
          {'id': 'a', 'name': 'Sara', 'email': 'sara@example.com'},
        ],
      });
      final c = controllerFor(repo);
      await c.load();
      await pumpScreen(
          tester,
          EventScreen(
              controller: c, eventId: 'event_1', openLink: (_) async => true),
          height: 1400);
      await tester.pumpAndSettle();
      expect(
          find.text('Waiting to be sent to Google Calendar'), findsOneWidget);
      expect(find.text('Ask to send invitations'), findsNothing);
    });

    testWidgets('sync reads right to left in Arabic at every width',
        (tester) async {
      final repo = FakeCalendar()
        ..status = CalendarSyncStatus([google()], canManage: true);
      for (final width in widths) {
        await pumpScreen(
            tester,
            CalendarScreen(
                controller: controllerFor(repo), openLink: (_) async => true),
            width: width,
            height: 1400,
            locale: const Locale('ar'));
        await tester.pump(const Duration(milliseconds: 50));
        expect(find.text('زامن الآن'), findsOneWidget, reason: 'at $width');
        expect(tester.takeException(), isNull, reason: 'overflow at $width');
      }
    });
  });
}
