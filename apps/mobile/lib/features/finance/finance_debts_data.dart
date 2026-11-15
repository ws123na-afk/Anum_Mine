import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'finance_more_data.dart' show dec, decOr;

/// The personal debt overview, credit cards, promises to pay, financial
/// alerts and the Money Advisor's answers on the phone
/// (docs/finance.md#debt-overview, #credit-cards, #financial-alerts,
/// #advisor-answers). What the phone reads and sends, mirroring the web's
/// `lib/financeDebts.ts`. Money stays a decimal string next to its currency;
/// nothing is added across currencies and every figure is the API's.

class DebtHistoryItem {
  const DebtHistoryItem(
      {this.on,
      required this.amount,
      required this.currency,
      required this.kind,
      required this.evidence,
      this.refId,
      this.note});
  final String? on, refId, note;
  final String amount, currency, kind, evidence;

  factory DebtHistoryItem.fromJson(JsonReader r) => DebtHistoryItem(
        on: r.optString('on'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        kind: r.string('kind'),
        evidence: r.string('evidence'),
        refId: r.optString('ref_id'),
        note: r.optString('note'),
      );
}

class DebtPromise {
  const DebtPromise(
      {required this.id,
      required this.madeOn,
      required this.dueBy,
      required this.amount,
      this.note,
      required this.status,
      required this.paid});
  final String id, madeOn, dueBy, amount, status, paid;
  final String? note;

  factory DebtPromise.fromJson(JsonReader r) => DebtPromise(
        id: r.string('id'),
        madeOn: r.string('made_on'),
        dueBy: r.string('due_by'),
        amount: decOr(r, 'amount'),
        note: r.optString('note'),
        status: r.optString('status') ?? 'pending',
        paid: decOr(r, 'paid'),
      );
}

/// One obligation of the overview (`debt_overview.Obligation`).
class Obligation {
  const Obligation({
    required this.id,
    required this.refType,
    required this.refId,
    required this.type,
    required this.direction,
    required this.name,
    this.counterparty,
    required this.currency,
    this.originalAmount,
    required this.interest,
    this.rate,
    this.expectedInstallment,
    required this.paid,
    required this.remaining,
    this.interestPaid,
    this.fees,
    this.nextDueOn,
    required this.nextDueAmount,
    required this.overdueAmount,
    required this.status,
    this.verifiedBalance,
    this.verifiedOn,
    this.difference,
    required this.discrepancy,
    this.disputedAmount,
    required this.unverifiedAmount,
    this.payoffOn,
    this.assumptions = const [],
    this.history = const [],
    this.promises = const [],
    this.sources = const {},
    this.missing = const [],
  });
  final String id, refType, refId, type, direction, name, currency, interest;
  final String paid, remaining, nextDueAmount, overdueAmount, status;
  final String unverifiedAmount;
  final String? counterparty, originalAmount, rate, expectedInstallment;
  final String? interestPaid, fees, nextDueOn, verifiedBalance, verifiedOn;
  final String? difference, disputedAmount, payoffOn;
  final bool discrepancy;
  final List<String> assumptions, missing;
  final List<DebtHistoryItem> history;
  final List<DebtPromise> promises;

  /// figure: source ("remaining": "ledger").
  final Map<String, String> sources;

  bool get open => status != 'paid_off' && status != 'closed';
  bool get owe => direction == 'owe';

