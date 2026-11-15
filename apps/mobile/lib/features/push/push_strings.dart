import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of phone push in Settings > Notifications
/// (docs/notifications.md#phone-push). Both maps hold the same keys
/// (test/push/push_test.dart checks it). `{name}` is filled in by [f].
class PushStrings {
  const PushStrings(this.arabic);
  final bool arabic;

  static PushStrings of(BuildContext context) =>
      PushStrings(AnumLocalizations.maybeOf(context).isArabic);

  /// For the controller, which has the app's language code but no context.
  static PushStrings forLanguage(String? language) =>
      PushStrings(language == 'ar');

  String t(String key) => (arabic ? ar : en)[key] ?? en[key] ?? key;

  String f(String key, Map<String, String> values) {
    var text = t(key);
    values.forEach((name, value) => text = text.replaceAll('{$name}', value));
    return text;
  }

  static const en = <String, String>{
    'checkingTitle': 'Phone push',
    'checkingText': 'Checking whether this phone can get notifications...',
    'noBuildTitle': 'Phone push is not set up in this app',
    'noBuildText':
        'This build has no Firebase configuration, so it cannot get push notifications. Approvals and results still appear in the app. The owner adds google-services.json and GoogleService-Info.plist and rebuilds (docs/mobile.md).',
    'serverOffTitle': 'Phone push is off on the server',
    'serverOffText':
        'Phone push is off on this server. Approvals and results still appear in the app. Ask the person who runs this ANUM server to set it up.',
    'notAskedTitle': 'Get notified on this phone',
    'notAskedText':
        'Hear about approvals waiting for you and tasks that finish or fail, even when the app is closed. A notification says only what happened, never the content.',
    'blockedTitle': 'Notifications are blocked',
    'blockedText':
        "Notifications for ANUM are turned off in this phone's settings. Turn them on there, then come back.",
    'onTitle': 'This phone gets notifications',
    'onText':
        'For this workspace. Turn off "Phone push" above to pause them, or remove this phone below.',
    'signedOutText': 'Sign in to get notifications on this phone.',
    'errorTitle': 'Phone push needs attention',
    'errorText': 'See below for what failed.',
    'turnOn': 'Turn on notifications',
    'openSettings': 'Open phone settings',
    'checkAgain': 'Check again',
    'myPhones': 'My phones in this workspace',
    'thisPhone': '{name} (this phone)',
    'notNotified': 'Not notified yet',
    'lastNotified': 'Last notified {date}',
    'remove': 'Remove {name}',
    'turnedOn': 'Notifications are on for this phone in this workspace.',
    'removed': 'Removed {name}.',
    'noToken':
        'This phone has no push token yet. Check that Google Play services (Android) or notifications (iPhone) work, then try again.',
    'failedCheck': 'Could not check phone push.',
    'failedEnable': 'Notifications were not turned on.',
    'failedRemove': '{name} was not removed.',
    'sessionExpired': 'Your session expired; sign in again.',
    'tryAgain': 'Try again.',
    'checkConnection': 'Check your connection and try again.',
  };

  static const ar = <String, String>{
    'checkingTitle': 'إشعارات الهاتف',
    'checkingText': 'جارٍ التحقق مما إذا كان هذا الهاتف يستقبل الإشعارات...',
    'noBuildTitle': 'إشعارات الهاتف غير مُعدّة في هذا التطبيق',
    'noBuildText':
        'لا يحتوي هذا الإصدار على إعدادات Firebase، لذا لا يستقبل الإشعارات. تظهر الموافقات والنتائج في التطبيق كما هي. يضيف المالك الملفين google-services.json و GoogleService-Info.plist ثم يعيد البناء (docs/mobile.md).',
    'serverOffTitle': 'إشعارات الهاتف متوقفة على الخادم',
    'serverOffText':
        'إشعارات الهاتف متوقفة على هذا الخادم. تظهر الموافقات والنتائج في التطبيق كما هي. اطلب من مسؤول خادم ANUM إعدادها.',
    'notAskedTitle': 'استقبل الإشعارات على هذا الهاتف',
    'notAskedText':
        'اعرف بالموافقات التي تنتظرك والمهام التي تنتهي أو تفشل، حتى والتطبيق مغلق. يذكر الإشعار ما حدث فقط، ولا يعرض المحتوى أبدًا.',
    'blockedTitle': 'الإشعارات محظورة',
    'blockedText':
        'إشعارات ANUM متوقفة في إعدادات هذا الهاتف. فعّلها هناك ثم عُد إلى هنا.',
    'onTitle': 'هذا الهاتف يستقبل الإشعارات',
    'onText':
        'لمساحة العمل هذه. أوقف "إشعارات الهاتف" أعلاه لإيقافها مؤقتًا، أو أزل هذا الهاتف أدناه.',
    'signedOutText': 'سجّل الدخول لتستقبل الإشعارات على هذا الهاتف.',
    'errorTitle': 'إشعارات الهاتف تحتاج إلى انتباهك',
    'errorText': 'انظر أدناه لمعرفة ما فشل.',
    'turnOn': 'فعّل الإشعارات',
    'openSettings': 'افتح إعدادات الهاتف',
    'checkAgain': 'تحقق مرة أخرى',
    'myPhones': 'هواتفي في مساحة العمل هذه',
    'thisPhone': '{name} (هذا الهاتف)',
    'notNotified': 'لم يصله إشعار بعد',
    'lastNotified': 'آخر إشعار {date}',
    'remove': 'أزل {name}',
    'turnedOn': 'الإشعارات مفعّلة لهذا الهاتف في مساحة العمل هذه.',
    'removed': 'أُزيل {name}.',
    'noToken':
        'لا يملك هذا الهاتف رمز إشعارات بعد. تأكد من عمل خدمات Google Play (أندرويد) أو الإشعارات (آيفون)، ثم حاول مرة أخرى.',
    'failedCheck': 'تعذّر التحقق من إشعارات الهاتف.',
    'failedEnable': 'لم تُفعَّل الإشعارات.',
    'failedRemove': 'لم يُزَل {name}.',
    'sessionExpired': 'انتهت جلستك؛ سجّل الدخول مرة أخرى.',
    'tryAgain': 'حاول مرة أخرى.',
    'checkConnection': 'تحقق من اتصالك وحاول مرة أخرى.',
  };
}
