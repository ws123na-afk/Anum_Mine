import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'finance_controller.dart';
import 'finance_models.dart';
import 'finance_strings.dart';

/// Finance on the phone (docs/finance.md): the signed-in person's own month,
/// transactions, accounts, bills, debts and goals. Read only: nothing here
/// pays, transfers or trades. Statement import is on the web.
class FinanceScreen extends StatefulWidget {
  const FinanceScreen({required this.controller, super.key});
  final FinanceController controller;

  @override
  State<FinanceScreen> createState() => _FinanceScreenState();
}

class _FinanceScreenState extends State<FinanceScreen> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      widget.controller.load();
    });
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _say(String text) {
    if (!mounted) return;
    ScaffoldMessenger.maybeOf(context)
        ?.showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    final s = FinanceStrings.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        return Scaffold(
          // The AppBar's back button returns to where Finance was opened from.
          appBar: AppBar(title: Text(s.t('title'))),
          body: AnumPage(
            onRefresh: c.load,
            children: [
              AnumHeader(eyebrow: s.t('subtitle'), title: s.t('title')),
              _Sections(controller: c, strings: s),
              ..._body(context, c, s),
              Text(s.t('readOnly'),
                  style: TextStyle(color: context.palette.muted)),
              Text(s.t('importOnWeb'),
                  style: TextStyle(color: context.palette.muted)),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(
      BuildContext context, FinanceController c, FinanceStrings s) {
    if (c.summaryState == FinanceLoad.denied) {
      return [
        AnumFeedback(kind: AnumFeedbackKind.permission, message: s.t('denied'))
      ];
    }
    return switch (c.section) {
      FinanceSection.overview => _overview(context, c, s),
      FinanceSection.transactions => _transactions(context, c, s),
      FinanceSection.accounts => _accounts(context, c, s),
      FinanceSection.bills => _items(context, c, s, 'bills'),
      FinanceSection.debts => _items(context, c, s, 'debts'),
      FinanceSection.goals => _items(context, c, s, 'goals'),
    };
  }

  // -- overview -----------------------------------------------------------

  List<Widget> _overview(
      BuildContext context, FinanceController c, FinanceStrings s) {
    switch (c.summaryState) {
      case FinanceLoad.idle:
      case FinanceLoad.loading:
        if (c.summary == null) {
          return [
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: s.t('loading'))
          ];
        }
      case FinanceLoad.denied:
        return [];
      case FinanceLoad.error:
        return [
          AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('summaryFailed', {'message': c.summaryError ?? ''}),
              onRetry: c.loadSummary)
        ];
      case FinanceLoad.ready:
        break;
    }
    final d = c.summary!;
    final balance = d.pick(d.balances), spent = d.pick(d.spend);
    final income = d.pick(d.income);
    final spend = d.spendByCategory
        .where((row) => row.currency == spent.currency)
        .toList();
    final top = spend.isEmpty
        ? 0.0
        : spend
            .map((row) => double.tryParse(row.amount) ?? 0)
            .reduce((a, b) => a > b ? a : b);
    return [
      if (d.accounts.isEmpty)
        AnumEmpty(
            title: s.t('welcome'),
            message: s.t('welcomeText'),
            icon: Icons.account_balance_wallet_outlined,
            action: s.t('addAccount'),
            onAction: () => _addAccount(context, c, s)),
      AnumMetricGrid(children: [
        AnumMetric(
            label: s.t('balance'),
            value: formatAmount(balance.amount, balance.currency),
            icon: Icons.account_balance_outlined,
            caption: d.balances.length > 1 ? formatAmounts(d.balances) : null),
        AnumMetric(
            label: s.t('spent'),
            value: formatAmount(spent.amount, spent.currency),
            icon: Icons.north_east,
            caption: d.month),
        AnumMetric(
            label: s.t('income'),
            value: formatAmount(income.amount, income.currency),
            icon: Icons.south_west,
            caption: formatAmounts(d.net)),
        AnumMetric(
            label: s.t('bills30'),
            value: '${d.upcomingBills.length}',
            icon: Icons.event_outlined,
            caption:
                d.billsDue.isEmpty ? s.t('none') : formatAmounts(d.billsDue)),
        AnumMetric(
            label: s.t('debts'),
            value: d.debtCount == 0 ? '-' : formatAmounts(d.debts),
            icon: Icons.credit_card_outlined,
            caption: d.debtCount == 0
                ? s.t('none')
                : formatAmounts(d.minimumPayments)),
      ]),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('byCategory')),
          if (spend.isEmpty)
            Text(s.t('noSpending'))
          else
            for (final row in spend)
              _SpendBar(
                  label: s.t('category.${row.category}'),
                  amount: formatAmount(row.amount, row.currency),
                  share:
                      top <= 0 ? 0 : (double.tryParse(row.amount) ?? 0) / top),
        ]),
      ),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AnumHeader(title: s.t('upcoming')),
          if (d.upcomingBills.isEmpty)
            Text(s.t('noBillsDue'))
          else
            for (final bill in d.upcomingBills.take(6))
              AnumRow(
                  title: bill.name,
                  icon: Icons.event_outlined,
                  tone: bill.daysUntil <= 3 ? AnumTone.warn : null,
                  subtitle:
                      '${bill.dueOn} · ${bill.daysUntil <= 0 ? s.t('dueToday') : s.t('dueIn', {
                              'n': bill.daysUntil
                            })}',
                  detail: formatAmount(bill.amount, bill.currency)),
        ]),
      ),
      if (d.accounts.isNotEmpty)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(title: s.t('accounts')),
            for (final a in d.accounts.where((a) => !a.archived))
              AnumRow(
                  title: a.name,
                  icon: Icons.account_balance_outlined,
                  subtitle: [
                    s.t('kind.${a.kind}'),
                    if (a.last4 != null) s.t('endingIn', {'last4': a.last4!}),
                  ].join(' · '),
                  detail: formatAmount(a.balance, a.currency)),
          ]),
        ),
    ];
  }

  // -- transactions --------------------------------------------------------

  List<Widget> _transactions(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final search = TextField(
      controller: _search,
      textInputAction: TextInputAction.search,
      decoration: InputDecoration(
          labelText: s.t('search'), prefixIcon: const Icon(Icons.search)),
      onSubmitted: c.search,
    );
    final add = Align(
      alignment: AlignmentDirectional.centerStart,
      child: FilledButton.icon(
          onPressed:
              c.accounts.isEmpty ? null : () => _addTransaction(context, c, s),
          icon: const Icon(Icons.add),
          label: Text(s.t('addTransaction'))),
    );
    if (c.transactionsState == FinanceLoad.loading && c.transactions.isEmpty) {
      return [
        search,
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (c.transactionsState == FinanceLoad.error ||
        c.transactionsState == FinanceLoad.denied) {
      return [
        search,
        AnumFeedback(
            kind: c.transactionsState == FinanceLoad.denied
                ? AnumFeedbackKind.permission
                : AnumFeedbackKind.error,
            message: c.transactionsState == FinanceLoad.denied
                ? s.t('denied')
                : s.t('listFailed', {'message': c.transactionsError ?? ''}),
            onRetry: c.loadTransactions),
      ];
    }
    if (c.transactions.isEmpty) {
      return [
        search,
        add,
        c.query.isEmpty
            ? AnumEmpty(
                title: s.t('noTransactions'),
                message: s.t('noTransactionsText'),
                icon: Icons.receipt_long_outlined)
            : AnumEmpty(title: s.t('noMatch'), message: c.query),
      ];
    }
    return [
      search,
      add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final t in c.transactions)
            AnumRow(
              title: t.description,
              subtitle: '${t.postedOn} · ${s.t('category.${t.category}')}',
              detail: formatAmount(t.amount, t.currency),
              icon: t.moneyIn ? Icons.south_west : Icons.north_east,
              tone: t.moneyIn ? AnumTone.ok : null,
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${t.description}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, t.description, () => c.deleteTransaction(t.id)),
              ),
            ),
        ]),
      ),
      if (c.hasMoreTransactions)
        OutlinedButton(
          onPressed: c.loadingMore ? null : c.loadMoreTransactions,
          child: Text(s.t('more',
              {'shown': c.transactions.length, 'total': c.transactionsTotal})),
        ),
    ];
  }

  // -- accounts ------------------------------------------------------------

  List<Widget> _accounts(
      BuildContext context, FinanceController c, FinanceStrings s) {
    final add = Align(
      alignment: AlignmentDirectional.centerStart,
      child: FilledButton.icon(
          onPressed: () => _addAccount(context, c, s),
          icon: const Icon(Icons.add),
          label: Text(s.t('addAccount'))),
    );
    if (c.accountsState == FinanceLoad.loading && c.accounts.isEmpty) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (c.accountsState == FinanceLoad.error) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: s.t('listFailed', {'message': ''}),
            onRetry: c.loadAccounts)
      ];
    }
    if (c.accounts.isEmpty) {
      return [
        AnumEmpty(
            title: s.t('noAccounts'),
            message: s.t('noAccountsText'),
            icon: Icons.account_balance_outlined,
            action: s.t('addAccount'),
            onAction: () => _addAccount(context, c, s)),
      ];
    }
    return [
      add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final a in c.accounts)
            AnumRow(
              title: a.name,
              icon: Icons.account_balance_outlined,
              subtitle: [
                s.t('kind.${a.kind}'),
                a.currency,
                if (a.last4 != null) s.t('endingIn', {'last4': a.last4!}),
              ].join(' · '),
              detail: formatAmount(a.balance, a.currency),
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${a.name}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, a.name, () => c.deleteAccount(a.id)),
              ),
            ),
        ]),
      ),
    ];
  }

  // -- bills, debts, goals -------------------------------------------------

  List<Widget> _items(BuildContext context, FinanceController c,
      FinanceStrings s, String kind) {
    final state = c.itemStates[kind] ?? FinanceLoad.idle;
    final list = c.items[kind] ?? const <FinanceItem>[];
    final add = kind == 'bills'
        ? Align(
            alignment: AlignmentDirectional.centerStart,
            child: FilledButton.icon(
                onPressed: () => _addBill(context, c, s),
                icon: const Icon(Icons.add),
                label: Text(s.t('addBill'))),
          )
        : null;
    if ((state == FinanceLoad.loading || state == FinanceLoad.idle) &&
        list.isEmpty) {
      return [
        AnumFeedback(
            kind: AnumFeedbackKind.loading, message: s.t('loadingList'))
      ];
    }
    if (state == FinanceLoad.error || state == FinanceLoad.denied) {
      return [
        AnumFeedback(
            kind: state == FinanceLoad.denied
                ? AnumFeedbackKind.permission
                : AnumFeedbackKind.error,
            message: state == FinanceLoad.denied
                ? s.t('denied')
                : s.t('listFailed', {'message': c.itemsError ?? ''}),
            onRetry: () => c.loadItems(kind)),
      ];
    }
    final empty = switch (kind) {
      'bills' => (s.t('noBills'), s.t('noBillsText')),
      'debts' => (s.t('noDebts'), s.t('noDebtsText')),
      _ => (s.t('noGoals'), s.t('noGoalsText')),
    };
    if (list.isEmpty) {
      return [
        if (add != null) add,
        AnumEmpty(
            title: empty.$1,
            message: empty.$2,
            icon: Icons.event_note_outlined,
            action: kind == 'bills' ? s.t('addBill') : null,
            onAction: kind == 'bills' ? () => _addBill(context, c, s) : null),
      ];
    }
    return [
      if (add != null) add,
      AnumSurface(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
        child: Column(children: [
          for (final item in list)
            AnumRow(
              title: item.name,
              subtitle: kind == 'goals'
                  ? formatAmount(item.detail, item.currency)
                  : item.detail,
              detail: formatAmount(item.amount, item.currency),
              trailing: IconButton(
                tooltip: '${s.t('delete')} ${item.name}',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _confirmDelete(
                    context, s, item.name, () => c.deleteItem(kind, item.id)),
              ),
            ),
        ]),
      ),
    ];
  }

  // -- dialogs -------------------------------------------------------------

  Future<void> _confirmDelete(BuildContext context, FinanceStrings s,
      String name, Future<String?> Function() action) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('deleteTitle', {'name': name})),
        content: Text(s.t('deleteText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(s.t('cancel'))),
          FilledButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(s.t('delete'))),
        ],
      ),
    );
    if (ok != true) return;
    final error = await action();
    _say(error ?? s.t('deleted'));
  }

  /// One small form in a dialog: the fields, a check, then the save call.
  Future<void> _form(
    BuildContext context,
    FinanceStrings s,
    String title,
    List<(String key, String label, TextInputType type)> fields,
    String? Function(Map<String, String> values) check,
    Future<String?> Function(Map<String, String> values) save,
  ) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => _FormDialog(
          strings: s, title: title, fields: fields, check: check, save: save),
    );
    if (saved == true) _say(s.t('saved'));
  }

  Future<void> _addAccount(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addAccount'),
        [
          ('name', s.t('name'), TextInputType.text),
          ('last4', s.t('last4'), TextInputType.number),
        ],
        (v) => v['name']!.isEmpty
            ? s.t('badText')
            : (v['last4']!.isNotEmpty &&
                    !RegExp(r'^\d{4}$').hasMatch(v['last4']!))
                ? s.t('badLast4')
                : null,
        (v) => c.addAccount(name: v['name']!, kind: 'bank', last4: v['last4']),
      );

  Future<void> _addTransaction(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addTransaction'),
        [
          ('description', s.t('description'), TextInputType.text),
          (
            'amount',
            s.t('amount'),
            const TextInputType.numberWithOptions(signed: true, decimal: true)
          ),
          ('date', s.t('date'), TextInputType.datetime),
        ],
        (v) => v['description']!.isEmpty
            ? s.t('badText')
            : parseAmount(v['amount']!) == null
                ? s.t('badAmount')
                : !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(v['date']!)
                    ? s.t('badDate')
                    : null,
        (v) => c.addTransaction(
            accountId: c.accounts
                .firstWhere((a) => !a.archived, orElse: () => c.accounts.first)
                .id,
            postedOn: v['date']!,
            amount: parseAmount(v['amount']!)!,
            description: v['description']!),
      );

  Future<void> _addBill(
          BuildContext context, FinanceController c, FinanceStrings s) =>
      _form(
        context,
        s,
        s.t('addBill'),
        [
          ('name', s.t('name'), TextInputType.text),
          (
            'amount',
            s.t('amountPositive'),
            const TextInputType.numberWithOptions(decimal: true)
          ),
          ('date', s.t('date'), TextInputType.datetime),
        ],
        (v) => v['name']!.isEmpty
            ? s.t('badText')
            : parseAmount(v['amount']!, signed: false) == null
                ? s.t('badAmount')
                : !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(v['date']!)
                    ? s.t('badDate')
                    : null,
        (v) => c.addBill(
            name: v['name']!,
            amount: parseAmount(v['amount']!, signed: false)!,
            dueOn: v['date']!),
      );
}

