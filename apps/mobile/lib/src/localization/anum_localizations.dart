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
