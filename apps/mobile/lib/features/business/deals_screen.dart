import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'business_controller.dart';
import 'business_models.dart';
import 'catalog_data.dart';
import 'deals_data.dart';
import 'desk_widgets.dart';

/// Create and edit leads and opportunities (docs/business.md). The forms only
/// collect and send; the server decides what is allowed and says why not.

const _newCompany = '__new__';
const _none = '';

String? _blank(String text) => text.trim().isEmpty ? null : text.trim();

class LeadFormScreen extends StatefulWidget {
  const LeadFormScreen({required this.controller, this.lead, super.key});
  final BusinessController controller;

  /// Null creates a lead; otherwise edits it.
  final BusinessLead? lead;

  @override
  State<LeadFormScreen> createState() => _LeadFormScreenState();
}

class _LeadFormScreenState extends State<LeadFormScreen> {
  final form = GlobalKey<FormState>();
  late final title = TextEditingController(text: widget.lead?.title);
  late final nextStep = TextEditingController(text: widget.lead?.nextStep);
  final companyName = TextEditingController();
  final companyWebsite = TextEditingController();
  late String account = widget.lead?.accountId ?? _newCompany;
  String product = _none;
  String contact = _none;
  List<(String, String)> accounts = [];
  List<(String, String)> products = [];
  List<(String, String)> contacts = [];
  bool busy = false;
  String? error;

