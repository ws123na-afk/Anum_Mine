import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'finance_more_data.dart' show decOr;

/// Automatic matching on the phone (docs/finance-matching.md): the owner's
/// record-linking policy, the matches ANUM made or holds for review, the
/// history backfill, Tamara installments with the payment that paid each one,
/// and SMS and Apple Pay capture health. What the phone reads and sends,
/// mirroring `packages/contracts/src/financeMatching.ts`. Money stays a
/// decimal string next to its currency.

const matchSwitches = [
  'match_transactions',
  'reconcile_installments',
  'link_recurring_bills',
  'link_card_repayments',
  'link_bank_loan_repayments',
  'prevent_duplicates',
  'reprocess_history',
  'notify_exceptions',
];
const matchSources = ['statement', 'sms', 'wallet', 'manual'];
const matchCountKeys = [
  'transactions',
  'matched',
  'auto_linked',
  'verified',
  'provisional',
  'ambiguous',
  'review',
  'missing_source',
  'duplicates',
  'corrected',
  'differences',
  'already_linked',
  'kept_owner_choice',
  'errors',
];

class MatchPolicy {
  MatchPolicy({
    required this.switches,
    required this.minConfidence,
    required this.reviewConfidence,
    required this.settledSources,
    required this.sourceAccountIds,
    required this.revision,
  });
  final Map<String, bool> switches;
  String minConfidence;
  final String reviewConfidence;
  final List<String> settledSources, sourceAccountIds;
  final int revision;

  factory MatchPolicy.fromJson(JsonReader r) => MatchPolicy(
        switches: {for (final k in matchSwitches) k: r.optBool(k) ?? false},
        minConfidence: decOr(r, 'min_confidence', '0.9'),
        reviewConfidence: decOr(r, 'review_confidence', '0.5'),
        settledSources: [...?r.optStrings('settled_sources')],
        sourceAccountIds: [...?r.optStrings('source_account_ids')],
        revision: r.optInt('revision') ?? 0,
      );

  MatchPolicy copy() => MatchPolicy(
        switches: {...switches},
        minConfidence: minConfidence,
        reviewConfidence: reviewConfidence,
        settledSources: [...settledSources],
        sourceAccountIds: [...sourceAccountIds],
        revision: revision,
      );

  /// What PUT /automation/matching takes back.
  Map<String, Object?> body({required bool approve}) => {
        ...switches,
        'min_confidence': minConfidence,
        'review_confidence': reviewConfidence,
        'settled_sources': settledSources,
        'source_account_ids': sourceAccountIds,
        'approve': approve,
        'expected_revision': revision,
      };

  bool sameAs(MatchPolicy other) {
    String sorted(List<String> v) => ([...v]..sort()).join(',');
    return matchSwitches.every((k) => switches[k] == other.switches[k]) &&
        double.tryParse(minConfidence) ==
            double.tryParse(other.minConfidence) &&
        sorted(settledSources) == sorted(other.settledSources) &&
        sorted(sourceAccountIds) == sorted(other.sourceAccountIds);
  }
}

class MatchPolicyView {
  const MatchPolicyView({
    required this.policy,
    required this.version,
    required this.approved,
    required this.neverAutomated,
    required this.connectedProviders,
    required this.otherProviders,
  });
  final MatchPolicy policy;
  final String version;
  final bool approved;
  final List<String> neverAutomated, connectedProviders, otherProviders;

  factory MatchPolicyView.fromJson(JsonReader r) {
    final providers = r.optObjects('providers',
        (p) => (p.string('name'), p.optBool('connected') ?? false));
    return MatchPolicyView(
      policy: MatchPolicy.fromJson(r.object('policy')),
      version: r.optString('version') ?? '',
      approved: r.optBool('approved') ?? false,
      neverAutomated: [...?r.optStrings('never_automated')],
      connectedProviders: [
        for (final p in providers)
          if (p.$2) p.$1
      ],
      otherProviders: [
        for (final p in providers)
          if (!p.$2) p.$1
      ],
    );
  }
}

class MatchEvidence {
  const MatchEvidence(this.code, this.value);
  final String code;
  final String? value;
}

/// The bank's own conversion figures behind a match across currencies
/// (docs/finance-matching.md#across-currencies). `basis` is `missing` (no
/// original amount: review only) or `owner_confirmed`; null on the bank's
/// figures. The phone never works out a rate.
class MatchFx {
  const MatchFx({
    required this.bankCurrency,
    required this.rateKind,
    this.basis,
    this.originalAmount,
    this.originalCurrency,
    this.billedAmount,
    this.bankAmount,
    this.rate,
    this.feeAmount,
    this.feeCurrency,
    this.feeSource,
  });
  final String bankCurrency, rateKind;
  final String? basis, originalAmount, originalCurrency, billedAmount;
  final String? bankAmount, rate, feeAmount, feeCurrency, feeSource;

  /// `bank`, `missing` or `confirmed`.
  String get reading {
    if (basis == 'owner_confirmed') return 'confirmed';
    if (basis == 'missing' || originalAmount == null) return 'missing';
    return 'bank';
  }

