import '../../data/json_read.dart';

/// Finance (docs/finance.md): what the phone reads from `/api/v1/finance`.
/// Money stays a decimal string next to its currency, exactly as the API sends
/// it; the phone only formats it for display and never adds two currencies.

/// A wire amount ("1234.50") shown with its currency code and grouping,
/// for display only: `AED 1,234.50`, `AED -45.00`.
String formatAmount(String value, String currency) {
  final match = RegExp(r'^(-?)(\d+)(?:\.(\d+))?$').firstMatch(value.trim());
  if (match == null) return '-';
  final whole = match
      .group(2)!
      .replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  final fraction = match.group(3);
  final number =
      fraction == null || fraction.isEmpty ? whole : '$whole.$fraction';
  return '$currency ${match.group(1)}$number';
}

/// Per-currency figures side by side, never summed: `AED 10.00 · USD 5.00`.
String formatAmounts(List<CurrencyAmount> amounts) => amounts.isEmpty
    ? '-'
    : amounts.map((a) => formatAmount(a.amount, a.currency)).join(' · ');

/// A typed amount as the wire string, or null when it is not a number with at
/// most [decimals] decimals ("1,234.5" and "-45" are read).
String? parseAmount(String input, {int decimals = 2, bool signed = true}) {
  final text = input.trim().replaceAll(',', '').replaceAll(' ', '');
  final match = RegExp(r'^(-?)(\d{1,12})(?:\.(\d+))?$').firstMatch(text);
  if (match == null) return null;
  if ((match.group(3)?.length ?? 0) > decimals) return null;
  if (!signed && match.group(1)!.isNotEmpty) return null;
  return text;
}

class CurrencyAmount {
  const CurrencyAmount(this.currency, this.amount);
  final String currency, amount;
  factory CurrencyAmount.fromJson(JsonReader r) =>
      CurrencyAmount(r.string('currency'), r.string('amount'));
}

class FinanceAccount {
  const FinanceAccount(
      {required this.id,
      required this.name,
      required this.kind,
      required this.currency,
      required this.balance,
      this.institution,
      this.last4,
      this.archived = false,
      this.transactionCount = 0});
  final String id, name, kind, currency, balance;
  final String? institution, last4;
  final bool archived;
  final int transactionCount;

  factory FinanceAccount.fromJson(JsonReader r) => FinanceAccount(
        id: r.string('id'),
        name: r.string('name'),
        kind: r.string('kind'),
        currency: r.string('currency'),
        balance: r.string('balance'),
        institution: r.optString('institution'),
        last4: r.optString('last4'),
        archived: r.optBool('archived') ?? false,
        transactionCount: r.optInt('transaction_count') ?? 0,
      );
}

class CategorySpend {
  const CategorySpend(this.category, this.currency, this.amount, this.count);
  final String category, currency, amount;
  final int count;
  factory CategorySpend.fromJson(JsonReader r) => CategorySpend(
      r.string('category'),
      r.string('currency'),
      r.string('amount'),
      r.integer('count'));
}

class UpcomingBill {
  const UpcomingBill(
      {required this.id,
      required this.name,
      required this.amount,
      required this.currency,
      required this.dueOn,
      required this.daysUntil,
      required this.category});
  final String id, name, amount, currency, dueOn, category;
  final int daysUntil;
  factory UpcomingBill.fromJson(JsonReader r) => UpcomingBill(
        id: r.string('id'),
        name: r.string('name'),
        amount: r.string('amount'),
        currency: r.string('currency'),
        dueOn: r.string('due_on'),
        daysUntil: r.integer('days_until'),
        category: r.string('category'),
      );
}

class GoalProgress {
  const GoalProgress(
      this.id, this.name, this.currency, this.target, this.saved, this.percent);
  final String id, name, currency, target, saved;
  final int percent;
  factory GoalProgress.fromJson(JsonReader r) => GoalProgress(
      r.string('id'),
      r.string('name'),
      r.string('currency'),
      r.string('target_amount'),
      r.string('saved_amount'),
      r.integer('percent'));
}

