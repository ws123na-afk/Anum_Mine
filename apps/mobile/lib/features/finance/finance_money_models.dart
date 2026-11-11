import '../../data/json_read.dart';
import 'finance_more_data.dart' show dec, decOr;
import 'finance_personal_models.dart';

/// The Money advisor, the Plan's afford and compare answers, payslips and
/// transfers home as the phone reads them (docs/finance.md#money-advisor,
/// #planning-and-advice, #payslips, #sending-money-home), mirroring
/// `packages/contracts/src/finance.ts` (`FinanceAdvisor*`) and
/// `financePersonal.ts`. Advice only: nothing here pays or moves money.

// -- the Money advisor ------------------------------------------------------------

class AdvisorSettingsData {
  const AdvisorSettingsData({required this.enabled, required this.weekday});
  final bool enabled;

  /// Monday is 0, Sunday 6.
  final int weekday;
  factory AdvisorSettingsData.fromJson(JsonReader r) => AdvisorSettingsData(
      enabled: r.optBool('enabled') ?? true, weekday: r.optInt('weekday') ?? 6);
}

/// One way of living on the money you have (`FinanceAdvisorScenario`).
class AdvisorScenario {
  const AdvisorScenario(this.kind, this.monthly, this.days, this.runsOutOn);
  final String kind, monthly;
  final int? days;
  final String? runsOutOn;
  factory AdvisorScenario.fromJson(JsonReader r) => AdvisorScenario(
      r.string('kind'),
      decOr(r, 'monthly_outflow'),
      r.optInt('days'),
      r.optString('runs_out_on'));
}

/// A week or month of spending against the one before (`FinanceAdvisorPeriod`).
class AdvisorPeriod {
  const AdvisorPeriod(this.amount, this.previous, this.changePercent);
  final String amount, previous;
  final num? changePercent;
  factory AdvisorPeriod.fromJson(JsonReader r) => AdvisorPeriod(
      decOr(r, 'amount'), decOr(r, 'previous'), r.optNumber('change_percent'));
}

class AdvisorComing {
  const AdvisorComing(
      {required this.kind,
      required this.name,
      required this.amount,
      this.dueOn,
      this.detected = false});
  final String kind, name, amount;
  final String? dueOn;
  final bool detected;
  factory AdvisorComing.fromJson(JsonReader r) => AdvisorComing(
      kind: r.string('kind'),
      name: r.string('name'),
      amount: decOr(r, 'amount'),
      dueOn: r.optString('due_on'),
      detected: r.optBool('detected') ?? false);
}

/// Everything about one currency (`FinanceAdvisorCurrency`).
class AdvisorCurrency {
  const AdvisorCurrency({
    required this.currency,
    required this.runwayStatus,
    required this.liquid,
    required this.scenarios,
    required this.week,
    required this.month,
    required this.coming,
    required this.comingTotal,
    required this.within7,
  });
  final String currency, runwayStatus, liquid, comingTotal, within7;
  final List<AdvisorScenario> scenarios;
  final AdvisorPeriod? week, month;
  final List<AdvisorComing> coming;

  factory AdvisorCurrency.fromJson(JsonReader r) {
    final runway = r.optObject('runway');
    final spending = r.optObject('spending');
    final coming = r.optObject('coming');
    return AdvisorCurrency(
      currency: r.string('currency'),
      runwayStatus: runway?.optString('status') ?? 'no_records',
      liquid: runway == null ? '0' : decOr(runway, 'liquid'),
      scenarios:
          runway?.optObjects('scenarios', AdvisorScenario.fromJson) ?? const [],
      week: spending?.optObject('week') == null
          ? null
          : AdvisorPeriod.fromJson(spending!.object('week')),
      month: spending?.optObject('month') == null
          ? null
          : AdvisorPeriod.fromJson(spending!.object('month')),
      coming: coming?.optObjects('items', AdvisorComing.fromJson) ?? const [],
      comingTotal: coming == null ? '0' : decOr(coming, 'total'),
      within7: coming == null ? '0' : decOr(coming, 'within_7_days'),
    );
  }
}

