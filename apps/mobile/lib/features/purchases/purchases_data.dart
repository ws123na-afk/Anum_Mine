import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';

/// Purchases, deliveries, returns and refunds (docs/purchases.md#api), the
/// fields the phone shows. A purchase never posts money: its payment and
/// refunds point at Finance transactions.

class PurchaseMoneyLink {
  const PurchaseMoneyLink({
    required this.linkId,
    required this.status,
    required this.amount,
    required this.currency,
    required this.recordedAsCash,
  });
  factory PurchaseMoneyLink.fromJson(JsonReader j) => PurchaseMoneyLink(
        linkId: j.optString('link_id'),
        status: j.string('status'),
        amount: j.string('amount'),
        currency: j.string('currency'),
        recordedAsCash: j.optBool('recorded_as_cash') ?? false,
      );
  final String? linkId;
  final String status, amount, currency;
  final bool recordedAsCash;
}

class PurchaseMoney {
  const PurchaseMoney(this.currency, this.amount);
  factory PurchaseMoney.fromJson(JsonReader j) =>
      PurchaseMoney(j.string('currency'), j.string('amount'));
  final String currency, amount;
}

class PurchaseDue {
  const PurchaseDue(
      {required this.kind, required this.dueOn, required this.daysLeft});
  factory PurchaseDue.fromJson(JsonReader j) => PurchaseDue(
      kind: j.string('kind'),
      dueOn: j.string('due_on'),
      daysLeft: j.integer('days_left'));
  final String kind, dueOn;
  final int daysLeft;
}

class Purchase {
  const Purchase({
    required this.id,
    required this.ownership,
    required this.merchant,
    required this.orderedOn,
    required this.currency,
    required this.status,
    required this.version,
    required this.canEdit,
    required this.canPay,
    required this.mine,
    required this.needsReview,
    this.orderRef,
    this.total,
    this.payment,
    this.expectedDeliveryOn,
    this.deliveredOn,
    this.returnWindowUntil,
    this.warrantyUntil,
    this.notes,
    this.archivedAt,
    this.allowedMoves = const [],
    this.due = const [],
    this.refunded = const [],
  });
  factory Purchase.fromJson(JsonReader j) => Purchase(
        id: j.string('id'),
        ownership: j.optString('ownership') ?? 'personal',
        merchant: j.string('merchant'),
        orderRef: j.optString('order_ref'),
        orderedOn: j.string('ordered_on'),
        currency: j.optString('currency') ?? '',
        total: j.optString('total'),
        payment: j.optObject('payment') == null
            ? null
            : PurchaseMoneyLink.fromJson(j.object('payment')),
        expectedDeliveryOn: j.optString('expected_delivery_on'),
        deliveredOn: j.optString('delivered_on'),
        returnWindowUntil: j.optString('return_window_until'),
        warrantyUntil: j.optString('warranty_until'),
        status: j.string('status'),
        needsReview: j.optBool('needs_review') ?? false,
        notes: j.optString('notes'),
        archivedAt: j.optString('archived_at'),
        version: j.integer('version'),
        canEdit: j.optBool('can_edit') ?? false,
        canPay: j.optBool('can_pay') ?? false,
        mine: j.optBool('mine') ?? false,
        allowedMoves: j.optStrings('allowed_moves') ?? const [],
        due: j.optObjects('due', PurchaseDue.fromJson),
        refunded: j.optObjects('refunded', PurchaseMoney.fromJson),
      );
  final String id, ownership, merchant, orderedOn, currency, status;
  final String? orderRef, total, expectedDeliveryOn, deliveredOn;
  final String? returnWindowUntil, warrantyUntil, notes, archivedAt;
  final PurchaseMoneyLink? payment;
  final int version;
  final bool canEdit, canPay, mine, needsReview;
  final List<String> allowedMoves;
  final List<PurchaseDue> due;
  final List<PurchaseMoney> refunded;
}

class PurchaseItem {
  const PurchaseItem(
      {required this.id,
      required this.description,
      required this.quantity,
      this.unitPrice});
  factory PurchaseItem.fromJson(JsonReader j) => PurchaseItem(
      id: j.string('id'),
      description: j.string('description'),
      quantity: j.optString('quantity') ?? '1',
      unitPrice: j.optString('unit_price'));
  final String id, description, quantity;
  final String? unitPrice;
}

/// A carrier's public tracking page for one shipment (docs/purchases.md#courier-tracking).
class TrackingLink {
  const TrackingLink(this.carrierName, this.url);
  final String carrierName;
  final Uri url;
}