  factory Obligation.fromJson(JsonReader r) => Obligation(
        id: r.string('id'),
        refType: r.string('ref_type'),
        refId: r.string('ref_id'),
        type: r.string('type'),
        direction: r.string('direction'),
        name: r.string('name'),
        counterparty: r.optString('counterparty'),
        currency: r.string('currency'),
        originalAmount: dec(r, 'original_amount'),
        interest: r.string('interest'),
        rate: dec(r, 'rate'),
        expectedInstallment: dec(r, 'expected_installment'),
        paid: decOr(r, 'paid'),
        remaining: decOr(r, 'remaining'),
        interestPaid: dec(r, 'interest_paid'),
        fees: dec(r, 'fees'),
        nextDueOn: r.optString('next_due_on'),
        nextDueAmount: decOr(r, 'next_due_amount'),
        overdueAmount: decOr(r, 'overdue_amount'),
        status: r.string('status'),
        verifiedBalance: dec(r, 'verified_balance'),
        verifiedOn: r.optString('verified_on'),
        difference: dec(r, 'difference'),
        discrepancy: r.optBool('discrepancy') ?? false,
        disputedAmount: dec(r, 'disputed_amount'),
        unverifiedAmount: decOr(r, 'unverified_amount'),
        payoffOn: r.optString('payoff_on'),
        assumptions: [
          for (final a in r.optList('assumptions') ?? const []) '$a'
        ],
        history: r.optObjects('history', DebtHistoryItem.fromJson),
        promises: r.optObjects('promises', DebtPromise.fromJson),
        sources: {
          for (final s in r.optObjects('sources', (x) => x))
            s.string('figure'): s.string('source')
        },
        missing: [for (final m in r.optList('missing') ?? const []) '$m'],
      );
}

class DebtCalendarItem {
  const DebtCalendarItem(
      {required this.on,
      required this.obligationId,
      required this.name,
      required this.amount,
      required this.currency,
      required this.direction,
      required this.status});
  final String on, obligationId, name, amount, currency, direction, status;

  factory DebtCalendarItem.fromJson(JsonReader r) => DebtCalendarItem(
        on: r.string('on'),
        obligationId: r.string('obligation_id'),
        name: r.string('name'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        direction: r.string('direction'),
        status: r.string('status'),
      );
}

class DebtPlanRow {
  const DebtPlanRow(
      {required this.rank,
      required this.obligationId,
      required this.name,
      required this.currency,
      required this.remaining,
      this.rate,
      required this.reason});
  final int rank;
  final String obligationId, name, currency, remaining, reason;
  final String? rate;

  factory DebtPlanRow.fromJson(JsonReader r) => DebtPlanRow(
        rank: r.integer('rank'),
        obligationId: r.string('obligation_id'),
        name: r.string('name'),
        currency: r.string('currency'),
        remaining: decOr(r, 'remaining'),
        rate: dec(r, 'rate'),
        reason: r.string('reason'),
      );
}

class DebtTotal {
  const DebtTotal(
      {required this.currency,
      required this.owe,
      required this.owedToMe,
      required this.overdue,
      required this.due30});
  final String currency, owe, owedToMe, overdue, due30;

  factory DebtTotal.fromJson(JsonReader r) => DebtTotal(
        currency: r.string('currency'),
        owe: decOr(r, 'owe'),
        owedToMe: decOr(r, 'owed_to_me'),
        overdue: decOr(r, 'overdue'),
        due30: decOr(r, 'due_30_days'),
      );
}

class DebtOverview {
  const DebtOverview(
      {required this.today,
      required this.items,
      required this.totals,
      required this.calendar,
      required this.plan,
      required this.discrepancies});
  final String today;
  final List<Obligation> items;
  final List<DebtTotal> totals;
  final List<DebtCalendarItem> calendar;
  final List<DebtPlanRow> plan;
  final int discrepancies;

  List<Obligation> get owe => [
        for (final o in items)
          if (o.owe && o.open) o
      ];
  List<Obligation> get owedToMe => [
        for (final o in items)
          if (!o.owe && o.open) o
      ];
  List<Obligation> get finished => [
        for (final o in items)
          if (!o.open) o
      ];

  factory DebtOverview.fromJson(JsonReader r) => DebtOverview(
        today: r.string('today'),
        items: r.objects('items', Obligation.fromJson),
        totals: r.objects('totals', DebtTotal.fromJson),
        calendar: r.objects('calendar', DebtCalendarItem.fromJson),
        plan: r.objects('plan', DebtPlanRow.fromJson),
        discrepancies: r.optInt('discrepancies') ?? 0,
      );
}

class CardPayment {
  const CardPayment(
      {required this.transactionId,
      required this.postedOn,
      required this.amount,
      required this.evidence,
      required this.linked,
      this.fromAccountName});
  final String transactionId, postedOn, amount, evidence;
  final bool linked;
  final String? fromAccountName;

