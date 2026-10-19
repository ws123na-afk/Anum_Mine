import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'business_controller.dart';
import 'business_models.dart';

/// The Business catalog on the phone (docs/business.md#what-it-does): products
/// and service packages with their priced versions. Reading is for owners and
/// members; every change is `business:manage` (owners) and the server decides.

const productKinds = ['product', 'service_package'];
const productMaturities = ['idea', 'prototype', 'pilot', 'live', 'retired'];
const priceTypes = ['fixed', 'from', 'range', 'hourly', 'quote'];
const currencies = ['SAR', 'AED', 'USD'];

class CatalogOffer {
  const CatalogOffer({
    required this.id,
    required this.version,
    required this.status,
    required this.currency,
    required this.priceType,
    required this.priceMin,
    required this.priceMax,
    required this.deliverables,
    required this.limitations,
    required this.laborHours,
    required this.hourlyCost,
    required this.externalCosts,
    required this.contingencyPct,
    required this.notes,
    required this.estimatedCost,
    required this.marginAtPrice,
    required this.marginNegative,
  });
  final String id, status, currency, priceType, notes;
  final int version;
  final double? priceMin, priceMax, marginAtPrice;
  final List<String> deliverables, limitations;
  final double laborHours,
      hourlyCost,
      externalCosts,
      contingencyPct,
      estimatedCost;
  final bool marginNegative;

  bool get isDraft => status == 'draft';
  bool get isActive => status == 'active';

  factory CatalogOffer.fromJson(JsonReader r) {
    final cost = r.object('cost');
    return CatalogOffer(
      id: r.string('id'),
      version: r.integer('version'),
      status: r.string('status'),
      currency: r.string('currency'),
      priceType: r.string('price_type'),
      priceMin: r.optNumber('price_min'),
      priceMax: r.optNumber('price_max'),
      deliverables: r.optStrings('deliverables') ?? const [],
      limitations: r.optStrings('limitations') ?? const [],
      laborHours: cost.number('labor_hours'),
      hourlyCost: cost.number('hourly_cost'),
      externalCosts: cost.number('external_costs'),
      contingencyPct: cost.number('contingency_pct'),
      notes: r.optString('notes') ?? '',
      estimatedCost: r.number('estimated_cost'),
      marginAtPrice: r.optNumber('margin_at_price'),
      marginNegative: r.optBool('margin_negative') ?? false,
    );
  }
}

class CatalogProduct {
  const CatalogProduct({
    required this.id,
    required this.kind,
    required this.name,
    required this.nameAr,
    required this.summary,
    required this.maturity,
    required this.integrations,
    required this.versions,
    required this.offers,
    this.activeOffer,
  });
  final String id, kind, name, nameAr, summary, maturity;
  final List<String> integrations;
  final int versions;
  final CatalogOffer? activeOffer;

  /// Every version, newest first; empty in a list row (the detail has them).
  final List<CatalogOffer> offers;

  CatalogOffer? get draft {
    for (final offer in offers) {
      if (offer.isDraft) return offer;
    }
    return null;
  }

  factory CatalogProduct.fromJson(JsonReader r) => CatalogProduct(
        id: r.string('id'),
        kind: r.string('kind'),
        name: r.string('name'),
        nameAr: r.optString('name_ar') ?? '',
        summary: r.optString('summary') ?? '',
        maturity: r.string('maturity'),
        integrations: r.optStrings('integrations') ?? const [],
        versions: r.optInt('versions') ?? 0,
        activeOffer: r.optObject('active_offer') == null
            ? null
            : CatalogOffer.fromJson(r.object('active_offer')),
        offers: r.optObjects('offers', CatalogOffer.fromJson),
      );
}

abstract interface class CatalogRepository {
  Future<BusinessPage<CatalogProduct>> products({
    String? query,
    String? kind,
    String? maturity,
    required int offset,
    int limit,
  });
  Future<CatalogProduct> product(String id);
  Future<CatalogProduct> createProduct(Map<String, Object?> fields);
  Future<CatalogProduct> updateProduct(String id, Map<String, Object?> fields);
  Future<void> deleteProduct(String id);
  Future<CatalogProduct> addVersion(String id, Map<String, Object?> fields);
  Future<CatalogProduct> updateVersion(
    String offerId,
    Map<String, Object?> fields,
  );
  Future<CatalogProduct> activateVersion(String offerId);
  Future<CatalogProduct> retireVersion(String offerId);
}

class ApiCatalogRepository implements CatalogRepository {
  ApiCatalogRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';

  String _id(String value) => Uri.encodeComponent(value);

