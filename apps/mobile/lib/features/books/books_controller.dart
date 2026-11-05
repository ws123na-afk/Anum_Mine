import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'books_data.dart';

enum BooksPeriod { year, month, last }

/// The business books on the phone: setup and permissions, the P&L for a
/// period and basis, and the bills list with its open and overdue totals.
class BooksController extends ChangeNotifier {
  BooksController(this.repository, {DateTime Function()? today})
      : _today = today ?? DateTime.now {
    bills = RecordsList<BooksBill>((query, filter, token) async {
      final chunk =
          await repository.bills(query: query, status: filter, token: token);
      if (token == null) {
        openTotals = chunk.open;
        overdueTotals = chunk.overdue;
      }
      return chunk;
    });
  }
  final BooksRepository repository;
  final DateTime Function() _today;
  late final RecordsList<BooksBill> bills;

  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  BooksSetup? setup;

  RecordsPhase reportPhase = RecordsPhase.loading;
  RecordsFailure? reportFailure;
  BooksReport? report;
  BooksPeriod period = BooksPeriod.year;
  String? basis;

  List<BooksAmount> openTotals = const [], overdueTotals = const [];
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    try {
      setup = await repository.setup();
      failure = null;
      phase =
          setup!.permissions.read ? RecordsPhase.ready : RecordsPhase.denied;
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase == RecordsPhase.ready && setup!.configured) {
      basis ??= setup!.basis;
      await Future.wait([loadReport(), bills.load()]);
    }
  }

  /// From and to of [period]: the financial year the API proposes, or a
  /// calendar month.
  ({String? from, String? to}) range() {
    final today = _today();
    switch (period) {
      case BooksPeriod.year:
        return (from: setup?.periodFrom, to: setup?.periodTo);
      case BooksPeriod.month:
        return (
          from: isoDay(DateTime(today.year, today.month)),
          to: isoDay(DateTime(today.year, today.month + 1, 0))
        );
      case BooksPeriod.last:
        return (
          from: isoDay(DateTime(today.year, today.month - 1)),
          to: isoDay(DateTime(today.year, today.month, 0))
        );
    }
  }

  /// Only the newest report request may change what is shown: a slower
  /// answer for an earlier period or basis is dropped.
  int _reportGeneration = 0;

  Future<void> loadReport() async {
    final generation = ++_reportGeneration;
    reportPhase = RecordsPhase.loading;
    _notify();
    final dates = range();
    try {
      final loaded =
          await repository.report(from: dates.from, to: dates.to, basis: basis);
      if (generation != _reportGeneration) return;
      report = loaded;
      reportFailure = null;
      reportPhase = RecordsPhase.ready;
    } on Object catch (error) {
      if (generation != _reportGeneration) return;
      reportFailure = RecordsFailure.from(error);
      reportPhase =
          reportFailure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
  }

  Future<void> choose({BooksPeriod? period, String? basis}) {
    if (period != null) this.period = period;
    if (basis != null) this.basis = basis;
    return loadReport();
  }

  Future<void> saveSettings(Map<String, Object?> body) async {
    await repository.saveSettings({...body, 'version': setup?.version});
    await load();
  }

  /// A bill changed on its page (a payment or a void): reload the bills
  /// with the same search and filter, so the open and overdue totals and
  /// the filter's rows follow, and the P&L.
  Future<void> billChanged(BooksBill bill) async {
    bills.upsert(bill, (b) => b.id == bill.id);
    await Future.wait([bills.load(), loadReport()]);
  }

  @override
  void dispose() {
    _disposed = true;
    bills.dispose();
    super.dispose();
  }
}
