import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/payment_picker.dart';
import '../records_kit/records_form.dart';
import '../records_kit/records_widgets.dart';
import 'finance_lending_strings.dart';
import 'finance_personal_controller.dart';
import 'finance_personal_models.dart';
import 'finance_personal_strings.dart';
import 'finance_review_models.dart';

/// Loan schedules with interest, repayment suggestions and expected
/// one-off income on the phone (docs/finance.md#lent-and-borrowed,
/// #forecast). Every figure stays in its own currency; nothing is added
/// across currencies, and nothing here moves money.

String _money(BuildContext context, String amount, String currency) =>
    formatMoneyText(amount, currency, locale: Localizations.localeOf(context));

/// Trims a decimal's trailing zeros for display ("5.0000" reads "5").
String _rate(String value) {
  if (!value.contains('.')) return value;
  final trimmed = value.replaceFirst(RegExp(r'0+$'), '');
  return trimmed.endsWith('.')
      ? trimmed.substring(0, trimmed.length - 1)
      : trimmed;
}

AnumTone _installmentTone(InstallmentState state) => switch (state) {
      InstallmentState.repaid => AnumTone.ok,
      InstallmentState.partRepaid => AnumTone.warn,
      InstallmentState.overdue => AnumTone.stop,
      InstallmentState.due => AnumTone.neutral,
    };

// -- the loan form -----------------------------------------------------------

/// Adds an entry, or edits [loan]: who owes whom, the amount and currency,
/// the dates, an optional yearly rate and an optional monthly schedule, the
/// reminder and (on an edit) closed without full repayment. The API checks
/// everything again and its refusal is shown under the fields.
Future<bool> openLoanForm(BuildContext context, FinancePersonalController c,
    {PersonLoan? loan}) async {
  final s = FinancePersonalStrings.of(context);
  final l = FinanceLendingStrings.of(context);
  final editing = loan != null;
  final saved = await openRecordsForm(
    context,
    RecordsForm(
      key: const Key('loan-form'),
      title: editing
          ? l.t('loan.editTitle', {'person': loan.person})
          : s.t('people.addTitle'),
      intro: editing ? l.t('loan.currencyLocked') : s.t('people.addIntro'),
      submitLabel: s.t('people.save'),
      fields: [
        RecordsInput.choice('direction', s.t('people.direction'),
            options: {
              'lent': s.t('people.dirLent'),
              'borrowed': s.t('people.dirBorrowed'),
            },
            initial: loan?.direction.name ?? 'lent'),
        RecordsInput.text('person', s.t('people.person'),
            required: true, maxLength: 120, initial: loan?.person),
        RecordsInput.number('amount', s.t('people.amount'),
            required: true, initial: loan?.amount),
        RecordsInput.text('currency', s.t('people.currency'),
            help: s.t('people.currencyHelp'),
            maxLength: 3,
            initial: loan?.currency),
        RecordsInput.date('lent_on', s.t('people.date'),
            required: true, initial: loan?.lentOn ?? c.todayIso),
        RecordsInput.date('due_on', s.t('people.dueDate'),
            initial: loan?.installments == null ? loan?.dueOn : null),
        RecordsInput.number('interest_rate', l.t('loan.rate'),
            help: l.t('loan.rateHelp'),
            initial:
                loan?.interestRate == null ? null : _rate(loan!.interestRate!)),
        RecordsInput.number('installments', l.t('loan.installments'),
            help: l.t('loan.installmentsHelp'),
            initial: loan?.installments?.toString()),
        RecordsInput.date('first_due_on', l.t('loan.firstDue'),
            help: l.t('loan.firstDueHelp'), initial: loan?.firstDueOn),
        RecordsInput.toggle('remind', l.t('loan.remind'),
            initial: loan?.remind ?? true),
        RecordsInput.number('remind_days_before', l.t('loan.remindDays'),
            initial: '${loan?.remindDaysBefore ?? 3}'),
        if (editing)
          RecordsInput.toggle('closed', l.t('loan.closed'),
              initial: loan.closedOn != null),
        RecordsInput.multiline('notes', s.t('people.notes'),
            maxLength: 1000, initial: loan?.notes),
      ],
      onSubmit: (v) async {
        final input = loanInputFrom(v, s, l, editing: editing);
        if (editing) {
          await c.updateLoan(loan.id, input);
        } else {
          await c.addLoan(input);
        }
      },
    ),
  );
  if (saved && context.mounted) {
    recordsToast(
        context,
        editing
            ? l.t('loan.saved', {'person': loan.person})
            : s.t('people.saved'));
  }
  return saved;
}

