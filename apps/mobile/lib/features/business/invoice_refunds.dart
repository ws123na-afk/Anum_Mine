import 'package:flutter/material.dart';

import 'invoices.dart' show friendly;
import '../../src/localization/amount_input.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'invoice_models.dart';
import 'invoice_repository.dart';

/// Refunds of a card payment asked for from ANUM (docs/business.md#refunds-from-anum)
/// and the provider refunds ANUM could not match
/// (docs/business.md#refunds-anum-could-not-match), on the phone.

String providerName(String provider) =>
    provider == 'moyasar' ? 'Moyasar' : 'Stripe';

/// One key per open refund form: a retried submit is the same request.
String newRefundKey([DateTime? now]) {
  final at = (now ?? DateTime.now()).microsecondsSinceEpoch;
  return 'refund-app-$at-${identityHashCode(Object())}';
}

/// The amount and reason of a refund; never above [max].
class RefundDialog extends StatefulWidget {
  const RefundDialog(
      {required this.max,
      required this.currency,
      required this.provider,
      super.key});
  final double max;
  final String currency, provider;

  @override
  State<RefundDialog> createState() => _RefundDialogState();
}

class _RefundDialogState extends State<RefundDialog> {
  late final amount =
      TextEditingController(text: widget.max.toStringAsFixed(2));
  final reason = TextEditingController();
  String? amountError, reasonError;

  @override
  void dispose() {
    amount.dispose();
    reason.dispose();
    super.dispose();
  }

  void _submit() {
    final l = context.anum;
    final value = readAmount(amount.text).number;
    final why = reason.text.replaceAll(RegExp(r'\s+'), ' ').trim();
    final max = formatMoney(widget.max, widget.currency, locale: l.locale);
    setState(() {
      amountError = value == null || value <= 0 || value > widget.max + 0.004
          ? l.f('biz.inv.rr.errAmount', {'max': max})
          : null;
      reasonError = why.length < 3 ? l.t('biz.inv.rr.errReason') : null;
    });
    if (amountError != null || reasonError != null) return;
    Navigator.pop(context, <String, Object?>{
      'amount': double.parse(value!.toStringAsFixed(2)),
      'reason': why,
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.inv.rr.title')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.f('biz.inv.rr.help', {
              'provider': providerName(widget.provider),
              'max': formatMoney(widget.max, widget.currency, locale: l.locale),
            })),
            const SizedBox(height: 12),
            TextField(
              key: const Key('inv-rr-amount'),
              controller: amount,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                  labelText: '${l.t('biz.inv.rr.amount')} (${widget.currency})',
                  errorText: amountError),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('inv-rr-reason'),
              controller: reason,
              maxLength: 300,
              decoration: InputDecoration(
                  labelText: l.t('biz.inv.rr.reason'), errorText: reasonError),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('inv-rr-submit'),
          onPressed: _submit,
          child: Text(l.t('biz.inv.rr.submit')),
        ),
      ],
    );
  }
}

/// The rows of the refunds asked for on one card payment.
List<Widget> refundRequestRows(
  BuildContext context,
  Invoice inv,
  String paymentId, {
  required bool busy,
  required void Function(RefundRequest) onWithdraw,
}) {
  final l = context.anum;
  final asked = inv.refundRequests.where((r) => r.paymentId == paymentId);
  return [
    for (final r in asked)
      AnumRow(
        key: Key('inv-rr-${r.id}'),
        title: formatMoney(r.amount, inv.currency, locale: l.locale),
        subtitle:
            '${l.t('biz.inv.rr.status.${r.status}')}, ${l.f('biz.inv.rr.line', {
              'by': r.requestedBy,
              'date': r.createdAt
            })}',
        detail: [
          r.reason,
          if (r.waiting && r.payloadHash != null)
            l.f('biz.inv.rr.code', {'hash': r.payloadHash!.substring(0, 12)}),
          if (!r.waiting && r.detail.isNotEmpty) r.detail,
        ].join(' · '),
        trailing: r.waiting && inv.can.refund
            ? IconButton(
                key: Key('inv-rr-withdraw-${r.id}'),
                tooltip: l.t('biz.inv.rr.withdraw'),
                onPressed: busy ? null : () => onWithdraw(r),
                icon: const Icon(Icons.undo_outlined),
              )
            : null,
      ),
  ];
}

/// Business, Invoices, Refunds to check: provider refunds of payments ANUM
/// never recorded. Members read; an owner marks one checked or opens it again.
class RefundReviewsScreen extends StatefulWidget {
  const RefundReviewsScreen(
      {required this.repository, required this.owner, super.key});
  final InvoicesRepository repository;
  final bool owner;

  @override
  State<RefundReviewsScreen> createState() => _RefundReviewsState();
}

