import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the Commerce screen (docs/commerce.md). The
/// terms follow the web catalog (apps/web/src/lib/messages/commerce.*.ts).
class CommerceStrings {
  const CommerceStrings(this.arabic);
  final bool arabic;

  static CommerceStrings of(BuildContext context) =>
      CommerceStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? _ar : _en)[key] ?? _en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  static const _en = <String, String>{
    'title': 'Commerce',
    'subtitle': 'Sales, orders and stock across Amazon, noon and your store',
    'entry': 'Sales and orders',
    'entrySubtitle': 'Amazon, noon and your store',
    'period.today': 'Today',
    'period.yesterday': 'Yesterday',
    'period.last_7_days': '7 days',
    'period.last_30_days': '30 days',
    'revenue': 'Revenue',
    'orders': 'Orders',
    'units': '{n} units',
    'toShip': 'To ship',
    'late': '{n} late',
    'noneLate': 'None late',
    'returns': 'Returns',
    'alerts': '{n} open alerts',
    'byChannel': 'By marketplace',
    'top': 'Top products',
    'sold': '{n} sold',
    'lowStock': 'Low stock',
    'left': '{n} left',
    'orderList': 'Orders',
    'status.open': 'Not shipped',
    'status.all': 'All',
    'status.shipped': 'Shipped',
    'status.delivered': 'Delivered',
    'status.pending': 'Pending',
    'status.unshipped': 'To ship',
    'status.partially_shipped': 'Partly shipped',
    'status.cancelled': 'Cancelled',
    'status.returned': 'Returned',
    'lateBadge': 'Late',
    'more': 'Load more ({shown} of {total})',
    'loading': 'Loading sales...',
    'loadingOrders': 'Loading orders...',
    'retry': 'Try again',
    'summaryFailed': 'Sales could not be loaded: {message}',
    'ordersFailed': 'Orders could not be loaded: {message}',
    'denied':
        'Your role in this workspace does not include commerce. Ask a workspace owner to add you as a member or viewer.',
    'noChannels': 'No sales channel connected',
    'noChannelsText':
        'A workspace owner connects Amazon, noon or your store under Commerce › Connections on the web.',
    'noOrders': 'No orders here',
    'noOrdersText':
        'Orders appear after a connected channel syncs. Try another status.',
    'noSales': 'No sales in this period yet.',
    'items': 'Items',
    'shipBy': 'Ship by {when}',
    'placed': 'Placed {when}',
    'changesOnWeb':
        'Price and stock changes are proposed on the web and approved in Approvals.',
  };

  static const _ar = <String, String>{
    'title': 'التجارة',
    'subtitle': 'المبيعات والطلبات والمخزون على أمازون ونون ومتجرك',
    'entry': 'المبيعات والطلبات',
    'entrySubtitle': 'أمازون ونون ومتجرك',
    'period.today': 'اليوم',
    'period.yesterday': 'أمس',
    'period.last_7_days': '7 أيام',
    'period.last_30_days': '30 يومًا',
    'revenue': 'الإيرادات',
    'orders': 'الطلبات',
    'units': '{n} قطعة',
    'toShip': 'للشحن',
    'late': '{n} متأخر',
    'noneLate': 'لا شيء متأخر',
    'returns': 'المرتجعات',
    'alerts': '{n} تنبيهات مفتوحة',
    'byChannel': 'حسب السوق',
    'top': 'الأكثر مبيعًا',
    'sold': 'بيع {n}',
    'lowStock': 'مخزون منخفض',
    'left': 'تبقّى {n}',
    'orderList': 'الطلبات',
    'status.open': 'لم يُشحن',
    'status.all': 'الكل',
    'status.shipped': 'تم الشحن',
    'status.delivered': 'تم التسليم',
    'status.pending': 'قيد الانتظار',
    'status.unshipped': 'للشحن',
    'status.partially_shipped': 'شُحن جزئيًا',
    'status.cancelled': 'ملغى',
    'status.returned': 'مرتجع',
    'lateBadge': 'متأخر',
    'more': 'حمّل المزيد ({shown} من {total})',
    'loading': 'جارٍ تحميل المبيعات...',
    'loadingOrders': 'جارٍ تحميل الطلبات...',
    'retry': 'حاول مرة أخرى',
    'summaryFailed': 'تعذّر تحميل المبيعات: {message}',
    'ordersFailed': 'تعذّر تحميل الطلبات: {message}',
    'denied':
        'دورك في مساحة العمل هذه لا يشمل التجارة. اطلب من مالك المساحة إضافتك عضوًا أو مشاهدًا.',
    'noChannels': 'لا توجد قناة بيع مربوطة',
    'noChannelsText':
        'يربط مالك المساحة أمازون أو نون أو متجرك من التجارة › الاتصالات على الويب.',
    'noOrders': 'لا طلبات هنا',
    'noOrdersText': 'تظهر الطلبات بعد مزامنة قناة مربوطة. جرّب حالة أخرى.',
    'noSales': 'لا مبيعات في هذه الفترة بعد.',
    'items': 'القطع',
    'shipBy': 'الشحن قبل {when}',
    'placed': 'طُلب في {when}',
    'changesOnWeb':
        'تُقترح تغييرات السعر والمخزون على الويب وتُعتمد في الموافقات.',
  };

  static Iterable<String> get keys => _en.keys;
  static bool get catalogsMatch =>
      _en.keys.toSet().containsAll(_ar.keys) &&
      _ar.keys.toSet().containsAll(_en.keys);
}
