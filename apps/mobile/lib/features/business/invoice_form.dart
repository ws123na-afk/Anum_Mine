import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';
import 'invoice_controller.dart';
import 'invoice_models.dart';
import 'invoice_repository.dart';
import 'invoices.dart' show friendly, money, parseNumber, plainNumber;

/// The new-invoice and edit-draft forms (docs/business.md#where-lines-come-from).
/// The phone only picks what to bill and types what a person writes; the
/// server works out every net, tax and total and refuses what cannot be
/// billed (already invoiced, paid, wrong currency).

/// One manual line being typed.
class LineDraft {
  LineDraft({
    String description = '',
    String quantity = '1',
    String price = '',
    String tax = '',
    this.kind = 'service',
  })  : description = TextEditingController(text: description),
        quantity = TextEditingController(text: quantity),
        price = TextEditingController(text: price),
        tax = TextEditingController(text: tax);

  factory LineDraft.from(InvoiceLine line) => LineDraft(
        description: line.description,
        quantity: plainNumber(line.quantity),
        price: plainNumber(line.unitPrice),
        tax: plainNumber(line.taxRatePct),
        kind: line.kind,
      );

  final TextEditingController description, quantity, price, tax;
  String kind;

  bool get blank =>
      description.text.trim().isEmpty && price.text.trim().isEmpty;

  void dispose() {
    for (final t in [description, quantity, price, tax]) {
      t.dispose();
    }
  }

  /// The wire line, or a problem key (`desc`, `qty`, `price`, `tax`).
  (Map<String, Object?>?, String?) toJson() {
    final desc = description.text.trim();
    if (desc.isEmpty) return (null, 'desc');
    final qty = parseNumber(quantity.text);
    if (qty == null || qty <= 0) return (null, 'qty');
    final unit = parseNumber(price.text);
    if (unit == null || unit < 0) return (null, 'price');
    final rate = tax.text.trim().isEmpty ? null : parseNumber(tax.text);
    if (tax.text.trim().isNotEmpty && (rate == null || rate < 0 || rate > 100)) {
      return (null, 'tax');
    }
    return (
      {
        'description': desc,
        'kind': kind,
        'quantity': qty,
        'unit_price': unit,
        if (rate != null) 'tax_rate_pct': rate,
      },
      null
    );
  }
}

/// The text details shared by both forms.
class DetailsDraft {
  DetailsDraft({Invoice? from})
      : dueDays = TextEditingController(
            text: from == null ? '' : '${from.dueInDays}'),
        reference = TextEditingController(text: from?.customerReference),
        notes = TextEditingController(text: from?.notes),
        terms = TextEditingController(text: from?.paymentTerms),
        billName = TextEditingController(text: from?.billToName),
        billAddress = TextEditingController(text: from?.billToAddress),
        billEmail = TextEditingController(text: from?.billToEmail),
        billTax = TextEditingController(text: from?.billToTaxNumber);

  final TextEditingController dueDays,
      reference,
      notes,
      terms,
      billName,
      billAddress,
      billEmail,
      billTax;

  void dispose() {
    for (final t in [
      dueDays,
      reference,
      notes,
      terms,
      billName,
      billAddress,
      billEmail,
      billTax
    ]) {
      t.dispose();
    }
  }

  /// Null when the days are fine (or blank), else true for "invalid".
  bool get daysInvalid {
    final t = dueDays.text.trim();
    if (t.isEmpty) return false;
    final n = int.tryParse(t);
    return n == null || n < 0 || n > 365;
  }

  /// The fields as the API takes them. [all] sends empty values too (an edit
  /// can clear a field); a create sends only what was typed.
  Map<String, Object?> toBody({required bool all}) {
    final days = int.tryParse(dueDays.text.trim());
    final values = <String, String>{
      'customer_reference': reference.text.trim(),
      'notes': notes.text.trim(),
      'payment_terms': terms.text.trim(),
      'bill_to_name': billName.text.trim(),
      'bill_to_address': billAddress.text.trim(),
      'bill_to_email': billEmail.text.trim(),
      'bill_to_tax_number': billTax.text.trim(),
    };
    return {
      if (days != null) 'due_in_days': days,
      for (final e in values.entries)
        if (all || e.value.isNotEmpty) e.key: e.value,
    };
  }
}

