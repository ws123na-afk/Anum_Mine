import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the calendar reminder email settings in
/// Settings > Notifications (docs/calendar.md#reminders). The terms follow the
/// web catalog (`settings.notifications.calendar_email*` keys of
/// apps/web/src/lib/messages/calendar.en.ts and calendar.ar.ts). Both maps
/// hold the same keys (test/notification_email_test.dart checks it).
class CalendarEmailStrings {
  const CalendarEmailStrings(this.arabic);
  final bool arabic;

  static CalendarEmailStrings of(BuildContext context) =>
      CalendarEmailStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key) => (arabic ? ar : en)[key] ?? en[key] ?? key;

  static const en = <String, String>{
    'title': 'Calendar reminders by email',
    'hint':
        'Also email the calendar reminders an event asks to send by email. Needs Email delivery above to be on.',
    'needsEmail':
        'Off while Email delivery is off: switch it on to get calendar reminder emails.',
    'language': 'Language of calendar reminder emails',
    'both': 'English, then Arabic',
    'en': 'English',
    'ar': 'Arabic',
  };

  static const ar = <String, String>{
    'title': 'تذكيرات التقويم بالبريد',
    'hint':
        'أرسل بالبريد أيضًا تذكيرات التقويم التي يطلب الحدث إرسالها بالبريد. يلزم تفعيل التسليم بالبريد أعلاه.',
    'needsEmail':
        'متوقفة ما دام التسليم بالبريد متوقفًا: فعّله لتصلك رسائل تذكير التقويم.',
    'language': 'لغة رسائل تذكير التقويم',
    'both': 'الإنجليزية ثم العربية',
    'en': 'الإنجليزية',
    'ar': 'العربية',
  };
}
