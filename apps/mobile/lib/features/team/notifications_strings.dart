import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of the Notifications screen
/// (docs/team-work.md#notifications-page). The kind names match the web's
/// (`apps/web/src/lib/messages/webLeads.*.ts`, `ntf.kind.*`).
/// test/notifications_test.dart checks both catalogs have the same keys and
/// placeholders.
class NotificationText {
  const NotificationText(this.arabic);
  final bool arabic;

  static NotificationText of(BuildContext context) =>
      NotificationText(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key) => (arabic ? ar : en)[key] ?? en[key] ?? key;

  String f(String key, Map<String, Object> values) {
    var text = t(key);
    values.forEach((k, v) => text = text.replaceAll('{$k}', '$v'));
    return text;
  }

  /// Kinds the API sends today; any other reads as Other.
  static const knownKinds = [
    'work_assigned',
    'work_comment',
    'work_completed',
    'work_blocked',
    'work_follow_up',
    'work_overdue',
    'calendar_reminder',
    'approval_reminder',
    'budget_alert',
    'system_health',
    'system_restart',
    'agent_controls',
    'skill_signing',
    'document_expiry',
    'vehicle_reminder',
    'purchase_reminder',
    'attendance_missed_check',
    'visitor_arrived',
    'missed_call',
    'finance_daily_report',
    'finance_weekly_advice',
    'finance_loan_due',
    'finance_suggestions',
    'finance_exceptions',
    'finance_sms',
  ];

  String kind(String kind) =>
      knownKinds.contains(kind) ? t('kind.$kind') : t('kind.other');

  static const en = <String, String>{
    'title': 'Notifications',
    'unread': '{count} unread',
    'allRead': 'All read',
    'stateLabel': 'Show',
    'state.all': 'All',
    'state.unread': 'Unread',
    'state.read': 'Read',
    'kindsLabel': 'Kind',
    'kind.all': 'Every kind',
    'chip': '{label} {count}',
    'chipSemantics': '{label}, {count} in all, {unread} unread',
    'readAll': 'Mark all read',
    'refresh': 'Refresh',
    'loading': 'Loading notifications...',
    'loadingMore': 'Loading more...',
    'loadMore': 'Load more',
    'empty':
        'You are all caught up. New assignments, comments and reminders for you appear here.',
    'emptyFilter': 'No notifications match this filter.',
    'showEvery': 'Show every notification',
    'denied': 'You cannot read notifications in this workspace: {message}',
    'error': 'Could not load notifications: {message}',
    'kindsError': 'The kind filters could not load: {message}',
    'moreFor': 'More actions for {title}',
    'markRead': 'Mark read',
    'markUnread': 'Mark unread',
    'delete': 'Delete',
    'deleteTitle': 'Delete this notification?',
    'deleteText':
        '"{title}" is removed for good. The work item or record it points to stays as it is.',
    'cancel': 'Cancel',
    'deleted': 'Notification deleted.',
    'actionFailed': 'That did not work: {message}',
    'unreadMark': 'Unread',
    'rowSemantics': '{state}. {kind}. {title}. {body}. {when}',
    'stateRead': 'Read',
    'stateUnread': 'Unread',
    'kind.other': 'Other',
    'kind.work_assigned': 'Assigned to you',
    'kind.work_comment': 'Comments',
    'kind.work_completed': 'Work completed',
    'kind.work_blocked': 'Work blocked',
    'kind.work_follow_up': 'Follow-ups',
    'kind.work_overdue': 'Overdue work',
    'kind.calendar_reminder': 'Calendar reminders',
    'kind.approval_reminder': 'Approval reminders',
    'kind.system_health': 'System health',
    'kind.system_restart': 'Unexpected restart',
    'kind.budget_alert': 'Model budget',
    'kind.agent_controls': 'Agent controls',
    'kind.skill_signing': 'Skill signing',
    'kind.document_expiry': 'Document expiry',
    'kind.vehicle_reminder': 'Vehicle reminders',
    'kind.purchase_reminder': 'Purchase reminders',
    'kind.attendance_missed_check': 'Attendance',
    'kind.visitor_arrived': 'Visitors',
    'kind.missed_call': 'Missed calls',
    'kind.finance_daily_report': 'Trading report',
    'kind.finance_weekly_advice': 'Money advisor',
    'kind.finance_loan_due': 'Loan payments due',
    'kind.finance_suggestions': 'Finance suggestions',
    'kind.finance_exceptions': 'Finance exceptions',
    'kind.finance_sms': 'Bank messages',
  };

