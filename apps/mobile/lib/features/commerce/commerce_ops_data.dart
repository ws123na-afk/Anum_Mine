import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'commerce_controller.dart' show CommerceLoad;

/// What a person can do in Commerce besides read sales (docs/commerce.md):
/// edit the catalogue, propose price and stock changes (approved in
/// Approvals), acknowledge alerts, and sync or switch channels. The server
/// checks `commerce:operate` and `commerce:manage` on every call.

class CommerceRights {
  const CommerceRights({this.operate = false, this.manage = false});
  final bool operate, manage;
}

class CommerceListing {
  const CommerceListing({
    required this.id,
    required this.connectorId,
    required this.sku,
    required this.marketplace,
    required this.title,
    required this.status,
    this.price,
    this.currency,
    this.quantity,
    this.buyBoxWinner,
    this.lowestCompetitorPrice,
  });
  final String id, connectorId, sku, marketplace, title, status;
  final double? price, lowestCompetitorPrice;
  final String? currency;
  final int? quantity;
  final bool? buyBoxWinner;

  factory CommerceListing.fromJson(JsonReader r) => CommerceListing(
        id: r.string('id'),
        connectorId: r.string('connector_id'),
        sku: r.string('sku'),
        marketplace: r.optString('marketplace') ?? '',
        title: r.optString('title') ?? '',
        status: r.optString('status') ?? 'active',
        price: r.optNumber('price'),
        currency: r.optString('currency'),
        quantity: r.optInt('quantity'),
        buyBoxWinner: r.optBool('buy_box_winner'),
        lowestCompetitorPrice: r.optNumber('lowest_competitor_price'),
      );
}

class CommerceProduct {
  const CommerceProduct({
    required this.id,
    required this.sku,
    required this.title,
    required this.listings,
    this.titleAr,
    this.brand,
    this.costPrice,
    this.currency,
    this.lowStockThreshold,
    this.totalStock,
    this.tyreSize,
  });
  final String id, sku, title;
  final String? titleAr, brand, currency, tyreSize;
  final double? costPrice;
  final int? lowStockThreshold, totalStock;
  final List<CommerceListing> listings;

  factory CommerceProduct.fromJson(JsonReader r) => CommerceProduct(
        id: r.string('id'),
        sku: r.string('sku'),
        title: r.optString('title') ?? '',
        titleAr: r.optString('title_ar'),
        brand: r.optString('brand'),
        costPrice: r.optNumber('cost_price'),
        currency: r.optString('currency'),
        lowStockThreshold: r.optInt('low_stock_threshold'),
        totalStock: r.optInt('total_stock'),
        tyreSize: r.optObject('tyre')?.optString('size'),
        listings: r.optObjects('listings', CommerceListing.fromJson),
      );
}

class CommerceReturn {
  const CommerceReturn({
    required this.id,
    required this.orderId,
    required this.quantity,
    required this.status,
    required this.returnedAt,
    this.sku,
    this.reason,
    this.amount,
    this.currency,
  });
  final String id, orderId, status;
  final String? sku, reason, currency;
  final int quantity;
  final double? amount;
  final DateTime returnedAt;

  factory CommerceReturn.fromJson(JsonReader r) => CommerceReturn(
        id: r.string('id'),
        orderId: r.string('order_external_id'),
        quantity: r.optInt('quantity') ?? 1,
        status: r.optString('status') ?? 'refunded',
        returnedAt: r.date('returned_at'),
        sku: r.optString('sku'),
        reason: r.optString('reason'),
        amount: r.optNumber('amount'),
        currency: r.optString('currency'),
      );
}

class CommerceAlert {
  const CommerceAlert({
    required this.id,
    required this.kind,
    required this.severity,
    required this.title,
    required this.detail,
    required this.status,
    required this.createdAt,
  });
  final String id, kind, severity, title, detail, status;
  final DateTime createdAt;

