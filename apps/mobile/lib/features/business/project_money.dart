import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'business_models.dart';
import 'invoice_models.dart' show PortalDecisionSummary;
import 'portal_access.dart' show PortalDecisionLine;

/// A project's money on the phone (docs/business.md#change-orders): the
/// profitability (revenue and cash collected kept apart), change orders (an
/// owner approves the exact change with its content hash) and the usage and
/// cost ledger. Empty figures are "-"; the API computes every amount.

String _money(double? amount, String currency) =>
    amount == null ? '-' : '$currency ${amount.toStringAsFixed(2)}';

String _signed(double? amount, String currency) {
  if (amount == null) return '-';
  final text = '$currency ${amount.abs().toStringAsFixed(2)}';
  return amount > 0 ? '+$text' : (amount < 0 ? '-$text' : text);
}

class ChangeOrder {
  const ChangeOrder({
    required this.id,
    required this.number,
    required this.title,
    required this.status,
    required this.currency,
    required this.timelineDays,
    required this.issues,
    required this.stale,
    this.totalDelta,
    this.contentHash,
    this.baseVersion,
    this.resultVersion,
    this.decisionNote = '',
    this.portalDecision,
  });

  /// What the customer decided in their portal about this change order.
  final PortalDecisionSummary? portalDecision;
  final String id, title, status, currency, decisionNote;
  final int number, timelineDays;
  final double? totalDelta;
  final String? contentHash;
  final int? baseVersion, resultVersion;
  final List<String> issues;
  final bool stale;

  factory ChangeOrder.fromJson(JsonReader r) => ChangeOrder(
        id: r.string('id'),
        number: r.integer('number'),
        title: r.string('title'),
        status: r.string('status'),
        currency: r.string('currency'),
        timelineDays: r.integer('timeline_days'),
        totalDelta: r.optNumber('total_delta'),
        contentHash: r.optString('content_hash'),
        baseVersion: r.optNumber('base_version')?.toInt(),
        resultVersion: r.optNumber('result_version')?.toInt(),
        decisionNote: r.optString('decision_note') ?? '',
        issues: r.strings('issues'),
        stale: r.boolean('stale'),
        portalDecision: PortalDecisionSummary.maybe(r),
      );
}

class Profitability {
  const Profitability({
    required this.currency,
    required this.acceptedRevenue,
    required this.changeOrdersRevenue,
    required this.revenue,
    required this.contractTotal,
    required this.notes,
    this.cashCollected,
    this.labourCost,
    this.directCosts,
    this.grossMargin,
    this.grossMarginPct,
  });
  final String currency;
  final double acceptedRevenue, changeOrdersRevenue, revenue, contractTotal;
  final double? cashCollected, labourCost, directCosts, grossMargin;
  final double? grossMarginPct;
  final List<String> notes;

  factory Profitability.fromJson(JsonReader r) => Profitability(
        currency: r.string('currency'),
        acceptedRevenue: r.number('accepted_revenue'),
        changeOrdersRevenue: r.number('change_orders_revenue'),
        revenue: r.number('revenue'),
        contractTotal: r.number('contract_total'),
        cashCollected: r.optNumber('cash_collected'),
        labourCost: r.optNumber('labour_cost'),
        directCosts: r.optNumber('direct_costs'),
        grossMargin: r.optNumber('gross_margin'),
        grossMarginPct: r.optNumber('gross_margin_pct'),
        notes: r.strings('notes'),
      );
}

class UsageEntry {
  const UsageEntry({
    required this.id,
    required this.kind,
    required this.description,
    required this.occurredOn,
    required this.amount,
    required this.currency,
    required this.billable,
  });
  final String id, kind, description, occurredOn, currency;
  final double amount;
  final bool billable;

  factory UsageEntry.fromJson(JsonReader r) => UsageEntry(
        id: r.string('id'),
        kind: r.string('kind'),
        description: r.string('description'),
        occurredOn: r.string('occurred_on'),
        amount: r.number('amount'),
        currency: r.string('currency'),
        billable: r.boolean('billable'),
      );
}

