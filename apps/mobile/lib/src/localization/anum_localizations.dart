import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AnumLocalizations {
  const AnumLocalizations(this.locale);
  final Locale locale;
  bool get isArabic => locale.languageCode == 'ar';
  static const delegate = _AnumDelegate();
  static AnumLocalizations of(BuildContext context) =>
      Localizations.of<AnumLocalizations>(context, AnumLocalizations)!;
  String t(String key) => (isArabic ? _ar : _en)[key] ?? _en[key] ?? key;

  /// [t] with each `{name}` placeholder replaced by its value.
  String f(String key, Map<String, Object> values) {
    var text = t(key);
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// The catalog for [context], or English when none is installed (widgets
  /// pumped in tests without the delegate).
  static AnumLocalizations maybeOf(BuildContext context) =>
      Localizations.of<AnumLocalizations>(context, AnumLocalizations) ??
      english;
  static const english = AnumLocalizations(Locale('en'));
  static const _en = <String, String>{
    'assistant': 'Assistant',
    'home': 'Home',
    'workspace': 'ANUM Workspace',
    'subtitle': 'Governed agent operations',
    'tasks': 'Tasks',
    'voice': 'Voice',
    'approvals': 'Approvals',
    'automation': 'Automation',
    'resources': 'Resources',
    'developer': 'Developer',
    'refresh': 'Refresh',
    'settings': 'Settings',
    'organization': 'Organization operations',
    'language': 'Language',
    'english': 'English',
    'arabic': 'Arabic',
    'offline': 'Offline. Showing the most recent workspace state.',
    'signIn': 'Sign in to ANUM',
    'continue': 'Continue',
    'workspaceSetup': 'Set up your workspace',
    'createWorkspace': 'Create workspace',
    'modelSetup': 'Connect a model',
    'saveModel': 'Test, save, and continue',
    'sourcesEyebrow': 'Retrieval',
    'sourcesTitle': 'Sources used',
    'sourcesNone': 'none',
    'sourcesOne': '1 source',
    'sourcesMany': '{n} sources',
    'sourcesNoneSpoken': 'No sources used',
    'sourcesCountSpoken': '{count} used by this run',
    'sourcesMemory': 'Memory',
    'sourcesFile': 'File',
    'sourcesPassage': 'passage {n}, {score}% match',
    'sourcesShortened': 'shortened',
    'sourcesOpensFile': 'opens file details',
    'sourcesOpensMemory': 'opens it in Memory',
    'sourcesTruncated':
        'Some retrieved text was shortened to fit the prompt limit.',
    'sourcesNoResults': 'No indexed memory or file matched this task.',
    'sourcesSkipped': 'Retrieval skipped.',
    'sourcesSkippedReason': 'Retrieval skipped: {reason}.',
    'sourcesUnavailable':
        'Retrieval was unavailable; the task ran without workspace context.',
    'sourcesUntrusted': 'Retrieved text is passed to the model as labeled, '
        'untrusted data; it cannot change tools or approvals.',
    'sourcesLoading': "Loading the run's sources…",
    'sourcesErrorTitle': "Couldn't load the sources used",
    'sourcesErrorHint': '{message} Check your connection, then try again.',
    'sourcesDeniedHint': '{message} Members with task access can see them; '
        'ask a workspace owner for access.',
    'retry': 'Try again',
    'memoryUsedByRun': 'Used by the run you came from',
    'whatsapp': 'WhatsApp',
    'waLoading': 'Loading WhatsApp…',
    'waDeniedTitle': 'WhatsApp is for owners and members',
    'waDeniedText':
        'Customer conversations are visible to workspace owners and members. Ask an owner of this workspace if you need them.',
    'waNotConnected': 'WhatsApp is not connected yet',
    'waNotConnectedOwner':
        'Connect your WhatsApp Business number on the web: WhatsApp > Setup. Customers\' messages then arrive here.',
    'waNotConnectedMember':
        'An owner of this workspace connects the WhatsApp Business number. Ask them to connect it.',
    'waLoadFailed': 'WhatsApp could not be loaded',
    'waSearch': 'Search conversations',
    'waClearSearch': 'Clear search',
    'waFilter.all': 'All',
    'waFilter.unread': 'Unread',
    'waFilter.needs_reply': 'Needs a reply',
    'waFilter.awaiting_approval': 'Awaiting approval',
    'waNoMatch': 'No matching conversations',
    'waNoMatchText': 'Try other words or another filter.',
    'waShowAll': 'Show all conversations',
    'waEmpty': 'No conversations yet',
    'waEmptyText':
        'Messages customers send to {number} appear here within seconds.',
    'waMore': 'Load older conversations',
    'waUnread': 'unread: {count}',
    'waAwaiting': 'awaiting approval: {count}',
    'waYou': 'You:',
    'waWindowOpenShort': 'Reply window open',
    'waTemplatesOnly': 'Templates only',
    'waCategory.order_status': 'Order question',
    'waCategory.price_stock': 'Price or stock',
    'waCategory.complaint': 'Complaint',
    'waCategory.supplier': 'Supplier',
    'waCategory.other': 'Other',
    'waWindowClosed': '24-hour window closed: templates only',
    'waWindowHours': 'Free-form replies for {count} h more',
    'waWindowMinutes': 'Free-form replies for {count} min more',
    'waThreadFailed': 'The conversation could not be loaded',
    'waBudget':
        'This workspace has used its monthly model budget. An owner can raise it under Budgets.',
    'waOlder': 'Load older messages',
    'waNoMessages': 'No messages in this conversation yet.',
    'waReply': 'Your reply',
    'waApprovalNote': 'Free-form replies always need an owner\'s approval.',
    'waSending': 'Sending…',
    'waSendForApproval': 'Send for approval',
    'waClosedExplain':
        'More than 24 hours passed since the customer\'s last message, so WhatsApp allows only approved templates. Send a template from the web inbox.',
    'waSuggested': 'Suggested reply',
    'waThinking': 'Thinking…',
    'waSuggest': 'Suggest a reply',
    'waSuggestAgain': 'Suggest again',
    'waNoSuggestion': 'No suggestion yet. Nothing is sent until you send it.',
    'waCustomerWants': 'Customer wants:',
    'waFromModel':
        'AI draft by {model} from {count} customer message(s), read as untrusted text. Check prices and dates before sending.',
    'waFromRules':
        'Rule-based draft. Connect a model in Settings for AI drafts.',
    'waUseDraft': 'Use this draft',
    'waUnsupported':
        'A {kind} message ANUM cannot show. Open WhatsApp to see it.',
    'waFromCustomer': 'From the customer',
    'waFromYou': 'From your business',
    'waTemplate': 'Template {name}',
    'waMedia': 'Attachment (open it in the web inbox)',
    'waStatus.received': 'received',
    'waStatus.awaiting_approval': 'awaiting approval',
    'waStatus.sending': 'sending',
    'waStatus.sent': 'sent',
    'waStatus.delivered': 'delivered',
    'waStatus.read': 'read',
    'waStatus.failed': 'failed',
    'waStatus.not_sent': 'not sent',
    'waApproveTitle': 'Approve this reply',
    'waTarget': 'Sends through {target}',
    'waHash': 'Payload hash {hash}',
    'waApproveSend': 'Approve & send',
    'waReject': 'Reject',
    'waOwnerApproves': 'Waiting for an owner of this workspace to approve it.',
    'waAwaitingOwner':
        'Waiting for approval. Approve it below or under Approvals.',
    'waAwaitingMember':
        'Waiting for an owner to approve it. It is sent as soon as they do.',
    'waSent': 'Sent.',
    'waApproved': 'Approved. The message status shows whether it was sent.',
    'waRejected': 'Rejected. The reply will not be sent.',
  };
  static const _ar = <String, String>{
    'assistant': 'المساعد',
    'home': 'الرئيسية',
    'workspace': 'مساحة عمل أنوم',
    'subtitle': 'عمليات وكلاء خاضعة للحوكمة',
    'tasks': 'المهام',
    'voice': 'الصوت',
    'approvals': 'الموافقات',
    'automation': 'الأتمتة',
    'resources': 'الموارد',
    'developer': 'المطوّر',
    'refresh': 'تحديث',
    'settings': 'الإعدادات',
    'organization': 'عمليات المؤسسة',
    'language': 'اللغة',
    'english': 'الإنجليزية',
    'arabic': 'العربية',
    'offline': 'غير متصل. يتم عرض أحدث حالة محفوظة لمساحة العمل.',
    'signIn': 'تسجيل الدخول إلى أنوم',
    'continue': 'متابعة',
    'workspaceSetup': 'إعداد مساحة العمل',
    'createWorkspace': 'إنشاء مساحة العمل',
    'modelSetup': 'ربط نموذج',
    'saveModel': 'اختبار وحفظ ومتابعة',
    'sourcesEyebrow': 'الاسترجاع',
    'sourcesTitle': 'المصادر المستخدمة',
    'sourcesNone': 'لا شيء',
    'sourcesOne': 'مصدر واحد',
    'sourcesMany': 'المصادر: {n}',
    'sourcesNoneSpoken': 'لم تُستخدم أي مصادر',
    'sourcesCountSpoken': '{count}، استخدمها هذا التشغيل',
    'sourcesMemory': 'ذاكرة',
    'sourcesFile': 'ملف',
    'sourcesPassage': 'المقطع {n}، تطابق {score}٪',
    'sourcesShortened': 'مختصر',
    'sourcesOpensFile': 'يفتح تفاصيل الملف',
    'sourcesOpensMemory': 'يفتحها في الذاكرة',
    'sourcesTruncated': 'اختُصر بعض النص المسترجع ليناسب حد الطلب.',
    'sourcesNoResults': 'لم تطابق أي ذاكرة أو ملف مفهرس هذه المهمة.',
    'sourcesSkipped': 'تم تخطي الاسترجاع.',
    'sourcesSkippedReason': 'تم تخطي الاسترجاع: {reason}.',
    'sourcesUnavailable':
        'لم يكن الاسترجاع متاحًا؛ نُفذت المهمة دون سياق مساحة العمل.',
    'sourcesUntrusted': 'يُمرَّر النص المسترجع إلى النموذج كبيانات موسومة غير '
        'موثوقة؛ ولا يمكنه تغيير الأدوات أو الموافقات.',
    'sourcesLoading': 'جارٍ تحميل مصادر التشغيل…',
    'sourcesErrorTitle': 'تعذر تحميل المصادر المستخدمة',
    'sourcesErrorHint': '{message} تحقق من الاتصال ثم حاول مرة أخرى.',
    'sourcesDeniedHint': '{message} يستطيع رؤيتها الأعضاء الذين لديهم صلاحية '
        'المهام؛ اطلب الصلاحية من مالك مساحة العمل.',
    'retry': 'حاول مرة أخرى',
    'memoryUsedByRun': 'استخدمها التشغيل الذي أتيت منه',
    'whatsapp': 'واتساب',
    'waLoading': 'جارٍ تحميل واتساب…',
    'waDeniedTitle': 'واتساب متاح للمالكين والأعضاء',
    'waDeniedText':
        'محادثات العملاء تظهر لمالكي مساحة العمل وأعضائها. اطلب من مالك مساحة العمل إن كنت تحتاج إليها.',
    'waNotConnected': 'واتساب غير متصل بعد',
    'waNotConnectedOwner':
        'اربط رقم واتساب للأعمال من الويب: واتساب > الإعداد. ستصل رسائل العملاء هنا بعد ذلك.',
    'waNotConnectedMember':
        'يربط مالك مساحة العمل رقم واتساب للأعمال. اطلب منه ربطه.',
    'waLoadFailed': 'تعذّر تحميل واتساب',
    'waSearch': 'ابحث في المحادثات',
    'waClearSearch': 'مسح البحث',
    'waFilter.all': 'الكل',
    'waFilter.unread': 'غير مقروءة',
    'waFilter.needs_reply': 'بانتظار الرد',
    'waFilter.awaiting_approval': 'بانتظار الموافقة',
    'waNoMatch': 'لا محادثات مطابقة',
    'waNoMatchText': 'جرّب كلمات أخرى أو تصفية أخرى.',
    'waShowAll': 'عرض كل المحادثات',
    'waEmpty': 'لا توجد محادثات بعد',
    'waEmptyText':
        'تظهر هنا خلال ثوانٍ الرسائل التي يرسلها العملاء إلى {number}.',
    'waMore': 'تحميل محادثات أقدم',
    'waUnread': 'غير مقروءة: {count}',
    'waAwaiting': 'بانتظار الموافقة: {count}',
    'waYou': 'أنت:',
    'waWindowOpenShort': 'نافذة الرد مفتوحة',
    'waTemplatesOnly': 'القوالب فقط',
    'waCategory.order_status': 'استفسار عن طلب',
    'waCategory.price_stock': 'سعر أو توفر',
    'waCategory.complaint': 'شكوى',
    'waCategory.supplier': 'مورّد',
    'waCategory.other': 'أخرى',
    'waWindowClosed': 'انتهت نافذة الـ24 ساعة: القوالب فقط',
    'waWindowHours': 'الرد الحر متاح لـ{count} س أخرى',
    'waWindowMinutes': 'الرد الحر متاح لـ{count} د أخرى',
    'waThreadFailed': 'تعذّر تحميل المحادثة',
    'waBudget':
        'استهلكت مساحة العمل ميزانية النموذج الشهرية. يستطيع المالك رفعها من الميزانيات.',
    'waOlder': 'تحميل رسائل أقدم',
    'waNoMessages': 'لا رسائل في هذه المحادثة بعد.',
    'waReply': 'ردك',
    'waApprovalNote': 'الردود الحرة تحتاج دائمًا إلى موافقة المالك.',
    'waSending': 'جارٍ الإرسال…',
    'waSendForApproval': 'إرسال للموافقة',
    'waClosedExplain':
        'مرّ أكثر من 24 ساعة على آخر رسالة من العميل، لذا يسمح واتساب بالقوالب المعتمدة فقط. أرسل قالبًا من صندوق الوارد على الويب.',
    'waSuggested': 'الرد المقترح',
    'waThinking': 'جارٍ التفكير…',
    'waSuggest': 'اقترح ردًا',
    'waSuggestAgain': 'اقترح من جديد',
    'waNoSuggestion': 'لا اقتراح بعد. لا يُرسل شيء حتى ترسله أنت.',
    'waCustomerWants': 'يريد العميل:',
    'waFromModel':
        'مسودة ذكاء اصطناعي من {model} بناءً على رسائل العميل ({count})، قُرئت كنص غير موثوق. تحقّق من الأسعار والتواريخ قبل الإرسال.',
    'waFromRules':
        'مسودة من القواعد. اربط نموذجًا في الإعدادات لمسودات الذكاء الاصطناعي.',
    'waUseDraft': 'استخدم هذه المسودة',
    'waUnsupported':
        'رسالة من نوع {kind} لا يستطيع ANUM عرضها. افتح واتساب لرؤيتها.',
    'waFromCustomer': 'من العميل',
    'waFromYou': 'من نشاطك التجاري',
    'waTemplate': 'القالب {name}',
    'waMedia': 'مرفق (افتحه في صندوق الوارد على الويب)',
    'waStatus.received': 'مستلمة',
    'waStatus.awaiting_approval': 'بانتظار الموافقة',
    'waStatus.sending': 'قيد الإرسال',
    'waStatus.sent': 'أُرسلت',
    'waStatus.delivered': 'وصلت',
    'waStatus.read': 'قُرئت',
    'waStatus.failed': 'فشلت',
    'waStatus.not_sent': 'لم تُرسل',
    'waApproveTitle': 'الموافقة على هذا الرد',
    'waTarget': 'يُرسل عبر {target}',
    'waHash': 'بصمة المحتوى {hash}',
    'waApproveSend': 'موافقة وإرسال',
    'waReject': 'رفض',
    'waOwnerApproves': 'بانتظار موافقة مالك مساحة العمل.',
    'waAwaitingOwner': 'بانتظار الموافقة. وافق عليها أدناه أو من الموافقات.',
    'waAwaitingMember': 'بانتظار موافقة المالك. تُرسل فور موافقته.',
    'waSent': 'أُرسلت.',
    'waApproved': 'تمت الموافقة. تُظهر حالة الرسالة ما إذا أُرسلت.',
    'waRejected': 'رُفض. لن يُرسل الرد.',
  };
}

class _AnumDelegate extends LocalizationsDelegate<AnumLocalizations> {
  const _AnumDelegate();
  @override
  bool isSupported(Locale locale) =>
      const {'en', 'ar'}.contains(locale.languageCode);
  @override
  Future<AnumLocalizations> load(Locale locale) =>
      SynchronousFuture(AnumLocalizations(locale));
  @override
  bool shouldReload(_AnumDelegate old) => false;
}

class LocaleController extends ChangeNotifier {
  Locale? locale;
  static const _key = 'anum.locale';
  Future<void> restore() async {
    final code = (await SharedPreferences.getInstance()).getString(_key);
    if (code != null && const {'en', 'ar'}.contains(code)) {
      locale = Locale(code);
    }
    notifyListeners();
  }

  Future<void> select(String code) async {
    locale = Locale(code);
    notifyListeners();
    await (await SharedPreferences.getInstance()).setString(_key, code);
  }
}

extension AnumTranslation on BuildContext {
  AnumLocalizations get anum => AnumLocalizations.of(this);
}
