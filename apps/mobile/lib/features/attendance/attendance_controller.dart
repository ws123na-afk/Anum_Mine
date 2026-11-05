import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'attendance_data.dart';

/// The person's own attendance: today and its buttons, the days by week,
/// daily reports and corrections.
class AttendanceController extends ChangeNotifier {
  AttendanceController(this.repository, {DateTime Function()? now})
      : _now = now ?? DateTime.now {
    reports = RecordsList<AttendanceReport>(
        (query, _, token) => repository.reports(query: query, token: token));
    corrections = RecordsList<AttendanceCorrection>(
        (_, __, token) => repository.corrections(token: token));
  }
  final AttendanceRepository repository;
  final DateTime Function() _now;
  late final RecordsList<AttendanceReport> reports;
  late final RecordsList<AttendanceCorrection> corrections;

  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  AttendanceToday? today;
  List<AttendanceSite> sites = const [];

  /// The button being recorded, so it cannot be pressed twice.
  String? checking;

  /// The last day of the week shown in History (today's week first).
  late DateTime weekEnd = _dayOnly(_now());
  RecordsPhase weekPhase = RecordsPhase.loading;
  RecordsFailure? weekFailure;
  List<AttendanceDay> week = const [];
  bool _disposed = false;

  static DateTime _dayOnly(DateTime at) => DateTime(at.year, at.month, at.day);
  DateTime get weekStart => weekEnd.subtract(const Duration(days: 6));
  bool get atLatestWeek => !weekEnd.isBefore(_dayOnly(_now()));

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    try {
      today = await repository.today();
      failure = null;
      phase = RecordsPhase.ready;
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase != RecordsPhase.ready) return;
    try {
      sites = [
        for (final site in await repository.sites())
          if (!site.archived) site
      ];
    } on Object {
      sites = const [];
    }
    _notify();
    await Future.wait([loadWeek(), reports.load(), corrections.load()]);
  }

  Future<void> refreshToday() async {
    try {
      today = await repository.today();
      _notify();
    } on Object {
      // The next load shows the current state.
    }
  }

  /// Records [kind] now with one idempotency key per press. A repeat of
  /// the same key (a lost answer) is stored once by the API.
  Future<AttendanceEvent> check(String kind, {String? siteId, String? key}) async {
    checking = kind;
    _notify();
    try {
      final event = await repository.check(kind,
          idempotencyKey: key ?? newIdempotencyKey('att'), siteId: siteId);
      await refreshToday();
      return event;
    } finally {
      checking = null;
      _notify();
    }
  }

  Future<void> loadWeek() async {
    weekPhase = RecordsPhase.loading;
    _notify();
    try {
      week = (await repository.days(isoDay(weekStart), isoDay(weekEnd)))
          .reversed
          .toList();
      weekFailure = null;
      weekPhase = RecordsPhase.ready;
    } on Object catch (error) {
      weekFailure = RecordsFailure.from(error);
      weekPhase =
          weekFailure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
  }

  Future<void> shiftWeek(int weeks) {
    weekEnd = weekEnd.add(Duration(days: 7 * weeks));
    final latest = _dayOnly(_now());
    if (weekEnd.isAfter(latest)) weekEnd = latest;
    return loadWeek();
  }

  Future<void> correct(Map<String, Object?> body) async {
    final correction = await repository.correct(body);
    corrections.upsert(correction, (c) => c.id == correction.id);
    await Future.wait([refreshToday(), loadWeek()]);
  }

  Future<AttendanceReport> saveReport(
      String day, Map<String, Object?> body) async {
    final saved = await repository.saveReport(day, body);
    reports.upsert(saved, (r) => r.day == saved.day);
    return saved;
  }

  Future<void> deleteReport(String day) async {
    await repository.deleteReport(day);
    reports.remove((r) => r.day == day);
  }

  @override
  void dispose() {
    _disposed = true;
    reports.dispose();
    corrections.dispose();
    super.dispose();
  }
}
