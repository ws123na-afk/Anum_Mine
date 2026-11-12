import 'dart:typed_data';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../records_kit/records_kit.dart';
import 'finance_models.dart';
import 'finance_more_data.dart';

/// The Finance tools the phone gained in the November 2026 pass
/// (docs/finance.md, docs/finance-sms.md, docs/finance-statements.md):
/// resolving and completing a reconciliation, the iPhone SMS review and the
/// ledger, and statement import (CSV and PDF) with the preview the web shows.
/// What the phone reads and sends mirrors `financeReconciliation.ts`,
/// `financeSms.ts` and `finance.ts` in packages/contracts. Money stays a
/// decimal string next to its currency; nothing is added across currencies.

const _base = FinanceMoreRepository.base;
String _id(String value) => Uri.encodeComponent(value);

// -- reconciliation: resolving, completing, reopening ---------------------------

/// A resolution the owner made in a session (`FinanceReconResolution`).
class ReconResolution {
  const ReconResolution(
      {required this.id,
      required this.kind,
      required this.createdAt,
      this.statementRef,
      this.transactionId,
      this.otherTransactionId,
      this.note});
  final String id, kind, createdAt;
  final String? statementRef, transactionId, otherTransactionId, note;

  factory ReconResolution.fromJson(JsonReader r) => ReconResolution(
        id: r.string('id'),
        kind: r.string('kind'),
        createdAt: r.optString('created_at') ?? '',
        statementRef: r.optString('statement_ref'),
        transactionId: r.optString('transaction_id'),
        otherTransactionId: r.optString('other_transaction_id'),
        note: r.optString('note'),
      );
}

/// One line of a session's history (`completed` or `reopened`).
class ReconHistory {
  const ReconHistory(
      {required this.action, required this.at, this.reason, this.unexplained});
  final String action, at;
  final String? reason, unexplained;

  factory ReconHistory.fromJson(JsonReader r) => ReconHistory(
        action: r.string('action'),
        at: r.optString('at') ?? '',
        reason: r.optString('reason'),
        unexplained: r.optString('unexplained'),
      );
}

/// One journal leg of a correcting entry.
class ReconLeg {
  const ReconLeg(this.ledgerAccount, this.amount, this.currency);
  final String ledgerAccount, amount, currency;
  factory ReconLeg.fromJson(JsonReader r) => ReconLeg(
      r.string('ledger_account'), decOr(r, 'amount'), r.string('currency'));
}

/// The correcting entry the owner approves (`FinanceReconCorrectionPreview`):
/// posted only with this [payloadHash].
class ReconCorrection {
  const ReconCorrection(
      {required this.statementRef,
      required this.postedOn,
      required this.amount,
      required this.currency,
      required this.description,
      required this.category,
      required this.legs,
      required this.payloadHash});
  final String statementRef, postedOn, amount, currency, description, category;
  final List<ReconLeg> legs;
  final String payloadHash;

  factory ReconCorrection.fromJson(JsonReader r) => ReconCorrection(
        statementRef: r.string('statement_ref'),
        postedOn: r.string('posted_on'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        description: r.optString('description') ?? '',
        category: r.optString('category') ?? 'other',
        legs: r.optObjects('legs', ReconLeg.fromJson),
        payloadHash: r.string('payload_hash'),
      );
}

/// A statement import as the lists show it (`FinanceImport`).
class StatementImport {
  const StatementImport(
      {required this.id,
      required this.accountId,
      required this.filename,
      required this.createdAt,
      required this.rowCount,
      required this.importedCount,
      required this.duplicateCount,
      required this.errorCount,
      this.reconciled = 0,
      this.heldForReview = 0,
      this.skipped = 0,
      this.dropped = 0,
      this.transfers = 0,
      this.billsPaid = 0,
      this.corrected = 0,
      this.notInLedger = 0,
      this.check});
  final String id, accountId, filename, createdAt;
  final int rowCount, importedCount, duplicateCount, errorCount;
  final int reconciled, heldForReview, skipped, dropped, transfers;
  final int billsPaid, corrected, notInLedger;

  /// The stored balance check: `tied`, `differs` or `unknown`.
  final String? check;

  factory StatementImport.fromJson(JsonReader r) => StatementImport(
        id: r.string('id'),
        accountId: r.string('account_id'),
        filename: r.optString('filename') ?? '',
        createdAt: r.optString('created_at') ?? '',
        rowCount: r.optInt('row_count') ?? 0,
        importedCount: r.optInt('imported_count') ?? 0,
        duplicateCount: r.optInt('duplicate_count') ?? 0,
        errorCount: r.optInt('error_count') ?? 0,
        reconciled: r.optInt('reconciled') ?? 0,
        heldForReview: r.optInt('held_for_review') ?? 0,
        skipped: r.optInt('skipped') ?? 0,
        dropped: r.optInt('dropped') ?? 0,
        transfers: r.optInt('transfers') ?? 0,
        billsPaid: r.optInt('bills_paid') ?? 0,
        corrected: r.optInt('corrected') ?? 0,
        notInLedger: r.optInt('not_in_ledger') ?? 0,
        check: r.optObject('reconciliation')?.optString('status'),
      );
}

/// The ledger rows a statement row can be matched to: the session's own
/// unmatched or pending rows of the same amount (the web's
/// `matchCandidates`).
List<ReconIssue> reconMatchCandidates(ReconIssue row, List<ReconIssue> all) {
  final amount = row.amount;
  if (amount == null) return const [];
  final fromStatement = row.statementRef != null;
  return [
    for (final e in all)
      if (fromStatement
          ? e.transactionId != null &&
              const {'unmatched_payment', 'unmatched_deposit', 'pending'}
                  .contains(e.kind) &&
              sameAmount(e.amount, amount)
          : e.kind == 'not_in_ledger' &&
              e.statementRef != null &&
              sameAmount(e.amount, amount))
        e
  ];
}

/// Two decimal strings are the same amount ("-10.5" and "-10.50").
bool sameAmount(String? a, String? b) {
  if (a == null || b == null) return false;
  final x = double.tryParse(a), y = double.tryParse(b);
  return x != null && y != null && (x - y).abs() < 0.0005;
}

extension FinanceReconActions on FinanceMoreRepository {
  Future<JsonReader> _send(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  /// Every exception of a session (up to 200), for the match picker.
  Future<List<ReconIssue>> reconAllIssues(String id) async => (await _send(
          'GET',
          '/reconciliations/${_id(id)}${recordsQuery({
                'limit': 200,
                'offset': 0
              })}'))
      .optObjects('items', ReconIssue.fromJson);

