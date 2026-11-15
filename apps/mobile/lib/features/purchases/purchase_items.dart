import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'purchases_controller.dart';
import 'purchases_data.dart';
import 'purchases_strings.dart';

/// A purchase's items and a return's items edited on the phone, as the web
/// does (docs/purchases.md#mobile): the purchase's lines (what, how many,
/// the price of one) are sent whole with the version on screen (`PATCH
/// /purchases/{id}` with `items`; a line keeps its id, so returns that name
/// it still do), and a return names how many of each item go back (`PATCH
/// /purchases/returns/{id}` with `items`; none is the whole order). The
/// server checks what can still be returned and refuses more in its own
/// words.

/// A quantity or price the API accepts, or null. Arabic digits and a comma
/// decimal are read as Western.
String? _number(String text) => decimalText(text);

class _Line {
  _Line({this.id, this.description = '', this.quantity = '1', this.price = ''});
  final String? id;
  String description, quantity, price;
}

/// The PATCH `items` of a purchase from its lines.
List<Map<String, Object?>> purchaseItemsBody(
        List<({String? id, String description, String quantity, String price})>
            lines) =>
    [
      for (final l in lines)
        {
          if (l.id != null) 'id': l.id,
          'description': l.description.trim(),
          'quantity': _number(l.quantity)!,
          'unit_price': l.price.trim().isEmpty ? null : _number(l.price),
        }
    ];

/// Opens the purchase's items; true when they were saved.
Future<bool> editPurchaseItems(
    BuildContext context, PurchaseDetailController c) async {
  final saved = await Navigator.of(context).push<bool>(
      MaterialPageRoute(builder: (_) => PurchaseItemsScreen(controller: c)));
  if (saved == true && context.mounted) {
    recordsToast(context, PurchasesStrings.of(context).t('itemsSaved'));
  }
  return saved == true;
}

class PurchaseItemsScreen extends StatefulWidget {
  const PurchaseItemsScreen({required this.controller, super.key});
  final PurchaseDetailController controller;

  @override
  State<PurchaseItemsScreen> createState() => _PurchaseItemsScreenState();
}

class _PurchaseItemsScreenState extends State<PurchaseItemsScreen> {
  final _form = GlobalKey<FormState>();
  late final List<_Line> _lines = [
    for (final i in widget.controller.detail!.items)
      _Line(
          id: i.id,
          description: i.description,
          quantity: _trimZeros(i.quantity),
          price: i.unitPrice ?? '')
  ];
  bool _busy = false;
  String? _error;

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.controller.update({
        'items': purchaseItemsBody([
          for (final l in _lines)
            (
              id: l.id,
              description: l.description,
              quantity: l.quantity,
              price: l.price
            )
        ])
      });
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = isStale(error)
            ? KitStrings.of(context).t('conflict')
            : RecordsFailure.from(error).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    final k = KitStrings.of(context);
    final p = context.palette;
    final purchase = widget.controller.detail!.purchase;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('editItems'))),
      body: Form(
        key: _form,
        child: AnumPage(children: [
          Text('${purchase.merchant} · ${purchase.currency}',
              style: Theme.of(context).textTheme.titleMedium),
          Text(s.t('itemsHelp'), style: TextStyle(color: p.muted)),
          if (_lines.isEmpty)
            Text(s.t('itemsNone'),
                key: const Key('items-none'),
                style: TextStyle(color: p.muted)),
          for (var i = 0; i < _lines.length; i++)
            Container(
              key: ObjectKey(_lines[i]),
              padding: const EdgeInsets.all(AnumSpacing.sm),
              decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: p.line)),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    TextFormField(
                      key: Key('item-description-$i'),
                      initialValue: _lines[i].description,
                      enabled: !_busy,
                      maxLength: 200,
                      decoration:
                          InputDecoration(labelText: s.t('itemDescription')),
                      onChanged: (v) => _lines[i].description = v,
                      validator: (v) => (v ?? '').trim().isEmpty
                          ? s.t('itemNeedsName')
                          : null,
                    ),
                    TextFormField(
                      key: Key('item-quantity-$i'),
                      initialValue: _lines[i].quantity,
                      enabled: !_busy,
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      textDirection: TextDirection.ltr,
                      decoration:
                          InputDecoration(labelText: s.t('itemQuantity')),
                      onChanged: (v) => _lines[i].quantity = v,
                      validator: (v) {
                        final n = _number(v ?? '');
                        return n == null || (double.tryParse(n) ?? 0) <= 0
                            ? s.t('itemBadQuantity')
                            : null;
                      },
                    ),
                    TextFormField(
                      key: Key('item-price-$i'),
                      initialValue: _lines[i].price,
                      enabled: !_busy,
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      textDirection: TextDirection.ltr,
                      decoration: InputDecoration(
                          labelText: s.t('itemPrice'),
                          suffixText: purchase.currency),
                      onChanged: (v) => _lines[i].price = v,
                      validator: (v) =>
                          (v ?? '').trim().isNotEmpty && _number(v!) == null
                              ? s.t('itemBadPrice')
                              : null,
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerEnd,
                      child: TextButton.icon(
                        key: Key('item-remove-$i'),
                        onPressed:
                            _busy ? null : () => setState(() => _lines.removeAt(i)),
                        icon: const Icon(Icons.delete_outline),
                        label: Text(s.t('itemRemove')),
                      ),
                    ),
                  ]),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('item-add'),
              onPressed: _busy ? null : () => setState(() => _lines.add(_Line())),
              icon: const Icon(Icons.add),
              label: Text(s.t('itemAdd')),
            ),
          ),
          if (_error != null)
            Semantics(
              liveRegion: true,
              child: Text(_error!,
                  key: const Key('items-error'),
                  style: TextStyle(color: p.stop)),
            ),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
            FilledButton.icon(
              key: const Key('items-save'),
              onPressed: _busy ? null : _save,
              icon: const Icon(Icons.save_outlined),
              label: Text(k.t('save')),
            ),
            TextButton(
              onPressed: _busy ? null : () => Navigator.of(context).pop(false),
              child: Text(k.t('cancel')),
            ),
          ]),
        ]),
      ),
    );
  }
}

