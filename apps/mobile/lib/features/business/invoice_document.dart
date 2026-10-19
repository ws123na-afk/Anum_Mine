import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'invoice_models.dart';

/// One invoice as the phone shows and hands on a printable copy
/// (docs/business.md#printing-and-pdf). Built from the owner's invoice or the
/// customer's portal invoice, so both previews read the same.
///
/// The phone cannot put a bearer token in a link, so "Print or PDF" fetches
/// the server's printable page with the session header and saves it through
/// the system save dialog (the file opens in any browser: Print, Save as
/// PDF). The same figures show here natively and copy as plain text.
class InvoiceDocument {
  const InvoiceDocument({
    required this.status,
    required this.currency,
    required this.lines,
    required this.subtotal,
    required this.tax,
    required this.total,
    required this.paid,
    required this.balance,
    required this.payments,
    this.number,
    this.issueDate,
    this.dueDate,
    this.sellerName = '',
    this.sellerAddress = '',
    this.sellerTax = '',
    this.sellerContact = '',
    this.bankDetails = '',
    this.buyerName = '',
    this.buyerAddress = '',
    this.buyerTaxNumber = '',
    this.reference = '',
    this.notes = '',
    this.paymentTerms = '',
    this.voidReason = '',
  });

  final String status, currency;
  final String? number, issueDate, dueDate;
  final String sellerName, sellerAddress, sellerTax, sellerContact, bankDetails;
  final String buyerName, buyerAddress, buyerTaxNumber, reference;
  final String notes, paymentTerms, voidReason;
  final List<InvoiceDocumentLine> lines;
  final double subtotal, tax, total, paid, balance;
  final List<InvoiceDocumentPayment> payments;

  /// The title: the number once it has one, else the status says why not.
  String heading(AnumLocalizations l) =>
      number ?? l.t('biz.inv.status.$status');

  /// Plain text for the clipboard: seller, buyer, lines, totals, payments.
  String toText(AnumLocalizations l) {
    String m(double v) => formatMoney(v, currency, locale: l.locale);
    final out = StringBuffer()
      ..writeln('${l.t('biz.inv.doc.invoice')} ${heading(l)}')
      ..writeln(l.t('biz.inv.status.$status'));
    if (status != 'sent' && status != 'paid' && status != 'void') {
      out.writeln(l.t('biz.inv.doc.notSent'));
    }
    out.writeln();
    if (sellerName.isNotEmpty) {
      out
        ..writeln('${l.t('biz.inv.doc.from')}: $sellerName')
        ..writeAll([
          if (sellerAddress.isNotEmpty) sellerAddress,
          if (sellerTax.isNotEmpty) sellerTax,
          if (sellerContact.isNotEmpty) sellerContact,
        ].map((e) => '$e\n'));
    }
    out
      ..writeln('${l.t('biz.inv.doc.to')}: $buyerName')
      ..writeAll([
        if (buyerAddress.isNotEmpty) buyerAddress,
        if (buyerTaxNumber.isNotEmpty) buyerTaxNumber,
      ].map((e) => '$e\n'));
    out
      ..writeln('${l.t('biz.inv.doc.issued')}: ${dayOf(issueDate)}')
      ..writeln('${l.t('biz.inv.doc.due')}: ${dayOf(dueDate)}');
    if (reference.isNotEmpty) {
      out.writeln('${l.t('biz.inv.doc.reference')}: $reference');
    }
    out.writeln();
    for (var i = 0; i < lines.length; i++) {
      final line = lines[i];
      out.writeln(
          '${i + 1}. ${line.description}: ${_qty(line.quantity)} x ${m(line.unitPrice)}, ${l.t('biz.inv.doc.tax')} ${_pct(line.taxRatePct)}%, ${m(line.total)}');
    }
    out
      ..writeln()
      ..writeln('${l.t('biz.inv.doc.subtotal')}: ${m(subtotal)}')
      ..writeln('${l.t('biz.inv.doc.tax')}: ${m(tax)}')
      ..writeln('${l.t('biz.inv.doc.total')}: ${m(total)}')
      ..writeln('${l.t('biz.inv.doc.paid')}: ${m(paid)}')
      ..writeln('${l.t('biz.inv.doc.balance')}: ${m(balance)}');
    if (payments.isNotEmpty) {
      out
        ..writeln()
        ..writeln(l.t('biz.inv.doc.payments'));
      for (final p in payments) {
        out.writeln(
            '${p.paidOn}: ${m(p.amount)}, ${l.t('biz.inv.method.${p.method}')}${p.reference.isEmpty ? '' : ', ${p.reference}'}');
      }
    }
    if (bankDetails.isNotEmpty) {
      out
        ..writeln()
        ..writeln(l.t('biz.inv.doc.bank'))
        ..writeln(bankDetails);
    }
    if (paymentTerms.isNotEmpty) {
      out
        ..writeln()
        ..writeln(paymentTerms);
    }
    if (notes.isNotEmpty) out.writeln(notes);
    if (voidReason.isNotEmpty) {
      out.writeln('${l.t('biz.inv.doc.voidReason')}: $voidReason');
    }
    return out.toString().trimRight();
  }