/// Checks the form's values the way the API does and builds the body; a
/// problem is thrown as a [FormatException] the form shows.
PersonLoanInput loanInputFrom(
    Map<String, Object?> v, FinancePersonalStrings s, FinanceLendingStrings l,
    {required bool editing}) {
  final amount = v['amount'] as String?;
  if (amount == null || (double.tryParse(amount) ?? 0) <= 0) {
    throw FormatException(s.t('people.badAmount'));
  }
  final currency = (v['currency'] as String?)?.toUpperCase();
  if (currency != null && !RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
    throw FormatException(s.t('people.badCurrency'));
  }
  final rate = v['interest_rate'] as String?;
  if (rate != null) {
    final value = double.tryParse(rate);
    if (value == null || value <= 0 || value > 100) {
      throw FormatException(l.t('loan.badRate'));
    }
  }
  final count = v['installments'] as String?;
  int? installments;
  if (count != null) {
    installments = int.tryParse(count);
    if (installments == null || installments < 1 || installments > 360) {
      throw FormatException(l.t('loan.badInstallments'));
    }
  }
  final firstDue = v['first_due_on'] as String?;
  if (installments != null && firstDue == null) {
    throw FormatException(l.t('loan.needFirstDue'));
  }
  if (installments == null && firstDue != null) {
    throw FormatException(l.t('loan.firstDueWithout'));
  }
  final dueOn = v['due_on'] as String?;
  if (rate != null && installments == null && dueOn == null) {
    throw FormatException(l.t('loan.rateNeedsDue'));
  }
  final daysText = v['remind_days_before'] as String?;
  final days = daysText == null ? 3 : int.tryParse(daysText);
  if (days == null || days < 0 || days > 30) {
    throw FormatException(l.t('loan.badDays'));
  }
  return PersonLoanInput(
    direction: v['direction'] == 'borrowed'
        ? LoanDirection.borrowed
        : LoanDirection.lent,
    person: v['person'] as String,
    amount: amount,
    currency: currency,
    lentOn: v['lent_on'] as String,
    dueOn: dueOn,
    notes: v['notes'] as String?,
    remind: v['remind'] as bool? ?? true,
    remindDaysBefore: days,
    closed: editing ? (v['closed'] as bool? ?? false) : null,
    interestRate: rate,
    installments: installments,
    firstDueOn: firstDue,
  );
}

// -- the loan's schedule -------------------------------------------------------

/// Opens the schedule and figures of [loan] in a sheet.
Future<void> openLoanDetail(
        BuildContext context, FinancePersonalController c, PersonLoan loan) =>
    showRecordsSheet<void>(
        context, (_) => LoanDetailSheet(controller: c, loan: loan));

/// One loan: amount, repaid, left, interest, the next repayment, what is
/// overdue, and the schedule (each repayment's date, principal, interest,
/// amount and whether it is repaid), with Edit.
class LoanDetailSheet extends StatefulWidget {
  const LoanDetailSheet(
      {required this.controller, required this.loan, super.key});
  final FinancePersonalController controller;
  final PersonLoan loan;

  @override
  State<LoanDetailSheet> createState() => _LoanDetailSheetState();
}

