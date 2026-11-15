import '../../data/api_client.dart';
import '../../data/json_read.dart';
import 'finance_more_data.dart' show dec, decOr;

/// Budgets, the salary allocation, recurring payments and the category
/// ontology on the phone (docs/finance.md#budgets-and-planning,
/// #recurring-payments, #category-ontology, #learned-merchants). What the
/// phone reads and sends, mirroring `packages/contracts/src/finance.ts`.
/// Every figure (spent, coming, forecast, risk) is the API's; money stays a
/// decimal string next to its currency and nothing is added across
/// currencies.

List<String> _strings(JsonReader r, String key) =>
    [for (final v in r.optList(key) ?? const <Object?>[]) '$v'];

// -- categories ------------------------------------------------------------------

class SubcategoryNode {
  const SubcategoryNode(
      {required this.id,
      required this.name,
      this.nameAr,
      required this.builtIn,
      required this.archived});
  final String id, name;
  final String? nameAr;
  final bool builtIn, archived;

  String label(bool arabic) => arabic && nameAr != null ? nameAr! : name;

  factory SubcategoryNode.fromJson(JsonReader r) => SubcategoryNode(
      id: r.string('id'),
      name: r.string('name'),
      nameAr: r.optString('name_ar'),
      builtIn: r.optBool('built_in') ?? false,
      archived: r.optBool('archived') ?? false);
}

class CategoryNode {
  const CategoryNode(
      {required this.id,
      required this.name,
      required this.nameAr,
      required this.spending,
      required this.essential,
      required this.subcategories});
  final String id, name, nameAr;
  final bool spending, essential;
  final List<SubcategoryNode> subcategories;

  String label(bool arabic) => arabic ? nameAr : name;

  factory CategoryNode.fromJson(JsonReader r) => CategoryNode(
      id: r.string('id'),
      name: r.string('name'),
      nameAr: r.optString('name_ar') ?? r.string('name'),
      spending: r.optBool('spending') ?? true,
      essential: r.optBool('essential') ?? false,
      subcategories: r.optObjects('subcategories', SubcategoryNode.fromJson));
}

class CategoryGroup {
  const CategoryGroup(
      {required this.id,
      required this.name,
      required this.nameAr,
      required this.categories});
  final String id, name, nameAr;
  final List<CategoryNode> categories;

  String label(bool arabic) => arabic ? nameAr : name;

  factory CategoryGroup.fromJson(JsonReader r) => CategoryGroup(
      id: r.string('id'),
      name: r.string('name'),
      nameAr: r.optString('name_ar') ?? r.string('name'),
      categories: r.optObjects('categories', CategoryNode.fromJson));
}

class CategoryTree {
  const CategoryTree({required this.groups, this.groupTotals = const {}});
  final List<CategoryGroup> groups;

  /// Group id: currency: amount spent this month (never added across).
  final Map<String, Map<String, String>> groupTotals;

  Iterable<CategoryNode> get spendingCategories =>
      groups.expand((g) => g.categories).where((c) => c.spending);

  factory CategoryTree.fromJson(JsonReader r) => CategoryTree(
          groups: r.optObjects('groups', CategoryGroup.fromJson),
          groupTotals: {
            for (final e in (r.optMap('group_totals') ?? const {}).entries)
              e.key: {
                for (final c in ((e.value as Map?) ?? const {}).entries)
                  '${c.key}': '${c.value}'
              }
          });
}

class CategoryMapping {
  const CategoryMapping(
      {required this.merchant,
      required this.direction,
      required this.category,
      required this.confirmations,
      required this.enabled,
      this.example});
  final String merchant, direction, category;
  final int confirmations;
  final bool enabled;
  final String? example;

  factory CategoryMapping.fromJson(JsonReader r) => CategoryMapping(
      merchant: r.string('merchant'),
      direction: r.string('direction'),
      category: r.string('category'),
      confirmations: r.optInt('confirmations') ?? 1,
      enabled: r.optBool('enabled') ?? true,
      example: r.optString('example'));
}

// -- budgets ---------------------------------------------------------------------

const budgetKinds = [
  'spending',
  'savings',
  'emergency',
  'sinking_fund',
  'remittance',
  'debt'
];
const budgetPeriods = ['monthly', 'weekly', 'custom'];
const allocationBuckets = [
  'housing',
  'food',
  'transport',
  'family',
  'debts',
  'cards',
  'savings',
  'remittances',
  'other'
];
const savingKinds = {'savings', 'emergency', 'sinking_fund'};