  /// The owner's invoice. A draft or waiting one has no seller copy yet, so
  /// [fallback] (the billing profile) stands in for it.
  factory InvoiceDocument.fromInvoice(Invoice invoice,
      {BillingProfile? fallback}) {
    final seller = invoice.seller ?? fallback;
    return InvoiceDocument(
      status: invoice.status,
      currency: invoice.currency,
      number: invoice.number,
      issueDate: invoice.issueDate,
      dueDate: invoice.dueDate,
      sellerName: seller?.legalName ?? '',
      sellerAddress: seller?.address ?? '',
      sellerTax: seller == null || seller.taxNumber.isEmpty
          ? ''
          : '${seller.taxLabel}: ${seller.taxNumber}',
      sellerContact: [
        if (seller != null && seller.email.isNotEmpty) seller.email,
        if (seller != null && seller.phone.isNotEmpty) seller.phone,
      ].join(', '),
      bankDetails: seller?.bankDetails ?? '',
      buyerName: invoice.billToName,
      buyerAddress: invoice.billToAddress,
      buyerTaxNumber: invoice.billToTaxNumber,
      reference: invoice.customerReference,
      notes: invoice.notes,
      paymentTerms: invoice.paymentTerms,
      voidReason: invoice.voidReason,
      lines: [
        for (final line in invoice.lines)
          InvoiceDocumentLine(line.description, line.quantity, line.unitPrice,
              line.taxRatePct, line.total),
      ],
      subtotal: invoice.subtotal,
      tax: invoice.tax,
      total: invoice.total,
      paid: invoice.amountPaid,
      balance: invoice.balance,
      payments: [
        for (final p in invoice.payments)
          InvoiceDocumentPayment(p.paidOn, p.amount, p.method, p.reference),
      ],
    );
  }
}

class InvoiceDocumentLine {
  const InvoiceDocumentLine(
      this.description, this.quantity, this.unitPrice, this.taxRatePct, this.total);
  final String description;
  final double quantity, unitPrice, taxRatePct, total;
}

class InvoiceDocumentPayment {
  const InvoiceDocumentPayment(
      this.paidOn, this.amount, this.method, this.reference);
  final String paidOn, method, reference;
  final double amount;
}

String _qty(double v) =>
    v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toString();
String _pct(double v) =>
    v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toString();

/// Hands a printable page to the person: the system save dialog. Replaced in
/// tests.
abstract interface class InvoiceDocumentSaver {
  Future<bool> save(String fileName, Uint8List bytes);
}

/// Saves through `file_picker` (already a dependency: the Command report and
/// the audit export save the same way).
class FilePickerInvoiceSaver implements InvoiceDocumentSaver {
  const FilePickerInvoiceSaver();
  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    final path = await FilePicker.platform.saveFile(
      dialogTitle: fileName,
      fileName: fileName,
      bytes: bytes,
    );
    return path != null;
  }
}

/// Invoice preview: the seller, the buyer, the lines, the totals and the
/// payments, with "Copy as text" and "Save printable copy".
class InvoicePreviewScreen extends StatefulWidget {
  const InvoicePreviewScreen({
    required this.document,
    required this.loadHtml,
    this.saver = const FilePickerInvoiceSaver(),
    this.fileStem = 'invoice',
    super.key,
  });
  final InvoiceDocument document;

  /// Fetches the server's printable page for a language (`en` or `ar`).
  final Future<String> Function(String lang) loadHtml;
  final InvoiceDocumentSaver saver;
  final String fileStem;

  @override
  State<InvoicePreviewScreen> createState() => _InvoicePreviewState();
}

class _InvoicePreviewState extends State<InvoicePreviewScreen> {
  bool busy = false;

