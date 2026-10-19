import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';
import 'catalog_data.dart';
import 'desk_widgets.dart';

/// The catalog section of Business (docs/business.md): products and service
/// packages, their priced versions, and the changes owners may make.

String _priceText(BuildContext context, CatalogOffer offer) {
  final l = context.anum;
  String amount(double? value) => formatMoney(value, offer.currency);
  return switch (offer.priceType) {
    'quote' => l.t('biz.cat.price.quote'),
    'range' => '${amount(offer.priceMin)} - ${amount(offer.priceMax)}',
    'from' => l.f('biz.cat.price.fromAmount', {
        'amount': amount(offer.priceMin),
      }),
    'hourly' => l.f('biz.cat.price.hourlyAmount', {
        'amount': amount(offer.priceMin),
      }),
    _ => amount(offer.priceMin),
  };
}

AnumTone _maturityTone(String maturity) => switch (maturity) {
      'live' => AnumTone.ok,
      'retired' => AnumTone.stop,
      'pilot' || 'prototype' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

class CatalogView extends StatefulWidget {
  const CatalogView({
    required this.controller,
    required this.canManage,
    super.key,
  });
  final CatalogController controller;
  final bool canManage;

  @override
  State<CatalogView> createState() => _CatalogViewState();
}

class _CatalogViewState extends State<CatalogView> {
  final search = TextEditingController();

  CatalogController get c => widget.controller;

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  Future<void> openNew() => Navigator.of(context).push(
        MaterialPageRoute<void>(
            builder: (_) => ProductFormScreen(controller: c)),
      );

  Future<void> open(CatalogProduct product) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ProductScreen(
            controller: c,
            productId: product.id,
            canManage: widget.canManage,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (!widget.canManage)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Text(
                l.t('biz.cat.ownersOnly'),
                key: const Key('cat-readonly'),
                style: TextStyle(color: context.palette.muted),
              ),
            ),
          TextField(
            key: const Key('cat-search'),
            controller: search,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              labelText: l.t('biz.cat.search'),
              prefixIcon: const Icon(Icons.search),
            ),
            onSubmitted: (text) =>
                c.filter(text: text, kind: c.kind, maturity: c.maturity),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
            spacing: AnumSpacing.xs,
            children: [
              ChoiceChip(
                key: const Key('cat-maturity-all'),
                label: Text(l.t('biz.leads.all')),
                selected: c.maturity == null,
                onSelected: (_) => c.filter(text: search.text, kind: c.kind),
              ),
              for (final item in productMaturities)
                ChoiceChip(
                  key: Key('cat-maturity-$item'),
                  label: Text(l.t('biz.cat.maturity.$item')),
                  selected: c.maturity == item,
                  onSelected: (_) =>
                      c.filter(text: search.text, kind: c.kind, maturity: item),
                ),
            ],
          ),
          if (widget.canManage)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton.icon(
                key: const Key('cat-new'),
                onPressed: openNew,
                icon: const Icon(Icons.add),
                label: Text(l.t('biz.cat.new')),
              ),
            ),
          DeskList<CatalogProduct>(
            state: c.state,
            items: c.items,
            total: c.total,
            loadingMore: c.loadingMore,
            error: c.error,
            onRetry: c.load,
            onMore: c.more,
            filtered: c.query != null || c.maturity != null,
            emptyTitle: l.t('biz.cat.empty'),
            emptyText: widget.canManage
                ? l.t('biz.cat.emptyOwner')
                : l.t('biz.cat.emptyText'),
            emptyAction: widget.canManage ? l.t('biz.cat.new') : null,
            onEmptyAction: widget.canManage ? openNew : null,
            row: (item) {
              final active = item.activeOffer;
              return AnumRow(
                key: Key('cat-row-${item.id}'),
                title: item.name,
                subtitle:
                    '${l.t('biz.cat.kind.${item.kind}')} · ${l.t('biz.cat.maturity.${item.maturity}')}',
                detail: active == null
                    ? l.t('biz.cat.noActive')
                    : '${l.f('biz.cat.version', {
                            'version': active.version
                          })} · ${_priceText(context, active)}',
                icon: Icons.inventory_2_outlined,
                tone: _maturityTone(item.maturity),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => open(item),
              );
            },
          ),
        ],
      ),
    );
  }
}

