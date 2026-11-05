import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/payment_picker.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'purchases_controller.dart';
import 'purchases_data.dart';
import 'purchases_strings.dart';

const returnReasons = [
  'damaged', 'defective', 'wrong_item', 'not_as_described', 'missing_parts', //
  'late', 'changed_mind', 'other',
];
const shipmentStatuses = [
  'pending', 'in_transit', 'out_for_delivery', 'delivered', 'failed', //
  'returned_to_sender',
];

AnumTone purchaseTone(String status) => switch (status) {
      'delivered' || 'refunded' || 'returned' || 'approved' => AnumTone.ok,
      'cancelled' || 'rejected' => AnumTone.neutral,
      'disputed' || 'partially_delivered' => AnumTone.stop,
      'ordered' || 'confirmed' || 'requested' => AnumTone.info,
      _ => AnumTone.warn,
    };

String purchaseDue(BuildContext context, PurchaseDue due) {
  final s = PurchasesStrings.of(context);
  final what = s.label('due', due.kind);
  if (due.daysLeft == 0) return s.t('dueToday', {'what': what});
  return due.daysLeft < 0
      ? s.t('dueLate', {'what': what, 'days': -due.daysLeft})
      : s.t('dueIn', {'what': what, 'days': due.daysLeft});
}

String moneyLink(BuildContext context, PurchaseMoneyLink? link) {
  final s = PurchasesStrings.of(context);
  if (link == null) return s.t('noPayment');
  if (link.status == 'needs_review') return s.t('paymentReview');
  final amount = formatMoneyText(link.amount, link.currency,
      locale: Localizations.localeOf(context));
  return s.t(link.recordedAsCash ? 'paymentCash' : 'paymentLinked',
      {'amount': amount});
}

/// Purchases on the phone (docs/purchases.md#mobile): the list with status
/// groups, a purchase with its timeline, shipments and tracking, returns,
/// and linking its payment and refunds from Finance. From 840 px the list
/// and the selected purchase sit side by side.
class PurchasesScreen extends StatefulWidget {
  const PurchasesScreen({required this.controller, super.key});
  final PurchasesController controller;

  @override
  State<PurchasesScreen> createState() => _PurchasesScreenState();
}

class _PurchasesScreenState extends State<PurchasesScreen> {
  String? _selected;
  PurchasesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    if (c.purchases.phase == RecordsPhase.loading) c.load();
  }

  Future<void> _add() async {
    final s = PurchasesStrings.of(context);
    final key = newIdempotencyKey('pur');
    PurchaseDetail? created;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('add'),
        submitLabel: s.t('add'),
        fields: [
          RecordsInput.text('merchant', s.t('merchant'),
              required: true, maxLength: 120),
          RecordsInput.text('order_ref', s.t('orderRef'), maxLength: 80),
          RecordsInput.date('ordered_on', s.t('orderedOn'),
              required: true, initial: isoDay(DateTime.now())),
          RecordsInput.number('total', s.t('total')),
          RecordsInput.text('currency', s.t('currency'), maxLength: 3),
          RecordsInput.date('expected_delivery_on', s.t('expected')),
          RecordsInput.date('return_window_until', s.t('returnWindow')),
          RecordsInput.choice('ownership', s.t('ownership'),
              initial: 'personal',
              options: {
                'personal': s.t('personal'),
                'business': s.t('business')
              }),
        ],
        onSubmit: (v) async {
          created = await c.create({
            for (final entry in v.entries)
              if (entry.value != null)
                entry.key: entry.key == 'currency'
                    ? (entry.value! as String).toUpperCase()
                    : entry.value,
          }, idempotencyKey: key);
        },
      ),
    );
    if (!mounted || !saved || created == null) return;
    recordsToast(context, s.t('saved'));
    _open(created!.purchase);
  }

  void _open(Purchase purchase) {
    if (recordsWide(context)) {
      setState(() => _selected = purchase.id);
      return;
    }
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => Scaffold(
        appBar: AppBar(title: Text(purchase.merchant)),
        body: PurchaseDetailHost(
            repository: c.repository,
            purchaseId: purchase.id,
            onChanged: c.changed),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    return ListenableBuilder(
      listenable: c.purchases,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(s.t('title'))),
        floatingActionButton: c.purchases.phase == RecordsPhase.denied
            ? null
            : FloatingActionButton.extended(
                key: const Key('purchases-add'),
                onPressed: _add,
                icon: const Icon(Icons.add),
                label: Text(s.t('add')),
              ),
        body: LayoutBuilder(builder: (context, box) {
          final list = _PurchaseList(
              controller: c, selected: _selected, onOpen: _open, onAdd: _add);
          if (box.maxWidth < AnumBreakpoints.expanded) return list;
          final selected = _selected;
          return Row(children: [
            SizedBox(width: 420, child: list),
            const VerticalDivider(width: 1),
            Expanded(
              child: selected == null
                  ? Center(
                      child: Padding(
                        padding: const EdgeInsets.all(AnumSpacing.lg),
                        child:
                            Text(s.t('subtitle'), textAlign: TextAlign.center),
                      ),
                    )
                  : PurchaseDetailHost(
                      key: ValueKey(selected),
                      repository: c.repository,
                      purchaseId: selected,
                      onChanged: c.changed),
            ),
          ]);
        }),
      ),
    );
  }
}

