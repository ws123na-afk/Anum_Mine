import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'commerce_controller.dart' show CommerceLoad;
import 'commerce_models.dart' show formatMoney;
import 'commerce_ops_data.dart';
import 'commerce_strings.dart';

/// The working sections of Commerce on the phone: catalogue, listings,
/// returns, alerts and channels (docs/commerce.md). Controls follow
/// `commerce:operate` and `commerce:manage`; the server checks them again and
/// its sentence is shown when it refuses.

enum CommerceSection { catalogue, listings, returns, alerts, channels }

Future<bool> _confirm(
  BuildContext context,
  CommerceStrings s, {
  required String title,
  required String message,
  required String action,
}) async =>
    await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(s.t('ops.cancel')),
          ),
          FilledButton(
            key: const Key('ops-confirm'),
            onPressed: () => Navigator.pop(context, true),
            child: Text(action),
          ),
        ],
      ),
    ) ??
    false;

void _say(BuildContext context, String text) {
  final messenger = ScaffoldMessenger.of(context);
  messenger.clearSnackBars();
  messenger.showSnackBar(SnackBar(content: Text(text)));
}

/// A paged list with every state, so each section only says how a row looks.
class _OpsListView<T> extends StatelessWidget {
  const _OpsListView({
    required this.list,
    required this.ops,
    required this.row,
    required this.emptyTitle,
    required this.emptyText,
    this.filtered = false,
  });
  final OpsList<T> list;
  final CommerceOps ops;
  final Widget Function(T item) row;
  final String emptyTitle, emptyText;
  final bool filtered;

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    switch (list.state) {
      case CommerceLoad.idle:
      case CommerceLoad.loading:
        if (list.items.isEmpty) {
          return AnumFeedback(
              kind: AnumFeedbackKind.loading, message: s.t('loading'));
        }
      case CommerceLoad.denied:
        return AnumFeedback(
            kind: AnumFeedbackKind.permission, message: s.t('denied'));
      case CommerceLoad.error:
        return AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: s.t('ops.failed', {'message': list.error ?? ''}),
          onRetry: () => ops.first(list),
        );
      case CommerceLoad.ready:
        break;
    }
    if (list.items.isEmpty) {
      return AnumEmpty(
        title: filtered ? s.t('ops.noMatch') : emptyTitle,
        message: filtered ? s.t('ops.noMatchText') : emptyText,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final item in list.items) AnumSurface(child: row(item)),
        if (list.error != null)
          Text(list.error!, style: TextStyle(color: context.palette.stop)),
        if (list.hasMore)
          OutlinedButton(
            key: const Key('ops-more'),
            onPressed: list.loadingMore ? null : () => ops.more(list),
            child: Text(
                s.t('more', {'shown': list.items.length, 'total': list.total})),
          ),
      ],
    );
  }
}

class CommerceOpsView extends StatefulWidget {
  const CommerceOpsView({
    required this.ops,
    required this.section,
    this.onOpenApprovals,
    super.key,
  });
  final CommerceOps ops;
  final CommerceSection section;
  final VoidCallback? onOpenApprovals;

  @override
  State<CommerceOpsView> createState() => _CommerceOpsViewState();
}

class _CommerceOpsViewState extends State<CommerceOpsView> {
  final search = TextEditingController();

