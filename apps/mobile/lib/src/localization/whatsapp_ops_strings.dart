/// whatsapp ops strings, English and Arabic: Templates, Setup, template
/// sending and attachments (docs/whatsapp.md#templates-and-setup). Looked up
/// by `AnumLocalizations.t` after the main catalogs.
const waOpsEn = <String, String>{
  // Shared
  'waOps.saving': 'Saving…',
  'waOps.cancel': 'Cancel',
  'waOps.notConnectedOwner':
      'Connect your WhatsApp Business number under Setup. Customers\' messages then arrive here.',
  'waOps.closedExplain':
      'More than 24 hours passed since the customer\'s last message, so WhatsApp allows only approved templates. Send a template below.',
  'waOps.mediaSaved': 'Attachment saved.',

  // Templates
  'waOps.templates.title': 'Templates',
  'waOps.templates.refresh': 'Refresh from Meta',
  'waOps.templates.loading': 'Loading templates…',
  'waOps.templates.deniedTitle': 'Templates are for owners and members',
  'waOps.templates.failed': 'Templates could not be loaded',
  'waOps.templates.intro':
      'Message templates approved by Meta are the only way to write to a customer after the 24-hour window closes. Create and submit them in Meta Business Manager, then refresh here.',
  'waOps.templates.search': 'Search templates',
  'waOps.templates.emptyTitle': 'No templates yet',
  'waOps.templates.emptyText':
      'Create a template in Meta Business Manager, wait for its approval, then refresh this list.',
  'waOps.templates.noMatch': 'No template matches',
  'waOps.templates.noMatchText':
      'Try another word from the name or the message.',
  'waOps.templates.more': 'Show more ({count} left)',
  'waOps.templates.fetched': 'Loaded from Meta at {time}.',
  'waOps.templates.autoExplain':
      'Auto-send lets an approved template go out as soon as it is requested in a conversation where the customer has written. Your approval rules, autonomy level, quiet hours and daily limits still apply.',
  'waOps.templates.autoShort': 'Auto-send',
  'waOps.templates.autoLabel': 'Send without waiting for approval',
  'waOps.templates.params': '{count} parameters',
  'waOps.templates.refreshed': 'Templates refreshed from Meta.',
  'waOps.templates.autoOn': '{name} can now be sent automatically.',
  'waOps.templates.autoOff': '{name} now waits for approval.',
  'waOps.tstatus.APPROVED': 'approved',
  'waOps.tstatus.PENDING': 'pending',
  'waOps.tstatus.REJECTED': 'rejected',
  'waOps.tstatus.PAUSED': 'paused',
  'waOps.tstatus.DISABLED': 'disabled',
  'waOps.tcat.MARKETING': 'marketing',
  'waOps.tcat.UTILITY': 'utility',
  'waOps.tcat.AUTHENTICATION': 'authentication',

  // Send a template from a thread
  'waOps.send.open': 'Send a template',
  'waOps.send.title': 'Send a template',
  'waOps.send.loadFailed': 'Templates could not be loaded. {message}',
  'waOps.send.none':
      'There are no approved templates. Create one in Meta Business Manager and refresh the list.',
  'waOps.send.choose': 'Approved template',
  'waOps.send.parameter': 'Value for parameter {number}',
  'waOps.send.preview': 'What the customer will read',
  'waOps.send.autoNote':
      'This template is on the auto-send list. It goes out at once unless a rule asks for approval.',
  'waOps.send.approvalNote':
      'An owner approves this before it is sent. Approvals show on the message.',
  'waOps.send.now': 'Send now',
  'waOps.send.fillAll': 'Fill in every value before sending.',
  'waOps.send.failed': 'The template was not sent.',

  // Setup
  'waOps.setup.open': 'Open Setup',
  'waOps.setup.title': 'WhatsApp setup',
  'waOps.setup.loading': 'Loading WhatsApp setup…',
  'waOps.setup.deniedTitle': 'Setup is for owners and members',
  'waOps.setup.failed': 'Setup could not be loaded',
  'waOps.setup.statusTitle': 'Connection',
  'waOps.setup.deployment':
      'This server is missing settings WhatsApp needs: {names}. Ask whoever runs ANUM to set them.',
  'waOps.setup.state.none': 'Not connected',
  'waOps.setup.state.connected': 'Connected',
  'waOps.setup.state.error': 'Problem',
  'waOps.setup.state.unverified': 'Not checked yet',
  'waOps.setup.name': 'Verified name',
  'waOps.setup.number': 'Number',
  'waOps.setup.quality': 'Quality rating',
  'waOps.setup.lastWebhook': 'Last message received',
  'waOps.setup.never': 'Never',
  'waOps.setup.lastCheck': 'Last check',
  'waOps.setup.webhookUrl': 'Webhook URL for Meta',
  'waOps.setup.copy': 'Copy URL',
  'waOps.setup.copied': 'Copied',
  'waOps.setup.webhookHelp':
      'In Meta, open WhatsApp > Configuration, paste this URL as the callback URL and enter the verify token you saved here. Subscribe to the messages field.',
  'waOps.setup.check': 'Check connection',
  'waOps.setup.checking': 'Checking…',
  'waOps.setup.test': 'Send test message',
  'waOps.setup.testNeedsNumber':
      'Save your own WhatsApp number as the test number first.',
  'waOps.setup.disconnect': 'Disconnect',
  'waOps.setup.disconnectTitle': 'Disconnect WhatsApp?',
  'waOps.setup.disconnectText':
      'ANUM forgets this number and its saved credentials. Conversations stay readable. You can connect again later.',
  'waOps.setup.disconnected': 'WhatsApp is disconnected.',
  'waOps.setup.ownerOnlyTitle': 'Only owners change the connection',
  'waOps.setup.ownerOnly':
      'You can see the status here. An owner of this workspace connects, checks and disconnects the number.',
  'waOps.setup.editTitle': 'Update the connection',
  'waOps.setup.connectTitle': 'Connect your number',
  'waOps.setup.intro':
      'Copy these values from Meta for Developers: WhatsApp > API Setup. Secrets are sent once and never shown again.',
  'waOps.setup.phoneId': 'Phone number ID',
  'waOps.setup.wabaId': 'WhatsApp Business account ID',
  'waOps.setup.token': 'Access token',
  'waOps.setup.keep': 'Leave empty to keep the saved token ({hint}).',
  'waOps.setup.secret': 'App secret',
  'waOps.setup.keepSaved': 'Leave empty to keep the saved value.',
  'waOps.setup.verify': 'Verify token',
  'waOps.setup.verifyHelp':
      'A random value of at least {count} characters that you also enter in Meta. The generate button copies one.',
  'waOps.setup.generate': 'Generate and copy a verify token',
  'waOps.setup.generated':
      'Verify token generated and copied. Paste it into Meta, then save.',
  'waOps.setup.testNumber': 'Your own number for tests',
  'waOps.setup.retention': 'Keep message history',
  'waOps.setup.retentionNote':
      'Older messages are deleted automatically after this time.',
  'waOps.setup.retentionDefault': 'Server default ({value})',
  'waOps.setup.retentionKeep': 'Until deleted',
  'waOps.setup.days': '{count} days',
  'waOps.setup.triage': 'Sort and summarise new messages automatically',
  'waOps.setup.secretsNote':
      'Secrets are encrypted on the server and never sent back to the app. Leave a secret empty to keep the saved one.',
  'waOps.setup.save': 'Save changes',
  'waOps.setup.connect': 'Connect',
  'waOps.setup.saved': 'Saved. {name} is connected.',
  'waOps.setup.savedProblem':
      'Saved, but Meta did not accept the connection. {message}',
  'waOps.setup.checkOk': 'The connection works.',
  'waOps.setup.testSent': 'Test message sent to {number}.',
  'waOps.err.phoneId':
      'The phone number ID is the 5 to 20 digit number shown under API Setup in Meta.',
  'waOps.err.wabaId':
      'The business account ID is the 5 to 20 digit number shown under API Setup in Meta.',
  'waOps.err.secrets':
      'To connect, enter the access token, the app secret and a verify token.',
  'waOps.err.verifyToken': 'Use a verify token of at least {count} characters.',
  'waOps.err.testNumber':
      'Enter your test number in international format, digits only, for example 9665XXXXXXXX.',

  // Attachments
  'waOps.media.image': 'Image',
  'waOps.media.document': 'Document',
  'waOps.media.audio': 'Audio message',
  'waOps.media.video': 'Video',
  'waOps.media.sticker': 'Sticker',
  'waOps.media.file': 'Attachment',
  'waOps.media.open': 'Open',
  'waOps.media.opening': 'Opening…',
  'waOps.media.openLabel': 'Open the {kind}',
  'waOps.media.save': 'Save a copy',
  'waOps.media.imageLabel': 'Image from the conversation',
  'waOps.media.cannotShow':
      'This image cannot be shown here. Save a copy to open it in another app.',
};