/// The project money routes. `ApiBusinessRepository` implements it.
abstract interface class ProjectMoneyRepository {
  Future<Profitability> profitability(String projectId);
  Future<BusinessPage<ChangeOrder>> changeOrders(String projectId);
  Future<ChangeOrder> createChangeOrder(
      String projectId, Map<String, Object?> body);
  Future<ChangeOrder> submitChangeOrder(String id);
  Future<ChangeOrder> approveChangeOrder(String id, String contentHash);
  Future<ChangeOrder> rejectChangeOrder(String id, String reason);
  Future<ChangeOrder> withdrawChangeOrder(String id, String reason);
  Future<BusinessPage<UsageEntry>> usage(String projectId);
  Future<UsageEntry> createUsage(Map<String, Object?> body);
}

class ApiProjectMoney implements ProjectMoneyRepository {
  ApiProjectMoney(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';
  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _call(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  @override
  Future<Profitability> profitability(String projectId) async =>
      Profitability.fromJson(
          await _call('GET', '/projects/${_id(projectId)}/profitability'));

  @override
  Future<BusinessPage<ChangeOrder>> changeOrders(String projectId) async =>
      BusinessPage.fromJson(
        await _call(
            'GET', '/projects/${_id(projectId)}/change-orders?limit=50'),
        ChangeOrder.fromJson,
      );

  @override
  Future<ChangeOrder> createChangeOrder(
          String projectId, Map<String, Object?> body) async =>
      ChangeOrder.fromJson(await _call(
          'POST', '/projects/${_id(projectId)}/change-orders', body));

  @override
  Future<ChangeOrder> submitChangeOrder(String id) async =>
      ChangeOrder.fromJson(
          await _call('POST', '/change-orders/${_id(id)}/submit'));

  @override
  Future<ChangeOrder> approveChangeOrder(String id, String contentHash) async =>
      ChangeOrder.fromJson(await _call('POST',
          '/change-orders/${_id(id)}/approve', {'content_hash': contentHash}));

  @override
  Future<ChangeOrder> rejectChangeOrder(String id, String reason) async =>
      ChangeOrder.fromJson(await _call(
          'POST', '/change-orders/${_id(id)}/reject', {'reason': reason}));

  @override
  Future<ChangeOrder> withdrawChangeOrder(String id, String reason) async =>
      ChangeOrder.fromJson(await _call(
          'POST', '/change-orders/${_id(id)}/withdraw', {'reason': reason}));

  @override
  Future<BusinessPage<UsageEntry>> usage(String projectId) async =>
      BusinessPage.fromJson(
        await _call('GET',
            '/usage?project_id=${Uri.encodeQueryComponent(projectId)}&limit=50'),
        UsageEntry.fromJson,
      );

  @override
  Future<UsageEntry> createUsage(Map<String, Object?> body) async =>
      UsageEntry.fromJson(await _call('POST', '/usage', body));
}

enum MoneyLoad { loading, ready, denied, error }

/// Change orders, ledger and profitability of one project.
class ProjectMoneyScreen extends StatefulWidget {
  const ProjectMoneyScreen({
    required this.repository,
    required this.project,
    required this.permissions,
    this.onContractChanged,
    super.key,
  });
  final ProjectMoneyRepository repository;
  final BusinessProject project;
  final BusinessPermissions permissions;
  final VoidCallback? onContractChanged;

  @override
  State<ProjectMoneyScreen> createState() => _ProjectMoneyScreenState();
}

class _ProjectMoneyScreenState extends State<ProjectMoneyScreen> {
  MoneyLoad state = MoneyLoad.loading;
  String? error;
  Profitability? profit;
  List<ChangeOrder> changes = [];
  List<UsageEntry> entries = [];
  bool busy = false;

  ProjectMoneyRepository get repo => widget.repository;
  String get projectId => widget.project.id;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      state = MoneyLoad.loading;
      error = null;
    });
    try {
      final results = await Future.wait<Object>([
        repo.profitability(projectId),
        repo.changeOrders(projectId),
        repo.usage(projectId),
      ]);
      if (!mounted) return;
      setState(() {
        profit = results[0] as Profitability;
        changes = (results[1] as BusinessPage<ChangeOrder>).items;
        entries = (results[2] as BusinessPage<UsageEntry>).items;
        state = MoneyLoad.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        state = e is ApiException && e.isPermissionDenied
            ? MoneyLoad.denied
            : MoneyLoad.error;
        error = e is ApiException ? e.message : '$e';
      });
    }
  }

  Future<void> act(Future<Object?> Function() action, String done) async {
    final messenger = ScaffoldMessenger.of(context);
    setState(() => busy = true);
    try {
      final result = await action();
      if (result is ChangeOrder && result.status == 'approved') {
        widget.onContractChanged?.call();
      }
      messenger.showSnackBar(SnackBar(content: Text(done)));
      await load();
    } on ApiException catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(e.message)));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(widget.project.name)),
      body: AnumPage(
        onRefresh: load,
        children: [
          AnumHeader(
            eyebrow: widget.project.accountName,
            title: l.t('biz.money.title'),
          ),
          ...switch (state) {
            MoneyLoad.loading => [
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('biz.loading')),
              ],
            MoneyLoad.denied => [
                AnumFeedback(
                    kind: AnumFeedbackKind.permission,
                    message: l.t('biz.denied')),
              ],
            MoneyLoad.error => [
                AnumFeedback(
                  kind: AnumFeedbackKind.error,
                  message: l.f('biz.failed', {'message': error ?? ''}),
                  onRetry: load,
                ),
              ],
            MoneyLoad.ready => [
                _profit(context),
                ..._changes(context),
                ..._ledger(context),
              ],
          },
        ],
      ),
    );
  }

  Widget _fact(BuildContext context, String label, String value) => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Row(
          children: [
            Expanded(
              child:
                  Text(label, style: TextStyle(color: context.palette.muted)),
            ),
            const SizedBox(width: 12),
            Flexible(child: Text(value, textAlign: TextAlign.end)),
          ],
        ),
      );

  Widget _profit(BuildContext context) {
    final l = context.anum;
    final p = profit!;
    return AnumSurface(
      key: const Key('biz-money-profit'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t('biz.money.profit'),
              style: Theme.of(context).textTheme.titleMedium),
          _fact(context, l.t('biz.money.accepted'),
              _money(p.acceptedRevenue, p.currency)),
          _fact(context, l.t('biz.money.changeOrders'),
              _signed(p.changeOrdersRevenue, p.currency)),
          _fact(
              context, l.t('biz.money.revenue'), _money(p.revenue, p.currency)),
          _fact(context, l.t('biz.money.contract'),
              _money(p.contractTotal, p.currency)),
          _fact(context, l.t('biz.money.cash'),
              _money(p.cashCollected, p.currency)),
          _fact(context, l.t('biz.money.labour'),
              _money(p.labourCost, p.currency)),
          _fact(context, l.t('biz.money.direct'),
              _money(p.directCosts, p.currency)),
          _fact(
            context,
            l.t('biz.money.margin'),
            p.grossMargin == null
                ? '-'
                : '${_signed(p.grossMargin, p.currency)} (${p.grossMarginPct?.toStringAsFixed(1) ?? '-'}%)',
          ),
          for (final note in p.notes)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(note,
                  style: TextStyle(
                      color: context.palette.muted, fontSize: AnumType.sm)),
            ),
        ],
      ),
    );
  }

  AnumTone _tone(String status) => switch (status) {
        'approved' => AnumTone.ok,
        'rejected' || 'withdrawn' => AnumTone.stop,
        'pending_approval' => AnumTone.warn,
        _ => AnumTone.neutral,
      };

  List<Widget> _changes(BuildContext context) {
    final l = context.anum;
    final write = widget.permissions.write;
    final decide = widget.permissions.decide;
    return [
      Wrap(
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        runSpacing: 4,
        children: [
          Text(l.t('biz.money.changesTitle'),
              style: Theme.of(context).textTheme.titleMedium),
          if (write && widget.project.status != 'closed')
            TextButton.icon(
              key: const Key('biz-change-new'),
              onPressed: busy ? null : () => _newChange(context),
              icon: const Icon(Icons.add),
              label: Text(l.t('biz.money.newChange')),
            ),
        ],
      ),
      if (changes.isEmpty)
        AnumEmpty(
          title: l.t('biz.money.noChanges'),
          message: l.t('biz.money.noChangesText'),
          icon: Icons.edit_note_outlined,
        ),
      for (final c in changes)
        AnumSurface(
          key: Key('biz-change-${c.id}'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumRow(
                title: l.f('biz.money.changeHeading',
                    {'number': c.number, 'title': c.title}),
                subtitle: l.f('biz.money.changeImpact', {
                  'price': _signed(c.totalDelta, c.currency),
                  'days': c.timelineDays,
                }),
                icon: Icons.difference_outlined,
                trailing: AnumPill(
                    label: l.t('biz.money.status.${c.status}'),
                    tone: _tone(c.status)),
              ),
              for (final issue in c.issues)
                Text(issue, style: TextStyle(color: context.palette.stop)),
              if (c.decisionNote.isNotEmpty)
                Text(c.decisionNote,
                    style: TextStyle(color: context.palette.muted)),
              if (c.portalDecision != null)
                PortalDecisionLine(decision: c.portalDecision!),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  if (write && (c.status == 'draft' || c.stale))
                    FilledButton(
                      key: Key('biz-change-submit-${c.id}'),
                      onPressed: busy
                          ? null
                          : () => act(() => repo.submitChangeOrder(c.id),
                              l.t('biz.money.sent')),
                      child: Text(l.t('biz.money.submit')),
                    ),
                  if (decide && c.status == 'pending_approval' && !c.stale)
                    FilledButton(
                      key: Key('biz-change-approve-${c.id}'),
                      onPressed: busy || c.contentHash == null
                          ? null
                          : () => act(
                              () =>
                                  repo.approveChangeOrder(c.id, c.contentHash!),
                              l.t('biz.money.approved')),
                      child: Text(l.t('biz.money.approve')),
                    ),
                  if (decide && c.status == 'pending_approval')
                    OutlinedButton(
                      onPressed: busy
                          ? null
                          : () => _reason(context, l.t('biz.money.reject'),
                              (r) => repo.rejectChangeOrder(c.id, r)),
                      child: Text(l.t('biz.money.reject')),
                    ),
                  if (write &&
                      (c.status == 'draft' || c.status == 'pending_approval'))
                    OutlinedButton(
                      onPressed: busy
                          ? null
                          : () => _reason(context, l.t('biz.money.withdraw'),
                              (r) => repo.withdrawChangeOrder(c.id, r)),
                      child: Text(l.t('biz.money.withdraw')),
                    ),
                ],
              ),
              if (!decide && c.status == 'pending_approval')
                Text(l.t('biz.money.ownerDecides'),
                    style: TextStyle(color: context.palette.muted)),
            ],
          ),
        ),
    ];
  }

  List<Widget> _ledger(BuildContext context) {
    final l = context.anum;
    return [
      Wrap(
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 8,
        runSpacing: 4,
        children: [
          Text(l.t('biz.money.ledgerTitle'),
              style: Theme.of(context).textTheme.titleMedium),
          if (widget.permissions.write)
            TextButton.icon(
              key: const Key('biz-usage-new'),
              onPressed: busy ? null : () => _newCost(context),
              icon: const Icon(Icons.add),
              label: Text(l.t('biz.money.newCost')),
            ),
        ],
      ),
      if (entries.isEmpty)
        AnumEmpty(
          title: l.t('biz.money.noCosts'),
          message: l.t('biz.money.noCostsText'),
          icon: Icons.receipt_long_outlined,
        ),
      for (final e in entries)
        AnumRow(
          title: e.description,
          subtitle:
              '${l.t('biz.money.kind.${e.kind}')} · ${e.occurredOn}${e.billable ? ' · ${l.t('biz.money.billable')}' : ''}',
          detail: _money(e.amount, e.currency),
          icon: Icons.payments_outlined,
        ),
    ];
  }

  Future<void> _reason(BuildContext context, String title,
      Future<ChangeOrder> Function(String reason) run) async {
    final l = context.anum;
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => _ReasonDialog(l: l, title: title),
    );
    if (reason == null || !context.mounted) return;
    await act(() => run(reason), l.t('biz.money.saved'));
  }

  Future<void> _newChange(BuildContext context) async {
    final l = context.anum;
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (_) => _ChangeDialog(l: l),
    );
    if (body == null) return;
    await act(() => repo.createChangeOrder(projectId, body),
        l.t('biz.money.drafted'));
  }

  Future<void> _newCost(BuildContext context) async {
    final l = context.anum;
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (_) => _CostDialog(l: l, project: widget.project),
    );
    if (body == null) return;
    await act(() => repo.createUsage(body), l.t('biz.money.costAdded'));
  }
}

