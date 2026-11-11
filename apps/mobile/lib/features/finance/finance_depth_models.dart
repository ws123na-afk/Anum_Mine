import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'finance_models.dart';

/// Finance settings, the emergency reserve, card FX details and the
/// exception notices on the phone (docs/finance.md#finance-settings,
/// #emergency-reserve, #card-fx-and-fees, #exception-notices). Every figure
/// is the API's: the phone never works out a rate, and every change goes
/// through the existing audited routes.

/// A decimal the API may send as a string or a number, or null.
String? _dec(JsonReader r, String key) => switch (r.json[key]) {
      final String s => s,
      final num n => '$n',
      _ => null,
    };

/// Main currency, Finance time zone and the statement keyword rules
/// (`GET /settings`). The phone keeps the rules as they are when it saves.
class FinanceSettingsData {
  const FinanceSettingsData(
      {required this.baseCurrency,
      required this.timezone,
      this.rules = const []});
  final String baseCurrency, timezone;

  /// The keyword rules exactly as read (`{match, category}`): sent back
  /// unchanged by the currency form, replaced by the keyword rules page.
  final List<Map<String, Object?>> rules;

  factory FinanceSettingsData.fromJson(JsonReader r) => FinanceSettingsData(
        baseCurrency: r.optString('base_currency') ?? 'AED',
        timezone: r.optString('timezone') ?? 'Asia/Dubai',
        rules: [
          for (final rule in r.optObjects('category_rules', (j) => j))
            {
              'match': rule.optString('match'),
              'category': rule.optString('category')
            }
        ],
      );

  /// The `PUT /settings` body with a new currency and time zone.
  Map<String, Object?> body(
          {String? currency,
          String? timezone,
          List<Map<String, Object?>>? rules}) =>
      {
        'base_currency': (currency ?? baseCurrency).toUpperCase(),
        'timezone': timezone ?? this.timezone,
        'category_rules': rules ?? this.rules,
      };
}

/// The Safe to spend plan (`GET /plan`): the buffer per currency.
class FinancePlanData {
  const FinancePlanData(
      {this.buffers = const [], this.combine = false, this.fxMaxAgeDays = 7});
  final List<CurrencyAmount> buffers;
  final bool combine;
  final int fxMaxAgeDays;

  factory FinancePlanData.fromJson(JsonReader r) => FinancePlanData(
        buffers: [
          for (final b in r.optObjects('buffers', (j) => j))
            CurrencyAmount(b.string('currency'), _dec(b, 'amount') ?? '0'),
        ],
        combine: r.optBool('combine_currencies') ?? false,
        fxMaxAgeDays: r.optInt('fx_max_age_days') ?? 7,
      );

  /// The `PUT /plan` body with [currency]'s buffer set to [amount], or
  /// taken out when [amount] is null; every other setting is kept.
  Map<String, Object?> withBuffer(String currency, String? amount) {
    final rows = [
      for (final b in buffers)
        if (b.currency != currency)
          {'currency': b.currency, 'amount': b.amount},
      if (amount != null) {'currency': currency, 'amount': amount},
    ];
    return {
      'buffers': rows,
      'combine_currencies': combine,
      'fx_max_age_days': fxMaxAgeDays,
    };
  }
}

/// One emergency reserve (`FinanceReserve` of `GET /reserve`).
class ReserveDetail {
  const ReserveDetail({
    required this.goalId,
    required this.name,
    required this.currency,
    required this.mode,
    required this.target,
    required this.saved,
    required this.heldIn,
    required this.status,
    this.months,
    this.recalculatedTarget,
    this.essentialMonthly,
    this.progressPercent,
    this.monthsCovered,
    this.accountId,
    this.accountName,
    this.paused = false,
    this.monthlyContribution,
  });
  final String goalId, name, currency, mode, target, saved, heldIn, status;
  final int? months, progressPercent;
  final String? recalculatedTarget, essentialMonthly, monthsCovered;
  final String? accountId, accountName, monthlyContribution;
  final bool paused;

  /// 0 to 1 for a progress bar.
  double get fraction => ((progressPercent ?? 0).clamp(0, 100)) / 100;