class _LoanDetailSheetState extends State<LoanDetailSheet> {
  static const _firstRows = 12;
  MoneyLoad _state = MoneyLoad.loading;
  PersonLoanDetail? _detail;
  String? _error;
  bool _all = false, _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _state = MoneyLoad.loading;
      _error = null;
    });
    try {
      final detail = await widget.controller.repository.loan(widget.loan.id);
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _state = MoneyLoad.ready;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _state = FinancePersonalController.failedState(error);
        _error = FinancePersonalController.reason(error);
      });
    }
  }

  Future<void> _edit() async {
    final loan = _detail?.loan ?? widget.loan;
    final saved = await openLoanForm(context, widget.controller, loan: loan);
    if (saved && mounted) await _load();
  }

  Future<bool> _confirm(String title, String body, String confirm) async =>
      await showDialog<bool>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(FinanceLendingStrings.of(dialog).t('loan.cancel')),
            ),
            FilledButton(
              key: const Key('loan-confirm'),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(confirm),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> _run(Future<void> Function() action, String done) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted) recordsToast(context, done);
    } catch (error) {
      if (mounted) {
        recordsToast(context, FinancePersonalController.reason(error));
      }
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _unlink(LoanMovement movement) async {
    final l = FinanceLendingStrings.of(context);
    if (!await _confirm(
        l.t('loan.unlinkTitle'), l.t('loan.unlinkBody'), l.t('loan.unlink'))) {
      return;
    }
    final loan = _detail?.loan ?? widget.loan;
    await _run(() => widget.controller.unlinkLoan(loan, movement.linkId),
        l.t('loan.unlinked'));
    if (mounted) await _load();
  }

  Future<void> _linkHanded() async {
    final s = FinancePersonalStrings.of(context);
    final l = FinanceLendingStrings.of(context);
    final c = widget.controller;
    final loan = _detail?.loan ?? widget.loan;
    final linked = await showRecordsSheet<bool>(
      context,
      (_) => PaymentPickerSheet(
        key: const Key('loan-handed-picker'),
        text: PaymentPickerText(
          title: l.t('loan.handedTitle', {'person': loan.person}),
          help: l.t(loan.direction == LoanDirection.lent
              ? 'loan.handedHelpOut'
              : 'loan.handedHelpIn'),
          search: s.t('repay.search'),
          empty: s.t('repay.empty'),
          remaining: s.t('repay.remaining'),
          already: s.t('repay.already'),
        ),
        load: (query) async => [
          for (final item
              in await c.repository.handedCandidates(loan, query: query))
            PaymentChoice(
              id: item.canonicalId,
              postedOn: item.postedOn,
              merchant: item.merchant,
              amount: item.amount,
              currency: item.currency,
              remaining: item.remaining,
              alreadyLinked: false,
            ),
        ],
        link: (choice, key) =>
            c.recordHanded(loan, canonicalId: choice.id, idempotencyKey: key),
      ),
    );
    if ((linked ?? false) && mounted) {
      recordsToast(context, l.t('loan.handedDone'));
      await _load();
    }
  }

  Future<void> _delete() async {
    final l = FinanceLendingStrings.of(context);
    final loan = _detail?.loan ?? widget.loan;
    if (!await _confirm(l.t('loan.deleteTitle', {'person': loan.person}),
        l.t('loan.deleteBody'), l.t('loan.delete'))) {
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.controller.deleteLoan(loan);
      if (!mounted) return;
      final messenger = ScaffoldMessenger.maybeOf(context);
      Navigator.of(context).pop();
      messenger
        ?..removeCurrentSnackBar()
        ..showSnackBar(SnackBar(
            content: Text(l.t('loan.deleted', {'person': loan.person}))));
    } catch (error) {
      if (!mounted) return;
      setState(() => _busy = false);
      recordsToast(context, FinancePersonalController.reason(error));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    final l = FinanceLendingStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final title = Semantics(
      header: true,
      child: Text(widget.loan.person,
          style: Theme.of(context).textTheme.titleLarge),
    );
    final Widget body = switch (_state) {
      MoneyLoad.idle || MoneyLoad.loading => AnumFeedback(
          kind: AnumFeedbackKind.loading, message: l.t('loan.loading')),
      MoneyLoad.denied => AnumFeedback(
          kind: AnumFeedbackKind.permission, message: s.t('pm.denied')),
      MoneyLoad.offline => AnumFeedback(
          kind: AnumFeedbackKind.offline,
          message: s.t('pm.offline'),
          onRetry: _load),
      MoneyLoad.error => AnumFeedback(
          kind: AnumFeedbackKind.error,
          message: l.t('loan.failed', {'message': _error ?? ''}),
          onRetry: _load),
      MoneyLoad.ready => _figures(context, s, l, muted),
    };
    return Column(
      key: Key('loan-detail-${widget.loan.id}'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        title,
        const SizedBox(height: AnumSpacing.sm),
        body,
        const SizedBox(height: AnumSpacing.md),
        Wrap(
          alignment: WrapAlignment.end,
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: Text(l.t('loan.done')),
            ),
            if (_state == MoneyLoad.ready) ...[
              Semantics(
                label: l.t('loan.deleteLabel', {'person': widget.loan.person}),
                excludeSemantics: true,
                child: TextButton.icon(
                  key: const Key('loan-delete'),
                  onPressed: _busy ? null : _delete,
                  style: TextButton.styleFrom(
                      foregroundColor: context.palette.stop),
                  icon: const Icon(Icons.delete_outline),
                  label: Text(l.t('loan.delete')),
                ),
              ),
              FilledButton.icon(
                key: const Key('loan-detail-edit'),
                onPressed: _busy ? null : _edit,
                icon: const Icon(Icons.edit_outlined),
                label: Text(l.t('loan.edit')),
              ),
            ],
          ],
        ),
      ],
    );
  }

  Widget _figures(BuildContext context, FinancePersonalStrings s,
      FinanceLendingStrings l, TextStyle muted) {
    final d = _detail!;
    final loan = d.loan;
    String m(String amount) => _money(context, amount, loan.currency);
    final rows = _all ? d.schedule : d.schedule.take(_firstRows).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(title: l.t('loan.amount'), detail: m(loan.amount)),
        AnumRow(
            key: const Key('loan-detail-handed'),
            title: l.t('loan.handed'),
            detail: m(loan.handed)),
        AnumRow(title: l.t('loan.repaid'), detail: m(loan.repaid)),
        AnumRow(
            key: const Key('loan-detail-left'),
            title: l.t('loan.left'),
            detail: m(loan.outstanding)),
        AnumRow(
            title: loan.interestRate == null
                ? l.t('loan.interestFree')
                : l.t(
                    'loan.interestTotal', {'rate': _rate(loan.interestRate!)}),
            detail: loan.interestRate == null ? null : m(loan.interest)),
        if (loan.nextDueOn != null)
          AnumRow(
              icon: Icons.event_outlined,
              title: l.t('loan.next', {
                'date': recordsDate(context, loan.nextDueOn),
                'amount': m(loan.nextDueAmount),
              })),
        if (loan.hasOverdue)
          AnumRow(
              key: const Key('loan-detail-overdue'),
              icon: Icons.warning_amber_outlined,
              tone: AnumTone.stop,
              title: l.t('loan.overdue', {'amount': m(loan.overdueAmount)})),
        const SizedBox(height: AnumSpacing.sm),
        AnumHeader(title: l.t('loan.scheduleTitle')),
        if (d.schedule.isEmpty)
          Text(
              loan.dueOn == null
                  ? l.t('loan.noDue')
                  : l.t(
                      'loan.once', {'date': recordsDate(context, loan.dueOn)}),
              style: muted)
        else ...[
          for (final row in rows)
            AnumRow(
              key: Key('loan-installment-${row.number}'),
              icon: row.state == InstallmentState.repaid
                  ? Icons.check_circle_outline
                  : row.state == InstallmentState.overdue
                      ? Icons.warning_amber_outlined
                      : Icons.event_outlined,
              tone: _installmentTone(row.state),
              title: l.t('loan.row', {
                'n': row.number,
                'date': recordsDate(context, row.dueOn),
              }),
              subtitle: '${l.t('loan.state.${row.state.name}')} · '
                  '${l.t('loan.rowDetail', {
                    'principal': m(row.principal),
                    'interest': m(row.interest),
                  })}',
              detail: m(row.amount),
            ),
          if (!_all && d.schedule.length > _firstRows)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                key: const Key('loan-schedule-all'),
                onPressed: () => setState(() => _all = true),
                child: Text(l.t('loan.scheduleMore', {'n': d.schedule.length})),
              ),
            ),
        ],
        const SizedBox(height: AnumSpacing.sm),
        AnumHeader(title: l.t('loan.movements')),
        if (d.movements.isEmpty)
          Text(l.t('loan.movementsNone'),
              key: const Key('loan-movements-none'), style: muted)
        else
          for (final mv in d.movements)
            AnumRow(
              key: Key('loan-movement-${mv.linkId}'),
              icon:
                  mv.role == 'deposit' ? Icons.call_received : Icons.call_made,
              tone: mv.active ? AnumTone.neutral : AnumTone.warn,
              title: (mv.description ?? '').isEmpty ? '-' : mv.description!,
              subtitle: [
                mv.postedOn == null ? '-' : recordsDate(context, mv.postedOn),
                l.label('loan.role', mv.role),
                if (!mv.active) l.t('loan.review'),
              ].join(' · '),
              detail: mv.amount != null && mv.currency != null
                  ? _money(context, mv.amount!, mv.currency!)
                  : '-',
              trailing: IconButton(
                key: Key('loan-unlink-${mv.linkId}'),
                tooltip: l.t('loan.unlinkLabel', {
                  'what': (mv.description ?? '').isEmpty
                      ? l.t('loan.unlink')
                      : mv.description!,
                }),
                onPressed: _busy ? null : () => _unlink(mv),
                icon: const Icon(Icons.link_off),
              ),
            ),
        const SizedBox(height: AnumSpacing.xs),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
            key: const Key('loan-link-handed'),
            onPressed: _busy ? null : _linkHanded,
            icon: const Icon(Icons.link),
            label: Text(l.t(loan.direction == LoanDirection.lent
                ? 'loan.linkLent'
                : 'loan.linkBorrowed')),
          ),
        ),
      ],
    );
  }
}