/// Asks for the reason a change order is rejected or withdrawn (3 characters or more).
class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({required this.l, required this.title});
  final AnumLocalizations l;
  final String title;
  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final field = TextEditingController();
  String? problem;

  @override
  void dispose() {
    field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = widget.l;
    return AlertDialog(
      title: Text(widget.title),
      content: TextField(
        key: const Key('biz-change-reason'),
        controller: field,
        decoration: InputDecoration(
          labelText: l.t('biz.money.reason'),
          errorText: problem,
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(l.t('biz.money.cancel')),
        ),
        FilledButton(
          onPressed: () {
            final text = field.text.trim();
            if (text.length < 3) {
              setState(() => problem = l.t('biz.money.reasonRequired'));
              return;
            }
            Navigator.pop(context, text);
          },
          child: Text(widget.title),
        ),
      ],
    );
  }
}

double? _amount(String text, {bool signed = false}) {
  final clean = text.replaceAll(',', '').trim();
  if (!RegExp(signed ? r'^-?\d+(\.\d{1,2})?$' : r'^\d+(\.\d{1,2})?$')
      .hasMatch(clean)) {
    return null;
  }
  return double.parse(clean);
}

/// One line of the scope change: added to the contract, or a line of the
/// current contract removed (matched exactly by the API, as on the web).
class _LineFields {
  String action = 'add';
  String kind = 'service';
  final description = TextEditingController();
  final quantity = TextEditingController(text: '1');
  final price = TextEditingController();