/// A small form in a dialog that owns its text controllers, so they live
/// until the dialog has finished closing.
class _FormDialog extends StatefulWidget {
  const _FormDialog(
      {required this.strings,
      required this.title,
      required this.fields,
      required this.check,
      required this.save});
  final FinanceStrings strings;
  final String title;
  final List<(String key, String label, TextInputType type)> fields;
  final String? Function(Map<String, String> values) check;
  final Future<String?> Function(Map<String, String> values) save;

  @override
  State<_FormDialog> createState() => _FormDialogState();
}

class _FormDialogState extends State<_FormDialog> {
  late final Map<String, TextEditingController> _controllers = {
    for (final f in widget.fields) f.$1: TextEditingController()
  };
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    final values = {
      for (final e in _controllers.entries) e.key: e.value.text.trim()
    };
    final problem = widget.check(values);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() => _busy = true);
    final failed = await widget.save(values);
    if (!mounted) return;
    if (failed != null) {
      setState(() {
        _error = failed;
        _busy = false;
      });
      return;
    }
    Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    final error = _error;
    return AlertDialog(
      title: Text(widget.title),
      scrollable: true,
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        for (final f in widget.fields)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
            child: TextField(
                controller: _controllers[f.$1],
                keyboardType: f.$3,
                decoration: InputDecoration(labelText: f.$2)),
          ),
        if (error != null)
          Text(error, style: TextStyle(color: context.palette.stop)),
      ]),
      actions: [
        TextButton(
            onPressed: _busy ? null : () => Navigator.of(context).pop(false),
            child: Text(s.t('cancel'))),
        FilledButton(
            onPressed: _busy ? null : _submit, child: Text(s.t('save'))),
      ],
    );
  }
}