// -- repayment suggestions -------------------------------------------------------

/// Transactions the Suggestions queue proposes as repayments of a loan with
/// a person: Accept links it through the links API (with the payload hash
/// shown), Dismiss drops it for good.
class LoanSuggestionsView extends StatelessWidget {
  const LoanSuggestionsView({required this.controller, super.key});
  final FinancePersonalController controller;

  Future<void> _decide(
      BuildContext context, Suggestion item, bool accept) async {
    final l = FinanceLendingStrings.of(context);
    final problem = await controller.decideLoanSuggestion(item, accept: accept);
    if (!context.mounted) return;
    recordsToast(
        context,
        problem == null
            ? l.t(accept ? 'sugg.linked' : 'sugg.dismissed')
            : l.t('sugg.decideFailed', {'message': problem}));
  }

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    final l = FinanceLendingStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final muted = TextStyle(color: context.palette.muted);
        final children = <Widget>[
          AnumHeader(title: l.t('sugg.title'), subtitle: l.t('sugg.intro')),
        ];
        final hasData = c.suggestionsState == MoneyLoad.ready ||
            c.loanSuggestions.isNotEmpty;
        if ((c.suggestionsState == MoneyLoad.idle ||
                c.suggestionsState == MoneyLoad.loading) &&
            !hasData) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.loading, message: l.t('sugg.loading')));
        } else if (c.suggestionsState == MoneyLoad.denied) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.permission, message: s.t('pm.denied')));
        } else if (c.suggestionsState == MoneyLoad.offline) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.offline,
              message: s.t('pm.offline'),
              onRetry: c.loadLoanSuggestions));
        } else if (c.suggestionsState == MoneyLoad.error) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.error,
              message:
                  l.t('sugg.failed', {'message': c.suggestionsError ?? ''}),
              onRetry: c.loadLoanSuggestions));
        } else if (c.loanSuggestions.isEmpty) {
          children.add(Text('${l.t('sugg.empty')}. ${l.t('sugg.emptyText')}',
              key: const Key('loan-suggestions-empty'), style: muted));
        } else {
          for (final item in c.loanSuggestions) {
            children.add(_SuggestionCard(
                item: item,
                busy: c.decidingSuggestion != null,
                onDecide: (accept) => _decide(context, item, accept)));
          }
          if (c.suggestionsError != null) {
            children.add(Text(
                l.t('sugg.moreFailed', {'message': c.suggestionsError!}),
                style: TextStyle(color: context.palette.stop)));
          }
          if (c.hasMoreSuggestions) {
            children.add(Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                key: const Key('loan-suggestions-more'),
                onPressed:
                    c.loadingMoreSuggestions ? null : c.loadMoreLoanSuggestions,
                child: Text(c.loadingMoreSuggestions
                    ? l.t('sugg.loadingMore')
                    : l.t('sugg.more')),
              ),
            ));
          }
        }
        return Column(
          key: const Key('loan-suggestions'),
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < children.length; i++) ...[
              children[i],
              if (i < children.length - 1)
                const SizedBox(height: AnumSpacing.sm),
            ],
          ],
        );
      },
    );
  }
}

