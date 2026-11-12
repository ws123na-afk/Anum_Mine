import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'purchases_controller.dart';
import 'purchases_data.dart';
import 'purchases_screen.dart' show returnReasons, shipmentStatuses;
import 'purchases_strings.dart';

/// The changes the phone makes to one purchase (docs/purchases.md#mobile):
/// edit, receipt, note, archive, delete, shipments, returns with their
/// status, and payments and refunds recorded as cash or unlinked. Each one
/// is sent with the version on screen; a refusal is shown, never swallowed.

/// Only the fields that changed, with an emptied optional field as null.
Map<String, Object?> changedFields(
    Map<String, Object?> values, Map<String, String?> before) {
  final out = <String, Object?>{};
  for (final entry in values.entries) {
    final now = entry.value is String && (entry.value as String).isEmpty
        ? null
        : entry.value;
    if (now?.toString() != before[entry.key]) out[entry.key] = now;
  }
  return out;
}

String _failure(BuildContext context, Object error) => isStale(error)
    ? KitStrings.of(context).t('conflict')
    : RecordsFailure.from(error).message;

/// Runs [work] and tells what happened: [done] on success, the reason else.
Future<bool> runPurchaseAction(
    BuildContext context, Future<void> Function() work,
    {required String done}) async {
  try {
    await work();
    if (context.mounted) recordsToast(context, done);
    return true;
  } on Object catch (error) {
    if (context.mounted) recordsToast(context, _failure(context, error));
    return false;
  }
}

Future<void> editPurchase(
    BuildContext context, PurchaseDetailController c) async {
  final s = PurchasesStrings.of(context);
  final p = c.detail!.purchase;
  final before = <String, String?>{
    'merchant': p.merchant,
    'order_ref': p.orderRef,
    'ordered_on': p.orderedOn,
    'total': p.total,
    'currency': p.currency,
    'expected_delivery_on': p.expectedDeliveryOn,
    'delivered_on': p.deliveredOn,
    'return_window_until': p.returnWindowUntil,
    'warranty_until': p.warrantyUntil,
    'notes': p.notes,
  };
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      title: s.t('edit'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.text('merchant', s.t('merchant'),
            required: true, initial: p.merchant, maxLength: 120),
        RecordsInput.text('order_ref', s.t('orderRef'),
            initial: p.orderRef, maxLength: 80),
        RecordsInput.date('ordered_on', s.t('orderedOn'),
            required: true, initial: p.orderedOn),
        RecordsInput.number('total', s.t('total'), initial: p.total),
        RecordsInput.text('currency', s.t('currency'),
            required: true, initial: p.currency, maxLength: 3),
        RecordsInput.date('expected_delivery_on', s.t('expected'),
            initial: p.expectedDeliveryOn),
        RecordsInput.date('delivered_on', s.t('deliveredOn'),
            initial: p.deliveredOn),
        RecordsInput.date('return_window_until', s.t('returnWindow'),
            initial: p.returnWindowUntil),
        RecordsInput.date('warranty_until', s.t('warranty'),
            initial: p.warrantyUntil),
        RecordsInput.multiline('notes', s.t('notes'),
            initial: p.notes, maxLength: 1000),
      ],
      onSubmit: (v) async {
        final changes = changedFields({
          ...v,
          if (v['currency'] case final String code)
            'currency': code.toUpperCase(),
        }, before);
        if (changes.isEmpty) return;
        await c.update(changes);
      },
    ),
  );
  if (saved && context.mounted) recordsToast(context, s.t('saved'));
}

Future<void> addPurchaseNote(
    BuildContext context, PurchaseDetailController c) async {
  final s = PurchasesStrings.of(context);
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      title: s.t('addNote'),
      submitLabel: s.t('addNote'),
      fields: [
        RecordsInput.multiline('note', s.t('note'),
            required: true, maxLength: 300),
      ],
      onSubmit: (v) => c.addNote(v['note']! as String),
    ),
  );
  if (saved && context.mounted) recordsToast(context, s.t('noteAdded'));
}

/// Archive keeps the history out of the lists; delete is only for a
/// purchase with no money linked (the API says so otherwise).
Future<void> deletePurchase(
    BuildContext context, PurchaseDetailController c) async {
  final s = PurchasesStrings.of(context);
  final sure = await confirmRecords(context,
      title: s.t('deleteTitle'),
      message: s.t('deleteText', {'merchant': c.detail!.purchase.merchant}),
      action: s.t('delete'));
  if (!sure || !context.mounted) return;
  await runPurchaseAction(context, c.delete, done: s.t('deleted'));
}

