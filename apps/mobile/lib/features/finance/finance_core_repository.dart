import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../meetings/meetings_repository.dart' show MeetingUploadTransfer;
import '../records_kit/records_kit.dart';
import '../vault/vault_data.dart';
import 'finance_core_models.dart';
import 'finance_personal_models.dart';
import 'finance_rules_data.dart';

/// The routes behind the phone's transaction register, a transaction's
/// detail and the salary history (docs/finance.md#transaction-detail,
/// docs/finance.md#salary). Every row is the caller's own; the server checks
/// `finance:read` and `finance:manage` on each call.
class FinanceCoreRepository {
  const FinanceCoreRepository(this.api, {this.uploads});
  final AnumApiClient api;

  /// The app's file transfer, for a receipt sent to the vault; null where
  /// uploading is not available (then Attach receipt is not offered).
  final MeetingUploadTransfer? uploads;
  static const base = '/api/v1/finance';
  static const pageSize = 25;

  String _id(String value) => Uri.encodeComponent(value);

  /// Transactions with the filters, paged on the server.
  Future<RegisterPage> register(RegisterFilters filters,
          {int offset = 0, int limit = pageSize}) async =>
      RegisterPage.fromJson(JsonReader.of(await api.request(
          'GET',
          '$base/register${recordsQuery({
                ...filters.toQuery(),
                'limit': limit,
                'offset': offset
              })}')));

  Future<RegisterDetail> detail(String id) async => RegisterDetail.fromJson(
      JsonReader.of(await api.request('GET', '$base/register/${_id(id)}')));

  /// The owner's note: never changes the amount or the category.
  Future<void> saveNote(String id, String? note) =>
      api.request('PATCH', '$base/transactions/${_id(id)}',
          body: {'notes': (note ?? '').trim().isEmpty ? null : note!.trim()});

  /// The owner's category: a rule never changes it afterwards. A row an SMS or
  /// Apple Pay recorded answers 409 (it is corrected under iPhone SMS, Review).
  Future<void> saveCategory(String id, String category) =>
      api.request('PATCH', '$base/transactions/${_id(id)}',
          body: {'category': category});

  /// The detail's Save (docs/finance.md#transaction-types,
  /// #subcategories, #learned-merchants, #rules-and-learning): only what the
  /// owner changed is sent. [subcategorySet] sends [subcategoryId] (null
  /// clears it); [remember] saves a learned phrase; [learnMerchant] lets this
  /// correction file the merchant's later payments the same way; [fromAi]
  /// marks a category taken from the AI answer, which history never learns.
  Future<void> saveCorrection(String id,
          {String? category,
          String? type,
          bool subcategorySet = false,
          String? subcategoryId,
          String? remember,
          bool learnMerchant = false,
          bool fromAi = false}) =>
      api.request('PATCH', '$base/transactions/${_id(id)}', body: {
        if (category != null) 'category': category,
        if (type != null) 'transaction_type': type,
        if (subcategorySet) 'subcategory_id': subcategoryId,
        if (remember != null) 'remember_category_match': remember,
        if (learnMerchant) 'learn_merchant': true,
        if (fromAi) 'from_ai_suggestion': true,
      });

  /// Money in recorded by mistake, corrected to money out under [category]
  /// (a spending category); the ledger reverses and reposts the entry.
  Future<void> correctToMoneyOut(String id, String amount, String category) =>
      api.request('PATCH', '$base/transactions/${_id(id)}', body: {
        'amount': '-${amount.replaceFirst(RegExp(r'^[+-]'), '')}',
        'category': category,
      });

  /// Deletes the wrong copy of a duplicate.
  Future<void> deleteTransaction(String id) =>
      api.request('DELETE', '$base/transactions/${_id(id)}');

  /// The built-in subcategories (from the category tree) and the owner's
  /// own, each under its parent category.
  Future<List<SubcategoryChoice>> subcategoryChoices() async {
    final tree = JsonReader.of(await api.request('GET', '$base/categories'));
    final own = JsonReader.of(await api.request('GET', '$base/subcategories'));
    return [
      for (final g in tree.optObjects('groups', (r) => r))
        for (final c in g.optObjects('categories', (r) => r))
          for (final x in c.optObjects('subcategories', (r) => r))
            if (x.optBool('built_in') ?? false)
              SubcategoryChoice(
                  id: x.string('id'),
                  parent: c.string('id'),
                  name: x.string('name'),
                  nameAr: x.optString('name_ar'),
                  builtIn: true),
      for (final x in own.optObjects('data', (r) => r))
        SubcategoryChoice(
            id: x.string('id'),
            parent: x.string('parent'),
            name: x.string('name'),
            archived: x.optBool('archived') ?? false),
    ];
  }

