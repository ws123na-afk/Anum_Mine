import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of file versions and the storage screen
/// (docs/files.md#versions). The terms follow the web catalog (`files.ver.*`
/// and `files.stor.*` in apps/web/src/lib/messages/filesVersions.en.ts and
/// .ar.ts). Both maps hold the same keys (test/file_versions_test.dart checks
/// it).
class FileStrings {
  const FileStrings(this.arabic);
  final bool arabic;

  static const english = FileStrings(false);

  static FileStrings of(BuildContext context) =>
      FileStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? ar : en)[key] ?? en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// A message with plural forms (`key.one` ... `key.other`; Arabic uses the
  /// CLDR forms), `{n}` filled in.
  String count(String key, int n, [Map<String, Object> values = const {}]) {
    final form = arabic
        ? AnumLocalizations.arabicPluralForm(n)
        : (n == 1 ? 'one' : 'other');
    final plural = '$key.$form';
    final source = arabic ? ar : en;
    final name = source.containsKey(plural) ? plural : '$key.other';
    return t(name, {...values, 'n': n});
  }

  static const en = <String, String>{
    'ver.title': 'Versions',
    'ver.current': 'Current: version {version}',
    'ver.help':
        'A new version keeps the current file as an older version. Download or restore any of them; restoring never loses anything.',
    'ver.upload': 'Upload new version',
    'ver.uploading': 'Uploading...',
    'ver.uploaded': 'Version {version} of {name} is now current.',
    'ver.uploadFailed': 'The new version of {name} was not stored: {message}',
    'ver.cancelled': 'Upload cancelled; nothing was stored.',
    'ver.loading': 'Loading versions',
    'ver.loadFailed': 'Could not load the versions: {message}',
    'ver.denied':
        'Seeing versions needs a viewer, member or owner role in this workspace.',
    'ver.empty': 'No earlier versions',
    'ver.emptyText':
        'Upload a new version and this file becomes version {version} in the list.',
    'ver.emptyReadOnly':
        'When someone uploads a new version, the earlier ones are listed here.',
    'ver.row': 'Version {version}',
    'ver.meta': '{size} · uploaded {uploaded} by {who} · replaced {replaced}',
    'ver.download': 'Download version {version}',
    'ver.downloadFailed':
        'Version {version} could not be downloaded: {message}',
    'ver.restore': 'Restore',
    'ver.restoreOf': 'Restore version {version}',
    'ver.restored':
        'Version {version} is current again, as version {now}. The version it replaced was kept.',
    'ver.restoreFailed': 'Version {version} was not restored: {message}',
    'ver.delete': 'Delete version {version}',
    'ver.confirmTitle': 'Delete version {version}?',
    'ver.confirmText':
        'This frees its space and cannot be undone. The current version is not affected.',
    'ver.confirmDelete': 'Delete',
    'ver.keep': 'Keep',
    'ver.deleted': 'Version {version} deleted.',
    'ver.deleteFailed': 'Version {version} was not deleted: {message}',
    'ver.quarantined':
        'Quarantined: the malware scanner found {signature}, so it cannot be downloaded or restored. Delete it.',
    'ver.unknownSignature': 'malware',
    'ver.manageDenied':
        'Only the person who uploaded this file or a workspace owner can change its versions.',
    'ver.loadMore': 'Show more versions ({shown} of {total})',
    'ver.loadingMore': 'Loading...',
    'ver.loadMoreFailed': 'More versions could not be loaded: {message}',
    'ver.sameType':
        'A new version keeps the file type ({extension}). Choose a file with that extension, or upload it as a new file.',
    'ver.noExtension': 'no extension',
    'ver.empty0': '{name} is empty. Choose a file with content.',
    'ver.tooLarge':
        '{name} is {size}; files can be at most {max}. Split or compress it.',
    'ver.quota':
        '{name} ({size}) does not fit: {left} of the {quota} limit is left, and every version counts. Delete files or older versions first.',
    'you': 'You',
    'detail.version': 'Version',
    'stor.open': 'Storage',
    'stor.title': 'Storage',
    'stor.loading': 'Loading storage',
    'stor.failed': 'Storage could not be loaded: {message}',
    'stor.denied':
        'Seeing storage needs a viewer, member or owner role in this workspace.',
    'stor.retry': 'Try again',
    'stor.used': '{used} of {quota} used',
    'stor.left': '{left} left',
    'stor.versions.one': '{size} of it is {n} older version',
    'stor.versions.other': '{size} of it is {n} older versions',
    'stor.limitNote':
        'The limit is set by the operator for this workspace and counts every version. Identical bytes of one file count once, so restoring a version costs nothing.',
    'stor.nearLimit':
        'This workspace is almost full. Delete files or older versions, or ask the operator to raise the limit.',
    'stor.full':
        'Storage is full: uploads and new versions are refused until you free space.',
    'stor.byKind': 'By kind',
    'stor.largest': 'Largest files',
    'stor.largestHelp':
        'Current size plus older versions. Open a file to delete versions you no longer need.',
    'stor.noVersions': 'No older versions',
    'stor.versionsCell.one': '{n} older version ({size})',
    'stor.versionsCell.other': '{n} older versions ({size})',
    'stor.total': 'Total {size}',
    'stor.openFile': 'Open {name}',
    'stor.empty': 'Nothing is stored yet',
    'stor.emptyText':
        'Uploaded files and their versions appear here with the space they take.',
    'stor.files.one': '{n} file',
    'stor.files.other': '{n} files',
    'kind.text': 'Text',
    'kind.markdown': 'Markdown',
    'kind.data': 'Data (CSV and JSON)',
    'kind.image': 'Images',
    'kind.pdf': 'PDF',
    'kind.document': 'Office documents',
    'kind.archive': 'Archives',
    'kind.video': 'Video',
    'kind.other': 'Other',
  };

  static const ar = <String, String>{
    'ver.title': 'الإصدارات',
    'ver.current': 'الحالي: الإصدار {version}',
    'ver.help':
        'الإصدار الجديد يُبقي الملف الحالي إصدارًا أقدم. نزّل أيًا منها أو استعده؛ والاستعادة لا تفقدك شيئًا.',
    'ver.upload': 'ارفع إصدارًا جديدًا',
    'ver.uploading': 'جارٍ الرفع...',
    'ver.uploaded': 'أصبح الإصدار {version} من {name} هو الحالي.',
    'ver.uploadFailed': 'لم يُحفظ الإصدار الجديد من {name}: {message}',
    'ver.cancelled': 'أُلغي الرفع ولم يُحفظ شيء.',
    'ver.loading': 'جارٍ تحميل الإصدارات',
    'ver.loadFailed': 'تعذّر تحميل الإصدارات: {message}',
    'ver.denied':
        'عرض الإصدارات يحتاج دور مشاهد أو عضو أو مالك في مساحة العمل هذه.',
    'ver.empty': 'لا إصدارات أقدم',
    'ver.emptyText':
        'ارفع إصدارًا جديدًا فيظهر هذا الملف في القائمة بوصفه الإصدار {version}.',
    'ver.emptyReadOnly':
        'حين يرفع أحدهم إصدارًا جديدًا تظهر الإصدارات الأقدم هنا.',
    'ver.row': 'الإصدار {version}',
    'ver.meta': '{size} · رُفع {uploaded} بواسطة {who} · استُبدل {replaced}',
    'ver.download': 'نزّل الإصدار {version}',
    'ver.downloadFailed': 'تعذّر تنزيل الإصدار {version}: {message}',
    'ver.restore': 'استعد',
    'ver.restoreOf': 'استعد الإصدار {version}',
    'ver.restored':
        'عاد الإصدار {version} حاليًا بوصفه الإصدار {now}. وحُفظ الإصدار الذي استُبدل.',
    'ver.restoreFailed': 'لم تتم استعادة الإصدار {version}: {message}',
    'ver.delete': 'احذف الإصدار {version}',
    'ver.confirmTitle': 'تحذف الإصدار {version}؟',
    'ver.confirmText':
        'يوفّر ذلك مساحته ولا يمكن التراجع عنه. ولا يتأثر الإصدار الحالي.',
    'ver.confirmDelete': 'احذف',
    'ver.keep': 'أبقِه',
    'ver.deleted': 'حُذف الإصدار {version}.',
    'ver.deleteFailed': 'لم يُحذف الإصدار {version}: {message}',
    'ver.quarantined':
        'في الحجر: وجد الماسح ضد البرمجيات الخبيثة {signature}، فلا يمكن تنزيله ولا استعادته. احذفه.',
    'ver.unknownSignature': 'برمجية خبيثة',
    'ver.manageDenied': 'يغيّر إصدارات الملف من رفعه أو مالك مساحة العمل فقط.',
    'ver.loadMore': 'اعرض مزيدًا من الإصدارات ({shown} من {total})',
    'ver.loadingMore': 'جارٍ التحميل...',
    'ver.loadMoreFailed': 'تعذّر تحميل مزيد من الإصدارات: {message}',
    'ver.sameType':
        'الإصدار الجديد يحافظ على نوع الملف ({extension}). اختر ملفًا بهذا الامتداد، أو ارفعه ملفًا جديدًا.',
    'ver.noExtension': 'بلا امتداد',
    'ver.empty0': '{name} فارغ. اختر ملفًا له محتوى.',
    'ver.tooLarge':
        'حجم {name} هو {size}؛ والحد الأقصى للملف {max}. قسّمه أو اضغطه.',
    'ver.quota':
        'لا يتسع {name} ({size}): بقي {left} من حد {quota}، وكل إصدار يُحتسب. احذف ملفات أو إصدارات أقدم أولًا.',
    'you': 'أنت',
    'detail.version': 'الإصدار',
    'stor.open': 'التخزين',
    'stor.title': 'التخزين',
    'stor.loading': 'جارٍ تحميل التخزين',
    'stor.failed': 'تعذّر تحميل التخزين: {message}',
    'stor.denied':
        'عرض التخزين يحتاج دور مشاهد أو عضو أو مالك في مساحة العمل هذه.',
    'stor.retry': 'حاول مجددًا',
    'stor.used': 'المستخدم {used} من {quota}',
    'stor.left': 'المتبقي {left}',
    'stor.versions.one': '{size} منه إصدار أقدم واحد',
    'stor.versions.two': '{size} منه إصداران أقدم',
    'stor.versions.few': '{size} منه {n} إصدارات أقدم',
    'stor.versions.many': '{size} منه {n} إصدارًا أقدم',
    'stor.versions.other': '{size} منه {n} إصدار أقدم',
    'stor.limitNote':
        'يحدد المشغّل هذا الحد لمساحة العمل، ويُحتسب فيه كل إصدار. والبايتات المتطابقة للملف الواحد تُحتسب مرة واحدة، فاستعادة إصدار لا تكلّف شيئًا.',
    'stor.nearLimit':
        'مساحة العمل هذه شبه ممتلئة. احذف ملفات أو إصدارات أقدم، أو اطلب من المشغّل رفع الحد.',
    'stor.full':
        'التخزين ممتلئ: يُرفض الرفع والإصدارات الجديدة حتى تحرّر مساحة.',
    'stor.byKind': 'حسب النوع',
    'stor.largest': 'أكبر الملفات',
    'stor.largestHelp':
        'الحجم الحالي مع الإصدارات الأقدم. افتح ملفًا لحذف الإصدارات التي لا تحتاجها.',
    'stor.noVersions': 'لا إصدارات أقدم',
    'stor.versionsCell.one': 'إصدار أقدم واحد ({size})',
    'stor.versionsCell.two': 'إصداران أقدم ({size})',
    'stor.versionsCell.few': '{n} إصدارات أقدم ({size})',
    'stor.versionsCell.many': '{n} إصدارًا أقدم ({size})',
    'stor.versionsCell.other': '{n} إصدار أقدم ({size})',
    'stor.total': 'المجموع {size}',
    'stor.openFile': 'افتح {name}',
    'stor.empty': 'لا شيء مخزّن بعد',
    'stor.emptyText':
        'تظهر هنا الملفات المرفوعة وإصداراتها مع المساحة التي تشغلها.',
    'stor.files.one': 'ملف واحد',
    'stor.files.two': 'ملفان',
    'stor.files.few': '{n} ملفات',
    'stor.files.many': '{n} ملفًا',
    'stor.files.other': '{n} ملف',
    'kind.text': 'نص',
    'kind.markdown': 'Markdown',
    'kind.data': 'بيانات (CSV وJSON)',
    'kind.image': 'صور',
    'kind.pdf': 'PDF',
    'kind.document': 'مستندات Office',
    'kind.archive': 'أرشيفات',
    'kind.video': 'فيديو',
    'kind.other': 'أخرى',
  };
}
