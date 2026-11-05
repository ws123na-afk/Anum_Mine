// How long records are kept (owner decision O5, docs/record-retention.md), Arabic. Same keys and placeholders as
// recordRetention.en.ts.
import type { recordRetentionEn } from './recordRetention.en.ts';
import type { Message } from './types.ts';

export const recordRetentionAr: Record<keyof typeof recordRetentionEn, Message> = {
  'recordRetention.title': 'مدة الاحتفاظ بالسجلات',
  'recordRetention.lead': 'يحذف ANUM هذه السجلات تلقائيًا بعد المدد أدناه. تسري المدد المعتادة إلى أن يغيّرها مالك.',
  'recordRetention.loading': 'جارٍ قراءة مدة الاحتفاظ بالسجلات...',
  'recordRetention.error': 'تعذّرت قراءة مدة الاحتفاظ بالسجلات: {message} تحقّق من اتصالك ثم حاول مرة أخرى.',
  'recordRetention.denied': 'لا يمكنك رؤية هذا الإعداد: {message} اطلب من مالك مساحة العمل أن يضيفك إليها.',
  'recordRetention.retry': 'حاول مرة أخرى',
  'recordRetention.days': { zero: '{count} يوم', one: 'يوم واحد', two: 'يومان', few: '{count} أيام', many: '{count} يومًا', other: '{count} يوم' },
  'recordRetention.badge.standard': 'المدة المعتادة',
  'recordRetention.badge.changed': 'حدّدها مالك',
  'recordRetention.transcripts.title': 'نصوص الاجتماعات',
  'recordRetention.transcripts.text': 'تُحذف رسائل الاجتماع وسجل أحداثه بعد {period} من انتهائه. يبقى الاجتماع وملخصه والقرارات المؤكَّدة وروابطها والمهام.',
  'recordRetention.visitors.title': 'سجلات الزوار',
  'recordRetention.visitors.text': 'تُحذف الزيارة بعد {period} من آخر وقت فيها: تسجيل الخروج، أو الإلغاء، أو الموعد المخطط لزائر لم يحضر.',
  'recordRetention.snapshots': 'لقطات سجل المكتب: لا يُخزَّن منها شيء، فلا يوجد ما يُحذف.',
  'recordRetention.standardIs': 'المدة المعتادة: {period}.',
  'recordRetention.daysLabel': 'أيام الاحتفاظ، من {min} إلى {max}',
  'recordRetention.save': 'حفظ',
  'recordRetention.useStandard': 'استخدم المدة المعتادة ({period})',
  'recordRetention.saved': 'حُفظ. يستخدم التنظيف اليومي التالي مدة {period}.',
  'recordRetention.savedStandard': 'حُفظ. عادت المدة المعتادة {period}.',
  'recordRetention.invalid.empty': 'أدخل عدد الأيام.',
  'recordRetention.invalid.notWhole': 'أدخل أيامًا كاملة، مثل 90.',
  'recordRetention.invalid.range': 'اختر من {min} إلى {max} يومًا.',
  'recordRetention.stale': 'غيّر أحدهم هذا الإعداد قبل لحظات. تظهر المدد الحالية: راجعها ثم احفظ مرة أخرى.',
  'recordRetention.ownerOnly': 'لا يغيّر مدة الاحتفاظ بالسجلات إلا مالك مساحة العمل.',
  'recordRetention.isDefault': 'لم يغيّر أحد هذه المدد.',
  'recordRetention.lastChanged': 'آخر تغيير بواسطة {user}، {when}.',
  'recordRetention.daily': 'يعمل التنظيف مرة كل يوم، لذا قد يبقى السجل حتى يوم واحد بعد مدته.',
};
