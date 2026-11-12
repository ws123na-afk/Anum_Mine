/// Calendar sync strings (docs/calendar.md#sync), English and Arabic.
/// Looked up by `AnumLocalizations.t` after the main catalogs.
const calendarSyncEn = <String, String>{
  'calsync.none':
      'Events live in ANUM. An owner connects Google or Microsoft 365 under Connections.',
  'calsync.on': 'Synced with {provider} ({email}). {when}',
  'calsync.when.never': 'Never synced.',
  'calsync.when.now': 'Synced just now.',
  'calsync.when.minutes': 'Synced {count} min ago.',
  'calsync.when.hours': 'Synced {count} h ago.',
  'calsync.when.days': 'Synced {count} d ago.',
  'calsync.problem': '{provider} sync needs attention',
  'calsync.error': 'What went wrong: {reason}',
  'calsync.fix.reauth_required':
      "{provider} no longer accepts ANUM's access. An owner connects it again under Connections.",
  'calsync.fix.error':
      'The last sync failed. Try Sync now; if it keeps failing, an owner reconnects {provider} under Connections.',
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
  'calconn.title': 'Calendar connections',
  'calconn.subtitle': 'Google or Microsoft 365 calendars that sync with ANUM.',
  'calconn.open': 'Connections',
  'calconn.loading': 'Loading the calendar connections',
  'calconn.loadFailed': 'The connections could not load',
  'calconn.retry': 'Try again',
  'calconn.deniedTitle': 'You cannot see the calendar',
  'calconn.denied':
      'Seeing the calendar needs a viewer, member or owner role in this workspace.',
  'calconn.ownersOnly':
      'Only workspace owners connect, choose, sync or disconnect calendars. You see their status here.',
  'calconn.connections': 'Connected calendars',
  'calconn.none': 'No calendar connected yet',
  'calconn.noneOwner':
      'Connect Google or Microsoft 365 below. Events then sync both ways.',
  'calconn.noneOther':
      'Events live in ANUM. A workspace owner can connect Google or Microsoft 365.',
  'calconn.providers': 'Calendars you can connect',
  'calconn.notSetUp':
      'Not set up on this server. The operator adds: {settings}',
  'calconn.connect': 'Connect {provider}',
  'calconn.connectAnother': 'Connect another {provider} account',
  'calconn.reconnect': 'Connect {provider} again',
  'calconn.opening': 'Opening the {provider} sign-in in your browser...',
  'calconn.waiting':
      'Finish signing in to {provider} in your browser. ANUM finishes the connection when you come back.',
  'calconn.finishing': 'Finishing the connection...',
  'calconn.finish': 'I have signed in',
  'calconn.cancel': 'Cancel',
  'calconn.browserFailed':
      'The browser could not open. Check that a browser is installed and try again.',
  'calconn.connectFailed': 'Connecting did not finish: {reason}',
  'calconn.connected': 'Connected {email}. Its events are syncing.',
  'calconn.status.connected': 'Connected',
  'calconn.status.error': 'Last sync failed',
  'calconn.status.reauth_required': 'Needs signing in again',
  'calconn.events': '{count} events synced',
  'calconn.also': 'Also synced, read only: {names}',
  'calconn.readFailed': 'Some chosen calendars could not be read: {reasons}',
  'calconn.fix.reauth_required':
      "{provider} no longer accepts ANUM's access. An owner presses Connect {provider} again and signs in; events already synced stay.",
  'calconn.fix.error':
      'The last sync failed. Try Sync now; if it keeps failing, disconnect and connect {provider} again.',
  'calconn.choose': 'Choose calendars',
  'calconn.chooseTitle': 'Calendars of {email}',
  'calconn.chooseLead':
      'The primary calendar always syncs both ways. Other calendars you tick are read into ANUM and kept up to date; ANUM never writes to them.',
  'calconn.listing': 'Asking {provider} for the calendars of this account',
  'calconn.listFailed': 'The calendars could not be listed',
  'calconn.listEmpty': 'No calendars listed',
  'calconn.listEmptyText':
      '{provider} lists no calendars for this account. Sync now, then try again.',
  'calconn.primary': 'Primary calendar, always syncs both ways',
  'calconn.readOnly': 'Read into ANUM only',
  'calconn.tooMany': 'Choose at most {max} calendars besides the primary one.',
  'calconn.save': 'Save choice',
  'calconn.saving': 'Saving...',
  'calconn.saved': 'Saved. {count} other calendar(s) of {email} sync now.',
  'calconn.disconnect': 'Disconnect',
  'calconn.disconnectTitle': 'Disconnect this calendar?',
  'calconn.disconnectText':
      'ANUM stops syncing {email} and gives back its access to {provider}. The events already in ANUM stay here.',
  'calconn.disconnected': 'Disconnected {email}.',
  'calconn.raw': '{text}',
  'calconn.rule':
      'If an event changed in both places since the last sync, the later change wins and the calendar wins a tie. Events organised by someone else, and the other calendars you choose, follow the calendar.',
};