  factory CommerceAlert.fromJson(JsonReader r) => CommerceAlert(
        id: r.string('id'),
        kind: r.string('kind'),
        severity: r.string('severity'),
        title: r.string('title'),
        detail: r.optString('detail') ?? '',
        status: r.string('status'),
        createdAt: r.date('created_at'),
      );
}

class CommerceChannel {
  const CommerceChannel({
    required this.id,
    required this.channel,
    required this.name,
    required this.marketplaces,
    required this.enabled,
    required this.health,
    this.lastError,
    this.lastSyncAt,
  });
  final String id, channel, name, health;
  final List<String> marketplaces;
  final bool enabled;
  final String? lastError;
  final DateTime? lastSyncAt;

  factory CommerceChannel.fromJson(JsonReader r) => CommerceChannel(
        id: r.string('id'),
        channel: r.string('channel'),
        name: r.string('name'),
        marketplaces: r.optStrings('marketplace_names') ?? const [],
        enabled: r.optBool('enabled') ?? true,
        health: r.string('health'),
        lastError: r.optString('last_error'),
        lastSyncAt: r.optDate('last_sync_at'),
      );
}

class CommercePage<T> {
  const CommercePage(this.items, this.total);
  final List<T> items;
  final int total;
}

/// The answer to a price or stock proposal: nothing changes on the channel
/// until the approval is decided.
class ChangeProposal {
  const ChangeProposal(this.listing, this.approvalId);
  final CommerceListing listing;
  final String approvalId;
}

abstract interface class CommerceOpsRepository {
  Future<CommerceRights> rights();
  Future<CommercePage<CommerceProduct>> products(
      {String? query, required int offset, int limit});
  Future<CommerceProduct> createProduct(Map<String, Object?> fields);
  Future<CommerceProduct> updateProduct(String id, Map<String, Object?> fields);
  Future<void> deleteProduct(String id);
  Future<CommercePage<CommerceListing>> listings(
      {String? query, required int offset, int limit});
  Future<ChangeProposal> proposePrice(String listingId, double price);
  Future<ChangeProposal> proposeStock(String listingId, int quantity);
  Future<CommercePage<CommerceReturn>> returns(
      {required int days, required int offset, int limit});
  Future<CommercePage<CommerceAlert>> alerts(
      {String? status, required int offset, int limit});
  Future<CommerceAlert> acknowledge(String id);
  Future<List<CommerceChannel>> channels();
  Future<String> testChannel(String id);
  Future<String> syncChannel(String id);
  Future<CommerceChannel> setEnabled(String id, bool enabled);
  Future<void> deleteChannel(String id);
}

class ApiCommerceOpsRepository implements CommerceOpsRepository {
  ApiCommerceOpsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/commerce';

  String _id(String value) => Uri.encodeComponent(value);

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!,
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonReader> _call(String method, String path,
          {Map<String, Object?>? body}) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  CommercePage<T> _page<T>(JsonReader r, T Function(JsonReader) read) =>
      CommercePage(r.objects('items', read), r.integer('total'));

  @override
  Future<CommerceRights> rights() async {
    final p = (await _call('GET', '/setup')).object('permissions');
    return CommerceRights(
        operate: p.boolean('operate'), manage: p.boolean('manage'));
  }

