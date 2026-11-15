import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';
import '../records_kit/records_widgets.dart';

/// English and Arabic strings of a transaction's corrections on the phone
/// (docs/finance.md#transaction-detail): the category with its subcategory,
/// the type, "Use this for later payments", "Remember this category", Apply
/// to similar with its preview and undo, the AI category, money in
/// corrected to money out, and a duplicate copy deleted. The terms follow the
/// web drawer's catalogs (`financeRegister`, `financeCategories`,
/// `financeCore`, `financeSuggestions`).
class FinanceCorrectStrings extends ModuleStrings {
  const FinanceCorrectStrings(bool arabic) : super(arabic, _en, _ar);
  static FinanceCorrectStrings of(BuildContext context) =>
      FinanceCorrectStrings(AnumLocalizations.maybeOf(context).isArabic);
  static bool get catalogsMatch => ModuleStrings.match(_en, _ar);
  static Iterable<String> get allTexts => [..._en.values, ..._ar.values];

  /// "Put back one row", "Put back 3 rows"; Arabic by its plural forms.
  String count(String key, int n) {
    if (!arabic) return t(n == 1 ? '$key.one' : '$key.other', {'n': n});
    final form = '$key.${AnumLocalizations.arabicPluralForm(n)}';
    return t(maybe(form) == null ? '$key.other' : form, {'n': n});
  }

  static const _en = <String, String>{
    'title': 'Category and type',
    'suggested': 'Suggested category: {category}, {by}',
    'suggestedBy.owner_rule': 'from your rule',
    'suggestedBy.rule': 'from a built-in rule',
    'suggestedBy.manual': 'chosen by you',
    'suggestedBy.none': 'no rule matched',
    'suggestedBy.match': 'matched with another record',
    'suggestedBy.system': 'set by ANUM',
    'type': 'Type: {type}',
    'typeBy.rule': 'from a rule',
    'typeBy.owner': 'chosen by you',
    'typeBy.system': 'set by ANUM',
    'typeBy.sms': 'from the message',
    'typeBy.match': 'matched with another record',
    'category': 'Choose category',
    'suggestedOption': '{category} (suggested)',
    'subcategory': 'Subcategory',
    'subcategoryNone': 'None',
    'chooseType': 'What is this?',
    'learn': 'Use this for later payments to this merchant',
    'learnHint':
        'New payments from the same merchant are filed the same way. You can switch it off under Suggestions, Categories.',
    'remember': 'Remember this category for matching descriptions',
    'rememberPhrase': 'Merchant or description to remember (a specific phrase)',
    'rememberHint':
        'Applies to future imports only. Check the phrase so unrelated payments are not affected.',
    'rememberShort':
        'Type a specific phrase of at least 4 characters from the description.',
    'rememberMissing':
        'The phrase must appear in this transaction\'s description.',
    'save': 'Save',
    'saveCategory': 'Save category',
    'saving': 'Saving…',
    'typeSaved': 'Saved as {type}.',
    'categorySaved': 'Saved as {category}. ANUM keeps your choice.',
    'failed': 'Not saved: {message}',
    'smsOrigin':
        'An SMS or Apple Pay message recorded this transaction. Correct its category under More in Finance, iPhone SMS, so the audit trail keeps the change.',
    'uncategorizedTitle': 'Not counted yet',
    'uncategorizedText':
        'No rule recognised this payment, so it is in neither income nor spending. Choose what it is and save.',
    'refundSuggested':
        'This may be a refund of an earlier purchase. Check it and choose the purchase\'s category to take it off that spending.',
    'linksReview.one':
        'A link to this payment needs review because the payment changed.',
    'linksReview.other':
        '{n} links to this payment need review because the payment changed.',
    'duplicate': 'Possible duplicate',
    'duplicateText':
        'Similar to another record: {amount} on {date}, "{description}". Check it, then delete the copy that is wrong or keep both.',
    'viewMatch': 'View match',
    'deleteCopy': 'Delete this copy',
    'deleteTitle': 'Delete this transaction?',
    'deleteText':
        '{description}, {amount}, is removed from your ledger. Importing the same statement again brings it back.',
    'delete': 'Delete',
    'deleted': 'Deleted.',
    'directionTitle': 'Check payment direction',
    'directionText':
        'This room-rent entry is incoming. If you paid it, check the statement debit and correct it to money out. Nothing is changed on its own.',
    'correctOut': 'Correct to money out',
    'confirmOutTitle': 'Confirm outgoing payment',
    'confirmOutText':
        'Only confirm after checking the bank statement and choosing the right spending category. This changes {amount} from money in to money out, updates the ledger and may change your balance. The original record stays in the audit trail.',
    'outNeedsCategory':
        'Choose a spending category before changing an incoming transaction to money out.',
    'outDone':
        'Corrected to money out. The balance and the ledger are updated.',
    'similar': 'Apply to similar',
    'similarTitle': 'Apply to similar transactions',
    'similarIntro':
        'The rule this correction suggests and the other rows it would change. Nothing changes until you save.',
    'scope': 'Where',
    'scope.all': 'Every account',
    'scope.account': 'Only this account',
    'rule': 'The rule: {match}, {direction}, files as {category}.',
    'dir.in': 'money in',
    'dir.out': 'money out',
    'dir.any': 'money in or out',
    'saveApply': 'Save rule and apply to {n} rows',
    'saveApply.one': 'Save rule and apply to 1 row',
    'saveOnly': 'Save rule only',
    'savedOnly': 'Rule saved. It runs on new rows from now on.',
    'applied':
        'Changed {n} rows. You can undo it here or under Rules, Recent changes.',
    'cancel': 'Cancel',
    'loading': 'Finding similar transactions…',
    'loadFailed': 'Similar transactions could not be found: {message}',
    'denied':
        'You cannot change rules here. Ask the workspace owner for Finance access.',
    'undo': 'Undo',
    'undone.zero': 'No row was put back.',
    'undone.one': 'Put back one row.',
    'undone.two': 'Put back 2 rows.',
    'undone.few': 'Put back {n} rows.',
    'undone.many': 'Put back {n} rows.',
    'undone.other': 'Put back {n} rows.',
    'ai': 'Ask AI for a category',
    'aiResult':
        'AI suggests {category}. It is not saved: choose it above if it is right.',
    'aiNone': 'The AI found no category for this row.',
    'receipt': 'Receipt',
    'receipts.zero': 'No receipts linked',
    'receipts.one': '1 receipt linked',
    'receipts.two': '2 receipts linked',
    'receipts.few': '{n} receipts linked',
    'receipts.many': '{n} receipts linked',
    'receipts.other': '{n} receipts linked',
    'receiptHint':
        'PDF, JPG or PNG. It is kept privately in your document vault.',
    'receiptNeedsSave':
        'Save the category once to attach a receipt: this record was made before receipts could be linked.',
    'attach': 'Attach receipt',
    'attaching': 'Uploading…',
    'receiptAttached':
        'Receipt saved in your vault and linked to this payment.',
  };