  Future<ReconDetail> startRecon(Map<String, Object?> body) async =>
      ReconDetail.fromJson(await _send('POST', '/reconciliations', body));

  Future<void> resolveRecon(String id, Map<String, Object?> body) =>
      _send('POST', '/reconciliations/${_id(id)}/resolutions', body);

  Future<void> undoResolution(String id, String resolutionId) => api.request(
      'DELETE',
      '$_base/reconciliations/${_id(id)}/resolutions/${_id(resolutionId)}');

  Future<ReconCorrection> previewCorrection(String id, String statementRef) =>
      _send('POST', '/reconciliations/${_id(id)}/corrections/preview',
          {'statement_ref': statementRef}).then(ReconCorrection.fromJson);

  /// Posts the correcting entry the owner saw: the API refuses it (409
  /// `payload_changed`) when it changed since the preview.
  Future<void> postCorrection(String id, ReconCorrection shown) =>
      _send('POST', '/reconciliations/${_id(id)}/corrections', {
        'statement_ref': shown.statementRef,
        'payload_hash': shown.payloadHash,
      });

  Future<ReconDetail> completeRecon(String id, int version) async =>
      ReconDetail.fromJson(await _send('POST',
          '/reconciliations/${_id(id)}/complete', {'version': version}));

  Future<ReconDetail> reopenRecon(
          String id, int version, String reason) async =>
      ReconDetail.fromJson(await _send(
          'POST',
          '/reconciliations/${_id(id)}/reopen',
          {'version': version, 'reason': reason}));

  /// The person's accounts (archived included; screens leave them out).
  Future<List<FinanceAccount>> financeAccounts() async =>
      (await _send('GET', '/accounts'))
          .optObjects('data', FinanceAccount.fromJson);

  Future<RecordsChunk<StatementImport>> statementImports(
      {String? token, int limit = 20}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _send(
        'GET', '/imports${recordsQuery({'limit': limit, 'offset': offset})}');
    return RecordsChunk.offsetPage(page, StatementImport.fromJson,
        offset: offset);
  }

  /// Undoes an import: its transactions go with it.
  Future<void> undoImport(String importId) =>
      api.request('DELETE', '$_base/imports/${_id(importId)}');
}

// -- iPhone SMS: the review queue -----------------------------------------------

/// The statuses a message can have, in the web's order.
const smsStatuses = [
  'review',
  'failed',
  'duplicate',
  'attached',
  'posted',
  'dismissed',
  'rejected'
];

/// The kinds a message can say happened (docs/finance-sms.md#kinds).
const smsKinds = [
  'salary',
  'deposit',
  'transfer_in',
  'transfer_out',
  'own_transfer',
  'card_purchase',
  'atm_withdrawal',
  'card_repayment',
  'loan_disbursement',
  'loan_repayment',
  'bank_fee',
  'refund',
  'reversal',
  'unknown',
];

/// One message (`FinanceSmsMessage`). The body is masked by the server;
/// [parsed] is what ANUM read, as text.
class SmsMessage {
  const SmsMessage(
      {required this.id,
      required this.source,
      required this.receivedAt,
      required this.sender,
      required this.bodyMasked,
      required this.status,
      required this.parsed,
      this.reason,
      this.bankKey,
      this.entryId,
      this.fix,
      this.learnedWhat,
      this.learnedValue});
  final String id, source, receivedAt, sender, bodyMasked, status;
  final String? reason, bankKey, entryId;
  final Map<String, String> parsed;

  /// The one action that makes such a message automatic next time
  /// (docs/finance-sms.md#learning).
  final SmsFix? fix;

  /// What ANUM applied from the person's earlier choices: `kind` or
  /// `category`, and the value.
  final String? learnedWhat, learnedValue;

  bool get posted => status == 'posted';

  /// Recorded, corrected or dismissed: not for a rejected, dismissed or
  /// attached message (the web's rule).
  bool get actionable =>
      status != 'rejected' && status != 'dismissed' && status != 'attached';

  factory SmsMessage.fromJson(JsonReader r) => SmsMessage(
        id: r.string('id'),
        source: r.optString('source') ?? 'device',
        receivedAt: r.optString('received_at') ?? '',
        sender: r.optString('sender') ?? '',
        bodyMasked: r.optString('body_masked') ?? '',
        status: r.string('status'),
        reason: r.optString('reason'),
        bankKey: r.optString('bank_key'),
        entryId: r.optString('entry_id'),
        fix: r.optObject('fix') == null
            ? null
            : SmsFix.fromJson(r.object('fix')),
        learnedWhat: r.optObject('learned')?.optString('what'),
        learnedValue: r.optObject('learned')?.optString('value'),
        parsed: {
          for (final MapEntry(:key, :value)
              in (r.optMap('parsed') ?? const {}).entries)
            if (value != null && '$value'.isNotEmpty) key: '$value',
        },
      );
}

/// The one step that stops such a message waiting (`FinanceSmsFix`).
class SmsFix {
  const SmsFix(
      {required this.action,
      this.sender,
      this.last4,
      this.accountId,
      this.accountName,
      this.party,
      this.kind,
      this.ref});
  final String action;
  final String? sender, last4, accountId, accountName, party, kind, ref;

  /// Done in one tap from the phone (a card without a proposed account, a
  /// decision, a record or a Setup change are said in words).
  bool get oneTap => switch (action) {
        'trust_sender' || 'auto_post_on' || 'clear_hold' => true,
        'remember_kind' || 'undo_learning' || 'undo_recheck' => true,
        'map_card' => accountId != null,
        _ => false,
      };

