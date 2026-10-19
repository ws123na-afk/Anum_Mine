import 'package:clock/clock.dart';
import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'billing_profile.dart';
import 'business_controller.dart';
import 'invoice_controller.dart';
import 'invoice_document.dart';
import 'invoice_form.dart';
import 'invoice_models.dart';

/// Invoices on the phone (docs/business.md#invoices): the per-currency
/// summary, the filtered list, an invoice with its lines, approval state,
/// payments and the actions the server allows, a new-invoice form, a printable
/// copy and the billing profile. The phone never adds currencies together,
/// never works out a tax figure (the server does) and never sends an invoice:
/// an owner approves sending in Approvals.

String money(BuildContext context, double? amount, String currency) =>
    formatMoney(amount, currency, locale: context.anum.locale);

/// Parses a typed number; a comma is accepted as the decimal mark.
double? parseNumber(String text) =>
    double.tryParse(text.trim().replaceAll(',', '.'));

String plainNumber(double value) =>
    value == value.roundToDouble() ? value.toStringAsFixed(0) : '$value';

AnumTone invoiceTone(String status) => switch (status) {
      'paid' => AnumTone.ok,
      'sent' => AnumTone.info,
      'pending_approval' => AnumTone.warn,
      'void' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

/// Today as `YYYY-MM-DD` (the app's injectable clock, so tests pin it).
String today() => dayOfDate(clock.now());

String invoiceTitle(AnumLocalizations l, String? number, String status) =>
    number ?? l.t('biz.inv.status.$status');

String friendly(Object error) =>
    error is ApiException ? error.readable : '$error';

/// The Invoices section of Business.
class InvoicesView extends StatefulWidget {
  const InvoicesView({
    required this.controller,
    this.onOpenApprovals,
    this.saver = const FilePickerInvoiceSaver(),
    super.key,
  });
  final InvoicesController controller;
  final VoidCallback? onOpenApprovals;
  final InvoiceDocumentSaver saver;

  @override
  State<InvoicesView> createState() => _InvoicesViewState();
}

class _InvoicesViewState extends State<InvoicesView> {
  final search = TextEditingController();

  @override
  void initState() {
    super.initState();
    search.text = widget.controller.filter.query ?? '';
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  Future<void> _open(String id) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => InvoiceDetailScreen(
            controller: widget.controller,
            invoiceId: id,
            onOpenApprovals: widget.onOpenApprovals,
            saver: widget.saver,
          ),
        ),
      );

  Future<void> _new() async {
    final created = await Navigator.of(context).push<Invoice>(
      MaterialPageRoute<Invoice>(
        builder: (_) => NewInvoiceScreen(controller: widget.controller),
      ),
    );
    if (created == null) return;
    await widget.controller.refresh();
    if (mounted) await _open(created.id);
  }

  void _profile() => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => BillingProfileScreen(controller: widget.controller),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        switch (c.state) {
          case BusinessLoad.idle:
          case BusinessLoad.loading:
            return AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading'));
          case BusinessLoad.denied:
            return AnumFeedback(
                kind: AnumFeedbackKind.permission,
                message: c.error ?? l.t('biz.denied'));
          case BusinessLoad.error:
            return AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: l.f('biz.failed', {'message': c.error ?? ''}),
              onRetry: c.load,
            );
          case BusinessLoad.ready:
            break;
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _summary(context, c),
            const SizedBox(height: 16),
            _actions(context, c),
            const SizedBox(height: 16),
            _filters(context, c),
            const SizedBox(height: 16),
            ..._list(context, c),
          ],
        );
      },
    );
  }

  Widget _summary(BuildContext context, InvoicesController c) {
    final l = context.anum;
    final s = c.summary;
    if (s == null) {
      return Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        children: [
          Text(l.t('biz.inv.summaryFailed'),
              style: TextStyle(color: context.palette.muted)),
          TextButton(onPressed: c.refresh, child: Text(l.t('biz.retry'))),
        ],
      );
    }
    if (s.currencies.isEmpty) {
      return Text(l.t('biz.inv.summaryNone'),
          style: TextStyle(color: context.palette.muted));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final f in s.currencies) ...[
          if (s.currencies.length > 1)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(f.currency,
                  style: Theme.of(context).textTheme.titleSmall),
            ),
          AnumMetricGrid(
            children: [
              AnumMetric(
                label: l.t('biz.inv.sum.outstanding'),
                value: money(context, f.outstanding, f.currency),
                icon: Icons.hourglass_bottom_outlined,
              ),
              AnumMetric(
                label: l.t('biz.inv.sum.overdue'),
                value: money(context, f.overdue, f.currency),
                icon: Icons.event_busy_outlined,
                caption: l.f('biz.inv.sum.overdueCount', {'count': f.overdueCount}),
                accent: f.overdueCount > 0 ? context.palette.stop : null,
              ),
              AnumMetric(
                label: l.t('biz.inv.sum.invoiced'),
                value: money(context, f.invoicedThisMonth, f.currency),
                icon: Icons.receipt_long_outlined,
                caption: l.t('biz.inv.sum.thisMonth'),
              ),
              AnumMetric(
                label: l.t('biz.inv.sum.collected'),
                value: money(context, f.collectedThisMonth, f.currency),
                icon: Icons.payments_outlined,
                caption: l.t('biz.inv.sum.thisMonth'),
              ),
              AnumMetric(
                label: l.t('biz.inv.sum.drafts'),
                value: money(context, f.drafts, f.currency),
                icon: Icons.edit_note_outlined,
                caption: l.t('biz.inv.sum.draftsHint'),
              ),
            ],
          ),
          const SizedBox(height: 12),
        ],
        for (final note in s.notes)
          Text(note, style: TextStyle(color: context.palette.muted)),
        if (s.truncated)
          Text(l.t('biz.inv.summaryTruncated'),
              style: TextStyle(color: context.palette.warn)),
      ],
    );
  }

  Widget _actions(BuildContext context, InvoicesController c) {
    final l = context.anum;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        if (c.canWrite)
          FilledButton.icon(
            key: const Key('inv-new'),
            onPressed: _new,
            icon: const Icon(Icons.add),
            label: Text(l.t('biz.inv.new')),
          ),
        OutlinedButton.icon(
          key: const Key('inv-profile'),
          onPressed: _profile,
          icon: const Icon(Icons.badge_outlined),
          label: Text(l.t('biz.inv.profile')),
        ),
      ],
    );
  }

  Widget _filters(BuildContext context, InvoicesController c) {
    final l = context.anum;
    final f = c.filter;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const Key('inv-search'),
          controller: search,
          enabled: !f.overdueOnly,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            labelText: l.t('biz.inv.search'),
            prefixIcon: const Icon(Icons.search),
          ),
          onSubmitted: c.search,
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            ChoiceChip(
              key: const Key('inv-filter-all'),
              label: Text(l.t('biz.inv.filter.all')),
              selected: f.status == null && !f.overdueOnly,
              onSelected: (_) => c.applyFilter(InvoiceFilter(query: f.query)),
            ),
            for (final status in invoiceStatuses)
              ChoiceChip(
                key: Key('inv-filter-$status'),
                label: Text(l.t('biz.inv.status.$status')),
                selected: f.status == status && !f.overdueOnly,
                onSelected: (_) =>
                    c.applyFilter(InvoiceFilter(status: status, query: f.query)),
              ),
            FilterChip(
              key: const Key('inv-filter-overdue'),
              label: Text(l.t('biz.inv.filter.overdue')),
              selected: f.overdueOnly,
              onSelected: (on) => c.applyFilter(f.copyWith(
                overdueOnly: on,
                status: () => null,
              )),
            ),
          ],
        ),
      ],
    );
  }

  List<Widget> _list(BuildContext context, InvoicesController c) {
    final l = context.anum;
    final list = c.invoices;
    if (list.state == BusinessLoad.loading) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
      ];
    }
    if (list.items.isEmpty) {
      if (c.filter.active) {
        return [
          AnumFeedback(
              kind: AnumFeedbackKind.empty, message: l.t('biz.inv.noMatch')),
          Center(
            child: TextButton(
              onPressed: () {
                search.clear();
                c.applyFilter(const InvoiceFilter());
              },
              child: Text(l.t('biz.inv.clearFilters')),
            ),
          ),
        ];
      }
      return [
        AnumEmpty(
          title: l.t('biz.inv.empty'),
          message: c.canWrite
              ? l.t('biz.inv.emptyText')
              : l.t('biz.inv.emptyReadOnly'),
          icon: Icons.receipt_long_outlined,
          action: c.canWrite ? l.t('biz.inv.new') : null,
          onAction: c.canWrite ? _new : null,
        ),
      ];
    }
    return [
      for (final item in list.items) _row(context, item),
      if (list.hasMore)
        OutlinedButton(
          key: const Key('inv-more'),
          onPressed: list.loadingMore ? null : c.more,
          child: Text(l.f('biz.more',
              {'shown': list.items.length, 'total': list.total})),
        ),
      if (list.error != null)
        Text(list.error!, style: TextStyle(color: context.palette.stop)),
    ];
  }

  Widget _row(BuildContext context, InvoiceListItem item) {
    final l = context.anum;
    final owed = item.status == 'sent'
        ? l.f('biz.inv.rowBalance', {
            'balance': money(context, item.balance, item.currency),
            'due': dayOf(item.dueDate),
          })
        : null;
    return AnumSurface(
      key: Key('inv-row-${item.id}'),
      onTap: () => _open(item.id),
      child: AnumRow(
        title: invoiceTitle(l, item.number, item.status),
        subtitle: [
          item.accountName,
          if (item.projectName != null) item.projectName!,
        ].join(' · '),
        detail: [
          l.f('biz.inv.rowTotal',
              {'total': money(context, item.total, item.currency)}),
          if (owed != null) owed,
          if (item.overdue)
            l.f('biz.inv.daysOverdue', {'days': item.daysOverdue}),
        ].join(' · '),
        icon: Icons.receipt_long_outlined,
        trailing: AnumPill(
          label: l.t('biz.inv.status.${item.status}'),
          tone: item.overdue ? AnumTone.stop : invoiceTone(item.status),
        ),
      ),
    );
  }
}