  bool get blank =>
      description.text.trim().isEmpty && price.text.trim().isEmpty;

  void dispose() {
    description.dispose();
    quantity.dispose();
    price.dispose();
  }
}

const _lineKinds = ['service', 'labor', 'external_cost', 'other'];

/// The change order form: what changes, who asked, the request, any number of
/// added or removed lines, the timeline change and the internal cost change.
class _ChangeDialog extends StatefulWidget {
  const _ChangeDialog({required this.l});
  final AnumLocalizations l;
  @override
  State<_ChangeDialog> createState() => _ChangeDialogState();
}

class _ChangeDialogState extends State<_ChangeDialog> {
  final title = TextEditingController(), requestedBy = TextEditingController();
  final request = TextEditingController();
  final days = TextEditingController(text: '0');
  final cost = TextEditingController(text: '0');
  final lines = <_LineFields>[_LineFields()];
  String? problem;

  @override
  void dispose() {
    for (final c in [title, requestedBy, request, days, cost]) {
      c.dispose();
    }
    for (final line in lines) {
      line.dispose();
    }
    super.dispose();
  }

  void save() {
    final l = widget.l;
    final name = title.text.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (name.length < 2) {
      return setState(() => problem = l.t('biz.money.titleRequired'));
    }
    final body = <Map<String, Object?>>[];
    for (var i = 0; i < lines.length; i++) {
      final line = lines[i];
      if (line.blank) continue;
      final description =
          line.description.text.trim().replaceAll(RegExp(r'\s+'), ' ');
      final quantity =
          _amount(line.quantity.text.trim().isEmpty ? '1' : line.quantity.text);
      final price = _amount(line.price.text);
      if (description.isEmpty ||
          quantity == null ||
          quantity <= 0 ||
          price == null) {
        return setState(
            () => problem = l.f('biz.money.lineInvalid', {'n': i + 1}));
      }
      body.add({
        'action': line.action,
        'description': description,
        'kind': line.kind,
        'quantity': quantity,
        'unit_price': price,
      });
    }
    final shift =
        int.tryParse(days.text.trim().isEmpty ? '0' : days.text.trim());
    if (shift == null || shift.abs() > 3650) {
      return setState(() => problem = l.t('biz.money.daysInvalid'));
    }
    final costDelta =
        _amount(cost.text.trim().isEmpty ? '0' : cost.text, signed: true);
    if (costDelta == null) {
      return setState(() => problem = l.t('biz.money.costDeltaInvalid'));
    }
    if (body.isEmpty && shift == 0 && costDelta == 0) {
      return setState(() => problem = l.t('biz.money.nothing'));
    }
    Navigator.pop(context, {
      'title': name,
      'requested_by': requestedBy.text.trim(),
      'request': request.text.trim(),
      'lines': body,
      'timeline_days': shift,
      'internal_cost_delta': costDelta,
    });
  }