  factory SmsFix.fromJson(JsonReader r) => SmsFix(
        action: r.string('action'),
        sender: r.optString('sender'),
        last4: r.optString('last4'),
        accountId: r.optString('account_id'),
        accountName: r.optString('account_name'),
        party: r.optString('party'),
        kind: r.optString('kind'),
        ref: r.optString('ref'),
      );
}

/// Something ANUM learned from the person's reviews, with its undo
/// (`FinanceSmsLearningNotice`).
class SmsNotice {
  const SmsNotice(
      {required this.id,
      required this.kind,
      this.sender,
      this.party,
      this.value,
      this.last4,
      this.accountId,
      this.accountName,
      this.ref});
  final String id, kind;
  final String? sender, party, value, last4, accountId, accountName, ref;

  factory SmsNotice.fromJson(JsonReader r) => SmsNotice(
        id: r.string('id'),
        kind: r.string('kind'),
        sender: r.optString('sender'),
        party: r.optString('party'),
        value: r.optString('value'),
        last4: r.optString('last4'),
        accountId: r.optString('account_id'),
        accountName: r.optString('account_name'),
        ref: r.optString('ref'),
      );
}

/// One reason messages wait, split by the sender or card they share
/// (`FinanceSmsReviewSummary`).
class SmsReasonGroup {
  const SmsReasonGroup(
      {required this.reason, required this.count, required this.items});
  final String reason;
  final int count;
  final List<({String key, int count, SmsFix? fix})> items;

  factory SmsReasonGroup.fromJson(JsonReader r) => SmsReasonGroup(
        reason: r.string('reason'),
        count: r.optInt('count') ?? 0,
        items: r.optObjects(
            'items',
            (i) => (
                  key: i.optString('key') ?? '-',
                  count: i.optInt('count') ?? 0,
                  fix: i.optObject('fix') == null
                      ? null
                      : SmsFix.fromJson(i.object('fix')),
                )),
      );
}

/// What a correction answered (`FinanceSmsCorrectionResult`).
class SmsPostResult {
  const SmsPostResult(
      {this.ruleError, this.ruleProposed = false, this.learned = const []});
  final String? ruleError;
  final bool ruleProposed;
  final List<SmsNotice> learned;
}

/// A sender format ANUM learned or was taught (`FinanceSmsRule`).
class SmsRule {
  const SmsRule(
      {required this.id,
      required this.sender,
      required this.kind,
      required this.status,
      required this.hits,
      required this.pattern,
      this.accountId});
  final String id, sender, kind, status, pattern;
  final int hits;
  final String? accountId;

  factory SmsRule.fromJson(JsonReader r) => SmsRule(
        id: r.string('id'),
        sender: r.optString('sender') ?? '',
        kind: r.optString('kind') ?? 'unknown',
        status: r.string('status'),
        hits: r.optInt('hits') ?? 0,
        pattern: r.optString('pattern_preview') ?? '',
        accountId: r.optString('account_id'),
      );
}

/// The integration's state, enough for the review page's header.
class SmsStatus {
  const SmsStatus(
      {required this.enabled,
      required this.ready,
      required this.devicesActive,
      required this.reviewCount,
      this.lastReceivedAt,
      this.lastError,
      this.aiOn = false});
  final bool enabled, ready, aiOn;
  final int devicesActive, reviewCount;
  final String? lastReceivedAt, lastError;

  factory SmsStatus.fromJson(JsonReader r, {bool aiOn = false}) => SmsStatus(
        enabled: r.optBool('enabled') ?? false,
        ready: r.optBool('ready') ?? false,
        devicesActive: r.optInt('devices_active') ?? 0,
        reviewCount: r.optInt('review_count') ?? 0,
        lastReceivedAt: r.optString('last_received_at'),
        lastError: r.optString('last_error'),
        aiOn: aiOn,
      );
}

// -- the ledger ---------------------------------------------------------------

/// An account, card or loan line of the dashboard (`AccountLine`).
class LedgerAccountLine {
  const LedgerAccountLine(
      {required this.id,
      required this.name,
      required this.kind,
      required this.currency,
      required this.balance,
      this.owed,
      this.utilization,
      this.observed,
      this.difference});
  final String id, name, kind, currency, balance;
  final String? owed, observed, difference;
  final int? utilization;

  factory LedgerAccountLine.fromJson(JsonReader r) => LedgerAccountLine(
        id: r.string('id'),
        name: r.string('name'),
        kind: r.optString('kind') ?? 'bank',
        currency: r.string('currency'),
        balance: decOr(r, 'balance'),
        owed: dec(r, 'owed'),
        utilization: r.optInt('utilization_percent'),
        observed: dec(r, 'observed_balance'),
        difference: dec(r, 'difference'),
      );
}

/// A category or merchant line: what it is, its money and how many rows.
class LedgerLine {
  const LedgerLine(this.name, this.currency, this.amount, this.count);
  final String name, currency, amount;
  final int count;
  static LedgerLine Function(JsonReader) reader(String key) =>
      (r) => LedgerLine(r.optString(key) ?? '', r.string('currency'),
          decOr(r, 'amount'), r.optInt('count') ?? 0);
}

/// One month (or the year, when [month] has four digits) in one currency.
class LedgerMonth {
  const LedgerMonth(
      this.month, this.currency, this.income, this.expenses, this.net);
  final String month, currency, income, expenses, net;
  factory LedgerMonth.fromJson(JsonReader r) => LedgerMonth(
      r.string('month'),
      r.string('currency'),
      decOr(r, 'income'),
      decOr(r, 'expenses'),
      decOr(r, 'net'));
}

/// A due bill, a debt's payoff or a goal, as the dashboard lists them.
class LedgerItem {
  const LedgerItem(this.id, this.name, this.currency, this.amount, this.note,
      {this.months, this.percent});
  final String id, name, currency, amount;
  final String? note;
  final int? months, percent;
}

/// The ledger dashboard (`FinanceLedgerDashboard`).
class LedgerBoard {
  const LedgerBoard({
    required this.month,
    required this.ledgerKind,
    required this.figures,
    required this.inTransit,
    required this.accounts,
    required this.cards,
    required this.loans,
    required this.categories,
    required this.merchants,
    required this.trend,
    required this.bills,
    required this.debts,
    required this.goals,
    required this.currencies,
    required this.entryCount,
    required this.uncategorized,
    required this.syncOn,
    required this.devicesActive,
    required this.reviewCount,
    this.lastReceivedAt,
    this.lastError,
  });
  final String month, ledgerKind;