class FinanceSummary {
  const FinanceSummary({
    required this.month,
    required this.baseCurrency,
    required this.accounts,
    required this.balances,
    required this.spendByCategory,
    required this.income,
    required this.spend,
    required this.net,
    required this.upcomingBills,
    required this.billsDue,
    required this.debts,
    required this.minimumPayments,
    required this.debtCount,
    required this.goals,
  });
  final String month, baseCurrency;
  final List<FinanceAccount> accounts;
  final List<CurrencyAmount> balances,
      income,
      spend,
      net,
      billsDue,
      debts,
      minimumPayments;
  final List<CategorySpend> spendByCategory;
  final List<UpcomingBill> upcomingBills;
  final int debtCount;
  final List<GoalProgress> goals;

  factory FinanceSummary.fromJson(JsonReader r) {
    List<CurrencyAmount> amounts(String key) =>
        r.optObjects(key, CurrencyAmount.fromJson);
    return FinanceSummary(
      month: r.string('month'),
      baseCurrency: r.string('base_currency'),
      accounts: r.optObjects(
          'accounts',
          (a) => FinanceAccount(
                id: a.string('id'),
                name: a.string('name'),
                kind: a.string('kind'),
                currency: a.string('currency'),
                balance: a.string('balance'),
                institution: a.optString('institution'),
                last4: a.optString('last4'),
                archived: a.optBool('archived') ?? false,
              )),
      balances: amounts('balances'),
      spendByCategory:
          r.optObjects('spend_by_category', CategorySpend.fromJson),
      income: amounts('income'),
      spend: amounts('spend'),
      net: amounts('net'),
      upcomingBills: r.optObjects('upcoming_bills', UpcomingBill.fromJson),
      billsDue: amounts('bills_due'),
      debts: amounts('debts'),
      minimumPayments: amounts('minimum_payments'),
      debtCount: r.optInt('debt_count') ?? 0,
      goals: r.optObjects('goals', GoalProgress.fromJson),
    );
  }

  /// The figure in the base currency, else the first one, else zero.
  CurrencyAmount pick(List<CurrencyAmount> amounts) =>
      amounts.where((a) => a.currency == baseCurrency).firstOrNull ??
      amounts.firstOrNull ??
      CurrencyAmount(baseCurrency, '0');
}

class FinanceTransaction {
  const FinanceTransaction(
      {required this.id,
      required this.accountId,
      required this.postedOn,
      required this.amount,
      required this.currency,
      required this.description,
      required this.category,
      this.notes});
  final String id, accountId, postedOn, amount, currency, description, category;
  final String? notes;
  bool get moneyIn => !amount.startsWith('-') && amount != '0.00';

  factory FinanceTransaction.fromJson(JsonReader r) => FinanceTransaction(
        id: r.string('id'),
        accountId: r.string('account_id'),
        postedOn: r.string('posted_on'),
        amount: r.string('amount'),
        currency: r.string('currency'),
        description: r.string('description'),
        category: r.string('category'),
        notes: r.optString('notes'),
      );
}

class TransactionPage {
  const TransactionPage(this.items, this.total);
  final List<FinanceTransaction> items;
  final int total;
  factory TransactionPage.fromJson(JsonReader r) => TransactionPage(
      r.optObjects('items', FinanceTransaction.fromJson), r.integer('total'));
}

/// A bill, debt or goal as the phone lists it: a title, a line of detail and
/// the amount that matters (due amount, balance owed, target).
class FinanceItem {
  const FinanceItem(
      {required this.id,
      required this.name,
      required this.amount,
      required this.currency,
      this.detail = ''});
  final String id, name, amount, currency, detail;

  factory FinanceItem.bill(JsonReader r) => FinanceItem(
      id: r.string('id'),
      name: r.string('name'),
      amount: r.string('amount'),
      currency: r.string('currency'),
      detail: '${r.string('cadence')} ${r.string('next_due_on')}');
  factory FinanceItem.debt(JsonReader r) => FinanceItem(
      id: r.string('id'),
      name: r.string('name'),
      amount: r.string('balance'),
      currency: r.string('currency'),
      detail: '${r.string('kind')} ${r.string('apr_percent')}');
  factory FinanceItem.goal(JsonReader r) => FinanceItem(
      id: r.string('id'),
      name: r.string('name'),
      amount: r.string('target_amount'),
      currency: r.string('currency'),
      detail: r.string('saved_amount'));
}
