/// Autonomy policy strings, English and Arabic: quiet hours, daily caps,
/// blocked lists, rules and the organization guardrails (docs/autonomy.md).
/// Looked up by `AnumLocalizations.t` after the main catalogs. No plural keys:
/// `p()` reads only the main catalog.
const autonomyPolicyEn = <String, String>{
  // ---- Shared ----
  'ap.accessMessage':
      'You do not have access to {what}.\nThe API answered: {message}',
  'ap.what.rules': 'the rules',
  'ap.what.rulesTitle': 'The rules',
  'ap.what.guardrails': 'the organization guardrails',
  'ap.what.guardrailsTitle': 'The organization guardrails',
  'ap.addItem': 'Add',
  'ap.removeItem': 'Remove {item}',
  'ap.filterList': 'Search this list',
  'ap.noMatch': 'Nothing in this list matches "{q}".',
  'ap.showMore': 'Show more ({shown} of {total})',

  // ---- Settings: thresholds ----
  'ap.amountMax': 'The organization allows at most {max}.',
  'ap.priceChange': 'Price change threshold (%)',
  'ap.priceChangeMax': 'The organization allows at most {max}%.',
  'ap.proposalAfter': 'Suggest a rule after this many approvals',
  'ap.proposalAfterHelp':
      'From 2 to 50. ANUM suggests letting a pattern act on its own after you approve it this many times.',
  'ap.err.amountMax':
      'Amount threshold is above the organization limit of {max}.',
  'ap.err.priceChange':
      'Price change threshold must be a number from 0 to 1000.',
  'ap.err.priceChangeMax':
      'Price change threshold is above the organization limit of {max}%.',
  'ap.err.proposalAfter':
      'The number of approvals before a suggestion must be a whole number from 2 to 50.',

  // ---- Quiet hours ----
  'ap.quiet': 'Quiet hours',
  'ap.quietSubtitle':
      'Inside this window nothing with an effect runs on its own: it asks instead. A window may cross midnight.',
  'ap.quietOn': 'Use quiet hours',
  'ap.quietOff': 'No quiet hours. ANUM may act at any hour.',
  'ap.quietStart': 'Starts',
  'ap.quietEnd': 'Ends',
  'ap.quietTimeHelp': 'Time as HH:MM, 24-hour, for example 22:00.',
  'ap.quietZone': 'Time zone',
  'ap.quietZoneHelp': 'An IANA name such as Asia/Riyadh, or UTC.',
  'ap.err.quietTime':
      'Quiet hours must be written as HH:MM in 24-hour time, for example 22:00.',
  'ap.err.quietZone': 'Use an IANA time zone name such as Asia/Riyadh.',

  // ---- Daily caps ----
  'ap.caps': 'Daily caps',
  'ap.capsSubtitle':
      'The most things ANUM does on its own per day for an area or a tool. Above the cap it asks. Without a cap an area allows 200 a day. Reads and drafts are not counted.',
  'ap.capsEmpty':
      'No daily caps. Add one to limit how much ANUM does on its own each day.',
  'ap.capsNone': 'No daily caps are set.',
  'ap.capAdd': 'Add a cap',
  'ap.capKey': 'Area or tool name',
  'ap.capKeyHelp': 'For example email, or the exact tool name.',
  'ap.capValue': 'Actions per day',
  'ap.capRemove': 'Remove the cap for {key}',
  'ap.capRemoveRow': 'Remove this cap',
  'ap.err.capKey':
      'Every daily cap needs an area or tool name of at most 120 characters.',
  'ap.err.capDup': 'The daily cap for {key} is listed twice.',
  'ap.err.capValue':
      'The daily cap for {key} must be a whole number from 0 to 100000.',
  'ap.err.capsMany': 'At most 100 daily caps.',

  // ---- Blocked lists ----
  'ap.blockedRecipients': 'Blocked recipients',
  'ap.blockedRecipientsSubtitle':
      'A call to these addresses or numbers is blocked, even with an approval.',
  'ap.blockedDomains': 'Blocked domains',
  'ap.blockedDomainsSubtitle':
      'A call to these domains and their subdomains is blocked, even with an approval.',
  'ap.recipientField': 'Email address or phone number',
  'ap.domainField': 'Domain, for example example.com',
  'ap.recipientsEmpty':
      'No blocked recipients. Add an address or a phone number to block it.',
  'ap.domainsEmpty': 'No blocked domains. Add a domain to block it.',
  'ap.err.recipient':
      'Enter an email address like name@example.com, or a phone number with its country code.',
  'ap.err.domain':
      'Enter a domain like example.com, without http:// or a path.',
  'ap.err.dup': '{item} is already in the list.',
  'ap.err.listFull': 'A list holds at most 500 entries.',

  // ---- Entry points ----
  'ap.rulesTitle': 'Rules',
  'ap.rulesTile': 'Set the level for one tool, or one tool with a template.',
  'ap.guardrailsTitle': 'Organization guardrails',
  'ap.guardrailsTile':
      'The floors every workspace keeps, set by organization administrators.',

  // ---- Rules ----
  'ap.rulesHeading': 'Rules for exact patterns',
  'ap.rulesSubtitle':
      'A rule sets the level for one tool, optionally with one template. Rules never lift first contact, bulk sends, thresholds, quiet hours, caps or guardrails.',
  'ap.rules.list': 'Rules in force',
  'ap.rules.empty': 'No rules yet',
  'ap.rules.emptyMessage':
      'Without a rule each tool follows the level of its area. Add a rule below, or use "Always ask for this" on an activity entry.',
  'ap.rules.addFirst': 'Add a rule',
  'ap.rules.add': 'Add a rule',
  'ap.rules.addSubtitle':
      'Adding a rule for a pattern that already has one replaces it.',
  'ap.rules.tool': 'Tool name',
  'ap.rules.toolHelp':
      'The exact tool name, as shown on an activity entry. The API refuses names that are not available.',
  'ap.rules.template': 'Template (optional)',
  'ap.rules.templateHelp':
      'Only for tools that send a template. Leave empty for the tool as a whole.',
  'ap.rules.level': 'Level',
  'ap.rules.addButton': 'Add rule',
  'ap.rules.added': 'Rule added for {pattern}.',
  'ap.rules.deleted': 'Rule removed for {pattern}.',
  'ap.rules.delete': 'Remove the rule for {pattern}',
  'ap.rules.detail': '{category}, {level}, {source}',
  'ap.rules.source.manual': 'added by hand',
  'ap.rules.source.proposal': 'from a suggestion',
  'ap.rules.source.tighten': 'always ask',
  'ap.rules.tightenOnly':
      'Only workspace owners allow more autonomy or remove rules. You can add rules that make ANUM always ask.',
  'ap.rules.noAdd':
      'You cannot add or remove rules in this workspace. What is in force is shown below.',
  'ap.err.tool': 'Enter the tool name.',
  'ap.err.toolLong': 'Tool names are at most 160 characters.',
  'ap.err.template': 'Template names are at most 120 characters.',

  // ---- Organization guardrails ----
  'ap.g.heading': 'Floors every workspace keeps',
  'ap.g.subtitle':
      'A workspace may be stricter than these but never looser. A floor lowered later caps what a workspace stored.',
  'ap.g.adminRequired': 'Organization administrator access required',
  'ap.g.adminOnly':
      'Only organization administrators change the guardrails. What is in force is shown below.',
  'ap.g.limits': 'Limits',
  'ap.g.limitsSubtitle':
      'Leave a field empty for no limit. Workspaces cannot set thresholds above these.',
  'ap.g.spending': 'Spending limit',
  'ap.g.spendingHelp':
      'An action carrying a larger amount is blocked, even with an approval.',
  'ap.g.maxAmount': 'Highest amount threshold',
  'ap.g.maxRecipients': 'Highest recipients threshold',
  'ap.g.maxPrice': 'Highest price change threshold (%)',
  'ap.g.noLimit': 'No limit',
  'ap.g.ceilings': 'Level ceilings',
  'ap.g.ceilingsSubtitle': 'The most autonomy a workspace may set per area.',
  'ap.g.noCeiling': 'No ceiling',
  'ap.g.quietSubtitle':
      'Applies to every workspace, on top of its own quiet hours.',
  'ap.g.capsSubtitle':
      'Autonomous actions per day for an area or a tool, for every workspace. Above the cap ANUM asks.',
  'ap.g.blockedRecipientsSubtitle':
      'Blocked for every workspace, even with an approval.',
  'ap.g.blockedDomainsSubtitle':
      'Blocked for every workspace, subdomains included, even with an approval.',
  'ap.g.save': 'Save guardrails',
  'ap.g.saved':
      'Guardrails saved. They apply to every workspace now and are in the audit log.',
  'ap.err.gNumber': '{field} must be a number of 0 or more.',
  'ap.err.gRecipients':
      'Highest recipients threshold must be a whole number from 0 to 10000.',
};

