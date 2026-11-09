import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the phone's personal money screens
/// (docs/finance.md): the Money planner, Lent and borrowed, Fixed costs and
/// the debt payoff card. The terms follow the web catalog.
class FinancePersonalStrings {
  const FinancePersonalStrings(this.arabic);
  final bool arabic;

  static FinancePersonalStrings of(BuildContext context) =>
      FinancePersonalStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = maybe(key) ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// The text for [key], or null when neither catalog has it.
  String? maybe(String key) => (arabic ? _ar : _en)[key] ?? _en[key];

  /// Example questions for the planner, in the screen's language. The
  /// Assistant's finance skill knows these words (docs/finance.md#assistant).
  List<String> get examples => arabic
      ? const [
          'أقدر أشتري سيارة بـ 60 ألف SAR؟',
          'أي دين أسدد أولا؟',
          'أي قرض أفضل: 80000 على 4 سنوات بنسبة 3.5% ثابت؟',
          'أرخص طريقة للتحويل للأهل؟',
        ]
      : const [
          'Can I afford a car of 60,000 SAR?',
          'Which debt should I pay first?',
          'Which loan is better: 80000 over 4 years at 3.5% flat?',
          'What is the cheapest way to send money home?',
        ];

  static const _en = <String, String>{
    'pm.denied':
        'Your role cannot see personal money here. Workspace owners and members with Finance access can: ask an owner.',
    'pm.offline':
        'You are offline, or ANUM could not be reached. Check the connection and try again.',
    'pm.linkFailed': 'The link could not be opened.',
    'planner.title': 'Money planner',
    'planner.intro':
        'Ask a money question in your own words. ANUM answers from your own records and, when web search is on, from live sources it names with the date read.',
    'planner.unavailable':
        'The planner needs the Assistant, which this app cannot reach. Use the Plan view on the web.',
    'planner.ask': 'Your question',
    'planner.hint': 'Can I afford a car of 60,000 SAR?',
    'planner.send': 'Ask',
    'planner.stop': 'Stop',
    'planner.adviceOnly':
        'Advice only: ANUM never pays, buys or transfers money for you.',
    'planner.empty': 'Ask your first question',
    'planner.emptyText':
        'Type a question above or tap an example: affording a purchase, which debt to pay first, which loan is cheaper, or the cheapest way to send money home.',
    'planner.working': 'Working it out from your records',
    'planner.denied':
        'Your role cannot use the assistant in this workspace. Ask a workspace owner to give you the member role.',
    'planner.failed': 'The planner did not answer: {message} Try again.',
    'planner.answer': 'Answer',
    'planner.advicePill': 'Advice only',
    'planner.ownData': 'From your own records only: no live source was used.',
    'planner.sources': 'Live sources',
    'planner.sourcesNote':
        'Figures read from these pages on the date given in the answer. Check them with the seller or lender.',
    'planner.saved':
        'This question and its answer are kept in your Assistant conversations.',
    'people.loading': 'Loading what is lent and borrowed',
    'people.failed': 'Lent and borrowed could not be loaded: {message}',
    'people.moreFailed': 'More entries could not be loaded: {message}',
    'people.add': 'Add an entry',
    'people.filter': 'Show',
    'people.all': 'All',
    'people.lent': 'They owe me',
    'people.borrowed': 'I owe',
    'people.empty': 'Nothing lent or borrowed yet',
    'people.emptyText':
        'Record money you lent to someone or borrowed from someone, with a due date, and ANUM reminds you here before it is due.',
    'people.noneHere': 'No entries in this view.',
    'people.more': 'Show more',
    'people.loadingMore': 'Loading more',
    'people.reminders':
        'A reminder arrives in ANUM before a due date and once it is overdue. Linked repayments count as transfers, not spending or income.',
    'people.addTitle': 'Add an entry',
    'people.addIntro':
        'Money lent or borrowed with a person, without interest. Link the transactions later as repayments come in.',
    'people.save': 'Save',
    'people.direction': 'Who owes whom',
    'people.dirLent': 'I lent: they owe me',
    'people.dirBorrowed': 'I borrowed: I owe them',
    'people.person': 'Person',
    'people.amount': 'Amount',
    'people.currency': 'Currency',
    'people.currencyHelp':
        'Three letters, for example AED. Empty: your main currency.',
    'people.date': 'Date the money changed hands',
    'people.dueDate': 'Due date (optional)',
    'people.notes': 'Notes (optional)',
    'people.badAmount': 'Enter an amount above zero.',
    'people.badCurrency':
        'Enter the currency as three letters, for example AED.',
    'people.saved': 'Saved.',
    'people.totals': 'What I owe and what I am owed',
    'people.overdueCount': 'Overdue: {n}',
    'people.allSettled': 'Nothing is open: everything is repaid or closed.',
    'people.theyOweMe': 'They owe me',
    'people.iOwe': 'I owe',
    'people.noDue': 'No due date',
    'people.due': 'Due {date}',
    'people.rowLent': 'I lent',
    'people.rowBorrowed': 'I borrowed',
    'people.left': 'Left {left} of {amount}',
    'people.status.open': 'Open',
    'people.status.dueSoon': 'Due soon',
    'people.status.overdue': 'Overdue',
    'people.status.repaid': 'Repaid',
    'people.status.closed': 'Closed',
    'people.recordRepayment': 'Record a repayment',
    'repay.title': 'Repayment with {person}',
    'repay.helpIn':
        'Pick the money in that repaid you. It is linked to this entry and counts as a transfer, not income.',
    'repay.helpOut':
        'Pick the payment you made to repay. It is linked to this entry and counts as a transfer, not spending.',
    'repay.search': 'Search transactions',
    'repay.empty':
        'No matching transaction. Add it under Transactions first, then link it here.',
    'repay.remaining': '{amount} can still be linked',
    'repay.already': 'Already linked',
    'repay.done': 'Repayment recorded.',
    'fixed.loading': 'Loading your fixed monthly costs',
    'fixed.failed': 'Fixed costs could not be loaded: {message}',
    'fixed.empty': 'No fixed monthly costs yet',
    'fixed.emptyText':
        'Add your rent and regular bills under Bills. Debt minimums, installments and regular transfers home count here too.',
    'fixed.openBills': 'Open Bills',
    'fixed.month': 'Month {month}',
    'fixed.title': 'Fixed monthly costs',
    'fixed.total': 'Total in {currency}',
    'fixed.never':
        'One total per currency: currencies are never added together.',
    'fixed.leftOut': 'Left out',
    'fixed.leftOutText': 'Not monthly, or already counted once as a bill.',
    'fixed.howCounted':
        'A weekly bill counts as x 52 / 12, quarterly / 3, yearly / 12.',
    'fixed.kind.rent': 'Rent',
    'fixed.kind.subscription': 'Subscription',
    'fixed.kind.bill': 'Bill',
    'fixed.kind.installment': 'Installment',
    'fixed.kind.loan_minimum': 'Loan minimum',
    'fixed.kind.card_minimum': 'Card minimum',
    'fixed.kind.remittance': 'Transfer home',
    'fixed.note.weekly': 'weekly',
    'fixed.note.quarterly': 'quarterly',
    'fixed.note.yearly': 'yearly',
    'fixed.note.once': 'once, not monthly',
    'fixed.note.in_bill': 'counted as the bill',
    'fixed.note.with_fee': 'fee included',
    'payoff.loading': 'Comparing payoff orders',
    'payoff.failed': 'The payoff comparison could not be loaded: {message}',
    'payoff.title': 'Which debt to pay first',
    'payoff.empty':
        'No debts to compare. Debts you add on the web with their rate and minimum payment are compared here.',
    'payoff.paying': 'Paying {amount} a month',
    'payoff.belowMinimums':
        'This is less than the minimum payments together: pay at least the minimums.',
    'payoff.avalanche': 'Avalanche',
    'payoff.snowball': 'Snowball',
    'payoff.avalancheText': 'Highest rate first.',
    'payoff.snowballText': 'Smallest balance first.',
    'payoff.cheaper': 'Cheaper',
    'payoff.debtFree': 'Debt free in',
    'payoff.months': '{n} months',
    'payoff.never': 'Never at this payment: it does not cover the interest',
    'payoff.interest': 'Total interest',
    'payoff.order': 'Order: {list}',
    'payoff.same': 'Both orders cost the same.',
    'payoff.saves': '{strategy} saves {amount} in interest.',
    'payoff.firstWin': 'Snowball clears its first debt in {n} months.',
    'payoff.missingRates':
        'No rate entered for: {list}. Counted at 0 percent, never guessed.',
    'payoff.note':
        'Advice only: ANUM never moves money. Figures are estimates from your own balances, rates and minimums.',
    'web.onWeb': 'Payslips and Send home are on the web.',
    'web.open': 'Payslips and Send home are on the web: open',
  };