  void _say(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  Future<void> _copy() async {
    final l = context.anum;
    await Clipboard.setData(
        ClipboardData(text: widget.document.toText(l)));
    if (mounted) _say(l.t('biz.inv.doc.copied'));
  }

  Future<void> _save() async {
    final l = context.anum;
    setState(() => busy = true);
    try {
      final html = await widget.loadHtml(l.isArabic ? 'ar' : 'en');
      final saved = await widget.saver.save(
          '${widget.fileStem}.html', Uint8List.fromList(utf8.encode(html)));
      if (mounted && saved) _say(l.t('biz.inv.doc.saved'));
    } on ApiException catch (e) {
      if (mounted) _say(l.f('biz.inv.doc.saveFailed', {'message': e.readable}));
    } on Object catch (e) {
      if (mounted) _say(l.f('biz.inv.doc.saveFailed', {'message': '$e'}));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = widget.document;
    String m(double v) => formatMoney(v, d.currency, locale: l.locale);
    final sent = d.status == 'sent' || d.status == 'paid' || d.status == 'void';
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.inv.doc.title'))),
      body: AnumPage(children: [
        AnumHeader(
          eyebrow: l.t('biz.inv.status.${d.status}'),
          title: d.heading(l),
          subtitle: sent
              ? '${l.t('biz.inv.doc.issued')} ${dayOf(d.issueDate)}, ${l.t('biz.inv.doc.due')} ${dayOf(d.dueDate)}'
              : l.t('biz.inv.doc.notSent'),
        ),
        Wrap(spacing: 8, runSpacing: 8, children: [
          FilledButton.icon(
            key: const Key('inv-doc-save'),
            onPressed: busy ? null : _save,
            icon: const Icon(Icons.picture_as_pdf_outlined),
            label: Text(l.t('biz.inv.doc.save')),
          ),
          OutlinedButton.icon(
            key: const Key('inv-doc-copy'),
            onPressed: _copy,
            icon: const Icon(Icons.copy_outlined),
            label: Text(l.t('biz.inv.doc.copy')),
          ),
        ]),
        Text(l.t('biz.inv.doc.saveHint'),
            style: TextStyle(color: context.palette.muted)),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.doc.from')),
              if (d.sellerName.isEmpty)
                Text(l.t('biz.inv.doc.noSeller'),
                    style: TextStyle(color: context.palette.muted))
              else ...[
                Text(d.sellerName,
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                if (d.sellerAddress.isNotEmpty) Text(d.sellerAddress),
                if (d.sellerTax.isNotEmpty) Text(d.sellerTax),
                if (d.sellerContact.isNotEmpty) Text(d.sellerContact),
              ],
            ],
          ),
        ),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.doc.to')),
              Text(d.buyerName.isEmpty ? '-' : d.buyerName,
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              if (d.buyerAddress.isNotEmpty) Text(d.buyerAddress),
              if (d.buyerTaxNumber.isNotEmpty) Text(d.buyerTaxNumber),
              if (d.reference.isNotEmpty)
                Text('${l.t('biz.inv.doc.reference')}: ${d.reference}'),
            ],
          ),
        ),
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.lines')),
              for (final line in d.lines)
                AnumRow(
                  title: line.description,
                  subtitle:
                      '${_qty(line.quantity)} x ${m(line.unitPrice)}, ${l.t('biz.inv.doc.tax')} ${_pct(line.taxRatePct)}%',
                  detail: m(line.total),
                ),
            ],
          ),
        ),
        AnumSurface(child: InvoiceTotals(document: d)),
        if (d.payments.isNotEmpty)
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AnumHeader(title: l.t('biz.inv.doc.payments')),
                for (final p in d.payments)
                  AnumRow(
                    title: m(p.amount),
                    subtitle:
                        '${p.paidOn}, ${l.t('biz.inv.method.${p.method}')}',
                    detail: p.reference.isEmpty ? null : p.reference,
                  ),
              ],
            ),
          ),
        if (d.bankDetails.isNotEmpty)
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AnumHeader(title: l.t('biz.inv.doc.bank')),
                Text(d.bankDetails),
              ],
            ),
          ),
        if (d.paymentTerms.isNotEmpty || d.notes.isNotEmpty)
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (d.paymentTerms.isNotEmpty) Text(d.paymentTerms),
                if (d.notes.isNotEmpty) Text(d.notes),
              ],
            ),
          ),
        if (d.voidReason.isNotEmpty)
          Text('${l.t('biz.inv.doc.voidReason')}: ${d.voidReason}',
              style: TextStyle(color: context.palette.stop)),
      ]),
    );
  }
}

/// Subtotal, tax, total, paid and balance, as the server worked them out.
class InvoiceTotals extends StatelessWidget {
  const InvoiceTotals({required this.document, super.key});
  final InvoiceDocument document;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final d = document;
    String m(double v) => formatMoney(v, d.currency, locale: l.locale);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(title: l.t('biz.inv.doc.subtotal'), detail: m(d.subtotal)),
        AnumRow(title: l.t('biz.inv.doc.tax'), detail: m(d.tax)),
        AnumRow(title: l.t('biz.inv.doc.total'), detail: m(d.total)),
        AnumRow(title: l.t('biz.inv.doc.paid'), detail: m(d.paid)),
        AnumRow(title: l.t('biz.inv.doc.balance'), detail: m(d.balance)),
      ],
    );
  }
}