/// One invoice: lines, totals, state and every action the server allows.
class InvoiceDetailScreen extends StatefulWidget {
  const InvoiceDetailScreen({
    required this.controller,
    required this.invoiceId,
    this.onOpenApprovals,
    this.saver = const FilePickerInvoiceSaver(),
    super.key,
  });
  final InvoicesController controller;
  final String invoiceId;
  final VoidCallback? onOpenApprovals;
  final InvoiceDocumentSaver saver;

  @override
  State<InvoiceDetailScreen> createState() => _InvoiceDetailState();
}

class _InvoiceDetailState extends State<InvoiceDetailScreen> {
  Invoice? invoice;
  String? error;
  String? notice;
  bool busy = false;

  InvoicesController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final next = await c.repository.get(widget.invoiceId);
      if (mounted) {
        setState(() {
          invoice = next;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    }
  }

  /// Runs one change; shows the server's refusal as it is.
  Future<void> act(Future<Invoice> Function() change, [String? done]) async {
    setState(() {
      busy = true;
      notice = null;
      error = null;
    });
    try {
      final next = await change();
      if (!mounted) return;
      setState(() {
        invoice = next;
        notice = done;
      });
      await c.refresh();
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<bool> _confirm(String title, String body, String confirm) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(context.anum.t('biz.cancel')),
            ),
            FilledButton(
              key: const Key('inv-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(confirm),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> _edit() async {
    final next = await Navigator.of(context).push<Invoice>(
      MaterialPageRoute<Invoice>(
        builder: (_) => EditInvoiceScreen(controller: c, invoice: invoice!),
      ),
    );
    if (next == null || !mounted) return;
    setState(() {
      invoice = next;
      notice = context.anum.t('biz.inv.saved');
    });
    await c.refresh();
  }

  Future<void> _delete() async {
    final l = context.anum;
    if (!await _confirm(l.t('biz.inv.deleteTitle'), l.t('biz.inv.deleteBody'),
        l.t('biz.inv.delete'))) {
      return;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await c.repository.delete(widget.invoiceId);
      await c.refresh();
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        setState(() {
          error = friendly(e);
          busy = false;
        });
      }
    }
  }

  Future<void> _void() async {
    final l = context.anum;
    final reason = await showDialog<String>(
      context: context,
      builder: (context) => ReasonDialog(
        title: l.t('biz.inv.voidTitle'),
        body: l.t('biz.inv.voidBody'),
        label: l.t('biz.inv.voidReason'),
        confirm: l.t('biz.inv.void'),
      ),
    );
    if (reason == null) return;
    await act(() => c.repository.voidInvoice(widget.invoiceId, reason),
        l.t('biz.inv.voided'));
  }

  Future<void> _pay() async {
    final inv = invoice!;
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (context) => PaymentDialog(
          balance: inv.balance, currency: inv.currency, issueDate: inv.issueDate),
    );
    if (body == null) return;
    await act(() => c.repository.recordPayment(inv.id, body),
        context.anum.t('biz.inv.paymentRecorded'));
  }

  Future<void> _removePayment(InvoicePayment p) async {
    final l = context.anum;
    if (!await _confirm(
        l.t('biz.inv.removePaymentTitle'),
        l.f('biz.inv.removePaymentBody', {
          'amount': money(context, p.amount, invoice!.currency),
          'date': p.paidOn,
        }),
        l.t('biz.inv.removePayment'))) {
      return;
    }
    await act(() => c.repository.removePayment(widget.invoiceId, p.id),
        l.t('biz.inv.paymentRemoved'));
  }

  void _preview() {
    final inv = invoice!;
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => InvoicePreviewScreen(
          document: InvoiceDocument.fromInvoice(inv, fallback: c.profile),
          loadHtml: (lang) => c.repository.printHtml(inv.id, lang),
          saver: widget.saver,
          fileStem: inv.number ?? 'invoice-draft',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final inv = invoice;
    return Scaffold(
      appBar: AppBar(
          title: Text(inv == null
              ? l.t('biz.inv.title')
              : invoiceTitle(l, inv.number, inv.status))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (inv == null && error == null)
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
          if (inv == null && error != null)
            AnumFeedback(
                kind: AnumFeedbackKind.error, message: error!, onRetry: load),
          if (inv != null) ..._body(context, inv),
        ],
      ),
    );
  }

  List<Widget> _body(BuildContext context, Invoice inv) {
    final l = context.anum;
    String m(double v) => money(context, v, inv.currency);
    final doc = InvoiceDocument.fromInvoice(inv);
    final billTo = [
      if (inv.billToAddress.isNotEmpty) inv.billToAddress,
      if (inv.billToEmail.isNotEmpty) inv.billToEmail,
      if (inv.billToTaxNumber.isNotEmpty) inv.billToTaxNumber,
    ].join(' · ');
    return [
      AnumHeader(
        eyebrow: l.t('biz.inv.status.${inv.status}'),
        title: invoiceTitle(l, inv.number, inv.status),
        subtitle: [
          inv.accountName,
          if (inv.projectName != null) inv.projectName!,
        ].join(' · '),
      ),
      Wrap(spacing: 8, runSpacing: 8, children: [
        AnumPill(
            label: l.t('biz.inv.status.${inv.status}'),
            tone: invoiceTone(inv.status)),
        if (inv.status == 'sent' || inv.status == 'paid')
          AnumPill(
              label: l.t('biz.inv.pay.${inv.paymentState}'),
              tone: inv.paymentState == 'paid'
                  ? AnumTone.ok
                  : inv.paymentState == 'partially_paid'
                      ? AnumTone.warn
                      : AnumTone.neutral),
        if (inv.overdue)
          AnumPill(
              label: l.f('biz.inv.daysOverdue', {'days': inv.daysOverdue}),
              tone: AnumTone.stop),
      ]),
      if (notice != null)
        Text(notice!,
            key: const Key('inv-notice'),
            style: TextStyle(color: context.palette.ok)),
      if (error != null)
        Text(error!,
            key: const Key('inv-error'),
            style: TextStyle(color: context.palette.stop)),
      if (inv.status == 'pending_approval') _approval(context, inv),
      if (inv.decisionNote.isNotEmpty && inv.status == 'draft')
        AnumSurface(
          accent: context.palette.warn,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('biz.inv.decisionNote'),
                  style: Theme.of(context).textTheme.titleSmall),
              Text(inv.decisionNote, key: const Key('inv-decision-note')),
            ],
          ),
        ),
      if (inv.issues.isNotEmpty &&
          (inv.status == 'draft' || inv.status == 'pending_approval'))
        AnumSurface(
          accent: context.palette.warn,
          child: Column(
            key: const Key('inv-issues'),
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('biz.inv.issues'),
                  style: Theme.of(context).textTheme.titleSmall),
              for (final issue in inv.issues) Text('• $issue'),
            ],
          ),
        ),
      _actions(context, inv),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.inv.lines')),
            for (final line in inv.lines)
              AnumRow(
                title: line.description,
                subtitle:
                    '${plainNumber(line.quantity)} x ${m(line.unitPrice)}, ${l.t('biz.inv.doc.tax')} ${plainNumber(line.taxRatePct)}%',
                detail: l.f('biz.inv.lineFigures', {
                  'net': m(line.net),
                  'tax': m(line.tax),
                  'total': m(line.total),
                }),
                trailing: line.manual
                    ? null
                    : AnumPill(label: l.t('biz.inv.source.${line.source}')),
              ),
          ],
        ),
      ),
      AnumSurface(child: InvoiceTotals(document: doc)),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumRow(
                title: l.t('biz.inv.doc.issued'), detail: dayOf(inv.issueDate)),
            AnumRow(
              title: l.t('biz.inv.doc.due'),
              detail: inv.dueDate == null
                  ? l.f('biz.inv.dueIn', {'days': inv.dueInDays})
                  : dayOf(inv.dueDate),
            ),
            AnumRow(
                title: l.t('biz.inv.doc.to'),
                subtitle: billTo.isEmpty ? null : billTo,
                detail: inv.billToName.isEmpty ? '-' : inv.billToName),
            if (inv.customerReference.isNotEmpty)
              AnumRow(
                  title: l.t('biz.inv.doc.reference'),
                  detail: inv.customerReference),
          ],
        ),
      ),
      if (inv.payments.isNotEmpty || inv.status == 'sent')
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.doc.payments')),
              if (inv.payments.isEmpty)
                Text(l.t('biz.inv.noPayments'),
                    style: TextStyle(color: context.palette.muted)),
              for (final p in inv.payments)
                AnumRow(
                  key: Key('inv-payment-${p.id}'),
                  title: m(p.amount),
                  subtitle: '${p.paidOn}, ${l.t('biz.inv.method.${p.method}')}',
                  detail: [
                    if (p.reference.isNotEmpty) p.reference,
                    if (p.note.isNotEmpty) p.note,
                  ].join(' · '),
                  trailing: inv.can.removePayment
                      ? IconButton(
                          key: Key('inv-payment-remove-${p.id}'),
                          tooltip: l.t('biz.inv.removePayment'),
                          onPressed: busy ? null : () => _removePayment(p),
                          icon: const Icon(Icons.delete_outline),
                        )
                      : null,
                ),
            ],
          ),
        ),
      if (inv.paymentTerms.isNotEmpty || inv.notes.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (inv.paymentTerms.isNotEmpty) Text(inv.paymentTerms),
              if (inv.notes.isNotEmpty) Text(inv.notes),
            ],
          ),
        ),
      if (inv.status == 'void' && inv.voidReason.isNotEmpty)
        Text('${l.t('biz.inv.voidReason')}: ${inv.voidReason}',
            style: TextStyle(color: context.palette.stop)),
    ];
  }

  Widget _approval(BuildContext context, Invoice inv) {
    final l = context.anum;
    return AnumSurface(
      accent: context.palette.warn,
      child: Column(
        key: const Key('inv-approval'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t('biz.inv.pendingTitle'),
              style: Theme.of(context).textTheme.titleSmall),
          Text(inv.approvalState == 'pending'
              ? l.t('biz.inv.pendingBody')
              : l.t('biz.inv.pendingLapsed')),
          if (inv.approvalHash != null && inv.approvalHash!.length >= 12)
            Text(
                l.f('biz.inv.checkCode',
                    {'code': inv.approvalHash!.substring(0, 12)}),
                style: TextStyle(color: context.palette.muted)),
          if (widget.onOpenApprovals != null)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton(
                key: const Key('inv-open-approvals'),
                onPressed: widget.onOpenApprovals,
                child: Text(l.t('biz.proposals.openApprovals')),
              ),
            ),
        ],
      ),
    );
  }

  Widget _actions(BuildContext context, Invoice inv) {
    final l = context.anum;
    final can = inv.can;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        if (can.requestSend)
          FilledButton.icon(
            key: const Key('inv-request'),
            onPressed: busy || inv.issues.isNotEmpty
                ? null
                : () => act(() => c.repository.requestApproval(inv.id),
                    l.t('biz.inv.requested')),
            icon: const Icon(Icons.send_outlined),
            label: Text(l.t('biz.inv.request')),
          ),
        if (can.cancelRequest)
          OutlinedButton(
            key: const Key('inv-cancel-request'),
            onPressed: busy
                ? null
                : () => act(() => c.repository.cancelRequest(inv.id),
                    l.t('biz.inv.requestCancelled')),
            child: Text(l.t('biz.inv.cancelRequest')),
          ),
        if (can.edit)
          OutlinedButton.icon(
            key: const Key('inv-edit'),
            onPressed: busy ? null : _edit,
            icon: const Icon(Icons.edit_outlined),
            label: Text(l.t('biz.inv.edit')),
          ),
        if (can.recordPayment)
          FilledButton.icon(
            key: const Key('inv-pay'),
            onPressed: busy ? null : _pay,
            icon: const Icon(Icons.payments_outlined),
            label: Text(l.t('biz.inv.recordPayment')),
          ),
        OutlinedButton.icon(
          key: const Key('inv-preview'),
          onPressed: busy ? null : _preview,
          icon: const Icon(Icons.picture_as_pdf_outlined),
          label: Text(l.t('biz.inv.preview')),
        ),
        if (can.voidIt)
          OutlinedButton(
            key: const Key('inv-void'),
            onPressed: busy ? null : _void,
            child: Text(l.t('biz.inv.void')),
          ),
        if (can.delete)
          OutlinedButton.icon(
            key: const Key('inv-delete'),
            onPressed: busy ? null : _delete,
            icon: const Icon(Icons.delete_outline),
            label: Text(l.t('biz.inv.delete')),
          ),
      ],
    );
  }
}