class ProductScreen extends StatefulWidget {
  const ProductScreen({
    required this.controller,
    required this.productId,
    required this.canManage,
    super.key,
  });
  final CatalogController controller;
  final String productId;
  final bool canManage;

  @override
  State<ProductScreen> createState() => _ProductScreenState();
}

class _ProductScreenState extends State<ProductScreen> {
  CatalogProduct? product;
  BusinessLoad state = BusinessLoad.loading;
  String? error, actionError;
  bool busy = false;

  CatalogController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      state = BusinessLoad.loading;
      error = null;
    });
    try {
      final found = await c.repository.product(widget.productId);
      if (!mounted) return;
      setState(() {
        product = found;
        state = BusinessLoad.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        state = e is ApiException && e.isPermissionDenied
            ? BusinessLoad.denied
            : BusinessLoad.error;
        error = e is ApiException ? e.readable : '$e';
      });
    }
  }

  Future<void> run(Future<CatalogProduct> Function() change) async {
    setState(() {
      busy = true;
      actionError = null;
    });
    try {
      final next = await change();
      if (mounted) setState(() => product = next);
    } on ApiException catch (e) {
      if (mounted) setState(() => actionError = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> edit() async {
    final next = await Navigator.of(context).push<CatalogProduct>(
      MaterialPageRoute(
        builder: (_) => ProductFormScreen(controller: c, product: product),
      ),
    );
    if (next != null && mounted) setState(() => product = next);
  }

  Future<void> editVersion([CatalogOffer? offer]) async {
    final next = await Navigator.of(context).push<CatalogProduct>(
      MaterialPageRoute(
        builder: (_) => VersionFormScreen(
          controller: c,
          productId: product!.id,
          offer: offer,
        ),
      ),
    );
    if (next != null && mounted) setState(() => product = next);
  }

  Future<void> activateOffer(CatalogOffer offer) async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.cat.activateTitle', {'version': offer.version}),
      message: l.t('biz.cat.activateText'),
      action: l.t('biz.cat.activate'),
    );
    if (ok) await run(() => c.activate(offer.id));
  }

  Future<void> retireOffer(CatalogOffer offer) async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.cat.retireTitle', {'version': offer.version}),
      message: offer.isDraft
          ? l.t('biz.cat.retireDraftText')
          : l.t('biz.cat.retireText'),
      action: offer.isDraft ? l.t('biz.cat.discard') : l.t('biz.cat.retire'),
    );
    if (ok) await run(() => c.retire(offer.id));
  }

  Future<void> remove() async {
    final l = context.anum;
    final ok = await confirmDestructive(
      context,
      title: l.f('biz.cat.deleteTitle', {'name': product!.name}),
      message: l.t('biz.cat.deleteText'),
      action: l.t('biz.cat.delete'),
    );
    if (!ok) return;
    setState(() {
      busy = true;
      actionError = null;
    });
    try {
      await c.delete(product!.id);
      if (mounted) Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          actionError = e.readable;
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = product;
    return Scaffold(
      appBar: AppBar(title: Text(p?.name ?? l.t('biz.tab.catalog'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (state == BusinessLoad.loading && p == null)
            AnumFeedback(
              kind: AnumFeedbackKind.loading,
              message: l.t('biz.loading'),
            ),
          if (state == BusinessLoad.denied)
            AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: l.t('biz.denied'),
            ),
          if (state == BusinessLoad.error)
            AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: l.f('biz.failed', {'message': error ?? ''}),
              onRetry: load,
            ),
          if (p != null) ...[
            if (actionError != null)
              Semantics(
                liveRegion: true,
                child: Text(
                  actionError!,
                  key: const Key('product-error'),
                  style: TextStyle(color: context.palette.stop),
                ),
              ),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  AnumHeader(
                    eyebrow: l.t('biz.cat.kind.${p.kind}'),
                    title: p.name,
                    subtitle: p.nameAr.isEmpty ? null : p.nameAr,
                  ),
                  Wrap(
                    spacing: AnumSpacing.xs,
                    children: [
                      AnumPill(
                        label: l.t('biz.cat.maturity.${p.maturity}'),
                        tone: _maturityTone(p.maturity),
                      ),
                    ],
                  ),
                  if (p.summary.isNotEmpty) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(p.summary),
                  ],
                  if (p.integrations.isNotEmpty) ...[
                    const SizedBox(height: AnumSpacing.xs),
                    Text(
                      l.f('biz.cat.integrations', {
                        'list': p.integrations.join(', '),
                      }),
                      style: TextStyle(color: context.palette.muted),
                    ),
                  ],
                  if (widget.canManage) ...[
                    const SizedBox(height: AnumSpacing.sm),
                    Wrap(
                      spacing: AnumSpacing.xs,
                      children: [
                        OutlinedButton.icon(
                          key: const Key('product-edit'),
                          onPressed: busy ? null : edit,
                          icon: const Icon(Icons.edit_outlined),
                          label: Text(l.t('biz.form.edit')),
                        ),
                        OutlinedButton.icon(
                          key: const Key('product-delete'),
                          onPressed: busy ? null : remove,
                          icon: const Icon(Icons.delete_outline),
                          label: Text(l.t('biz.cat.delete')),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
            AnumHeader(
              title: l.t('biz.cat.versions'),
              subtitle: widget.canManage ? null : l.t('biz.cat.ownersOnly'),
            ),
            if (widget.canManage && p.draft == null)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  key: const Key('version-add'),
                  onPressed: busy ? null : () => editVersion(),
                  icon: const Icon(Icons.add),
                  label: Text(l.t('biz.cat.addVersion')),
                ),
              ),
            if (p.offers.isEmpty)
              AnumEmpty(
                title: l.t('biz.cat.noVersions'),
                message: widget.canManage
                    ? l.t('biz.cat.noVersionsOwner')
                    : l.t('biz.cat.noVersionsText'),
              ),
            for (final offer in p.offers) _offerCard(context, offer),
          ],
        ],
      ),
    );
  }

  Widget _offerCard(BuildContext context, CatalogOffer offer) {
    final l = context.anum;
    final muted = TextStyle(color: context.palette.muted);
    return AnumSurface(
      key: Key('offer-${offer.version}'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnumRow(
            title: l.f('biz.cat.version', {'version': offer.version}),
            subtitle: _priceText(context, offer),
            trailing: AnumPill(
              label: l.t('biz.cat.offer.${offer.status}'),
              tone: offer.isActive
                  ? AnumTone.ok
                  : offer.isDraft
                      ? AnumTone.warn
                      : AnumTone.neutral,
            ),
          ),
          Text(
            l.f('biz.cat.cost', {
              'cost': formatMoney(offer.estimatedCost, offer.currency),
            }),
            style: muted,
          ),
          if (offer.marginNegative)
            Text(
              l.t('biz.cat.marginNegative'),
              key: Key('offer-${offer.version}-margin'),
              style: TextStyle(color: context.palette.stop),
            )
          else if (offer.marginAtPrice != null)
            Text(
              l.f('biz.cat.margin', {
                'margin': formatMoney(offer.marginAtPrice, offer.currency),
              }),
              style: muted,
            ),
          if (offer.deliverables.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(
              l.t('biz.cat.deliverables'),
              style: Theme.of(context).textTheme.labelLarge,
            ),
            for (final item in offer.deliverables) Text('• $item'),
          ],
          if (offer.limitations.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(
              l.t('biz.cat.limitations'),
              style: Theme.of(context).textTheme.labelLarge,
            ),
            for (final item in offer.limitations) Text('• $item'),
          ],
          if (widget.canManage && offer.status != 'retired')
            Wrap(
              spacing: AnumSpacing.xs,
              children: [
                if (offer.isDraft) ...[
                  TextButton(
                    key: Key('offer-${offer.version}-edit'),
                    onPressed: busy ? null : () => editVersion(offer),
                    child: Text(l.t('biz.form.edit')),
                  ),
                  TextButton(
                    key: Key('offer-${offer.version}-activate'),
                    onPressed: busy ? null : () => activateOffer(offer),
                    child: Text(l.t('biz.cat.activate')),
                  ),
                ],
                TextButton(
                  key: Key('offer-${offer.version}-retire'),
                  onPressed: busy ? null : () => retireOffer(offer),
                  child: Text(
                    offer.isDraft
                        ? l.t('biz.cat.discard')
                        : l.t('biz.cat.retire'),
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class ProductFormScreen extends StatefulWidget {
  const ProductFormScreen({required this.controller, this.product, super.key});
  final CatalogController controller;

  /// Null creates a product; otherwise edits it.
  final CatalogProduct? product;

  @override
  State<ProductFormScreen> createState() => _ProductFormScreenState();
}

class _ProductFormScreenState extends State<ProductFormScreen> {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.product?.name);
  late final nameAr = TextEditingController(text: widget.product?.nameAr);
  late final summary = TextEditingController(text: widget.product?.summary);
  late final integrations = TextEditingController(
    text: widget.product?.integrations.join(', '),
  );
  late String kind = widget.product?.kind ?? 'product';
  late String maturity = widget.product?.maturity ?? 'idea';
  bool busy = false;
  String? error;

  @override
  void dispose() {
    for (final field in [name, nameAr, summary, integrations]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    final fields = <String, Object?>{
      'name': name.text.trim(),
      'name_ar': nameAr.text.trim(),
      'summary': summary.text.trim(),
      'maturity': maturity,
      'integrations': [
        for (final item in integrations.text.split(','))
          if (item.trim().isNotEmpty) item.trim(),
      ],
    };
    try {
      final saved = widget.product == null
          ? await widget.controller.create({...fields, 'kind': kind})
          : await widget.controller.update(widget.product!.id, fields);
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final editing = widget.product != null;
    return DeskForm(
      formKey: form,
      title: editing ? l.t('biz.cat.editProduct') : l.t('biz.cat.new'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        if (!editing)
          deskChoice(
            key: const Key('product-kind'),
            label: l.t('biz.cat.form.kind'),
            value: kind,
            values: productKinds,
            text: (v) => l.t('biz.cat.kind.$v'),
            onChanged: (v) => setState(() => kind = v),
          ),
        TextFormField(
          key: const Key('product-name'),
          controller: name,
          maxLength: 160,
          decoration: InputDecoration(labelText: l.t('biz.cat.form.name')),
          validator: (v) => (v ?? '').trim().length < 2
              ? l.t('biz.cat.form.nameNeeded')
              : null,
        ),
        TextFormField(
          key: const Key('product-name-ar'),
          controller: nameAr,
          maxLength: 160,
          decoration: InputDecoration(labelText: l.t('biz.cat.form.nameAr')),
        ),
        TextFormField(
          key: const Key('product-summary'),
          controller: summary,
          minLines: 2,
          maxLines: 6,
          maxLength: 2000,
          decoration: InputDecoration(labelText: l.t('biz.cat.form.summary')),
        ),
        deskChoice(
          key: const Key('product-maturity'),
          label: l.t('biz.cat.form.maturity'),
          value: maturity,
          values: productMaturities,
          text: (v) => l.t('biz.cat.maturity.$v'),
          onChanged: (v) => setState(() => maturity = v),
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextFormField(
          key: const Key('product-integrations'),
          controller: integrations,
          decoration: InputDecoration(
            labelText: l.t('biz.cat.form.integrations'),
            helperText: l.t('biz.cat.form.integrationsHint'),
            helperMaxLines: 2,
          ),
        ),
      ],
    );
  }
}

class VersionFormScreen extends StatefulWidget {
  const VersionFormScreen({
    required this.controller,
    required this.productId,
    this.offer,
    super.key,
  });
  final CatalogController controller;
  final String productId;

  /// Null adds a new draft version; otherwise edits that draft.
  final CatalogOffer? offer;

  @override
  State<VersionFormScreen> createState() => _VersionFormScreenState();
}

class _VersionFormScreenState extends State<VersionFormScreen> {
  final form = GlobalKey<FormState>();
  late final CatalogOffer? o = widget.offer;
  String num_(double? v) => v == null ? '' : _plain(v);
  late final priceMin = TextEditingController(text: num_(o?.priceMin));
  late final priceMax = TextEditingController(text: num_(o?.priceMax));
  late final labor = TextEditingController(text: num_(o?.laborHours));
  late final hourly = TextEditingController(text: num_(o?.hourlyCost));
  late final external = TextEditingController(text: num_(o?.externalCosts));
  late final contingency = TextEditingController(text: num_(o?.contingencyPct));
  late final deliverables = TextEditingController(
    text: o?.deliverables.join('\n'),
  );
  late final limitations = TextEditingController(
    text: o?.limitations.join('\n'),
  );
  late final notes = TextEditingController(text: o?.notes);
  late String currency = o?.currency ?? 'SAR';
  late String priceType = o?.priceType ?? 'fixed';
  bool busy = false;
  String? error;

  static String _plain(double v) =>
      v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v';

  @override
  void dispose() {
    for (final field in [
      priceMin,
      priceMax,
      labor,
      hourly,
      external,
      contingency,
      deliverables,
      limitations,
      notes,
    ]) {
      field.dispose();
    }
    super.dispose();
  }

  String? _amount(String? value, {bool required = false}) {
    final l = context.anum;
    final text = (value ?? '').trim();
    if (text.isEmpty) return required ? l.t('biz.cat.form.amountNeeded') : null;
    final parsed = double.tryParse(text);
    return parsed == null || parsed < 0 ? l.t('biz.cat.form.amountBad') : null;
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    final quote = priceType == 'quote';
    final fields = <String, Object?>{
      'currency': currency,
      'price_type': priceType,
      'price_min': quote ? null : deskNumber(priceMin.text),
      'price_max': priceType == 'range' ? deskNumber(priceMax.text) : null,
      'deliverables': deskLines(deliverables.text),
      'limitations': deskLines(limitations.text),
      'cost': {
        'labor_hours': deskNumber(labor.text) ?? 0,
        'hourly_cost': deskNumber(hourly.text) ?? 0,
        'external_costs': deskNumber(external.text) ?? 0,
        'contingency_pct': deskNumber(contingency.text) ?? 0,
      },
      'notes': notes.text.trim(),
    };
    try {
      final saved = o == null
          ? await widget.controller.addVersion(widget.productId, fields)
          : await widget.controller.updateVersion(o!.id, fields);
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _money(
    Key key,
    TextEditingController controller,
    String label, {
    bool required = false,
  }) =>
      TextFormField(
        key: key,
        controller: controller,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        decoration: InputDecoration(labelText: label),
        validator: (v) => _amount(v, required: required),
      );

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return DeskForm(
      formKey: form,
      title: o == null ? l.t('biz.cat.addVersion') : l.t('biz.cat.editVersion'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        deskChoice(
          key: const Key('version-currency'),
          label: l.t('biz.cat.form.currency'),
          value: currency,
          values: currencies,
          text: (v) => v,
          onChanged: (v) => setState(() => currency = v),
        ),
        deskChoice(
          key: const Key('version-price-type'),
          label: l.t('biz.cat.form.priceType'),
          value: priceType,
          values: priceTypes,
          text: (v) => l.t('biz.cat.priceType.$v'),
          onChanged: (v) => setState(() => priceType = v),
        ),
        if (priceType != 'quote')
          _money(
            const Key('version-price-min'),
            priceMin,
            priceType == 'range'
                ? l.t('biz.cat.form.priceLow')
                : l.t('biz.cat.form.price'),
            required: true,
          ),
        if (priceType == 'range')
          _money(
            const Key('version-price-max'),
            priceMax,
            l.t('biz.cat.form.priceHigh'),
            required: true,
          ),
        TextFormField(
          key: const Key('version-deliverables'),
          controller: deliverables,
          minLines: 2,
          maxLines: 8,
          decoration: InputDecoration(
            labelText: l.t('biz.cat.deliverables'),
            helperText: l.t('biz.cat.form.linesHint'),
          ),
        ),
        TextFormField(
          key: const Key('version-limitations'),
          controller: limitations,
          minLines: 2,
          maxLines: 8,
          decoration: InputDecoration(
            labelText: l.t('biz.cat.limitations'),
            helperText: l.t('biz.cat.form.linesHint'),
          ),
        ),
        AnumHeader(
          title: l.t('biz.cat.form.costs'),
          subtitle: l.t('biz.cat.form.costsHint'),
        ),
        _money(const Key('version-labor'), labor, l.t('biz.cat.form.labor')),
        _money(const Key('version-hourly'), hourly, l.t('biz.cat.form.hourly')),
        _money(
          const Key('version-external'),
          external,
          l.t('biz.cat.form.external'),
        ),
        _money(
          const Key('version-contingency'),
          contingency,
          l.t('biz.cat.form.contingency'),
        ),
        TextFormField(
          key: const Key('version-notes'),
          controller: notes,
          minLines: 2,
          maxLines: 6,
          maxLength: 2000,
          decoration: InputDecoration(labelText: l.t('biz.cat.form.notes')),
        ),
      ],
    );
  }
}