  factory CardPayment.fromJson(JsonReader r) => CardPayment(
        transactionId: r.string('transaction_id'),
        postedOn: r.string('posted_on'),
        amount: decOr(r, 'amount'),
        evidence: r.string('evidence'),
        linked: r.optBool('linked') ?? false,
        fromAccountName: r.optString('from_account_name'),
      );
}

/// One credit card account (`credit_cards.CardView`).
class CreditCardView {
  const CreditCardView({
    required this.accountId,
    this.debtId,
    required this.name,
    this.last4,
    required this.currency,
    required this.outstanding,
    this.limit,
    this.available,
    this.utilization,
    this.apr,
    this.statementDay,
    this.statementOn,
    this.statementBalance,
    this.statementMinimum,
    this.dueOn,
    required this.paidSinceStatement,
    required this.paidProvisional,
    required this.paymentState,
    required this.checkStatus,
    this.checkDifference,
    this.checkLedger,
    this.payFull,
    this.payMinimum,
    this.interestEstimate,
    this.monthsToClear,
    required this.neverClears,
    required this.cycle,
    required this.payments,
    required this.evidence,
    required this.missing,
  });
  final String accountId, name, currency, outstanding;
  final String paidSinceStatement, paidProvisional, paymentState, checkStatus;
  final String? debtId, last4, limit, available, apr, statementOn;
  final String? statementBalance, statementMinimum, dueOn, checkDifference;
  final String? checkLedger, payFull, payMinimum, interestEstimate;
  final int? utilization, statementDay, monthsToClear;
  final bool neverClears;

  /// purchases, refunds, cashback, fees, interest, cash_advances,
  /// installment_conversions, payments.
  final Map<String, String> cycle;
  final List<CardPayment> payments;

  /// posted, pending, unverified.
  final Map<String, int> evidence;
  final List<String> missing;

  factory CreditCardView.fromJson(JsonReader r) {
    final options = r.optObjects('pay_options', (x) => x);
    JsonReader? option(String code) {
      for (final o in options) {
        if (o.string('code') == code) return o;
      }
      return null;
    }

    final minimum = option('minimum');
    final check = r.object('check');
    final cycle = r.object('cycle');
    final counts = r.optMap('evidence') ?? const {};
    return CreditCardView(
      accountId: r.string('account_id'),
      debtId: r.optString('debt_id'),
      name: r.string('name'),
      last4: r.optString('last4'),
      currency: r.string('currency'),
      outstanding: decOr(r, 'outstanding'),
      limit: dec(r, 'limit'),
      available: dec(r, 'available'),
      utilization: r.optInt('utilization_percent'),
      apr: dec(r, 'apr_percent'),
      statementDay: r.optInt('statement_day'),
      statementOn: r.optString('statement_on'),
      statementBalance: dec(r, 'statement_balance'),
      statementMinimum: dec(r, 'statement_minimum'),
      dueOn: r.optString('due_on'),
      paidSinceStatement: decOr(r, 'paid_since_statement'),
      paidProvisional: decOr(r, 'paid_provisional'),
      paymentState: r.string('payment_state'),
      checkStatus: check.string('status'),
      checkDifference: dec(check, 'difference'),
      checkLedger: dec(check, 'ledger_balance'),
      payFull: option('full') == null ? null : dec(option('full')!, 'amount'),
      payMinimum: minimum == null ? null : dec(minimum, 'amount'),
      interestEstimate:
          minimum == null ? null : dec(minimum, 'interest_estimate'),
      monthsToClear: minimum?.optInt('months_to_clear'),
      neverClears: minimum?.optBool('never_clears') ?? false,
      cycle: {
        for (final k in const [
          'purchases',
          'refunds',
          'cashback',
          'fees',
          'interest',
          'cash_advances',
          'installment_conversions',
          'payments'
        ])
          k: decOr(cycle, k)
      },
      payments: r.optObjects('payments', CardPayment.fromJson),
      evidence: {
        for (final e in counts.entries)
          e.key: e.value is num ? (e.value as num).toInt() : 0
      },
      missing: [for (final m in r.optList('missing') ?? const []) '$m'],
    );
  }
}

/// Where a financial alert can go (docs/finance.md#alert-channels). A push or
/// an email carries the alert's fixed words only, never the details.
const alertChannels = ['in_app', 'push', 'email'];

/// One alert kind and its channels; [on] is the in-app switch.
class AlertKindSetting {
  const AlertKindSetting(
      {required this.kind,
      required this.on,
      this.push = false,
      this.email = false});
  final String kind;
  final bool on, push, email;