Widget _gap([double h = 12]) => SizedBox(height: h);

/// Fields for one manual line.
class _LineEditor extends StatelessWidget {
  const _LineEditor({
    required this.index,
    required this.draft,
    required this.onChanged,
    required this.onRemove,
    this.problem,
  });
  final int index;
  final LineDraft draft;
  final VoidCallback onChanged, onRemove;
  final String? problem;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    String? err(String key) => problem == key ? l.t('biz.inv.line.$key') : null;
    return AnumSurface(
      key: Key('inv-line-$index'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(
              child: Text(l.f('biz.inv.line.heading', {'n': index + 1}),
                  style: Theme.of(context).textTheme.titleSmall),
            ),
            IconButton(
              key: Key('inv-line-remove-$index'),
              tooltip: l.t('biz.inv.line.remove'),
              onPressed: onRemove,
              icon: const Icon(Icons.delete_outline),
            ),
          ]),
          TextField(
            key: Key('inv-line-desc-$index'),
            controller: draft.description,
            maxLength: 300,
            onChanged: (_) => onChanged(),
            decoration: InputDecoration(
                labelText: l.t('biz.inv.line.desc'), errorText: err('desc')),
          ),
          DropdownButtonFormField<String>(
            key: Key('inv-line-kind-$index'),
            initialValue: draft.kind,
            isExpanded: true,
            decoration: InputDecoration(labelText: l.t('biz.inv.line.kind')),
            items: [
              for (final k in lineKinds)
                DropdownMenuItem(value: k, child: Text(l.t('biz.inv.kind.$k'))),
            ],
            onChanged: (v) {
              draft.kind = v ?? draft.kind;
              onChanged();
            },
          ),
          _gap(),
          TextField(
            key: Key('inv-line-qty-$index'),
            controller: draft.quantity,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
                labelText: l.t('biz.inv.line.qty'), errorText: err('qty')),
          ),
          _gap(),
          TextField(
            key: Key('inv-line-price-$index'),
            controller: draft.price,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
                labelText: l.t('biz.inv.line.price'), errorText: err('price')),
          ),
          _gap(),
          TextField(
            key: Key('inv-line-tax-$index'),
            controller: draft.tax,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
                labelText: l.t('biz.inv.line.tax'),
                helperText: l.t('biz.inv.line.taxHint'),
                errorText: err('tax')),
          ),
        ],
      ),
    );
  }
}

/// The fields of [DetailsDraft] as a block.
class _DetailsFields extends StatelessWidget {
  const _DetailsFields({required this.draft, required this.daysError});
  final DetailsDraft draft;
  final bool daysError;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          key: const Key('inv-due-days'),
          controller: draft.dueDays,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
              labelText: l.t('biz.inv.dueDays'),
              helperText: l.t('biz.inv.dueDaysHint'),
              errorText: daysError ? l.t('biz.inv.profile.daysInvalid') : null),
        ),
        _gap(),
        TextField(
          key: const Key('inv-reference'),
          controller: draft.reference,
          maxLength: 120,
          decoration: InputDecoration(labelText: l.t('biz.inv.customerRef')),
        ),
        TextField(
          key: const Key('inv-bill-name'),
          controller: draft.billName,
          maxLength: 200,
          decoration: InputDecoration(
              labelText: l.t('biz.inv.billName'),
              helperText: l.t('biz.inv.billHint')),
        ),
        TextField(
          key: const Key('inv-bill-address'),
          controller: draft.billAddress,
          maxLength: 1000,
          minLines: 1,
          maxLines: 3,
          decoration: InputDecoration(labelText: l.t('biz.inv.billAddress')),
        ),
        TextField(
          key: const Key('inv-bill-email'),
          controller: draft.billEmail,
          maxLength: 254,
          keyboardType: TextInputType.emailAddress,
          decoration: InputDecoration(labelText: l.t('biz.inv.billEmail')),
        ),
        TextField(
          key: const Key('inv-bill-tax'),
          controller: draft.billTax,
          maxLength: 60,
          decoration: InputDecoration(labelText: l.t('biz.inv.billTax')),
        ),
        TextField(
          key: const Key('inv-notes'),
          controller: draft.notes,
          maxLength: 1000,
          minLines: 1,
          maxLines: 4,
          decoration: InputDecoration(labelText: l.t('biz.inv.notes')),
        ),
        TextField(
          key: const Key('inv-terms'),
          controller: draft.terms,
          maxLength: 1000,
          minLines: 1,
          maxLines: 4,
          decoration: InputDecoration(labelText: l.t('biz.inv.terms')),
        ),
      ],
    );
  }
}