  static const _ar = <String, String>{
    'pm.denied':
        'دورك لا يتيح رؤية أموالك الشخصية هنا. يستطيع ذلك مالكو مساحة العمل والأعضاء الذين لديهم صلاحية المالية: اطلب ذلك من مالك.',
    'pm.offline':
        'أنت غير متصل، أو تعذر الوصول إلى ANUM. تحقق من الاتصال وحاول مرة أخرى.',
    'pm.linkFailed': 'تعذر فتح الرابط.',
    'planner.title': 'مخطط المال',
    'planner.intro':
        'اسأل سؤالًا عن المال بكلماتك. يجيب ANUM من سجلاتك أنت، ومن مصادر حية يذكرها مع تاريخ القراءة عندما يكون البحث على الويب مفعّلًا.',
    'planner.unavailable':
        'يحتاج المخطط إلى المساعد، ولا يستطيع هذا التطبيق الوصول إليه. استخدم عرض الخطة على الويب.',
    'planner.ask': 'سؤالك',
    'planner.hint': 'أقدر أشتري سيارة بـ 60 ألف SAR؟',
    'planner.send': 'اسأل',
    'planner.stop': 'أوقف',
    'planner.adviceOnly':
        'نصيحة فقط: لا يدفع ANUM ولا يشتري ولا يحوّل المال نيابة عنك أبدًا.',
    'planner.empty': 'اسأل سؤالك الأول',
    'planner.emptyText':
        'اكتب سؤالًا في الأعلى أو اختر مثالًا: هل أقدر على شراء شيء، أي دين أسدد أولًا، أي قرض أرخص، أو أرخص طريقة للتحويل للأهل.',
    'planner.working': 'نحسبها من سجلاتك',
    'planner.denied':
        'دورك لا يتيح استخدام المساعد في مساحة العمل هذه. اطلب من مالك مساحة العمل أن يمنحك دور العضو.',
    'planner.failed': 'لم يجب المخطط: {message} حاول مرة أخرى.',
    'planner.answer': 'الإجابة',
    'planner.advicePill': 'نصيحة فقط',
    'planner.ownData': 'من سجلاتك أنت فقط: لم يُستخدم أي مصدر حي.',
    'planner.sources': 'مصادر حية',
    'planner.sourcesNote':
        'أرقام قُرئت من هذه الصفحات في التاريخ المذكور في الإجابة. تحقق منها مع البائع أو المقرض.',
    'planner.saved': 'هذا السؤال وإجابته محفوظان في محادثات المساعد.',
    'people.loading': 'نحمّل ما أقرضته وما اقترضته',
    'people.failed': 'تعذر تحميل الإقراض والاقتراض: {message}',
    'people.moreFailed': 'تعذر تحميل المزيد: {message}',
    'people.add': 'أضف قيدًا',
    'people.filter': 'اعرض',
    'people.all': 'الكل',
    'people.lent': 'لي عندهم',
    'people.borrowed': 'عليّ لهم',
    'people.empty': 'لا شيء مُقرض أو مُقترض بعد',
    'people.emptyText':
        'سجّل مالًا أقرضته لشخص أو اقترضته منه، مع تاريخ استحقاق، وسيذكّرك ANUM هنا قبل موعده.',
    'people.noneHere': 'لا قيود في هذا العرض.',
    'people.more': 'اعرض المزيد',
    'people.loadingMore': 'نحمّل المزيد',
    'people.reminders':
        'يصلك تذكير في ANUM قبل تاريخ الاستحقاق ومرة أخرى عند التأخر. السداد المربوط يُحسب تحويلًا، لا صرفًا ولا دخلًا.',
    'people.addTitle': 'أضف قيدًا',
    'people.addIntro':
        'مال أقرضته أو اقترضته مع شخص، بلا فائدة. اربط المعاملات لاحقًا عند وصول السداد.',
    'people.save': 'حفظ',
    'people.direction': 'من يدين لمن',
    'people.dirLent': 'أقرضت: لي عندهم',
    'people.dirBorrowed': 'اقترضت: عليّ لهم',
    'people.person': 'الشخص',
    'people.amount': 'المبلغ',
    'people.currency': 'العملة',
    'people.currencyHelp': 'ثلاثة أحرف، مثل AED. فارغ: عملتك الأساسية.',
    'people.date': 'تاريخ انتقال المال',
    'people.dueDate': 'تاريخ الاستحقاق (اختياري)',
    'people.notes': 'ملاحظات (اختياري)',
    'people.badAmount': 'أدخل مبلغًا أكبر من صفر.',
    'people.badCurrency': 'أدخل العملة بثلاثة أحرف، مثل AED.',
    'people.saved': 'تم الحفظ.',
    'people.totals': 'ما عليّ وما لي',
    'people.overdueCount': 'متأخر: {n}',
    'people.allSettled': 'لا شيء مفتوح: كل شيء مسدد أو مغلق.',
    'people.theyOweMe': 'لي عندهم',
    'people.iOwe': 'عليّ لهم',
    'people.noDue': 'بلا تاريخ استحقاق',
    'people.due': 'يستحق في {date}',
    'people.rowLent': 'أقرضت',
    'people.rowBorrowed': 'اقترضت',
    'people.left': 'المتبقي {left} من {amount}',
    'people.status.open': 'مفتوح',
    'people.status.dueSoon': 'يستحق قريبًا',
    'people.status.overdue': 'متأخر',
    'people.status.repaid': 'مسدد',
    'people.status.closed': 'مغلق',
    'people.recordRepayment': 'سجّل سدادًا',
    'repay.title': 'سداد مع {person}',
    'repay.helpIn':
        'اختر المال الوارد الذي سدّد لك. يُربط بهذا القيد ويُحسب تحويلًا، لا دخلًا.',
    'repay.helpOut':
        'اختر الدفعة التي سددت بها. تُربط بهذا القيد وتُحسب تحويلًا، لا صرفًا.',
    'repay.search': 'ابحث في المعاملات',
    'repay.empty':
        'لا توجد معاملة مطابقة. أضفها في المعاملات أولًا، ثم اربطها هنا.',
    'repay.remaining': 'يمكن ربط {amount}',
    'repay.already': 'مربوطة بالفعل',
    'repay.done': 'سُجّل السداد.',
    'fixed.loading': 'نحمّل تكاليفك الشهرية الثابتة',
    'fixed.failed': 'تعذر تحميل التكاليف الثابتة: {message}',
    'fixed.empty': 'لا تكاليف شهرية ثابتة بعد',
    'fixed.emptyText':
        'أضف الإيجار والفواتير المنتظمة في الفواتير. الحد الأدنى للديون والأقساط والتحويلات المنتظمة للأهل تُحسب هنا أيضًا.',
    'fixed.openBills': 'افتح الفواتير',
    'fixed.month': 'شهر {month}',
    'fixed.title': 'التكاليف الشهرية الثابتة',
    'fixed.total': 'الإجمالي بعملة {currency}',
    'fixed.never': 'إجمالي واحد لكل عملة: لا تُجمع العملات معًا أبدًا.',
    'fixed.leftOut': 'غير محسوب',
    'fixed.leftOutText': 'ليس شهريًا، أو محسوب مرة واحدة كفاتورة.',
    'fixed.howCounted':
        'الفاتورة الأسبوعية تُحسب x 52 / 12، والفصلية / 3، والسنوية / 12.',
    'fixed.kind.rent': 'إيجار',
    'fixed.kind.subscription': 'اشتراك',
    'fixed.kind.bill': 'فاتورة',
    'fixed.kind.installment': 'قسط',
    'fixed.kind.loan_minimum': 'الحد الأدنى للقرض',
    'fixed.kind.card_minimum': 'الحد الأدنى للبطاقة',
    'fixed.kind.remittance': 'تحويل للأهل',
    'fixed.note.weekly': 'أسبوعي',
    'fixed.note.quarterly': 'فصلي',
    'fixed.note.yearly': 'سنوي',
    'fixed.note.once': 'مرة واحدة، ليس شهريًا',
    'fixed.note.in_bill': 'محسوب ضمن الفاتورة',
    'fixed.note.with_fee': 'شاملًا الرسوم',
    'payoff.loading': 'نقارن ترتيب السداد',
    'payoff.failed': 'تعذر تحميل مقارنة السداد: {message}',
    'payoff.title': 'أي دين أسدد أولًا',
    'payoff.empty':
        'لا ديون للمقارنة. الديون التي تضيفها على الويب مع نسبتها وحدها الأدنى تُقارن هنا.',
    'payoff.paying': 'تدفع {amount} شهريًا',
    'payoff.belowMinimums':
        'هذا أقل من مجموع الحدود الدنيا: ادفع الحدود الدنيا على الأقل.',
    'payoff.avalanche': 'الانهيار',
    'payoff.snowball': 'كرة الثلج',
    'payoff.avalancheText': 'الأعلى نسبة أولًا.',
    'payoff.snowballText': 'الأصغر رصيدًا أولًا.',
    'payoff.cheaper': 'الأرخص',
    'payoff.debtFree': 'بلا ديون بعد',
    'payoff.months': 'عدد الأشهر: {n}',
    'payoff.never': 'أبدًا بهذه الدفعة: لا تغطي الفائدة',
    'payoff.interest': 'إجمالي الفائدة',
    'payoff.order': 'الترتيب: {list}',
    'payoff.same': 'الترتيبان بالتكلفة نفسها.',
    'payoff.saves': '{strategy} يوفر {amount} من الفائدة.',
    'payoff.firstWin': 'كرة الثلج تنهي أول دين بعد عدد من الأشهر: {n}.',
    'payoff.missingRates':
        'لا نسبة مُدخلة لـ: {list}. تُحسب بصفر بالمئة، ولا نخمّنها أبدًا.',
    'payoff.note':
        'نصيحة فقط: لا يحرّك ANUM المال أبدًا. الأرقام تقديرات من أرصدتك ونسبك وحدودك الدنيا.',
    'web.onWeb': 'قسائم الراتب والتحويل للأهل على الويب.',
    'web.open': 'قسائم الراتب والتحويل للأهل على الويب: افتح',
  };

  static bool get catalogsMatch =>
      _en.keys.toSet().containsAll(_ar.keys) &&
      _ar.keys.toSet().containsAll(_en.keys);
}