  @override
  Future<CommercePage<CommerceProduct>> products(
          {String? query, required int offset, int limit = 20}) async =>
      _page(
          await _call(
              'GET',
              '/products${_q({
                    'q': query,
                    'offset': '$offset',
                    'limit': '$limit'
                  })}'),
          CommerceProduct.fromJson);

  @override
  Future<CommerceProduct> createProduct(Map<String, Object?> fields) async =>
      CommerceProduct.fromJson(await _call('POST', '/products', body: fields));

  @override
  Future<CommerceProduct> updateProduct(
          String id, Map<String, Object?> fields) async =>
      CommerceProduct.fromJson(
          await _call('PATCH', '/products/${_id(id)}', body: fields));

  @override
  Future<void> deleteProduct(String id) =>
      api.request('DELETE', '$_base/products/${_id(id)}');

  @override
  Future<CommercePage<CommerceListing>> listings(
          {String? query, required int offset, int limit = 20}) async =>
      _page(
          await _call(
              'GET',
              '/listings${_q({
                    'q': query,
                    'offset': '$offset',
                    'limit': '$limit'
                  })}'),
          CommerceListing.fromJson);

  ChangeProposal _proposal(JsonReader r) => ChangeProposal(
      CommerceListing.fromJson(r.object('listing')),
      r.object('approval').string('id'));

  @override
  Future<ChangeProposal> proposePrice(String id, double price) async =>
      _proposal(await _call('POST', '/listings/${_id(id)}/price-change',
          body: {'price': price}));

  @override
  Future<ChangeProposal> proposeStock(String id, int quantity) async =>
      _proposal(await _call('POST', '/listings/${_id(id)}/stock-change',
          body: {'quantity': quantity}));

  @override
  Future<CommercePage<CommerceReturn>> returns(
          {required int days, required int offset, int limit = 20}) async =>
      _page(
          await _call('GET', '/returns?days=$days&offset=$offset&limit=$limit'),
          CommerceReturn.fromJson);

  @override
  Future<CommercePage<CommerceAlert>> alerts(
          {String? status, required int offset, int limit = 20}) async =>
      _page(
          await _call(
              'GET',
              '/alerts${_q({
                    'status': status,
                    'offset': '$offset',
                    'limit': '$limit'
                  })}'),
          CommerceAlert.fromJson);

  @override
  Future<CommerceAlert> acknowledge(String id) async => CommerceAlert.fromJson(
      await _call('POST', '/alerts/${_id(id)}/acknowledge'));

  @override
  Future<List<CommerceChannel>> channels() async {
    // The route returns a bare list; the transport wraps it as `data`.
    final raw = await api.request('GET', '$_base/connectors');
    final items = raw['data'];
    return [
      for (final item in items is List ? items : const <Object?>[])
        CommerceChannel.fromJson(JsonReader.of(item, 'channel')),
    ];
  }

  @override
  Future<String> testChannel(String id) async =>
      (await _call('POST', '/connectors/${_id(id)}/test')).string('message');

  @override
  Future<String> syncChannel(String id) async =>
      (await _call('POST', '/connectors/${_id(id)}/sync')).string('message');

  @override
  Future<CommerceChannel> setEnabled(String id, bool enabled) async =>
      CommerceChannel.fromJson(await _call('PATCH', '/connectors/${_id(id)}',
          body: {'enabled': enabled}));

  @override
  Future<void> deleteChannel(String id) =>
      api.request('DELETE', '$_base/connectors/${_id(id)}');
}

/// One paged list with its own state, so a failed page keeps the others.
class OpsList<T> {
  OpsList(this.fetch);
  final Future<CommercePage<T>> Function(int offset) fetch;
  CommerceLoad state = CommerceLoad.idle;
  final List<T> items = [];
  int total = 0;
  bool loadingMore = false;
  String? error;
  int generation = 0;
  bool get hasMore => items.length < total;
}

class CommerceOps extends ChangeNotifier {
  CommerceOps(this.repository) {
    products = OpsList((o) =>
        repository.products(query: productQuery, offset: o, limit: pageSize));
    listings = OpsList((o) =>
        repository.listings(query: listingQuery, offset: o, limit: pageSize));
    returns = OpsList((o) =>
        repository.returns(days: returnDays, offset: o, limit: pageSize));
    alerts = OpsList((o) =>
        repository.alerts(status: alertStatus, offset: o, limit: pageSize));
  }

  final CommerceOpsRepository repository;
  static const pageSize = 20;
  static const returnWindows = [7, 30, 90];

  CommerceRights rights = const CommerceRights();
  late final OpsList<CommerceProduct> products;
  late final OpsList<CommerceListing> listings;
  late final OpsList<CommerceReturn> returns;
  late final OpsList<CommerceAlert> alerts;
  String? productQuery, listingQuery;
  String alertStatus = 'open';
  int returnDays = 30;