  Widget _line(BuildContext context, int index) {
    final l = widget.l;
    final line = lines[index];
    final n = index + 1;
    return Padding(
      key: Key('biz-change-line-$index'),
      padding: const EdgeInsets.only(top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 8,
            children: [
              SegmentedButton<String>(
                key: Key('biz-change-action-$index'),
                segments: [
                  ButtonSegment(
                      value: 'add', label: Text(l.t('biz.money.add'))),
                  ButtonSegment(
                      value: 'remove', label: Text(l.t('biz.money.remove'))),
                ],
                selected: {line.action},
                onSelectionChanged: (value) =>
                    setState(() => line.action = value.first),
              ),
              IconButton(
                key: Key('biz-change-remove-line-$index'),
                tooltip: l.f('biz.money.removeLine', {'n': n}),
                icon: const Icon(Icons.delete_outline),
                onPressed: () => setState(() {
                  lines.removeAt(index).dispose();
                }),
              ),
            ],
          ),
          TextField(
            key: Key('biz-change-item-$index'),
            controller: line.description,
            decoration:
                InputDecoration(labelText: l.f('biz.money.lineItem', {'n': n})),
          ),
          DropdownButton<String>(
            key: Key('biz-change-kind-$index'),
            isExpanded: true,
            value: line.kind,
            items: [
              for (final kind in _lineKinds)
                DropdownMenuItem(
                    value: kind, child: Text(l.t('biz.money.lineKind.$kind'))),
            ],
            onChanged: (value) =>
                setState(() => line.kind = value ?? line.kind),
          ),
          TextField(
            key: Key('biz-change-quantity-$index'),
            controller: line.quantity,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: l.t('biz.money.quantity')),
          ),
          TextField(
            key: Key('biz-change-price-$index'),
            controller: line.price,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(labelText: l.t('biz.money.unitPrice')),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l = widget.l;
    return AlertDialog(
      title: Text(l.t('biz.money.newChange')),
      content: SizedBox(
        width: 480,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                  key: const Key('biz-change-title'),
                  controller: title,
                  decoration:
                      InputDecoration(labelText: l.t('biz.money.changeWhat'))),
              TextField(
                  key: const Key('biz-change-requested-by'),
                  controller: requestedBy,
                  decoration:
                      InputDecoration(labelText: l.t('biz.money.requestedBy'))),
              TextField(
                  key: const Key('biz-change-request'),
                  controller: request,
                  maxLines: 3,
                  minLines: 1,
                  decoration:
                      InputDecoration(labelText: l.t('biz.money.request'))),
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(l.t('biz.money.scopeHint'),
                    style: TextStyle(color: context.palette.muted)),
              ),
              for (var i = 0; i < lines.length; i++) _line(context, i),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton.icon(
                  key: const Key('biz-change-add-line'),
                  onPressed: lines.length >= 40
                      ? null
                      : () => setState(() => lines.add(_LineFields())),
                  icon: const Icon(Icons.add),
                  label: Text(l.t('biz.money.addLine')),
                ),
              ),
              TextField(
                  key: const Key('biz-change-days'),
                  controller: days,
                  keyboardType:
                      const TextInputType.numberWithOptions(signed: true),
                  decoration:
                      InputDecoration(labelText: l.t('biz.money.days'))),
              TextField(
                  key: const Key('biz-change-cost'),
                  controller: cost,
                  keyboardType: const TextInputType.numberWithOptions(
                      signed: true, decimal: true),
                  decoration:
                      InputDecoration(labelText: l.t('biz.money.costDelta'))),
              if (problem != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(problem!,
                      style: TextStyle(color: context.palette.stop)),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.money.cancel'))),
        FilledButton(
            key: const Key('biz-change-save'),
            onPressed: save,
            child: Text(l.t('biz.money.saveDraft'))),
      ],
    );
  }
}