/// One card's advice (`FinanceAdvisorCard`).
class AdvisorCard {
  const AdvisorCard({
    required this.name,
    required this.currency,
    required this.owed,
    required this.neverClears,
    required this.missing,
    this.last4,
    this.available,
    this.minimum,
    this.dueOn,
    this.apr,
    this.interestIfMinimum,
    this.monthsToClear,
  });
  final String name, currency, owed;
  final String? last4, available, minimum, dueOn, apr, interestIfMinimum;
  final int? monthsToClear;
  final bool neverClears;
  final List<String> missing;

  factory AdvisorCard.fromJson(JsonReader r) => AdvisorCard(
        name: r.string('name'),
        currency: r.string('currency'),
        last4: r.optString('last4'),
        owed: decOr(r, 'owed'),
        available: dec(r, 'available'),
        minimum: dec(r, 'minimum_payment'),
        dueOn: r.optString('due_on'),
        apr: dec(r, 'apr_percent'),
        interestIfMinimum: dec(r, 'interest_if_minimum'),
        monthsToClear: r.optInt('months_to_clear_at_minimum'),
        neverClears: r.optBool('never_clears_at_minimum') ?? false,
        missing: r.optStrings('missing') ?? const [],
      );
}

/// One next step, already written in English and Arabic by the API.
class AdvisorAction {
  const AdvisorAction(this.code, this.severity, this.textEn, this.textAr);
  final String code, severity, textEn, textAr;
  String text(bool arabic) => arabic && textAr.isNotEmpty ? textAr : textEn;
  factory AdvisorAction.fromJson(JsonReader r) => AdvisorAction(
      r.string('code'),
      r.optString('severity') ?? 'info',
      r.optString('text_en') ?? '',
      r.optString('text_ar') ?? '');
}

/// A weekly note (`FinanceAdvisorNoteView` with its `note`).
class AdvisorNoteData {
  const AdvisorNoteData({
    required this.id,
    required this.weekStart,
    required this.weekEnd,
    required this.trigger,
    required this.historyDays,
    required this.currencies,
    required this.cards,
    required this.actions,
    required this.noticeEn,
    required this.noticeAr,
    required this.salaryFound,
    this.generatedAt,
  });
  final String id, weekStart, weekEnd, trigger, noticeEn, noticeAr;
  final String? generatedAt;
  final int historyDays;
  final bool salaryFound;
  final List<AdvisorCurrency> currencies;
  final List<AdvisorCard> cards;
  final List<AdvisorAction> actions;

  String notice(bool arabic) =>
      arabic && noticeAr.isNotEmpty ? noticeAr : noticeEn;

  factory AdvisorNoteData.fromJson(JsonReader r) {
    final note = r.object('note');
    return AdvisorNoteData(
      id: r.string('id'),
      weekStart: r.string('week_start'),
      weekEnd: note.optString('week_end') ?? '',
      trigger: r.optString('trigger') ?? 'schedule',
      generatedAt: r.optString('generated_at'),
      historyDays: note.optInt('history_days') ?? 0,
      currencies: note.optObjects('currencies', AdvisorCurrency.fromJson),
      cards: note.optObjects('cards', AdvisorCard.fromJson),
      actions: note.optObjects('actions', AdvisorAction.fromJson),
      noticeEn: note.optString('notice_en') ?? '',
      noticeAr: note.optString('notice_ar') ?? '',
      salaryFound: note.optObject('income')?.optBool('salary_found') ?? false,
    );
  }
}

class AdvisorOverviewData {
  const AdvisorOverviewData(
      {required this.settings,
      required this.timezone,
      this.nextNoteOn,
      this.latest});
  final AdvisorSettingsData settings;
  final String timezone;
  final String? nextNoteOn;
  final AdvisorNoteData? latest;

