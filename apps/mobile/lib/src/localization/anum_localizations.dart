import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AnumLocalizations {
  const AnumLocalizations(this.locale);
  final Locale locale;
  bool get isArabic => locale.languageCode == 'ar';
  static const delegate = _AnumDelegate();
  static AnumLocalizations of(BuildContext context) => Localizations.of<AnumLocalizations>(context, AnumLocalizations)!;
  String t(String key) => (isArabic ? _ar : _en)[key] ?? _en[key] ?? key;
  static const _en = <String, String>{
    'home':'Home',
    'workspace':'ANUM Workspace','subtitle':'Governed agent operations','tasks':'Tasks','voice':'Voice','approvals':'Approvals','automation':'Automation','resources':'Resources','refresh':'Refresh','settings':'Settings','organization':'Organization operations','language':'Language','english':'English','arabic':'Arabic','offline':'Offline. Showing the most recent workspace state.','signIn':'Sign in to ANUM','continue':'Continue','workspaceSetup':'Set up your workspace','createWorkspace':'Create workspace','modelSetup':'Connect a model','saveModel':'Test, save, and continue',
  };
  static const _ar = <String, String>{
    'home':'الرئيسية',
    'workspace':'مساحة عمل أنوم','subtitle':'عمليات وكلاء خاضعة للحوكمة','tasks':'المهام','voice':'الصوت','approvals':'الموافقات','automation':'الأتمتة','resources':'الموارد','refresh':'تحديث','settings':'الإعدادات','organization':'عمليات المؤسسة','language':'اللغة','english':'الإنجليزية','arabic':'العربية','offline':'غير متصل. يتم عرض أحدث حالة محفوظة لمساحة العمل.','signIn':'تسجيل الدخول إلى أنوم','continue':'متابعة','workspaceSetup':'إعداد مساحة العمل','createWorkspace':'إنشاء مساحة العمل','modelSetup':'ربط نموذج','saveModel':'اختبار وحفظ ومتابعة',
  };
}

class _AnumDelegate extends LocalizationsDelegate<AnumLocalizations> {
  const _AnumDelegate();
  @override bool isSupported(Locale locale) => const {'en','ar'}.contains(locale.languageCode);
  @override Future<AnumLocalizations> load(Locale locale) => SynchronousFuture(AnumLocalizations(locale));
  @override bool shouldReload(_AnumDelegate old) => false;
}

class LocaleController extends ChangeNotifier {
  Locale? locale;
  static const _key='anum.locale';
  Future<void> restore() async {final code=(await SharedPreferences.getInstance()).getString(_key);if(code!=null&&const {'en','ar'}.contains(code))locale=Locale(code);notifyListeners();}
  Future<void> select(String code) async {locale=Locale(code);notifyListeners();await (await SharedPreferences.getInstance()).setString(_key,code);}
}

extension AnumTranslation on BuildContext {AnumLocalizations get anum=>AnumLocalizations.of(this);}