  static const ar = <String, String>{
    'title': 'الإشعارات',
    'unread': '{count} غير مقروء',
    'allRead': 'كلها مقروءة',
    'stateLabel': 'عرض',
    'state.all': 'الكل',
    'state.unread': 'غير المقروءة',
    'state.read': 'المقروءة',
    'kindsLabel': 'النوع',
    'kind.all': 'كل الأنواع',
    'chip': '{label} {count}',
    'chipSemantics': '{label}، {count} في المجموع، {unread} غير مقروء',
    'readAll': 'تعليم الكل كمقروء',
    'refresh': 'تحديث',
    'loading': 'جارٍ تحميل الإشعارات...',
    'loadingMore': 'جارٍ تحميل المزيد...',
    'loadMore': 'تحميل المزيد',
    'empty':
        'لا جديد لديك. تظهر هنا الإسنادات والتعليقات والتذكيرات الجديدة الخاصة بك.',
    'emptyFilter': 'لا توجد إشعارات تطابق هذه التصفية.',
    'showEvery': 'عرض كل الإشعارات',
    'denied': 'لا يمكنك قراءة الإشعارات في مساحة العمل هذه: {message}',
    'error': 'تعذر تحميل الإشعارات: {message}',
    'kindsError': 'تعذر تحميل تصفية الأنواع: {message}',
    'moreFor': 'إجراءات أخرى لـ {title}',
    'markRead': 'تعليم كمقروء',
    'markUnread': 'تعليم كغير مقروء',
    'delete': 'حذف',
    'deleteTitle': 'حذف هذا الإشعار؟',
    'deleteText':
        'سيُحذف "{title}" نهائيًا. تبقى المهمة أو السجل الذي يشير إليه كما هو.',
    'cancel': 'إلغاء',
    'deleted': 'حُذف الإشعار.',
    'actionFailed': 'لم ينجح ذلك: {message}',
    'unreadMark': 'غير مقروء',
    'rowSemantics': '{state}. {kind}. {title}. {body}. {when}',
    'stateRead': 'مقروء',
    'stateUnread': 'غير مقروء',
    'kind.other': 'أخرى',
    'kind.work_assigned': 'أُسند إليك',
    'kind.work_comment': 'تعليقات',
    'kind.work_completed': 'عمل مكتمل',
    'kind.work_blocked': 'عمل متوقف',
    'kind.work_follow_up': 'متابعات',
    'kind.work_overdue': 'عمل متأخر',
    'kind.calendar_reminder': 'تذكيرات التقويم',
    'kind.approval_reminder': 'تذكيرات الموافقات',
    'kind.system_health': 'صحة النظام',
    'kind.system_restart': 'إعادة تشغيل غير متوقعة',
    'kind.budget_alert': 'ميزانية النموذج',
    'kind.agent_controls': 'ضوابط الوكلاء',
    'kind.skill_signing': 'توقيع المهارات',
    'kind.document_expiry': 'انتهاء المستندات',
    'kind.vehicle_reminder': 'تذكيرات المركبات',
    'kind.purchase_reminder': 'تذكيرات المشتريات',
    'kind.attendance_missed_check': 'الحضور',
    'kind.visitor_arrived': 'الزوار',
    'kind.missed_call': 'مكالمات فائتة',
    'kind.finance_daily_report': 'تقرير التداول',
    'kind.finance_weekly_advice': 'مستشار المال',
    'kind.finance_loan_due': 'أقساط مستحقة',
    'kind.finance_suggestions': 'اقتراحات مالية',
    'kind.finance_exceptions': 'استثناءات مالية',
    'kind.finance_sms': 'رسائل البنك',
  };
}
