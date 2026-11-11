import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';

/// Save as purchase on the phone (docs/purchases.md#save-as-purchase-from-the-email-center):
/// the open message becomes a purchase draft, read by the same fixed patterns
/// as a shared order message, and one attachment the person picks is kept
/// privately in their document vault as the receipt. The server checks
/// Purchases, the Email Center, the mailbox and the vault.

/// Attachments worth offering as a receipt: downloadable ones, PDFs then images first.
List<MailAttachment> receiptChoices(List<MailAttachment> files) {
  int rank(MailAttachment f) {
    final name = f.filename.toLowerCase();
    if (name.endsWith('.pdf')) return 0;
    if (RegExp(r'\.(png|jpe?g|webp|heic)$').hasMatch(name)) return 1;
    return 2;
  }

  final list = files.where((f) => f.downloadable).toList()
    ..sort((a, b) {
      final byRank = rank(a).compareTo(rank(b));
      return byRank != 0 ? byRank : a.index.compareTo(b.index);
    });
  return list;
}

class SaveAsPurchaseButton extends StatelessWidget {
  const SaveAsPurchaseButton({required this.controller, super.key});
  final MailCenterController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final c = controller;
    final message = c.detail?.message;
    final saved = c.purchaseSaved;
    final done = saved != null &&
        message != null &&
        saved.uid == message.uid &&
        saved.folder == message.folder;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _button(context, l, c),
        if (done)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
                saved.text.isEmpty ? l.t('mc.purchase.saved') : saved.text,
                key: const Key('mc-purchase-done')),
          ),
      ],
    );
  }

  Widget _button(
      BuildContext context, AnumLocalizations l, MailCenterController c) {
    return OutlinedButton.icon(
      key: const Key('mc-save-purchase'),
      onPressed: c.busy == null && c.detail != null
          ? () => showDialog<void>(
                context: context,
                builder: (_) => SaveAsPurchaseDialog(controller: c),
              )
          : null,
      icon: const Icon(Icons.add_shopping_cart_outlined),
      label: Text(l.t('mc.purchase.button')),
    );
  }
}

class SaveAsPurchaseDialog extends StatefulWidget {
  const SaveAsPurchaseDialog({required this.controller, super.key});
  final MailCenterController controller;

  @override
  State<SaveAsPurchaseDialog> createState() => _SaveAsPurchaseState();
}

class _SaveAsPurchaseState extends State<SaveAsPurchaseDialog> {
  late final List<MailAttachment> choices =
      receiptChoices(widget.controller.detail?.attachments ?? const []);
  late int? pick = choices.isEmpty ? null : choices.first.index;
  String ownership = 'personal';
  bool busy = false;

  @override
  void initState() {
    super.initState();
    widget.controller.purchaseError = null;
  }

  Future<void> _save() async {
    setState(() => busy = true);
    final ok = await widget.controller
        .saveAsPurchase(attachmentIndex: pick, ownership: ownership);
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop();
    } else {
      setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final error = widget.controller.purchaseError;
    return AlertDialog(
      title: Text(l.t('mc.purchase.title')),
      scrollable: true,
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t('mc.purchase.help')),
          const SizedBox(height: 12),
          Text(l.t('mc.purchase.whose'),
              style: Theme.of(context).textTheme.titleSmall),
          RadioGroup<String>(
            groupValue: ownership,
            onChanged: (v) => setState(() => ownership = v ?? 'personal'),
            child: Column(children: [
              for (final value in const ['personal', 'business'])
                RadioListTile<String>(
                  key: Key('mc-purchase-owner-$value'),
                  value: value,
                  contentPadding: EdgeInsets.zero,
                  title: Text(l.t('mc.purchase.owner.$value')),
                ),
            ]),
          ),
          Text(l.t('mc.purchase.receipt'),
              style: Theme.of(context).textTheme.titleSmall),
          RadioGroup<int?>(
            groupValue: pick,
            onChanged: (v) => setState(() => pick = v),
            child: Column(children: [
              for (final file in choices)
                RadioListTile<int?>(
                  key: Key('mc-purchase-file-${file.index}'),
                  value: file.index,
                  contentPadding: EdgeInsets.zero,
                  title: Text(
                      file.filename.isEmpty ? file.contentType : file.filename,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis),
                ),
              RadioListTile<int?>(
                key: const Key('mc-purchase-file-none'),
                value: null,
                contentPadding: EdgeInsets.zero,
                title: Text(l.t('mc.purchase.noReceipt')),
              ),
            ]),
          ),
          if (choices.isEmpty) Text(l.t('mc.purchase.noFiles')),
          if (pick != null) Text(l.t('mc.purchase.private')),
          if (error != null && !busy) ...[
            const SizedBox(height: 8),
            Text(error,
                key: const Key('mc-purchase-error'),
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
        ],
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.of(context).pop(),
          child: Text(l.t('mc.purchase.cancel')),
        ),
        FilledButton(
          key: const Key('mc-purchase-save'),
          onPressed: busy ? null : _save,
          child:
              Text(busy ? l.t('mc.purchase.saving') : l.t('mc.purchase.save')),
        ),
      ],
    );
  }
}
