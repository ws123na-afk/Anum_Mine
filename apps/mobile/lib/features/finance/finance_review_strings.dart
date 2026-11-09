import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of Finance's review side on the phone
/// (docs/finance.md): transaction types and filing, the suggestion queue,
/// the last statement import and reconciliation status. The terms follow
/// the web catalogs (`financeRegister`, `financeSuggestions`,
/// `financeRecon`).
class FinanceReviewStrings {
  const FinanceReviewStrings(this.arabic);
  final bool arabic;

  static FinanceReviewStrings of(BuildContext context) =>
      FinanceReviewStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = maybe(key) ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// The text for [key], or null when neither catalog has it.
  String? maybe(String key) => (arabic ? _ar : _en)[key] ?? _en[key];

  static const _en = <String, String>{
    // -- transactions: types and filing ------------------------------------
    'tx.uncategorized':
        '{n} uncategorized, not counted in income or spending. File each one so it counts.',
    'tx.showThem': 'Show them',
    'tx.showAll': 'Show all rows',
    'tx.filterOn': 'Showing only the rows not counted yet.',
    'tx.noneLeft': 'Nothing left to file',
    'tx.noneLeftText':
        'Every row has a type now. A new row no rule recognises waits here.',
    'tx.file': 'File it',
    'tx.fileTitle': 'What is this?',
    'tx.fileIntro':
        'No rule recognised this payment, so it is in neither income nor spending. Choose what it is and save.',
    'tx.type': 'Type',
    'tx.category': 'Category',
    'tx.categoryAuto': 'The usual one for this type',
    'tx.save': 'Save',
    'tx.cancel': 'Cancel',
    'tx.filed': 'Saved as {type}.',
    'tx.moreOnWeb':
        'Rules, subcategories and Apply to similar are on the web: Finance, Transactions.',
    'type.income': 'Income',
    'type.expense': 'Spending',
    'type.internal_transfer': 'Between your accounts',
    'type.card_payment': 'Card payment',
    'type.loan_received': 'Loan received',
    'type.loan_principal': 'Loan repayment',
    'type.interest': 'Interest charged',
    'type.refund': 'Refund',
    'type.cashback': 'Cashback',
    'type.bank_fee': 'Bank fee',
    'type.investment_contribution': 'Money into investments',
    'type.investment_withdrawal': 'Money out of investments',
    'type.owner_contribution': 'Owner contribution',
    'type.owner_drawing': 'Owner drawing',
    'type.reimbursement': 'Reimbursement',
    'type.receivable_collection': 'Money collected',
    'type.payable_settlement': 'Bill settled',
    'type.fx_exchange': 'Currency exchange',
    'type.tax': 'Tax',
    'type.reversal': 'Reversal',
    'type.uncategorized': 'Uncategorized',

    // -- web links -----------------------------------------------------------
    'web.finish': 'Finish on the web',
    'web.linkFailed': 'The link could not be opened.',

    // -- the last statement import -------------------------------------------
    'import.title': 'Last statement import',
    'import.loading': 'Loading the last statement import',
    'import.failed': 'The last import could not be loaded: {message}',
    'import.none': 'No statement imported yet',
    'import.noneText':
        'Statements are imported on the web (Finance, Import). Each import then shows here with what happened to its rows.',
    'import.imported': 'Imported',
    'import.held': 'Held for review',
    'import.attached': 'Attached to a recorded payment',
    'import.notInLedger': 'Not in the ledger',
    'import.notInLedgerText':
        'Some statement rows are not in your books yet. Finish them on the web: Connections, Import history, Rows.',
    'import.allIn': 'Every statement row is in your books.',
    'import.status.tied': 'Balances match the statement.',
    'import.status.differs': 'Balances differ from the statement.',
    'import.status.unknown': 'The statement printed no balance to check.',

    // -- reconciliation --------------------------------------------------------
    'recon.title': 'Reconciliation',
    'recon.intro':
        'Whether each bank and card account agrees with its statements. There is no live bank feed: statements, SMS and Apple Pay only.',
    'recon.loading': 'Loading reconciliation status',
    'recon.failed': 'Reconciliation status could not be loaded: {message}',
    'recon.empty': 'No bank or card account yet',
    'recon.emptyText':
        'Add a bank or card account and import its statement on the web to see it here.',
    'recon.through': 'Reconciled through {date}',
    'recon.never': 'Not reconciled yet',
    'recon.open': 'Open: {start} to {end}',
    'recon.unexplained': 'Unexplained {amount}',
    'recon.balanced': 'Balances, ready to complete',
    'recon.items': 'Items that need you: {n}',
    'recon.missing': 'Months with activity but no statement: {months}',
    'recon.next.import': 'Next: import the statement for {month}.',
    'recon.next.importAny': 'Next: import a statement for this account.',
    'recon.next.reconcile': 'Next: reconcile the statement to {month}.',
    'recon.next.finish': 'Next: finish the open reconciliation for {month}.',
    'recon.next.review':
        'Next: the reconciliation for {month} changed after completion. Review it.',
    'recon.next.upToDate': 'Up to date.',
    'recon.next.none': 'Nothing to reconcile for this kind of account.',
    'recon.onWeb':
        'Matching rows, corrections and completing a reconciliation are done on the web.',
    'reconKind.not_in_ledger': 'Statement row not in your books',
    'reconKind.unmatched_payment': 'Payment no statement confirmed',
    'reconKind.unmatched_deposit': 'Deposit no statement confirmed',
    'reconKind.pending': 'SMS or Apple Pay record not on a statement yet',
    'reconKind.possible_duplicate': 'Possible duplicate',
    'reconKind.wrong_direction': 'Direction looks wrong',
    'reconKind.reversal_pair': 'Looks like a payment and its reversal',
    'reconKind.unknown_currency': 'Currency not known',
    'reconKind.unrecognized_fee': 'Fee not recorded as a bank fee',
    'reconKind.missing_period': 'No statement for this month',
    'reconKind.opening_differs': 'Opening balance differs',
    'reconKind.amount_due_differs': 'Amount due differs from the card balance',

    // -- suggestions -----------------------------------------------------------
    'sug.intro':
        'Everything that waits for you: what ANUM found, why, and what it proposes. Nothing changes until you accept.',
    'sug.loading': 'Loading suggestions',
    'sug.failed': 'Suggestions could not be loaded: {message}',
    'sug.moreFailed': 'More suggestions could not be loaded: {message}',
    'sug.more': 'Show more ({shown} of {total})',
    'sug.confidence': '{percent}% sure',
    'sug.why': 'Why',
    'sug.effect': 'Money',
    'sug.accept': 'Accept',
    'sug.seen': 'Mark as seen',
    'sug.dismiss': 'Dismiss',
    'sug.openOnWeb': 'This one is resolved on its page on the web.',
    'sug.accepted': 'Done. Rows changed: {n}.',
    'sug.acknowledged': 'Marked as seen.',
    'sug.dismissed': 'Dismissed. It will not come back.',
    'sug.empty': 'Nothing waits for you',
    'sug.emptyText':
        'When ANUM finds something to check, it shows here. Look at your transactions for rows still to file.',
    'sug.emptyAction': 'Open Transactions',
    'sug.unavailable':
        'Some checks could not run this time: {names}. The rest are shown.',
    'sug.rulesOnWeb':
        'Rules, merchant spellings, subcategories and automation are edited on the web: Finance, Suggestions.',
    'sug.kind.classification': 'Category from your history',
    'sug.kind.conflict': 'Rule and history differ',
    'sug.kind.uncategorized': 'Uncategorized',
    'sug.kind.statement_hold': 'Statement rows held',
    'sug.kind.sms_review': 'Bank messages',
    'sug.kind.duplicate': 'Possible duplicates',
    'sug.kind.refund_match': 'Refunds',
    'sug.kind.payment_match': 'Unmatched bill payments',
    'sug.kind.bill_due': 'Bills due',
    'sug.kind.subscription': 'Recurring payments',
    'sug.kind.unusual_spend': 'Unusual spending',
    'sug.kind.missing_statement': 'Missing statements',
    'sug.kind.cash_shortage': 'Cash shortage',
    'sug.kind.reconciliation': 'Reconciliation',
    'sug.kind.external': 'Other checks',
    'sug.head.classification': 'File as {category}?',
    'sug.head.conflict': 'Your earlier choice was {category}',
    'sug.head.uncategorized': 'Not yet filed',
    'sug.head.statement_hold': '{count} statement rows held in {file}',
    'sug.head.sms_review': 'A bank message waits for review',
    'sug.head.duplicate': 'Possible duplicate payment',
    'sug.head.refund_match': 'This refund may match a purchase',
    'sug.head.payment_match': '{name}: no payment matched for {due_on}',
    'sug.head.bill_due': '{name} is due in {days} day(s)',
    'sug.head.subscription': '{name} looks like a regular payment',
    'sug.head.unusual_spend': 'Unusual payment',
    'sug.head.missing_statement': 'No statement for {account} in {month}',
    'sug.head.cash_shortage': 'Possible cash shortage in {currency}',
    'sug.head.reconciliation': '{account}: {exception}',
    'sug.head.external': 'Needs your review',
    'sug.why.classification':
        'You chose {category} for {merchant} {agree} of {seen} times.',
    'sug.why.conflict':
        'A rule filed it as {current}, but you chose {category} for {merchant} {agree} of {seen} times.',
    'sug.why.uncategorized':
        'No rule recognised it. It counts as neither income nor spending until you file it.',
    'sug.why.statement_hold':
        'These rows could be more than one recorded payment, so they were not added on their own.',
    'sug.why.sms_review':
        'It was not recorded on its own. Check it under iPhone SMS, Review on the web.',
    'sug.why.duplicate':
        'Same account and amount within {days} days, and both name the same merchant.',
    'sug.why.refund_match':
        'The amount fits an earlier purchase, but the descriptions do not fully agree.',
    'sug.why.payment_match':
        'If you paid it, match the payment on Bills so it is not counted as still due.',
    'sug.why.bill_due': 'No payment is matched to this due date yet.',
    'sug.why.subscription':
        'Seen {count} times. Save it as a bill to track it.',
    'sug.why.unusual_spend':
        'You usually pay {usual} here ({count} earlier payments).',
    'sug.why.missing_statement':
        'Import that month\'s statement so nothing is missing.',
    'sug.why.cash_shortage':
        'Bills, minimum payments and goals this month are more than you have.',
    'sug.why.reconciliation.blocking':
        'The reconciliation for {start} to {end} cannot be completed until this is resolved.',
    'sug.why.reconciliation.warning':
        'The reconciliation for {start} to {end} can be completed, but this is not explained yet.',
    'sug.why.reconciliation.missing':
        'Activity in {month} is on no statement. Import that month\'s statement, then reconcile it.',
  };

