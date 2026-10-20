// Approvals list: filter, search and paging (Arabic). Same keys and placeholders as approvalPaging.en.ts;
// the type makes a missing key a build error.
import type { approvalPagingEn } from './approvalPaging.en.ts';
import type { Message } from './types.ts';

export const approvalPagingAr: Record<keyof typeof approvalPagingEn, Message> = {
  'approvals.filter.label': 'عرض',
  'approvals.filter.all': 'الكل',
  'approvals.filter.pending': 'معلّقة',
  'approvals.filter.decided': 'السجل',
  'approvals.search.label': 'بحث في الموافقات',
  'approvals.search.placeholder': 'الأداة أو السبب أو المهمة أو الوجهة',
  'approvals.loading': 'جار تحميل الموافقات...',
  'approvals.error.title': 'تعذّر تحميل الموافقات',
  'approvals.error.text': 'تعذّر تحميل الموافقات: {reason}',
  'approvals.denied.title': 'لا يمكنك الاطلاع على الموافقات',
  'approvals.denied.text': 'الاطلاع على الموافقات يحتاج إلى صلاحية في مساحة العمل هذه. اطلب من المالك أو المسؤول تغيير دورك.',
  'approvals.empty.searchTitle': 'لا توجد موافقات مطابقة لبحثك',
  'approvals.empty.searchText': 'لا شيء يطابق "{query}". جرّب اسم أداة أو جزءا من سبب أو عنوان مهمة.',
  'approvals.empty.decidedTitle': 'لا توجد قرارات بعد',
  'approvals.empty.decidedText': 'تظهر هنا الطلبات الموافق عليها والمرفوضة والمنتهية بعد أن يقرر أحد.',
  'approvals.loadMore': 'تحميل المزيد من الموافقات',
  'approvals.loadMoreError': 'تعذّر تحميل المزيد من الموافقات: {reason}',
  'approvals.shown': 'المعروض {shown} من {total}',
  'approvals.shownUnknown': 'المعروض {shown}',
};