class _PurchaseList extends StatelessWidget {
  const _PurchaseList({
    required this.controller,
    required this.selected,
    required this.onOpen,
    required this.onAdd,
  });
  final PurchasesController controller;
  final String? selected;
  final ValueChanged<Purchase> onOpen;
  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    final k = KitStrings.of(context);
    final list = controller.purchases;
    final children = <Widget>[
      AnumHeader(title: s.t('title'), subtitle: s.t('subtitle')),
    ];
    if (list.phase == RecordsPhase.denied ||
        (list.phase != RecordsPhase.ready && !list.narrowed)) {
      children.add(RecordsStateView(
          phase: list.phase,
          failure: list.failure,
          deniedHint: s.t('denied'),
          onRetry: controller.load));
      return AnumPage(onRefresh: controller.load, children: children);
    }
    children.addAll([
      RecordsSearchField(
          initial: list.query,
          hint: s.t('search'),
          onSearch: (q) => list.load(query: q)),
      RecordsFilterChips(
        options: {
          for (final f in ['', 'open', 'awaiting', 'returns', 'closed'])
            f: s.t('filter.${f.isEmpty ? 'all' : f}')
        },
        selected: list.filter,
        onSelected: (f) => list.load(filter: f),
      ),
    ]);
    if (list.phase != RecordsPhase.ready) {
      children.add(RecordsStateView(
          phase: list.phase, failure: list.failure, onRetry: list.load));
    } else if (list.items.isEmpty) {
      children.add(list.narrowed
          ? AnumEmpty(
              title: k.t('noMatch'),
              message: k.t('noMatchText'),
              icon: Icons.search_off,
              action: k.t('clearFilters'),
              onAction: () => list.load(query: '', filter: ''))
          : AnumEmpty(
              key: const Key('purchases-empty'),
              title: s.t('empty'),
              message: s.t('emptyText'),
              icon: Icons.shopping_bag_outlined,
              action: s.t('add'),
              onAction: onAdd));
    } else {
      final locale = Localizations.localeOf(context);
      children.add(AnumSurface(
        child: Column(children: [
          for (final p in list.items)
            Semantics(
              selected: p.id == selected,
              button: true,
              child: AnumRow(
                key: Key('purchase-${p.id}'),
                icon: Icons.shopping_bag_outlined,
                tone: purchaseTone(p.status),
                title: p.merchant,
                subtitle: [
                  s.label('st', p.status),
                  recordsDate(context, p.orderedOn),
                  if (p.total != null)
                    formatMoneyText(p.total!, p.currency, locale: locale),
                  if (p.orderRef != null) p.orderRef!,
                ].join(' · '),
                detail: [
                  if (p.needsReview) s.t('reviewFlag'),
                  for (final due in p.due.take(2)) purchaseDue(context, due),
                ].join(' · ').nullIfEmpty,
                trailing: const Icon(Icons.chevron_right),
                onTap: () => onOpen(p),
              ),
            ),
        ]),
      ));
      children.add(RecordsMore(list: list));
    }
    return AnumPage(onRefresh: controller.load, children: children);
  }
}