  static const _ar = <String, String>{
    'tx.uncategorized':
        '{n} غير مصنفة، غير محسوبة في الدخل ولا الإنفاق. صنّف كل واحدة لتُحسب.',
    'tx.showThem': 'اعرضها',
    'tx.showAll': 'اعرض كل الصفوف',
    'tx.filterOn': 'تُعرض الصفوف غير المحسوبة بعد فقط.',
    'tx.noneLeft': 'لا شيء بانتظار التصنيف',
    'tx.noneLeftText':
        'كل الصفوف لها نوع الآن. أي صف جديد لا تتعرف عليه قاعدة ينتظر هنا.',
    'tx.file': 'صنّفها',
    'tx.fileTitle': 'ما هذه المعاملة؟',
    'tx.fileIntro':
        'لم تتعرف أي قاعدة على هذه الدفعة، فهي ليست ضمن الدخل ولا الإنفاق. اختر نوعها ثم احفظ.',
    'tx.type': 'النوع',
    'tx.category': 'الفئة',
    'tx.categoryAuto': 'الفئة المعتادة لهذا النوع',
    'tx.save': 'حفظ',
    'tx.cancel': 'إلغاء',
    'tx.filed': 'حُفظت على أنها {type}.',
    'tx.moreOnWeb':
        'القواعد والفئات الفرعية والتطبيق على المشابه على الويب: المالية، المعاملات.',
    'type.income': 'دخل',
    'type.expense': 'إنفاق',
    'type.internal_transfer': 'بين حساباتك',
    'type.card_payment': 'سداد بطاقة',
    'type.loan_received': 'قرض مستلم',
    'type.loan_principal': 'سداد قرض',
    'type.interest': 'فائدة محتسبة',
    'type.refund': 'استرداد',
    'type.cashback': 'استرداد نقدي',
    'type.bank_fee': 'رسوم بنكية',
    'type.investment_contribution': 'إيداع في الاستثمار',
    'type.investment_withdrawal': 'سحب من الاستثمار',
    'type.owner_contribution': 'مساهمة المالك',
    'type.owner_drawing': 'مسحوبات المالك',
    'type.reimbursement': 'تعويض مصروفات',
    'type.receivable_collection': 'تحصيل مستحقات',
    'type.payable_settlement': 'تسوية فاتورة',
    'type.fx_exchange': 'صرف عملة',
    'type.tax': 'ضريبة',
    'type.reversal': 'عكس قيد',
    'type.uncategorized': 'غير مصنفة',
    'web.finish': 'أكمل على الويب',
    'web.linkFailed': 'تعذر فتح الرابط.',
    'import.title': 'آخر استيراد لكشف',
    'import.loading': 'نحمّل آخر استيراد لكشف',
    'import.failed': 'تعذر تحميل آخر استيراد: {message}',
    'import.none': 'لم يُستورد أي كشف بعد',
    'import.noneText':
        'تُستورد الكشوف على الويب (المالية، الاستيراد). يظهر كل استيراد هنا بعدها مع ما حدث لصفوفه.',
    'import.imported': 'مستورد',
    'import.held': 'محجوز للمراجعة',
    'import.attached': 'مرفق بدفعة مسجلة',
    'import.notInLedger': 'خارج الدفتر',
    'import.notInLedgerText':
        'بعض صفوف الكشف ليست في دفاترك بعد. أكملها على الويب: الاتصالات، سجل الاستيراد، الصفوف.',
    'import.allIn': 'كل صفوف الكشف في دفاترك.',
    'import.status.tied': 'الأرصدة تطابق الكشف.',
    'import.status.differs': 'الأرصدة تختلف عن الكشف.',
    'import.status.unknown': 'لم يطبع الكشف رصيدًا للمقارنة.',
    'recon.title': 'المطابقة',
    'recon.intro':
        'هل يتفق كل حساب بنكي وبطاقة مع كشوفه. لا يوجد ربط مباشر بالبنك: الكشوف ورسائل SMS وApple Pay فقط.',
    'recon.loading': 'نحمّل حالة المطابقة',
    'recon.failed': 'تعذر تحميل حالة المطابقة: {message}',
    'recon.empty': 'لا يوجد حساب بنكي أو بطاقة بعد',
    'recon.emptyText':
        'أضف حسابًا بنكيًا أو بطاقة واستورد كشفه على الويب ليظهر هنا.',
    'recon.through': 'مطابَق حتى {date}',
    'recon.never': 'لم تتم مطابقته بعد',
    'recon.open': 'مفتوحة: من {start} إلى {end}',
    'recon.unexplained': 'فرق غير مفسَّر {amount}',
    'recon.balanced': 'متوازنة وجاهزة للإكمال',
    'recon.items': 'بنود تحتاجك: {n}',
    'recon.missing': 'أشهر فيها حركة بلا كشف: {months}',
    'recon.next.import': 'التالي: استورد كشف {month}.',
    'recon.next.importAny': 'التالي: استورد كشفًا لهذا الحساب.',
    'recon.next.reconcile': 'التالي: طابق الكشف حتى {month}.',
    'recon.next.finish': 'التالي: أكمل المطابقة المفتوحة لشهر {month}.',
    'recon.next.review': 'التالي: تغيّرت مطابقة {month} بعد إكمالها. راجعها.',
    'recon.next.upToDate': 'محدّث.',
    'recon.next.none': 'لا مطابقة لهذا النوع من الحسابات.',
    'recon.onWeb': 'مطابقة الصفوف والتصحيحات وإكمال المطابقة تتم على الويب.',
    'reconKind.not_in_ledger': 'سطر في الكشف ليس في دفاترك',
    'reconKind.unmatched_payment': 'دفعة لم يؤكدها أي كشف',
    'reconKind.unmatched_deposit': 'إيداع لم يؤكده أي كشف',
    'reconKind.pending': 'رسالة SMS أو عملية Apple Pay ليست في كشف بعد',
    'reconKind.possible_duplicate': 'تكرار محتمل',
    'reconKind.wrong_direction': 'يبدو الاتجاه خاطئًا',
    'reconKind.reversal_pair': 'تبدو دفعة وعكسها',
    'reconKind.unknown_currency': 'العملة غير معروفة',
    'reconKind.unrecognized_fee': 'رسوم غير مسجلة كرسوم بنكية',
    'reconKind.missing_period': 'لا كشف لهذا الشهر',
    'reconKind.opening_differs': 'الرصيد الافتتاحي مختلف',
    'reconKind.amount_due_differs': 'المبلغ المستحق يختلف عن رصيد البطاقة',
    'sug.intro':
        'كل ما ينتظرك: ما وجده ANUM ولماذا وما يقترحه. لا يتغير شيء حتى توافق.',
    'sug.loading': 'نحمّل الاقتراحات',
    'sug.failed': 'تعذر تحميل الاقتراحات: {message}',
    'sug.moreFailed': 'تعذر تحميل مزيد من الاقتراحات: {message}',
    'sug.more': 'اعرض المزيد ({shown} من {total})',
    'sug.confidence': 'متأكد بنسبة {percent}%',
    'sug.why': 'لماذا',
    'sug.effect': 'المال',
    'sug.accept': 'قبول',
    'sug.seen': 'تمت المشاهدة',
    'sug.dismiss': 'تجاهل',
    'sug.openOnWeb': 'يُحسم هذا الاقتراح في صفحته على الويب.',
    'sug.accepted': 'تم. الصفوف التي تغيرت: {n}.',
    'sug.acknowledged': 'تم وضع علامة المشاهدة.',
    'sug.dismissed': 'تم التجاهل. لن يظهر مرة أخرى.',
    'sug.empty': 'لا شيء ينتظرك',
    'sug.emptyText':
        'عندما يجد ANUM ما يحتاج إلى فحص يظهر هنا. راجع معاملاتك بحثًا عن صفوف لم تُصنف بعد.',
    'sug.emptyAction': 'افتح المعاملات',
    'sug.unavailable':
        'تعذر تشغيل بعض الفحوص هذه المرة: {names}. البقية معروضة.',
    'sug.rulesOnWeb':
        'تُعدَّل القواعد وكتابات التجار والفئات الفرعية والأتمتة على الويب: المالية، الاقتراحات.',
    'sug.kind.classification': 'فئة من اختياراتك السابقة',
    'sug.kind.conflict': 'القاعدة تخالف اختياراتك',
    'sug.kind.uncategorized': 'غير مصنفة',
    'sug.kind.statement_hold': 'صفوف كشف معلقة',
    'sug.kind.sms_review': 'رسائل البنك',
    'sug.kind.duplicate': 'تكرار محتمل',
    'sug.kind.refund_match': 'المبالغ المستردة',
    'sug.kind.payment_match': 'فواتير بلا دفعة مطابقة',
    'sug.kind.bill_due': 'فواتير مستحقة',
    'sug.kind.subscription': 'دفعات متكررة',
    'sug.kind.unusual_spend': 'صرف غير معتاد',
    'sug.kind.missing_statement': 'كشوف ناقصة',
    'sug.kind.cash_shortage': 'نقص في النقد',
    'sug.kind.reconciliation': 'المطابقة',
    'sug.kind.external': 'فحوص أخرى',
    'sug.head.classification': 'هل تصنفها {category}؟',
    'sug.head.conflict': 'اختيارك السابق كان {category}',
    'sug.head.uncategorized': 'لم تُصنف بعد',
    'sug.head.statement_hold': '{count} من صفوف الكشف معلقة في {file}',
    'sug.head.sms_review': 'رسالة بنك تنتظر المراجعة',
    'sug.head.duplicate': 'دفعة مكررة محتملة',
    'sug.head.refund_match': 'قد يطابق هذا الاسترداد عملية شراء',
    'sug.head.payment_match': '{name}: لا دفعة مطابقة لتاريخ {due_on}',
    'sug.head.bill_due': '{name} مستحقة بعد {days} يوم',
    'sug.head.subscription': '{name} تبدو دفعة منتظمة',
    'sug.head.unusual_spend': 'دفعة غير معتادة',
    'sug.head.missing_statement': 'لا كشف لحساب {account} في {month}',
    'sug.head.cash_shortage': 'نقص محتمل في النقد بعملة {currency}',
    'sug.head.reconciliation': '{account}: {exception}',
    'sug.head.external': 'يحتاج إلى مراجعتك',
    'sug.why.classification':
        'اخترت {category} للتاجر {merchant} في {agree} من {seen} مرات.',
    'sug.why.conflict':
        'صنفتها قاعدة على أنها {current}، لكنك اخترت {category} للتاجر {merchant} في {agree} من {seen} مرات.',
    'sug.why.uncategorized':
        'لم تتعرف عليها أي قاعدة. لا تُحسب دخلًا ولا صرفًا حتى تصنفها.',
    'sug.why.statement_hold':
        'قد تطابق هذه الصفوف أكثر من دفعة مسجلة، لذلك لم تُضف تلقائيًا.',
    'sug.why.sms_review':
        'لم تُسجل تلقائيًا. راجعها في رسائل iPhone، المراجعة على الويب.',
    'sug.why.duplicate':
        'نفس الحساب والمبلغ خلال {days} أيام، وكلاهما يذكر التاجر نفسه.',
    'sug.why.refund_match':
        'المبلغ يناسب عملية شراء سابقة، لكن الوصفين لا يتفقان تمامًا.',
    'sug.why.payment_match':
        'إذا دفعتها، طابق الدفعة في الفواتير حتى لا تُحسب مستحقة.',
    'sug.why.bill_due': 'لا توجد دفعة مطابقة لهذا الموعد بعد.',
    'sug.why.subscription': 'ظهرت {count} مرات. احفظها فاتورة لمتابعتها.',
    'sug.why.unusual_spend': 'تدفع عادةً {usual} هنا ({count} دفعات سابقة).',
    'sug.why.missing_statement': 'استورد كشف ذلك الشهر حتى لا ينقص شيء.',
    'sug.why.cash_shortage':
        'الفواتير والحد الأدنى للدفعات والأهداف هذا الشهر أكثر مما لديك.',
    'sug.why.reconciliation.blocking':
        'لا يمكن إكمال مطابقة الفترة من {start} إلى {end} حتى تحل هذا.',
    'sug.why.reconciliation.warning':
        'يمكن إكمال مطابقة الفترة من {start} إلى {end}، لكن هذا لم يُفسر بعد.',
    'sug.why.reconciliation.missing':
        'هناك حركة في {month} ليست في أي كشف. استورد كشف ذلك الشهر ثم طابقه.',
  };

  static bool get catalogsMatch =>
      _en.keys.toSet().containsAll(_ar.keys) &&
      _ar.keys.toSet().containsAll(_en.keys);

  /// Every text in both catalogs, for the punctuation guard in tests.
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];
}
