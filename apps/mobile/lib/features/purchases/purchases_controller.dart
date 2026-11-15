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

  /// A purchase changed on its page: show the new state in the list. With
  /// a status group chosen, the list is read again, since the purchase may
  /// have moved in or out of the group.
  void changed(Purchase purchase) {
    if (purchases.filter.isNotEmpty) {
      purchases.load();
    } else {
      purchases.upsert(purchase, (p) => p.id == purchase.id);
    }
  }

  /// A purchase was deleted on its page: it leaves the list.
  void removed(String id) => purchases.remove((p) => p.id == id);

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

  /// Runs a change and reads the purchase again. A stale version reloads
  /// too, and the refusal goes back to the caller to show.
  Future<void> _change(Future<void> Function() work) async {
    try {
      await work();
    } on Object catch (error) {
      if (isStale(error)) await _reload();
      rethrow;
    }
    await _reload();
  }

  /// Changes the purchase's details from the version on screen.
  Future<void> update(Map<String, Object?> changes) => _change(() => repository
      .update(purchaseId, {...changes, 'version': detail!.purchase.version}));

  /// Sets or clears the receipt (a vault document id).
  Future<void> setReceipt(String? documentId) =>
      update({'document_id': documentId});

  Future<void> archive(bool archived) =>
      _change(() => repository.archive(purchaseId,
          version: detail!.purchase.version, archived: archived));

  /// Deletes the purchase; the caller leaves the page.
  Future<void> delete() async {
    await repository.delete(purchaseId);
    deleted = true;
    _notify();
  }

  /// True once the purchase was deleted here.
  bool deleted = false;

  Future<void> addNote(String note) =>
      _change(() => repository.addNote(purchaseId, note));

  Future<void> updateShipment(
          PurchaseShipment shipment, Map<String, Object?> changes) =>
      _change(() => repository.updateShipment(
          shipment.id, {...changes, 'version': shipment.version}));

  Future<void> deleteShipment(PurchaseShipment shipment) =>
      _change(() => repository.deleteShipment(shipment.id));

  Future<void> updateReturn(
          PurchaseReturn item, Map<String, Object?> changes) =>
      _change(() => repository
          .updateReturn(item.id, {...changes, 'version': item.version}));

  Future<void> moveReturn(PurchaseReturn item, String to, {String? note}) =>
      _change(() => repository.moveReturn(item.id,
          to: to, version: item.version, note: note));

  Future<void> deleteReturn(PurchaseReturn item) =>
      _change(() => repository.deleteReturn(item.id));

  Future<void> recordCash(
          {required String accountId,
          required String amount,
          required String postedOn,
          required String idempotencyKey,
          PurchaseReturn? refundOf}) =>
      _change(() => refundOf == null
          ? repository.recordCash(purchaseId,
              accountId: accountId,
              amount: amount,
              postedOn: postedOn,
              idempotencyKey: idempotencyKey)
          : repository.recordCashRefund(refundOf.id,
              accountId: accountId,
              amount: amount,
              postedOn: postedOn,
              idempotencyKey: idempotencyKey));

  Future<void> unlinkPayment() =>
      _change(() => repository.unlinkPayment(purchaseId));

  Future<void> unlinkRefund(PurchaseReturn item, String linkId) =>
      _change(() => repository.unlinkRefund(item.id, linkId));

  @override
  void dispose() {
    _disposed = true;
    timeline.dispose();
    super.dispose();
  }
}