extension on String {
  String? get nullIfEmpty => isEmpty ? null : this;
}

/// Owns the detail controller of one purchase while it is shown.
class PurchaseDetailHost extends StatefulWidget {
  const PurchaseDetailHost({
    required this.repository,
    required this.purchaseId,
    this.onChanged,
    super.key,
  });
  final PurchasesRepository repository;
  final String purchaseId;
  final ValueChanged<Purchase>? onChanged;

  @override
  State<PurchaseDetailHost> createState() => _PurchaseDetailHostState();
}

class _PurchaseDetailHostState extends State<PurchaseDetailHost> {
  late final PurchaseDetailController _c = PurchaseDetailController(
      widget.repository, widget.purchaseId,
      onChanged: widget.onChanged)
    ..load();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  Future<void> _move(String to) async {
    final s = PurchasesStrings.of(context);
    try {
      await _c.move(to);
      if (mounted) recordsToast(context, s.t('moved'));
    } on Object catch (error) {
      if (!mounted) return;
      recordsToast(
          context,
          isStale(error)
              ? KitStrings.of(context).t('conflict')
              : RecordsFailure.from(error).message);
    }
  }

  Future<void> _shipment() async {
    final s = PurchasesStrings.of(context);
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('addShipment'),
        submitLabel: s.t('addShipment'),
        fields: [
          RecordsInput.text('carrier', s.t('carrier'), maxLength: 60),
          RecordsInput.text('tracking_number', s.t('tracking'), maxLength: 60),
          RecordsInput.choice('status', s.t('status'),
              initial: 'in_transit',
              options: {
                for (final st in shipmentStatuses) st: s.label('ship', st)
              }),
          RecordsInput.date('shipped_on', s.t('shippedOn')),
          RecordsInput.date('expected_on', s.t('expectedOn')),
          RecordsInput.text('note', s.t('note'), maxLength: 300),
        ],
        onSubmit: (v) => _c.addShipment({
          for (final e in v.entries)
            if (e.value != null) e.key: e.value
        }),
      ),
    );
    if (saved && mounted) recordsToast(context, s.t('saved'));
  }

  Future<void> _return() async {
    final s = PurchasesStrings.of(context);
    final key = newIdempotencyKey('ret');
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: s.t('startReturn'),
        submitLabel: s.t('startReturn'),
        fields: [
          RecordsInput.choice('reason', s.t('reason'),
              initial: 'damaged',
              options: {for (final r in returnReasons) r: s.label('why', r)}),
          RecordsInput.multiline('reason_note', s.t('reasonNote'),
              maxLength: 300),
          RecordsInput.number('amount', s.t('returnAmount')),
          RecordsInput.date('deadline_on', s.t('deadline')),
        ],
        onSubmit: (v) => _c.startReturn({
          for (final e in v.entries)
            if (e.value != null) e.key: e.value
        }, idempotencyKey: key),
      ),
    );
    if (saved && mounted) recordsToast(context, s.t('returnStarted'));
  }

  Future<void> _link({PurchaseReturn? refundOf}) async {
    final s = PurchasesStrings.of(context);
    final purchase = _c.detail!.purchase;
    final refund = refundOf != null;
    final linked = await showRecordsSheet<bool>(
      context,
      (_) => PaymentPickerSheet(
        text: PaymentPickerText(
          title: s.t(refund ? 'refundTitle' : 'linkTitle'),
          help: s.t(refund ? 'refundHelp' : 'linkHelp'),
          search: s.t('linkSearch'),
          empty: s.t('linkEmpty'),
          remaining: s.t('linkRemaining'),
          already: s.t('linkAlready'),
        ),
        load: (query) async => [
          for (final c in await _c.repository.candidates(
              refund ? 'refund' : 'payment',
              query: query,
              around: refund ? refundOf.requestedOn : purchase.orderedOn))
            PaymentChoice(
              id: c.canonicalId,
              postedOn: c.postedOn,
              merchant: c.merchant,
              amount: c.amount,
              currency: c.currency,
              remaining: c.remaining,
              alreadyLinked: c.linked,
            ),
        ],
        link: (choice, key) => refund
            ? _c.repository.linkRefund(refundOf.id,
                canonicalId: choice.id, idempotencyKey: key)
            : _c.repository.linkPayment(purchase.id,
                canonicalId: choice.id, idempotencyKey: key),
      ),
    );
    if (linked ?? false) {
      await _c.refresh();
      if (mounted) {
        recordsToast(
            context, s.t(refund ? 'refundLinked' : 'paymentLinkedToast'));
      }
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([_c, _c.timeline]),
        builder: (context, _) {
          final s = PurchasesStrings.of(context);
          final detail = _c.detail;
          if (_c.phase != RecordsPhase.ready || detail == null) {
            return AnumPage(children: [
              RecordsStateView(
                  phase: _c.phase,
                  failure: _c.failure,
                  deniedHint: s.t('denied'),
                  onRetry: _c.load),
            ]);
          }
          final p = detail.purchase;
          final locale = Localizations.localeOf(context);
          final editable = p.canEdit && p.archivedAt == null;
          return AnumPage(
            onRefresh: _c.load,
            children: [
              RecordsSection(
                title: p.merchant,
                children: [
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        AnumPill(
                            key: const Key('purchase-status'),
                            label: s.label('st', p.status),
                            tone: purchaseTone(p.status)),
                        AnumPill(
                            label: s.t(p.ownership == 'business'
                                ? 'business'
                                : 'personal')),
                        if (p.needsReview)
                          AnumPill(
                              label: s.t('reviewFlag'), tone: AnumTone.warn),
                        for (final due in p.due)
                          AnumPill(
                              label: purchaseDue(context, due),
                              tone: due.daysLeft < 0
                                  ? AnumTone.stop
                                  : AnumTone.warn,
                              icon: Icons.event_outlined),
                      ]),
                  const SizedBox(height: AnumSpacing.xs),
                  RecordsField(s.t('orderRef'), p.orderRef),
                  RecordsField(s.t('orderedOn'), recordsDate(context, p.orderedOn)),
                  RecordsField(
                      s.t('total'),
                      p.total == null
                          ? null
                          : formatMoneyText(p.total!, p.currency,
                              locale: locale)),
                  RecordsField(s.t('expected'),
                      recordsDate(context, p.expectedDeliveryOn)),
                  RecordsField(s.t('returnWindow'),
                      recordsDate(context, p.returnWindowUntil)),
                  RecordsField(s.t('payment'), moneyLink(context, p.payment)),
                  for (final refunded in p.refunded)
                    RecordsField(
                        s.t('refundedTotal'),
                        formatMoneyText(refunded.amount, refunded.currency,
                            locale: locale)),
                  if (!p.canEdit)
                    Padding(
                      padding: const EdgeInsets.only(top: AnumSpacing.xs),
                      child: Text(s.t('readOnly'),
                          style: TextStyle(color: context.palette.muted)),
                    ),
                  if (editable && p.canPay && p.payment == null) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton.icon(
                        key: const Key('purchase-link-payment'),
                        onPressed: _link,
                        icon: const Icon(Icons.link),
                        label: Text(s.t('linkPayment')),
                      ),
                    ),
                  ],
                ],
              ),
              if (editable && p.allowedMoves.isNotEmpty)
                RecordsSection(
                  title: s.t('moveTo'),
                  children: [
                    Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xs,
                      children: [
                        for (final to in p.allowedMoves)
                          ActionChip(
                            key: Key('move-$to'),
                            label: Text(s.label('st', to)),
                            onPressed: () => _move(to),
                          ),
                      ],
                    ),
                  ],
                ),
              if (detail.items.isNotEmpty)
                RecordsSection(title: s.t('items'), children: [
                  for (final item in detail.items)
                    Padding(
                      padding: const EdgeInsets.symmetric(
                          vertical: AnumSpacing.xxs),
                      child: Text(s.t('itemLine', {
                        'quantity': item.quantity,
                        'description': item.description
                      })),
                    ),
                ]),
              RecordsSection(
                title: s.t('shipments'),
                trailing: editable
                    ? IconButton(
                        key: const Key('purchase-add-shipment'),
                        tooltip: s.t('addShipment'),
                        onPressed: _shipment,
                        icon: const Icon(Icons.add))
                    : null,
                children: [
                  if (detail.shipments.isEmpty)
                    Text(s.t('shipmentsEmpty'),
                        style: TextStyle(color: context.palette.muted))
                  else
                    for (final sh in detail.shipments)
                      AnumRow(
                        key: Key('shipment-${sh.id}'),
                        icon: Icons.local_shipping_outlined,
                        tone: sh.status == 'failed'
                            ? AnumTone.stop
                            : sh.status == 'delivered'
                                ? AnumTone.ok
                                : AnumTone.info,
                        title: [
                          s.label('ship', sh.status),
                          if (sh.carrier != null) sh.carrier!,
                        ].join(' · '),
                        subtitle: sh.trackingNumber == null
                            ? null
                            : '${s.t('tracking')}: ${sh.trackingNumber}',
                        detail: [
                          if (sh.shippedOn != null)
                            '${s.t('shippedOn')} ${recordsDate(context, sh.shippedOn)}',
                          if (sh.expectedOn != null)
                            '${s.t('expectedOn')} ${recordsDate(context, sh.expectedOn)}',
                        ].join(' · ').nullIfEmpty,
                      ),
                ],
              ),
              RecordsSection(
                title: s.t('returns'),
                trailing: editable
                    ? IconButton(
                        key: const Key('purchase-start-return'),
                        tooltip: s.t('startReturn'),
                        onPressed: _return,
                        icon: const Icon(Icons.assignment_return_outlined))
                    : null,
                children: [
                  if (detail.returns.isEmpty)
                    Text(s.t('returnsEmpty'),
                        style: TextStyle(color: context.palette.muted))
                  else
                    for (final r in detail.returns)
                      AnumRow(
                        key: Key('return-${r.id}'),
                        icon: Icons.assignment_return_outlined,
                        tone: purchaseTone(r.status),
                        title:
                            '${s.label('why', r.reason)} · ${s.label('st', r.status)}',
                        subtitle: [
                          recordsDate(context, r.requestedOn),
                          if (r.amount != null)
                            formatMoneyText(r.amount!, r.currency,
                                locale: locale),
                          s.t('refunded', {
                            'amount': formatMoneyText(r.refunded, r.currency,
                                locale: locale)
                          }),
                        ].join(' · '),
                        detail: r.reasonNote,
                        trailing: r.canRefund
                            ? IconButton(
                                key: Key('return-refund-${r.id}'),
                                tooltip: s.t('linkRefund'),
                                onPressed: () => _link(refundOf: r),
                                icon: const Icon(Icons.currency_exchange))
                            : null,
                      ),
                ],
              ),
              _Timeline(list: _c.timeline),
            ],
          );
        },
      );
}

class _Timeline extends StatelessWidget {
  const _Timeline({required this.list});
  final RecordsList<PurchaseEvent> list;

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    return RecordsSection(
      title: s.t('timeline'),
      children: [
        if (list.phase != RecordsPhase.ready)
          RecordsStateView(
              phase: list.phase, failure: list.failure, onRetry: list.load)
        else if (list.items.isEmpty)
          Text(s.t('timelineEmpty'),
              style: TextStyle(color: context.palette.muted))
        else ...[
          for (final e in list.items)
            AnumRow(
              key: Key('event-${e.id}'),
              icon: Icons.history,
              title: e.kind == 'status' && e.toStatus != null
                  ? (e.fromStatus == null
                      ? s.label('st', e.toStatus!)
                      : s.t('evMove', {
                          'from': s.label('st', e.fromStatus!),
                          'to': s.label('st', e.toStatus!)
                        }))
                  : s.label('ev', e.kind),
              subtitle: e.note,
              detail: recordsDateTime(context, e.occurredAt),
            ),
          RecordsMore(list: list),
        ],
      ],
    );
  }
}
