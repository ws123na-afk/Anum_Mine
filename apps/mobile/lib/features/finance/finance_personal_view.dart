import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../records_kit/payment_picker.dart';
import '../records_kit/records_widgets.dart';
import 'finance_lending_strings.dart';
import 'finance_lending_view.dart';
import 'finance_personal_controller.dart';
import 'finance_personal_models.dart';
import 'finance_personal_strings.dart';

/// Opens an https link outside the app; false when it could not.
typedef MoneyLinkOpener = Future<bool> Function(Uri uri);

/// Source and web links open in the browser, never in a web view.
Future<bool> openMoneyLink(Uri uri) async {
  try {
    return await launchUrl(uri, mode: LaunchMode.externalApplication);
  } on Object {
    return false;
  }
}

String _money(BuildContext context, String amount, String currency) =>
    formatMoneyText(amount, currency, locale: Localizations.localeOf(context));

/// Loading, permission denied, offline or failed, or null when there is
/// something to show. [hasData] keeps a list on screen while it reloads.
Widget? moneyStateView(BuildContext context, FinancePersonalStrings s,
    MoneyLoad state, bool hasData, String? error, VoidCallback retry,
    {required String loading, required String failed}) {
  if ((state == MoneyLoad.idle || state == MoneyLoad.loading) && !hasData) {
    return AnumFeedback(kind: AnumFeedbackKind.loading, message: loading);
  }
  return switch (state) {
    MoneyLoad.denied => AnumFeedback(
        kind: AnumFeedbackKind.permission, message: s.t('pm.denied')),
    MoneyLoad.offline => AnumFeedback(
        kind: AnumFeedbackKind.offline,
        message: s.t('pm.offline'),
        onRetry: retry),
    MoneyLoad.error => AnumFeedback(
        kind: AnumFeedbackKind.error,
        message: s.t(failed, {'message': error ?? ''}),
        onRetry: retry),
    _ => null,
  };
}

/// Lays [parts] out one under the other with the shared spacing.
Widget _stack(List<Widget> parts) => Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < parts.length; i++) ...[
          parts[i],
          if (i < parts.length - 1) const SizedBox(height: AnumSpacing.md),
        ],
      ],
    );

// -- Money planner ------------------------------------------------------------

/// The Money planner: the person types a money question ("Can I afford a
/// car of 60,000 SAR?") and reads the answer the Assistant's finance skill
/// works out from their own records, with every live source's address and
/// the date it was read. Advice only (docs/finance.md#planning-and-advice).
class MoneyPlannerView extends StatefulWidget {
  const MoneyPlannerView(
      {required this.controller, required this.openLink, super.key});
  final FinancePersonalController controller;
  final MoneyLinkOpener openLink;

  @override
  State<MoneyPlannerView> createState() => _MoneyPlannerViewState();
}

class _MoneyPlannerViewState extends State<MoneyPlannerView> {
  late final _question =
      TextEditingController(text: widget.controller.question);

  @override
  void dispose() {
    _question.dispose();
    super.dispose();
  }

  void _ask([String? text]) {
    if (text != null) _question.text = text;
    FocusScope.of(context).unfocus();
    widget.controller.ask(_question.text);
  }