Future<void> editShipment(BuildContext context, PurchaseDetailController c,
    PurchaseShipment sh) async {
  final s = PurchasesStrings.of(context);
  final action = await showRecordsSheet<String>(
      context,
      (sheet) => _ActionList(title: s.label('ship', sh.status), actions: {
            'edit': (Icons.edit_outlined, s.t('editShipment')),
            'delete': (Icons.delete_outline, s.t('deleteShipment')),
          }));
  if (!context.mounted || action == null) return;
  if (action == 'delete') {
    final sure = await confirmRecords(context,
        title: s.t('deleteShipment'),
        message: s.t('deleteShipmentText'),
        action: s.t('delete'));
    if (sure && context.mounted) {
      await runPurchaseAction(context, () => c.deleteShipment(sh),
          done: s.t('shipmentDeleted'));
    }
    return;
  }
  final before = <String, String?>{
    'carrier': sh.carrier,
    'tracking_number': sh.trackingNumber,
    'status': sh.status,
    'shipped_on': sh.shippedOn,
    'expected_on': sh.expectedOn,
    'delivered_on': sh.deliveredOn,
    'note': sh.note,
  };
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      title: s.t('editShipment'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.text('carrier', s.t('carrier'),
            initial: sh.carrier, maxLength: 60),
        RecordsInput.text('tracking_number', s.t('tracking'),
            initial: sh.trackingNumber, maxLength: 60),
        RecordsInput.choice('status', s.t('status'),
            initial: sh.status,
            options: {
              for (final st in shipmentStatuses) st: s.label('ship', st)
            }),
        RecordsInput.date('shipped_on', s.t('shippedOn'),
            initial: sh.shippedOn),
        RecordsInput.date('expected_on', s.t('expectedOn'),
            initial: sh.expectedOn),
        RecordsInput.date('delivered_on', s.t('deliveredOn'),
            initial: sh.deliveredOn),
        RecordsInput.text('note', s.t('note'),
            initial: sh.note, maxLength: 300),
      ],
      onSubmit: (v) async {
        final changes = changedFields(v, before);
        if (changes.isNotEmpty) await c.updateShipment(sh, changes);
      },
    ),
  );
  if (saved && context.mounted) recordsToast(context, s.t('saved'));
}

/// Everything that can be done to one return, in one sheet: its details,
/// its status, refunds (linked from Finance, recorded as cash, unlinked)
/// and removing it.
Future<void> returnActions(
    BuildContext context, PurchaseDetailController c, PurchaseReturn r,
    {required VoidCallback onLinkRefund}) async {
  final s = PurchasesStrings.of(context);
  final linked = [
    for (final f in r.refunds)
      if (f.linkId != null && f.status != 'void') f
  ];
  final action = await showRecordsSheet<String>(
      context,
      (sheet) => _ActionList(
              title: '${s.label('why', r.reason)} · ${s.label('st', r.status)}',
              actions: {
                if (r.canEdit) 'edit': (Icons.edit_outlined, s.t('editReturn')),
                if (r.canEdit)
                  for (final to in r.allowedMoves)
                    'move:$to': (
                      Icons.arrow_forward,
                      s.t('returnMove', {'status': s.label('st', to)})
                    ),
                if (r.canRefund) ...{
                  'link': (Icons.currency_exchange, s.t('linkRefund')),
                  'cash': (Icons.payments_outlined, s.t('cashRefund')),
                  for (final f in linked)
                    'unlink:${f.linkId}': (
                      Icons.link_off,
                      s.t('unlinkRefund',
                          {'amount': '${f.amount} ${f.currency}'})
                    ),
                },
                if (r.canEdit)
                  'delete': (Icons.delete_outline, s.t('deleteReturn')),
              }));
  if (!context.mounted || action == null) return;
  if (action == 'link') return onLinkRefund();
  if (action == 'cash') return recordCash(context, c, refundOf: r);
  if (action == 'edit') return _editReturn(context, c, r);
  if (action == 'delete') {
    final sure = await confirmRecords(context,
        title: s.t('deleteReturn'),
        message: s.t('deleteReturnText'),
        action: s.t('delete'));
    if (sure && context.mounted) {
      await runPurchaseAction(context, () => c.deleteReturn(r),
          done: s.t('returnDeleted'));
    }
    return;
  }
  if (action.startsWith('unlink:')) {
    final sure = await confirmRecords(context,
        title: s.t('unlinkTitle'),
        message: s.t('unlinkText'),
        action: s.t('unlink'));
    if (sure && context.mounted) {
      await runPurchaseAction(
          context, () => c.unlinkRefund(r, action.substring(7)),
          done: s.t('unlinked'));
    }
    return;
  }
  if (action.startsWith('move:')) {
    final to = action.substring(5);
    String? note;
    if (to == 'disputed') {
      final values = <String, Object?>{};
      final saved = await openRecordsForm(
          context,
          RecordsForm(
            title: s.t('returnMove', {'status': s.label('st', to)}),
            submitLabel: s.label('st', to),
            fields: [
              RecordsInput.multiline('note', s.t('disputeNote'),
                  required: true, maxLength: 500),
            ],
            onSubmit: (v) async => values.addAll(v),
          ));
      if (!saved || !context.mounted) return;
      note = values['note'] as String?;
    }
    await runPurchaseAction(context, () => c.moveReturn(r, to, note: note),
        done: s.t('moved'));
  }
}