  bool channel(String name) => switch (name) {
        'push' => push,
        'email' => email,
        _ => on,
      };

  AlertKindSetting withChannel(String name, bool value) => AlertKindSetting(
      kind: kind,
      on: name == 'in_app' ? value : on,
      push: name == 'push' ? value : push,
      email: name == 'email' ? value : email);

  static AlertKindSetting read(JsonReader r) {
    final on = r.optBool('in_app') ?? r.optBool('on') ?? true;
    return AlertKindSetting(
        kind: r.string('kind'),
        on: on,
        push: r.optBool('push') ?? false,
        email: r.optBool('email') ?? false);
  }
}

/// Whether a channel can be used now and, if not, why (`not_configured`,
/// `push_off`, `no_device`, `email_off`, `no_address`).
class AlertChannelInfo {
  const AlertChannelInfo({required this.available, this.reason});
  final bool available;
  final String? reason;
}

class AlertSettingsView {
  const AlertSettingsView({required this.kinds, this.channels = const {}});
  final List<AlertKindSetting> kinds;
  final Map<String, AlertChannelInfo> channels;

  /// The in-app channel always can; an older server that names no channels
  /// leaves push and email unusable rather than promising them.
  bool usable(String channel) =>
      channel == 'in_app' || (channels[channel]?.available ?? false);

  /// The string key saying why a channel cannot be used, or null.
  String? reasonKey(String channel) {
    if (channel == 'in_app' || channels.isEmpty) return null;
    final info = channels[channel];
    if (info == null || info.available) return null;
    return 'alerts.why.$channel.${info.reason ?? 'not_configured'}';
  }

  AlertSettingsView withKinds(List<AlertKindSetting> next) =>
      AlertSettingsView(kinds: next, channels: channels);

  static AlertSettingsView read(JsonReader r) {
    final found = r.optObject('channels');
    return AlertSettingsView(
      kinds: r.objects('kinds', AlertKindSetting.read),
      channels: {
        if (found != null)
          for (final c in alertChannels)
            if (found.optObject(c) case final info?)
              c: AlertChannelInfo(
                  available: info.optBool('available') ?? false,
                  reason: info.optString('reason')),
      },
    );
  }
}

/// The PUT body: every kind's channels, plus the in-app switch older
/// servers read.
Map<String, Object?> alertSettingsBody(List<AlertKindSetting> kinds) => {
      'kinds': {for (final k in kinds) k.kind: k.on},
      'channels': {
        for (final k in kinds)
          k.kind: {'in_app': k.on, 'push': k.push, 'email': k.email}
      },
    };

class AlertPreviewItem {
  const AlertPreviewItem(
      {required this.kind, required this.body, required this.on});
  final String kind, body, on;
}

class AnswerRecord {
  const AnswerRecord(
      {required this.label,
      this.on,
      this.amount,
      this.currency,
      this.evidence});
  final String label;
  final String? on, amount, currency, evidence;
}

/// One Money Advisor answer (`advisor_answers.Answer`).
class AdvisorAnswer {
  const AdvisorAnswer(
      {required this.question,
      required this.asOf,
      required this.answer,
      required this.calculation,
      required this.records,
      required this.missing,
      required this.complete,
      required this.figures});
  final String question, asOf, answer;
  final List<String> calculation, missing;
  final List<AnswerRecord> records;
  final List<(String label, String amount, String currency)> figures;
  final bool complete;