  CommerceLoad channelsState = CommerceLoad.idle;
  final List<CommerceChannel> channels = [];
  String? channelsError;

  CommerceLoad _stateOf(Object e) => e is ApiException && e.isPermissionDenied
      ? CommerceLoad.denied
      : CommerceLoad.error;
  String _message(Object e) => e is ApiException ? e.readable : '$e';

  Future<void> loadRights() async {
    try {
      rights = await repository.rights();
    } catch (_) {
      rights = const CommerceRights();
    }
    notifyListeners();
  }

  Future<void> first<T>(OpsList<T> list) async {
    final generation = ++list.generation;
    list.state = CommerceLoad.loading;
    list.error = null;
    notifyListeners();
    try {
      final page = await list.fetch(0);
      if (generation != list.generation) return;
      list.items
        ..clear()
        ..addAll(page.items);
      list.total = page.total;
      list.state = CommerceLoad.ready;
    } catch (e) {
      if (generation != list.generation) return;
      list.state = _stateOf(e);
      list.error = _message(e);
    }
    notifyListeners();
  }

  Future<void> more<T>(OpsList<T> list) async {
    if (list.loadingMore || !list.hasMore) return;
    final generation = list.generation;
    list.loadingMore = true;
    notifyListeners();
    try {
      final page = await list.fetch(list.items.length);
      if (generation == list.generation) {
        list.items.addAll(page.items);
        list.total = page.total;
      }
    } catch (e) {
      list.error = _message(e);
    } finally {
      list.loadingMore = false;
      notifyListeners();
    }
  }

  Future<void> searchProducts(String? text) {
    productQuery = (text ?? '').trim().isEmpty ? null : text!.trim();
    return first(products);
  }

  Future<void> searchListings(String? text) {
    listingQuery = (text ?? '').trim().isEmpty ? null : text!.trim();
    return first(listings);
  }

  Future<void> setReturnDays(int days) {
    returnDays = days;
    return first(returns);
  }

  Future<void> setAlertStatus(String status) {
    alertStatus = status;
    return first(alerts);
  }

  Future<CommerceProduct> saveProduct(
      String? id, Map<String, Object?> fields) async {
    final saved = id == null
        ? await repository.createProduct(fields)
        : await repository.updateProduct(id, fields);
    await first(products);
    return saved;
  }

  Future<void> deleteProduct(String id) async {
    await repository.deleteProduct(id);
    await first(products);
  }

  Future<ChangeProposal> propose(String listingId,
          {double? price, int? quantity}) =>
      price != null
          ? repository.proposePrice(listingId, price)
          : repository.proposeStock(listingId, quantity!);

  Future<void> acknowledge(String id) async {
    await repository.acknowledge(id);
    await first(alerts);
  }

  Future<void> loadChannels() async {
    channelsState = CommerceLoad.loading;
    channelsError = null;
    notifyListeners();
    try {
      final found = await repository.channels();
      channels
        ..clear()
        ..addAll(found);
      channelsState = CommerceLoad.ready;
    } catch (e) {
      channelsState = _stateOf(e);
      channelsError = _message(e);
    }
    notifyListeners();
  }

  Future<String> testChannel(String id) async {
    final message = await repository.testChannel(id);
    await loadChannels();
    return message;
  }

  Future<String> syncChannel(String id) async {
    final message = await repository.syncChannel(id);
    await loadChannels();
    return message;
  }

  Future<void> setEnabled(String id, bool enabled) async {
    await repository.setEnabled(id, enabled);
    await loadChannels();
  }

  Future<void> deleteChannel(String id) async {
    await repository.deleteChannel(id);
    await loadChannels();
  }

  void reset() {
    for (final list in [products, listings, returns, alerts]) {
      list.items.clear();
      list.total = 0;
      list.state = CommerceLoad.idle;
      list.generation++;
    }
    channels.clear();
    channelsState = CommerceLoad.idle;
    rights = const CommerceRights();
    notifyListeners();
  }
}