Future<void> _editReturn(
    BuildContext context, PurchaseDetailController c, PurchaseReturn r) async {
  final s = PurchasesStrings.of(context);
  final before = <String, String?>{
    'reason': r.reason,
    'reason_note': r.reasonNote,
    'amount': r.amount,
    'deadline_on': r.deadlineOn,
    'pickup_on': r.pickupOn,
    'courier': r.courier,
    'tracking_number': r.trackingNumber,
    'confirmation_ref': r.confirmationRef,
    'refund_ref': r.refundRef,
    'expected_refund_on': r.expectedRefundOn,
  };
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      title: s.t('editReturn'),
      submitLabel: KitStrings.of(context).t('save'),
      fields: [
        RecordsInput.choice('reason', s.t('reason'),
            initial: r.reason,
            options: {for (final w in returnReasons) w: s.label('why', w)}),
        RecordsInput.multiline('reason_note', s.t('reasonNote'),
            initial: r.reasonNote, maxLength: 300),
        RecordsInput.number('amount', s.t('returnAmount'), initial: r.amount),
        RecordsInput.date('deadline_on', s.t('deadline'),
            initial: r.deadlineOn),
        RecordsInput.date('pickup_on', s.t('pickupOn'), initial: r.pickupOn),
        RecordsInput.text('courier', s.t('carrier'),
            initial: r.courier, maxLength: 60),
        RecordsInput.text('tracking_number', s.t('tracking'),
            initial: r.trackingNumber, maxLength: 60),
        RecordsInput.text('confirmation_ref', s.t('confirmationRef'),
            initial: r.confirmationRef, maxLength: 80),
        RecordsInput.text('refund_ref', s.t('refundRef'),
            initial: r.refundRef, maxLength: 80),
        RecordsInput.date('expected_refund_on', s.t('expectedRefund'),
            initial: r.expectedRefundOn),
      ],
      onSubmit: (v) async {
        final changes = changedFields(v, before);
        if (changes.isNotEmpty) await c.updateReturn(r, changes);
      },
    ),
  );
  if (saved && context.mounted) recordsToast(context, s.t('saved'));
}

/// Records a payment (or, with [refundOf], a refund) as cash or an account
/// movement in Finance. The accounts come from Finance; without one the
/// person is told to add it there first.
Future<void> recordCash(BuildContext context, PurchaseDetailController c,
    {PurchaseReturn? refundOf}) async {
  final s = PurchasesStrings.of(context);
  final List<FinanceAccountChoice> accounts;
  try {
    accounts = await c.repository.financeAccounts();
  } on Object catch (error) {
    if (context.mounted) recordsToast(context, _failure(context, error));
    return;
  }
  if (!context.mounted) return;
  if (accounts.isEmpty) {
    recordsToast(context, s.t('noAccounts'));
    return;
  }
  final purchase = c.detail!.purchase;
  final initial =
      accounts.where((a) => a.kind == 'cash').firstOrNull ?? accounts.first;
  final key = newIdempotencyKey(refundOf == null ? 'cash' : 'cashref');
  final refund = refundOf != null;
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      title: s.t(refund ? 'cashRefund' : 'cashPayment'),
      intro: s.t(refund ? 'cashRefundHelp' : 'cashPaymentHelp'),
      submitLabel: s.t('record'),
      fields: [
        RecordsInput.choice('account_id', s.t('account'),
            initial: initial.id,
            options: {
              for (final a in accounts) a.id: '${a.name} (${a.currency})'
            }),
        RecordsInput.number('amount', s.t('amount'),
            required: true, initial: refund ? refundOf.amount : purchase.total),
        RecordsInput.date('posted_on', s.t('paidOn'),
            required: true,
            initial: refund ? isoDay(DateTime.now()) : purchase.orderedOn),
      ],
      onSubmit: (v) => c.recordCash(
          accountId: v['account_id']! as String,
          amount: v['amount']! as String,
          postedOn: v['posted_on']! as String,
          idempotencyKey: key,
          refundOf: refundOf),
    ),
  );
  if (saved && context.mounted) {
    recordsToast(context, s.t(refund ? 'cashRefundDone' : 'cashPaymentDone'));
  }
}