  /// Today's recalculated target moved more than 5% from the saved one
  /// (the page offers to save it again), as on the web.
  bool get drifted {
    final now = recalculatedTarget;
    if (now == null) return false;
    final saved = minorUnits(target), fresh = minorUnits(now);
    if (saved == null || fresh == null) return false;
    if (saved <= BigInt.zero) return fresh > BigInt.zero;
    return (fresh - saved).abs() * BigInt.from(100) > saved * BigInt.from(5);
  }

  factory ReserveDetail.fromJson(JsonReader r) => ReserveDetail(
        goalId: r.string('goal_id'),
        name: r.optString('name') ?? '',
        currency: r.string('currency'),
        mode: r.optString('mode') ?? 'amount',
        months: r.optInt('months'),
        target: _dec(r, 'target') ?? '0',
        recalculatedTarget: _dec(r, 'recalculated_target'),
        essentialMonthly: _dec(r, 'essential_monthly'),
        saved: _dec(r, 'saved') ?? '0',
        progressPercent: r.optInt('progress_percent'),
        monthsCovered: _dec(r, 'months_covered'),
        heldIn: r.optString('held_in') ?? 'goal',
        accountId: r.optString('account_id'),
        accountName: r.optString('account_name'),
        paused: r.optBool('paused') ?? false,
        monthlyContribution: _dec(r, 'monthly_contribution'),
        status: r.optString('status') ?? 'building',
      );
}

/// A currency with essential spending and no reserve yet.
class ReserveSuggestion {
  const ReserveSuggestion(
      {required this.currency,
      required this.essentialMonthly,
      required this.months,
      required this.target});
  final String currency, essentialMonthly, target;
  final int months;

  factory ReserveSuggestion.fromJson(JsonReader r) => ReserveSuggestion(
        currency: r.string('currency'),
        essentialMonthly: _dec(r, 'essential_monthly') ?? '0',
        months: r.optInt('months') ?? 3,
        target: _dec(r, 'target') ?? '0',
      );
}

/// `GET /reserve`.
class ReserveBook {
  const ReserveBook(
      {this.reserves = const [],
      this.suggestions = const [],
      this.defaultMonths = 3});
  final List<ReserveDetail> reserves;
  final List<ReserveSuggestion> suggestions;
  final int defaultMonths;

  ReserveDetail? reserveIn(String currency) =>
      reserves.where((r) => r.currency == currency).firstOrNull;
  ReserveSuggestion? suggestionIn(String currency) =>
      suggestions.where((s) => s.currency == currency).firstOrNull;

  factory ReserveBook.fromJson(JsonReader r) => ReserveBook(
        reserves: r.optObjects('reserves', ReserveDetail.fromJson),
        suggestions: r.optObjects('suggestions', ReserveSuggestion.fromJson),
        defaultMonths: r.optInt('default_months') ?? 3,
      );
}

/// The parts of a goal a reserve update keeps (`PATCH /goals` replaces the
/// name, target date and notes, so they are sent back as they are).
class GoalRecord {
  const GoalRecord(
      {required this.id, required this.name, this.targetOn, this.notes});
  final String id, name;
  final String? targetOn, notes;

  factory GoalRecord.fromJson(JsonReader r) => GoalRecord(
        id: r.string('id'),
        name: r.optString('name') ?? '',
        targetOn: r.optString('target_on'),
        notes: r.optString('notes'),
      );
}

/// A wire decimal as whole minor units of its own decimals ("12.50" is
/// 1250 at two places), or null when it is not a plain decimal.
BigInt? minorUnits(String value, [int? places]) {
  final m = RegExp(r'^(-?)(\d+)(?:\.(\d+))?$').firstMatch(value.trim());
  if (m == null) return null;
  final fraction = m.group(3) ?? '';
  final scale = places ?? fraction.length;
  if (fraction.length > scale) return null;
  final digits = '${m.group(2)}${fraction.padRight(scale, '0')}';
  final n = BigInt.parse(digits);
  return m.group(1)!.isEmpty ? n : -n;
}