class ComingBill {
  const ComingBill(
      {required this.name, required this.dueOn, required this.amount});
  final String name, dueOn, amount;
  factory ComingBill.fromJson(JsonReader r) => ComingBill(
      name: r.string('name'),
      dueOn: r.string('due_on'),
      amount: decOr(r, 'amount'));
}

class PlanBudget {
  const PlanBudget({
    required this.id,
    required this.name,
    required this.kind,
    required this.period,
    required this.currency,
    required this.amount,
    required this.periodStart,
    required this.periodEnd,
    required this.categories,
    required this.subcategoryIds,
    required this.fixed,
    required this.essential,
    required this.allocation,
    required this.alertPercent,
    required this.reserve,
    required this.active,
    this.goalId,
    this.debtIds = const [],
    this.notes,
    this.spent,
    this.remaining,
    this.over = '0',
    this.percent,
    this.coming = '0',
    this.comingItems = const [],
    this.forecast,
    this.risk = 'ok',
    this.rows = 0,
    this.goalSaved,
    this.goalTarget,
  });
  final String id, name, kind, period, currency, amount, periodStart, periodEnd;
  final List<String> categories, subcategoryIds, debtIds;
  final bool fixed, essential, reserve, active;
  final String allocation, risk, over, coming;
  final int alertPercent, rows;
  final int? percent;
  final String? goalId,
      notes,
      spent,
      remaining,
      forecast,
      goalSaved,
      goalTarget;
  final List<ComingBill> comingItems;

  factory PlanBudget.fromJson(JsonReader r) => PlanBudget(
        id: r.string('id'),
        name: r.string('name'),
        kind: r.string('kind'),
        period: r.string('period'),
        currency: r.string('currency'),
        amount: decOr(r, 'amount'),
        periodStart: r.string('period_start'),
        periodEnd: r.string('period_end'),
        categories: _strings(r, 'categories'),
        subcategoryIds: _strings(r, 'subcategory_ids'),
        fixed: r.optBool('fixed') ?? false,
        essential: r.optBool('essential') ?? true,
        allocation: r.optString('allocation') ?? 'other',
        alertPercent: r.optInt('alert_percent') ?? 80,
        reserve: r.optBool('reserve_in_safe_to_spend') ?? false,
        active: r.optBool('active') ?? true,
        goalId: r.optString('goal_id'),
        debtIds: _strings(r, 'debt_ids'),
        notes: r.optString('notes'),
        spent: dec(r, 'spent'),
        remaining: dec(r, 'remaining'),
        over: decOr(r, 'over'),
        percent: r.optInt('percent'),
        coming: decOr(r, 'coming'),
        comingItems: r.optObjects('coming_items', ComingBill.fromJson),
        forecast: dec(r, 'forecast'),
        risk: r.optString('risk') ?? 'ok',
        rows: r.optInt('rows') ?? 0,
        goalSaved: dec(r, 'goal_saved'),
        goalTarget: dec(r, 'goal_target'),
      );

  /// The whole budget as the form sends it back (PATCH takes all of it).
  Map<String, Object?> body({bool? active}) => {
        'name': name,
        'kind': kind,
        'period': period,
        'currency': currency,
        'amount': amount,
        'categories': categories,
        'subcategory_ids': subcategoryIds,
        'starts_on': period == 'monthly' ? null : periodStart,
        'ends_on': period == 'custom' ? periodEnd : null,
        'fixed': fixed,
        'essential': essential,
        'allocation': allocation,
        'goal_id': goalId,
        'debt_ids': debtIds,
        'alert_percent': alertPercent,
        'reserve_in_safe_to_spend': reserve,
        'active': active ?? this.active,
        'notes': notes,
      };
}

class PlanTotals {
  const PlanTotals(
      {required this.currency,
      required this.budget,
      required this.spent,
      required this.coming});
  final String currency, budget, spent, coming;
  factory PlanTotals.fromJson(JsonReader r) => PlanTotals(
      currency: r.string('currency'),
      budget: decOr(r, 'budget'),
      spent: decOr(r, 'spent'),
      coming: decOr(r, 'coming'));
}