  factory MatchFx.fromJson(JsonReader r) => MatchFx(
        bankCurrency: r.optString('bank_currency') ?? '',
        rateKind: r.optString('rate_kind') ?? 'not_stated',
        basis: r.optString('basis'),
        originalAmount: r.optString('original_amount'),
        originalCurrency: r.optString('original_currency'),
        billedAmount: r.optString('billed_amount'),
        bankAmount: r.optString('bank_amount'),
        rate: r.optString('rate'),
        feeAmount: r.optString('fee_amount'),
        feeCurrency: r.optString('fee_currency'),
        feeSource: r.optString('fee_source'),
      );
}

class MatchRecordItem {
  const MatchRecordItem({
    required this.id,
    required this.kind,
    required this.label,
    required this.amount,
    required this.currency,
    required this.outcome,
    required this.evidenceState,
    required this.reasonCode,
    required this.confidence,
    required this.version,
    this.dueOn,
    this.postedOn,
    this.linkId,
    this.competitors = 0,
    this.flags = const [],
    this.evidence = const [],
    this.fx,
  });
  final String id, kind, label, amount, currency, outcome, evidenceState;
  final String reasonCode, confidence;
  final String? dueOn, postedOn, linkId;
  final int version, competitors;
  final List<String> flags;
  final List<MatchEvidence> evidence;
  final MatchFx? fx;

  bool get waiting => outcome == 'review' || outcome == 'stale';
  bool get undoable =>
      outcome == 'auto_linked' ||
      outcome == 'accepted' ||
      (outcome == 'stale' && linkId != null);

  factory MatchRecordItem.fromJson(JsonReader r) => MatchRecordItem(
        id: r.string('id'),
        kind: r.string('kind'),
        label: r.optString('label') ?? '',
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        outcome: r.string('outcome'),
        evidenceState: r.optString('evidence_state') ?? 'pending',
        reasonCode: r.optString('reason_code') ?? '',
        confidence: decOr(r, 'confidence'),
        version: r.optInt('version') ?? 1,
        dueOn: r.optString('due_on'),
        postedOn: r.optString('posted_on'),
        linkId: r.optString('link_id'),
        competitors: r.optInt('competitors') ?? 0,
        flags: [...?r.optStrings('flags')],
        evidence: r.optObjects('evidence',
            (e) => MatchEvidence(e.string('code'), e.optString('value'))),
        fx: switch (r.optObject('fx')) {
          final JsonReader fx => MatchFx.fromJson(fx),
          null => null,
        },
      );
}

class MatchRecordPage {
  const MatchRecordPage(this.items, this.total, this.counts);
  final List<MatchRecordItem> items;
  final int total;
  final Map<String, int> counts;
}

class InstallmentRowItem {
  const InstallmentRowItem({
    required this.number,
    required this.total,
    required this.dueOn,
    required this.amount,
    required this.currency,
    required this.status,
  });
  final int number, total;
  final String dueOn, amount, currency, status;

  factory InstallmentRowItem.fromJson(JsonReader r) => InstallmentRowItem(
        number: r.integer('installment'),
        total: r.integer('total_installments'),
        dueOn: r.string('due_on'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        status: r.string('status'),
      );
}

class InstallmentOrderItem {
  const InstallmentOrderItem({
    required this.provider,
    required this.orderId,
    required this.merchant,
    required this.currency,
    required this.stillDue,
    required this.installments,
    this.nextDueOn,
  });
  final String provider, orderId, merchant, currency, stillDue;
  final String? nextDueOn;
  final List<InstallmentRowItem> installments;

  factory InstallmentOrderItem.fromJson(JsonReader r) => InstallmentOrderItem(
        provider: r.string('provider'),
        orderId: r.string('order_id'),
        merchant: r.string('merchant'),
        currency: r.string('currency'),
        stillDue: decOr(r, 'still_due'),
        nextDueOn: r.optString('next_due_on'),
        installments: r.optObjects('installments', InstallmentRowItem.fromJson),
      );
}

class BackfillRunItem {
  const BackfillRunItem({
    required this.id,
    required this.mode,
    required this.status,
    required this.counts,
    this.startOn,
    this.endOn,
    this.cursorOn,
    this.errorCode,
  });
  final String id, mode, status;
  final String? startOn, endOn, cursorOn, errorCode;
  final Map<String, int> counts;

  bool get running => status == 'running';

  /// Done months over all months, 0 to 1.
  double get progress {
    if (status == 'done' || status == 'undone') return 1;
    final start = startOn, end = endOn;
    if (start == null || end == null) return 0;
    int months(String day) =>
        int.parse(day.substring(0, 4)) * 12 + int.parse(day.substring(5, 7));
    final total = months(end) - months(start) + 1;
    final done = cursorOn == null ? 0 : months(cursorOn!) - months(start) + 1;
    return (done / (total < 1 ? 1 : total)).clamp(0, 1).toDouble();
  }

