import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';
import 'finance_more_data.dart';

/// Owner rules, merchant spellings, subcategories and automation policies
/// (docs/finance.md#rules-and-learning, #automation-policies): what the phone
/// reads and sends, mirroring `packages/contracts/src/financeRules.ts`. A
/// rule runs only once the owner approves a version with the payload hash
/// they were shown; Apply changes earlier rows only with the preview hash
/// the owner saw, as one batch that can be undone.

const _base = FinanceMoreRepository.base;
String _id(String value) => Uri.encodeComponent(value);

/// What a rule matches (`RuleMatch`). Every condition given must hold.
class RuleMatchSpec {
  const RuleMatchSpec(
      {this.merchant,
      this.words = const [],
      this.reference,
      this.direction = 'any',
      this.minAmount,
      this.maxAmount,
      this.currency,
      this.accountIds = const [],
      this.senders = const []});
  final String? merchant, reference, minAmount, maxAmount, currency;
  final List<String> words, accountIds, senders;
  final String direction;

  factory RuleMatchSpec.fromJson(JsonReader r) => RuleMatchSpec(
        merchant: r.optString('merchant'),
        words: r.optStrings('words') ?? const [],
        reference: r.optString('reference'),
        direction: r.optString('direction') ?? 'any',
        minAmount: dec(r, 'min_amount'),
        maxAmount: dec(r, 'max_amount'),
        currency: r.optString('currency'),
        accountIds: r.optStrings('account_ids') ?? const [],
        senders: r.optStrings('senders') ?? const [],
      );

  Map<String, Object?> wire() => {
        'merchant': merchant,
        'words': words,
        'reference': reference,
        'direction': direction,
        'min_amount': minAmount,
        'max_amount': maxAmount,
        'currency': currency,
        'account_ids': accountIds,
        'senders': senders,
      };
}

/// What one version of a rule says (`RuleContent`): the part the payload
/// hash covers.
class RuleContentSpec {
  const RuleContentSpec(
      {required this.match,
      required this.category,
      this.subcategoryId,
      this.transactionType,
      this.priority = 100,
      this.enabled = true,
      this.autoApply = false,
      this.note});
  final RuleMatchSpec match;
  final String category;
  final String? subcategoryId, transactionType, note;
  final int priority;
  final bool enabled, autoApply;

  factory RuleContentSpec.fromJson(JsonReader r) => RuleContentSpec(
        match: RuleMatchSpec.fromJson(r.object('match')),
        category: r.string('category'),
        subcategoryId: r.optString('subcategory_id'),
        transactionType: r.optString('transaction_type'),
        priority: r.optInt('priority') ?? 100,
        enabled: r.optBool('enabled') ?? true,
        autoApply: r.optBool('auto_apply') ?? false,
        note: r.optString('note'),
      );

  Map<String, Object?> wire() => {
        'match': match.wire(),
        'category': category,
        'subcategory_id': subcategoryId,
        'transaction_type': transactionType,
        'priority': priority,
        'enabled': enabled,
        'auto_apply': autoApply,
        'note': note,
      };

  /// What it matches, in a short line: the merchant, the words and the
  /// reference the owner typed.
  String get summary => [
        if (match.merchant != null) match.merchant!,
        ...match.words,
        if (match.reference != null) match.reference!,
      ].join(', ');
}

/// One version of a rule (`RuleVersionView`).
class RuleVersion {
  const RuleVersion(
      {required this.ruleId,
      required this.version,
      required this.status,
      required this.content,
      required this.provenance,
      required this.payloadHash,
      required this.createdAt,
      this.decidedAt,
      this.revertedFrom,
      this.sourceTransactionId});
  final String ruleId, status, provenance, payloadHash, createdAt;
  final int version;
  final RuleContentSpec content;
  final String? decidedAt, sourceTransactionId;
  final int? revertedFrom;