  /// income, expenses, surplus, assets, liabilities, net_worth: each per
  /// currency, never added together.
  final Map<String, List<CurrencyAmount>> figures;
  final List<CurrencyAmount> inTransit;
  final List<LedgerAccountLine> accounts, cards, loans;
  final List<LedgerLine> categories, merchants;
  final List<LedgerMonth> trend;
  final List<LedgerItem> bills, debts, goals;
  final List<String> currencies;
  final int entryCount, uncategorized, devicesActive, reviewCount;
  final bool syncOn;
  final String? lastReceivedAt, lastError;

  static const figureKeys = [
    'income',
    'expenses',
    'surplus',
    'assets',
    'liabilities',
    'net_worth'
  ];

  factory LedgerBoard.fromJson(JsonReader r) {
    List<CurrencyAmount> money(String key) => r.optObjects(
        key, (m) => CurrencyAmount(m.string('currency'), decOr(m, 'amount')));
    final sync = r.optObject('sync');
    return LedgerBoard(
      month: r.string('month'),
      ledgerKind: r.optString('ledger_kind') ?? 'personal',
      figures: {for (final key in figureKeys) key: money(key)},
      inTransit: money('in_transit'),
      accounts: r.optObjects('accounts', LedgerAccountLine.fromJson),
      cards: r.optObjects('cards', LedgerAccountLine.fromJson),
      loans: r.optObjects('loans', LedgerAccountLine.fromJson),
      categories: r.optObjects('categories', LedgerLine.reader('category')),
      merchants: r.optObjects('merchants', LedgerLine.reader('merchant')),
      trend: [
        ...r.optObjects('trend', LedgerMonth.fromJson),
        ...r.optObjects('year', LedgerMonth.fromJson),
      ],
      bills: r.optObjects(
          'bills_due',
          (b) => LedgerItem(
              b.optString('id') ?? '',
              b.optString('name') ?? '',
              b.optString('currency') ?? '',
              decOr(b, 'amount'),
              b.optString('due_on'))),
      debts: r.optObjects(
          'debts',
          (d) => LedgerItem(d.string('id'), d.string('name'),
              d.string('currency'), decOr(d, 'balance'), null,
              months: d.optInt('months_to_clear'))),
      goals: r.optObjects(
          'goals',
          (g) => LedgerItem(g.optString('id') ?? '', g.optString('name') ?? '',
              g.optString('currency') ?? '', decOr(g, 'saved_amount'), null,
              percent: g.optInt('percent'))),
      currencies: r.optStrings('currencies') ?? const [],
      entryCount: r.optInt('entry_count') ?? 0,
      uncategorized: r.optInt('uncategorized_count') ?? 0,
      syncOn: sync?.optBool('enabled') ?? false,
      devicesActive: sync?.optInt('devices_active') ?? 0,
      reviewCount: sync?.optInt('review_count') ?? 0,
      lastReceivedAt: sync?.optString('last_received_at'),
      lastError: sync?.optString('last_error'),
    );
  }
}

/// One posting of a journal entry.
class LedgerPosting {
  const LedgerPosting(
      this.ledgerAccount, this.accountId, this.amount, this.currency);
  final String ledgerAccount, amount, currency;
  final String? accountId;
  bool get credit => amount.startsWith('-');
  String get unsigned => credit ? amount.substring(1) : amount;
}

/// One journal entry (`FinanceJournalEntry`).
class LedgerEntry {
  const LedgerEntry(
      {required this.id,
      required this.postedOn,
      required this.kind,
      required this.description,
      required this.source,
      required this.status,
      required this.postings,
      required this.sources,
      this.canonicalId,
      this.reconciledAt,
      this.originalAmount,
      this.originalCurrency,
      this.fxRate});
  final String id, postedOn, kind, description, source, status;
  final String? canonicalId, reconciledAt;
  final String? originalAmount, originalCurrency, fxRate;
  final List<LedgerPosting> postings;

  /// `sms` and `wallet`: the reports behind this one transaction.
  final List<String> sources;

  factory LedgerEntry.fromJson(JsonReader r) => LedgerEntry(
        id: r.string('id'),
        canonicalId: r.optString('canonical_id'),
        postedOn: r.string('posted_on'),
        kind: r.optString('kind') ?? 'unknown',
        description: r.optString('description') ?? '',
        source: r.optString('source') ?? 'sms',
        status: r.optString('status') ?? 'posted',
        reconciledAt: r.optString('reconciled_at'),
        originalAmount: dec(r, 'original_amount'),
        originalCurrency: r.optString('original_currency'),
        fxRate: dec(r, 'fx_rate'),
        postings: r.optObjects(
            'postings',
            (p) => LedgerPosting(
                p.string('ledger_account'),
                p.optString('account_id'),
                decOr(p, 'amount'),
                p.string('currency'))),
        sources: r.optObjects('sources', (s) => s.optString('kind') ?? 'sms'),
      );
}

/// One canonical transaction: the entry that stands and its whole chain.
class LedgerChain {
  const LedgerChain(this.canonicalId, this.current, this.chain);
  final String canonicalId;
  final LedgerEntry? current;
  final List<LedgerEntry> chain;
  factory LedgerChain.fromJson(JsonReader r) => LedgerChain(
        r.string('canonical_id'),
        r.optObject('current') == null
            ? null
            : LedgerEntry.fromJson(r.object('current')),
        r.optObjects('chain', LedgerEntry.fromJson),
      );
}

extension FinanceSmsApi on FinanceMoreRepository {
  Future<JsonReader> _call(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  Future<SmsStatus> smsStatus() async {
    final status = await _call('GET', '/sms/status');
    var ai = false;
    try {
      final settings = await _call('GET', '/sms/settings');
      final aiBlock = settings.optObject('ai');
      ai = (aiBlock?.optBool('enabled') ?? false) &&
          aiBlock?.optString('opted_in_at') != null;
    } on ApiException {
      ai = false;
    }
    return SmsStatus.fromJson(status, aiOn: ai);
  }

  Future<RecordsChunk<SmsMessage>> smsMessages(
      {String status = '', String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _call(
        'GET',
        '/sms/messages${recordsQuery({
              'status': status,
              'limit': FinanceMoreRepository.pageSize,
              'offset': offset,
            })}');
    return RecordsChunk.offsetPage(page, SmsMessage.fromJson, offset: offset);
  }