  CommerceOps get ops => widget.ops;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(CommerceOpsView old) {
    super.didUpdateWidget(old);
    if (old.section != widget.section) {
      search.clear();
      _load();
    }
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  void _load() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      switch (widget.section) {
        case CommerceSection.catalogue:
          ops.first(ops.products);
        case CommerceSection.listings:
          ops.first(ops.listings);
        case CommerceSection.returns:
          ops.first(ops.returns);
        case CommerceSection.alerts:
          ops.first(ops.alerts);
        case CommerceSection.channels:
          ops.loadChannels();
      }
    });
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: ops,
        builder: (context, _) => switch (widget.section) {
          CommerceSection.catalogue => _catalogue(context),
          CommerceSection.listings => _listings(context),
          CommerceSection.returns => _returns(context),
          CommerceSection.alerts => _alerts(context),
          CommerceSection.channels => _channels(context),
        },
      );

  Widget _searchBox(CommerceStrings s, String key, String label,
          Future<void> Function(String?) run) =>
      TextField(
        key: Key(key),
        controller: search,
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          labelText: label,
          prefixIcon: const Icon(Icons.search),
        ),
        onSubmitted: run,
      );

  // -- catalogue -------------------------------------------------------------

  Widget _catalogue(BuildContext context) {
    final s = CommerceStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _searchBox(s, 'ops-product-search', s.t('ops.searchProducts'),
            ops.searchProducts),
        if (ops.rights.operate)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton.icon(
              key: const Key('ops-product-new'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                    builder: (_) => ProductFormScreen(ops: ops)),
              ),
              icon: const Icon(Icons.add),
              label: Text(s.t('ops.newProduct')),
            ),
          )
        else
          Text(s.t('ops.operatorsOnly'),
              key: const Key('ops-readonly'),
              style: TextStyle(color: context.palette.muted)),
        _OpsListView<CommerceProduct>(
          list: ops.products,
          ops: ops,
          filtered: ops.productQuery != null,
          emptyTitle: s.t('ops.noProducts'),
          emptyText: s.t('ops.noProductsText'),
          row: (p) => AnumRow(
            key: Key('ops-product-${p.id}'),
            title: s.arabic && p.titleAr != null ? p.titleAr! : p.title,
            subtitle: [
              p.sku,
              if (p.tyreSize != null) p.tyreSize!,
              if (p.brand != null) p.brand!
            ].join(' · '),
            detail: p.totalStock == null
                ? s.t('ops.listingsCount', {'n': p.listings.length})
                : '${s.t('ops.listingsCount', {
                        'n': p.listings.length
                      })} · ${s.t('ops.stock', {'n': p.totalStock!})}',
            icon: Icons.inventory_2_outlined,
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => ProductScreen(
                    ops: ops,
                    product: p,
                    onOpenApprovals: widget.onOpenApprovals),
              ),
            ),
          ),
        ),
      ],
    );
  }

  // -- listings --------------------------------------------------------------

  Widget _listings(BuildContext context) {
    final s = CommerceStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _searchBox(s, 'ops-listing-search', s.t('ops.searchListings'),
            ops.searchListings),
        Text(s.t('ops.changesApproved'),
            style: TextStyle(color: context.palette.muted)),
        _OpsListView<CommerceListing>(
          list: ops.listings,
          ops: ops,
          filtered: ops.listingQuery != null,
          emptyTitle: s.t('ops.noListings'),
          emptyText: s.t('ops.noListingsText'),
          row: (l) => ListingCard(
              ops: ops, listing: l, onOpenApprovals: widget.onOpenApprovals),
        ),
      ],
    );
  }

  // -- returns ---------------------------------------------------------------

  Widget _returns(BuildContext context) {
    final s = CommerceStrings.of(context);
    final local = MaterialLocalizations.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(spacing: AnumSpacing.xs, children: [
          for (final days in CommerceOps.returnWindows)
            ChoiceChip(
              key: Key('ops-returns-$days'),
              label: Text(s.t('ops.lastDays', {'n': days})),
              selected: ops.returnDays == days,
              onSelected: (_) => ops.setReturnDays(days),
            ),
        ]),
        _OpsListView<CommerceReturn>(
          list: ops.returns,
          ops: ops,
          emptyTitle: s.t('ops.noReturns'),
          emptyText: s.t('ops.noReturnsText'),
          row: (r) => AnumRow(
            title: r.orderId,
            subtitle: [
              if (r.sku != null) '${r.quantity} × ${r.sku}',
              if (r.reason != null && r.reason!.isNotEmpty) r.reason!,
            ].join(' · '),
            detail:
                '${r.status.replaceAll('_', ' ')} · ${local.formatMediumDate(r.returnedAt.toLocal())}',
            icon: Icons.assignment_return_outlined,
            trailing: r.amount == null
                ? null
                : Text(formatMoney(r.amount!, r.currency ?? 'SAR')),
          ),
        ),
      ],
    );
  }

  // -- alerts ----------------------------------------------------------------

  Widget _alerts(BuildContext context) {
    final s = CommerceStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(spacing: AnumSpacing.xs, children: [
          for (final status in const ['open', 'acknowledged', 'resolved'])
            ChoiceChip(
              key: Key('ops-alerts-$status'),
              label: Text(s.t('ops.alert.$status')),
              selected: ops.alertStatus == status,
              onSelected: (_) => ops.setAlertStatus(status),
            ),
        ]),
        _OpsListView<CommerceAlert>(
          list: ops.alerts,
          ops: ops,
          emptyTitle: s.t('ops.noAlerts'),
          emptyText: s.t('ops.noAlertsText'),
          row: (a) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumRow(
                title: a.title,
                subtitle:
                    a.detail.isEmpty ? s.t('ops.kind.${a.kind}') : a.detail,
                icon: a.severity == 'critical'
                    ? Icons.error_outline
                    : Icons.warning_amber_rounded,
                tone: a.severity == 'critical'
                    ? AnumTone.stop
                    : a.severity == 'warning'
                        ? AnumTone.warn
                        : null,
                trailing: AnumPill(label: s.t('ops.severity.${a.severity}')),
              ),
              if (a.status == 'open' && ops.rights.operate)
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: TextButton(
                    key: Key('ops-ack-${a.id}'),
                    onPressed: () async {
                      try {
                        await ops.acknowledge(a.id);
                      } on ApiException catch (e) {
                        if (context.mounted) _say(context, e.readable);
                      }
                    },
                    child: Text(s.t('ops.acknowledge')),
                  ),
                ),
            ],
          ),
        ),
        if (ops.alertStatus == 'open' && !ops.rights.operate)
          Text(s.t('ops.operatorsOnly'),
              style: TextStyle(color: context.palette.muted)),
      ],
    );
  }

  // -- channels --------------------------------------------------------------

  Widget _channels(BuildContext context) {
    final s = CommerceStrings.of(context);
    switch (ops.channelsState) {
      case CommerceLoad.idle:
      case CommerceLoad.loading:
        if (ops.channels.isEmpty) {
          return AnumFeedback(
              kind: AnumFeedbackKind.loading, message: s.t('loading'));
        }
      case CommerceLoad.denied:
        return AnumFeedback(
            kind: AnumFeedbackKind.permission, message: s.t('denied'));
      case CommerceLoad.error:
        return AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: s.t('ops.failed', {'message': ops.channelsError ?? ''}),
          onRetry: ops.loadChannels,
        );
      case CommerceLoad.ready:
        break;
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(s.t('ops.connectOnWeb'),
            style: TextStyle(color: context.palette.muted)),
        if (ops.channels.isEmpty)
          AnumEmpty(
              title: s.t('noChannels'),
              message: s.t('noChannelsText'),
              icon: Icons.storefront_outlined),
        for (final c in ops.channels)
          AnumSurface(child: ChannelCard(ops: ops, channel: c)),
      ],
    );
  }
}