  factory BackfillRunItem.fromJson(JsonReader r) => BackfillRunItem(
        id: r.string('id'),
        mode: r.string('mode'),
        status: r.string('status'),
        startOn: r.optString('start_on'),
        endOn: r.optString('end_on'),
        cursorOn: r.optString('cursor_on'),
        errorCode: r.optString('error_code'),
        counts: _counts(r.optMap('counts')),
      );
}

Map<String, int> _counts(Map<String, Object?>? raw) => {
      for (final entry in (raw ?? const {}).entries)
        if (entry.value is num) entry.key: (entry.value as num).toInt(),
    };

class CaptureChannel {
  const CaptureChannel(this.channel, this.lastCapturedAt, this.lastFailedAt,
      this.lastFailedCode, this.refused, this.waiting);
  final String channel;
  final String? lastCapturedAt, lastFailedAt, lastFailedCode;
  final int refused, waiting;
}

class CaptureHealthView {
  const CaptureHealthView({
    required this.state,
    required this.channels,
    required this.devices,
    required this.senders,
    required this.mappings,
    required this.unmappedAccounts,
    required this.gaps,
    required this.problems,
    required this.unmappedWallet,
  });
  final String state;
  final List<CaptureChannel> channels;
  final List<(String name, String status, String? lastOk, String? error)>
      devices;
  final List<(String sender, int count)> senders;
  final List<(String kind, String label, String? account)> mappings;
  final List<String> unmappedAccounts, problems;
  final List<(String start, int hours)> gaps;
  final int unmappedWallet;

  factory CaptureHealthView.fromJson(JsonReader r) => CaptureHealthView(
        state: r.string('state'),
        channels: r.optObjects(
            'channels',
            (c) => CaptureChannel(
                c.string('channel'),
                c.optString('last_captured_at'),
                c.optString('last_failed_at'),
                c.optString('last_failed_code'),
                c.optInt('refused_7d') ?? 0,
                c.optInt('awaiting_review') ?? 0)),
        devices: r.optObjects(
            'devices',
            (d) => (
                  d.string('name'),
                  d.string('status'),
                  d.optString('last_ok_at'),
                  d.optString('last_error')
                )),
        senders: r.optObjects('unknown_senders',
            (s) => (s.string('sender'), s.optInt('messages') ?? 0)),
        mappings: r.optObjects(
            'mappings',
            (m) => (
                  m.string('kind'),
                  m.string('label'),
                  m.optString('account_name')
                )),
        unmappedAccounts: [...?r.optStrings('accounts_without_mapping')],
        gaps: r.optObjects(
            'gaps', (g) => (g.string('start'), g.optInt('hours') ?? 0)),
        problems: [...?r.optStrings('problems')],
        unmappedWallet: r.optInt('unmapped_wallet_cards') ?? 0,
      );
}

/// Every route the matching screens use, through the app's API client.
class FinanceMatchingRepository {
  FinanceMatchingRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));
  Future<JsonReader> _send(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$base$path', body: body));
  String _id(String value) => Uri.encodeComponent(value);

  Future<MatchPolicyView> policy() async =>
      MatchPolicyView.fromJson(await _get('/automation/matching'));
  Future<MatchPolicyView> savePolicy(MatchPolicy policy,
          {required bool approve}) async =>
      MatchPolicyView.fromJson(await _send(
          'PUT', '/automation/matching', policy.body(approve: approve)));
  Future<List<(String id, String name)>> accounts() async =>
      JsonReader.of(await api.request('GET', '$base/accounts'))
          .optObjects(
              'data',
              (a) => (a.optBool('archived') ?? false)
                  ? null
                  : (a.string('id'), a.string('name')))
          .whereType<(String, String)>()
          .toList();

  Future<Map<String, int>> runNow() async =>
      _counts((await _send('POST', '/matching/run')).optMap('counts'));
  Future<MatchRecordPage> records(String outcome, {int limit = 25}) async {
    final r =
        await _get('/matching/records?outcome=${_id(outcome)}&limit=$limit');
    return MatchRecordPage(r.optObjects('items', MatchRecordItem.fromJson),
        r.optInt('total') ?? 0, _counts(r.optMap('counts')));
  }

  Future<MatchRecordItem> decide(String id, String action, int version) async =>
      MatchRecordItem.fromJson(await _send('POST',
          '/matching/records/${_id(id)}/$action', {'version': version}));
  Future<List<InstallmentOrderItem>> installments() async =>
      (await _get('/matching/installments'))
          .optObjects('orders', InstallmentOrderItem.fromJson);
  Future<List<BackfillRunItem>> runs() async =>
      (await _get('/matching/backfill?limit=5'))
          .optObjects('items', BackfillRunItem.fromJson);
  Future<BackfillRunItem> startRun(String mode) async =>
      BackfillRunItem.fromJson(
          await _send('POST', '/matching/backfill', {'mode': mode}));
  Future<BackfillRunItem> continueRun(String id) async =>
      BackfillRunItem.fromJson(
          await _send('POST', '/matching/backfill/${_id(id)}/continue'));
  Future<int> undoRun(String id) async =>
      (await _send('POST', '/matching/backfill/${_id(id)}/undo'))
          .optInt('links_removed') ??
      0;
  Future<CaptureHealthView> captureHealth() async =>
      CaptureHealthView.fromJson(await _get('/capture-health'));
}