class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard(
      {required this.item, required this.busy, required this.onDecide});
  final Suggestion item;
  final bool busy;
  final void Function(bool accept) onDecide;

  @override
  Widget build(BuildContext context) {
    final l = FinanceLendingStrings.of(context);
    final p = item.params;
    final currency = p['currency'] ?? '';
    final lent = p['direction'] != 'borrowed';
    final person = p['person'] ?? '-';
    final amount = _money(context, p['amount'] ?? '0', currency);
    final percent = item.percent;
    return AnumSurface(
      key: Key('loan-suggestion-${item.key}'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(
            l.t(lent ? 'sugg.head.lent' : 'sugg.head.borrowed',
                {'person': person, 'amount': amount}),
            style: Theme.of(context).textTheme.titleSmall),
        if (item.occurredOn != null)
          Text(recordsDate(context, item.occurredOn),
              style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t(
            lent ? 'sugg.why.lent' : 'sugg.why.borrowed', {'person': person})),
        if (p['outstanding'] != null)
          Text(
              l.t('sugg.left', {
                'amount': _money(context, p['outstanding']!, currency),
              }),
              style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (percent != null)
              AnumPill(label: l.t('sugg.sure', {'n': percent})),
            if (item.acceptMode == 'link')
              FilledButton.icon(
                key: Key('loan-suggestion-accept-${item.key}'),
                onPressed: busy ? null : () => onDecide(true),
                icon: const Icon(Icons.link),
                label: Text(l.t('sugg.accept')),
              ),
            OutlinedButton(
              key: Key('loan-suggestion-dismiss-${item.key}'),
              onPressed: busy ? null : () => onDecide(false),
              child: Text(l.t('sugg.dismiss')),
            ),
          ],
        ),
      ]),
    );
  }
}