class PlanReport {
  const PlanReport({required this.budgets, required this.totals});
  final List<PlanBudget> budgets;
  final List<PlanTotals> totals;
  factory PlanReport.fromJson(JsonReader r) => PlanReport(
      budgets: r.optObjects('budgets', PlanBudget.fromJson),
      totals: r.optObjects('totals', PlanTotals.fromJson));
}

class AllocationView {
  const AllocationView(
      {required this.currency,
      this.income,
      this.incomeSource,
      required this.buckets,
      required this.allocated,
      this.unallocated,
      this.leftOut = const []});
  final String currency, allocated;
  final String? income, incomeSource, unallocated;
  final Map<String, String> buckets;
  final List<String> leftOut;

  factory AllocationView.fromJson(JsonReader r) => AllocationView(
        currency: r.string('currency'),
        income: dec(r, 'income'),
        incomeSource: r.optString('income_source'),
        buckets: {
          for (final e in (r.optMap('buckets') ?? const {}).entries)
            e.key: '${e.value}'
        },
        allocated: decOr(r, 'allocated'),
        unallocated: dec(r, 'unallocated'),
        leftOut: [
          for (final l in r.optObjects('left_out', (x) => x.string('name'))) l
        ],
      );
}

// -- recurring -------------------------------------------------------------------

const recurringKinds = [
  'rent',
  'phone',
  'internet',
  'streaming',
  'subscription',
  'insurance',
  'school_fees',
  'loan',
  'remittance',
  'membership',
  'utility',
  'installment',
  'other'
];

class FoundRecurring {
  const FoundRecurring({
    required this.key,
    required this.name,
    required this.amount,
    required this.currency,
    required this.cadence,
    required this.nextDueOn,
    required this.category,
    required this.accountId,
    required this.occurrences,
    required this.kind,
    this.merchant,
    this.lastAmount,
    this.priceChange,
  });
  final String key, name, amount, currency, cadence, nextDueOn, category;
  final String accountId, kind;
  final int occurrences;
  final String? merchant, lastAmount, priceChange;

  factory FoundRecurring.fromJson(JsonReader r) => FoundRecurring(
        key: r.string('key'),
        name: r.string('name'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        cadence: r.string('cadence'),
        nextDueOn: r.string('next_due_on'),
        category: r.string('category'),
        accountId: r.string('account_id'),
        occurrences: r.optInt('occurrences') ?? 0,
        kind: r.optString('kind') ?? 'other',
        merchant: r.optString('merchant'),
        lastAmount: dec(r, 'last_amount'),
        priceChange: dec(r, 'price_change'),
      );
}

class KeptBill {
  const KeptBill({
    required this.id,
    required this.name,
    required this.amount,
    required this.currency,
    required this.cadence,
    required this.category,
    required this.active,
    required this.autoLink,
    required this.stillBillingRarelyUsed,
    this.accountId,
    this.merchant,
    this.recurringKind,
    this.usage,
    this.lastPaidOn,
    this.lastAmount,
    this.priceChange,
  });
  final String id, name, amount, currency, cadence, category;
  final bool active, autoLink, stillBillingRarelyUsed;
  final String? accountId, merchant, recurringKind, usage, lastPaidOn;
  final String? lastAmount, priceChange;

  bool get canAutoLink => merchant != null || accountId != null;

  factory KeptBill.fromJson(JsonReader r) => KeptBill(
        id: r.string('id'),
        name: r.string('name'),
        amount: decOr(r, 'amount'),
        currency: r.string('currency'),
        cadence: r.string('cadence'),
        category: r.string('category'),
        active: r.optBool('active') ?? true,
        autoLink: r.optBool('auto_link') ?? false,
        stillBillingRarelyUsed: r.optBool('still_billing_rarely_used') ?? false,
        accountId: r.optString('account_id'),
        merchant: r.optString('merchant'),
        recurringKind: r.optString('recurring_kind'),
        usage: r.optString('usage'),
        lastPaidOn: r.optString('last_paid_on'),
        lastAmount: dec(r, 'last_amount'),
        priceChange: dec(r, 'price_change'),
      );
}

class RecurringView {
  const RecurringView(
      {required this.found, required this.kept, required this.dismissed});
  final List<FoundRecurring> found;
  final List<KeptBill> kept;
  final int dismissed;
  factory RecurringView.fromJson(JsonReader r) => RecurringView(
      found: r.optObjects('found', FoundRecurring.fromJson),
      kept: r.optObjects('kept', KeptBill.fromJson),
      dismissed: r.optInt('dismissed') ?? 0);
}

/// A goal a savings budget can save into (`GET /goals`).
class GoalChoice {
  const GoalChoice(
      {required this.id, required this.name, required this.currency});
  final String id, name, currency;
}

// -- the repository ----------------------------------------------------------------

class FinanceBudgetRepository {
  FinanceBudgetRepository(this.api);
  final AnumApiClient api;
  static const base = '/api/v1/finance';