class _RefundReviewsState extends State<RefundReviewsScreen> {
  static const _page = 20;
  String state = 'open';
  final items = <RefundReview>[];
  int total = 0;
  bool loading = true, busy = false;
  String? error, notice;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool more = false}) async {
    setState(() {
      loading = !more;
      busy = more;
      error = null;
    });
    try {
      final page = await widget.repository.refundReviews(
          state: state, offset: more ? items.length : 0, limit: _page);
      if (!mounted) return;
      setState(() {
        if (!more) items.clear();
        items.addAll(page.items);
        total = page.total;
      });
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) {
        setState(() {
          loading = false;
          busy = false;
        });
      }
    }
  }

  Future<void> _check(RefundReview review) async {
    final l = context.anum;
    final note = await showDialog<String>(
      context: context,
      builder: (context) => _CheckDialog(review: review),
    );
    if (note == null) return;
    await _change(() => widget.repository.checkRefundReview(review.id, note),
        l.t('biz.inv.rv.saved'));
  }

  Future<void> _change(Future<RefundReview> Function() run, String done) async {
    setState(() {
      busy = true;
      notice = null;
      error = null;
    });
    try {
      await run();
      if (!mounted) return;
      notice = done;
      await _load();
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.inv.rv.title'))),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(l.t('biz.inv.rv.help'),
                style: TextStyle(color: context.palette.muted)),
            if (!widget.owner) ...[
              const SizedBox(height: 8),
              Text(l.t('biz.inv.rv.ownerOnly'),
                  key: const Key('inv-rv-owner-only'),
                  style: TextStyle(color: context.palette.warn)),
            ],
            const SizedBox(height: 12),
            SegmentedButton<String>(
              key: const Key('inv-rv-filter'),
              segments: [
                for (final s in const ['open', 'checked', 'all'])
                  ButtonSegment(
                      value: s, label: Text(l.t('biz.inv.rv.filter.$s'))),
              ],
              selected: {state},
              onSelectionChanged: (picked) {
                setState(() => state = picked.first);
                _load();
              },
            ),
            const SizedBox(height: 12),
            if (notice != null)
              Text(notice!, style: TextStyle(color: context.palette.ok)),
            if (error != null && !loading)
              AnumFeedback(
                  kind: AnumFeedbackKind.error,
                  message: l.f('biz.inv.rv.failed', {'message': error!}),
                  onRetry: _load),
            if (loading)
              AnumFeedback(
                  kind: AnumFeedbackKind.loading, message: l.t('biz.loading'))
            else if (items.isEmpty && error == null)
              AnumFeedback(
                  kind: AnumFeedbackKind.empty,
                  message: state == 'checked'
                      ? l.t('biz.inv.rv.emptyChecked')
                      : l.t('biz.inv.rv.empty'))
            else
              AnumSurface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final r in items) _row(context, r),
                    if (items.length < total)
                      TextButton(
                        key: const Key('inv-rv-more'),
                        onPressed: busy ? null : () => _load(more: true),
                        child: Text(l.f('biz.inv.rv.more',
                            {'shown': items.length, 'total': total})),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _row(BuildContext context, RefundReview r) {
    final l = context.anum;
    final amount = ['SAR', 'AED', 'USD'].contains(r.currency)
        ? formatMoney(r.amount, r.currency, locale: l.locale)
        : '${r.currency} ${r.amount.toStringAsFixed(2)}';
    return AnumRow(
      key: Key('inv-rv-${r.id}'),
      title: amount,
      subtitle: [
        l.t('biz.inv.rv.state.${r.status}'),
        l.f('biz.inv.rv.line', {
          'provider': providerName(r.provider),
          'refund': r.providerRefundId
        }),
      ].join(', '),
      detail: [
        l.f('biz.inv.rv.payment',
            {'payment': r.providerPaymentId, 'date': r.createdAt}),
        if (r.invoiceNumber.isNotEmpty)
          l.f('biz.inv.rv.invoice', {'number': r.invoiceNumber}),
        if (!r.open && r.checkedBy != null)
          l.f('biz.inv.rv.checkedBy',
              {'by': r.checkedBy!, 'date': r.checkedAt ?? '-'}),
        if (r.note.isNotEmpty) r.note,
      ].join(' · '),
      trailing: !widget.owner
          ? null
          : r.open
              ? IconButton(
                  key: Key('inv-rv-check-${r.id}'),
                  tooltip: l.t('biz.inv.rv.check'),
                  onPressed: busy ? null : () => _check(r),
                  icon: const Icon(Icons.task_alt_outlined),
                )
              : IconButton(
                  key: Key('inv-rv-reopen-${r.id}'),
                  tooltip: l.t('biz.inv.rv.reopen'),
                  onPressed: busy
                      ? null
                      : () => _change(
                          () => widget.repository.reopenRefundReview(r.id),
                          l.t('biz.inv.rv.saved')),
                  icon: const Icon(Icons.undo_outlined),
                ),
    );
  }
}

class _CheckDialog extends StatefulWidget {
  const _CheckDialog({required this.review});
  final RefundReview review;

  @override
  State<_CheckDialog> createState() => _CheckDialogState();
}

class _CheckDialogState extends State<_CheckDialog> {
  final note = TextEditingController();

  @override
  void dispose() {
    note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.inv.rv.checkTitle')),
      content: TextField(
        key: const Key('inv-rv-note'),
        controller: note,
        maxLength: 300,
        decoration: InputDecoration(labelText: l.t('biz.inv.rv.note')),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('inv-rv-check-submit'),
          onPressed: () => Navigator.pop(
              context, note.text.replaceAll(RegExp(r'\s+'), ' ').trim()),
          child: Text(l.t('biz.inv.rv.check')),
        ),
      ],
    );
  }
}