  Future<void> _open(Uri uri) async {
    final s = FinancePersonalStrings.of(context);
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (!await widget.openLink(uri)) {
      messenger?.showSnackBar(SnackBar(content: Text(s.t('pm.linkFailed'))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final c = widget.controller;
        final p = context.palette;
        if (c.planner == null) {
          return AnumEmpty(
              title: s.t('planner.title'),
              message: s.t('planner.unavailable'),
              icon: Icons.calculate_outlined);
        }
        final form = AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
                title: s.t('planner.title'), subtitle: s.t('planner.intro')),
            const SizedBox(height: AnumSpacing.sm),
            TextField(
              key: const Key('planner-question'),
              controller: _question,
              minLines: 1,
              maxLines: 4,
              maxLength: 500,
              enabled: !c.asking,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _ask(),
              decoration: InputDecoration(
                  labelText: s.t('planner.ask'), hintText: s.t('planner.hint')),
            ),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                for (final example in s.examples)
                  ActionChip(
                    label: Text(example),
                    onPressed: c.asking ? null : () => _ask(example),
                  ),
              ],
            ),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              spacing: AnumSpacing.sm,
              runSpacing: AnumSpacing.xs,
              children: [
                FilledButton.icon(
                  key: const Key('planner-send'),
                  onPressed: c.asking ? null : _ask,
                  icon: const Icon(Icons.send_outlined),
                  label: Text(s.t('planner.send')),
                ),
                if (c.asking)
                  OutlinedButton.icon(
                    onPressed: c.stopAsking,
                    icon: const Icon(Icons.stop_circle_outlined),
                    label: Text(s.t('planner.stop')),
                  ),
              ],
            ),
            const SizedBox(height: AnumSpacing.sm),
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.info_outline, size: 18, color: p.warn),
              const SizedBox(width: AnumSpacing.xs),
              Expanded(
                  child: Text(s.t('planner.adviceOnly'),
                      style: TextStyle(color: p.muted))),
            ]),
          ]),
        );
        final retry = c.question.isEmpty ? null : () => _ask(c.question);
        final Widget result = switch (c.plannerState) {
          MoneyLoad.idle => AnumEmpty(
              title: s.t('planner.empty'),
              message: s.t('planner.emptyText'),
              icon: Icons.question_answer_outlined),
          MoneyLoad.loading => AnumFeedback(
              kind: AnumFeedbackKind.loading, message: s.t('planner.working')),
          MoneyLoad.denied => AnumFeedback(
              kind: AnumFeedbackKind.permission,
              message: s.t('planner.denied')),
          MoneyLoad.offline => AnumFeedback(
              kind: AnumFeedbackKind.offline,
              message: s.t('pm.offline'),
              onRetry: retry),
          MoneyLoad.error => AnumFeedback(
              kind: AnumFeedbackKind.error,
              message: s.t('planner.failed', {'message': c.plannerError ?? ''}),
              onRetry: retry),
          MoneyLoad.ready => _Answer(
              question: c.question,
              answer: c.answer!,
              strings: s,
              onOpen: _open),
        };
        return _stack([form, result]);
      },
    );
  }
}

class _Answer extends StatelessWidget {
  const _Answer(
      {required this.question,
      required this.answer,
      required this.strings,
      required this.onOpen});
  final String question;
  final PlannerAnswer answer;
  final FinancePersonalStrings strings;
  final void Function(Uri uri) onOpen;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final p = context.palette;
    final sources = answer.sources;
    return AnumSurface(
      semanticLabel: s.t('planner.answer'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            eyebrow: s.t('planner.answer'),
            title: question,
            trailing: AnumPill(
                label: s.t('planner.advicePill'), tone: AnumTone.warn)),
        const SizedBox(height: AnumSpacing.sm),
        SelectableText(answer.text, key: const Key('planner-answer')),
        const SizedBox(height: AnumSpacing.sm),
        if (sources.isEmpty)
          Text(s.t('planner.ownData'), style: TextStyle(color: p.muted))
        else ...[
          Semantics(
              header: true,
              child: Text(s.t('planner.sources'),
                  style: Theme.of(context).textTheme.titleSmall)),
          Text(s.t('planner.sourcesNote'), style: TextStyle(color: p.muted)),
          for (final uri in sources)
            AnumRow(
              icon: Icons.open_in_new,
              title: uri.host,
              subtitle: uri.toString(),
              onTap: () => onOpen(uri),
            ),
        ],
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('planner.saved'), style: TextStyle(color: p.muted)),
      ]),
    );
  }
}

// -- Lent and borrowed ----------------------------------------------------------