const waOpsAr = <String, String>{
  // Shared
  'waOps.saving': 'جارٍ الحفظ…',
  'waOps.cancel': 'إلغاء',
  'waOps.notConnectedOwner':
      'اربط رقم واتساب للأعمال من الإعداد. بعدها تصل رسائل العملاء هنا.',
  'waOps.closedExplain':
      'مرّت أكثر من 24 ساعة على آخر رسالة من العميل، لذا يسمح واتساب بالقوالب المعتمدة فقط. أرسل قالبًا من الأسفل.',
  'waOps.mediaSaved': 'تم حفظ المرفق.',

  // Templates
  'waOps.templates.title': 'القوالب',
  'waOps.templates.refresh': 'تحديث من ميتا',
  'waOps.templates.loading': 'جارٍ تحميل القوالب…',
  'waOps.templates.deniedTitle': 'القوالب للمالكين والأعضاء',
  'waOps.templates.failed': 'تعذّر تحميل القوالب',
  'waOps.templates.intro':
      'قوالب الرسائل المعتمدة من ميتا هي الطريقة الوحيدة لمراسلة العميل بعد انتهاء نافذة الـ 24 ساعة. أنشئها وقدّمها في مدير أعمال ميتا ثم حدّث القائمة هنا.',
  'waOps.templates.search': 'ابحث في القوالب',
  'waOps.templates.emptyTitle': 'لا توجد قوالب بعد',
  'waOps.templates.emptyText':
      'أنشئ قالبًا في مدير أعمال ميتا وانتظر اعتماده ثم حدّث هذه القائمة.',
  'waOps.templates.noMatch': 'لا يوجد قالب مطابق',
  'waOps.templates.noMatchText': 'جرّب كلمة أخرى من الاسم أو الرسالة.',
  'waOps.templates.more': 'عرض المزيد (متبقٍ {count})',
  'waOps.templates.fetched': 'تم التحميل من ميتا عند {time}.',
  'waOps.templates.autoExplain':
      'يتيح الإرسال التلقائي خروج القالب المعتمد فور طلبه في محادثة كتب فيها العميل. تبقى قواعد الموافقة ومستوى الاستقلالية وساعات الهدوء والحدود اليومية سارية.',
  'waOps.templates.autoShort': 'إرسال تلقائي',
  'waOps.templates.autoLabel': 'أرسل دون انتظار الموافقة',
  'waOps.templates.params': '{count} متغيرات',
  'waOps.templates.refreshed': 'تم تحديث القوالب من ميتا.',
  'waOps.templates.autoOn': 'يمكن الآن إرسال {name} تلقائيًا.',
  'waOps.templates.autoOff': 'أصبح {name} ينتظر الموافقة.',
  'waOps.tstatus.APPROVED': 'معتمد',
  'waOps.tstatus.PENDING': 'قيد المراجعة',
  'waOps.tstatus.REJECTED': 'مرفوض',
  'waOps.tstatus.PAUSED': 'متوقف مؤقتًا',
  'waOps.tstatus.DISABLED': 'معطّل',
  'waOps.tcat.MARKETING': 'تسويق',
  'waOps.tcat.UTILITY': 'خدمي',
  'waOps.tcat.AUTHENTICATION': 'توثيق',

  // Send a template from a thread
  'waOps.send.open': 'إرسال قالب',
  'waOps.send.title': 'إرسال قالب',
  'waOps.send.loadFailed': 'تعذّر تحميل القوالب. {message}',
  'waOps.send.none':
      'لا توجد قوالب معتمدة. أنشئ قالبًا في مدير أعمال ميتا ثم حدّث القائمة.',
  'waOps.send.choose': 'القالب المعتمد',
  'waOps.send.parameter': 'قيمة المتغير {number}',
  'waOps.send.preview': 'ما سيقرؤه العميل',
  'waOps.send.autoNote':
      'هذا القالب في قائمة الإرسال التلقائي. يُرسل فورًا ما لم تطلب قاعدة الموافقة.',
  'waOps.send.approvalNote':
      'يوافق مالك على هذا قبل إرساله. تظهر الموافقات على الرسالة.',
  'waOps.send.now': 'أرسل الآن',
  'waOps.send.fillAll': 'املأ كل القيم قبل الإرسال.',
  'waOps.send.failed': 'لم يُرسل القالب.',

  // Setup
  'waOps.setup.open': 'افتح الإعداد',
  'waOps.setup.title': 'إعداد واتساب',
  'waOps.setup.loading': 'جارٍ تحميل إعداد واتساب…',
  'waOps.setup.deniedTitle': 'الإعداد للمالكين والأعضاء',
  'waOps.setup.failed': 'تعذّر تحميل الإعداد',
  'waOps.setup.statusTitle': 'الاتصال',
  'waOps.setup.deployment':
      'ينقص هذا الخادم إعدادات يحتاجها واتساب: {names}. اطلب من مشغّل أنوم ضبطها.',
  'waOps.setup.state.none': 'غير متصل',
  'waOps.setup.state.connected': 'متصل',
  'waOps.setup.state.error': 'هناك مشكلة',
  'waOps.setup.state.unverified': 'لم يُفحص بعد',
  'waOps.setup.name': 'الاسم الموثّق',
  'waOps.setup.number': 'الرقم',
  'waOps.setup.quality': 'تقييم الجودة',
  'waOps.setup.lastWebhook': 'آخر رسالة مستلمة',
  'waOps.setup.never': 'أبدًا',
  'waOps.setup.lastCheck': 'آخر فحص',
  'waOps.setup.webhookUrl': 'رابط الويب هوك لميتا',
  'waOps.setup.copy': 'نسخ الرابط',
  'waOps.setup.copied': 'تم النسخ',
  'waOps.setup.webhookHelp':
      'في ميتا افتح واتساب > الإعدادات والصق هذا الرابط كرابط الاستدعاء وأدخل رمز التحقق الذي حفظته هنا. اشترك في حقل الرسائل.',
  'waOps.setup.check': 'افحص الاتصال',
  'waOps.setup.checking': 'جارٍ الفحص…',
  'waOps.setup.test': 'أرسل رسالة اختبار',
  'waOps.setup.testNeedsNumber': 'احفظ رقم واتساب الخاص بك كرقم اختبار أولًا.',
  'waOps.setup.disconnect': 'افصل',
  'waOps.setup.disconnectTitle': 'فصل واتساب؟',
  'waOps.setup.disconnectText':
      'ينسى أنوم هذا الرقم وبيانات الاعتماد المحفوظة. تبقى المحادثات مقروءة. يمكنك الربط مجددًا لاحقًا.',
  'waOps.setup.disconnected': 'تم فصل واتساب.',
  'waOps.setup.ownerOnlyTitle': 'المالكون فقط يغيّرون الاتصال',
  'waOps.setup.ownerOnly':
      'يمكنك رؤية الحالة هنا. مالك مساحة العمل هو من يربط الرقم ويفحصه ويفصله.',
  'waOps.setup.editTitle': 'حدّث الاتصال',
  'waOps.setup.connectTitle': 'اربط رقمك',
  'waOps.setup.intro':
      'انسخ هذه القيم من ميتا للمطورين: واتساب > إعداد الواجهة. تُرسل الأسرار مرة واحدة ولا تُعرض بعدها.',
  'waOps.setup.phoneId': 'معرّف رقم الهاتف',
  'waOps.setup.wabaId': 'معرّف حساب واتساب للأعمال',
  'waOps.setup.token': 'رمز الوصول',
  'waOps.setup.keep': 'اتركه فارغًا للإبقاء على الرمز المحفوظ ({hint}).',
  'waOps.setup.secret': 'سر التطبيق',
  'waOps.setup.keepSaved': 'اتركه فارغًا للإبقاء على القيمة المحفوظة.',
  'waOps.setup.verify': 'رمز التحقق',
  'waOps.setup.verifyHelp':
      'قيمة عشوائية من {count} حرفًا على الأقل تدخلها أيضًا في ميتا. زر التوليد ينسخ واحدة.',
  'waOps.setup.generate': 'ولّد رمز تحقق وانسخه',
  'waOps.setup.generated': 'تم توليد رمز التحقق ونسخه. الصقه في ميتا ثم احفظ.',
  'waOps.setup.testNumber': 'رقمك الخاص للاختبار',
  'waOps.setup.retention': 'مدة حفظ سجل الرسائل',
  'waOps.setup.retentionNote': 'تُحذف الرسائل الأقدم تلقائيًا بعد هذه المدة.',
  'waOps.setup.retentionDefault': 'افتراضي الخادم ({value})',
  'waOps.setup.retentionKeep': 'حتى تُحذف',
  'waOps.setup.days': '{count} يومًا',
  'waOps.setup.triage': 'صنّف الرسائل الجديدة ولخّصها تلقائيًا',
  'waOps.setup.secretsNote':
      'تُشفَّر الأسرار على الخادم ولا تعود إلى التطبيق. اترك السر فارغًا للإبقاء على المحفوظ.',
  'waOps.setup.save': 'حفظ التغييرات',
  'waOps.setup.connect': 'اربط',
  'waOps.setup.saved': 'تم الحفظ. {name} متصل.',
  'waOps.setup.savedProblem': 'تم الحفظ، لكن ميتا لم تقبل الاتصال. {message}',
  'waOps.setup.checkOk': 'الاتصال يعمل.',
  'waOps.setup.testSent': 'أُرسلت رسالة الاختبار إلى {number}.',
  'waOps.err.phoneId':
      'معرّف رقم الهاتف رقم من 5 إلى 20 خانة يظهر في إعداد الواجهة في ميتا.',
  'waOps.err.wabaId':
      'معرّف حساب الأعمال رقم من 5 إلى 20 خانة يظهر في إعداد الواجهة في ميتا.',
  'waOps.err.secrets': 'للربط أدخل رمز الوصول وسر التطبيق ورمز التحقق.',
  'waOps.err.verifyToken': 'استخدم رمز تحقق من {count} حرفًا على الأقل.',
  'waOps.err.testNumber':
      'أدخل رقم الاختبار بالصيغة الدولية وأرقامًا فقط، مثل 9665XXXXXXXX.',

  // Attachments
  'waOps.media.image': 'صورة',
  'waOps.media.document': 'مستند',
  'waOps.media.audio': 'رسالة صوتية',
  'waOps.media.video': 'فيديو',
  'waOps.media.sticker': 'ملصق',
  'waOps.media.file': 'مرفق',
  'waOps.media.open': 'فتح',
  'waOps.media.opening': 'جارٍ الفتح…',
  'waOps.media.openLabel': 'افتح: {kind}',
  'waOps.media.save': 'حفظ نسخة',
  'waOps.media.imageLabel': 'صورة من المحادثة',
  'waOps.media.cannotShow':
      'تعذّر عرض هذه الصورة هنا. احفظ نسخة لفتحها في تطبيق آخر.',
};