Future<void> unlinkPayment(
    BuildContext context, PurchaseDetailController c) async {
  final s = PurchasesStrings.of(context);
  final sure = await confirmRecords(context,
      title: s.t('unlinkTitle'),
      message: s.t('unlinkText'),
      action: s.t('unlink'));
  if (sure && context.mounted) {
    await runPurchaseAction(context, c.unlinkPayment, done: s.t('unlinked'));
  }
}

/// Attaches a receipt from the document vault (search, or upload a new
/// file there), or removes the one attached.
Future<void> chooseReceipt(BuildContext context, PurchaseDetailController c,
    PurchaseReceipts receipts) async {
  final s = PurchasesStrings.of(context);
  final picked = await showRecordsSheet<_ReceiptPick>(
      context,
      (_) => _ReceiptSheet(
          receipts: receipts,
          hasReceipt: c.detail!.purchase.documentId != null));
  if (picked == null || !context.mounted) return;
  await runPurchaseAction(context, () => c.setReceipt(picked.documentId),
      done: s
          .t(picked.documentId == null ? 'receiptRemoved' : 'receiptAttached'));
}

class _ReceiptPick {
  const _ReceiptPick(this.documentId);
  final String? documentId;
}

class _ReceiptSheet extends StatefulWidget {
  const _ReceiptSheet({required this.receipts, required this.hasReceipt});
  final PurchaseReceipts receipts;
  final bool hasReceipt;

  @override
  State<_ReceiptSheet> createState() => _ReceiptSheetState();
}

class _ReceiptSheetState extends State<_ReceiptSheet> {
  List<ReceiptChoice>? found;
  String? error;
  bool uploading = false;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _search('');
  }

  Future<void> _search(String query) async {
    final generation = ++_generation;
    setState(() {
      found = null;
      error = null;
    });
    try {
      final items = await widget.receipts.search(query);
      if (mounted && generation == _generation) setState(() => found = items);
    } on Object catch (e) {
      if (mounted && generation == _generation) {
        setState(() => error = RecordsFailure.from(e).message);
      }
    }
  }

  Future<void> _upload() async {
    setState(() {
      uploading = true;
      error = null;
    });
    try {
      final doc = await widget.receipts.upload();
      if (!mounted) return;
      if (doc != null) {
        Navigator.of(context).pop(_ReceiptPick(doc.id));
        return;
      }
    } on Object catch (e) {
      if (mounted) error = RecordsFailure.from(e).message;
    }
    if (mounted) setState(() => uploading = false);
  }

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    final p = context.palette;
    final items = found;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(s.t('receiptTitle'),
            style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('receiptHelp'), style: TextStyle(color: p.muted)),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton.icon(
            key: const Key('receipt-upload'),
            onPressed: uploading ? null : _upload,
            icon: const Icon(Icons.upload_file),
            label: Text(uploading ? s.t('uploading') : s.t('receiptUpload')),
          ),
          if (widget.hasReceipt)
            TextButton.icon(
              key: const Key('receipt-remove'),
              onPressed: uploading
                  ? null
                  : () => Navigator.of(context).pop(const _ReceiptPick(null)),
              icon: const Icon(Icons.link_off),
              label: Text(s.t('receiptRemove')),
            ),
        ]),
        const SizedBox(height: AnumSpacing.sm),
        RecordsSearchField(
            initial: '', hint: s.t('receiptSearch'), onSearch: _search),
        if (error != null)
          Semantics(
            liveRegion: true,
            child: Text(error!,
                key: const Key('receipt-error'),
                style: TextStyle(color: p.stop)),
          )
        else if (items == null)
          const Padding(
            padding: EdgeInsets.all(AnumSpacing.md),
            child: Center(child: CircularProgressIndicator()),
          )
        else if (items.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: AnumSpacing.sm),
            child: Text(s.t('receiptEmpty'),
                key: const Key('receipt-empty'),
                style: TextStyle(color: p.muted)),
          )
        else
          for (final doc in items)
            AnumRow(
              key: Key('receipt-${doc.id}'),
              icon: Icons.receipt_long_outlined,
              title: doc.title,
              subtitle: doc.category.isEmpty ? null : doc.category,
              onTap: () => Navigator.of(context).pop(_ReceiptPick(doc.id)),
            ),
      ],
    );
  }
}

/// A short list of actions in a sheet; pops the chosen key.
class _ActionList extends StatelessWidget {
  const _ActionList({required this.title, required this.actions});
  final String title;
  final Map<String, (IconData, String)> actions;

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AnumSpacing.xs),
          for (final MapEntry(:key, :value) in actions.entries)
            ListTile(
              key: Key('action-$key'),
              leading: Icon(value.$1),
              title: Text(value.$2),
              onTap: () => Navigator.of(context).pop(key),
            ),
        ],
      );
}