  bool get editing => widget.lead != null;
  BusinessController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    _loadChoices();
  }

  @override
  void dispose() {
    for (final field in [title, nextStep, companyName, companyWebsite]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> _loadChoices() async {
    try {
      if (c.accounts != null && !editing) await _findAccounts(null);
      if (c.catalog != null) {
        final page = await c.catalog!.repository.products(
          offset: 0,
          limit: 100,
        );
        products = [
          for (final p in page.items)
            if (p.maturity != 'retired') (p.id, p.name),
        ];
        final wanted = widget.lead?.productId;
        if (wanted != null) {
          // Keep a retired or far-down offer selectable under its own name.
          if (!products.any((p) => p.$1 == wanted)) {
            products.add((wanted, widget.lead?.productName ?? wanted));
          }
          product = wanted;
        }
      }
      await _loadContacts();
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    }
    if (mounted) setState(() {});
  }

  /// The company choices matching [text] (the first 50 by name); searching
  /// reaches companies beyond that. The chosen company stays selectable.
  Future<void> _findAccounts(String? text) async {
    final page = await c.accounts!.repository.accounts(
      query: text,
      offset: 0,
      limit: 50,
    );
    final kept = accounts.where((a) => a.$1 == account).toList();
    accounts = [
      ...kept,
      for (final a in page.items)
        if (a.id != account) (a.id, a.name),
    ];
  }

  Future<void> _search(String text) async {
    try {
      await _findAccounts(text.trim().isEmpty ? null : text.trim());
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    }
    if (mounted) setState(() {});
  }

  Future<void> _loadContacts() async {
    contacts = [];
    final id = editing ? widget.lead!.accountId : account;
    if (c.accounts == null || id == null || id == _newCompany) return;
    final detail = await c.accounts!.repository.account(id);
    contacts = [for (final p in detail.contacts) (p.id, p.name)];
    final wanted = widget.lead?.contactId;
    if (editing && wanted != null && contact == _none) {
      if (!contacts.any((p) => p.$1 == wanted) && mounted) {
        contacts.add((wanted, context.anum.t('biz.deal.lead.currentContact')));
      }
      contact = wanted;
    } else if (!contacts.any((p) => p.$1 == contact)) {
      contact = _none;
    }
    if (mounted) setState(() {});
  }

  Future<void> save() async {
    if (!(form.currentState?.validate() ?? false)) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final LeadDetail saved;
      if (editing) {
        saved = await c.updateLead(widget.lead!.id, {
          'title': title.text.trim(),
          // Only what the person changed: an offer or contact that did not
          // load into the list is never cleared by an unrelated edit.
          if (product != (widget.lead!.productId ?? _none))
            'product_id': product == _none ? null : product,
          if (contact != (widget.lead!.contactId ?? _none))
            'contact_id': contact == _none ? null : contact,
          'next_step': nextStep.text.trim(),
        });
      } else {
        saved = await c.createLead({
          'title': title.text.trim(),
          if (account == _newCompany)
            'account': {
              'name': companyName.text.trim(),
              'website': _blank(companyWebsite.text),
            }
          else
            'account_id': account,
          if (account != _newCompany && contact != _none) 'contact_id': contact,
          if (product != _none) 'product_id': product,
          'next_step': nextStep.text.trim(),
        });
      }
      if (mounted) Navigator.of(context).pop(saved);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  List<DropdownMenuItem<String>> _items(
          List<(String, String)> list, List<DropdownMenuItem<String>> first) =>
      [
        ...first,
        for (final item in list)
          DropdownMenuItem(value: item.$1, child: Text(item.$2)),
      ];

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final newCompany = !editing && account == _newCompany;
    return DeskForm(
      formKey: form,
      title: editing ? l.t('biz.deal.lead.edit') : l.t('biz.deal.lead.new'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        TextFormField(
          key: const Key('lead-title'),
          controller: title,
          maxLength: 200,
          decoration: InputDecoration(labelText: l.t('biz.deal.lead.title')),
          validator: (v) => (v ?? '').trim().length < 2
              ? l.t('biz.deal.lead.titleNeeded')
              : null,
        ),
        if (!editing && c.accounts != null)
          TextField(
            key: const Key('lead-account-search'),
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              labelText: l.t('biz.deal.lead.findCompany'),
              prefixIcon: const Icon(Icons.search),
            ),
            onSubmitted: _search,
          ),
        if (!editing)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: DropdownButtonFormField<String>(
              key: const Key('lead-account'),
              isExpanded: true,
              initialValue: account,
              decoration:
                  InputDecoration(labelText: l.t('biz.deal.lead.company')),
              items: _items(accounts, [
                DropdownMenuItem(
                  value: _newCompany,
                  child: Text(l.t('biz.deal.lead.newCompany')),
                ),
              ]),
              onChanged: (v) {
                if (v == null) return;
                setState(() => account = v);
                _loadContacts();
              },
            ),
          ),
        if (newCompany) ...[
          TextFormField(
            key: const Key('lead-company-name'),
            controller: companyName,
            maxLength: 200,
            decoration: InputDecoration(labelText: l.t('biz.acct.form.name')),
            validator: (v) => (v ?? '').trim().length < 2
                ? l.t('biz.acct.form.nameNeeded')
                : null,
          ),
          TextFormField(
            key: const Key('lead-company-website'),
            controller: companyWebsite,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: l.t('biz.acct.form.website'),
              helperText: l.t('biz.acct.form.websiteHint'),
            ),
          ),
        ],
        if (!newCompany && c.accounts != null)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: DropdownButtonFormField<String>(
              key: const Key('lead-contact'),
              isExpanded: true,
              initialValue: contact,
              decoration:
                  InputDecoration(labelText: l.t('biz.deal.lead.contact')),
              items: _items(contacts, [
                DropdownMenuItem(
                  value: _none,
                  child: Text(l.t('biz.deal.none')),
                ),
              ]),
              onChanged: (v) => setState(() => contact = v ?? _none),
            ),
          ),
        if (c.catalog != null)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: DropdownButtonFormField<String>(
              key: const Key('lead-product'),
              isExpanded: true,
              initialValue: product,
              decoration:
                  InputDecoration(labelText: l.t('biz.deal.lead.product')),
              items: _items(products, [
                DropdownMenuItem(
                  value: _none,
                  child: Text(l.t('biz.deal.none')),
                ),
              ]),
              onChanged: (v) => setState(() => product = v ?? _none),
            ),
          ),
        TextFormField(
          key: const Key('lead-next-step'),
          controller: nextStep,
          maxLength: 300,
          decoration: InputDecoration(labelText: l.t('biz.deal.nextStep')),
        ),
      ],
    );
  }
}

class OpportunityFormScreen extends StatefulWidget {
  const OpportunityFormScreen({
    required this.controller,
    this.lead,
    this.opportunity,
    super.key,
  });
  final BusinessController controller;