  factory AdvisorAnswer.fromJson(JsonReader r) => AdvisorAnswer(
        question: r.string('question'),
        asOf: r.string('as_of'),
        answer: r.string('answer'),
        calculation: [
          for (final c in r.optList('calculation') ?? const []) '$c'
        ],
        missing: [for (final m in r.optList('missing') ?? const []) '$m'],
        complete: r.optBool('complete') ?? false,
        figures: [
          for (final f in r.optObjects('figures', (x) => x))
            (f.string('label'), decOr(f, 'amount'), f.string('currency'))
        ],
        records: [
          for (final x in r.optObjects('records', (x) => x))
            AnswerRecord(
                label: x.string('label'),
                on: x.optString('on'),
                amount: dec(x, 'amount'),
                currency: x.optString('currency'),
                evidence: x.optString('evidence'))
        ],
      );
}

const advisorQuestions = [
  'cash',
  'owe',
  'due_week',
  'installment_today',
  'card_missing',
  'category_spend',
  'words_spend',
  'sent_home',
  'owed_to_me',
  'loan_priority',
  'after_salary',
  'afford',
  'subscriptions_rose',
  'uncategorized',
  'stale_sources',
  'backfill_changes',
];

/// The parameters each question uses; the others are never sent.
const questionParams = <String, List<String>>{
  'category_spend': ['category', 'month'],
  'words_spend': ['words', 'month'],
  'sent_home': ['country', 'currency', 'month'],
  'afford': ['amount', 'currency'],
};

class FinanceDebtsRepository {
  FinanceDebtsRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));

  Future<DebtOverview> overview() async =>
      DebtOverview.fromJson(await _get('/debt-overview'));

  Future<List<CreditCardView>> cards() async =>
      (await _get('/cards')).objects('items', CreditCardView.fromJson);

  Future<void> saveProfile(String debtId, Map<String, Object?> body) =>
      api.request('PUT', '$base/debts/${_id(debtId)}/profile', body: body);

  Future<String> addCardTerms(CreditCardView card) async {
    final made = JsonReader.of(await api.request('POST', '$base/debts', body: {
      'name': card.name,
      'kind': 'card',
      'balance': '0',
      'currency': card.currency,
      'account_id': card.accountId,
    }));
    return made.string('id');
  }

  Future<void> addPromise(String loanId, Map<String, Object?> body) =>
      api.request('POST', '$base/people-loans/${_id(loanId)}/promises',
          body: body);

  Future<void> removePromise(String loanId, String promiseId) => api.request(
      'DELETE', '$base/people-loans/${_id(loanId)}/promises/${_id(promiseId)}');

  Future<void> saveAgreedBalance(String loanId, String? balance) =>
      api.request('PUT', '$base/people-loans/${_id(loanId)}/agreed-balance',
          body: {'balance': balance});

  Future<AlertSettingsView> alertSettings() async =>
      AlertSettingsView.read(await _get('/alerts/settings'));

  Future<AlertSettingsView> saveAlertSettings(
          List<AlertKindSetting> kinds) async =>
      AlertSettingsView.read(JsonReader.of(await api.request(
          'PUT', '$base/alerts/settings',
          body: alertSettingsBody(kinds))));

  Future<List<AlertPreviewItem>> alertPreview() async =>
      (await _get('/alerts/preview')).objects(
          'items',
          (r) => AlertPreviewItem(
              kind: r.string('kind'),
              body: r.string('body'),
              on: r.string('on')));

  Future<AdvisorAnswer> answer(String question, Map<String, String> params) {
    final used = questionParams[question] ?? const [];
    final query = <String, String>{'question': question};
    for (final key in used) {
      final value = params[key]?.trim() ?? '';
      if (value.isNotEmpty) query[key] = value;
    }
    final text = query.entries
        .map((e) =>
            '${Uri.encodeQueryComponent(e.key)}=${Uri.encodeQueryComponent(e.value)}')
        .join('&');
    return _get('/advisor/answer?$text').then(AdvisorAnswer.fromJson);
  }
}