/// What I owe and what I am owed: totals per direction and currency, each
/// loan with its due date and an overdue badge, a new entry and a repayment
/// linked to a recorded transaction (docs/finance.md#lent-and-borrowed).
class PeopleLoansView extends StatelessWidget {
  const PeopleLoansView({required this.controller, super.key});
  final FinancePersonalController controller;

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final waiting = moneyStateView(context, s, c.loansState,
            c.loansState == MoneyLoad.ready, c.loansError, c.loadLoans,
            loading: s.t('people.loading'), failed: 'people.failed');
        if (waiting != null) return waiting;
        final add = Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton.icon(
            key: const Key('loan-add'),
            onPressed: () => _add(context, c, s),
            icon: const Icon(Icons.add),
            label: Text(s.t('people.add')),
          ),
        );
        final filter = Semantics(
          label: s.t('people.filter'),
          container: true,
          child: Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                for (final (value, label) in <(LoanDirection?, String)>[
                  (null, s.t('people.all')),
                  (LoanDirection.lent, s.t('people.lent')),
                  (LoanDirection.borrowed, s.t('people.borrowed')),
                ])
                  ChoiceChip(
                    label: Text(label),
                    selected: c.direction == value,
                    onSelected: (_) => c.showDirection(value),
                  ),
              ]),
        );
        if (c.loans.isEmpty && c.direction == null) {
          return _stack([
            add,
            AnumEmpty(
              title: s.t('people.empty'),
              message: s.t('people.emptyText'),
              icon: Icons.handshake_outlined,
              action: s.t('people.add'),
              onAction: () => _add(context, c, s),
            ),
          ]);
        }
        return _stack([
          _Totals(controller: c, strings: s),
          add,
          filter,
          if (c.loans.isEmpty)
            Text(s.t('people.noneHere'),
                style: TextStyle(color: context.palette.muted))
          else
            AnumSurface(
              padding: const EdgeInsets.symmetric(
                  horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
              child: Column(children: [
                for (final loan in c.loans)
                  _LoanRow(
                      loan: loan,
                      strings: s,
                      onOpen: () => openLoanDetail(context, c, loan),
                      onEdit: () => openLoanForm(context, c, loan: loan),
                      onRepay: loan.settled
                          ? null
                          : () => _repay(context, c, s, loan)),
              ]),
            ),
          if (c.loansError != null)
            Text(s.t('people.moreFailed', {'message': c.loansError!}),
                style: TextStyle(color: context.palette.stop)),
          if (c.hasMoreLoans)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                onPressed: c.loadingMore ? null : c.loadMoreLoans,
                child: Text(c.loadingMore
                    ? s.t('people.loadingMore')
                    : s.t('people.more')),
              ),
            ),
          LoanSuggestionsView(controller: c),
          Text(s.t('people.reminders'),
              style: TextStyle(color: context.palette.muted)),
        ]);
      },
    );
  }

  Future<void> _add(BuildContext context, FinancePersonalController c,
          FinancePersonalStrings s) =>
      openLoanForm(context, c);

  Future<void> _repay(BuildContext context, FinancePersonalController c,
      FinancePersonalStrings s, PersonLoan loan) async {
    final linked = await showRecordsSheet<bool>(
      context,
      (_) => PaymentPickerSheet(
        text: PaymentPickerText(
          title: s.t('repay.title', {'person': loan.person}),
          help: s.t(loan.direction == LoanDirection.lent
              ? 'repay.helpIn'
              : 'repay.helpOut'),
          search: s.t('repay.search'),
          empty: s.t('repay.empty'),
          remaining: s.t('repay.remaining'),
          already: s.t('repay.already'),
        ),
        load: (query) async => [
          for (final item
              in await c.repository.repaymentCandidates(loan, query: query))
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
        link: (choice, key) => c.recordRepayment(loan,
            canonicalId: choice.id, idempotencyKey: key),
      ),
    );
    if ((linked ?? false) && context.mounted) {
      recordsToast(context, s.t('repay.done'));
    }
  }
}