/// Asks for a reason of at least 3 characters (void).
class ReasonDialog extends StatefulWidget {
  const ReasonDialog({
    required this.title,
    required this.label,
    required this.confirm,
    this.body,
    super.key,
  });
  final String title, label, confirm;
  final String? body;

  @override
  State<ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<ReasonDialog> {
  final reason = TextEditingController();
  String? problem;

  @override
  void dispose() {
    reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(widget.title),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (widget.body != null) ...[
              Text(widget.body!),
              const SizedBox(height: 12),
            ],
            TextField(
              key: const Key('inv-reason'),
              controller: reason,
              autofocus: true,
              maxLength: 300,
              decoration: InputDecoration(
                  labelText: widget.label, errorText: problem),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('inv-reason-confirm'),
          onPressed: () {
            final text = reason.text.trim();
            if (text.length < 3) {
              setState(() => problem = l.t('biz.money.reasonRequired'));
              return;
            }
            Navigator.pop(context, text);
          },
          child: Text(widget.confirm),
        ),
      ],
    );
  }
}

/// Records a payment that arrived outside ANUM: amount (not above the
/// balance), date, method, reference and a note.
class PaymentDialog extends StatefulWidget {
  const PaymentDialog({
    required this.balance,
    required this.currency,
    this.issueDate,
    super.key,
  });
  final double balance;
  final String currency;
  final String? issueDate;