const autonomyPolicyAr = <String, String>{
  // ---- Shared ----
  'ap.accessMessage':
      'ليس لديك صلاحية الوصول إلى {what}.\nرد الواجهة: {message}',
  'ap.what.rules': 'القواعد',
  'ap.what.rulesTitle': 'القواعد',
  'ap.what.guardrails': 'ضوابط المؤسسة',
  'ap.what.guardrailsTitle': 'ضوابط المؤسسة',
  'ap.addItem': 'إضافة',
  'ap.removeItem': 'إزالة {item}',
  'ap.filterList': 'ابحث في هذه القائمة',
  'ap.noMatch': 'لا شيء في هذه القائمة يطابق "{q}".',
  'ap.showMore': 'عرض المزيد ({shown} من {total})',

  // ---- Settings: thresholds ----
  'ap.amountMax': 'تسمح المؤسسة بحد أقصى {max}.',
  'ap.priceChange': 'حد تغيير السعر (%)',
  'ap.priceChangeMax': 'تسمح المؤسسة بحد أقصى {max}%.',
  'ap.proposalAfter': 'اقترح قاعدة بعد هذا العدد من الموافقات',
  'ap.proposalAfterHelp':
      'من 2 إلى 50. يقترح ANUM السماح لنمط بالتنفيذ من تلقاء نفسه بعد أن توافق عليه هذا العدد من المرات.',
  'ap.err.amountMax': 'حد المبلغ أعلى من حد المؤسسة البالغ {max}.',
  'ap.err.priceChange': 'يجب أن يكون حد تغيير السعر رقمًا من 0 إلى 1000.',
  'ap.err.priceChangeMax': 'حد تغيير السعر أعلى من حد المؤسسة البالغ {max}%.',
  'ap.err.proposalAfter':
      'يجب أن يكون عدد الموافقات قبل الاقتراح عددًا صحيحًا من 2 إلى 50.',

  // ---- Quiet hours ----
  'ap.quiet': 'ساعات الهدوء',
  'ap.quietSubtitle':
      'داخل هذه الفترة لا يُنفَّذ أي إجراء له أثر من تلقاء نفسه: يسأل بدلًا من ذلك. قد تمتد الفترة عبر منتصف الليل.',
  'ap.quietOn': 'استخدام ساعات الهدوء',
  'ap.quietOff': 'لا توجد ساعات هدوء. يمكن لـ ANUM التصرف في أي ساعة.',
  'ap.quietStart': 'تبدأ',
  'ap.quietEnd': 'تنتهي',
  'ap.quietTimeHelp': 'الوقت بصيغة HH:MM بنظام 24 ساعة، مثل 22:00.',
  'ap.quietZone': 'المنطقة الزمنية',
  'ap.quietZoneHelp': 'اسم IANA مثل Asia/Riyadh، أو UTC.',
  'ap.err.quietTime': 'اكتب ساعات الهدوء بصيغة HH:MM بنظام 24 ساعة، مثل 22:00.',
  'ap.err.quietZone': 'استخدم اسم منطقة زمنية من IANA مثل Asia/Riyadh.',

  // ---- Daily caps ----
  'ap.caps': 'الحدود اليومية',
  'ap.capsSubtitle':
      'أقصى ما ينفذه ANUM من تلقاء نفسه في اليوم لمجال أو أداة. فوق الحد يسأل. بدون حد يسمح المجال بـ 200 في اليوم. القراءات والمسودات لا تُحتسب.',
  'ap.capsEmpty':
      'لا توجد حدود يومية. أضف حدًا لتقييد ما ينفذه ANUM من تلقاء نفسه كل يوم.',
  'ap.capsNone': 'لم تُضبط حدود يومية.',
  'ap.capAdd': 'إضافة حد',
  'ap.capKey': 'المجال أو اسم الأداة',
  'ap.capKeyHelp': 'مثل email، أو اسم الأداة بالضبط.',
  'ap.capValue': 'عدد الإجراءات في اليوم',
  'ap.capRemove': 'إزالة الحد الخاص بـ {key}',
  'ap.capRemoveRow': 'إزالة هذا الحد',
  'ap.err.capKey': 'يحتاج كل حد يومي إلى مجال أو اسم أداة بحد أقصى 120 حرفًا.',
  'ap.err.capDup': 'الحد اليومي لـ {key} مذكور مرتين.',
  'ap.err.capValue':
      'يجب أن يكون الحد اليومي لـ {key} عددًا صحيحًا من 0 إلى 100000.',
  'ap.err.capsMany': 'الحد الأقصى 100 حد يومي.',

  // ---- Blocked lists ----
  'ap.blockedRecipients': 'المستلمون المحظورون',
  'ap.blockedRecipientsSubtitle':
      'أي اتصال بهذه العناوين أو الأرقام محظور، حتى مع الموافقة.',
  'ap.blockedDomains': 'النطاقات المحظورة',
  'ap.blockedDomainsSubtitle':
      'أي اتصال بهذه النطاقات ونطاقاتها الفرعية محظور، حتى مع الموافقة.',
  'ap.recipientField': 'البريد الإلكتروني أو رقم الهاتف',
  'ap.domainField': 'النطاق، مثل example.com',
  'ap.recipientsEmpty':
      'لا يوجد مستلمون محظورون. أضف عنوانًا أو رقم هاتف لحظره.',
  'ap.domainsEmpty': 'لا توجد نطاقات محظورة. أضف نطاقًا لحظره.',
  'ap.err.recipient':
      'أدخل بريدًا إلكترونيًا مثل name@example.com، أو رقم هاتف مع رمز الدولة.',
  'ap.err.domain': 'أدخل نطاقًا مثل example.com، بدون http:// أو مسار.',
  'ap.err.dup': '{item} موجود في القائمة بالفعل.',
  'ap.err.listFull': 'تتسع القائمة لـ 500 عنصر كحد أقصى.',

  // ---- Entry points ----
  'ap.rulesTitle': 'القواعد',
  'ap.rulesTile': 'حدد المستوى لأداة واحدة، أو لأداة واحدة مع قالب.',
  'ap.guardrailsTitle': 'ضوابط المؤسسة',
  'ap.guardrailsTile':
      'الحدود الدنيا التي تلتزم بها كل مساحات العمل، ويضبطها مسؤولو المؤسسة.',

  // ---- Rules ----
  'ap.rulesHeading': 'قواعد لأنماط محددة',
  'ap.rulesSubtitle':
      'تحدد القاعدة المستوى لأداة واحدة، ومعها قالب واحد اختياريًا. لا ترفع القواعد أبدًا قيد أول تواصل أو الإرسال الجماعي أو الحدود أو ساعات الهدوء أو الضوابط.',
  'ap.rules.list': 'القواعد السارية',
  'ap.rules.empty': 'لا توجد قواعد بعد',
  'ap.rules.emptyMessage':
      'بدون قاعدة تتبع كل أداة مستوى مجالها. أضف قاعدة أدناه، أو استخدم «اسأل دائمًا لهذا» في أحد عناصر النشاط.',
  'ap.rules.addFirst': 'إضافة قاعدة',
  'ap.rules.add': 'إضافة قاعدة',
  'ap.rules.addSubtitle': 'إضافة قاعدة لنمط له قاعدة بالفعل تستبدلها.',
  'ap.rules.tool': 'اسم الأداة',
  'ap.rules.toolHelp':
      'اسم الأداة بالضبط كما يظهر في عنصر النشاط. ترفض الواجهة الأسماء غير المتاحة.',
  'ap.rules.template': 'القالب (اختياري)',
  'ap.rules.templateHelp':
      'للأدوات التي ترسل قالبًا فقط. اتركه فارغًا للأداة كلها.',
  'ap.rules.level': 'المستوى',
  'ap.rules.addButton': 'إضافة القاعدة',
  'ap.rules.added': 'أُضيفت قاعدة لـ {pattern}.',
  'ap.rules.deleted': 'أُزيلت القاعدة الخاصة بـ {pattern}.',
  'ap.rules.delete': 'إزالة القاعدة الخاصة بـ {pattern}',
  'ap.rules.detail': '{category}، {level}، {source}',
  'ap.rules.source.manual': 'أُضيفت يدويًا',
  'ap.rules.source.proposal': 'من اقتراح',
  'ap.rules.source.tighten': 'اسأل دائمًا',
  'ap.rules.tightenOnly':
      'وحدهم مالكو مساحة العمل يسمحون بمزيد من الاستقلالية أو يزيلون القواعد. يمكنك إضافة قواعد تجعل ANUM يسأل دائمًا.',
  'ap.rules.noAdd':
      'لا يمكنك إضافة القواعد أو إزالتها في مساحة العمل هذه. ما هو ساري معروض أدناه.',
  'ap.err.tool': 'أدخل اسم الأداة.',
  'ap.err.toolLong': 'أسماء الأدوات 160 حرفًا كحد أقصى.',
  'ap.err.template': 'أسماء القوالب 120 حرفًا كحد أقصى.',

  // ---- Organization guardrails ----
  'ap.g.heading': 'الحدود الدنيا التي تلتزم بها كل مساحات العمل',
  'ap.g.subtitle':
      'يمكن لمساحة العمل أن تكون أكثر تشددًا من هذه الحدود لكن ليس أقل. أي حد يُخفَّض لاحقًا يقيّد ما خزنته مساحة العمل.',
  'ap.g.adminRequired': 'يلزم الوصول كمسؤول مؤسسة',
  'ap.g.adminOnly':
      'وحدهم مسؤولو المؤسسة يغيّرون الضوابط. ما هو ساري معروض أدناه.',
  'ap.g.limits': 'الحدود',
  'ap.g.limitsSubtitle':
      'اترك الحقل فارغًا لعدم وجود حد. لا يمكن لمساحات العمل ضبط حدود أعلى من هذه.',
  'ap.g.spending': 'حد الإنفاق',
  'ap.g.spendingHelp': 'أي إجراء يحمل مبلغًا أكبر يُحظر، حتى مع الموافقة.',
  'ap.g.maxAmount': 'أعلى حد للمبلغ',
  'ap.g.maxRecipients': 'أعلى حد للمستلمين',
  'ap.g.maxPrice': 'أعلى حد لتغيير السعر (%)',
  'ap.g.noLimit': 'بلا حد',
  'ap.g.ceilings': 'سقوف المستويات',
  'ap.g.ceilingsSubtitle': 'أقصى استقلالية يمكن لمساحة العمل ضبطها لكل مجال.',
  'ap.g.noCeiling': 'بلا سقف',
  'ap.g.quietSubtitle':
      'تسري على كل مساحات العمل، إضافة إلى ساعات الهدوء الخاصة بكل منها.',
  'ap.g.capsSubtitle':
      'الإجراءات التلقائية في اليوم لمجال أو أداة، لكل مساحات العمل. فوق الحد يسأل ANUM.',
  'ap.g.blockedRecipientsSubtitle':
      'محظورون في كل مساحات العمل، حتى مع الموافقة.',
  'ap.g.blockedDomainsSubtitle':
      'محظورة في كل مساحات العمل مع نطاقاتها الفرعية، حتى مع الموافقة.',
  'ap.g.save': 'حفظ الضوابط',
  'ap.g.saved':
      'حُفظت الضوابط. تسري على كل مساحات العمل الآن وهي مسجلة في سجل التدقيق.',
  'ap.err.gNumber': 'يجب أن يكون {field} رقمًا لا يقل عن 0.',
  'ap.err.gRecipients':
      'يجب أن يكون أعلى حد للمستلمين عددًا صحيحًا من 0 إلى 10000.',
};