class _Totals extends StatelessWidget {
  const _Totals({required this.controller, required this.strings});
  final FinancePersonalController controller;
  final FinancePersonalStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final l = FinanceLendingStrings.of(context);
    final c = controller;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
          title: s.t('people.totals'),
          trailing: c.overdue > 0
              ? AnumPill(
                  label: s.t('people.overdueCount', {'n': c.overdue}),
                  tone: AnumTone.stop,
                  icon: Icons.warning_amber_outlined)
              : null,
        ),
        if (c.owed.isEmpty)
          Text(s.t('people.allSettled'),
              style: TextStyle(color: context.palette.muted))
        else
          for (final total in c.owed) ...[
            AnumRow(
              icon: Icons.call_received,
              tone: AnumTone.ok,
              title: s.t('people.theyOweMe'),
              detail: _money(context, total.theyOweMe, total.currency),
            ),
            if ((double.tryParse(total.theyOweMeOverdue) ?? 0) > 0)
              AnumRow(
                key: Key('owed-overdue-in-${total.currency}'),
                icon: Icons.warning_amber_outlined,
                tone: AnumTone.stop,
                title: l.t('loan.theyOweOverdue'),
                detail: _money(context, total.theyOweMeOverdue, total.currency),
              ),
            AnumRow(
              icon: Icons.call_made,
              tone: AnumTone.warn,
              title: s.t('people.iOwe'),
              detail: _money(context, total.iOwe, total.currency),
            ),
            if ((double.tryParse(total.iOweOverdue) ?? 0) > 0)
              AnumRow(
                key: Key('owed-overdue-out-${total.currency}'),
                icon: Icons.warning_amber_outlined,
                tone: AnumTone.stop,
                title: l.t('loan.iOweOverdue'),
                detail: _money(context, total.iOweOverdue, total.currency),
              ),
          ],
      ]),
    );
  }
}

class _LoanRow extends StatelessWidget {
  const _LoanRow(
      {required this.loan,
      required this.strings,
      required this.onOpen,
      required this.onEdit,
      this.onRepay});
  final PersonLoan loan;
  final FinancePersonalStrings strings;
  final VoidCallback onOpen, onEdit;
  final VoidCallback? onRepay;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final l = FinanceLendingStrings.of(context);
    final lent = loan.direction == LoanDirection.lent;
    final nextOn = loan.settled ? null : (loan.nextDueOn ?? loan.dueOn);
    final due = nextOn == null
        ? s.t('people.noDue')
        : s.t('people.due', {'date': recordsDate(context, nextOn)});
    final terms = [
      if (loan.installments != null)
        l.t('loan.installmentsShort', {'n': loan.installments!}),
      if (loan.interestRate != null)
        l.t('loan.rateShort', {
          'rate': (double.tryParse(loan.interestRate!) ?? 0)
              .toString()
              .replaceFirst(RegExp(r'\.0$'), '')
        }),
    ];
    final status = s.t('people.status.${loan.status.name}');
    return Padding(
      key: Key('loan-${loan.id}'),
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumRow(
          icon: lent ? Icons.call_received : Icons.call_made,
          tone: loan.status == LoanStatus.overdue
              ? AnumTone.stop
              : lent
                  ? AnumTone.ok
                  : AnumTone.warn,
          title: loan.person,
          subtitle: [
            s.t(lent ? 'people.rowLent' : 'people.rowBorrowed'),
            due,
            ...terms,
          ].join(' · '),
          detail: s.t('people.left', {
            'left': _money(context, loan.outstanding, loan.currency),
            'amount': _money(context, loan.amount, loan.currency),
          }),
        ),
        Wrap(
          spacing: AnumSpacing.sm,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            AnumPill(
              label: status,
              tone: switch (loan.status) {
                LoanStatus.overdue => AnumTone.stop,
                LoanStatus.dueSoon => AnumTone.warn,
                LoanStatus.repaid || LoanStatus.closed => AnumTone.ok,
                LoanStatus.open => AnumTone.neutral,
              },
            ),
            if (loan.hasOverdue)
              AnumPill(
                key: Key('loan-overdue-${loan.id}'),
                label: l.t('loan.overdue', {
                  'amount': _money(context, loan.overdueAmount, loan.currency)
                }),
                tone: AnumTone.stop,
                icon: Icons.warning_amber_outlined,
              ),
            if (loan.nextDueOn != null &&
                !loan.settled &&
                (double.tryParse(loan.nextDueAmount) ?? 0) > 0)
              Text(
                  l.t('loan.next', {
                    'date': recordsDate(context, loan.nextDueOn),
                    'amount':
                        _money(context, loan.nextDueAmount, loan.currency),
                  }),
                  style: TextStyle(color: context.palette.muted)),
          ],
        ),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xxs,
          children: [
            Semantics(
              label: l.t('loan.openLabel', {'person': loan.person}),
              button: true,
              excludeSemantics: true,
              child: TextButton.icon(
                key: Key('loan-open-${loan.id}'),
                onPressed: onOpen,
                icon: const Icon(Icons.calendar_month_outlined),
                label: Text(l.t('loan.open')),
              ),
            ),
            Semantics(
              label: l.t('loan.editLabel', {'person': loan.person}),
              button: true,
              excludeSemantics: true,
              child: TextButton.icon(
                key: Key('loan-edit-${loan.id}'),
                onPressed: onEdit,
                icon: const Icon(Icons.edit_outlined),
                label: Text(l.t('loan.edit')),
              ),
            ),
            if (onRepay != null)
              TextButton.icon(
                key: Key('loan-repay-${loan.id}'),
                onPressed: onRepay,
                icon: const Icon(Icons.link),
                label: Text(s.t('people.recordRepayment')),
              ),
          ],
        ),
      ]),
    );
  }
}

