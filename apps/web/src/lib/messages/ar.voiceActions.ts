// Arabic messages for voice action cards. Same keys and placeholders as en.voiceActions.ts;
// the type makes a missing key a build error (docs/web-localization.md).
import type { voiceActionsEn } from './en.voiceActions.ts';
import type { Message } from './types.ts';

export const voiceActionsAr: Record<keyof typeof voiceActionsEn, Message> = {
  'voice.tier.action': 'نُفّذ عبر ANUM',
  'voiceAction.label': 'ما قام به {name}',
  'voiceAction.kind.create_task': 'مهمة جديدة',
  'voiceAction.kind.assign': 'عمل مُسند',
  'voiceAction.kind.run_task': 'بدأ تشغيل المهمة',
  'voiceAction.kind.cancel_task': 'أُلغيت المهمة',
  'voiceAction.kind.task_status': 'حالة المهمة',
  'voiceAction.kind.run_quick_command': 'أمر سريع',
  'voiceAction.state.running': 'قيد التشغيل',
  'voiceAction.state.completed': 'اكتملت',
  'voiceAction.state.approval': 'بانتظار الموافقة',
  'voiceAction.state.failed': 'فشلت',
  'voiceAction.state.cancelled': 'أُلغيت',
  'voiceAction.state.created': 'محفوظة ولم تبدأ',
  'voiceAction.state.status': 'الحالة',
  'voiceAction.state.not_found': 'غير موجودة',
  'voiceAction.state.ambiguous': 'أيها تقصد؟',
  'voiceAction.state.denied': 'غير مسموح لدورك',
  'voiceAction.state.needs_detail': 'تحتاج إلى تفاصيل',
  'voiceAction.agent': 'الوكيل: {name}',
  'voiceAction.steps': { zero: '{count} خطوة', one: 'خطوة واحدة', two: 'خطوتان', few: '{count} خطوات', many: '{count} خطوة', other: '{count} خطوة' },
  'voiceAction.approvalsPending': { zero: 'لا موافقات بانتظارك', one: 'موافقة واحدة بانتظارك', two: 'موافقتان بانتظارك', few: '{count} موافقات بانتظارك', many: '{count} موافقة بانتظارك', other: '{count} موافقة بانتظارك' },
  'voiceAction.approvalNote': 'وافق عليها أو ارفضها من الموافقات. الصوت لا يقرر أي موافقة.',
  'voiceAction.openTask': 'افتح المهمة',
  'voiceAction.openApprovals': 'افتح الموافقات',
  'voiceAction.candidates': 'المطابقات',
};