  Future<SmsPostResult> postSms(String id, Map<String, Object?> body) async {
    final r = await _call('POST', '/sms/messages/${_id(id)}/post', body);
    return SmsPostResult(
        ruleError: r.optString('rule_error'),
        ruleProposed: r.optObject('proposed_rule') != null,
        learned: r.optObjects('learned', SmsNotice.fromJson));
  }

  /// What ANUM learned and has not been acknowledged yet
  /// (docs/finance-sms.md#learning).
  Future<List<SmsNotice>> smsNotices() async =>
      (await _call('GET', '/sms/learning'))
          .optObjects('notices', SmsNotice.fromJson);

  /// Why messages wait: counts per reason with the fix for each.
  Future<({int total, List<SmsReasonGroup> groups})> smsReviewSummary() async {
    final r = await _call('GET', '/sms/review/summary');
    return (
      total: r.optInt('total') ?? 0,
      groups: r.optObjects('groups', SmsReasonGroup.fromJson),
    );
  }

  /// Run one fix in one tap. [messageId] is needed for `undo_learning`.
  Future<void> applySmsFix(SmsFix fix, {String? messageId}) async {
    switch (fix.action) {
      case 'trust_sender':
        await _call(
            'POST', '/sms/learning/senders/trust', {'sender': fix.sender});
      case 'clear_hold':
        await _call(
            'POST', '/sms/learning/senders/clear-hold', {'sender': fix.sender});
      case 'auto_post_on':
        await _call('POST', '/sms/learning/auto-post', {'on': true});
      case 'remember_kind':
        await _call(
            'POST', '/sms/learning/kinds/${_id(fix.ref ?? '')}/activate');
      case 'map_card':
        await _call('POST', '/sms/learning/cards/map',
            {'last4': fix.last4, 'account_id': fix.accountId});
      case 'undo_learning' || 'undo_recheck':
        await _call(
            'POST', '/sms/messages/${_id(messageId ?? '')}/undo-learning');
    }
  }

  /// Undo what a notice says ANUM learned (for a card proposal: not now).
  Future<void> undoSmsNotice(SmsNotice n) async {
    switch (n.kind) {
      case 'sender_trusted':
        await _call(
            'POST', '/sms/learning/senders/untrust', {'sender': n.sender});
      case 'kind_remembered':
        await _call('DELETE', '/sms/learning/kinds/${_id(n.ref ?? '')}');
      case 'category_remembered':
        await _call('DELETE', '/sms/learning/categories/${_id(n.ref ?? '')}');
      default:
        await _call('POST', '/sms/learning/cards/decline', {'last4': n.last4});
    }
  }

  Future<void> mapSmsCard(SmsNotice n) => _call('POST',
      '/sms/learning/cards/map', {'last4': n.last4, 'account_id': n.accountId});

  /// Re-check every waiting message (docs/finance-sms.md#re-checking-waiting-messages):
  /// how many were recorded, how many still wait, and whether to run it again.
  Future<({int recorded, int stillWaiting, bool more})> recheckSms() async {
    final r =
        (await _call('POST', '/sms/learning/recheck')).optObject('recheck');
    return (
      recorded: r?.optInt('recorded') ?? 0,
      stillWaiting: r?.optInt('still_waiting') ?? 0,
      more: r?.optBool('more') ?? false,
    );
  }

  Future<void> dismissSmsNotice(String id) =>
      _call('POST', '/sms/learning/notices/${_id(id)}/dismiss');

  Future<void> dismissSms(String id) =>
      _call('POST', '/sms/messages/${_id(id)}/dismiss');

  Future<void> reverseSms(String id) =>
      _call('POST', '/sms/messages/${_id(id)}/reverse');

  /// AI help: the fields the model read and the note to check them.
  Future<({Map<String, String> fields, String note})> suggestSms(
      String id) async {
    final r = await _call('POST', '/sms/messages/${_id(id)}/suggest');
    return (
      fields: {
        for (final MapEntry(:key, :value)
            in (r.optMap('fields') ?? const {}).entries)
          if (value != null) key: '$value'
      },
      note: r.optString('note') ?? '',
    );
  }

  /// A message pasted by hand; answers the server's sentence about it.
  Future<String> pasteSms(String sender, String message) async {
    final r = await _call(
        'POST', '/sms/paste', {'sender': sender, 'message': message});
    return [r.optString('text'), r.optString('reason')]
        .whereType<String>()
        .where((t) => t.isNotEmpty)
        .join(' · ');
  }

  Future<List<SmsRule>> smsRules() async =>
      (await _call('GET', '/sms/rules')).optObjects('data', SmsRule.fromJson);

  Future<void> changeSmsRule(String id, String change) =>
      _call('POST', '/sms/rules/${_id(id)}/$change');

  Future<void> deleteSmsRule(String id) =>
      api.request('DELETE', '$_base/sms/rules/${_id(id)}');

  Future<LedgerBoard> ledger(
          {required String month,
          String account = '',
          String currency = '',
          String category = '',
          String kind = ''}) async =>
      LedgerBoard.fromJson(await _call(
          'GET',
          '/ledger/dashboard${recordsQuery({
                'month': month,
                'account_id': account,
                'currency': currency,
                'category': category,
                'kind': kind,
              })}'));

  Future<RecordsChunk<LedgerEntry>> ledgerEntries({String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _call(
        'GET',
        '/ledger/entries${recordsQuery({
              'limit': FinanceMoreRepository.pageSize,
              'offset': offset,
            })}');
    // The journal's total is "at least one more" while more exist.
    return RecordsChunk.offsetPage(page, LedgerEntry.fromJson, offset: offset);
  }

  Future<LedgerChain> ledgerChain(String canonicalId) async =>
      LedgerChain.fromJson(
          await _call('GET', '/ledger/transactions/${_id(canonicalId)}'));
}

// -- statement import -----------------------------------------------------------