// -- Fixed monthly costs ---------------------------------------------------------

/// Fixed monthly costs: one total per currency and the list (rent, bills,
/// subscriptions, installments, debt minimums, regular transfers home),
/// read only (docs/finance.md#fixed-commitments).
/// On the phone it sits under the bills list, whose Add bill is the next
/// step when there are no fixed costs yet.
class FixedCostsView extends StatelessWidget {
  const FixedCostsView({required this.controller, super.key});
  final FinancePersonalController controller;

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final waiting = moneyStateView(context, s, c.commitmentsState,
            c.commitments != null, c.commitmentsError, c.loadCommitments,
            loading: s.t('fixed.loading'), failed: 'fixed.failed');
        if (waiting != null) return waiting;
        final f = c.commitments!;
        if (f.isEmpty) {
          return AnumEmpty(
            title: s.t('fixed.empty'),
            message: s.t('fixed.emptyText'),
            icon: Icons.event_repeat_outlined,
          );
        }
        final muted = TextStyle(color: context.palette.muted);
        return _stack([
          AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(
                      eyebrow: f.month.isEmpty
                          ? null
                          : s.t('fixed.month', {'month': f.month}),
                      title: s.t('fixed.title')),
                  for (final total in f.totals) ...[
                    AnumRow(
                      icon: Icons.event_repeat_outlined,
                      title: s.t('fixed.total', {'currency': total.currency}),
                      detail: _money(context, total.amount, total.currency),
                    ),
                    if (f.incomeFor(total.currency) case final share?)
                      _IncomeShareLine(share: share, strings: s),
                  ],
                  Text(s.t('fixed.never'), style: muted),
                ]),
          ),
          AnumSurface(
            padding: const EdgeInsets.symmetric(
                horizontal: AnumSpacing.md, vertical: AnumSpacing.xs),
            child: Column(children: [
              for (final item in f.items)
                AnumRow(
                  icon: _kindIcon(item.kind),
                  title: item.name,
                  subtitle: _kindLine(s, item),
                  detail: _money(context, item.monthly, item.currency),
                ),
            ]),
          ),
          if (f.notCounted.isNotEmpty)
            AnumSurface(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AnumHeader(
                        title: s.t('fixed.leftOut'),
                        subtitle: s.t('fixed.leftOutText')),
                    for (final item in f.notCounted)
                      AnumRow(
                        title: item.name,
                        subtitle: _kindLine(s, item),
                        detail: _money(context, item.monthly, item.currency),
                      ),
                  ]),
            ),
          Text(s.t('fixed.howCounted'), style: muted),
        ]);
      },
    );
  }

  static IconData _kindIcon(String kind) => switch (kind) {
        'person_loan' => Icons.handshake_outlined,
        'rent' => Icons.home_outlined,
        'subscription' => Icons.subscriptions_outlined,
        'installment' => Icons.view_week_outlined,
        'loan_minimum' || 'card_minimum' => Icons.credit_card_outlined,
        'remittance' => Icons.send_outlined,
        _ => Icons.receipt_long_outlined,
      };

  static String _kindLine(FinancePersonalStrings s, Commitment item) {
    final kind = s.maybe('fixed.kind.${item.kind}') ?? item.kind;
    final note = item.note == null ? null : s.maybe('fixed.note.${item.note}');
    return note == null ? kind : '$kind · $note';
  }
}

