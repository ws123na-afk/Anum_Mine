// All inboxes (Arabic). Same keys and placeholders as mailAll.en.ts; the type makes a missing key a
// build error.
import type { mailAllEn } from './mailAll.en.ts';
import type { Message } from './types.ts';

export const mailAllAr: Record<keyof typeof mailAllEn, Message> = {
  'maila.option': 'كل صناديق الوارد',
  'maila.title': 'كل صناديق الوارد',
  'maila.loading': 'جار تحميل كل صناديق الوارد...',
  'maila.shown': '{shown} من {total}',
  'maila.results': { zero: 'لا توجد رسائل مطابقة.', one: 'رسالة واحدة مطابقة.', two: 'رسالتان مطابقتان.', few: '{count} رسائل مطابقة.', many: '{count} رسالة مطابقة.', other: '{count} رسالة مطابقة.' },
  'maila.filters': 'عوامل التصفية',
  'maila.mailbox': 'صندوق البريد',
  'maila.mailboxAll': 'كل صناديق البريد',
  'maila.since': 'من',
  'maila.until': 'إلى',
  'maila.badRange': 'تاريخ البداية بعد تاريخ النهاية. اختر بداية أبكر أو نهاية أبعد.',
  'maila.clear': 'مسح البحث وعوامل التصفية',
  'maila.empty.title': 'لا شيء في أي صندوق وارد',
  'maila.empty.text': 'يظهر هنا البريد الجديد في كل صناديق بريدك، الأحدث أولا.',
  'maila.empty.check': 'تحقق مرة أخرى',
  'maila.noMatch.title': 'لا شيء مطابق في صناديق الوارد',
  'maila.noMatch.text': 'جرب كلمات أخرى أو نطاق تواريخ أوسع أو كل صناديق البريد.',
  'maila.failed': 'تعذر تحميل صناديق الوارد: {reason}',
  'maila.denied.title': 'دورك لا يسمح بقراءة البريد',
  'maila.denied.text': 'يقرأ المالكون والأعضاء في مساحة العمل هذه البريد. اطلب من مالك تغيير دورك.',
  'maila.more': 'تحميل المزيد',
  'maila.moreFailed': 'تعذر تحميل الرسائل التالية: {reason}',
  'maila.unreadable': 'تعذرت قراءة {address}: {reason}',
  'maila.retry': 'حاول مرة أخرى',
  'maila.badge': 'صندوق البريد: {mailbox}',
  'maila.badgeShared': 'صندوق البريد: {mailbox} (مشارك معك)',
  'maila.reader.empty': 'اختر رسالة لقراءتها في صندوق بريدها',
  'maila.reader.text': 'تكتب الردود في صندوق بريد الرسالة وترسل من عنوانه بعد موافقة مالك.',
  'maila.openMailbox': 'فتح في {mailbox}',
  'maila.gone': 'لم يعد صندوق البريد هذا متاحا لك. حمل القائمة مرة أخرى.',
  'maila.readOnly': 'صندوق البريد هذا مشارك معك للقراءة فقط. اطلب من مالكه السماح لك بالإرسال منه.',
};
