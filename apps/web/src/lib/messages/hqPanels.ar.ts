// Arabic strings for the panels beside the headquarters scene: the same keys and placeholders as hqPanels.en.ts.
import type { hqPanelsEn } from './hqPanels.en.ts';
import type { Message } from './types.ts';

export const hqPanelsAr: Record<keyof typeof hqPanelsEn, Message> = {
  'hqPanels.stale': 'قديم',
  'hqPanels.staleHint': 'آخر تأكيد لهذه البيانات كان منذ مدة. اعتبرها قديمة حتى تتحدث.',
  'hqPanels.updated': 'آخر تحديث {time}',
  'hqPanels.verified': 'تم التحقق {time}',
  'hqPanels.fold': 'طي {name}',
  'hqPanels.open': 'فتح {name}',
  'hqPanels.moves.purpose': 'انتقل إلى مكان أو افتح أداة.',
  'hqPanels.plan.purpose': 'غرف هذا الطابق. اختر غرفة لفتحها.',
  'hqPanels.people.purpose': 'من يجلس أين، كما يبلغ عنه المكتب.',
  'hqPanels.status.purpose': 'الوكلاء العاملون أو في اجتماعات أو الخاملون في كل الحرم.',
  'hqPanels.status.loading': 'جارٍ قراءة الحرم.',
  'hqPanels.status.failed': 'تعذرت قراءة الحرم: {reason}. اضغط حاول مرة أخرى.',
  'hqPanels.status.empty': 'لا يوجد وكيل يبلغ عن هذا الحرم بعد.',
  'hqPanels.status.emptyAction': 'أضف وكيلاً',
  'hqPanels.status.stale': 'آخر تحقق في {time}. اعتبر هذه الأرقام قديمة.',
  'hqPanels.denied': 'لا يمكنك قراءة الحرم. اطلب من مالك مساحة العمل دور عضو أو مشاهد.',
  'hqPanels.map.show': 'إظهار خريطة الحرم',
  'hqPanels.map.hide': 'إخفاء خريطة الحرم',
  'hqPanels.flow.polling': 'تحقق كل 8 ثوانٍ',
  'hqPanels.name.office': 'المكتب',
  'hqPanels.name.flow': 'سير العمل المباشر',
  'hqPanels.say.live': '{name} مباشر.',
  'hqPanels.say.reconnecting': '{name} فقد الاتصال المباشر ويحاول مجدداً.',
  'hqPanels.say.polling': '{name} يتحقق الآن بمؤقت.',
  'hqPanels.say.connecting': '{name} يتصل.',
  'hqPanels.say.offline': '{name} غير متصل.',
  'hqPanels.say.denied': '{name} غير متاح لدورك.',
};