/// Fixed costs as a share of income in one currency, with where the income
/// figure comes from; says so when there is no income to compare with.
class _IncomeShareLine extends StatelessWidget {
  const _IncomeShareLine({required this.share, required this.strings});
  final IncomeShare share;
  final FinancePersonalStrings strings;

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final p = context.palette;
    final percent = share.percent;
    final monthly = share.monthly;
    if (percent == null || monthly == null) {
      return Text(s.t('fixed.noIncome', {'currency': share.currency}),
          style: TextStyle(color: p.muted));
    }
    final from = share.source == null
        ? null
        : s.maybe('fixed.shareFrom.${share.source}');
    return Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(
            s.t('fixed.share', {
              'percent': percent,
              'income': _money(context, monthly, share.currency),
            }),
            style: TextStyle(color: percent > 50 ? p.warn : null)),
        const SizedBox(height: AnumSpacing.xxs),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
              value: (percent / 100).clamp(0.0, 1.0),
              minHeight: 6,
              color: percent > 50 ? p.warn : p.violet,
              backgroundColor: p.surface3,
              semanticsLabel: s.t('fixed.share', {
                'percent': percent,
                'income': _money(context, monthly, share.currency),
              })),
        ),
        if (from != null) Text(from, style: TextStyle(color: p.muted)),
      ]),
    );
  }
}

// -- Debt payoff -----------------------------------------------------------------

/// Which debt to pay first: avalanche (highest rate first) against snowball
/// (smallest balance first) per currency, with months to debt-free, the
/// total interest and the order, read only
/// (docs/finance.md#planning-and-advice).
class DebtPayoffCard extends StatelessWidget {
  const DebtPayoffCard({required this.controller, super.key});
  final FinancePersonalController controller;

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final waiting = moneyStateView(context, s, c.payoffState,
            c.payoff != null, c.payoffError, c.loadPayoff,
            loading: s.t('payoff.loading'), failed: 'payoff.failed');
        if (waiting != null) return waiting;
        final page = c.payoff!;
        if (page.currencies.isEmpty) {
          return AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: s.t('payoff.title')),
                  Text(s.t('payoff.empty'),
                      style: TextStyle(color: context.palette.muted)),
                ]),
          );
        }
        return _stack([
          for (final plan in page.currencies)
            _PayoffPlan(plan: plan, strings: s),
        ]);
      },
    );
  }
}

class _PayoffPlan extends StatelessWidget {
  const _PayoffPlan({required this.plan, required this.strings});
  final Payoff plan;
  final FinancePersonalStrings strings;

  String _months(int? months) => months == null
      ? strings.t('payoff.never')
      : strings.t('payoff.months', {'n': months});