/// [months] times the essential monthly figure, exactly, with the figure's
/// own decimals ("1500.25" x 3 is "4500.75"); null when it cannot be worked
/// out (no essentials or months outside 1 to 24).
String? monthsTarget(String? essential, int months) {
  if (essential == null || months < 1 || months > 24) return null;
  final m = RegExp(r'^(\d+)(?:\.(\d+))?$').firstMatch(essential.trim());
  if (m == null) return null;
  final places = (m.group(2) ?? '').length;
  final units = minorUnits(essential, places);
  if (units == null || units <= BigInt.zero) return null;
  final total =
      (units * BigInt.from(months)).toString().padLeft(places + 1, '0');
  if (places == 0) return total;
  return '${total.substring(0, total.length - places)}.'
      '${total.substring(total.length - places)}';
}

/// The bank's FX fee of a transaction (`FeeView`).
class FxFee {
  const FxFee(
      {required this.amount,
      required this.currency,
      required this.source,
      required this.includedInBilled,
      required this.linked,
      this.transactionId,
      this.markupPercent});
  final String amount, currency, source;
  final bool includedInBilled, linked;
  final String? transactionId, markupPercent;

  factory FxFee.fromJson(JsonReader r) => FxFee(
        amount: _dec(r, 'amount') ?? '0',
        currency: r.string('currency'),
        source: r.optString('source') ?? 'fee_stated',
        transactionId: r.optString('transaction_id'),
        markupPercent: _dec(r, 'markup_percent'),
        includedInBilled: r.optBool('included_in_billed') ?? false,
        linked: r.optBool('linked') ?? false,
      );
}

/// An own transfer between accounts in two currencies (`TransferView`).
class FxTransfer {
  const FxTransfer(
      {required this.outAmount,
      required this.outCurrency,
      required this.inAmount,
      required this.inCurrency,
      required this.realisedRate,
      this.valuationRate,
      this.valuationDate,
      this.valuationSource,
      this.difference});
  final String outAmount, outCurrency, inAmount, inCurrency, realisedRate;
  final String? valuationRate, valuationDate, valuationSource, difference;

  /// gain, loss or none.
  String get tone {
    final d = difference;
    if (d == null) return 'none';
    final n = double.tryParse(d);
    if (n == null || n == 0) return 'none';
    return n > 0 ? 'gain' : 'loss';
  }

  factory FxTransfer.fromJson(JsonReader r) => FxTransfer(
        outAmount: _dec(r, 'out_amount') ?? '0',
        outCurrency: r.string('out_currency'),
        inAmount: _dec(r, 'in_amount') ?? '0',
        inCurrency: r.string('in_currency'),
        realisedRate: _dec(r, 'realised_rate') ?? '0',
        valuationRate: _dec(r, 'valuation_rate'),
        valuationDate: r.optString('valuation_date'),
        valuationSource: r.optString('valuation_source'),
        difference: _dec(r, 'difference'),
      );
}

/// `GET /transactions/{id}/fx` (`FinanceTransactionFx`).
class FxDetails {
  const FxDetails({
    required this.transactionId,
    required this.foreign,
    required this.rateKind,
    required this.split,
    required this.planHash,
    required this.canCorrect,
    this.originalAmount,
    this.originalCurrency,
    this.billedAmount,
    this.billedCurrency,
    this.purchaseAmount,
    this.rate,
    this.rateDate,
    this.rateSource,
    this.fee,
    this.notes = const [],
    this.transfer,
  });
  final String transactionId, rateKind, split, planHash;
  final bool foreign, canCorrect;
  final String? originalAmount, originalCurrency, billedAmount, billedCurrency;
  final String? purchaseAmount, rate, rateDate, rateSource;
  final FxFee? fee;
  final List<String> notes;
  final FxTransfer? transfer;

  /// Whether there is anything to show: a foreign purchase, a fee to read
  /// with it, or an own transfer in two currencies.
  bool get shows => foreign || fee != null || transfer != null;

  /// The rate is not on the row: only the billed amount exists. Never
  /// guessed.
  bool get rateNotStated => rate == null || rateKind == 'not_stated';