  static const _ar = <String, String>{
    'title': 'الفئة والنوع',
    'suggested': 'الفئة المقترحة: {category}، {by}',
    'suggestedBy.owner_rule': 'من قاعدتك',
    'suggestedBy.rule': 'من قاعدة مدمجة',
    'suggestedBy.manual': 'اخترتها بنفسك',
    'suggestedBy.none': 'لم تطابق أي قاعدة',
    'suggestedBy.match': 'بالمطابقة مع سجل آخر',
    'suggestedBy.system': 'حددها أنوم',
    'type': 'النوع: {type}',
    'typeBy.rule': 'من قاعدة',
    'typeBy.owner': 'اخترته بنفسك',
    'typeBy.system': 'حدده أنوم',
    'typeBy.sms': 'من الرسالة',
    'typeBy.match': 'بالمطابقة مع سجل آخر',
    'category': 'اختر الفئة',
    'suggestedOption': '{category} (مقترحة)',
    'subcategory': 'الفئة الفرعية',
    'subcategoryNone': 'بلا',
    'chooseType': 'ما هذه المعاملة؟',
    'learn': 'استخدمها للدفعات اللاحقة لهذا التاجر',
    'learnHint':
        'تُصنف الدفعات الجديدة من التاجر نفسه بالطريقة نفسها. يمكنك إيقافها في الاقتراحات، الفئات.',
    'remember': 'تذكّر هذه الفئة للأوصاف المطابقة',
    'rememberPhrase': 'التاجر أو الوصف المطلوب تذكره (عبارة محددة)',
    'rememberHint':
        'تنطبق على الاستيرادات القادمة فقط. راجع العبارة حتى لا تتأثر دفعات لا علاقة لها.',
    'rememberShort': 'اكتب عبارة محددة من الوصف لا تقل عن 4 أحرف.',
    'rememberMissing': 'يجب أن تظهر العبارة في وصف هذه المعاملة.',
    'save': 'حفظ',
    'saveCategory': 'حفظ الفئة',
    'saving': 'جارٍ الحفظ…',
    'typeSaved': 'حُفظت على أنها {type}.',
    'categorySaved': 'حُفظت ضمن {category}. يحتفظ ANUM باختيارك.',
    'failed': 'لم يُحفظ: {message}',
    'smsOrigin':
        'سجلت هذه المعاملة رسالة نصية أو Apple Pay. صحح فئتها من المزيد في المالية، رسائل الآيفون، ليبقى التغيير في سجل التدقيق.',
    'uncategorizedTitle': 'غير محسوبة بعد',
    'uncategorizedText':
        'لم تتعرف أي قاعدة على هذه الدفعة، فهي ليست ضمن الدخل ولا الإنفاق. اختر نوعها ثم احفظ.',
    'refundSuggested':
        'قد تكون هذه استردادًا لشراء سابق. تحقق منها واختر فئة الشراء لتُخصم من ذلك الإنفاق.',
    'linksReview.one': 'رابط بهذه الدفعة يحتاج مراجعة لأن الدفعة تغيّرت.',
    'linksReview.other':
        'روابط بهذه الدفعة ({n}) تحتاج مراجعة لأن الدفعة تغيّرت.',
    'duplicate': 'تكرار محتمل',
    'duplicateText':
        'تشبه سجلًا آخر: {amount} في {date}، "{description}". تحقق منها ثم احذف النسخة الخاطئة أو أبقِ الاثنتين.',
    'viewMatch': 'عرض السجل المطابق',
    'deleteCopy': 'حذف هذه النسخة',
    'deleteTitle': 'أتحذف هذه المعاملة؟',
    'deleteText':
        'ستُحذف {description}، {amount}، من سجلك. استيراد الكشف نفسه مرة أخرى يعيدها.',
    'delete': 'حذف',
    'deleted': 'حُذفت.',
    'directionTitle': 'تحقق من اتجاه الدفعة',
    'directionText':
        'هذا الإيجار مسجل كمال وارد. إن كنت دفعته فتحقق من الخصم في الكشف وصححه إلى مال صادر. لا يتغير شيء من تلقاء نفسه.',
    'correctOut': 'صحح إلى مال صادر',
    'confirmOutTitle': 'أكد الدفعة الصادرة',
    'confirmOutText':
        'لا تؤكد إلا بعد مراجعة الكشف البنكي واختيار فئة الإنفاق الصحيحة. يغيّر هذا {amount} من مال وارد إلى مال صادر، ويحدّث الدفتر وقد يغيّر رصيدك. يبقى السجل الأصلي في سجل التدقيق.',
    'outNeedsCategory': 'اختر فئة إنفاق قبل تغيير معاملة واردة إلى مال صادر.',
    'outDone': 'صُححت إلى مال صادر. حُدّث الرصيد والدفتر.',
    'similar': 'تطبيق على المماثل',
    'similarTitle': 'تطبيق على المعاملات المماثلة',
    'similarIntro':
        'القاعدة التي يقترحها هذا التصحيح والصفوف الأخرى التي ستغيّرها. لا يتغير شيء قبل الحفظ.',
    'scope': 'النطاق',
    'scope.all': 'كل الحسابات',
    'scope.account': 'هذا الحساب فقط',
    'rule': 'القاعدة: {match}، {direction}، تصنف كـ {category}.',
    'dir.in': 'وارد',
    'dir.out': 'صادر',
    'dir.any': 'وارد أو صادر',
    'saveApply': 'حفظ القاعدة وتطبيقها على {n} صفًا',
    'saveApply.one': 'حفظ القاعدة وتطبيقها على صف واحد',
    'saveOnly': 'حفظ القاعدة فقط',
    'savedOnly': 'حُفظت القاعدة. تعمل على الصفوف الجديدة من الآن.',
    'applied':
        'تغير {n} صفًا. يمكنك التراجع هنا أو من القواعد، التغييرات الأخيرة.',
    'cancel': 'إلغاء',
    'loading': 'جارٍ البحث عن معاملات مماثلة…',
    'loadFailed': 'تعذر العثور على معاملات مماثلة: {message}',
    'denied':
        'لا يمكنك تغيير القواعد هنا. اطلب صلاحية المالية من مالك مساحة العمل.',
    'undo': 'تراجع',
    'undone.zero': 'لم يُرجع أي صف.',
    'undone.one': 'أُرجع صف واحد.',
    'undone.two': 'أُرجع صفان.',
    'undone.few': 'أُرجعت {n} صفوف.',
    'undone.many': 'أُرجع {n} صفًا.',
    'undone.other': 'أُرجع {n} صف.',
    'ai': 'اسأل الذكاء الاصطناعي عن الفئة',
    'aiResult':
        'يقترح الذكاء الاصطناعي {category}. لم يُحفظ: اختره أعلاه إذا كان صحيحًا.',
    'aiNone': 'لم يجد الذكاء الاصطناعي فئة لهذا الصف.',
    'receipt': 'الإيصال',
    'receipts.zero': 'لا إيصالات مرتبطة',
    'receipts.one': 'إيصال واحد مرتبط',
    'receipts.two': 'إيصالان مرتبطان',
    'receipts.few': '{n} إيصالات مرتبطة',
    'receipts.many': '{n} إيصالًا مرتبطًا',
    'receipts.other': '{n} إيصال مرتبط',
    'receiptHint': 'PDF أو JPG أو PNG. يُحفظ بخصوصية في خزنة مستنداتك.',
    'receiptNeedsSave':
        'احفظ الفئة مرة واحدة لتتمكن من إرفاق إيصال: سُجّل هذا السجل قبل أن يصبح ربط الإيصالات ممكنًا.',
    'attach': 'إرفاق إيصال',
    'attaching': 'جارٍ الرفع…',
    'receiptAttached': 'حُفظ الإيصال في خزنتك ورُبط بهذه الدفعة.',
  };
}