// -- expected one-off income -----------------------------------------------------

/// Expected one-off income on Safe to spend: list (still expected, or with
/// the received ones), add, edit, mark received and delete. [onChanged]
/// reloads the forecast, which counts these on their date.
class ExpectedIncomeView extends StatelessWidget {
  const ExpectedIncomeView(
      {required this.controller, this.onChanged, super.key});
  final FinancePersonalController controller;
  final Future<void> Function()? onChanged;

  Future<void> _form(BuildContext context, {ExpectedIncome? item}) async {
    final l = FinanceLendingStrings.of(context);
    final c = controller;
    String? name;
    final saved = await openRecordsForm(
      context,
      RecordsForm(
        key: const Key('income-form'),
        title: l.t(item == null ? 'income.add' : 'income.edit'),
        intro: l.t('income.subtitle'),
        submitLabel: l.t('income.save'),
        fields: [
          RecordsInput.text('label', l.t('income.label'),
              required: true,
              maxLength: 120,
              help: l.t('income.labelHelp'),
              initial: item?.label),
          RecordsInput.number('amount', l.t('income.amount'),
              required: true, initial: item?.amount),
          RecordsInput.text('currency', l.t('income.currency'),
              help: l.t('income.currencyHelp'),
              maxLength: 3,
              initial: item?.currency),
          RecordsInput.date('expected_on', l.t('income.on'),
              required: true, initial: item?.expectedOn ?? c.todayIso),
          RecordsInput.multiline('notes', l.t('income.notes'),
              maxLength: 1000, initial: item?.notes),
        ],
        onSubmit: (v) async {
          final amount = v['amount'] as String?;
          if (amount == null || (double.tryParse(amount) ?? 0) <= 0) {
            throw FormatException(l.t('income.badAmount'));
          }
          final currency = (v['currency'] as String?)?.toUpperCase();
          if (currency != null && !RegExp(r'^[A-Z]{3}$').hasMatch(currency)) {
            throw FormatException(l.t('income.badCurrency'));
          }
          name = v['label'] as String;
          await c.saveIncome(
              ExpectedIncomeInput(
                label: name!,
                amount: amount,
                currency: currency,
                expectedOn: v['expected_on'] as String,
                receivedOn: item?.receivedOn,
                notes: v['notes'] as String?,
              ),
              id: item?.id);
        },
      ),
    );
    if (!saved || !context.mounted) return;
    recordsToast(context, l.t('income.saved', {'name': name ?? ''}));
    await onChanged?.call();
  }