  Widget _strategy(BuildContext context, String key, PayoffOrder order) {
    final s = strings;
    final best = plan.cheaper == key;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Wrap(
        spacing: AnumSpacing.xs,
        runSpacing: AnumSpacing.xxs,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text(s.t('payoff.$key'),
              style: Theme.of(context).textTheme.titleSmall),
          if (best) AnumPill(label: s.t('payoff.cheaper'), tone: AnumTone.ok),
        ],
      ),
      Text(s.t('payoff.${key}Text'),
          style: TextStyle(color: context.palette.muted)),
      AnumRow(
          icon: Icons.flag_outlined,
          title: s.t('payoff.debtFree'),
          detail: _months(order.months)),
      AnumRow(
          icon: Icons.percent,
          title: s.t('payoff.interest'),
          detail: _money(context, order.totalInterest, plan.currency)),
      if (order.order.isNotEmpty)
        Text(s.t('payoff.order', {'list': plan.names(order).join(', ')})),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final p = context.palette;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        AnumHeader(
            eyebrow: plan.currency,
            title: s.t('payoff.title'),
            subtitle: s.t('payoff.paying',
                {'amount': _money(context, plan.payment, plan.currency)})),
        if (plan.belowMinimums)
          Text(s.t('payoff.belowMinimums'), style: TextStyle(color: p.warn)),
        const SizedBox(height: AnumSpacing.sm),
        _strategy(context, 'avalanche', plan.avalanche),
        const Divider(height: AnumSpacing.lg),
        _strategy(context, 'snowball', plan.snowball),
        const SizedBox(height: AnumSpacing.sm),
        if (plan.cheaper == 'same')
          Text(s.t('payoff.same'))
        else if (plan.cheaper != null)
          Text(s.t('payoff.saves', {
            'strategy': s.t('payoff.${plan.cheaper}'),
            'amount': _money(context, plan.interestSaved, plan.currency),
          })),
        if (plan.firstWinMonths != null)
          Text(s.t('payoff.firstWin', {'n': plan.firstWinMonths!}),
              style: TextStyle(color: p.muted)),
        if (plan.fromPeople.isNotEmpty)
          Text(s.t('payoff.fromPeople', {'list': plan.fromPeople.join(', ')}),
              style: TextStyle(color: p.muted)),
        if (plan.missingRates.isNotEmpty)
          Text(
              s.t('payoff.missingRates',
                  {'list': plan.missingRates.join(', ')}),
              style: TextStyle(color: p.warn)),
        const SizedBox(height: AnumSpacing.xs),
        Text(s.t('payoff.note'), style: TextStyle(color: p.muted)),
      ]),
    );
  }
}

// -- Salary ------------------------------------------------------------------------

/// The salary check, compact: this month's expected net against what was
/// received, a missing or late flag, the next pay day, and a confirm or
/// reject for a month whose salary was found in the records
/// (docs/finance.md#salary). The profile and payslips are edited on the web.
class SalaryCard extends StatelessWidget {
  const SalaryCard(
      {required this.controller,
      required this.appUrl,
      required this.openLink,
      super.key});
  final FinancePersonalController controller;
  final String appUrl;
  final MoneyLinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final waiting = moneyStateView(context, s, c.salaryState,
            c.salary != null, c.salaryError, c.loadSalary,
            loading: s.t('salary.loading'), failed: 'salary.failed');
        if (waiting != null) return waiting;
        final p = context.palette;
        final muted = TextStyle(color: p.muted);
        final view = c.salary!;
        final profile = view.profile;
        final link = financeWebLink(appUrl, 'finance-advisor-salary');
        final web = link == null
            ? Text(s.t('salary.onWeb'), style: muted)
            : Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton.icon(
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.maybeOf(context);
                    if (!await openLink(link)) {
                      messenger?.showSnackBar(
                          SnackBar(content: Text(s.t('pm.linkFailed'))));
                    }
                  },
                  icon: const Icon(Icons.open_in_new),
                  label: Text(s.t('web.open')),
                ),
              );
        if (profile == null) {
          return AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: s.t('salary.none')),
                  Text(s.t('salary.noneText'), style: muted),
                  if (link != null) web,
                ]),
          );
        }
        final month = view.current;
        final missing = view.months?.missing ?? 0;
        return AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            AnumHeader(
              eyebrow: s.t('salary.title'),
              title: s.t('salary.employer',
                  {'employer': profile.employer, 'day': profile.payDay}),
              subtitle: s.t('salary.nextPay',
                  {'date': recordsDate(context, profile.nextPayOn)}),
            ),
            if (month != null) ...[
              Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(s.t('salary.month', {'month': month.month})),
                  AnumPill(
                    key: const Key('salary-status'),
                    label: s.maybe('salary.status.${month.status}') ??
                        month.status,
                    tone: switch (month.status) {
                      'received' => AnumTone.ok,
                      'missing' => AnumTone.stop,
                      'late' || 'partial' => AnumTone.warn,
                      _ => AnumTone.neutral,
                    },
                  ),
                ],
              ),
              AnumRow(
                  icon: Icons.event_available_outlined,
                  title: s.t('salary.expected'),
                  detail: _money(context, month.expectedNet, month.currency)),
              AnumRow(
                  icon: Icons.south_west,
                  tone: month.status == 'received' ? AnumTone.ok : null,
                  title: s.t('salary.received'),
                  detail: _money(context, month.received, month.currency)),
            ],
            if (missing > 0)
              Text(s.t('salary.missingCount', {'n': missing}),
                  style: TextStyle(color: p.warn)),
            for (final waiting in view.waiting)
              _SalaryQuestion(month: waiting, controller: c, strings: s),
            web,
          ]),
        );
      },
    );
  }
}