class ListingCard extends StatelessWidget {
  const ListingCard({
    required this.ops,
    required this.listing,
    this.onOpenApprovals,
    super.key,
  });
  final CommerceOps ops;
  final CommerceListing listing;
  final VoidCallback? onOpenApprovals;

  Future<void> _change(BuildContext context, {required bool price}) async {
    final s = CommerceStrings.of(context);
    final value = await showDialog<num>(
      context: context,
      builder: (context) => _NumberDialog(
        title: price
            ? s.t('ops.changePriceTitle', {'sku': listing.sku})
            : s.t('ops.changeStockTitle', {'sku': listing.sku}),
        label: price ? s.t('ops.newPrice') : s.t('ops.newStock'),
        integer: !price,
        initial: price ? listing.price : listing.quantity?.toDouble(),
      ),
    );
    if (value == null || !context.mounted) return;
    try {
      await ops.propose(
        listing.id,
        price: price ? value.toDouble() : null,
        quantity: price ? null : value.toInt(),
      );
      if (!context.mounted) return;
      final open = onOpenApprovals;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        key: const Key('ops-proposed'),
        content: Text(s.t('ops.proposed')),
        action: open == null
            ? null
            : SnackBarAction(label: s.t('ops.openApprovals'), onPressed: open),
      ));
    } on ApiException catch (e) {
      if (context.mounted) _say(context, e.readable);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(
          title: listing.sku,
          subtitle:
              '${listing.marketplace} · ${s.t('ops.listing.${listing.status}')}',
          detail: [
            listing.price == null
                ? s.t('ops.noPrice')
                : formatMoney(listing.price!, listing.currency ?? 'SAR'),
            if (listing.quantity != null)
              s.t('ops.stock', {'n': listing.quantity!}),
            if (listing.buyBoxWinner == false) s.t('ops.buyBoxLost'),
          ].join(' · '),
          icon: Icons.sell_outlined,
        ),
        if (ops.rights.operate)
          Wrap(spacing: AnumSpacing.xs, children: [
            TextButton(
              key: Key('ops-price-${listing.id}'),
              onPressed: () => _change(context, price: true),
              child: Text(s.t('ops.changePrice')),
            ),
            TextButton(
              key: Key('ops-stock-${listing.id}'),
              onPressed: () => _change(context, price: false),
              child: Text(s.t('ops.changeStock')),
            ),
          ]),
      ],
    );
  }
}