class _Sections extends StatelessWidget {
  const _Sections({required this.controller, required this.strings});
  final FinanceController controller;
  final FinanceStrings strings;

  @override
  Widget build(BuildContext context) => Semantics(
        label: strings.t('sections'),
        container: true,
        child: Wrap(spacing: 8, runSpacing: 8, children: [
          for (final section in FinanceSection.values)
            ChoiceChip(
              label: Text(strings.t('section.${section.name}')),
              selected: controller.section == section,
              onSelected: (_) => controller.select(section),
            ),
        ]),
      );
}

/// One category's spend as a bar scaled to the largest, with its amount.
class _SpendBar extends StatelessWidget {
  const _SpendBar(
      {required this.label, required this.amount, required this.share});
  final String label, amount;
  final double share;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Semantics(
      label: '$label $amount',
      excludeSemantics: true,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          // Wraps instead of clipping when large text leaves no room.
          Wrap(
            alignment: WrapAlignment.spaceBetween,
            spacing: AnumSpacing.xs,
            children: [
              Text(label),
              Text(amount, textDirection: TextDirection.ltr),
            ],
          ),
          const SizedBox(height: AnumSpacing.xxs),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
                value: share.clamp(0.0, 1.0),
                minHeight: 6,
                color: p.violet,
                backgroundColor: p.surface3),
          ),
        ]),
      ),
    );
  }
}