  Future<void> _change(BuildContext context, ExpectedIncome item,
      {required bool delete}) async {
    final l = FinanceLendingStrings.of(context);
    if (delete) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(l.t('income.deleteTitle', {'name': item.label})),
          content: Text(l.t('income.deleteText')),
          actions: [
            TextButton(
                onPressed: () => Navigator.of(dialog).pop(false),
                child: Text(l.t('income.cancel'))),
            FilledButton(
                key: const Key('income-delete-confirm'),
                onPressed: () => Navigator.of(dialog).pop(true),
                child: Text(l.t('income.delete'))),
          ],
        ),
      );
      if (ok != true || !context.mounted) return;
    }
    final problem = await controller.changeIncome(item, delete: delete);
    if (!context.mounted) return;
    if (problem != null) {
      recordsToast(context, l.t('income.failedAction', {'message': problem}));
      return;
    }
    recordsToast(
        context,
        l.t(delete ? 'income.deleted' : 'income.markedReceived',
            {'name': item.label}));
    await onChanged?.call();
  }

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    final l = FinanceLendingStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final muted = TextStyle(color: context.palette.muted);
        final add = FilledButton.icon(
          key: const Key('income-add'),
          onPressed: () => _form(context),
          icon: const Icon(Icons.add),
          label: Text(l.t('income.add')),
        );
        final children = <Widget>[
          AnumHeader(
              title: l.t('income.title'), subtitle: l.t('income.subtitle')),
          Align(alignment: AlignmentDirectional.centerStart, child: add),
          SwitchListTile(
            key: const Key('income-show-received'),
            contentPadding: EdgeInsets.zero,
            title: Text(l.t('income.showReceived')),
            value: c.showReceived,
            onChanged: c.incomeState == MoneyLoad.loading
                ? null
                : c.showReceivedIncome,
          ),
        ];
        final hasData = c.incomeState == MoneyLoad.ready;
        if ((c.incomeState == MoneyLoad.idle ||
                c.incomeState == MoneyLoad.loading) &&
            c.income.isEmpty) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.loading, message: l.t('income.loading')));
        } else if (c.incomeState == MoneyLoad.denied) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.permission, message: s.t('pm.denied')));
        } else if (c.incomeState == MoneyLoad.offline) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.offline,
              message: s.t('pm.offline'),
              onRetry: c.loadIncome));
        } else if (c.incomeState == MoneyLoad.error) {
          children.add(AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: l.t('income.failed', {'message': c.incomeError ?? ''}),
              onRetry: c.loadIncome));
        } else if (hasData && c.income.isEmpty && !c.showReceived) {
          children.add(AnumEmpty(
              key: const Key('income-empty'),
              title: l.t('income.emptyTitle'),
              message: l.t('income.empty'),
              icon: Icons.event_available_outlined,
              action: l.t('income.add'),
              onAction: () => _form(context)));
        } else if (c.income.isEmpty) {
          children.add(Text(l.t('income.noneOpen'), style: muted));
        } else {
          for (final item in c.income) {
            children.add(_IncomeRow(
              item: item,
              busy: c.changingIncome != null,
              onReceive: () => _change(context, item, delete: false),
              onEdit: () => _form(context, item: item),
              onDelete: () => _change(context, item, delete: true),
            ));
          }
          if (c.incomeError != null) {
            children.add(Text(
                l.t('income.moreFailed', {'message': c.incomeError!}),
                style: TextStyle(color: context.palette.stop)));
          }
          if (c.hasMoreIncome) {
            children.add(Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                key: const Key('income-more'),
                onPressed: c.loadingMoreIncome ? null : c.loadMoreIncome,
                child: Text(c.loadingMoreIncome
                    ? l.t('income.loadingMore')
                    : l.t('income.more')),
              ),
            ));
          }
        }
        return AnumSurface(
          key: const Key('expected-income'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var i = 0; i < children.length; i++) ...[
                children[i],
                if (i < children.length - 1)
                  const SizedBox(height: AnumSpacing.xs),
              ],
            ],
          ),
        );
      },
    );
  }
}