class _NumberDialog extends StatefulWidget {
  const _NumberDialog({
    required this.title,
    required this.label,
    required this.integer,
    this.initial,
  });
  final String title, label;
  final bool integer;
  final double? initial;

  @override
  State<_NumberDialog> createState() => _NumberDialogState();
}

class _NumberDialogState extends State<_NumberDialog> {
  late final controller = TextEditingController(
    text: widget.initial == null
        ? ''
        : widget.integer
            ? widget.initial!.toInt().toString()
            : widget.initial!.toStringAsFixed(2),
  );
  String? error;

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  void ok() {
    final s = CommerceStrings.of(context);
    final text = controller.text.trim();
    final n = widget.integer ? int.tryParse(text) : double.tryParse(text);
    final bad = n == null || (widget.integer ? n < 0 : n <= 0);
    if (bad) {
      setState(() =>
          error = widget.integer ? s.t('ops.stockBad') : s.t('ops.priceBad'));
      return;
    }
    Navigator.pop(context, n);
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    return AlertDialog(
      title: Text(widget.title),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(
          key: const Key('ops-number'),
          controller: controller,
          autofocus: true,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration:
              InputDecoration(labelText: widget.label, errorText: error),
        ),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('ops.needsApproval'),
            style: TextStyle(color: context.palette.muted)),
      ]),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(s.t('ops.cancel')),
        ),
        FilledButton(
          key: const Key('ops-number-ok'),
          onPressed: ok,
          child: Text(s.t('ops.propose')),
        ),
      ],
    );
  }
}

class ChannelCard extends StatefulWidget {
  const ChannelCard({required this.ops, required this.channel, super.key});
  final CommerceOps ops;
  final CommerceChannel channel;

  @override
  State<ChannelCard> createState() => _ChannelCardState();
}

class _ChannelCardState extends State<ChannelCard> {
  bool busy = false;