/// Search and pick a client account (any size of workspace).
class AccountPickerDialog extends StatefulWidget {
  const AccountPickerDialog({required this.repository, super.key});
  final InvoicesRepository repository;

  @override
  State<AccountPickerDialog> createState() => _AccountPickerState();
}

class _AccountPickerState extends State<AccountPickerDialog> {
  final query = TextEditingController();
  final items = <ClientAccount>[];
  int total = 0;
  bool loading = true, more = false;
  String? error;

  @override
  void initState() {
    super.initState();
    _load(0);
  }

  @override
  void dispose() {
    query.dispose();
    super.dispose();
  }

  Future<void> _load(int offset) async {
    setState(() {
      if (offset == 0) loading = true;
      more = offset > 0;
      error = null;
    });
    try {
      final page = await widget.repository
          .accounts(query: query.text.trim(), offset: offset, limit: 20);
      if (!mounted) return;
      setState(() {
        if (offset == 0) items.clear();
        items.addAll(page.items);
        total = page.total;
      });
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) {
        setState(() {
          loading = false;
          more = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('biz.inv.new.pickAccount')),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              key: const Key('inv-account-search'),
              controller: query,
              autofocus: true,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                  labelText: l.t('biz.clients.search'),
                  prefixIcon: const Icon(Icons.search)),
              onSubmitted: (_) => _load(0),
            ),
            const SizedBox(height: 8),
            Flexible(
              child: loading
                  ? const Center(child: CircularProgressIndicator())
                  : error != null
                      ? AnumFeedback(
                          kind: AnumFeedbackKind.error,
                          message: l.f('biz.failed', {'message': error!}),
                          onRetry: () => _load(0))
                      : items.isEmpty
                          ? Padding(
                              padding: const EdgeInsets.all(16),
                              child: Text(l.t('biz.clients.noMatch')))
                          : ListView(shrinkWrap: true, children: [
                              for (final a in items)
                                ListTile(
                                  key: Key('inv-account-${a.id}'),
                                  title: Text(a.name),
                                  subtitle: a.city.isEmpty && a.country.isEmpty
                                      ? null
                                      : Text([a.city, a.country]
                                          .where((e) => e.isNotEmpty)
                                          .join(', ')),
                                  onTap: () => Navigator.pop(context, a),
                                ),
                              if (items.length < total)
                                TextButton(
                                  onPressed: more
                                      ? null
                                      : () => _load(items.length),
                                  child: Text(l.f('biz.more', {
                                    'shown': items.length,
                                    'total': total
                                  })),
                                ),
                            ]),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
      ],
    );
  }
}

/// A new invoice: pick a project or a client account, then what to bill.
class NewInvoiceScreen extends StatefulWidget {
  const NewInvoiceScreen({required this.controller, super.key});
  final InvoicesController controller;

  @override
  State<NewInvoiceScreen> createState() => _NewInvoiceState();
}

class _NewInvoiceState extends State<NewInvoiceScreen> {
  InvoicesRepository get repo => widget.controller.repository;

  BusinessLoad projectsState = BusinessLoad.loading;
  String? projectsError;
  List<ProjectChoice> projects = const [];

  ProjectChoice? project;
  ClientAccount? account;
  String currency = 'SAR';