  /// "Apply to similar": the rule this correction suggests and the other
  /// rows it would change. Nothing is saved.
  Future<SimilarProposal> similar(String id, {bool thisAccount = false}) async {
    final r = JsonReader.of(await api.request(
        'POST', '$base/transactions/${_id(id)}/similar',
        body: {'scope': thisAccount ? 'account' : 'all'}));
    return SimilarProposal(
        content: RuleContentSpec.fromJson(r.object('content')),
        preview: RulePreview.fromJson(r.object('preview')),
        sourceTransactionId: r.optString('source_transaction_id') ?? id);
  }

  /// Saves the suggested rule and approves the version the server returns
  /// with its payload hash (two explicit steps). With [apply], previews it
  /// again and applies it with that preview's hash as one batch that can be
  /// undone. Answers how many rows changed and the batch id (null when
  /// nothing was applied).
  Future<({int changed, String? batchId})> saveSimilar(SimilarProposal p,
      {required bool apply}) async {
    final proposed = RuleVersion.fromJson(
        JsonReader.of(await api.request('POST', '$base/rules', body: {
      'content': p.content.wire(),
      'source_transaction_id': p.sourceTransactionId,
    })));
    final approved = RuleVersion.fromJson(JsonReader.of(await api.request(
        'POST', '$base/rules/${_id(proposed.ruleId)}/approve', body: {
      'version': proposed.version,
      'payload_hash': proposed.payloadHash
    })));
    if (!apply) return (changed: 0, batchId: null);
    final preview = RulePreview.fromJson(JsonReader.of(await api.request(
        'POST', '$base/rules/${_id(approved.ruleId)}/preview')));
    if (preview.changes == 0) return (changed: 0, batchId: null);
    final batch = RuleBatch.fromJson(JsonReader.of(await api.request(
        'POST', '$base/rules/${_id(approved.ruleId)}/apply',
        body: {'preview_hash': preview.hash})));
    return (changed: batch.changed, batchId: batch.id);
  }

  /// Undoes an applied batch; answers how many rows were put back.
  Future<int> undoBatch(String batchId) async =>
      JsonReader.of(await api.request(
              'POST', '$base/rule-batches/${_id(batchId)}/undo'))
          .optInt('restored') ??
      0;

  /// The workspace model's category for the row, shown only: never saved,
  /// never learned. The API answers 409 when AI help is off or no model is
  /// set up, with the reason.
  Future<({String? category, String? type})> aiSuggestion(String id) async {
    final r = JsonReader.of(await api.request(
        'POST', '$base/transactions/${_id(id)}/ai-suggestion'));
    return (
      category: r.optString('category'),
      type: r.optString('transaction_type')
    );
  }

  /// A receipt kept privately in the document vault and linked to the
  /// payment's canonical transaction (docs/finance.md#transaction-detail).
  Future<void> attachReceipt(
      {required String path,
      required String name,
      required String canonicalId,
      required String description}) async {
    final vault = ApiVaultRepository(api, transfer: uploads);
    final dot = name.lastIndexOf('.');
    final title = (dot > 0 ? name.substring(0, dot) : name).trim();
    final doc = await vault.upload(path,
        name: name,
        title: title.isEmpty ? description : title,
        category: 'receipt',
        accessClass: 'private');
    await vault.addLink(doc.id,
        targetType: 'finance_transaction',
        targetId: canonicalId,
        label: description.length > 200
            ? description.substring(0, 200)
            : description);
  }

  /// Expected against received, month by month, newest first.
  Future<(List<SalaryMonth>, int, String?)> salaryMonths(
      {int offset = 0, int limit = 12}) async {
    final r = JsonReader.of(await api.request(
        'GET',
        '$base/salary/months${recordsQuery({
              'limit': limit,
              'offset': offset
            })}'));
    return (
      r.optObjects('months', SalaryMonth.fromJson),
      r.optInt('total') ?? 0,
      r.optString('currency'),
    );
  }
}