  factory RuleVersion.fromJson(JsonReader r) => RuleVersion(
        ruleId: r.string('rule_id'),
        version: r.integer('version'),
        status: r.string('status'),
        content: RuleContentSpec.fromJson(r.object('content')),
        provenance: r.optString('provenance') ?? 'owner',
        payloadHash: r.string('payload_hash'),
        createdAt: r.optString('created_at') ?? '',
        decidedAt: r.optString('decided_at'),
        revertedFrom: r.optInt('reverted_from'),
        sourceTransactionId: r.optString('source_transaction_id'),
      );
}

/// A rule in the list: the version that runs and a change waiting.
class RuleSummary {
  const RuleSummary(this.ruleId, this.current, this.pending, this.versions);
  final String ruleId;
  final RuleVersion? current, pending;
  final int versions;

  RuleVersion get shown => (current ?? pending)!;

  factory RuleSummary.fromJson(JsonReader r) => RuleSummary(
        r.string('rule_id'),
        r.optObject('current') == null
            ? null
            : RuleVersion.fromJson(r.object('current')),
        r.optObject('pending') == null
            ? null
            : RuleVersion.fromJson(r.object('pending')),
        r.optInt('versions') ?? 1,
      );
}

/// A page of rules, with how many changes wait and how many plain phrase
/// rules are still read from the older list.
class RulePage {
  const RulePage(this.chunk, this.pending, this.legacy);
  final RecordsChunk<RuleSummary> chunk;
  final int pending, legacy;
}

/// One row a rule would change (`PreviewRowView`).
class RulePreviewRow {
  const RulePreviewRow(
      {required this.transactionId,
      required this.postedOn,
      required this.description,
      required this.amount,
      required this.currency,
      required this.before,
      required this.after,
      this.beforeType,
      this.afterType});
  final String transactionId, postedOn, description, amount, currency;
  final String before, after;
  final String? beforeType, afterType;

  factory RulePreviewRow.fromJson(JsonReader r) => RulePreviewRow(
        transactionId: r.string('transaction_id'),
        postedOn: r.string('posted_on'),
        description: r.optString('description') ?? '',
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        before: r.optString('before_category') ?? 'other',
        after: r.optString('after_category') ?? 'other',
        beforeType: r.optString('before_type'),
        afterType: r.optString('after_type'),
      );
}

/// Which rows a rule would change and which it leaves alone
/// (`PreviewView`). Apply sends [hash] back.
class RulePreview {
  const RulePreview(
      {required this.matched,
      required this.changes,
      required this.unchanged,
      required this.manualKept,
      required this.smsKept,
      required this.truncated,
      required this.hash,
      required this.rows});
  final int matched, changes, unchanged, manualKept, smsKept;
  final bool truncated;
  final String hash;
  final List<RulePreviewRow> rows;

  factory RulePreview.fromJson(JsonReader r) => RulePreview(
        matched: r.optInt('matched') ?? 0,
        changes: r.optInt('changes') ?? 0,
        unchanged: r.optInt('unchanged') ?? 0,
        manualKept: r.optInt('manual_kept') ?? 0,
        smsKept: r.optInt('sms_kept') ?? 0,
        truncated: r.optBool('truncated') ?? false,
        hash: r.string('preview_hash'),
        rows: r.optObjects('rows', RulePreviewRow.fromJson),
      );
}

/// A rule apply or accepted suggestion that changed rows (`BatchView`).
class RuleBatch {
  const RuleBatch(
      {required this.id,
      required this.origin,
      required this.changed,
      required this.status,
      required this.createdAt,
      this.ruleId,
      this.ruleVersion});
  final String id, origin, status, createdAt;
  final int changed;
  final String? ruleId;
  final int? ruleVersion;

  bool get undone => status != 'applied';

  factory RuleBatch.fromJson(JsonReader r) => RuleBatch(
        id: r.string('id'),
        origin: r.optString('origin') ?? 'owner',
        changed: r.optInt('changed') ?? 0,
        status: r.optString('status') ?? 'applied',
        createdAt: r.optString('created_at') ?? '',
        ruleId: r.optString('rule_id'),
        ruleVersion: r.optInt('rule_version'),
      );
}