  Billable? billable;
  BusinessLoad billableState = BusinessLoad.idle;
  String? billableError;

  final milestones = <String>{};
  final usage = <String>{};
  final contractAmount = TextEditingController();
  final markup = TextEditingController(text: '0');
  final offerQty = TextEditingController(text: '1');
  final offerPrice = TextEditingController();
  final taxRate = TextEditingController();
  String? offerId;
  final lines = <LineDraft>[];
  final details = DetailsDraft();
  final problems = <String, String>{};
  int? lineProblem;
  String? lineProblemKey;
  bool busy = false;
  String? error;

  @override
  void initState() {
    super.initState();
    _loadProjects();
  }

  @override
  void dispose() {
    contractAmount.dispose();
    markup.dispose();
    offerQty.dispose();
    offerPrice.dispose();
    taxRate.dispose();
    details.dispose();
    for (final d in lines) {
      d.dispose();
    }
    super.dispose();
  }

  Future<void> _loadProjects() async {
    setState(() {
      projectsState = BusinessLoad.loading;
      projectsError = null;
    });
    try {
      final next = await repo.projects();
      if (mounted) {
        setState(() {
          projects = next;
          projectsState = BusinessLoad.ready;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          projectsState = InvoicesController.stateOf(e);
          projectsError = friendly(e);
        });
      }
    }
  }