  factory AdvisorOverviewData.fromJson(JsonReader r) => AdvisorOverviewData(
        settings: AdvisorSettingsData.fromJson(r.object('settings')),
        timezone: r.optString('timezone') ?? '',
        nextNoteOn: r.optString('next_note_on'),
        latest: r.optObject('latest') == null
            ? null
            : AdvisorNoteData.fromJson(r.object('latest')),
      );
}

/// A past note in the history list (`FinanceAdvisorNoteSummary`).
class AdvisorNoteRow {
  const AdvisorNoteRow(
      {required this.id,
      required this.weekStart,
      required this.trigger,
      required this.actions,
      required this.urgent,
      required this.survival});
  final String id, weekStart, trigger;
  final int actions, urgent;

  /// Days per currency, null when no end date.
  final Map<String, int?> survival;

  factory AdvisorNoteRow.fromJson(JsonReader r) => AdvisorNoteRow(
        id: r.string('id'),
        weekStart: r.string('week_start'),
        trigger: r.optString('trigger') ?? 'schedule',
        actions: r.optInt('actions') ?? 0,
        urgent: r.optInt('urgent') ?? 0,
        survival: {
          for (final s in r.optObjects('survival', (j) => j))
            s.string('currency'): s.optInt('days'),
        },
      );
}

// -- the Plan: can I afford it, cash or a loan ------------------------------------

class AffordAnswer {
  const AffordAnswer({
    required this.currency,
    required this.price,
    required this.verdict,
    required this.liquid,
    required this.commitments,
    required this.payWith,
    required this.avoid,
    this.safeToSpend,
    this.income,
    this.ratioAfter,
    this.monthsLeft,
  });
  final String currency, price, verdict, liquid, commitments;
  final String? safeToSpend, income, ratioAfter, monthsLeft;
  final List<({String name, String balance, String currency, String note})>
      payWith;
  final List<({String code, Map<String, String> params})> avoid;

  factory AffordAnswer.fromJson(JsonReader r) => AffordAnswer(
        currency: r.string('currency'),
        price: decOr(r, 'price'),
        verdict: r.string('verdict'),
        liquid: decOr(r, 'liquid'),
        commitments: decOr(r, 'commitments_monthly'),
        safeToSpend: dec(r, 'safe_to_spend'),
        income: dec(r, 'income_monthly'),
        ratioAfter: dec(r, 'ratio_after'),
        monthsLeft: dec(r, 'months_left_after_cash'),
        payWith: [
          for (final p in r.optObjects('pay_with', (j) => j))
            (
              name: p.string('name'),
              balance: decOr(p, 'balance'),
              currency: p.string('currency'),
              note: p.optString('note') ?? '',
            )
        ],
        avoid: [
          for (final a in r.optObjects('avoid', (j) => j))
            (
              code: a.string('code'),
              params: {
                for (final e in (a.optMap('params') ?? const {}).entries)
                  e.key: '${e.value}',
                if (a.optString('currency') != null)
                  'currency': a.optString('currency')!,
              },
            )
        ],
      );
}

class CompareOption {
  const CompareOption({
    required this.kind,
    required this.label,
    required this.months,
    required this.status,
    this.monthly,
    this.totalCost,
    this.interest,
    this.ratePercent,
  });
  final String kind, label, status;
  final int months;
  final String? monthly, totalCost, interest, ratePercent;

  factory CompareOption.fromJson(JsonReader r) => CompareOption(
        kind: r.string('kind'),
        label: r.optString('label') ?? '',
        months: r.optInt('months') ?? 0,
        status: r.optString('status') ?? 'ok',
        monthly: dec(r, 'monthly'),
        totalCost: dec(r, 'total_cost'),
        interest: dec(r, 'interest'),
        ratePercent: dec(r, 'rate_percent'),
      );
}

// -- payslips ----------------------------------------------------------------------

const deductionKinds = [
  'social_insurance',
  'medical_insurance',
  'loan',
  'advance',
  'tax',
  'other'
];