class _IncomeRow extends StatelessWidget {
  const _IncomeRow({
    required this.item,
    required this.busy,
    required this.onReceive,
    required this.onEdit,
    required this.onDelete,
  });
  final ExpectedIncome item;
  final bool busy;
  final VoidCallback onReceive, onEdit, onDelete;

  @override
  Widget build(BuildContext context) {
    final l = FinanceLendingStrings.of(context);
    final when = item.state == IncomeState.received
        ? l.t('income.receivedOn',
            {'date': recordsDate(context, item.receivedOn)})
        : l.t('income.expectedOn',
            {'date': recordsDate(context, item.expectedOn)});
    return Column(
      key: Key('income-${item.id}'),
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AnumRow(
          icon: item.state == IncomeState.received
              ? Icons.check_circle_outline
              : Icons.event_outlined,
          tone: switch (item.state) {
            IncomeState.received => AnumTone.ok,
            IncomeState.late => AnumTone.warn,
            IncomeState.expected => AnumTone.info,
          },
          title: item.label,
          subtitle: '$when · ${l.t('income.state.${item.state.name}')}',
          detail: _money(context, item.amount, item.currency),
        ),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (item.state != IncomeState.received)
              TextButton.icon(
                key: Key('income-receive-${item.id}'),
                onPressed: busy ? null : onReceive,
                icon: const Icon(Icons.done),
                label: Text(l.t('income.receive')),
              ),
            IconButton(
              key: Key('income-edit-${item.id}'),
              tooltip: l.t('income.editLabel', {'name': item.label}),
              onPressed: busy ? null : onEdit,
              icon: const Icon(Icons.edit_outlined),
            ),
            IconButton(
              key: Key('income-delete-${item.id}'),
              tooltip: l.t('income.deleteLabel', {'name': item.label}),
              onPressed: busy ? null : onDelete,
              icon: const Icon(Icons.delete_outline),
            ),
          ],
        ),
      ],
    );
  }
}