class _SalaryQuestion extends StatelessWidget {
  const _SalaryQuestion(
      {required this.month, required this.controller, required this.strings});
  final SalaryMonth month;
  final FinancePersonalController controller;
  final FinancePersonalStrings strings;

  Future<void> _decide(BuildContext context, bool confirm) async {
    final s = strings;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final failed = await controller.decideSalary(month, confirm: confirm);
    messenger
      ?..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(
          content: Text(failed ??
              s.t(confirm ? 'salary.confirmed' : 'salary.rejected'))));
  }

  @override
  Widget build(BuildContext context) {
    final s = strings;
    final found = month.found!;
    final busy = controller.decidingSalary != null;
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.sm),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(s.t('salary.isThis', {'month': month.month}),
            style: Theme.of(context).textTheme.titleSmall),
        Text(found.description, textDirection: TextDirection.ltr),
        Text(
            s.t('salary.found', {
              'amount': _money(context, found.amount, month.currency),
              'date': recordsDate(context, found.postedOn),
            }),
            style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.xs),
        Wrap(spacing: AnumSpacing.sm, runSpacing: AnumSpacing.xs, children: [
          FilledButton(
              key: Key('salary-confirm-${month.month}'),
              onPressed: busy ? null : () => _decide(context, true),
              child: Text(s.t('salary.confirm'))),
          OutlinedButton(
              key: Key('salary-reject-${month.month}'),
              onPressed: busy ? null : () => _decide(context, false),
              child: Text(s.t('salary.reject'))),
        ]),
      ]),
    );
  }
}

/// The address of a web Finance page (`<app URL>#finance-advisor-salary`), or
/// null when this build does not know the web app's address.
Uri? financeWebLink(String appUrl, String section) {
  final base = appUrl.trim().split('#').first;
  final uri = Uri.tryParse(base);
  if (uri == null || !uri.hasScheme || uri.host.isEmpty) return null;
  if (uri.scheme != 'https' && uri.scheme != 'http') return null;
  return uri.replace(fragment: section);
}

/// "Payslips, the salary profile and Send home are on the web", a link when the build knows the
/// web app's address (`ANUM_WEB_APP_URL`), else plain text.
class FinanceWebOnlyLine extends StatelessWidget {
  const FinanceWebOnlyLine(
      {required this.appUrl, required this.openLink, super.key});
  final String appUrl;
  final MoneyLinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final s = FinancePersonalStrings.of(context);
    final muted = TextStyle(color: context.palette.muted);
    final link = financeWebLink(appUrl, 'finance-advisor-salary');
    if (link == null) return Text(s.t('web.onWeb'), style: muted);
    return Align(
      alignment: AlignmentDirectional.centerStart,
      child: TextButton.icon(
        onPressed: () async {
          final messenger = ScaffoldMessenger.maybeOf(context);
          if (!await openLink(link)) {
            messenger
                ?.showSnackBar(SnackBar(content: Text(s.t('pm.linkFailed'))));
          }
        },
        icon: const Icon(Icons.open_in_new),
        label: Text(s.t('web.open')),
      ),
    );
  }
}