  Future<void> _run(Future<String?> Function() action) async {
    setState(() => busy = true);
    try {
      final message = await action();
      if (mounted && message != null && message.isNotEmpty) {
        _say(context, message);
      }
    } on ApiException catch (e) {
      if (mounted) _say(context, e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _delete() async {
    final s = CommerceStrings.of(context);
    final ok = await _confirm(
      context,
      s,
      title: s.t('ops.deleteChannelTitle', {'name': widget.channel.name}),
      message: s.t('ops.deleteChannelText'),
      action: s.t('ops.delete'),
    );
    if (!ok) return;
    await _run(() async {
      await widget.ops.deleteChannel(widget.channel.id);
      return null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    final c = widget.channel;
    final rights = widget.ops.rights;
    final local = MaterialLocalizations.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(
          title: c.name,
          subtitle: c.marketplaces.join(', '),
          detail: c.lastSyncAt == null
              ? s.t('ops.neverSynced')
              : s.t('ops.lastSync', {
                  'when':
                      '${local.formatMediumDate(c.lastSyncAt!.toLocal())} ${local.formatTimeOfDay(TimeOfDay.fromDateTime(c.lastSyncAt!.toLocal()))}'
                }),
          icon: Icons.storefront_outlined,
          trailing: AnumPill(
            label: s.t('ops.health.${c.health}'),
            tone: switch (c.health) {
              'ok' => AnumTone.ok,
              'auth_failed' || 'error' => AnumTone.stop,
              'throttled' || 'pending' => AnumTone.warn,
              _ => AnumTone.neutral,
            },
          ),
        ),
        if (c.lastError != null && c.lastError!.isNotEmpty)
          Text(c.lastError!, style: TextStyle(color: context.palette.stop)),
        Wrap(spacing: AnumSpacing.xs, children: [
          if (rights.operate && c.enabled)
            TextButton(
              key: Key('ops-sync-${c.id}'),
              onPressed:
                  busy ? null : () => _run(() => widget.ops.syncChannel(c.id)),
              child: Text(s.t('ops.syncNow')),
            ),
          if (rights.manage) ...[
            TextButton(
              key: Key('ops-test-${c.id}'),
              onPressed:
                  busy ? null : () => _run(() => widget.ops.testChannel(c.id)),
              child: Text(s.t('ops.test')),
            ),
            TextButton(
              key: Key('ops-toggle-${c.id}'),
              onPressed: busy
                  ? null
                  : () => _run(() async {
                        await widget.ops.setEnabled(c.id, !c.enabled);
                        return null;
                      }),
              child: Text(c.enabled ? s.t('ops.turnOff') : s.t('ops.turnOn')),
            ),
            TextButton(
              key: Key('ops-delete-${c.id}'),
              onPressed: busy ? null : _delete,
              child: Text(s.t('ops.delete')),
            ),
          ],
        ]),
        if (!rights.operate && !rights.manage)
          Text(s.t('ops.managersOnly'),
              style: TextStyle(color: context.palette.muted)),
      ],
    );
  }
}

class ProductScreen extends StatefulWidget {
  const ProductScreen({
    required this.ops,
    required this.product,
    this.onOpenApprovals,
    super.key,
  });
  final CommerceOps ops;
  final CommerceProduct product;
  final VoidCallback? onOpenApprovals;

  @override
  State<ProductScreen> createState() => _ProductScreenState();
}

class _ProductScreenState extends State<ProductScreen> {
  late CommerceProduct product = widget.product;
  String? error;
  bool busy = false;

  Future<void> _edit() async {
    final next = await Navigator.of(context).push<CommerceProduct>(
      MaterialPageRoute(
          builder: (_) => ProductFormScreen(ops: widget.ops, product: product)),
    );
    if (next != null && mounted) setState(() => product = next);
  }

  Future<void> _delete() async {
    final s = CommerceStrings.of(context);
    final ok = await _confirm(
      context,
      s,
      title: s.t('ops.deleteProductTitle', {'sku': product.sku}),
      message: s.t('ops.deleteProductText'),
      action: s.t('ops.delete'),
    );
    if (!ok) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.ops.deleteProduct(product.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          error = e.readable;
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(product.sku)),
      body: AnumPage(children: [
        if (error != null)
          Semantics(
            liveRegion: true,
            child: Text(error!,
                key: const Key('ops-product-error'),
                style: TextStyle(color: context.palette.stop)),
          ),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AnumHeader(
                eyebrow: product.sku,
                title: s.arabic && product.titleAr != null
                    ? product.titleAr!
                    : product.title,
                subtitle: [
                  if (product.brand != null) product.brand!,
                  if (product.tyreSize != null) product.tyreSize!,
                ].join(' · '),
              ),
              if (product.costPrice != null)
                Text(
                    s.t('ops.cost', {
                      'cost': formatMoney(
                          product.costPrice!, product.currency ?? 'SAR')
                    }),
                    style: muted),
              if (product.lowStockThreshold != null)
                Text(s.t('ops.threshold', {'n': product.lowStockThreshold!}),
                    style: muted),
              Text(s.t('ops.catalogueNote'), style: muted),
              if (widget.ops.rights.operate) ...[
                const SizedBox(height: AnumSpacing.sm),
                Wrap(spacing: AnumSpacing.xs, children: [
                  OutlinedButton.icon(
                    key: const Key('ops-product-edit'),
                    onPressed: busy ? null : _edit,
                    icon: const Icon(Icons.edit_outlined),
                    label: Text(s.t('ops.edit')),
                  ),
                  OutlinedButton.icon(
                    key: const Key('ops-product-delete'),
                    onPressed: busy ? null : _delete,
                    icon: const Icon(Icons.delete_outline),
                    label: Text(s.t('ops.delete')),
                  ),
                ]),
              ],
            ],
          ),
        ),
        AnumHeader(title: s.t('ops.channelsHeader')),
        if (product.listings.isEmpty) Text(s.t('ops.notListed'), style: muted),
        for (final l in product.listings)
          AnumSurface(
            child: ListingCard(
                ops: widget.ops,
                listing: l,
                onOpenApprovals: widget.onOpenApprovals),
          ),
      ]),
    );
  }
}