  Future<CatalogProduct> _product(
    String method,
    String path, {
    Map<String, Object?>? body,
  }) async =>
      CatalogProduct.fromJson(
        JsonReader.of(await api.request(method, '$_base$path', body: body)),
      );

  @override
  Future<BusinessPage<CatalogProduct>> products({
    String? query,
    String? kind,
    String? maturity,
    required int offset,
    int limit = 20,
  }) async {
    final params = <String, String>{
      if (query != null && query.isNotEmpty) 'q': query,
      if (kind != null) 'kind': kind,
      if (maturity != null) 'maturity': maturity,
      'offset': '$offset',
      'limit': '$limit',
    };
    return BusinessPage.fromJson(
      JsonReader.of(
        await api.request(
          'GET',
          '$_base/products?${Uri(queryParameters: params).query}',
        ),
      ),
      CatalogProduct.fromJson,
    );
  }

  @override
  Future<CatalogProduct> product(String id) =>
      _product('GET', '/products/${_id(id)}');

  @override
  Future<CatalogProduct> createProduct(Map<String, Object?> fields) =>
      _product('POST', '/products', body: fields);

  @override
  Future<CatalogProduct> updateProduct(
    String id,
    Map<String, Object?> fields,
  ) =>
      _product('PATCH', '/products/${_id(id)}', body: fields);

  @override
  Future<void> deleteProduct(String id) =>
      api.request('DELETE', '$_base/products/${_id(id)}');

  @override
  Future<CatalogProduct> addVersion(String id, Map<String, Object?> fields) =>
      _product('POST', '/products/${_id(id)}/versions', body: fields);

  @override
  Future<CatalogProduct> updateVersion(
    String offerId,
    Map<String, Object?> fields,
  ) =>
      _product('PATCH', '/offers/${_id(offerId)}', body: fields);

  @override
  Future<CatalogProduct> activateVersion(String offerId) =>
      _product('POST', '/offers/${_id(offerId)}/activate');

  @override
  Future<CatalogProduct> retireVersion(String offerId) =>
      _product('POST', '/offers/${_id(offerId)}/retire');
}

/// The catalog list (search, kind and maturity filters, paging) and the
/// changes. A change returns the fresh product; the list reloads after it.
class CatalogController extends ChangeNotifier {
  CatalogController(this.repository);
  final CatalogRepository repository;
  static const pageSize = 20;

  BusinessLoad state = BusinessLoad.idle;
  final List<CatalogProduct> items = [];
  int total = 0;
  bool loadingMore = false;
  String? error;
  String? query, kind, maturity;
  bool get hasMore => items.length < total;

  Future<void> load() async {
    state = BusinessLoad.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.products(
        query: query,
        kind: kind,
        maturity: maturity,
        offset: 0,
        limit: pageSize,
      );
      items
        ..clear()
        ..addAll(page.items);
      total = page.total;
      state = BusinessLoad.ready;
    } catch (e) {
      state = e is ApiException && e.isPermissionDenied
          ? BusinessLoad.denied
          : BusinessLoad.error;
      error = e is ApiException ? e.readable : '$e';
    }
    notifyListeners();
  }

  Future<void> filter({String? text, String? kind, String? maturity}) {
    query = (text ?? '').trim().isEmpty ? null : text!.trim();
    this.kind = kind;
    this.maturity = maturity;
    return load();
  }

  Future<void> more() async {
    if (loadingMore || !hasMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.products(
        query: query,
        kind: kind,
        maturity: maturity,
        offset: items.length,
        limit: pageSize,
      );
      items.addAll(page.items);
      total = page.total;
    } catch (e) {
      error = e is ApiException ? e.readable : '$e';
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  Future<CatalogProduct> _then(Future<CatalogProduct> change) async {
    final product = await change;
    await load();
    return product;
  }

  Future<CatalogProduct> create(Map<String, Object?> fields) =>
      _then(repository.createProduct(fields));
  Future<CatalogProduct> update(String id, Map<String, Object?> fields) =>
      _then(repository.updateProduct(id, fields));
  Future<void> delete(String id) async {
    await repository.deleteProduct(id);
    await load();
  }

  Future<CatalogProduct> addVersion(String id, Map<String, Object?> fields) =>
      _then(repository.addVersion(id, fields));
  Future<CatalogProduct> updateVersion(
    String offerId,
    Map<String, Object?> fields,
  ) =>
      _then(repository.updateVersion(offerId, fields));
  Future<CatalogProduct> activate(String offerId) =>
      _then(repository.activateVersion(offerId));
  Future<CatalogProduct> retire(String offerId) =>
      _then(repository.retireVersion(offerId));

  void reset() {
    items.clear();
    total = 0;
    state = BusinessLoad.idle;
    notifyListeners();
  }
}