class PurchaseShipment {
  const PurchaseShipment({
    required this.id,
    required this.status,
    this.carrier,
    this.trackingNumber,
    this.shippedOn,
    this.expectedOn,
    this.deliveredOn,
    this.note,
    this.detectedCarrier,
    this.links = const [],
    this.severalCarriers = false,
  });
  factory PurchaseShipment.fromJson(JsonReader j) {
    final tracking = j.optObject('tracking');
    // Only the https links the server built; anything else is not offered.
    TrackingLink? link(String? name, String? url) {
      final uri = url == null ? null : Uri.tryParse(url);
      return name == null || uri == null || uri.scheme != 'https'
          ? null
          : TrackingLink(name, uri);
    }

    final named =
        link(tracking?.optString('carrier_name'), tracking?.optString('url'));
    final candidates = [
      for (final c in tracking?.optObjects('candidates', (c) => c) ??
          const <JsonReader>[])
        if (link(c.optString('carrier_name'), c.optString('url')) case final l?)
          l
    ];
    return PurchaseShipment(
      id: j.string('id'),
      status: j.string('status'),
      carrier: j.optString('carrier'),
      trackingNumber: j.optString('tracking_number'),
      shippedOn: j.optString('shipped_on'),
      expectedOn: j.optString('expected_on'),
      deliveredOn: j.optString('delivered_on'),
      note: j.optString('note'),
      detectedCarrier: tracking?.optString('detected_from') == 'number'
          ? tracking?.optString('carrier_name')
          : null,
      links: named != null ? [named] : candidates,
      severalCarriers: named == null && candidates.length > 1,
    );
  }
  final String id, status;
  final String? carrier, trackingNumber, shippedOn, expectedOn, deliveredOn;
  final String? note;

  /// The carrier read from the tracking number when none was named.
  final String? detectedCarrier;

  /// The carrier's tracking page, or one per candidate when the number's
  /// format is shared by several carriers ([severalCarriers]).
  final List<TrackingLink> links;
  final bool severalCarriers;
}

class PurchaseReturn {
  const PurchaseReturn({
    required this.id,
    required this.status,
    required this.reason,
    required this.requestedOn,
    required this.currency,
    required this.refunded,
    required this.canRefund,
    this.amount,
    this.reasonNote,
    this.deadlineOn,
    this.refunds = const [],
  });
  factory PurchaseReturn.fromJson(JsonReader j) => PurchaseReturn(
        id: j.string('id'),
        status: j.string('status'),
        reason: j.string('reason'),
        reasonNote: j.optString('reason_note'),
        requestedOn: j.string('requested_on'),
        amount: j.optString('amount'),
        currency: j.optString('currency') ?? '',
        deadlineOn: j.optString('deadline_on'),
        refunded: j.optString('refunded') ?? '0',
        canRefund: j.optBool('can_refund') ?? false,
        refunds: j.optObjects('refunds', PurchaseMoneyLink.fromJson),
      );
  final String id, status, reason, requestedOn, currency, refunded;
  final String? amount, reasonNote, deadlineOn;
  final bool canRefund;
  final List<PurchaseMoneyLink> refunds;
}

class PurchaseEvent {
  const PurchaseEvent({
    required this.id,
    required this.kind,
    required this.occurredAt,
    this.fromStatus,
    this.toStatus,
    this.note,
  });
  factory PurchaseEvent.fromJson(JsonReader j) => PurchaseEvent(
        id: j.string('id'),
        kind: j.string('kind'),
        fromStatus: j.optString('from_status'),
        toStatus: j.optString('to_status'),
        note: j.optString('note'),
        occurredAt: j.string('occurred_at'),
      );
  final String id, kind, occurredAt;
  final String? fromStatus, toStatus, note;
}

class PurchaseDetail {
  const PurchaseDetail({
    required this.purchase,
    required this.items,
    required this.shipments,
    required this.returns,
    required this.events,
    required this.eventsTotal,
  });
  factory PurchaseDetail.fromJson(JsonReader j) => PurchaseDetail(
        purchase: Purchase.fromJson(j),
        items: j.optObjects('items', PurchaseItem.fromJson),
        shipments: j.optObjects('shipments', PurchaseShipment.fromJson),
        returns: j.optObjects('returns', PurchaseReturn.fromJson),
        events: j.optObjects('events', PurchaseEvent.fromJson),
        eventsTotal: j.optInt('events_total') ?? 0,
      );
  final Purchase purchase;
  final List<PurchaseItem> items;
  final List<PurchaseShipment> shipments;
  final List<PurchaseReturn> returns;
  final List<PurchaseEvent> events;
  final int eventsTotal;
}

class PurchaseCandidate {
  const PurchaseCandidate({
    required this.canonicalId,
    required this.postedOn,
    required this.merchant,
    required this.amount,
    required this.currency,
    required this.remaining,
    required this.linked,
  });
  factory PurchaseCandidate.fromJson(JsonReader j) => PurchaseCandidate(
        canonicalId: j.string('canonical_id'),
        postedOn: j.string('posted_on'),
        merchant: j.optString('merchant') ?? '',
        amount: j.string('amount'),
        currency: j.string('currency'),
        remaining: j.optString('remaining') ?? j.string('amount'),
        linked: j.optBool('linked_to_purchase') ?? false,
      );
  final String canonicalId, postedOn, merchant, amount, currency, remaining;
  final bool linked;
}