class _CostDialog extends StatefulWidget {
  const _CostDialog({required this.l, required this.project});
  final AnumLocalizations l;
  final BusinessProject project;
  @override
  State<_CostDialog> createState() => _CostDialogState();
}

class _CostDialogState extends State<_CostDialog> {
  final description = TextEditingController();
  final quantity = TextEditingController(text: '1');
  final cost = TextEditingController();
  String kind = 'labour';
  bool billable = false;
  String? problem;

  @override
  void dispose() {
    for (final c in [description, quantity, cost]) {
      c.dispose();
    }
    super.dispose();
  }

  void save() {
    final l = widget.l;
    final q = _amount(quantity.text), unit = _amount(cost.text);
    if (description.text.trim().length < 2) {
      return setState(() => problem = l.t('biz.money.descriptionRequired'));
    }
    if (q == null || q <= 0 || unit == null) {
      return setState(() => problem = l.t('biz.money.costInvalid'));
    }
    final today = DateTime.now().toIso8601String().substring(0, 10);
    Navigator.pop(context, {
      'project_id': widget.project.id,
      'kind': kind,
      'description': description.text.trim(),
      'occurred_on': today,
      'quantity': q,
      'unit_cost': unit,
      'currency': widget.project.currency,
      'billable': billable,
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = widget.l;
    final labour = kind == 'labour';
    return AlertDialog(
      title: Text(l.t('biz.money.newCost')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            DropdownButton<String>(
              key: const Key('biz-usage-kind'),
              isExpanded: true,
              value: kind,
              items: [
                for (final k in const [
                  'labour',
                  'hosting',
                  'messaging',
                  'other'
                ])
                  DropdownMenuItem(
                      value: k, child: Text(l.t('biz.money.kind.$k'))),
              ],
              onChanged: (value) => setState(() => kind = value ?? kind),
            ),
            TextField(
                key: const Key('biz-usage-description'),
                controller: description,
                decoration:
                    InputDecoration(labelText: l.t('biz.money.description'))),
            TextField(
                key: const Key('biz-usage-quantity'),
                controller: quantity,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: l
                        .t(labour ? 'biz.money.hours' : 'biz.money.quantity'))),
            TextField(
                key: const Key('biz-usage-cost'),
                controller: cost,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(
                    labelText: l.t(labour
                        ? 'biz.money.hourlyCost'
                        : 'biz.money.unitCost'))),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: billable,
              title: Text(l.t('biz.money.billable')),
              onChanged: (value) => setState(() => billable = value ?? false),
            ),
            if (problem != null)
              Text(problem!, style: TextStyle(color: context.palette.stop)),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.money.cancel'))),
        FilledButton(
            key: const Key('biz-usage-save'),
            onPressed: save,
            child: Text(l.t('biz.money.record'))),
      ],
    );
  }
}