  /// The qualified lead a new opportunity is opened from.
  final BusinessLead? lead;

  /// Set to edit an existing opportunity instead.
  final BusinessOpportunity? opportunity;

  @override
  State<OpportunityFormScreen> createState() => _OpportunityFormScreenState();
}

class _OpportunityFormScreenState extends State<OpportunityFormScreen> {
  final form = GlobalKey<FormState>();
  late final BusinessOpportunity? o = widget.opportunity;
  String _num(double? v) => v == null
      ? ''
      : v == v.roundToDouble()
          ? v.toStringAsFixed(0)
          : '$v';
  late final name = TextEditingController(text: o?.name ?? widget.lead?.title);
  late final value = TextEditingController(text: _num(o?.value));
  late final probability = TextEditingController(text: _num(o?.probability));
  late final note = TextEditingController(text: o?.assumptionNote);
  late final nextStep = TextEditingController(text: o?.nextStep);
  late final decision = TextEditingController(text: o?.expectedDecisionOn);
  late String currency = o?.currency ?? 'SAR';
  bool busy = false;
  String? error;

  BusinessController get c => widget.controller;

  @override
  void dispose() {
    for (final field in [name, value, probability, note, nextStep, decision]) {
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
      'currency': currency,
      'value_amount': deskNumber(value.text),
      'probability_pct': deskNumber(probability.text),
      'assumption_note': note.text.trim(),
      'next_step': nextStep.text.trim(),
      'expected_decision_on': _blank(decision.text),
    };
    try {
      final saved = o == null
          ? await c.createOpportunity({...fields, 'lead_id': widget.lead!.id})
          : await c.updateOpportunity(o!.id, fields);
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
    return DeskForm(
      formKey: form,
      title: o == null ? l.t('biz.deal.opp.new') : l.t('biz.deal.opp.edit'),
      busy: busy,
      error: error,
      onSave: save,
      children: [
        Text(
          l.t('biz.deal.opp.assumptions'),
          style: TextStyle(color: context.palette.muted),
        ),
        TextFormField(
          key: const Key('opp-name'),
          controller: name,
          maxLength: 200,
          decoration: InputDecoration(labelText: l.t('biz.deal.opp.name')),
          validator: (v) => (v ?? '').trim().length < 2
              ? l.t('biz.deal.opp.nameNeeded')
              : null,
        ),
        deskChoice(
          key: const Key('opp-currency'),
          label: l.t('biz.cat.form.currency'),
          value: currency,
          values: currencies,
          text: (v) => v,
          onChanged: (v) => setState(() => currency = v),
        ),
        TextFormField(
          key: const Key('opp-value'),
          controller: value,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: InputDecoration(labelText: l.t('biz.deal.opp.value')),
          validator: (v) {
            final text = (v ?? '').trim();
            if (text.isEmpty) return null;
            final n = double.tryParse(text);
            return n == null || n < 0 ? l.t('biz.cat.form.amountBad') : null;
          },
        ),
        TextFormField(
          key: const Key('opp-probability'),
          controller: probability,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration:
              InputDecoration(labelText: l.t('biz.deal.opp.probability')),
          validator: (v) {
            final text = (v ?? '').trim();
            if (text.isEmpty) return null;
            final n = double.tryParse(text);
            return n == null || n < 0 || n > 100
                ? l.t('biz.deal.opp.probabilityBad')
                : null;
          },
        ),
        TextFormField(
          key: const Key('opp-note'),
          controller: note,
          maxLength: 500,
          decoration: InputDecoration(labelText: l.t('biz.deal.opp.note')),
        ),
        TextFormField(
          key: const Key('opp-next-step'),
          controller: nextStep,
          maxLength: 300,
          decoration: InputDecoration(labelText: l.t('biz.deal.nextStep')),
        ),
        TextFormField(
          key: const Key('opp-decision'),
          controller: decision,
          keyboardType: TextInputType.datetime,
          decoration: InputDecoration(
            labelText: l.t('biz.deal.opp.decision'),
            helperText: l.t('biz.deal.opp.decisionHint'),
          ),
          validator: (v) {
            final text = (v ?? '').trim();
            if (text.isEmpty) return null;
            return RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text) &&
                    DateTime.tryParse(text) != null
                ? null
                : l.t('biz.deal.opp.decisionBad');
          },
        ),
      ],
    );
  }
}

