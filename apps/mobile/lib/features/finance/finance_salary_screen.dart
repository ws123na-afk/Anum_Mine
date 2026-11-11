import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_widgets.dart';
import 'finance_models.dart';
import 'finance_money_models.dart';
import 'finance_money_strings.dart';
import 'finance_more_data.dart';
import 'finance_more_strings.dart';

/// Finance, Salary profile on the phone (docs/finance.md#salary): the
/// employer, joining date, expected gross, currency, pay day and account,
/// with this month's expected net and the next pay day; the deductions and
/// salary advances taken off it (add, edit, remove); and deleting the
/// profile. Every save sends the whole profile (`PUT /salary/profile`).

final _month = RegExp(r'^\d{4}-(0[1-9]|1[0-2])$');

class SalaryProfileScreen extends StatefulWidget {
  const SalaryProfileScreen(
      {required this.repository, this.onChanged, super.key});
  final FinanceMoreRepository repository;

  /// Called after a save or delete, so the cards that read the salary
  /// reload.
  final VoidCallback? onChanged;

  @override
  State<SalaryProfileScreen> createState() => _SalaryProfileScreenState();
}

class _SalaryProfileScreenState extends State<SalaryProfileScreen> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  SalaryProfileData? _profile;
  List<AccountChoice> _accounts = const [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      final profile = await widget.repository.salaryProfile();
      List<AccountChoice> accounts;
      try {
        accounts = [
          for (final a in await widget.repository.accounts())
            if (a.kind == 'bank' || a.kind == 'cash') a
        ];
      } on Object {
        accounts = const [];
      }
      if (!mounted) return;
      setState(() {
        _profile = profile;
        _accounts = accounts;
        _phase = RecordsPhase.ready;
      });
    } on Object catch (error) {
      if (!mounted) return;
      final failure = RecordsFailure.from(error);
      setState(() {
        _failure = failure;
        _phase = failure.denied ? RecordsPhase.denied : RecordsPhase.error;
      });
    }
  }

  Future<void> _saved(bool saved) async {
    if (!saved || !mounted) return;
    recordsToast(context, FinanceMoneyStrings.of(context).t('sal.saved'));
    widget.onChanged?.call();
    await _load();
  }

  Future<void> _editProfile() async {
    final m = FinanceMoneyStrings.of(context);
    final p = _profile;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: p == null ? m.t('sal.add') : m.t('sal.edit'),
        intro: m.t('sal.intro'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          RecordsInput.text('employer', m.t('sal.employer'),
              required: true, initial: p?.employer, maxLength: 120),
          RecordsInput.date('joined', m.t('sal.joined'),
              required: true, initial: p?.joinedOn),
          RecordsInput.number('gross', m.t('sal.gross'),
              required: true, initial: p?.expectedGross),
          RecordsInput.text('currency', m.t('sal.currency'),
              initial: p?.currency),
          RecordsInput.text('pay_day', m.t('sal.payDay'),
              required: true, initial: p?.payDay.toString()),
          RecordsInput.choice('account', m.t('sal.account'),
              required: false,
              options: {
                '': m.t('sal.anyAccount'),
                for (final a in _accounts) a.id: '${a.name} (${a.currency})',
              },
              initial: p?.accountId ?? ''),
        ],
        onSubmit: (v) async {
          final day =
              int.tryParse(decimalText((v['pay_day'] as String?) ?? '') ?? '');
          if (day == null || day < 1 || day > 31) {
            throw FormatException(m.t('sal.badDay'));
          }
          final currency = (v['currency'] as String?)?.toUpperCase();
          if (currency != null && !RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
            throw FormatException(m.t('plan.badCurrency'));
          }
          final account = v['account'] as String?;
          await widget.repository.saveSalaryProfile({
            'employer': (v['employer'] as String).trim(),
            'joined_on': v['joined'],
            'currency': currency,
            'expected_gross': v['gross'],
            'pay_day': day,
            'account_id': account == null || account.isEmpty ? null : account,
            // Null keeps the saved deductions and advances.
            'rules': null,
          });
        },
      ),
    );
    await _saved(saved);
  }

  Future<void> _editRule(SalaryProfileData p,
      {SalaryRuleData? rule, bool advance = false}) async {
    final m = FinanceMoneyStrings.of(context);
    final isAdvance = rule?.advance ?? advance;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        title: isAdvance ? m.t('sal.addAdvance') : m.t('sal.addDeduction'),
        submitLabel: KitStrings.of(context).t('save'),
        fields: [
          if (!isAdvance)
            RecordsInput.choice('reason', m.t('sal.reason'),
                options: {
                  for (final r in salaryReasons) r: m.t('sal.reason.$r')
                },
                initial: rule?.reason ?? 'other'),
          RecordsInput.text('label', m.t('sal.label'),
              initial: rule?.label, maxLength: 80),
          if (isAdvance) ...[
            RecordsInput.number('total', '${m.t('sal.total')} (${p.currency})',
                required: true, initial: rule?.total),
            RecordsInput.date('received', m.t('sal.received'),
                initial: rule?.receivedOn),
          ],
          RecordsInput.number('amount', '${m.t('sal.amount')} (${p.currency})',
              required: true, initial: rule?.amount),
          RecordsInput.text('starts', m.t('sal.starts'),
              required: true, initial: rule?.starts),
          if (!isAdvance)
            RecordsInput.text('ends', m.t('sal.ends'), initial: rule?.ends),
        ],
        onSubmit: (v) async {
          final starts = (v['starts'] as String).trim();
          final ends = (v['ends'] as String?)?.trim();
          if (!_month.hasMatch(starts) ||
              (ends != null && !_month.hasMatch(ends))) {
            throw FormatException(m.t('sal.badMonth'));
          }
          if (ends != null && ends.compareTo(starts) < 0) {
            throw FormatException(m.t('sal.badEnds'));
          }
          final next = SalaryRuleData(
            id: rule?.id,
            kind: isAdvance ? 'advance' : 'deduction',
            reason: isAdvance ? 'advance' : (v['reason'] as String?) ?? 'other',
            label: v['label'] as String?,
            amount: v['amount'] as String,
            starts: starts,
            ends: ends,
            total: v['total'] as String?,
            receivedOn: v['received'] as String?,
          );
          final rules = [
            for (final r in p.rules)
              if (rule != null && identical(r, rule)) next else r,
            if (rule == null) next,
          ];
          await widget.repository.saveSalaryProfile(p.body(rules: rules));
        },
      ),
    );
    await _saved(saved);
  }

  Future<void> _removeRule(SalaryProfileData p, SalaryRuleData rule) async {
    final m = FinanceMoneyStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('sal.removeRule.title'),
        message: m.t('sal.removeRule.text'),
        action: m.t('sal.removeRule'))) {
      return;
    }
    try {
      await widget.repository.saveSalaryProfile(p.body(rules: [
        for (final r in p.rules)
          if (!identical(r, rule)) r
      ]));
      await _saved(true);
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  Future<void> _delete() async {
    final m = FinanceMoneyStrings.of(context);
    if (!await confirmRecords(context,
        title: m.t('sal.delete.title'),
        message: m.t('sal.delete.text'),
        action: m.t('sal.delete'))) {
      return;
    }
    try {
      await widget.repository.deleteSalaryProfile();
      if (!mounted) return;
      recordsToast(context, m.t('sal.deleted'));
      widget.onChanged?.call();
      await _load();
    } on Object catch (error) {
      if (mounted) recordsToast(context, RecordsFailure.from(error).message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = FinanceMoneyStrings.of(context);
    final p = _profile;
    final muted = TextStyle(color: context.palette.muted);
    return Scaffold(
      appBar: AppBar(title: Text(m.t('sal.title'))),
      body: AnumPage(onRefresh: _load, children: [
        Text(m.t('sal.intro'), style: muted),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(
              phase: _phase,
              failure: _failure,
              deniedHint: FinanceMoreStrings.of(context).t('denied'),
              onRetry: _load)
        else if (p == null)
          AnumEmpty(
              icon: Icons.work_outline,
              title: m.t('sal.empty'),
              message: m.t('sal.emptyText'),
              action: m.t('sal.add'),
              onAction: _editProfile)
        else
          ..._body(context, p),
      ]),
    );
  }

  List<Widget> _body(BuildContext context, SalaryProfileData p) {
    final m = FinanceMoneyStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    String money(String v) => formatAmount(v, p.currency);
    final account = _accounts.where((a) => a.id == p.accountId).firstOrNull;
    return [
      Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
        FilledButton.icon(
          key: const Key('sal-edit'),
          onPressed: _editProfile,
          icon: const Icon(Icons.edit_outlined),
          label: Text(m.t('sal.edit')),
        ),
        OutlinedButton.icon(
          key: const Key('sal-delete'),
          onPressed: _delete,
          icon: const Icon(Icons.delete_outline),
          label: Text(m.t('sal.delete')),
        ),
      ]),
      RecordsSection(title: p.employer, children: [
        RecordsField(m.t('sal.gross'), money(p.expectedGross)),
        RecordsField(m.t('sal.net'), money(p.expectedNet)),
        RecordsField(m.t('sal.next'), p.nextPayOn),
        RecordsField(m.t('sal.payDay'), '${p.payDay}'),
        RecordsField(m.t('sal.joined'), p.joinedOn),
        RecordsField(m.t('sal.account'),
            account == null ? m.t('sal.anyAccount') : account.name),
      ]),
      AnumHeader(title: m.t('sal.rules')),
      Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.sm, children: [
        OutlinedButton.icon(
          key: const Key('sal-add-deduction'),
          onPressed: () => _editRule(p),
          icon: const Icon(Icons.remove_circle_outline),
          label: Text(m.t('sal.addDeduction')),
        ),
        OutlinedButton.icon(
          key: const Key('sal-add-advance'),
          onPressed: () => _editRule(p, advance: true),
          icon: const Icon(Icons.savings_outlined),
          label: Text(m.t('sal.addAdvance')),
        ),
      ]),
      if (p.rules.isEmpty)
        Text(m.t('sal.rulesNone'), style: muted)
      else
        AnumSurface(
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
          child: Column(children: [
            for (final (i, rule) in p.rules.indexed)
              RecordsRowAction(
                row: AnumRow(
                  key: Key('sal-rule-$i'),
                  icon: rule.advance
                      ? Icons.savings_outlined
                      : Icons.remove_circle_outline,
                  tone: rule.advance ? AnumTone.warn : null,
                  title: rule.label?.isNotEmpty == true
                      ? rule.label!
                      : m.t('sal.reason.${rule.reason}'),
                  subtitle: rule.advance
                      ? m.t('sal.rule.advance', {
                          'total': money(rule.total ?? '0'),
                          'amount': money(rule.amount),
                          'starts': rule.starts,
                        })
                      : m.t('sal.rule.deduction', {
                            'amount': money(rule.amount),
                            'starts': rule.starts,
                          }) +
                          (rule.ends == null
                              ? ''
                              : m.t('sal.rule.until', {'ends': rule.ends!})),
                  detail: rule.advance && p.advancesLeft[rule.id] != null
                      ? m.t('sal.rule.left',
                          {'amount': money(p.advancesLeft[rule.id]!)})
                      : null,
                ),
                action: Wrap(spacing: AnumSpacing.xs, children: [
                  TextButton(
                      key: Key('sal-rule-edit-$i'),
                      onPressed: () => _editRule(p, rule: rule),
                      child: Text(m.t('sal.editRule'))),
                  TextButton(
                      key: Key('sal-rule-remove-$i'),
                      onPressed: () => _removeRule(p, rule),
                      child: Text(m.t('sal.removeRule'))),
                ]),
              ),
          ]),
        ),
    ];
  }
}
