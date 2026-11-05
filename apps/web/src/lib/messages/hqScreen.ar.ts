// شاشات المقر الحية (docs/ANUM_MINE_HQ_LIVE_SCREENS.md). يجب أن تطابق مفاتيح hqScreen.en.ts وعناصرها النائبة.
// كل جملة تعرضها شاشة ثلاثية الأبعاد هي من هذه الجمل أو تسمية موجودة في الفهرس أو رقم أو وقت من سجل حقيقي.
import type { Message } from './types.ts';

export const hqScreenAr = {
  'hqScreen.live': 'مباشر',
  'hqScreen.stale': 'قديم',
  'hqScreen.notConnected': 'غير متصل',
  'hqScreen.updated': 'تم التحديث {time}',
  'hqScreen.verified': 'تم التحقق {time}',
  'hqScreen.offShift': 'خارج الدوام',
  'hqScreen.vacant': 'لا أحد هنا',
  'hqScreen.record.task': 'تشغيل مهمة',
  'hqScreen.record.queued': 'مهمة في الانتظار',
  'hqScreen.record.meeting': 'اجتماع',
  'hqScreen.record.approval': 'موافقة',
  'hqScreen.running': 'يعمل منذ {time}',
  'hqScreen.queuedFor': 'في الانتظار منذ {time}',
  'hqScreen.waitingFor': 'ينتظر منذ {time}',
  'hqScreen.flowTitle': 'آخر حركة العمل',
  'hqScreen.flowNone': 'لا شيء يتحرك الآن.',
  'hqScreen.flowRow': '{kind} · {stage} · {time}',
  'hqScreen.flowRowNoAge': '{kind} · {stage}',
  'hqScreen.tyreOff': '{name}: غير متصل',
  'hqScreen.tyreNoAccess': '{name}: لا صلاحية',
  'hqScreen.dur.s': '{n}ث',
  'hqScreen.dur.m': '{n}د',
  'hqScreen.dur.h': '{h}س {m}د',
  'hqScreen.dur.d': '{d}ي {h}س',
  'hqScreen.wallAria': 'شاشة جدار العمليات',
} as const satisfies Record<string, Message>;