/// One spelling of a merchant (`AliasView`).
class MerchantSpelling {
  const MerchantSpelling(this.id, this.alias, this.merchant);
  final String id, alias, merchant;
  factory MerchantSpelling.fromJson(JsonReader r) =>
      MerchantSpelling(r.string('id'), r.string('alias'), r.string('merchant'));
}

/// A subcategory under a built-in category (`SubcategoryView`).
class FinanceSubcategory {
  const FinanceSubcategory(this.id, this.parent, this.name, this.archived);
  final String id, parent, name;
  final bool archived;
  factory FinanceSubcategory.fromJson(JsonReader r) => FinanceSubcategory(
      r.string('id'),
      r.string('parent'),
      r.string('name'),
      r.optBool('archived') ?? false);
}

/// The exception notices a policy can turn on.
const noticeKinds = [
  'reconciliation_overdue',
  'statement_missing',
  'uncategorized_money',
  'business_unposted',
  'evidence_missing',
];

/// The owner's automation switches (`AutomationPolicy`): off by default.
class AutomationPolicySpec {
  const AutomationPolicySpec(
      {this.autoClassify = false,
      this.minConfidence = '0.9',
      this.sources = const ['rule'],
      this.notifyUnresolved = false,
      this.notifyMinItems = 1,
      this.notices = const [],
      this.exceptionDays = 7,
      this.uncategorizedMin = '500'});
  final bool autoClassify, notifyUnresolved;
  final String minConfidence, uncategorizedMin;
  final List<String> sources, notices;
  final int notifyMinItems, exceptionDays;

  factory AutomationPolicySpec.fromJson(JsonReader r) => AutomationPolicySpec(
        autoClassify: r.optBool('auto_classify') ?? false,
        minConfidence: dec(r, 'min_confidence') ?? '0.9',
        sources: r.optStrings('sources') ?? const ['rule'],
        notifyUnresolved: r.optBool('notify_unresolved') ?? false,
        notifyMinItems: r.optInt('notify_min_items') ?? 1,
        notices: r.optStrings('notices') ?? const [],
        exceptionDays: r.optInt('notice_exception_days') ?? 7,
        uncategorizedMin: dec(r, 'notice_uncategorized_min') ?? '500',
      );

  Map<String, Object?> wire() => {
        'auto_classify': autoClassify,
        'min_confidence': minConfidence,
        'sources': sources,
        'notify_unresolved': notifyUnresolved,
        'notify_min_items': notifyMinItems,
        'notices': notices,
        'notice_exception_days': exceptionDays,
        'notice_uncategorized_min': uncategorizedMin,
      };
}

/// The policy, when it was last changed and what is never automated.
class AutomationState {
  const AutomationState(this.policy, this.updatedAt, this.never);
  final AutomationPolicySpec policy;
  final String? updatedAt;
  final List<String> never;
  factory AutomationState.fromJson(JsonReader r) => AutomationState(
        AutomationPolicySpec.fromJson(r.object('policy')),
        r.optString('updated_at'),
        r.optStrings('never_automated') ?? const [],
      );
}

extension FinanceRulesApi on FinanceMoreRepository {
  Future<JsonReader> _do(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  Future<RulePage> ownerRules(
      {String state = 'all', String query = '', String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _do(
        'GET',
        '/rules${recordsQuery({
              'state': state.isEmpty ? 'all' : state,
              'q': query,
              'limit': FinanceMoreRepository.pageSize,
              'offset': offset,
            })}');
    return RulePage(
        RecordsChunk.offsetPage(page, RuleSummary.fromJson, offset: offset),
        page.optInt('pending') ?? 0,
        page.optInt('legacy_rules') ?? 0);
  }

  /// Every version of one rule, newest first.
  Future<List<RuleVersion>> ruleVersions(String ruleId) async =>
      (await _do('GET', '/rules/${_id(ruleId)}'))
          .optObjects('versions', RuleVersion.fromJson);

  /// A new rule or a changed version, proposed: it runs once approved.
  Future<RuleVersion> proposeRule(String? ruleId, RuleContentSpec content,
          {String? sourceTransactionId}) async =>
      RuleVersion.fromJson(await _do('POST',
          ruleId == null ? '/rules' : '/rules/${_id(ruleId)}/versions', {
        'content': content.wire(),
        if (ruleId == null && sourceTransactionId != null)
          'source_transaction_id': sourceTransactionId,
      }));

