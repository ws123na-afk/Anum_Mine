// Arabic floor overview texts: the same keys and placeholders as hqFloor.en.ts.
import type { hqFloorEn } from './hqFloor.en.ts';
import type { Message } from './types.ts';

export const hqFloorAr: Record<keyof typeof hqFloorEn, Message> = {
  'hqFloor.short': 'نظرة على الطابق',
  'hqFloor.enter': 'عرض الطابق كله من الأعلى مع اسم كل غرفة',
  'hqFloor.leave': 'مغادرة نظرة الطابق',
  'hqFloor.stats': 'المكاتب {desks}، الوكلاء {agents}',
  'hqFloor.listTitle': 'الغرف في {floor}',
  'hqFloor.summary': 'الغرف {rooms}، المكاتب {desks}، الوكلاء الجالسون {agents}.',
  'hqFloor.back': 'العودة إلى عرض المبنى',
  'hqFloor.rail': 'الطابق كله',
  'hqFloor.railHint': 'عرض الطابق المفتوح كله من الأعلى مع اسم كل غرفة',
};
