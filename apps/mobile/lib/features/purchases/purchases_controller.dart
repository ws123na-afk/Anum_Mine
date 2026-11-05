import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'purchases_data.dart';

/// The Purchases list: search, a status group and paging.
class PurchasesController extends ChangeNotifier {
  PurchasesController(this.repository) {
    purchases = RecordsList<Purchase>((query, filter, token) =>
        repository.list(query: query, status: filter, token: token));
  }
  final PurchasesRepository repository;
  late final RecordsList<Purchase> purchases;

  Future<void> load() => purchases.load();

  Future<PurchaseDetail> create(Map<String, Object?> body,
      {required String idempotencyKey}) async {
    final detail =
        await repository.create(body, idempotencyKey: idempotencyKey);
    purchases.upsert(detail.purchase, (p) => p.id == detail.purchase.id);
    return detail;
  }

  /// A purchase changed on its page: show the new state in the list.
  void changed(Purchase purchase) =>
      purchases.upsert(purchase, (p) => p.id == purchase.id);

  @override
  void dispose() {
    purchases.dispose();
    super.dispose();
  }
}

/// One purchase: the detail, its timeline (paged), shipments and returns.
class PurchaseDetailController extends ChangeNotifier {
  PurchaseDetailController(this.repository, this.purchaseId, {this.onChanged}) {
    timeline = RecordsList<PurchaseEvent>(
        (_, __, token) => repository.events(purchaseId, token: token));
  }
  final PurchasesRepository repository;
  final String purchaseId;
  final ValueChanged<Purchase>? onChanged;
  late final RecordsList<PurchaseEvent> timeline;

  PurchaseDetail? detail;
  RecordsPhase phase = RecordsPhase.loading;
  RecordsFailure? failure;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() async {
    phase = RecordsPhase.loading;
    _notify();
    await _reload();
  }

  Future<void> _reload() async {
    try {
      detail = await repository.get(purchaseId);
      failure = null;
      phase = RecordsPhase.ready;
      onChanged?.call(detail!.purchase);
    } on Object catch (error) {
      failure = RecordsFailure.from(error);
      phase = failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
    if (phase == RecordsPhase.ready) await timeline.load();
  }

  /// Moves the status from the version on screen; a stale version reloads
  /// and the refusal goes back to the caller to show.
  Future<void> move(String to) async {
    final current = detail!.purchase;
    try {
      detail =
          await repository.move(purchaseId, to: to, version: current.version);
      onChanged?.call(detail!.purchase);
      _notify();
      await timeline.load();
    } on Object catch (error) {
      if (isStale(error)) await _reload();
      rethrow;
    }
  }

  Future<void> addShipment(Map<String, Object?> body) async {
    await repository.addShipment(purchaseId, body);
    await _reload();
  }

  Future<void> startReturn(Map<String, Object?> body,
      {required String idempotencyKey}) async {
    await repository.startReturn(purchaseId, body,
        idempotencyKey: idempotencyKey);
    await _reload();
  }

  Future<void> refresh() => _reload();

  @override
  void dispose() {
    _disposed = true;
    timeline.dispose();
    super.dispose();
  }
}
