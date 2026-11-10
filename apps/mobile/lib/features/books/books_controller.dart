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
        evidenceMissing.clear();
      }
      await _readEvidence(chunk.items);
      return chunk;
    });
    review = RecordsList<ReviewItem>((query, filter, token) async {
      final chunk = await repository.review(kind: filter, token: token);
      if (token == null) reviewSummary = chunk;
      return chunk;
    });
  }

  /// Which bills of the loaded pages need a supporting document (at or
  /// above the evidence threshold with nothing attached). Read with the
  /// page; when the person may not read the vault the badges stay off and
  /// the list still shows.
  final Set<String> evidenceMissing = {};

  Future<void> _readEvidence(List<BooksBill> page) async {
    final ids = [
      for (final b in page)
        if (b.status != 'void') b.id
    ];
    if (ids.isEmpty) return;
    try {
      final found = await repository.evidence('bill', ids);
      for (final entry in found.items.entries) {
        if (entry.value.missing) {
          evidenceMissing.add(entry.key);
        } else {
          evidenceMissing.remove(entry.key);
        }
      }
    } on Object {
      // A badge is a hint: a refusal (no vault access) or a failure leaves
      // the bills list as it is.
    }
  }

  /// The Accountant review (owners and accountants only).
  late final RecordsList<ReviewItem> review;

  /// The first page's counts, close checklist and unavailable checks.
  ReviewChunk? reviewSummary;

  /// The draft bill being approved, so its button waits.
  String? approving;
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
      await Future.wait([
        loadReport(),
        bills.load(),
        if (setup!.permissions.account) review.load(),
      ]);
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

  /// Approves a draft bill shown in the review with its version, or a
  /// pending adjustment; the API refuses one's own (maker and checker).
  /// Throws the refusal.
  Future<void> approve(ReviewItem item) async {
    final id = item.refId, version = item.version;
    final adjustment = item.kind == 'adjustment_approval';
    if (id == null || (!adjustment && version == null) || approving != null) {
      return;
    }
    approving = item.key;
    _notify();
    try {
      if (adjustment) {
        await repository.approveAdjustment(id);
      } else {
        await repository.approveBill(id, version!);
      }
    } finally {
      approving = null;
      _notify();
    }
    await Future.wait(
        [review.load(), if (!adjustment) bills.load(), loadReport()]);
  }

  /// Rejects a pending adjustment with [reason]; it is never posted.
  /// Throws the refusal.
  Future<void> reject(ReviewItem item, String reason) async {
    final id = item.refId;
    if (id == null || approving != null) return;
    approving = item.key;
    _notify();
    try {
      await repository.voidAdjustment(id, reason);
    } finally {
      approving = null;
      _notify();
    }
    await review.load();
  }

  /// Records an adjustment; an accountant's at or above the threshold comes
  /// back `pending`. Reloads the review and the P&L. Throws the refusal.
  Future<BooksAdjustment> recordAdjustment(Map<String, Object?> body,
      {required String idempotencyKey}) async {
    final saved =
        await repository.createAdjustment(body, idempotencyKey: idempotencyKey);
    await Future.wait([review.load(), loadReport()]);
    return saved;
  }

  /// Evidence on a bill changed: its badge follows.
  void evidenceChanged(String billId, {required bool missing}) {
    if (missing) {
      evidenceMissing.add(billId);
    } else {
      evidenceMissing.remove(billId);
    }
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    review.dispose();
    bills.dispose();
    super.dispose();
  }
}