class Deduction {
  const Deduction(this.kind, this.amount, [this.label]);
  final String kind, amount;
  final String? label;
  Map<String, Object?> toJson() =>
      {'kind': kind, 'amount': amount, if (label != null) 'label': label};
  factory Deduction.fromJson(JsonReader r) =>
      Deduction(r.string('kind'), decOr(r, 'amount'), r.optString('label'));
}

class Payslip {
  const Payslip({
    required this.id,
    required this.employer,
    required this.month,
    required this.currency,
    required this.gross,
    required this.deductions,
    required this.totalDeductions,
    required this.net,
    required this.received,
    required this.difference,
    required this.status,
    this.paidOn,
    this.notes,
    this.movements = const [],
  });
  final String id, employer, month, currency, gross, totalDeductions, net;
  final String received, difference, status;
  final String? paidOn, notes;
  final List<Deduction> deductions;
  final List<LoanMovement> movements;

  factory Payslip.fromJson(JsonReader r) => Payslip(
        id: r.string('id'),
        employer: r.string('employer'),
        month: r.string('month'),
        paidOn: r.optString('paid_on'),
        currency: r.string('currency'),
        gross: decOr(r, 'gross'),
        deductions: r.optObjects('deductions', Deduction.fromJson),
        notes: r.optString('notes'),
        totalDeductions: decOr(r, 'total_deductions'),
        net: decOr(r, 'net'),
        received: decOr(r, 'received'),
        difference: decOr(r, 'difference'),
        status: r.optString('status') ?? 'not_linked',
        movements: r.optObjects('movements', LoanMovement.fromJson),
      );
}

// -- transfers home ----------------------------------------------------------------

class Remittance {
  const Remittance({
    required this.id,
    required this.sentOn,
    required this.amount,
    required this.currency,
    required this.fee,
    required this.recipient,
    required this.provider,
    required this.regular,
    required this.totalPaid,
    required this.linked,
    this.fxRate,
    this.receivedAmount,
    this.receivedCurrency,
    this.notes,
    this.feePercent,
    this.effectiveRate,
    this.movements = const [],
  });
  final String id, sentOn, amount, currency, fee, recipient, provider;
  final String totalPaid, linked;
  final bool regular;
  final String? fxRate, receivedAmount, receivedCurrency, notes;
  final String? feePercent, effectiveRate;
  final List<LoanMovement> movements;

  factory Remittance.fromJson(JsonReader r) => Remittance(
        id: r.string('id'),
        sentOn: r.string('sent_on'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        fee: decOr(r, 'fee'),
        fxRate: dec(r, 'fx_rate'),
        receivedAmount: dec(r, 'received_amount'),
        receivedCurrency: r.optString('received_currency'),
        recipient: r.string('recipient'),
        provider: r.string('provider'),
        regular: r.optBool('regular') ?? false,
        notes: r.optString('notes'),
        totalPaid: decOr(r, 'total_paid'),
        feePercent: dec(r, 'fee_percent'),
        effectiveRate: dec(r, 'effective_rate'),
        linked: decOr(r, 'linked'),
        movements: r.optObjects('movements', LoanMovement.fromJson),
      );
}

/// One provider and corridor compared from the person's own transfers.
class RemitProvider {
  const RemitProvider(
      {required this.provider,
      required this.corridor,
      required this.transfers,
      this.averageFeePercent,
      this.bestRate,
      this.latestRate});
  final String provider, corridor;
  final int transfers;
  final String? averageFeePercent, bestRate, latestRate;
  factory RemitProvider.fromJson(JsonReader r) => RemitProvider(
        provider: r.string('provider'),
        corridor: r.string('corridor'),
        transfers: r.optInt('transfers') ?? 0,
        averageFeePercent: dec(r, 'average_fee_percent'),
        bestRate: dec(r, 'best_effective_rate'),
        latestRate: dec(r, 'latest_effective_rate'),
      );
}