/// Reads a statement PDF: the raw file as the body, the password (when the
/// file has one) in `X-Statement-Password`, never stored (the app's file
/// transfer; a fake in tests).
abstract interface class StatementPdfTransport {
  Future<JsonMap> readStatementPdf(Map<String, String> query, Uint8List bytes,
      {String? password});
}

/// What the reader said about one row's match (`FinanceRowMatch`).
class RowMatch {
  const RowMatch(
      {required this.status,
      required this.reason,
      required this.defaultImport,
      this.recordText,
      this.billName,
      this.billStrong = false,
      this.transferAccount,
      this.transferStrong = false,
      this.amountDifference});
  final String status, reason;
  final bool defaultImport;
  final String? recordText, billName, transferAccount, amountDifference;
  final bool billStrong, transferStrong;

  factory RowMatch.fromJson(JsonReader r) {
    final record = r.optObject('record');
    final bill = r.optObject('bill');
    final transfer = r.optObject('transfer');
    return RowMatch(
      status: r.optString('status') ?? 'new',
      reason: r.optString('reason') ?? '',
      defaultImport: r.optBool('default_import') ?? true,
      recordText: record == null
          ? null
          : '${record.optString('posted_on') ?? ''} ${record.optString('description') ?? ''}'
              .trim(),
      billName: bill?.optString('name'),
      billStrong: bill?.optString('strength') == 'strong',
      transferAccount: transfer?.optString('account_name'),
      transferStrong: transfer?.optString('strength') == 'strong',
      amountDifference: dec(r, 'amount_difference'),
    );
  }
}

/// The person's choice for one previewed row; null follows the default.
class RowChoice {
  RowChoice();
  bool? import, bill, transfer, correct;
  Map<String, Object?> wire() => {
        if (import != null) 'action': import! ? 'import' : 'skip',
        if (bill == false) 'bill': false,
        if (transfer != null) 'transfer': transfer,
        if (correct == false) 'correct': false,
      };
  bool get isEmpty => wire().isEmpty;
}

/// Whether a row is imported, with the person's choice over the default.
bool rowImported(RowMatch? match, RowChoice? choice) =>
    choice?.import ?? match?.defaultImport ?? true;

/// The statement's balance check (`ReconciliationView` or the PDF check).
class BalanceCheck {
  const BalanceCheck(
      {required this.status,
      required this.rowsTotal,
      this.opening,
      this.closing,
      this.expected,
      this.difference,
      this.breaks = const []});

  /// `tied`/`matched`, `differs`/`mismatch` or `unknown`.
  final String status;
  final String rowsTotal;
  final String? opening, closing, expected, difference;
  final List<int> breaks;

  bool get ties => status == 'tied' || status == 'matched';
  bool get differs => status == 'differs' || status == 'mismatch';

  factory BalanceCheck.fromJson(JsonReader r) => BalanceCheck(
        status: r.optString('status') ?? 'unknown',
        rowsTotal: decOr(r, 'rows_total'),
        opening: dec(r, 'opening'),
        closing: dec(r, 'closing'),
        expected: dec(r, 'expected_closing'),
        difference: dec(r, 'difference'),
        breaks: [
          for (final b in r.optList('breaks') ?? const [])
            if (b is int) b
        ],
      );
}

/// One row of a CSV preview (`FinanceImportPreviewRow`).
class CsvRow {
  const CsvRow(
      {required this.line,
      required this.postedOn,
      required this.amount,
      required this.description,
      required this.category,
      required this.match,
      this.warning,
      this.balance});
  final int line;
  final String postedOn, amount, description, category;
  final String? warning, balance;
  final RowMatch match;

  factory CsvRow.fromJson(JsonReader r) => CsvRow(
        line: r.integer('line'),
        postedOn: r.string('posted_on'),
        amount: decOr(r, 'amount'),
        description: r.optString('description') ?? '',
        category: r.optString('category') ?? 'other',
        warning: r.optString('direction_warning'),
        balance: dec(r, 'balance'),
        match: RowMatch.fromJson(r.object('match')),
      );
}

/// The columns a CSV mapping can name (`ColumnMapping`), in the web's order.
const csvMappingFields = [
  'date',
  'description',
  'amount',
  'debit',
  'credit',
  'direction',
  'balance',
  'reference',
  'currency',
  'original_amount',
  'original_currency',
  'fee',
];

/// A CSV import preview (`FinanceImportPreview`).
class CsvPreview {
  const CsvPreview(
      {required this.currency,
      required this.preset,
      required this.headers,
      required this.mapping,
      required this.dateFormat,
      required this.dateFormats,
      required this.dateAmbiguous,
      required this.rowCount,
      required this.validCount,
      required this.newCount,
      required this.duplicateCount,
      required this.errorCount,
      required this.rows,
      required this.errors,
      required this.check});
  final String currency, preset, dateFormat;
  final List<String> headers, dateFormats;
  final Map<String, String> mapping;
  final bool dateAmbiguous;
  final int rowCount, validCount, newCount, duplicateCount, errorCount;
  final List<CsvRow> rows;
  final List<(int, String)> errors;
  final BalanceCheck check;

  factory CsvPreview.fromJson(JsonReader r) => CsvPreview(
        currency: r.string('currency'),
        preset: r.optString('preset') ?? 'auto',
        headers: r.optStrings('headers') ?? const [],
        mapping: {
          for (final MapEntry(:key, :value)
              in (r.optMap('mapping') ?? const {}).entries)
            if (value is String && value.isNotEmpty) key: value
        },
        dateFormat: r.optString('date_format') ?? '',
        dateFormats: r.optStrings('date_formats') ?? const [],
        dateAmbiguous: r.optBool('date_format_ambiguous') ?? false,
        rowCount: r.optInt('row_count') ?? 0,
        validCount: r.optInt('valid_count') ?? 0,
        newCount: r.optInt('new_count') ?? 0,
        duplicateCount: r.optInt('duplicate_count') ?? 0,
        errorCount: r.optInt('error_count') ?? 0,
        rows: r.optObjects('rows', CsvRow.fromJson),
        errors: r.optObjects('errors',
            (e) => (e.optInt('line') ?? 0, e.optString('message') ?? '')),
        check: BalanceCheck.fromJson(r.object('reconciliation')),
      );
}