  /// Approves the version the owner was shown, with its payload hash.
  Future<RuleVersion> approveRule(RuleVersion shown) async =>
      RuleVersion.fromJson(await _do(
          'POST',
          '/rules/${_id(shown.ruleId)}/approve',
          {'version': shown.version, 'payload_hash': shown.payloadHash}));

  Future<RuleVersion> rejectRule(RuleVersion shown) async =>
      RuleVersion.fromJson(await _do('POST',
          '/rules/${_id(shown.ruleId)}/reject', {'version': shown.version}));

  /// A new version with an older one's content, proposed.
  Future<RuleVersion> revertRule(String ruleId, int toVersion) async =>
      RuleVersion.fromJson(await _do(
          'POST', '/rules/${_id(ruleId)}/revert', {'to_version': toVersion}));

  /// Which rows an unsaved rule would change; nothing is saved.
  Future<RulePreview> previewDraftRule(RuleContentSpec content) async =>
      RulePreview.fromJson(
          await _do('POST', '/rules/preview', {'content': content.wire()}));

  /// The rows the approved version would change on earlier rows.
  Future<RulePreview> previewRule(String ruleId) async =>
      RulePreview.fromJson(await _do('POST', '/rules/${_id(ruleId)}/preview'));

  /// Applies the approved version to the rows the preview showed.
  Future<RuleBatch> applyRule(String ruleId, String previewHash) async =>
      RuleBatch.fromJson(await _do('POST', '/rules/${_id(ruleId)}/apply',
          {'preview_hash': previewHash}));

  Future<RecordsChunk<RuleBatch>> ruleBatches({String? token}) async {
    final offset = int.tryParse(token ?? '0') ?? 0;
    final page = await _do(
        'GET',
        '/rule-batches${recordsQuery({
              'limit': FinanceMoreRepository.pageSize,
              'offset': offset
            })}');
    return RecordsChunk.offsetPage(page, RuleBatch.fromJson, offset: offset);
  }

  /// Undoes a batch: answers how many rows were restored and how many were
  /// kept because they changed since.
  Future<({int restored, int kept})> undoRuleBatch(String id) async {
    final r = await _do('POST', '/rule-batches/${_id(id)}/undo');
    return (restored: r.optInt('restored') ?? 0, kept: r.optInt('kept') ?? 0);
  }

  Future<List<MerchantSpelling>> merchantSpellings() async =>
      (await _do('GET', '/merchants/aliases'))
          .optObjects('data', MerchantSpelling.fromJson);

  Future<MerchantSpelling> saveMerchantSpelling(
          String? id, String alias, String merchant) async =>
      MerchantSpelling.fromJson(await _do(
          id == null ? 'POST' : 'PATCH',
          id == null ? '/merchants/aliases' : '/merchants/aliases/${_id(id)}',
          {'alias': alias, 'merchant': merchant}));

  Future<void> deleteMerchantSpelling(String id) =>
      api.request('DELETE', '$_base/merchants/aliases/${_id(id)}');

  Future<List<FinanceSubcategory>> subcategories() async =>
      (await _do('GET', '/subcategories'))
          .optObjects('data', FinanceSubcategory.fromJson);

  Future<FinanceSubcategory> saveSubcategory(String? id,
          {required String parent,
          required String name,
          bool archived = false}) async =>
      FinanceSubcategory.fromJson(await _do(
          id == null ? 'POST' : 'PATCH',
          id == null ? '/subcategories' : '/subcategories/${_id(id)}',
          {'parent': parent, 'name': name, 'archived': archived}));

  Future<AutomationState> automation() async =>
      AutomationState.fromJson(await _do('GET', '/automation'));

  Future<AutomationState> saveAutomation(AutomationPolicySpec policy) async =>
      AutomationState.fromJson(await _do('PUT', '/automation', policy.wire()));
}