  @override
  State<PaymentDialog> createState() => _PaymentDialogState();
}

class _PaymentDialogState extends State<PaymentDialog> {
  late final amount =
      TextEditingController(text: widget.balance.toStringAsFixed(2));
  late final date = TextEditingController(text: today());
  final reference = TextEditingController();
  final note = TextEditingController();
  String method = 'bank_transfer';
  String? amountError, dateError;

  @override
  void dispose() {
    amount.dispose();
    date.dispose();
    reference.dispose();
    note.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final now = clock.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: DateTime.tryParse(date.text) ?? now,
      firstDate: DateTime.tryParse(widget.issueDate ?? '') ??
          DateTime(now.year - 2),
      lastDate: now,
    );
    if (picked != null) setState(() => date.text = dayOfDate(picked));
  }

  void _submit() {
    final l = context.anum;
    final value = parseNumber(amount.text);
    final when = DateTime.tryParse(date.text.trim());
    final shape = RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(date.text.trim());
    setState(() {
      amountError = value == null || value <= 0
          ? l.t('biz.inv.amountInvalid')
          : value > widget.balance + 0.004
              ? l.f('biz.inv.amountTooBig', {
                  'balance': formatMoney(widget.balance, widget.currency,
                      locale: l.locale)
                })
              : null;
      dateError = !shape || when == null ? l.t('biz.inv.dateInvalid') : null;
    });
    if (amountError != null || dateError != null) return;
    Navigator.pop(context, <String, Object?>{
      'amount': double.parse(value!.toStringAsFixed(2)),
      'paid_on': date.text.trim(),
      'method': method,
      'reference': reference.text.trim(),
      'note': note.text.trim(),
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.inv.recordPayment')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.f('biz.inv.paymentHint', {
              'balance': formatMoney(widget.balance, widget.currency,
                  locale: l.locale)
            })),
            const SizedBox(height: 12),
            TextField(
              key: const Key('inv-pay-amount'),
              controller: amount,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                  labelText: '${l.t('biz.inv.amount')} (${widget.currency})',
                  errorText: amountError),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('inv-pay-date'),
              controller: date,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(
                labelText: l.t('biz.inv.paidOn'),
                hintText: 'YYYY-MM-DD',
                errorText: dateError,
                suffixIcon: IconButton(
                  tooltip: l.t('biz.inv.pickDate'),
                  onPressed: _pick,
                  icon: const Icon(Icons.calendar_today_outlined),
                ),
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: const Key('inv-pay-method'),
              initialValue: method,
              isExpanded: true,
              decoration: InputDecoration(labelText: l.t('biz.inv.method')),
              items: [
                for (final m in paymentMethods)
                  DropdownMenuItem(
                      value: m, child: Text(l.t('biz.inv.method.$m'))),
              ],
              onChanged: (v) => setState(() => method = v ?? method),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('inv-pay-reference'),
              controller: reference,
              maxLength: 120,
              decoration: InputDecoration(labelText: l.t('biz.inv.reference')),
            ),
            TextField(
              key: const Key('inv-pay-note'),
              controller: note,
              maxLength: 300,
              decoration: InputDecoration(labelText: l.t('biz.inv.note')),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('inv-pay-submit'),
          onPressed: _submit,
          child: Text(l.t('biz.money.record')),
        ),
      ],
    );
  }
}
