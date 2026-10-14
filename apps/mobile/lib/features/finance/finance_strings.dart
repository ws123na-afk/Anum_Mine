import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the Finance screen (docs/finance.md). The
/// terms follow the web catalog (apps/web/src/lib/messages/finance.*.ts).
class FinanceStrings {
  const FinanceStrings(this.arabic);
  final bool arabic;

  static FinanceStrings of(BuildContext context) =>
      FinanceStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? _ar : _en)[key] ?? _en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  static const _en = <String, String>{
    'title': 'Finance',
    'subtitle':
        'Your own spending, balances, bills and goals. Only you can see them.',
    'readOnly': 'Read only: ANUM never pays, transfers or trades from here.',
    'section.overview': 'Overview',
    'section.transactions': 'Transactions',
    'section.accounts': 'Accounts',
    'section.bills': 'Bills',
    'section.debts': 'Debts',
    'section.goals': 'Goals',
    'sections': 'Finance sections',
    'balance': 'Balance',
    'spent': 'Spent',
    'income': 'Income',
    'bills30': 'Bills in 30 days',
    'debts': 'Debts',
    'none': 'None',
    'byCategory': 'Spending by category',
    'noSpending': 'No spending this month yet.',
    'upcoming': 'Bills due soon',
    'noBillsDue': 'No bills due in the next 30 days.',
    'dueIn': 'Due in {n} days',
    'dueToday': 'Due today',
    'accounts': 'Balances',
    'loading': 'Loading your finance...',
    'loadingList': 'Loading...',
    'denied':
        'Finance is for workspace owners and members, and each person sees only their own records. Ask a workspace owner to change your role from viewer to member.',
    'summaryFailed': 'Finance could not be loaded: {message}',
    'listFailed': 'This list could not be loaded: {message}',
    'welcome': 'Start with an account',
    'welcomeText':
        'Add the bank account or card a statement comes from, then import the statement as a CSV file on the web.',
    'addAccount': 'Add account',
    'noAccounts': 'No accounts yet',
    'noAccountsText':
        'Add a bank account, card, cash wallet, loan or investment account.',
    'search': 'Search descriptions',
    'noTransactions': 'No transactions yet',
    'noTransactionsText':
        'Import a statement on the web, or add a transaction here.',
    'noMatch': 'Nothing matches. Try other words.',
    'addTransaction': 'Add transaction',
    'more': 'Load more ({shown} of {total})',
    'addBill': 'Add bill',
    'noBills': 'No bills yet',
    'noBillsText':
        'Add rent, utilities or school fees to see what is due before it is due.',
    'noDebts': 'No debts recorded',
    'noDebtsText':
        'Add loans and card balances on the web to see what you owe in one place.',
    'noGoals': 'No goals yet',
    'noGoalsText':
        'Set a target, such as an emergency fund, on the web and track it here.',
    'delete': 'Delete',
    'deleteTitle': 'Delete {name}?',
    'deleteText': 'It is removed from your finance records.',
    'cancel': 'Cancel',
    'save': 'Save',
    'name': 'Name',
    'kind': 'Type',
    'last4': 'Last four digits (optional)',
    'amount': 'Amount, minus for money out',
    'amountPositive': 'Amount',
    'description': 'Description',
    'date': 'Date (YYYY-MM-DD)',
    'account': 'Account',
    'badAmount': 'Enter an amount such as 45.50 or -45.50.',
    'badText': 'Fill in every field.',
    'badLast4': 'Enter only the last four digits, or leave it empty.',
    'badDate': 'Enter the date as YYYY-MM-DD.',
    'saved': 'Saved.',
    'deleted': 'Deleted.',
    'kind.bank': 'Bank account',
    'kind.card': 'Card',
    'kind.cash': 'Cash',
    'kind.loan': 'Loan',
    'kind.investment': 'Investment',
    'endingIn': 'ending {last4}',
    'importOnWeb':
        'Import statements, edit categories and manage holdings in Finance on the web.',
    'category.income': 'Income',
    'category.transfer': 'Transfers',
    'category.groceries': 'Groceries',
    'category.dining': 'Dining',
    'category.transport': 'Transport',
    'category.fuel': 'Fuel',
    'category.utilities': 'Utilities',
    'category.telecom': 'Phone and internet',
    'category.housing': 'Housing',
    'category.shopping': 'Shopping',
    'category.health': 'Health',
    'category.education': 'Education',
    'category.entertainment': 'Entertainment',
    'category.subscriptions': 'Subscriptions',
    'category.travel': 'Travel',
    'category.insurance': 'Insurance',
    'category.fees': 'Fees',
    'category.cash': 'Cash',
    'category.other': 'Other',
  };

  static const _ar = <String, String>{
    'title': 'المالية',
    'subtitle': 'مصاريفك وأرصدتك وفواتيرك وأهدافك. لا يراها أحد غيرك.',
    'readOnly': 'للقراءة فقط: لا يدفع ANUM ولا يحوّل ولا يتداول من هنا أبدًا.',
    'section.overview': 'نظرة عامة',
    'section.transactions': 'المعاملات',
    'section.accounts': 'الحسابات',
    'section.bills': 'الفواتير',
    'section.debts': 'الديون',
    'section.goals': 'الأهداف',
    'sections': 'أقسام المالية',
    'balance': 'الرصيد',
    'spent': 'المصروف',
    'income': 'الدخل',
    'bills30': 'فواتير خلال 30 يومًا',
    'debts': 'الديون',
    'none': 'لا شيء',
    'byCategory': 'المصاريف حسب الفئة',
    'noSpending': 'لا مصاريف هذا الشهر بعد.',
    'upcoming': 'فواتير مستحقة قريبًا',
    'noBillsDue': 'لا فواتير مستحقة خلال 30 يومًا.',
    'dueIn': 'مستحقة بعد {n} يوم',
    'dueToday': 'مستحقة اليوم',
    'accounts': 'الأرصدة',
    'loading': 'جارٍ تحميل بياناتك المالية...',
    'loadingList': 'جارٍ التحميل...',
    'denied':
        'المالية لمالكي مساحة العمل وأعضائها، ويرى كل شخص سجلاته فقط. اطلب من مالك مساحة العمل تغيير دورك من مشاهد إلى عضو.',
    'summaryFailed': 'تعذّر تحميل المالية: {message}',
    'listFailed': 'تعذّر تحميل هذه القائمة: {message}',
    'welcome': 'ابدأ بحساب',
    'welcomeText':
        'أضف الحساب البنكي أو البطاقة التي يأتي منها الكشف، ثم استورد الكشف كملف CSV من الويب.',
    'addAccount': 'أضف حسابًا',
    'noAccounts': 'لا حسابات بعد',
    'noAccountsText':
        'أضف حسابًا بنكيًا أو بطاقة أو محفظة نقد أو قرضًا أو حساب استثمار.',
    'search': 'ابحث في الأوصاف',
    'noTransactions': 'لا معاملات بعد',
    'noTransactionsText': 'استورد كشفًا من الويب، أو أضف معاملة هنا.',
    'noMatch': 'لا شيء يطابق. جرّب كلمات أخرى.',
    'addTransaction': 'أضف معاملة',
    'more': 'حمّل المزيد ({shown} من {total})',
    'addBill': 'أضف فاتورة',
    'noBills': 'لا فواتير بعد',
    'noBillsText':
        'أضف الإيجار والخدمات أو الرسوم المدرسية لترى ما يستحق قبل موعده.',
    'noDebts': 'لا ديون مسجّلة',
    'noDebtsText':
        'أضف القروض وأرصدة البطاقات من الويب لترى ما عليك في مكان واحد.',
    'noGoals': 'لا أهداف بعد',
    'noGoalsText': 'حدّد هدفًا، مثل صندوق طوارئ، من الويب وتابعه هنا.',
    'delete': 'حذف',
    'deleteTitle': 'أتحذف {name}؟',
    'deleteText': 'سيُحذف من سجلاتك المالية.',
    'cancel': 'إلغاء',
    'save': 'حفظ',
    'name': 'الاسم',
    'kind': 'النوع',
    'last4': 'آخر أربعة أرقام (اختياري)',
    'amount': 'المبلغ، بإشارة سالب للصادر',
    'amountPositive': 'المبلغ',
    'description': 'الوصف',
    'date': 'التاريخ (YYYY-MM-DD)',
    'account': 'الحساب',
    'badAmount': 'أدخل مبلغًا مثل 45.50 أو -45.50.',
    'badText': 'املأ كل الحقول.',
    'badLast4': 'أدخل آخر أربعة أرقام فقط، أو اتركه فارغًا.',
    'badDate': 'أدخل التاريخ بصيغة YYYY-MM-DD.',
    'saved': 'حُفظ.',
    'deleted': 'حُذف.',
    'kind.bank': 'حساب بنكي',
    'kind.card': 'بطاقة',
    'kind.cash': 'نقد',
    'kind.loan': 'قرض',
    'kind.investment': 'استثمار',
    'endingIn': 'ينتهي بـ {last4}',
    'importOnWeb':
        'استورد الكشوف وعدّل الفئات وأدر الاستثمارات من المالية على الويب.',
    'category.income': 'الدخل',
    'category.transfer': 'التحويلات',
    'category.groceries': 'البقالة',
    'category.dining': 'المطاعم',
    'category.transport': 'المواصلات',
    'category.fuel': 'الوقود',
    'category.utilities': 'الخدمات',
    'category.telecom': 'الهاتف والإنترنت',
    'category.housing': 'السكن',
    'category.shopping': 'التسوق',
    'category.health': 'الصحة',
    'category.education': 'التعليم',
    'category.entertainment': 'الترفيه',
    'category.subscriptions': 'الاشتراكات',
    'category.travel': 'السفر',
    'category.insurance': 'التأمين',
    'category.fees': 'الرسوم',
    'category.cash': 'النقد',
    'category.other': 'أخرى',
  };

  static bool get catalogsMatch =>
      _en.keys.toSet().containsAll(_ar.keys) &&
      _ar.keys.toSet().containsAll(_en.keys);
}