class ProductFormScreen extends StatefulWidget {
  const ProductFormScreen({required this.ops, this.product, super.key});
  final CommerceOps ops;

  /// Null adds a product; otherwise edits it (the SKU never changes).
  final CommerceProduct? product;

  @override
  State<ProductFormScreen> createState() => _ProductFormScreenState();
}

class _ProductFormScreenState extends State<ProductFormScreen> {
  final form = GlobalKey<FormState>();
  late final p = widget.product;
  late final sku = TextEditingController(text: p?.sku);
  late final title = TextEditingController(text: p?.title);
  late final titleAr = TextEditingController(text: p?.titleAr);
  late final brand = TextEditingController(text: p?.brand);
  late final cost = TextEditingController(
      text: p?.costPrice == null ? '' : '${p!.costPrice}');
  late final low = TextEditingController(
      text: p?.lowStockThreshold == null ? '' : '${p!.lowStockThreshold}');
  bool busy = false;
  String? error;

  @override
  void dispose() {
    for (final c in [sku, title, titleAr, brand, cost, low]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    String? text(TextEditingController c) =>
        c.text.trim().isEmpty ? null : c.text.trim();
    final fields = <String, Object?>{
      'title': title.text.trim(),
      'title_ar': text(titleAr),
      'brand': text(brand),
      'cost_price': double.tryParse(cost.text.trim()),
      'low_stock_threshold': int.tryParse(low.text.trim()),
      if (p == null) 'sku': sku.text.trim(),
    };
    try {
      final saved = await widget.ops.saveProduct(p?.id, fields);
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = CommerceStrings.of(context);
    return Scaffold(
      appBar: AppBar(
          title:
              Text(p == null ? s.t('ops.newProduct') : s.t('ops.editProduct'))),
      body: Form(
        key: form,
        child: AnumPage(children: [
          if (error != null)
            Semantics(
              liveRegion: true,
              child: Text(error!,
                  key: const Key('ops-form-error'),
                  style: TextStyle(color: context.palette.stop)),
            ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  key: const Key('ops-sku'),
                  controller: sku,
                  enabled: p == null,
                  maxLength: 120,
                  decoration: InputDecoration(labelText: s.t('ops.sku')),
                  validator: (v) {
                    final t = (v ?? '').trim();
                    if (t.isEmpty) return s.t('ops.skuNeeded');
                    return RegExp(r'^[A-Za-z0-9][A-Za-z0-9._/ -]*$').hasMatch(t)
                        ? null
                        : s.t('ops.skuBad');
                  },
                ),
                TextFormField(
                  key: const Key('ops-title'),
                  controller: title,
                  maxLength: 500,
                  decoration: InputDecoration(
                      labelText: s.t('ops.title'),
                      helperText: s.t('ops.titleHint'),
                      helperMaxLines: 2),
                ),
                TextFormField(
                  key: const Key('ops-title-ar'),
                  controller: titleAr,
                  maxLength: 500,
                  decoration: InputDecoration(labelText: s.t('ops.titleAr')),
                ),
                TextFormField(
                  key: const Key('ops-brand'),
                  controller: brand,
                  maxLength: 120,
                  decoration: InputDecoration(labelText: s.t('ops.brand')),
                ),
                TextFormField(
                  key: const Key('ops-cost'),
                  controller: cost,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(labelText: s.t('ops.costPrice')),
                  validator: (v) {
                    final t = (v ?? '').trim();
                    if (t.isEmpty) return null;
                    final n = double.tryParse(t);
                    return n == null || n < 0 ? s.t('ops.priceBad') : null;
                  },
                ),
                TextFormField(
                  key: const Key('ops-low'),
                  controller: low,
                  keyboardType: TextInputType.number,
                  decoration:
                      InputDecoration(labelText: s.t('ops.lowThreshold')),
                  validator: (v) {
                    final t = (v ?? '').trim();
                    if (t.isEmpty) return null;
                    final n = int.tryParse(t);
                    return n == null || n < 0 ? s.t('ops.stockBad') : null;
                  },
                ),
              ],
            ),
          ),
          FilledButton(
            key: const Key('ops-save'),
            onPressed: busy ? null : save,
            child: busy
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2))
                : Text(s.t('ops.save')),
          ),
        ]),
      ),
    );
  }
}