  Future<void> _loadBillable() async {
    final accountId = project?.accountId ?? account?.id;
    if (accountId == null) return;
    setState(() {
      billableState = BusinessLoad.loading;
      billableError = null;
      billable = null;
      milestones.clear();
      usage.clear();
      offerId = null;
    });
    try {
      final next =
          await repo.billable(accountId: accountId, projectId: project?.id);
      if (!mounted) return;
      setState(() {
        billable = next;
        billableState = BusinessLoad.ready;
        taxRate.text = plainNumber(next.taxRatePct);
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          billableState = InvoicesController.stateOf(e);
          billableError = friendly(e);
        });
      }
    }
  }

  Future<void> _pickAccount() async {
    final picked = await showDialog<ClientAccount>(
      context: context,
      builder: (_) => AccountPickerDialog(repository: repo),
    );
    if (picked == null) return;
    setState(() {
      account = picked;
      project = null;
    });
    await _loadBillable();
  }

  Future<void> _submit() async {
    final l = context.anum;
    final b = billable;
    if (b == null) return;
    problems.clear();
    lineProblem = null;
    lineProblemKey = null;

    final body = <String, Object?>{};
    if (project != null) {
      body['project_id'] = project!.id;
    } else {
      body['account_id'] = account!.id;
      body['currency'] = currency;
    }
    if (milestones.isNotEmpty) body['milestone_ids'] = milestones.toList();
    if (b.contract != null && contractAmount.text.trim().isNotEmpty) {
      final v = parseNumber(contractAmount.text);
      if (v == null || v <= 0 || v > b.contract!.remaining + 0.004) {
        problems['contract'] = l.f('biz.inv.new.contractInvalid', {
          'remaining': money(context, b.contract!.remaining, b.currency)
        });
      } else {
        body['contract_amount'] = v;
      }
    }
    if (usage.isNotEmpty) {
      body['usage_ids'] = usage.toList();
      final pct = parseNumber(markup.text);
      if (pct == null || pct < 0 || pct > 100) {
        problems['markup'] = l.t('biz.inv.new.markupInvalid');
      } else {
        body['usage_markup_pct'] = pct;
      }
    }
    if (offerId != null) {
      final offer = b.offers.firstWhere((o) => o.id == offerId);
      final qty = parseNumber(offerQty.text);
      final price = parseNumber(offerPrice.text);
      if (qty == null || qty <= 0) {
        problems['offerQty'] = l.t('biz.inv.line.qty');
      }
      if (offer.price == null && (price == null || price < 0)) {
        problems['offerPrice'] = l.t('biz.inv.new.offerPriceNeeded');
      }
      body['offer_id'] = offerId;
      if (qty != null) body['offer_quantity'] = qty;
      if (price != null) body['offer_unit_price'] = price;
    }
    final manual = <Map<String, Object?>>[];
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].blank) continue;
      final (json, problem) = lines[i].toJson();
      if (problem != null) {
        lineProblem = i;
        lineProblemKey = problem;
        break;
      }
      manual.add(json!);
    }
    if (manual.isNotEmpty) body['lines'] = manual;
    final rate = parseNumber(taxRate.text);
    if (taxRate.text.trim().isNotEmpty && (rate == null || rate < 0 || rate > 100)) {
      problems['tax'] = l.t('biz.inv.profile.rateInvalid');
    } else if (rate != null && (rate - b.taxRatePct).abs() > 0.0001) {
      body['tax_rate_pct'] = rate;
    }
    final daysBad = details.daysInvalid;
    body.addAll(details.toBody(all: false));

    final nothing = !body.containsKey('milestone_ids') &&
        !body.containsKey('contract_amount') &&
        !body.containsKey('usage_ids') &&
        !body.containsKey('offer_id') &&
        manual.isEmpty;
    setState(() {
      error = nothing && problems.isEmpty && lineProblem == null && !daysBad
          ? l.t('biz.inv.new.nothing')
          : null;
    });
    if (error != null || problems.isNotEmpty || lineProblem != null || daysBad) {
      setState(() {});
      return;
    }
    setState(() => busy = true);
    try {
      final created = await repo.create(body);
      if (mounted) Navigator.of(context).pop(created);
    } catch (e) {
      if (mounted) {
        setState(() {
          error = friendly(e);
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final b = billable;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.inv.new'))),
      body: AnumPage(
        children: [
          Text(l.t('biz.inv.new.intro'),
              style: TextStyle(color: context.palette.muted)),
          ..._target(context),
          if (billableState == BusinessLoad.loading)
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
          if (billableState == BusinessLoad.denied ||
              billableState == BusinessLoad.error)
            AnumFeedback(
              kind: billableState == BusinessLoad.denied
                  ? AnumFeedbackKind.permission
                  : AnumFeedbackKind.error,
              message: billableError ?? '',
              onRetry:
                  billableState == BusinessLoad.error ? _loadBillable : null,
            ),
          if (b != null) ..._sources(context, b),
          if (b != null) ..._rest(context, b),
        ],
      ),
    );
  }

  List<Widget> _target(BuildContext context) {
    final l = context.anum;
    return switch (projectsState) {
      BusinessLoad.idle || BusinessLoad.loading => [
          AnumFeedback(
              kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
        ],
      BusinessLoad.denied => [
          AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: projectsError ?? l.t('biz.denied')),
        ],
      BusinessLoad.error => [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: l.f('biz.failed', {'message': projectsError ?? ''}),
              onRetry: _loadProjects),
        ],
      BusinessLoad.ready => [
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AnumHeader(title: l.t('biz.inv.new.who')),
                DropdownButtonFormField<String?>(
                  key: const Key('inv-new-project'),
                  initialValue: project?.id,
                  isExpanded: true,
                  decoration: InputDecoration(
                      labelText: l.t('biz.inv.new.project'),
                      helperText: projects.isEmpty
                          ? l.t('biz.inv.new.noProjects')
                          : null),
                  items: [
                    DropdownMenuItem<String?>(
                        value: null, child: Text(l.t('biz.inv.new.noProject'))),
                    for (final p in projects)
                      DropdownMenuItem<String?>(
                        value: p.id,
                        child: Text('${p.name} · ${p.accountName}',
                            overflow: TextOverflow.ellipsis),
                      ),
                  ],
                  onChanged: (id) async {
                    final chosen =
                        projects.where((p) => p.id == id).firstOrNull;
                    setState(() {
                      project = chosen;
                      if (chosen != null) {
                        account = null;
                        currency = chosen.currency;
                      }
                      if (chosen == null && account == null) billable = null;
                    });
                    if (chosen != null || account != null) {
                      await _loadBillable();
                    }
                  },
                ),
                if (project == null) ...[
                  _gap(),
                  OutlinedButton.icon(
                    key: const Key('inv-new-account'),
                    onPressed: _pickAccount,
                    icon: const Icon(Icons.apartment_outlined),
                    label: Text(account?.name ?? l.t('biz.inv.new.pickAccount')),
                  ),
                  _gap(),
                  DropdownButtonFormField<String>(
                    key: const Key('inv-new-currency'),
                    initialValue: currency,
                    isExpanded: true,
                    decoration:
                        InputDecoration(labelText: l.t('biz.inv.currency')),
                    items: const [
                      DropdownMenuItem(value: 'SAR', child: Text('SAR')),
                      DropdownMenuItem(value: 'AED', child: Text('AED')),
                      DropdownMenuItem(value: 'USD', child: Text('USD')),
                    ],
                    onChanged: (v) => setState(() => currency = v ?? currency),
                  ),
                ] else
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                        l.f('biz.inv.new.projectCurrency',
                            {'currency': project!.currency}),
                        style: TextStyle(color: context.palette.muted)),
                  ),
              ],
            ),
          ),
        ],
    };
  }

  List<Widget> _sources(BuildContext context, Billable b) {
    final l = context.anum;
    String m(double v) => money(context, v, b.currency);
    return [
      if (b.projectId != null)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.new.milestones')),
              if (b.milestones.isEmpty && b.contract == null)
                Text(l.t('biz.inv.new.noMilestones'),
                    style: TextStyle(color: context.palette.muted)),
              for (final x in b.milestones)
                CheckboxListTile(
                  key: Key('inv-milestone-${x.id}'),
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: milestones.contains(x.id),
                  onChanged: x.billable
                      ? (on) => setState(() => on == true
                          ? milestones.add(x.id)
                          : milestones.remove(x.id))
                      : null,
                  title: Text(x.changeOrderNumber == null
                      ? x.title
                      : l.f('biz.inv.new.changeOrderMilestone', {
                          'title': x.title,
                          'number': x.changeOrderNumber!
                        })),
                  subtitle: Text(x.billable
                      ? '${m(x.amount)}${x.dueOn == null ? '' : ', ${l.t('biz.inv.doc.due')} ${dayOf(x.dueOn)}'}'
                      : '${m(x.amount)}. ${x.reason}'),
                ),
              if (b.contract != null) ...[
                Text(
                    l.f('biz.inv.new.contractInfo', {
                      'total': m(b.contract!.contractTotal),
                      'invoiced': m(b.contract!.invoiced),
                      'remaining': m(b.contract!.remaining),
                    }),
                    style: TextStyle(color: context.palette.muted)),
                _gap(),
                TextField(
                  key: const Key('inv-contract-amount'),
                  controller: contractAmount,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  decoration: InputDecoration(
                    labelText: l.t('biz.inv.new.contractAmount'),
                    helperText: l.t('biz.inv.new.contractHint'),
                    errorText: problems['contract'],
                  ),
                ),
              ],
            ],
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.inv.new.usage')),
            if (b.usage.isEmpty)
              Text(l.t('biz.inv.new.noUsage'),
                  style: TextStyle(color: context.palette.muted)),
            for (final u in b.usage)
              CheckboxListTile(
                key: Key('inv-usage-${u.id}'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: usage.contains(u.id),
                onChanged: u.billable
                    ? (on) => setState(() =>
                        on == true ? usage.add(u.id) : usage.remove(u.id))
                    : null,
                title: Text(u.description),
                subtitle: Text(u.billable
                    ? '${u.occurredOn}, ${plainNumber(u.quantity)} x ${money(context, u.unitCost, u.currency)}'
                    : '${u.occurredOn}. ${u.reason}'),
              ),
            if (usage.isNotEmpty) ...[
              _gap(),
              TextField(
                key: const Key('inv-markup'),
                controller: markup,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: l.t('biz.inv.new.markup'),
                    helperText: l.t('biz.inv.new.markupHint'),
                    errorText: problems['markup']),
              ),
            ],
          ],
        ),
      ),
      if (b.projectId == null)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.new.offers')),
              if (b.offers.isEmpty)
                Text(l.t('biz.inv.new.noOffers'),
                    style: TextStyle(color: context.palette.muted))
              else ...[
                DropdownButtonFormField<String?>(
                  key: const Key('inv-offer'),
                  initialValue: offerId,
                  isExpanded: true,
                  decoration:
                      InputDecoration(labelText: l.t('biz.inv.new.offer')),
                  items: [
                    DropdownMenuItem<String?>(
                        value: null, child: Text(l.t('biz.inv.new.noOffer'))),
                    for (final o in b.offers)
                      DropdownMenuItem<String?>(
                        value: o.id,
                        child: Text(
                            o.price == null
                                ? o.label
                                : '${o.label} · ${money(context, o.price, o.currency)}',
                            overflow: TextOverflow.ellipsis),
                      ),
                  ],
                  onChanged: (v) => setState(() => offerId = v),
                ),
                if (offerId != null) ...[
                  _gap(),
                  TextField(
                    key: const Key('inv-offer-qty'),
                    controller: offerQty,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(
                        labelText: l.t('biz.inv.line.qty'),
                        errorText: problems['offerQty']),
                  ),
                  _gap(),
                  TextField(
                    key: const Key('inv-offer-price'),
                    controller: offerPrice,
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    decoration: InputDecoration(
                        labelText: l.t('biz.inv.line.price'),
                        helperText: b.offers
                                    .firstWhere((o) => o.id == offerId)
                                    .price ==
                                null
                            ? l.t('biz.inv.new.offerPriceNeeded')
                            : l.t('biz.inv.new.offerPriceOptional'),
                        errorText: problems['offerPrice']),
                  ),
                ],
              ],
            ],
          ),
        ),
    ];
  }

  List<Widget> _rest(BuildContext context, Billable b) {
    final l = context.anum;
    return [
      AnumHeader(title: l.t('biz.inv.new.manual')),
      for (var i = 0; i < lines.length; i++)
        _LineEditor(
          index: i,
          draft: lines[i],
          problem: lineProblem == i ? lineProblemKey : null,
          onChanged: () => setState(() {}),
          onRemove: () => setState(() => lines.removeAt(i).dispose()),
        ),
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: OutlinedButton.icon(
          key: const Key('inv-add-line'),
          onPressed: lines.length >= 60
              ? null
              : () => setState(() => lines.add(LineDraft())),
          icon: const Icon(Icons.add),
          label: Text(l.t('biz.inv.new.addLine')),
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.inv.new.details')),
            TextField(
              key: const Key('inv-tax-rate'),
              controller: taxRate,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                  labelText: l.t('biz.inv.new.taxRate'),
                  helperText: l.t('biz.inv.new.taxRateHint'),
                  errorText: problems['tax']),
            ),
            _gap(),
            _DetailsFields(draft: details, daysError: details.daysInvalid),
          ],
        ),
      ),
      if (error != null)
        Text(error!,
            key: const Key('inv-new-error'),
            style: TextStyle(color: context.palette.stop)),
      Align(
        alignment: AlignmentDirectional.centerStart,
        child: FilledButton(
          key: const Key('inv-create'),
          onPressed: busy ? null : _submit,
          child: Text(l.t('biz.inv.new.create')),
        ),
      ),
    ];
  }
}