  /// What the owner may do: split an inline fee into its own bank fee line
  /// (`split`), point a separate fee row at this purchase (`link`), or
  /// nothing (null).
  String? get action {
    if (fee == null || !canCorrect) return null;
    if (split == 'available') return 'split';
    if (split == 'link') return 'link';
    return null;
  }

  factory FxDetails.fromJson(JsonReader r) {
    final fee = r.optObject('fee');
    final transfer = r.optObject('transfer');
    return FxDetails(
      transactionId: r.string('transaction_id'),
      foreign: r.optBool('foreign') ?? false,
      originalAmount: _dec(r, 'original_amount'),
      originalCurrency: r.optString('original_currency'),
      billedAmount: _dec(r, 'billed_amount'),
      billedCurrency: r.optString('billed_currency'),
      purchaseAmount: _dec(r, 'purchase_amount'),
      rate: _dec(r, 'rate'),
      rateKind: r.optString('rate_kind') ?? 'not_stated',
      rateDate: r.optString('rate_date'),
      rateSource: r.optString('rate_source'),
      fee: fee == null ? null : FxFee.fromJson(fee),
      split: r.optString('split') ?? 'none',
      notes: r.optStrings('notes') ?? const [],
      planHash: r.optString('plan_hash') ?? '',
      canCorrect: r.optBool('can_correct') ?? false,
      transfer: transfer == null ? null : FxTransfer.fromJson(transfer),
    );
  }
}

/// A rate as the server wrote it, without trailing zeros ("3.750000" reads
/// "3.75"); never rounded further.
String plainRate(String? value) {
  if (value == null || value.isEmpty) return '-';
  if (!value.contains('.')) return value;
  final trimmed =
      value.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
  return trimmed.isEmpty ? '0' : trimmed;
}

/// The exception notices the owner may turn on, in this order.
const noticeKinds = [
  'reconciliation_overdue',
  'statement_missing',
  'uncategorized_money',
  'business_unposted',
  'evidence_missing',
];

/// The owner's automation policy (`GET /automation`): kept whole, so a
/// change to the notices sends every other switch back as it was.
class AutomationPolicy {
  const AutomationPolicy(this.raw);
  final Map<String, Object?> raw;

  List<String> get notices => [
        for (final kind in noticeKinds)
          if ((raw['notices'] as List<Object?>? ?? const []).contains(kind))
            kind
      ];
  int get exceptionDays => switch (raw['notice_exception_days']) {
        final num n => n.toInt(),
        _ => 7,
      };
  String get uncategorizedMin => switch (raw['notice_uncategorized_min']) {
        final String s => s,
        final num n => '$n',
        _ => '500',
      };

  factory AutomationPolicy.fromJson(JsonReader r) =>
      AutomationPolicy(r.optMap('policy') ?? const {});

  /// The `PUT /automation` body with these notices and thresholds.
  Map<String, Object?> withNotices(List<String> on,
          {required int days, required String uncategorizedMin}) =>
      {
        ...raw,
        'notices': [
          for (final kind in noticeKinds)
            if (on.contains(kind)) kind
        ],
        'notice_exception_days': days,
        'notice_uncategorized_min': uncategorizedMin,
      };
}

/// The routes behind Finance settings, the reserve form, the FX section of
/// a transaction and the exception notices. Every record is the caller's
/// own; the server checks `finance:read` and `finance:manage`.
/// Whether a transaction is one side of an own transfer, and the other side.
class TransferPairOf {
  const TransferPairOf(
      {required this.paired,
      this.linkId,
      this.linkVersion,
      this.role,
      this.otherAccount,
      this.otherDay,
      this.otherAmount,
      this.otherCurrency,
      this.otherText});
  final bool paired;
  final String? linkId, role, otherAccount, otherDay, otherAmount;
  final String? otherCurrency, otherText;
  final int? linkVersion;

  factory TransferPairOf.fromJson(JsonReader r) {
    final o = r.optObject('other');
    final amount = o?.json['amount'];
    return TransferPairOf(
      paired: r.optBool('paired') ?? false,
      linkId: r.optString('link_id'),
      linkVersion: r.optInt('link_version'),
      role: r.optString('role'),
      otherAccount: o?.optString('account_name'),
      otherDay: o?.optString('posted_on'),
      otherAmount: amount?.toString(),
      otherCurrency: o?.optString('currency'),
      otherText: o?.optString('description'),
    );
  }
}

