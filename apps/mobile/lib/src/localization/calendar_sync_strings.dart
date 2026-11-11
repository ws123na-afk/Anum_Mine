/// Calendar sync strings (docs/calendar.md#sync), English and Arabic.
/// Looked up by `AnumLocalizations.t` after the main catalogs.
const calendarSyncEn = <String, String>{
  'calsync.none':
      'Events live in ANUM. An owner connects Google or Microsoft 365 on the web under Calendar, Sync.',
  'calsync.on': 'Synced with {provider} ({email}). {when}',
  'calsync.when.never': 'Never synced.',
  'calsync.when.now': 'Synced just now.',
  'calsync.when.minutes': 'Synced {count} min ago.',
  'calsync.when.hours': 'Synced {count} h ago.',
  'calsync.when.days': 'Synced {count} d ago.',
  'calsync.problem': '{provider} sync needs attention',
  'calsync.error': 'What went wrong: {reason}',
  'calsync.fix.reauth_required':
      "{provider} no longer accepts ANUM's access. An owner connects it again on the web under Calendar, Sync.",
  'calsync.fix.error':
      'The last sync failed. Try Sync now; if it keeps failing, an owner reconnects {provider} on the web.',
  'calsync.now': 'Sync now',
  'calsync.syncing': 'Syncing…',
  'calsync.synced': 'Synced {email}.',
  'calsync.state.synced': 'Synced with {provider}',
  'calsync.state.waiting': 'Waiting to be sent to {provider}',
  'calsync.state.local': 'Only in ANUM',
  'calsync.state.failed': 'Not sent to {provider}',
  'calsync.failedText':
      'ANUM could not send this event to {provider} and stopped trying: {reason}',
  'calsync.waitingText':
      'The last send to {provider} failed: {reason} The next sync tries again.',
  'calsync.failedReadOnly':
      'The person who added the event or an owner can send it again.',
  'calsync.retry': 'Send again',
  'calsync.retried': 'Sent to {provider}.',
  'calsync.retryWaiting': 'Not sent yet: {reason}',
  // Email reminders and replies to one occurrence (docs/calendar.md#reminders).
  'calendarEmailReminder': 'Also by email: {label}',
  'calendarEmailRemindersHelp':
      'Email reminders go to the same people through the workspace mail server, only to those whose email notifications are on and who are not marked do not contact by email.',
  'calendarReminderWithEmail': '{label}, also by email',
  'calendarReplyScopeThis': 'This event only',
  'calendarReplyScopeAll': 'All events in the series',
  'calendarReplyThisOnly': 'For this event only',
  'calsync.invite.ask': 'Ask to send invitations',
  'calsync.invite.pending':
      'Invitations have not been approved for this event as it is now. Attendees stay in ANUM until an owner approves.',
  'calsync.invite.done':
      'Invitations were approved for this event and its attendees.',
  'calsync.invite.requested':
      'Waiting for an owner to approve inviting {people}. Nothing has been sent.',
  'calsync.invite.review': 'Review approvals',
};

const calendarSyncAr = <String, String>{
  'calsync.none':
      'الأحداث محفوظة في ANUM. يربط المالك تقويم Google أو Microsoft 365 من الويب في التقويم ثم المزامنة.',
  'calsync.on': 'متزامن مع {provider} ({email}). {when}',
  'calsync.when.never': 'لم تتم المزامنة بعد.',
  'calsync.when.now': 'تمت المزامنة الآن.',
  'calsync.when.minutes': 'تمت المزامنة قبل {count} دقيقة.',
  'calsync.when.hours': 'تمت المزامنة قبل {count} ساعة.',
  'calsync.when.days': 'تمت المزامنة قبل {count} يومًا.',
  'calsync.problem': 'مزامنة {provider} تحتاج إلى انتباه',
  'calsync.error': 'ما الذي حدث: {reason}',
  'calsync.fix.reauth_required':
      'لم يعد {provider} يقبل وصول ANUM. يربطه المالك من جديد على الويب في التقويم ثم المزامنة.',
  'calsync.fix.error':
      'فشلت آخر مزامنة. جرّب زامن الآن؛ وإن استمر الفشل فيعيد المالك ربط {provider} على الويب.',
  'calsync.now': 'زامن الآن',
  'calsync.syncing': 'جارٍ المزامنة…',
  'calsync.synced': 'تمت مزامنة {email}.',
  'calsync.state.synced': 'متزامن مع {provider}',
  'calsync.state.waiting': 'بانتظار إرساله إلى {provider}',
  'calsync.state.local': 'في ANUM فقط',
  'calsync.state.failed': 'لم يُرسل إلى {provider}',
  'calsync.failedText':
      'تعذّر على ANUM إرسال هذا الحدث إلى {provider} وتوقف عن المحاولة: {reason}',
  'calsync.waitingText':
      'فشل آخر إرسال إلى {provider}: {reason} ستعيد المزامنة التالية المحاولة.',
  'calsync.failedReadOnly': 'يستطيع من أضاف الحدث أو المالك إرساله مرة أخرى.',
  'calsync.retry': 'أرسل مرة أخرى',
  'calsync.retried': 'أُرسل إلى {provider}.',
  'calsync.retryWaiting': 'لم يُرسل بعد: {reason}',
  'calendarEmailReminder': 'بالبريد أيضًا: {label}',
  'calendarEmailRemindersHelp':
      'تذكيرات البريد تصل إلى الأشخاص أنفسهم عبر خادم البريد في مساحة العمل، فقط لمن فعّل إشعارات البريد ولمن لم يُطلب عدم مراسلته بالبريد.',
  'calendarReminderWithEmail': '{label}، وبالبريد أيضًا',
  'calendarReplyScopeThis': 'هذا الحدث فقط',
  'calendarReplyScopeAll': 'كل أحداث السلسلة',
  'calendarReplyThisOnly': 'لهذا الحدث فقط',
  'calsync.invite.ask': 'اطلب إرسال الدعوات',
  'calsync.invite.pending':
      'لم تتم الموافقة على الدعوات لهذا الحدث بصيغته الحالية. يبقى الحضور في ANUM إلى أن يوافق المالك.',
  'calsync.invite.done': 'تمت الموافقة على الدعوات لهذا الحدث ولحضوره.',
  'calsync.invite.requested':
      'بانتظار موافقة المالك على دعوة {people}. لم يُرسل شيء.',
  'calsync.invite.review': 'مراجعة الموافقات',
};
