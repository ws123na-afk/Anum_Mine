import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'commerce_controller.dart';
import 'commerce_models.dart';
import 'commerce_strings.dart';

/// Commerce on the phone (docs/commerce.md): a sales summary for a period and
/// the order list. Read-only: changes are proposed on the web and approved in
/// Approvals, where every client shows them.
class CommerceScreen extends StatefulWidget {
  const CommerceScreen({required this.controller, super.key});
  final CommerceController controller;

  @override
  State<CommerceScreen> createState() => _CommerceScreenState();
}

class _CommerceScreenState extends State<CommerceScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      widget.controller.lang = CommerceStrings.of(context).arabic ? 'ar' : 'en';
      widget.controller.load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return Scaffold(
          appBar: AppBar(title: Text(s.t('title'))),
          body: AnumPage(
            onRefresh: c.load,
            children: [
              AnumHeader(
                  eyebrow: s.t('subtitle'),
                  title: c.summaryText ?? s.t('title')),
              _Periods(controller: c, strings: s),
              ..._summary(context, c, s),
              ..._orders(context, c, s),
              Text(s.t('changesOnWeb'),
                  style: TextStyle(color: context.palette.muted)),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _summary(
      BuildContext context, CommerceController c, CommerceStrings s) {
    switch (c.summaryState) {
      case CommerceLoad.idle:
      case CommerceLoad.loading:
        if (c.dashboard == null) {
          return [
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: s.t('loading'))
          ];
        }
      case CommerceLoad.denied:
        return [
          AnumFeedback(
              kind: AnumFeedbackKind.permission, message: s.t('denied'))
        ];
      case CommerceLoad.error:
        return [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('summaryFailed', {'message': c.summaryError ?? ''}),
              onRetry: c.loadSummary)
        ];
      case CommerceLoad.ready:
        break;
    }
    final d = c.dashboard!;
    if (d.connectors == 0) {
      return [
        AnumEmpty(
            title: s.t('noChannels'),
            message: s.t('noChannelsText'),
            icon: Icons.storefront_outlined)
      ];
    }
    final main = d.currencies.isEmpty ? null : d.currencies.first;
    return [
      AnumMetricGrid(children: [
        AnumMetric(
            label: s.t('revenue'),
            value:
                main == null ? '-' : formatMoney(main.revenue, main.currency),
            icon: Icons.trending_up,
            caption: d.currencies.length > 1
                ? d.currencies
                    .skip(1)
                    .map((m) => formatMoney(m.revenue, m.currency))
                    .join(' · ')
                : null),
        AnumMetric(
            label: s.t('orders'),
            value: '${d.orders}',
            icon: Icons.shopping_cart_outlined,
            caption: s.t('units', {'n': d.units})),
        AnumMetric(
            label: s.t('toShip'),
            value: '${d.pendingTotal}',
            icon: Icons.local_shipping_outlined,
            caption: d.lateTotal > 0
                ? s.t('late', {'n': d.lateTotal})
                : s.t('noneLate'),
            accent: d.lateTotal > 0 ? context.palette.stop : null),
        AnumMetric(
            label: s.t('returns'),
            value: '${d.returns}',
            icon: Icons.assignment_return_outlined,
            caption: s.t('alerts', {'n': d.openAlerts})),
      ]),
      if (d.byChannel.isEmpty)
        AnumSurface(child: Text(s.t('noSales')))
      else
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('byChannel')),
            for (final row in d.byChannel)
              AnumRow(
                  title: row.marketplace,
                  icon: Icons.storefront_outlined,
                  detail: formatMoney(row.revenue, row.currency),
                  subtitle: '${row.orders} · ${s.t('orders')}'),
          ]),
        ),
      if (d.topProducts.isNotEmpty)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('top')),
            for (final p in d.topProducts.take(5))
              AnumRow(
                  title: (s.arabic && p.titleAr != null ? p.titleAr! : p.title),
                  subtitle:
                      [p.sku, if (p.tyreSize != null) p.tyreSize!].join(' · '),
                  detail: s.t('sold', {'n': p.units})),
          ]),
        ),
      if (d.lowStock.isNotEmpty)
        AnumSurface(
          accent: context.palette.warn,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('lowStock')),
            for (final row in d.lowStock.take(8))
              AnumRow(
                  title: row.sku,
                  subtitle: row.marketplace,
                  tone: row.quantity <= 0 ? AnumTone.stop : AnumTone.warn,
                  icon: Icons.inventory_2_outlined,
                  detail: s.t('left', {'n': row.quantity})),
          ]),
        ),
    ];
  }

  List<Widget> _orders(
      BuildContext context, CommerceController c, CommerceStrings s) {
    final filters = Wrap(spacing: 8, runSpacing: 8, children: [
      for (final status in const [
        'open',
        null,
        'shipped',
        'delivered',
        'cancelled',
        'returned'
      ])
        ChoiceChip(
          label: Text(s.t('status.${status ?? 'all'}')),
          selected: c.statusFilter == status,
          onSelected: (_) => c.setStatus(status),
        ),
    ]);
    final header = AnumHeader(title: s.t('orderList'));
    if (c.ordersState == CommerceLoad.denied) return [];
    if (c.ordersState == CommerceLoad.loading && c.orders.isEmpty) {
      return [
        header,
        filters,
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingOrders'))
      ];
    }
    if (c.ordersState == CommerceLoad.error) {
      return [
        header,
        filters,
        AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('ordersFailed', {'message': c.ordersError ?? ''}),
            onRetry: c.loadOrders),
      ];
    }
    if (c.orders.isEmpty) {
      return [
        header,
        filters,
        AnumEmpty(title: s.t('noOrders'), message: s.t('noOrdersText')),
      ];
    }
    return [
      header,
      filters,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final order in c.orders)
            AnumRow(
              title: order.externalId,
              subtitle:
                  '${order.marketplace} · ${s.t('status.${order.status}')}',
              detail: formatMoney(order.total, order.currency),
              icon: order.late
                  ? Icons.warning_amber_rounded
                  : Icons.receipt_long_outlined,
              tone: order.late ? AnumTone.stop : null,
              trailing: order.late
                  ? AnumPill(label: s.t('lateBadge'), tone: AnumTone.stop)
                  : null,
              onTap: () => _showOrder(context, order, s),
            ),
        ]),
      ),
      if (c.hasMoreOrders)
        OutlinedButton(
          onPressed: c.loadingMore ? null : c.loadMoreOrders,
          child: Text(
              s.t('more', {'shown': c.orders.length, 'total': c.ordersTotal})),
        ),
    ];
  }

  void _showOrder(
      BuildContext context, CommerceOrder order, CommerceStrings s) {
    final local = MaterialLocalizations.of(context);
    String when(DateTime value) =>
        '${local.formatMediumDate(value.toLocal())} ${local.formatTimeOfDay(TimeOfDay.fromDateTime(value.toLocal()))}';
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              AnumHeader(
                  title: order.externalId,
                  subtitle:
                      '${order.marketplace} · ${s.t('status.${order.status}')}'),
              const SizedBox(height: AnumSpacing.xs),
              Text(s.t('placed', {'when': when(order.purchasedAt)})),
              if (order.shipBy != null)
                Text(s.t('shipBy', {'when': when(order.shipBy!)})),
              const SizedBox(height: AnumSpacing.sm),
              Text(s.t('items'), style: Theme.of(context).textTheme.titleSmall),
              for (final line in order.lines)
                AnumRow(
                    title: '${line.quantity} × ${line.sku}',
                    subtitle: line.title,
                    detail: line.total == null
                        ? null
                        : formatMoney(line.total!, order.currency)),
              const SizedBox(height: AnumSpacing.sm),
              Text(formatMoney(order.total, order.currency),
                  style: Theme.of(context).textTheme.titleMedium),
            ],
          ),
        ),
      ),
    );
  }
}

class _Periods extends StatelessWidget {
  const _Periods({required this.controller, required this.strings});
  final CommerceController controller;
  final CommerceStrings strings;

  @override
  Widget build(BuildContext context) =>
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final period in CommerceController.periods)
          ChoiceChip(
            label: Text(strings.t('period.$period')),
            selected: controller.period == period,
            onSelected: (_) => controller.setPeriod(period),
          ),
      ]);
}