/// What the importer can read (`GET /formats`).
class ImportFormats {
  const ImportFormats(this.presets, this.dateFormats);
  final Map<String, String> presets;
  final List<String> dateFormats;
  factory ImportFormats.fromJson(JsonReader r) => ImportFormats(
        {
          for (final p in r.optObjects(
              'presets', (p) => (p.string('id'), p.optString('label') ?? '')))
            p.$1: p.$2
        },
        r.optStrings('date_formats') ?? const [],
      );
}

/// A column the PDF reader found, with the role it gave it.
class PdfColumn {
  const PdfColumn(this.index, this.label, this.role);
  final int index;
  final String label, role;
}

/// The roles a PDF column can have (`FinancePdfRole`).
const pdfRoles = [
  'date',
  'description',
  'debit',
  'credit',
  'amount',
  'balance',
  'reference',
  'currency',
  'skip',
];

/// One row the PDF reader found, editable before import.
class PdfRow {
  PdfRow(
      {required this.key,
      required this.line,
      required this.page,
      required this.postedOn,
      required this.description,
      required this.amount,
      required this.include,
      required this.flags,
      required this.confidence,
      this.balance,
      this.reference,
      this.currency,
      this.time,
      this.originalAmount,
      this.originalCurrency,
      this.fxRate,
      this.fee,
      this.vat,
      this.match});
  final String key;
  final int line, page;
  String postedOn, description, amount;
  bool include;
  final List<String> flags;
  final double confidence;
  final String? balance, reference, currency, time;
  final String? originalAmount, originalCurrency, fxRate, fee, vat;
  final RowMatch? match;
  final choice = RowChoice();

  factory PdfRow.fromJson(JsonReader r) {
    final flags = r.optStrings('flags') ?? const <String>[];
    final match = r.optObject('match') == null
        ? null
        : RowMatch.fromJson(r.object('match'));
    return PdfRow(
      key: '${r.optInt('page') ?? 0}:${r.integer('line')}',
      line: r.integer('line'),
      page: r.optInt('page') ?? 1,
      postedOn: r.optString('posted_on') ?? '',
      description: r.optString('description') ?? '',
      amount: dec(r, 'amount') ?? '',
      // A row in another currency than the account's waits for the owner;
      // a row already recorded is left out unless the owner ticks it.
      include:
          !flags.contains('foreign_currency') && (match?.defaultImport ?? true),
      flags: flags,
      confidence: (r.json['confidence'] as num?)?.toDouble() ?? 1,
      balance: dec(r, 'balance'),
      reference: r.optString('reference'),
      currency: r.optString('currency'),
      time: r.optString('time'),
      originalAmount: dec(r, 'original_amount'),
      originalCurrency: r.optString('original_currency'),
      fxRate: dec(r, 'fx_rate'),
      fee: dec(r, 'fee'),
      vat: dec(r, 'vat'),
      match: match,
    );
  }

  /// What the import sends for this row's match: a ticked row the reader
  /// would skip is imported anyway (`action: import`).
  Map<String, Object?> wire() => {
        ...choice.wire(),
        if (match?.defaultImport == false && choice.import == null)
          'action': 'import',
      };

  /// [problem], and for a row in another currency than the account's
  /// ([accountCurrency]) the bank-billed amount the phone does not take.
  String? problemIn(String accountCurrency) {
    final other = currency;
    if (other != null && other.isNotEmpty && other != accountCurrency) {
      return 'problem.foreign';
    }
    return problem;
  }

  /// Why the row cannot be imported as it is, as a message key, or null.
  String? get problem {
    if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(postedOn)) {
      return 'problem.date';
    }
    if (description.trim().isEmpty) return 'problem.description';
    final value = double.tryParse(amount.trim());
    if (value == null) return 'problem.amount';
    if (value == 0) return 'problem.zero';
    return null;
  }
}

/// A PDF statement as the reader saw it (`FinancePdfStatement`).
class PdfStatement {
  const PdfStatement(
      {required this.raw,
      required this.currency,
      required this.filename,
      required this.fileSha256,
      required this.source,
      required this.pageCount,
      required this.columns,
      required this.dateFormat,
      required this.dateFormats,
      required this.dateAmbiguous,
      required this.check,
      required this.rows,
      required this.warnings,
      required this.dropped,
      required this.skippedPages,
      this.fingerprint,
      this.templateId,
      this.periodStart,
      this.periodEnd,
      this.last4,
      this.last4Matches,
      this.opening,
      this.closing});

  /// The answer as sent, for the summary and dropped rows sent back.
  final Map<String, Object?> raw;
  final String currency, filename, fileSha256, source, dateFormat;
  final int pageCount;
  final List<PdfColumn> columns;
  final List<String> dateFormats, warnings;
  final bool dateAmbiguous;
  final BalanceCheck check;
  final List<PdfRow> rows;
  final List<(int, String, String)> dropped;
  final int skippedPages;
  final String? fingerprint, templateId, periodStart, periodEnd;
  final String? last4, opening, closing;
  final bool? last4Matches;

  factory PdfStatement.fromJson(JsonReader r) => PdfStatement(
        raw: r.json,
        currency: r.string('currency'),
        filename: r.optString('filename') ?? 'statement.pdf',
        fileSha256: r.string('file_sha256'),
        source: r.optString('source') ?? 'text',
        pageCount: r.optInt('page_count') ?? 0,
        columns: r.optObjects(
            'columns',
            (c) => PdfColumn(c.integer('index'), c.optString('label') ?? '',
                c.optString('role') ?? 'skip')),
        dateFormat: r.optString('date_format') ?? '',
        dateFormats: r.optStrings('date_formats') ?? const [],
        dateAmbiguous: r.optBool('date_format_ambiguous') ?? false,
        check: BalanceCheck.fromJson(r.object('check')),
        rows: r.optObjects('rows', PdfRow.fromJson),
        warnings: r.optStrings('warnings') ?? const [],
        dropped: r.optObjects(
            'dropped_rows',
            (d) => (
                  d.optInt('page') ?? 0,
                  d.optString('reason') ?? '',
                  d.optString('text') ?? ''
                )),
        skippedPages: (r.optList('skipped_pages') ?? const []).length,
        fingerprint: r.optString('fingerprint'),
        templateId: r.optString('template_id'),
        periodStart: r.optString('period_start'),
        periodEnd: r.optString('period_end'),
        last4: r.optString('account_last4'),
        last4Matches: r.optBool('account_last4_matches'),
        opening: dec(r, 'opening_balance'),
        closing: dec(r, 'closing_balance'),
      );