const calendarSyncAr = <String, String>{
  'calsync.none':
      'الأحداث محفوظة في ANUM. يربط المالك تقويم Google أو Microsoft 365 من الربط.',
  'calsync.on': 'متزامن مع {provider} ({email}). {when}',
  'calsync.when.never': 'لم تتم المزامنة بعد.',
  'calsync.when.now': 'تمت المزامنة الآن.',
  'calsync.when.minutes': 'تمت المزامنة قبل {count} دقيقة.',
  'calsync.when.hours': 'تمت المزامنة قبل {count} ساعة.',
  'calsync.when.days': 'تمت المزامنة قبل {count} يومًا.',
  'calsync.problem': 'مزامنة {provider} تحتاج إلى انتباه',
  'calsync.error': 'ما الذي حدث: {reason}',
  'calsync.fix.reauth_required':
      'لم يعد {provider} يقبل وصول ANUM. يربطه المالك من جديد من الربط.',
  'calsync.fix.error':
      'فشلت آخر مزامنة. جرّب زامن الآن؛ وإن استمر الفشل فيعيد المالك ربط {provider} من الربط.',
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
  'calconn.title': 'ربط التقويمات',
  'calconn.subtitle': 'تقويمات Google أو Microsoft 365 المتزامنة مع ANUM.',
  'calconn.open': 'الربط',
  'calconn.loading': 'جارٍ تحميل التقويمات المرتبطة',
  'calconn.loadFailed': 'تعذّر تحميل التقويمات المرتبطة',
  'calconn.retry': 'حاول مرة أخرى',
  'calconn.deniedTitle': 'لا يمكنك رؤية التقويم',
  'calconn.denied':
      'رؤية التقويم تحتاج إلى دور مشاهد أو عضو أو مالك في مساحة العمل هذه.',
  'calconn.ownersOnly':
      'مالكو مساحة العمل فقط يربطون التقويمات ويختارونها ويزامنونها ويفصلونها. ترى حالتها هنا.',
  'calconn.connections': 'التقويمات المرتبطة',
  'calconn.none': 'لا يوجد تقويم مرتبط بعد',
  'calconn.noneOwner':
      'اربط Google أو Microsoft 365 أدناه، وبعدها تتزامن الأحداث في الاتجاهين.',
  'calconn.noneOther':
      'الأحداث محفوظة في ANUM. يستطيع مالك مساحة العمل ربط Google أو Microsoft 365.',
  'calconn.providers': 'التقويمات التي يمكن ربطها',
  'calconn.notSetUp': 'غير مُعدّ على هذا الخادم. يضيف المشغّل: {settings}',
  'calconn.connect': 'ربط {provider}',
  'calconn.connectAnother': 'ربط حساب {provider} آخر',
  'calconn.reconnect': 'ربط {provider} من جديد',
  'calconn.opening': 'جارٍ فتح تسجيل الدخول إلى {provider} في المتصفح...',
  'calconn.waiting':
      'أكمل تسجيل الدخول إلى {provider} في المتصفح، وسيُكمل ANUM الربط عند عودتك.',
  'calconn.finishing': 'جارٍ إكمال الربط...',
  'calconn.finish': 'سجّلت الدخول',
  'calconn.cancel': 'إلغاء',
  'calconn.browserFailed':
      'تعذّر فتح المتصفح. تأكد من وجود متصفح ثم حاول مرة أخرى.',
  'calconn.connectFailed': 'لم يكتمل الربط: {reason}',
  'calconn.connected': 'تم ربط {email}، وأحداثه تتزامن الآن.',
  'calconn.status.connected': 'مرتبط',
  'calconn.status.error': 'فشلت آخر مزامنة',
  'calconn.status.reauth_required': 'يحتاج إلى تسجيل الدخول من جديد',
  'calconn.events': '{count} حدثًا متزامنًا',
  'calconn.also': 'تُزامَن أيضًا للقراءة فقط: {names}',
  'calconn.readFailed': 'تعذّرت قراءة بعض التقويمات المختارة: {reasons}',
  'calconn.fix.reauth_required':
      'لم يعد {provider} يقبل وصول ANUM. يضغط المالك ربط {provider} من جديد ويسجّل الدخول، وتبقى الأحداث المتزامنة.',
  'calconn.fix.error':
      'فشلت آخر مزامنة. جرّب زامن الآن، وإن تكرر الفشل فافصل {provider} واربطه من جديد.',
  'calconn.choose': 'اختيار التقويمات',
  'calconn.chooseTitle': 'تقويمات {email}',
  'calconn.chooseLead':
      'يُزامَن التقويم الأساسي دائمًا في الاتجاهين. أما التقويمات الأخرى التي تختارها فتُقرأ إلى ANUM وتبقى محدّثة، ولا يكتب ANUM فيها أبدًا.',
  'calconn.listing': 'جارٍ طلب تقويمات هذا الحساب من {provider}',
  'calconn.listFailed': 'تعذّر عرض التقويمات',
  'calconn.listEmpty': 'لا توجد تقويمات',
  'calconn.listEmptyText':
      'لا يعرض {provider} أي تقويم لهذا الحساب. زامن الآن ثم حاول مرة أخرى.',
  'calconn.primary': 'التقويم الأساسي، يُزامَن دائمًا في الاتجاهين',
  'calconn.readOnly': 'يُقرأ إلى ANUM فقط',
  'calconn.tooMany': 'اختر {max} تقويمات على الأكثر بجانب التقويم الأساسي.',
  'calconn.save': 'حفظ الاختيار',
  'calconn.saving': 'جارٍ الحفظ...',
  'calconn.saved':
      'تم الحفظ. يُزامَن الآن {count} من التقويمات الأخرى لـ {email}.',
  'calconn.disconnect': 'فصل',
  'calconn.disconnectTitle': 'فصل هذا التقويم؟',
  'calconn.disconnectText':
      'يتوقف ANUM عن مزامنة {email} ويعيد صلاحية الوصول إلى {provider}. تبقى الأحداث الموجودة في ANUM هنا.',
  'calconn.disconnected': 'تم فصل {email}.',
  'calconn.raw': '{text}',
  'calconn.rule':
      'إذا تغيّر الحدث في المكانين منذ آخر مزامنة فالتغيير الأحدث هو الذي يُعتمد، وعند التساوي يُعتمد ما في التقويم. الأحداث التي ينظمها شخص آخر والتقويمات الأخرى التي تختارها تتبع التقويم.',
};
