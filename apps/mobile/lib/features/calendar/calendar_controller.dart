import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'calendar_models.dart';
import 'calendar_repository.dart';

enum CalendarPhase { idle, loading, ready, error, denied }

String _describe(Object e) => e is ApiException ? e.readable : '$e';

/// Calendar & next actions (docs/calendar.md): a week of days, the selected
/// day's timeline, suggested next steps, and changes to events.
class CalendarController extends ChangeNotifier {
  CalendarController(this.repository, {DateTime Function()? clock})
      : clock = clock ?? DateTime.now {
    selectedDay = dayOf(this.clock());
    weekStart = selectedDay;
  }

  final CalendarRepository repository;
  final DateTime Function() clock;

  CalendarPhase phase = CalendarPhase.idle;
  String? error;
  late DateTime weekStart;
  late DateTime selectedDay;
  List<CalendarEvent> events = const [];
  bool truncated = false;
  bool canWrite = false;

  /// Calendar sync status (docs/calendar.md#sync); null until it loaded or
  /// when it could not (the calendar itself still works).
  CalendarSyncStatus? sync;

  List<NextStep> steps = const [];
  String? stepsError;
  bool stepsLoading = false;

  /// The action in progress (`create`, `save:ID`, `delete:ID`, `step:ID`...).
  String? busy;
  String? notice;
  bool noticeIsError = false;

  List<DateTime> get days =>
      [for (var i = 0; i < 7; i++) weekStart.add(Duration(days: i))];

  List<CalendarEvent> get dayEvents {
    final start = selectedDay;
    final end = start.add(const Duration(days: 1));
    return [
      for (final e in events)
        if (e.startsAt.toLocal().isBefore(end) &&
            e.endsAt.toLocal().isAfter(start))
          e
    ];
  }

  int countOn(DateTime day) {
    final end = day.add(const Duration(days: 1));
    return events
        .where((e) =>
            e.startsAt.toLocal().isBefore(end) &&
            e.endsAt.toLocal().isAfter(day))
        .length;
  }

  Future<void> load() async {
    phase = CalendarPhase.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.events(
          weekStart, weekStart.add(const Duration(days: 7)));
      events = page.items;
      truncated = page.truncated;
      canWrite = page.canWrite;
      phase = CalendarPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 403 ? CalendarPhase.denied : CalendarPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = CalendarPhase.error;
      error = _describe(e);
    }
    notifyListeners();
    if (phase == CalendarPhase.ready) {
      await loadSteps();
      await loadSync();
    }
  }

  Future<void> loadSync() async {
    try {
      sync = await repository.sync();
    } on Object {
      sync = null;
    }
    notifyListeners();
  }

  /// "Sync now" for an owner; the connection's own state tells how it went.
  Future<void> syncNow(SyncConnection connection) async {
    await _run('sync:${connection.id}', () async {
      final after = await repository.syncNow(connection.id);
      if (!after.healthy) {
        throw ApiException(409, after.lastError ?? after.status);
      }
      final page = await repository.events(
          weekStart, weekStart.add(const Duration(days: 7)));
      events = page.items;
    });
    await loadSync();
  }

  /// Asks for invitations; null on failure (the notice says why).
  Future<List<String>?> askInvitations(CalendarEvent event) async {
    List<String>? people;
    await _run('invite:${event.id}', () async {
      people = await repository.requestInvitations(event.id);
    });
    return people;
  }

  Future<void> loadSteps() async {
    stepsLoading = true;
    stepsError = null;
    notifyListeners();
    try {
      steps = await repository.nextSteps();
    } on Object catch (e) {
      stepsError = _describe(e);
    }
    stepsLoading = false;
    notifyListeners();
  }

  void selectDay(DateTime day) {
    selectedDay = dayOf(day);
    notifyListeners();
  }

  Future<void> shiftWeek(int weeks) async {
    weekStart = weekStart.add(Duration(days: 7 * weeks));
    selectedDay = weekStart;
    await load();
  }

  Future<void> today() async {
    weekStart = dayOf(clock());
    selectedDay = weekStart;
    await load();
  }

  Future<CalendarEvent?> create(EventDraft draft) async {
    CalendarEvent? created;
    await _run('create', () async {
      created = await repository.create(draft);
      events = [...events, created!]
        ..sort((a, b) => a.startsAt.compareTo(b.startsAt));
    });
    return created;
  }

  Future<CalendarEvent?> save(String id, Map<String, Object?> changes) async {
    CalendarEvent? saved;
    await _run('save:$id', () async {
      saved = await repository.update(id, changes);
      _replace(saved!);
    });
    return saved;
  }

  Future<CalendarEvent?> respond(
      CalendarEvent event, Attendee attendee, String response) async {
    final attendeeId = attendee.id;
    if (attendeeId == null) return null;
    CalendarEvent? saved;
    await _run('respond:${event.id}', () async {
      saved = await repository.respond(event.id, attendeeId, response);
      _replace(saved!);
    });
    return saved;
  }

  Future<bool> delete(String id) async {
    var ok = false;
    await _run('delete:$id', () async {
      await repository.delete(id);
      events = [
        for (final e in events)
          if (e.id != id) e
      ];
      ok = true;
    });
    return ok;
  }

  /// A task step creates the task and returns its id; an open step returns
  /// null (the screen opens its destination).
  Future<String?> runStep(NextStep step) async {
    if (!step.isTask) return null;
    String? task;
    await _run('step:${step.id}', () async {
      task = await repository.createTask(
          step.taskTitle ?? step.title, step.taskPrompt ?? step.title);
    });
    return task;
  }

  void _replace(CalendarEvent event) {
    events = [
      for (final e in events)
        if (e.id == event.id) event else e
    ];
    if (!events.any((e) => e.id == event.id)) events = [...events, event];
    if (event.cancelled) {
      events = [
        for (final e in events)
          if (e.id != event.id) e
      ];
    }
  }

  Future<void> _run(String kind, Future<void> Function() action) async {
    busy = kind;
    notice = null;
    notifyListeners();
    try {
      await action();
    } on Object catch (e) {
      notice = _describe(e);
      noticeIsError = true;
    }
    busy = null;
    notifyListeners();
  }

  void clearNotice() {
    notice = null;
    noticeIsError = false;
  }
}