abstract interface class PurchasesRepository {
  Future<RecordsChunk<Purchase>> list(
      {String query, String status, String? token});
  Future<PurchaseDetail> get(String id);
  Future<PurchaseDetail> create(Map<String, Object?> body,
      {required String idempotencyKey});
  Future<PurchaseDetail> move(String id,
      {required String to, required int version, String? note});
  Future<RecordsChunk<PurchaseEvent>> events(String id, {String? token});
  Future<PurchaseShipment> addShipment(String id, Map<String, Object?> body);
  Future<PurchaseReturn> startReturn(String id, Map<String, Object?> body,
      {required String idempotencyKey});
  Future<List<PurchaseCandidate>> candidates(String kind,
      {String query, String? around, String? amount});
  Future<PurchaseMoneyLink> linkPayment(String id,
      {required String canonicalId, required String idempotencyKey});
  Future<PurchaseMoneyLink> linkRefund(String returnId,
      {required String canonicalId, required String idempotencyKey});
}

class ApiPurchasesRepository implements PurchasesRepository {
  const ApiPurchasesRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/purchases';
  static const pageSize = 20;
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<RecordsChunk<Purchase>> list(
          {String query = '', String status = '', String? token}) async =>
      RecordsChunk.offsetPage(
          JsonReader(await api.request(
              'GET',
              '$_base${recordsQuery({
                    'q': query,
                    'status': status,
                    'offset': token ?? 0,
                    'limit': pageSize,
                  })}')),
          Purchase.fromJson);

  @override
  Future<PurchaseDetail> get(String id) async => PurchaseDetail.fromJson(
      JsonReader(await api.request('GET', '$_base/${_id(id)}')));

  @override
  Future<PurchaseDetail> create(Map<String, Object?> body,
          {required String idempotencyKey}) async =>
      PurchaseDetail.fromJson(JsonReader(await api.request('POST', _base,
          body: body, headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<PurchaseDetail> move(String id,
          {required String to, required int version, String? note}) async =>
      PurchaseDetail.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/status', body: {
        'to': to,
        'version': version,
        if (note != null) 'note': note
      })));

  @override
  Future<RecordsChunk<PurchaseEvent>> events(String id,
          {String? token}) async =>
      RecordsChunk.offsetPage(
          JsonReader(await api.request(
              'GET',
              '$_base/${_id(id)}/events'
                  '${recordsQuery({
                    'offset': token ?? 0,
                    'limit': pageSize
                  })}')),
          PurchaseEvent.fromJson);

  @override
  Future<PurchaseShipment> addShipment(
          String id, Map<String, Object?> body) async =>
      PurchaseShipment.fromJson(JsonReader(await api
          .request('POST', '$_base/${_id(id)}/shipments', body: body)));

  @override
  Future<PurchaseReturn> startReturn(String id, Map<String, Object?> body,
          {required String idempotencyKey}) async =>
      PurchaseReturn.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(id)}/returns',
          body: body, headers: {'Idempotency-Key': idempotencyKey})));

  @override
  Future<List<PurchaseCandidate>> candidates(String kind,
          {String query = '', String? around, String? amount}) async =>
      JsonReader(await api.request(
              'GET',
              '$_base/payments${recordsQuery({
                    'kind': kind,
                    'q': query,
                    'around': around,
                    'amount': amount
                  })}'))
          .optObjects('items', PurchaseCandidate.fromJson);

  @override
  Future<PurchaseMoneyLink> linkPayment(String id,
          {required String canonicalId,
          required String idempotencyKey}) async =>
      PurchaseMoneyLink.fromJson(JsonReader(await api.request(
              'POST', '$_base/${_id(id)}/payment',
              body: {'canonical_id': canonicalId},
              headers: {'Idempotency-Key': idempotencyKey}))
          .object('payment'));

  @override
  Future<PurchaseMoneyLink> linkRefund(String returnId,
          {required String canonicalId,
          required String idempotencyKey}) async =>
      PurchaseMoneyLink.fromJson(JsonReader(await api.request(
              'POST', '$_base/returns/${_id(returnId)}/refunds',
              body: {'canonical_id': canonicalId},
              headers: {'Idempotency-Key': idempotencyKey}))
          .object('refund'));
}

/// A stale version (someone changed the purchase): reload and try again.
bool isStale(Object error) =>
    error is ApiException &&
    error.statusCode == 409 &&
    (error.code == 'stale' || error.code == 'version_conflict');