  /// The import request for the rows (the web's `importBody`): unticked rows
  /// are sent too (`include: false`) so identical rows keep their place.
  Map<String, Object?> importBody(
      {required String accountId,
      required String opening,
      required String closing,
      required bool acceptMismatch,
      List<String>? roles,
      bool remember = false}) {
    String? clean(String text) => text.trim().isEmpty ? null : text.trim();
    final summary = [
      for (final item in (raw['check'] as Map?)?['summary'] as List? ?? [])
        if (item is Map) {'name': item['name'], 'stated': item['stated']}
    ];
    final dropped = [
      for (final item in raw['dropped_rows'] as List? ?? [])
        if (item is Map)
          {
            'page': item['page'],
            'reason': item['reason'],
            'text': item['text'] ?? '',
            'posted_on': item['posted_on'],
            'amount': item['amount'],
          }
    ];
    final sendable = rows.where((row) =>
        row.include ||
        (RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(row.postedOn) &&
            double.tryParse(row.amount.trim()) != null &&
            row.description.trim().isNotEmpty));
    return {
      'account_id': accountId,
      'filename': filename,
      'file_sha256': fileSha256,
      'source': source,
      'rows': [
        for (final row in sendable)
          {
            'posted_on': row.postedOn,
            'description': row.description.trim(),
            'amount': row.amount.trim(),
            'balance': row.balance,
            'reference': row.reference,
            'currency': row.currency,
            'time': row.time,
            'original_amount': row.originalAmount,
            'original_currency': row.originalCurrency,
            'fx_rate': row.fxRate,
            'fee': row.fee,
            'vat': row.vat,
            if (row.include)
              ...row.wire()
            else ...{'action': 'skip', 'include': false},
          }
      ],
      'opening_balance': clean(opening),
      'closing_balance': clean(closing),
      'accept_mismatch': acceptMismatch,
      'layout': fingerprint == null || roles == null
          ? null
          : {
              'fingerprint': fingerprint,
              'labels': [for (final c in columns) c.label],
              'roles': roles,
              'date_format': dateFormat.isEmpty ? null : dateFormat,
              'remember': remember,
            },
      'summary': summary,
      'dropped': dropped,
      'period_start': periodStart,
      'period_end': periodEnd,
    };
  }
}

/// A live balance check over the rows the person kept (the web's
/// `liveCheck`), in whole minor units so nothing rounds.
BalanceCheck liveCheck(List<PdfRow> rows, String opening, String closing) {
  int? cents(String? value) {
    if (value == null) return null;
    final parsed = double.tryParse(value.trim());
    return parsed == null ? null : (parsed * 100).round();
  }

  final included = rows.where((r) => r.include).toList();
  final amounts = [for (final r in included) cents(r.amount)];
  final total = amounts.fold<int>(0, (sum, v) => sum + (v ?? 0));
  final open = cents(opening), close = cents(closing);
  String text(int units) {
    final negative = units < 0;
    final abs = units.abs();
    final whole = abs ~/ 100, part = (abs % 100).toString().padLeft(2, '0');
    return '${negative ? '-' : ''}$whole.$part';
  }

  final breaks = <int>[];
  int? running = open;
  for (var i = 0; i < included.length; i++) {
    final amount = amounts[i];
    if (running != null && amount != null) running += amount;
    final balance = cents(included[i].balance);
    if (balance != null) {
      if (running != null && running != balance) breaks.add(included[i].line);
      running = balance;
    }
  }
  if (open == null || close == null || included.isEmpty) {
    return BalanceCheck(
        status: 'unknown', rowsTotal: text(total), breaks: breaks);
  }
  final expected = open + total, difference = close - expected;
  final unsure = included.any((r) => r.flags.contains('sign_unsure'));
  final status = difference == 0 && breaks.isEmpty && !amounts.contains(null)
      ? (unsure ? 'unknown' : 'matched')
      : 'mismatch';
  return BalanceCheck(
      status: status,
      rowsTotal: text(total),
      opening: text(open),
      closing: text(close),
      expected: text(expected),
      difference: text(difference),
      breaks: breaks);
}

/// The roles a PDF mapping must have before it can be read again (the
/// web's `checkRoles`), as a message key, or null.
String? checkRoles(List<String> roles) {
  if (!roles.contains('date')) return 'pdf.roles.date';
  if (!roles.contains('description')) return 'pdf.roles.description';
  final money = roles.contains('amount') ||
      roles.contains('debit') ||
      roles.contains('credit');
  if (!money) return 'pdf.roles.money';
  return null;
}

extension FinanceImportApi on FinanceMoreRepository {
  Future<JsonReader> _post(String path, Map<String, Object?> body) async =>
      JsonReader.of(await api.request('POST', '$_base$path', body: body));

  Future<ImportFormats> importFormats() async => ImportFormats.fromJson(
      JsonReader.of(await api.request('GET', '$_base/formats')));

  Future<CsvPreview> previewCsv(Map<String, Object?> body) async =>
      CsvPreview.fromJson(await _post('/imports/preview', body));

  Future<StatementImport> importCsv(Map<String, Object?> body) async =>
      StatementImport.fromJson(await _post('/imports', body));

  Future<PdfStatement> readPdf(
      {required String accountId,
      required String filename,
      required Uint8List bytes,
      String? password,
      List<String>? roles,
      String? dateFormat}) async {
    final transport = pdf;
    if (transport == null) {
      throw const ApiException(
          0, 'Reading PDF statements is not available here.');
    }
    final body = await transport.readStatementPdf({
      'account_id': accountId,
      'filename': filename,
      if (roles != null) 'roles': roles.join(','),
      if (dateFormat != null && dateFormat.isNotEmpty)
        'date_format': dateFormat,
    }, bytes, password: password);
    return PdfStatement.fromJson(JsonReader.of(body));
  }

  Future<StatementImport> importPdf(Map<String, Object?> body) async =>
      StatementImport.fromJson(await _post('/imports/pdf', body));
}
