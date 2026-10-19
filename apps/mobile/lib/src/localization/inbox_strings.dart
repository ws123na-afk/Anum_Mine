/// Unified inbox compose strings (docs/email.md), English and Arabic.
/// Looked up by `AnumLocalizations.t` after the main catalogs; the inbox
/// test checks both catalogs have the same keys and placeholders.
const inboxEn = <String, String>{
  'inbox.compose.new': 'New email',
  'inbox.compose.title': 'New email',
  'inbox.compose.from': 'From',
  'inbox.compose.to': 'To',
  'inbox.compose.cc': 'Cc',
  'inbox.compose.bcc': 'Bcc',
  'inbox.compose.addressHint': 'Separate addresses with a comma or semicolon.',
  'inbox.compose.subject': 'Subject',
  'inbox.compose.body': 'Message',
  'inbox.compose.save': 'Save draft',
  'inbox.compose.saving': 'Saving...',
  'inbox.compose.saved': 'Draft saved.',
  'inbox.compose.ask': 'Ask to send',
  'inbox.compose.asking': 'Asking...',
  'inbox.compose.invalid': 'Not an e-mail address: {list}',
  'inbox.compose.tooMany': 'Use at most {count} addresses.',
  'inbox.compose.needTo':
      'Add at least one address in To before asking to send.',
  'inbox.compose.needBody': 'Write the message before asking to send.',
  'inbox.compose.noMailbox':
      'Every mailbox needs reconnecting on the web before it can send. A workspace owner reconnects it in the ANUM web app (Inbox).',
  'inbox.compose.reconnectNote':
      'Mailboxes that need reconnecting on the web are not listed.',
  'inbox.compose.approvalNote':
      'Nothing is sent until a workspace owner approves this exact message. Attachments are added on the web.',
  'inbox.compose.locked':
      'The message waits for approval as shown, so it can no longer be edited here.',
  'inbox.compose.sentText': 'This email was sent.',
  'inbox.compose.draftingApproved': 'Drafting was approved. {result}',
  'inbox.compose.discardTitle': 'Discard this email?',
  'inbox.compose.discardText': 'Your unsaved changes will be lost.',
  'inbox.compose.discard': 'Discard',
  'inbox.compose.keep': 'Keep editing',
  'inbox.approval.cc': 'Cc: {cc}',
  'inbox.approval.bcc': 'Bcc: {bcc}',
  'inbox.approval.subject': 'Subject: {subject}',
};

const inboxAr = <String, String>{
  'inbox.compose.new': 'رسالة جديدة',
  'inbox.compose.title': 'رسالة جديدة',
  'inbox.compose.from': 'من',
  'inbox.compose.to': 'إلى',
  'inbox.compose.cc': 'نسخة',
  'inbox.compose.bcc': 'نسخة مخفية',
  'inbox.compose.addressHint': 'افصل بين العناوين بفاصلة أو فاصلة منقوطة.',
  'inbox.compose.subject': 'الموضوع',
  'inbox.compose.body': 'نص الرسالة',
  'inbox.compose.save': 'حفظ المسودة',
  'inbox.compose.saving': 'جارٍ الحفظ...',
  'inbox.compose.saved': 'حُفظت المسودة.',
  'inbox.compose.ask': 'طلب الإرسال',
  'inbox.compose.asking': 'جارٍ الطلب...',
  'inbox.compose.invalid': 'ليس عنوان بريد إلكتروني: {list}',
  'inbox.compose.tooMany': 'استخدم {count} عنوانًا على الأكثر.',
  'inbox.compose.needTo':
      'أضف عنوانًا واحدًا على الأقل في «إلى» قبل طلب الإرسال.',
  'inbox.compose.needBody': 'اكتب نص الرسالة قبل طلب الإرسال.',
  'inbox.compose.noMailbox':
      'كل صناديق البريد تحتاج إعادة ربط من الويب قبل الإرسال. يعيد مالك مساحة العمل ربطها من تطبيق ANUM على الويب (البريد).',
  'inbox.compose.reconnectNote':
      'لا تظهر هنا صناديق البريد التي تحتاج إعادة ربط من الويب.',
  'inbox.compose.approvalNote':
      'لا يُرسل شيء حتى يوافق مالك مساحة العمل على هذه الرسالة بعينها. تُضاف المرفقات من الويب.',
  'inbox.compose.locked':
      'الرسالة تنتظر الموافقة كما هي، لذا لا يمكن تعديلها هنا بعد الآن.',
  'inbox.compose.sentText': 'أُرسلت هذه الرسالة.',
  'inbox.compose.draftingApproved': 'تمت الموافقة على الكتابة. {result}',
  'inbox.compose.discardTitle': 'تجاهل هذه الرسالة؟',
  'inbox.compose.discardText': 'ستضيع التغييرات غير المحفوظة.',
  'inbox.compose.discard': 'تجاهل',
  'inbox.compose.keep': 'متابعة التحرير',
  'inbox.approval.cc': 'نسخة: {cc}',
  'inbox.approval.bcc': 'نسخة مخفية: {bcc}',
  'inbox.approval.subject': 'الموضوع: {subject}',
};
