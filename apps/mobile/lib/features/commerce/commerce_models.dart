import '../../data/json_read.dart';

/// Commerce (docs/commerce.md): what the summary dashboard and the order list
/// read from `/api/v1/commerce`.

class MoneyTotal {
  const MoneyTotal(
      {required this.currency,
      required this.revenue,
      required this.orders,
      required this.units});
  final String currency;
  final double revenue;
  final int orders, units;

  factory MoneyTotal.fromJson(JsonReader r) => MoneyTotal(
        currency: r.string('currency'),
        revenue: r.number('revenue'),
        orders: r.integer('orders'),
        units: r.integer('units'),
      );
}

class ChannelSales {
  const ChannelSales(
      {required this.channel,
      required this.marketplace,
      required this.currency,
      required this.revenue,
      required this.orders});
  final String channel, marketplace, currency;
  final double revenue;
  final int orders;

  factory ChannelSales.fromJson(JsonReader r) => ChannelSales(
        channel: r.string('channel'),
        marketplace: r.string('marketplace'),
        currency: r.string('currency'),
        revenue: r.number('revenue'),
        orders: r.integer('orders'),
      );
}

class TopProduct {
  const TopProduct(
      {required this.sku,
      required this.title,
      this.titleAr,
      this.tyreSize,
      required this.units});
  final String sku, title;
  final String? titleAr, tyreSize;
  final int units;

  factory TopProduct.fromJson(JsonReader r) => TopProduct(
        sku: r.string('sku'),
        title: r.string('title'),
        titleAr: r.optString('title_ar'),
        tyreSize: r.optString('tyre_size'),
        units: r.integer('units'),
      );
}

class LowStock {
  const LowStock(
      {required this.sku, required this.marketplace, required this.quantity});
  final String sku, marketplace;
  final int quantity;

  factory LowStock.fromJson(JsonReader r) => LowStock(
        sku: r.string('sku'),
        marketplace: r.string('marketplace'),
        quantity: r.integer('quantity'),
      );
}

class CommerceDashboard {
  const CommerceDashboard({
    required this.currencies,
    required this.byChannel,
    required this.topProducts,
    required this.lowStock,
    required this.pendingTotal,
    required this.lateTotal,
    required this.returns,
    required this.openAlerts,
    required this.connectors,
  });
  final List<MoneyTotal> currencies;
  final List<ChannelSales> byChannel;
  final List<TopProduct> topProducts;
  final List<LowStock> lowStock;
  final int pendingTotal, lateTotal, returns, openAlerts, connectors;

  int get orders => currencies.fold(0, (sum, c) => sum + c.orders);
  int get units => currencies.fold(0, (sum, c) => sum + c.units);

  factory CommerceDashboard.fromJson(JsonReader r) => CommerceDashboard(
        currencies: r.objects('currencies', MoneyTotal.fromJson),
        byChannel: r.objects('by_channel', ChannelSales.fromJson),
        topProducts: r.objects('top_products', TopProduct.fromJson),
        lowStock: r.objects('low_stock', LowStock.fromJson),
        pendingTotal: r.integer('pending_total'),
        lateTotal: r.integer('late_total'),
        returns: r.object('returns').integer('count'),
        openAlerts: r.integer('open_alerts'),
        connectors: r.integer('connectors'),
      );
}

class OrderLine {
  const OrderLine(
      {required this.sku,
      required this.title,
      required this.quantity,
      this.total});
  final String sku, title;
  final int quantity;
  final double? total;

  factory OrderLine.fromJson(JsonReader r) => OrderLine(
        sku: r.string('sku'),
        title: r.optString('title') ?? '',
        quantity: r.integer('quantity'),
        total: r.optNumber('total'),
      );
}

class CommerceOrder {
  const CommerceOrder({
    required this.id,
    required this.externalId,
    required this.channel,
    required this.marketplace,
    required this.status,
    required this.purchasedAt,
    this.shipBy,
    required this.total,
    this.currency,
    required this.itemCount,
    required this.late,
    this.city,
    this.lines = const [],
  });
  final String id, externalId, channel, marketplace, status;
  final DateTime purchasedAt;
  final DateTime? shipBy;
  final double total;
  final String? currency, city;
  final int itemCount;
  final bool late;
  final List<OrderLine> lines;

  factory CommerceOrder.fromJson(JsonReader r) => CommerceOrder(
        id: r.string('id'),
        externalId: r.string('external_id'),
        channel: r.string('channel'),
        marketplace: r.string('marketplace'),
        status: r.string('status'),
        purchasedAt: r.date('purchased_at'),
        shipBy: r.optDate('ship_by'),
        total: r.number('total_amount'),
        currency: r.optString('currency'),
        itemCount: r.integer('item_count'),
        late: r.optBool('late') ?? false,
        city: r.optString('ship_city'),
        lines: r.optObjects('lines', OrderLine.fromJson),
      );
}

class OrderPage {
  const OrderPage(
      {required this.items,
      required this.total,
      required this.offset,
      required this.limit});
  final List<CommerceOrder> items;
  final int total, offset, limit;

  factory OrderPage.fromJson(JsonReader r) => OrderPage(
        items: r.objects('items', CommerceOrder.fromJson),
        total: r.integer('total'),
        offset: r.integer('offset'),
        limit: r.integer('limit'),
      );
}

/// The spoken/written summary ("Amazon sales today: 2 orders, ...").
class SalesSummary {
  const SalesSummary(this.text);
  final String text;
  factory SalesSummary.fromJson(JsonReader r) => SalesSummary(r.string('text'));
}

/// Money in the interface's digits: "SAR 1,234.50".
String formatMoney(double amount, String? currency) {
  final fixed = amount.abs().toStringAsFixed(2);
  final parts = fixed.split('.');
  final whole =
      parts[0].replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  final sign = amount < 0 ? '-' : '';
  return '${currency ?? ''} $sign$whole.${parts[1]}'.trim();
}

/// Date range for a period name, as the API's `from`/`to` (local days).
({String from, String to}) periodRange(String period, DateTime now) {
  String day(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  final today = DateTime(now.year, now.month, now.day);
  final back = switch (period) {
    'last_7_days' => 6,
    'last_30_days' => 29,
    _ => 0,
  };
  if (period == 'yesterday') {
    final y = today.subtract(const Duration(days: 1));
    return (from: day(y), to: day(y));
  }
  return (from: day(today.subtract(Duration(days: back))), to: day(today));
}