/// Edit a draft (or one waiting for approval, which lapses the request):
/// keep or drop the lines that came from the contract, the ledger or an offer,
/// edit the lines a person wrote, and change the details.
class EditInvoiceScreen extends StatefulWidget {
  const EditInvoiceScreen(
      {required this.controller, required this.invoice, super.key});
  final InvoicesController controller;
  final Invoice invoice;

  @override
  State<EditInvoiceScreen> createState() => _EditInvoiceState();
}

class _EditInvoiceState extends State<EditInvoiceScreen> {
  late final DetailsDraft details = DetailsDraft(from: widget.invoice);
  late final List<(int, InvoiceLine)> sourced = [
    for (var i = 0; i < widget.invoice.lines.length; i++)
      if (!widget.invoice.lines[i].manual) (i, widget.invoice.lines[i]),
  ];
  late final Set<int> drop = {};
  late final List<LineDraft> manual = [
    for (final line in widget.invoice.lines)
      if (line.manual) LineDraft.from(line),
  ];
  bool busy = false;
  String? error;
  int? lineProblem;
  String? lineProblemKey;
  bool nameProblem = false;

  @override
  void dispose() {
    details.dispose();
    for (final d in manual) {
      d.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final l = context.anum;
    lineProblem = null;
    lineProblemKey = null;
    final typed = <Map<String, Object?>>[];
    for (var i = 0; i < manual.length; i++) {
      if (manual[i].blank) continue;
      final (json, problem) = manual[i].toJson();
      if (problem != null) {
        lineProblem = i;
        lineProblemKey = problem;
        break;
      }
      typed.add(json!);
    }
    final kept = sourced.length - drop.length;
    nameProblem = details.billName.text.trim().isEmpty;
    String? message;
    if (kept + typed.length == 0) message = l.t('biz.inv.edit.noLines');
    setState(() => error = message);
    if (lineProblem != null ||
        nameProblem ||
        details.daysInvalid ||
        message != null) {
      setState(() {});
      return;
    }
    setState(() => busy = true);
    try {
      final next = await widget.controller.repository.update(
        widget.invoice.id,
        {
          ...details.toBody(all: true),
          'remove_lines': drop.toList()..sort(),
          'lines': typed,
        },
      );
      if (mounted) Navigator.of(context).pop(next);
    } catch (e) {
      if (mounted) {
        setState(() {
          error = friendly(e);
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final inv = widget.invoice;
    String m(double v) => money(context, v, inv.currency);
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.inv.edit'))),
      body: AnumPage(
        children: [
          if (inv.status == 'pending_approval')
            Text(l.t('biz.inv.edit.lapses'),
                key: const Key('inv-edit-lapses'),
                style: TextStyle(color: context.palette.warn)),
          if (sourced.isNotEmpty)
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: l.t('biz.inv.edit.sourced')),
                  Text(l.t('biz.inv.edit.sourcedHint'),
                      style: TextStyle(color: context.palette.muted)),
                  for (final (index, line) in sourced)
                    CheckboxListTile(
                      key: Key('inv-keep-$index'),
                      contentPadding: EdgeInsets.zero,
                      controlAffinity: ListTileControlAffinity.leading,
                      value: !drop.contains(index),
                      onChanged: (on) => setState(() =>
                          on == true ? drop.remove(index) : drop.add(index)),
                      title: Text(line.description),
                      subtitle: Text(
                          '${l.t('biz.inv.source.${line.source}')}, ${m(line.total)}'),
                    ),
                ],
              ),
            ),
          AnumHeader(title: l.t('biz.inv.new.manual')),
          for (var i = 0; i < manual.length; i++)
            _LineEditor(
              index: i,
              draft: manual[i],
              problem: lineProblem == i ? lineProblemKey : null,
              onChanged: () => setState(() {}),
              onRemove: () => setState(() => manual.removeAt(i).dispose()),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('inv-add-line'),
              onPressed: manual.length >= 60
                  ? null
                  : () => setState(() => manual.add(LineDraft())),
              icon: const Icon(Icons.add),
              label: Text(l.t('biz.inv.new.addLine')),
            ),
          ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                AnumHeader(title: l.t('biz.inv.new.details')),
                _DetailsFields(draft: details, daysError: details.daysInvalid),
                if (nameProblem)
                  Text(l.t('biz.inv.edit.nameNeeded'),
                      key: const Key('inv-name-problem'),
                      style: TextStyle(color: context.palette.stop)),
              ],
            ),
          ),
          if (error != null)
            Text(error!,
                key: const Key('inv-edit-error'),
                style: TextStyle(color: context.palette.stop)),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton(
              key: const Key('inv-edit-save'),
              onPressed: busy ? null : _save,
              child: Text(l.t('biz.inv.edit.save')),
            ),
          ),
        ],
      ),
    );
  }
}
