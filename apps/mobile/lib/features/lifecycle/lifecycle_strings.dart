import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of Settings > Data export and deletion
/// (docs/organisation-lifecycle.md). The terms follow the web catalog
/// (apps/web/src/lib/messages/lifecycle.*.ts). Both maps hold the same keys
/// (test/lifecycle_test.dart checks it).
class LifecycleStrings {
  const LifecycleStrings(this.arabic);
  final bool arabic;

  static const english = LifecycleStrings(false);

  static LifecycleStrings of(BuildContext context) =>
      LifecycleStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? ar : en)[key] ?? en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// "3 days", "يومان", "5 ساعات": the unit word with its plural form.
  String count(String unit, int n) {
    if (!arabic) return t('$unit.${n == 1 ? 'one' : 'other'}', {'n': n});
    final form = AnumLocalizations.arabicPluralForm(n);
    return t('$unit.${form == 'zero' ? 'other' : form}', {'n': n});
  }

  static const en = <String, String>{
    'title': 'Data export and deletion',
    'loading': 'Loading data export and deletion',
    'permissionTitle': 'Permission required',
    'offlineTitle': 'You are offline',
    'offlineText': 'Reconnect to see exports and deletions.',
    'unavailable': 'Data export and deletion unavailable',
    'retry': 'Retry',
    'lead':
        "Take all of your organisation's data out of ANUM, or leave ANUM for good. Your own personal data is under Settings, Privacy.",
    'exportHeading': 'Export the organisation',
    'exportText':
        'One zip file with every row of every workspace as JSON lines and CSV, plus every workspace file. Secrets, sign-in accounts and rows private to one person are left out; each person downloads their own from Privacy. An archive is kept for {retention} and a download link works for {ttl}.',
    'requesting': 'Requesting...',
    'requestExport': 'Request export',
    'exportInProgress':
        'An export is being prepared. This page checks again every few seconds.',
    'exportBlocked':
        'New exports cannot start while the organisation is scheduled for deletion. Cancel the deletion first.',
    'noExports':
        'No exports yet. Request one to download everything your organisation keeps in ANUM.',
    'orgHeading': 'Delete the organisation',
    'orgText':
        'Deletes every workspace, member, file and record of this organisation. Nothing is deleted for {period} after the request, and it can be cancelled until then. Export the organisation first if you need a copy.',
    'scheduling': 'Scheduling...',
    'deleteOrg': 'Delete organisation',
    'deleteOrgTitle': 'Delete the organisation?',
    'deleteOrgWhat':
        'every workspace, member, file and record of this organisation',
    'deleteOrgField': 'Type the organisation id to confirm',
    'deleteOrgConfirm': 'Schedule organisation deletion',
    'cancelOrgTitle': 'Cancel deleting the organisation?',
    'wsHeading': 'Delete this workspace',
    'wsText':
        "Deletes this workspace's tasks, conversations, files and records. Organisation settings such as branding and policies stay. Nothing is deleted for {period} after the request, and it can be cancelled until then.",
    'deleteWs': 'Delete workspace',
    'deleteWsTitle': 'Delete this workspace?',
    'deleteWsWhat':
        'every task, conversation, file and record of this workspace',
    'deleteWsField': 'Type the workspace name to confirm',
    'deleteWsConfirm': 'Schedule workspace deletion',
    'cancelWsTitle': 'Cancel deleting this workspace?',
    'cancelText':
        'The deletion stops and nothing is removed. Everyone keeps working as before.',
    'keepScheduled': 'Keep it scheduled',
    'cancelDeletion': 'Cancel deletion',
    'openFailedCopied':
        'The browser could not open the download. The link was copied; paste it into a browser before {when}.',
    'linkCopied': 'Download link copied. It works until {when}.',
    'requested': 'Requested {when}',
    'size': 'Size {size}',
    'rows': '{n} rows',
    'files': '{n} files',
    'keptUntil': 'Kept until {when}',
    'archiveDeleted': 'The archive was deleted. Request a new export.',
    'openLink': 'Open download link',
    'copyLink': 'Copy download link',
    'what.organisation': 'organisation',
    'what.workspace': 'workspace',
    'banner.scheduled': 'This {what} will be deleted on {when}',
    'banner.scheduledBody':
        'Requested {when}{by}. Everyone keeps working until then.',
    'banner.by': ' by {who}',
    'banner.askOwner': ' Ask an owner to cancel it if this is a mistake.',
    'banner.purging': 'This {what} is being deleted now',
    'banner.purgingBody':
        'The deletion started {when} and can no longer be cancelled. Members lose access as it runs.',
    'banner.failed': 'Deleting this {what} failed',
    'banner.failedBody':
        '{error} The deletion is retried by the server; contact your platform operator if this stays.',
    'banner.noReason': 'The server did not say why.',
    'dialog.text':
        'This schedules deleting {what}. Nothing is deleted for {period}; until then anyone allowed can cancel it. After that it cannot be undone.',
    'dialog.typeExactly': 'Type exactly:',
    'dialog.mismatch': 'It must match exactly, including capitals.',
    'dialog.keep': 'Keep it',
    'status.queued': 'Waiting to start',
    'status.running': 'Preparing',
    'status.ready': 'Ready to download',
    'status.failed': 'Failed',
    'status.expired': 'Expired',
    'status.scheduled': 'Scheduled',
    'status.cancelled': 'Cancelled',
    'status.purging': 'Deleting now',
    'status.completed': 'Deleted',
    'permission':
        'Only active members of this workspace can see its data export and deletion. Ask a workspace owner to restore your membership, or switch to a workspace you belong to.',
    'orgRights':
        'Only an organisation administrator who is also an owner of this workspace can export the organisation or schedule its deletion.',
    'wsRights':
        'Only an owner of this workspace can schedule or cancel its deletion.',
    'done.export':
        'Export requested. It is prepared in the background; this page updates when it is ready.',
    'done.link': 'Download link ready. It works until {when}.',
    'done.orgScheduled':
        'Organisation deletion scheduled for {when}. You can cancel it until then.',
    'done.orgCancelled':
        'Organisation deletion cancelled. Nothing was deleted.',
    'done.wsScheduled':
        'Workspace deletion scheduled for {when}. You can cancel it until then.',
    'done.wsCancelled': 'Workspace deletion cancelled. Nothing was deleted.',
    'error.signIn': 'Your sign-in expired. Sign in again, then retry.',
    'error.reloaded':
        '{message} The page was reloaded to show the current state.',
    'error.expired':
        'This export has expired and its archive was deleted. Request a new export.',
    'error.nothingScheduled': '{message} Nothing was scheduled.',
    'error.offline': 'You are offline. Reconnect, then try again.',
    'error.load': 'Could not load data export and deletion',
    'error.export': 'Could not request the export',
    'error.link': 'Could not get a download link',
    'error.organisationDeletion': 'Could not change the organisation deletion',
    'error.workspaceDeletion': 'Could not change the workspace deletion',
    'error.generic': '{what}: {reason}. Check your connection and try again.',
    'day.one': '{n} day',
    'day.two': '{n} days',
    'day.few': '{n} days',
    'day.many': '{n} days',
    'day.other': '{n} days',
    'hour.one': '{n} hour',
    'hour.two': '{n} hours',
    'hour.few': '{n} hours',
    'hour.many': '{n} hours',
    'hour.other': '{n} hours',
    'minute.one': '{n} minute',
    'minute.two': '{n} minutes',
    'minute.few': '{n} minutes',
    'minute.many': '{n} minutes',
    'minute.other': '{n} minutes',
    'months': 'Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec',
    'tile.title': 'Data export and deletion',
    'tile.subtitle':
        'Export the whole organisation, or delete it or this workspace.',
  };

  static const ar = <String, String>{
    'title': 'تصدير البيانات وحذفها',
    'loading': 'جارٍ تحميل تصدير البيانات وحذفها',
    'permissionTitle': 'الإذن مطلوب',
    'offlineTitle': 'أنت غير متصل',
    'offlineText': 'أعد الاتصال لترى عمليات التصدير والحذف.',
    'unavailable': 'تصدير البيانات وحذفها غير متاح',
    'retry': 'إعادة المحاولة',
    'lead':
        'أخرج كل بيانات مؤسستك من ANUM، أو غادر ANUM نهائيًا. بياناتك الشخصية في الإعدادات، الخصوصية.',
    'exportHeading': 'تصدير المؤسسة',
    'exportText':
        'ملف zip واحد يضم كل صف من كل مساحة عمل بصيغتي JSON lines وCSV، مع كل ملفات مساحات العمل. لا تُضمَّن الأسرار وحسابات الدخول والصفوف الخاصة بشخص واحد؛ يحمّل كل شخص بياناته من الخصوصية. يُحتفظ بالأرشيف مدة {retention} ويعمل رابط التنزيل مدة {ttl}.',
    'requesting': 'جارٍ الطلب...',
    'requestExport': 'طلب تصدير',
    'exportInProgress':
        'يجري تجهيز تصدير. تتحقق هذه الصفحة مرة أخرى كل بضع ثوانٍ.',
    'exportBlocked':
        'لا يمكن بدء تصدير جديد ما دام حذف المؤسسة مجدولًا. ألغِ الحذف أولًا.',
    'noExports':
        'لا توجد عمليات تصدير بعد. اطلب واحدة لتنزيل كل ما تحفظه مؤسستك في ANUM.',
    'orgHeading': 'حذف المؤسسة',
    'orgText':
        'يحذف كل مساحة عمل وعضو وملف وسجل في هذه المؤسسة. لا يُحذف شيء خلال {period} بعد الطلب، ويمكن إلغاؤه حتى ذلك الحين. صدّر المؤسسة أولًا إن كنت تحتاج إلى نسخة.',
    'scheduling': 'جارٍ الجدولة...',
    'deleteOrg': 'حذف المؤسسة',
    'deleteOrgTitle': 'هل تريد حذف المؤسسة؟',
    'deleteOrgWhat': 'كل مساحة عمل وعضو وملف وسجل في هذه المؤسسة',
    'deleteOrgField': 'اكتب معرّف المؤسسة للتأكيد',
    'deleteOrgConfirm': 'جدولة حذف المؤسسة',
    'cancelOrgTitle': 'هل تريد إلغاء حذف المؤسسة؟',
    'wsHeading': 'حذف مساحة العمل هذه',
    'wsText':
        'يحذف مهام مساحة العمل هذه ومحادثاتها وملفاتها وسجلاتها. تبقى إعدادات المؤسسة مثل الهوية والسياسات. لا يُحذف شيء خلال {period} بعد الطلب، ويمكن إلغاؤه حتى ذلك الحين.',
    'deleteWs': 'حذف مساحة العمل',
    'deleteWsTitle': 'هل تريد حذف مساحة العمل هذه؟',
    'deleteWsWhat': 'كل مهمة ومحادثة وملف وسجل في مساحة العمل هذه',
    'deleteWsField': 'اكتب اسم مساحة العمل للتأكيد',
    'deleteWsConfirm': 'جدولة حذف مساحة العمل',
    'cancelWsTitle': 'هل تريد إلغاء حذف مساحة العمل هذه؟',
    'cancelText': 'يتوقف الحذف ولا يُزال شيء. يواصل الجميع العمل كما كانوا.',
    'keepScheduled': 'إبقاؤه مجدولًا',
    'cancelDeletion': 'إلغاء الحذف',
    'openFailedCopied':
        'تعذر على المتصفح فتح التنزيل. نُسخ الرابط؛ الصقه في متصفح قبل {when}.',
    'linkCopied': 'نُسخ رابط التنزيل. يعمل حتى {when}.',
    'requested': 'طُلب في {when}',
    'size': 'الحجم {size}',
    'rows': '{n} صف',
    'files': '{n} ملف',
    'keptUntil': 'محفوظ حتى {when}',
    'archiveDeleted': 'حُذف الأرشيف. اطلب تصديرًا جديدًا.',
    'openLink': 'فتح رابط التنزيل',
    'copyLink': 'نسخ رابط التنزيل',
    'what.organisation': 'المؤسسة',
    'what.workspace': 'مساحة العمل',
    'banner.scheduled': 'ستُحذف هذه {what} في {when}',
    'banner.scheduledBody':
        'طُلب في {when}{by}. يواصل الجميع العمل حتى ذلك الحين.',
    'banner.by': ' بواسطة {who}',
    'banner.askOwner': ' اطلب من أحد المالكين إلغاءه إن كان خطأ.',
    'banner.purging': 'تُحذف هذه {what} الآن',
    'banner.purgingBody':
        'بدأ الحذف في {when} ولم يعد إلغاؤه ممكنًا. يفقد الأعضاء الوصول أثناء تنفيذه.',
    'banner.failed': 'فشل حذف هذه {what}',
    'banner.failedBody':
        '{error} يعيد الخادم محاولة الحذف؛ تواصل مع مشغّل المنصة إن استمر ذلك.',
    'banner.noReason': 'لم يذكر الخادم السبب.',
    'dialog.text':
        'يجدول هذا حذف {what}. لا يُحذف شيء خلال {period}؛ ويمكن لأي شخص مخوّل إلغاؤه حتى ذلك الحين. بعدها لا يمكن التراجع عنه.',
    'dialog.typeExactly': 'اكتب تمامًا:',
    'dialog.mismatch': 'يجب أن يطابق تمامًا، بما في ذلك الأحرف الكبيرة.',
    'dialog.keep': 'إبقاؤه',
    'status.queued': 'في انتظار البدء',
    'status.running': 'قيد التجهيز',
    'status.ready': 'جاهز للتنزيل',
    'status.failed': 'فشل',
    'status.expired': 'منتهي الصلاحية',
    'status.scheduled': 'مجدول',
    'status.cancelled': 'ملغى',
    'status.purging': 'يُحذف الآن',
    'status.completed': 'محذوف',
    'permission':
        'لا يرى تصدير البيانات وحذفها إلا الأعضاء النشطون في مساحة العمل هذه. اطلب من أحد مالكي مساحة العمل استعادة عضويتك، أو انتقل إلى مساحة عمل تنتمي إليها.',
    'orgRights':
        'لا يستطيع تصدير المؤسسة أو جدولة حذفها إلا مسؤول المؤسسة الذي يكون أيضًا مالكًا لمساحة العمل هذه.',
    'wsRights':
        'لا يستطيع جدولة حذف مساحة العمل هذه أو إلغاءه إلا أحد مالكيها.',
    'done.export':
        'طُلب التصدير. يُجهَّز في الخلفية؛ وتتحدث هذه الصفحة عندما يجهز.',
    'done.link': 'رابط التنزيل جاهز. يعمل حتى {when}.',
    'done.orgScheduled':
        'جُدول حذف المؤسسة في {when}. يمكنك إلغاؤه حتى ذلك الحين.',
    'done.orgCancelled': 'أُلغي حذف المؤسسة. لم يُحذف شيء.',
    'done.wsScheduled':
        'جُدول حذف مساحة العمل في {when}. يمكنك إلغاؤه حتى ذلك الحين.',
    'done.wsCancelled': 'أُلغي حذف مساحة العمل. لم يُحذف شيء.',
    'error.signIn': 'انتهت جلسة الدخول. سجّل الدخول مرة أخرى، ثم أعد المحاولة.',
    'error.reloaded': '{message} أُعيد تحميل الصفحة لتعرض الحالة الحالية.',
    'error.expired':
        'انتهت صلاحية هذا التصدير وحُذف أرشيفه. اطلب تصديرًا جديدًا.',
    'error.nothingScheduled': '{message} لم تُجدول أي عملية.',
    'error.offline': 'أنت غير متصل. أعد الاتصال، ثم حاول مرة أخرى.',
    'error.load': 'تعذر تحميل تصدير البيانات وحذفها',
    'error.export': 'تعذر طلب التصدير',
    'error.link': 'تعذر الحصول على رابط التنزيل',
    'error.organisationDeletion': 'تعذر تغيير حذف المؤسسة',
    'error.workspaceDeletion': 'تعذر تغيير حذف مساحة العمل',
    'error.generic': '{what}: {reason}. تحقق من اتصالك وحاول مرة أخرى.',
    'day.one': 'يوم واحد',
    'day.two': 'يومان',
    'day.few': '{n} أيام',
    'day.many': '{n} يومًا',
    'day.other': '{n} يوم',
    'hour.one': 'ساعة واحدة',
    'hour.two': 'ساعتان',
    'hour.few': '{n} ساعات',
    'hour.many': '{n} ساعة',
    'hour.other': '{n} ساعة',
    'minute.one': 'دقيقة واحدة',
    'minute.two': 'دقيقتان',
    'minute.few': '{n} دقائق',
    'minute.many': '{n} دقيقة',
    'minute.other': '{n} دقيقة',
    'months':
        'يناير,فبراير,مارس,أبريل,مايو,يونيو,يوليو,أغسطس,سبتمبر,أكتوبر,نوفمبر,ديسمبر',
    'tile.title': 'تصدير البيانات وحذفها',
    'tile.subtitle': 'صدّر المؤسسة كاملة، أو احذفها أو احذف مساحة العمل هذه.',
  };
}