  String _q(Map<String, String?> values) {
    final kept = {
      for (final e in values.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value!
    };
    return kept.isEmpty ? '' : '?${Uri(queryParameters: kept).query}';
  }

  Future<JsonReader> _get(String path) async =>
      JsonReader.of(await api.request('GET', '$base$path'));

  Future<CategoryTree> categories({String? month}) async =>
      CategoryTree.fromJson(await _get('/categories${_q({'month': month})}'));

  Future<List<CategoryMapping>> mappings({String q = ''}) async =>
      (await _get('/category-mappings${_q({'q': q, 'limit': '100'})}'))
          .optObjects('items', CategoryMapping.fromJson);

  Future<void> switchMapping(CategoryMapping m, bool enabled) =>
      api.request('PATCH', '$base/category-mappings', body: {
        'merchant': m.merchant,
        'direction': m.direction,
        'enabled': enabled
      });

  Future<void> forgetMapping(CategoryMapping m) => api.request(
      'DELETE',
      '$base/category-mappings${_q({
            'merchant': m.merchant,
            'direction': m.direction
          })}');

  Future<void> addSubcategory(String parent, String name) =>
      api.request('POST', '$base/subcategories',
          body: {'parent': parent, 'name': name, 'archived': false});

  Future<PlanReport> budgets() async =>
      PlanReport.fromJson(await _get('/budgets/plans?include_inactive=true'));

  Future<void> saveBudget(Map<String, Object?> body, {String? id}) => id == null
      ? api.request('POST', '$base/budgets/plans', body: body)
      : api.request('PATCH', '$base/budgets/plans/${Uri.encodeComponent(id)}',
          body: body);

  Future<void> deleteBudget(String id) =>
      api.request('DELETE', '$base/budgets/plans/${Uri.encodeComponent(id)}');

  Future<List<AllocationView>> allocation() async =>
      (await _get('/budgets/allocation'))
          .optObjects('currencies', AllocationView.fromJson);

  Future<List<GoalChoice>> goals() async {
    final body = await api.request('GET', '$base/goals');
    final items = body['data'] ?? body['items'] ?? const <Object?>[];
    return [
      for (final g in items as List<Object?>)
        if (g is Map)
          GoalChoice(
              id: '${g['id']}',
              name: '${g['name']}',
              currency: '${g['currency']}')
    ];
  }

  Future<RecurringView> recurring() async =>
      RecurringView.fromJson(await _get('/recurring'));

  Future<void> confirmRecurring(FoundRecurring f,
          {required String name,
          required String kind,
          required bool autoLink,
          String? usage}) =>
      api.request('POST', '$base/recurring/confirm', body: {
        'key': f.key,
        'name': name,
        'amount': f.lastAmount ?? f.amount,
        'currency': f.currency,
        'cadence': f.cadence,
        'next_due_on': f.nextDueOn,
        'category': f.category,
        'account_id': f.accountId,
        'merchant': f.merchant,
        'recurring_kind': kind,
        'auto_link': autoLink,
        'usage': usage,
      });

  Future<void> rejectRecurring(String key) =>
      api.request('POST', '$base/recurring/reject', body: {'key': key});

  Future<void> recurringSettings(String billId,
          {bool? autoLink, String? usage}) =>
      api.request(
          'PATCH', '$base/recurring/bills/${Uri.encodeComponent(billId)}',
          body: {
            if (autoLink != null) 'auto_link': autoLink,
            if (usage != null) 'usage': usage,
          });

  Future<({int linked, int left})> linkNow() async {
    final r = JsonReader.of(
        await api.request('POST', '$base/recurring/link-now', body: {}));
    return (linked: r.optInt('linked') ?? 0, left: r.optInt('left') ?? 0);
  }
}