abstract interface class FinanceDepthRepository {
  Future<FinanceSettingsData> settings();
  Future<void> saveSettings(Map<String, Object?> body);
  Future<FinancePlanData> plan();
  Future<void> savePlan(Map<String, Object?> body);
  Future<ReserveBook> reserves();
  Future<List<GoalRecord>> goals();

  /// Creates the reserve ([goalId] null) or updates it, through the goal
  /// routes (`POST`, `PATCH /goals`).
  Future<void> saveReserve(String? goalId, Map<String, Object?> body);
  Future<FxDetails> fx(String transactionId);

  /// Separates or links the FX fee shown, with the view's `plan_hash`.
  /// Answers `split` or `link`.
  Future<String> separateFee(String transactionId, String planHash);

  /// The own transfer this transaction is one side of (docs/finance.md#own-transfers).
  Future<TransferPairOf> transferPair(String transactionId);

  /// Unpairs it by voiding the pair's link (the links API).
  Future<void> unpair(String linkId, int? version);
  Future<AutomationPolicy> automation();
  Future<AutomationPolicy> saveAutomation(Map<String, Object?> body);
}

class ApiFinanceDepthRepository implements FinanceDepthRepository {
  const ApiFinanceDepthRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<FinanceSettingsData> settings() async => FinanceSettingsData.fromJson(
      JsonReader.of(await api.request('GET', '$base/settings')));

  @override
  Future<void> saveSettings(Map<String, Object?> body) async {
    await api.request('PUT', '$base/settings', body: body);
  }

  @override
  Future<FinancePlanData> plan() async => FinancePlanData.fromJson(
      JsonReader.of(await api.request('GET', '$base/plan')));

  @override
  Future<void> savePlan(Map<String, Object?> body) async {
    await api.request('PUT', '$base/plan', body: body);
  }

  @override
  Future<ReserveBook> reserves() async => ReserveBook.fromJson(
      JsonReader.of(await api.request('GET', '$base/reserve')));

  @override
  Future<List<GoalRecord>> goals() async =>
      JsonReader.of(await api.request('GET', '$base/goals'))
          .optObjects('data', GoalRecord.fromJson);

  @override
  Future<void> saveReserve(String? goalId, Map<String, Object?> body) async {
    if (goalId == null) {
      await api.request('POST', '$base/goals', body: body);
    } else {
      await api.request('PATCH', '$base/goals/${_id(goalId)}', body: body);
    }
  }

  @override
  Future<FxDetails> fx(String transactionId) async =>
      FxDetails.fromJson(JsonReader.of(await api.request(
          'GET', '$base/transactions/${_id(transactionId)}/fx')));

  @override
  Future<String> separateFee(String transactionId, String planHash) async =>
      JsonReader.of(await api.request(
              'POST', '$base/transactions/${_id(transactionId)}/fx/separate',
              body: {'plan_hash': planHash}))
          .optString('mode') ??
      'split';

  @override
  Future<TransferPairOf> transferPair(String transactionId) async =>
      TransferPairOf.fromJson(JsonReader.of(await api.request(
          'GET', '$base/links/transfer?transaction_id=${_id(transactionId)}')));

  @override
  Future<void> unpair(String linkId, int? version) async {
    await api.request('POST', '$base/links/${_id(linkId)}/void',
        body: {if (version != null) 'version': version});
  }

  @override
  Future<AutomationPolicy> automation() async => AutomationPolicy.fromJson(
      JsonReader.of(await api.request('GET', '$base/automation')));

  @override
  Future<AutomationPolicy> saveAutomation(Map<String, Object?> body) async =>
      AutomationPolicy.fromJson(JsonReader.of(
          await api.request('PUT', '$base/automation', body: body)));
}

/// Whether [error] is the API refusing for lack of permission.
bool isDenied(Object error) =>
    error is ApiException && error.isPermissionDenied;