/// One opportunity: its assumptions, a stage move and the edit form.
class OpportunityScreen extends StatefulWidget {
  const OpportunityScreen({
    required this.controller,
    required this.opportunity,
    super.key,
  });
  final BusinessController controller;
  final BusinessOpportunity opportunity;

  @override
  State<OpportunityScreen> createState() => _OpportunityScreenState();
}

class _OpportunityScreenState extends State<OpportunityScreen> {
  late BusinessOpportunity item = widget.opportunity;
  bool busy = false;
  String? error;

  BusinessController get c => widget.controller;
  bool get canWrite => c.setup?.permissions.write ?? false;

  Future<void> edit() async {
    final next = await Navigator.of(context).push<BusinessOpportunity>(
      MaterialPageRoute(
        builder: (_) => OpportunityFormScreen(controller: c, opportunity: item),
      ),
    );
    if (next != null && mounted) setState(() => item = next);
  }

  Future<void> move(String to) async {
    final l = context.anum;
    var reason = '';
    if (to == 'lost') {
      final given = await askReason(
        context,
        title: l.f('biz.deal.opp.moveTo', {'stage': l.t('biz.stage.lost')}),
        label: l.t('biz.lead.reason'),
      );
      if (given == null) return;
      reason = given;
    }
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final next = await c.moveStage(item.id, to, reason);
      if (mounted) setState(() => item = next);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final won = item.stage == 'won';
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(item.name)),
      body: AnumPage(
        children: [
          if (error != null)
            Semantics(
              liveRegion: true,
              child: Text(
                error!,
                key: const Key('opp-error'),
                style: TextStyle(color: context.palette.stop),
              ),
            ),
          AnumSurface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AnumHeader(
                  eyebrow: l.t('biz.stage.${item.stage}'),
                  title: item.name,
                  subtitle: item.accountName,
                ),
                Text(
                  l.f('biz.pipeline.assumed', {
                    'value': formatMoney(item.value, item.currency),
                    'probability': item.probability == null
                        ? l.t('biz.unknown')
                        : '${item.probability!.toStringAsFixed(0)}%',
                  }),
                ),
                if (item.assumptionNote.isNotEmpty)
                  Text(item.assumptionNote, style: muted),
                if (item.nextStep.isNotEmpty)
                  Text(l.f('biz.deal.opp.next', {'step': item.nextStep}),
                      style: muted),
                if (item.expectedDecisionOn != null)
                  Text(
                    l.f('biz.deal.opp.decisionOn',
                        {'date': item.expectedDecisionOn!}),
                    style: muted,
                  ),
                if (item.stage == 'lost' && item.lostReason.isNotEmpty)
                  Text(l.f('biz.deal.opp.lostWhy', {'reason': item.lostReason}),
                      style: muted),
                if (won)
                  Text(l.t('biz.deal.opp.wonFinal'),
                      key: const Key('opp-won'), style: muted),
                if (canWrite && !won) ...[
                  const SizedBox(height: AnumSpacing.sm),
                  OutlinedButton.icon(
                    key: const Key('opp-edit'),
                    onPressed: busy ? null : edit,
                    icon: const Icon(Icons.edit_outlined),
                    label: Text(l.t('biz.form.edit')),
                  ),
                ],
              ],
            ),
          ),
          if (canWrite && !won) ...[
            AnumHeader(title: l.t('biz.deal.opp.moveHeader')),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  for (final stage
                      in opportunityStages.where((s) => s != item.stage))
                    OutlinedButton(
                      key: Key('opp-move-$stage'),
                      onPressed: busy ? null : () => move(stage),
                      child: Text(l.t('biz.stage.$stage')),
                    ),
                ]),
            Text(l.t('biz.deal.opp.wonHint'), style: muted),
          ],
        ],
      ),
    );
  }
}