/// "2.000" read as "2" (the API keeps three decimals).
String _trimZeros(String value) => value.contains('.')
    ? value.replaceFirst(RegExp(r'\.?0+$'), '')
    : value;

/// The return's `items` from how many of each go back: empty and zero are
/// left out, so nothing at all is the whole order.
List<Map<String, Object?>> returnItemsBody(Map<String, String> picked) => [
      for (final MapEntry(:key, :value) in picked.entries)
        if (_number(value) case final n?)
          if ((double.tryParse(n) ?? 0) > 0) {'item_id': key, 'quantity': n}
    ];

/// Opens a return's items; true when they were saved.
Future<bool> editReturnItems(BuildContext context, PurchaseDetailController c,
    PurchaseReturn r) async {
  final saved = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => ReturnItemsScreen(controller: c, item: r)));
  if (saved == true && context.mounted) {
    recordsToast(context, PurchasesStrings.of(context).t('returnItemsSaved'));
  }
  return saved == true;
}

class ReturnItemsScreen extends StatefulWidget {
  const ReturnItemsScreen(
      {required this.controller, required this.item, super.key});
  final PurchaseDetailController controller;
  final PurchaseReturn item;

  @override
  State<ReturnItemsScreen> createState() => _ReturnItemsScreenState();
}

class _ReturnItemsScreenState extends State<ReturnItemsScreen> {
  final _form = GlobalKey<FormState>();
  late final Map<String, String> _picked = {
    for (final i in widget.controller.detail!.items)
      i.id: _trimZeros(widget.item.items
              .where((l) => l.itemId == i.id)
              .firstOrNull
              ?.quantity ??
          '')
  };
  bool _busy = false;
  String? _error;

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.controller
          .updateReturn(widget.item, {'items': returnItemsBody(_picked)});
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = isStale(error)
            ? KitStrings.of(context).t('conflict')
            : RecordsFailure.from(error).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = PurchasesStrings.of(context);
    final k = KitStrings.of(context);
    final p = context.palette;
    final items = widget.controller.detail!.items;
    return Scaffold(
      appBar: AppBar(title: Text(s.t('returnItems'))),
      body: Form(
        key: _form,
        child: AnumPage(children: [
          if (items.isEmpty)
            AnumEmpty(
                key: const Key('return-items-none'),
                icon: Icons.list_alt_outlined,
                title: s.t('wholeOrder'),
                message: s.t('returnItemsNone'))
          else ...[
            Text(s.t('returnItemsHelp'), style: TextStyle(color: p.muted)),
            for (final i in items)
              TextFormField(
                key: Key('return-qty-${i.id}'),
                initialValue: _picked[i.id],
                enabled: !_busy,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                textDirection: TextDirection.ltr,
                decoration: InputDecoration(
                    labelText: s.t('returnHowMany', {
                  'item': i.description,
                  'max': _trimZeros(i.quantity)
                })),
                onChanged: (v) => _picked[i.id] = v,
                validator: (v) {
                  final text = (v ?? '').trim();
                  if (text.isEmpty) return null;
                  final n = double.tryParse(_number(text) ?? '');
                  final max = double.tryParse(i.quantity) ?? 0;
                  return n == null || n < 0 || n > max
                      ? s.t('returnBadQuantity', {'max': _trimZeros(i.quantity)})
                      : null;
                },
              ),
          ],
          if (_error != null)
            Semantics(
              liveRegion: true,
              child: Text(_error!,
                  key: const Key('return-items-error'),
                  style: TextStyle(color: p.stop)),
            ),
          Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
            if (items.isNotEmpty)
              FilledButton.icon(
                key: const Key('return-items-save'),
                onPressed: _busy ? null : _save,
                icon: const Icon(Icons.save_outlined),
                label: Text(k.t('save')),
              ),
            TextButton(
              onPressed: _busy ? null : () => Navigator.of(context).pop(false),
              child: Text(k.t('cancel')),
            ),
          ]),
        ]),
      ),
    );
  }
}
