import 'package:flutter/widgets.dart';

import '../../src/localization/anum_localizations.dart';

/// English and Arabic strings of Settings > Privacy (docs/privacy.md). The
/// terms follow the web catalog (`privacy.*` keys of
/// apps/web/src/lib/messages/en.ts and ar.ts). Both maps hold the same keys
/// (test/privacy_test.dart checks it). Retention texts the server writes
/// itself (private and task memories, audit log) are shown as sent.
class PrivacyStrings {
  const PrivacyStrings(this.arabic);
  final bool arabic;

  static const english = PrivacyStrings(false);

  static PrivacyStrings of(BuildContext context) =>
      PrivacyStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? ar : en)[key] ?? en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// A message with plural forms (`key.one` ... `key.other`; Arabic uses the
  /// CLDR forms), `{n}` filled in.
  String count(String key, int n, [Map<String, Object> values = const {}]) {
    final form = arabic
        ? AnumLocalizations.arabicPluralForm(n)
        : (n == 1 ? 'one' : 'other');
    return t('$key.${form == 'zero' ? 'other' : form}', {...values, 'n': n});
  }

  static const en = <String, String>{
    'title': 'Privacy',
    'loading': 'Loading your privacy summary',
    'permissionTitle': 'Permission required',
    'permission':
        'Only active members of this workspace can see or change their data here. Ask a workspace owner to restore your membership, or switch to a workspace you belong to.',
    'offlineTitle': 'You are offline',
    'offlineText': 'Reconnect to see and manage your data.',
    'unavailable': 'Privacy summary unavailable',
    'retry': 'Retry',
    'storesHeading': 'What ANUM stores about you',
    'signedIn':
        'Signed in as {user}{role} in {workspace}. Other workspaces keep their own data.',
    'role': ' ({role})',
    'privateTitle': 'Private to you',
    'nothingPrivate': 'Nothing private is stored for you here yet.',
    'sharedTitle': 'Shared with the workspace',
    'keptHeading': 'How long it is kept, and where it goes',
    'changeMemory': 'Change Remember things from my chats',
    'downloadHeading': 'Download my data',
    'downloadText':
        'A JSON file with your profile in this workspace, conversations, private memories, voice transcripts, tasks you created, approvals you decided, notification preferences, your preferences, the list of files you uploaded, what ANUM learned from you, the mailboxes you connected with your email drafts, the WhatsApp messages you sent, the actions ANUM took for you, your team work, and the tyre reports and sales channels you set up. Up to {max}.',
    'preparing': 'Preparing download...',
    'download': 'Download my data',
    'deleteHeading': 'Delete my data',
    'deleteText':
        'Permanently deletes your conversations, private memories (and their search index entries), voice sessions and transcripts, notification preferences, what ANUM learned from you, the private mailboxes you connected (ANUM also withdraws its access at the provider) the email drafts you never sent, your team notifications and team profile, and your private agents with their memories in this workspace, and resets your preferences (such as Remember things from my chats) in every workspace of this organization. This cannot be undone.',
    'keptNone':
        'Kept: shared workspace records (none created by you). A workspace owner can remove them.',
    'kept.one':
        'Kept: {n} shared workspace record you created or decided. A workspace owner can remove them.',
    'kept.two':
        'Kept: {n} shared workspace records you created or decided. A workspace owner can remove them.',
    'kept.few':
        'Kept: {n} shared workspace records you created or decided. A workspace owner can remove them.',
    'kept.many':
        'Kept: {n} shared workspace records you created or decided. A workspace owner can remove them.',
    'kept.other':
        'Kept: {n} shared workspace records you created or decided. A workspace owner can remove them.',
    'account': 'Your account: {note}',
    'typeToConfirm': 'Type {phrase} to confirm',
    'nothingNow': 'Nothing private is stored for you right now.',
    'willDelete.one': '{n} private item will be deleted.',
    'willDelete.two': '{n} private items will be deleted.',
    'willDelete.few': '{n} private items will be deleted.',
    'willDelete.many': '{n} private items will be deleted.',
    'willDelete.other': '{n} private items will be deleted.',
    'deleting': 'Deleting...',
    'delete': 'Delete my data',
    'policyNotHttps':
        'The policy link is not an https address, so it was copied instead of opened.',
    'policyBrowserFailed':
        'The browser could not open the policy. The link was copied; paste it into a browser.',
    'noPolicy':
        'Your organization has not published a privacy policy link yet. Ask the workspace owner for it.',
    'readPolicy': 'Read the privacy policy',
    'copyPolicy': 'Copy privacy policy link',
    'policyCopied': 'Privacy policy link copied.',
    'ret.conversations': 'Assistant conversations',
    'ret.conversationsDays':
        'Deleted after {days} without activity, or when you delete them.',
    'ret.conversationsKept': 'Kept until you delete them.',
    'ret.voice': 'Voice transcripts',
    'ret.voiceText':
        'You choose per session: removed when the session ends, after {days}, or kept until you delete them.',
    'ret.private': 'Private memories',
    'ret.task': 'Task memories',
    'ret.audit': 'Audit log',
    'days.one': '{n} day',
    'days.two': '{n} days',
    'days.few': '{n} days',
    'days.many': '{n} days',
    'days.other': '{n} days',
    'provider':
        'To produce answers, your messages, voice questions and task prompts are sent to {provider}.',
    'provider.anthropic': 'Anthropic (Claude), an external service',
    'provider.openai':
        'an OpenAI-compatible service, external unless your administrator runs it',
    'provider.ollama':
        'Ollama, a model running on your organization’s own server',
    'provider.mock':
        'no model provider (local development mock: nothing leaves the server)',
    'memory.on':
        'After each reply, a second short call to the same provider picks out lasting facts you stated (such as your name, role or how you like answers) and saves them as private memories only you can see. Memories that match your later questions are added to what is sent to the model.',
    'memory.off_by_user':
        'You turned off Remember things from my chats, so no facts are picked out of your chats and no memories are added to your questions. Memories already saved from your chats stay until you delete them.',
    'memory.off_on_server':
        'Chat memory is turned off on this server (ANUM_CONVERSATION_MEMORY_ENABLED), so no facts are picked out of your chats and no memories are added to your questions.',
    'memory.off_for_organization':
        'Your organization’s memory policy does not allow memories from chats, so no facts are picked out of your chats. {recall} An organization administrator can allow the "conversation" source under Governance.',
    'memory.not_for_role':
        'Your role in this workspace cannot save memories, so no facts are picked out of your chats. {recall} A workspace owner can change your role.',
    'memory.unknown':
        'This server did not say whether facts are picked out of your chats. Check Remember things from my chats in Settings, or update the server.',
    'memory.recall':
        'Memories you can read that match your question (ones you or your workspace saved) are still added to what is sent to the model.',
    'export.cancelled': 'Download cancelled. Nothing was saved.',
    'export.saved': 'Saved your data ({size}).',
    'result.deleted.one': 'Deleted {n} private item.',
    'result.deleted.two': 'Deleted {n} private items.',
    'result.deleted.few': 'Deleted {n} private items.',
    'result.deleted.many': 'Deleted {n} private items.',
    'result.deleted.other': 'Deleted {n} private items.',
    'result.nothing': 'There was nothing private to delete.',
    'result.stays.one': '{n} shared workspace record stays.',
    'result.stays.two': '{n} shared workspace records stay.',
    'result.stays.few': '{n} shared workspace records stay.',
    'result.stays.many': '{n} shared workspace records stay.',
    'result.stays.other': '{n} shared workspace records stay.',
    'error.signIn': 'Your sign-in expired. Sign in again, then retry.',
    'error.offline': 'You are offline. Reconnect, then try again.',
    'error.load': 'Could not load your privacy summary',
    'error.export': 'Could not prepare your download',
    'error.delete': 'Could not delete your data',
    'error.fixDelete': 'Reload to see what is still stored, then try again.',
    'error.fixOther': 'Check your connection and try again.',
    'error.generic': '{what}: {reason}. {fix}',
    'tile.title': 'Your data and privacy',
    'tile.subtitle': 'See what is stored, download it or delete it.',
    'private.conversations': 'Assistant conversations',
    'private.conversation_turns': 'Messages in those conversations',
    'private.private_memories': 'Private memories (from your chats)',
    'private.retrieval_index_entries':
        'Search index entries for those memories',
    'private.voice_sessions': 'Voice sessions',
    'private.voice_transcript_segments': 'Voice transcript lines',
    'private.notification_preferences': 'Notification preferences',
    'private.user_preferences': 'Your preferences (all workspaces)',
    'private.push_devices': 'Phones registered for push',
    'private.learning_signals': 'Feedback ANUM learned from',
    'private.learned_items': 'Learned style, preferences, routines and notes',
    'private.favorites': 'Your favorites',
    'private.private_collections': 'Private knowledge collections',
    'private.private_reports': 'Private reports',
    'private.email_mailboxes': 'Private mailboxes you connected',
    'private.email_messages': 'Email details synced from those mailboxes',
    'private.email_drafts': 'Email drafts you never sent',
    'private.team_notifications': 'Your team notifications',
    'private.team_profile': 'Your team profile (name, email, title)',
    'private.private_agents': 'Your private agents',
    'private.private_agent_memories': 'Memories of your private agents',
    'private.finance_records': 'Finance records',
    'private.app_calls':
        'Your calls with teammates (the other person keeps theirs, showing you as a deleted user)',
    'private.app_call_signals': 'Call setup messages',
    'shared.tasks_created': 'Tasks you created (with their runs)',
    'shared.approvals_decided': 'Approvals you decided',
    'shared.shared_memories_created': 'Shared memories you added',
    'shared.files_uploaded': 'Files you uploaded',
    'shared.email_mailboxes_connected': 'Workspace mailboxes you connected',
    'shared.email_drafts_sent': 'Emails you sent or sent for approval',
    'shared.whatsapp_messages_sent': 'WhatsApp messages you sent',
    'shared.autonomy_actions_requested': 'Actions ANUM took for you',
    'shared.work_items_created': 'Work items you created',
    'shared.work_items_assigned': 'Work items assigned to you',
    'shared.tyre_connections_created': 'Tyre data connection you set up',
    'shared.tyre_reports_created': 'Tyre reports you created',
    'shared.commerce_connectors_created': 'Sales channels you connected',
    'shared.marketing_accounts_connected': 'Social accounts you connected',
    'shared.marketing_campaigns_created': 'Marketing campaigns you created',
    'shared.calendar_events_created': 'Calendar events you created',
    'shared.automation_triggers_created': 'Automation triggers you added',
    'shared.shared_agents_owned': 'Agents you shared with the workspace',
    'shared.shared_agent_memories': 'Memories of the agents you shared',
  };

  static const ar = <String, String>{
    'title': 'الخصوصية',
    'loading': 'جارٍ تحميل ملخص خصوصيتك',
    'permissionTitle': 'الإذن مطلوب',
    'permission':
        'لا يرى بياناته هنا أو يغيّرها إلا الأعضاء النشطون في مساحة العمل هذه. اطلب من أحد مالكي مساحة العمل استعادة عضويتك، أو انتقل إلى مساحة عمل تنتمي إليها.',
    'offlineTitle': 'أنت غير متصل',
    'offlineText': 'أعد الاتصال لترى بياناتك وتديرها.',
    'unavailable': 'ملخص الخصوصية غير متاح',
    'retry': 'إعادة المحاولة',
    'storesHeading': 'ما يحفظه ANUM عنك',
    'signedIn':
        'مسجّل الدخول باسم {user}{role} في {workspace}. لكل مساحة عمل أخرى بياناتها الخاصة.',
    'role': ' ({role})',
    'privateTitle': 'خاص بك',
    'nothingPrivate': 'لا يُحفظ لك هنا أي شيء خاص بعد.',
    'sharedTitle': 'مشترك مع مساحة العمل',
    'keptHeading': 'مدة الاحتفاظ بها، وإلى أين تذهب',
    'changeMemory': 'تغيير تذكُّر أشياء من محادثاتي',
    'downloadHeading': 'تنزيل بياناتي',
    'downloadText':
        'ملف JSON يضم ملفك في مساحة العمل هذه، والمحادثات، والذكريات الخاصة، ونصوص الجلسات الصوتية، والمهام التي أنشأتها، والموافقات التي قررتها، وتفضيلات الإشعارات، وتفضيلاتك، وقائمة الملفات التي رفعتها، وما تعلّمه ANUM منك، وصناديق البريد التي ربطتها مع مسودات بريدك، ورسائل واتساب التي أرسلتها، والإجراءات التي اتخذها ANUM لك، وعمل فريقك، وتقارير الإطارات وقنوات البيع التي أعددتها. حتى {max}.',
    'preparing': 'جارٍ تجهيز التنزيل...',
    'download': 'تنزيل بياناتي',
    'deleteHeading': 'حذف بياناتي',
    'deleteText':
        'يحذف نهائيًا محادثاتك، وذكرياتك الخاصة (ومدخلات فهرس البحث الخاصة بها)، والجلسات الصوتية ونصوصها، وتفضيلات الإشعارات، وما تعلّمه ANUM منك، وصناديق البريد الخاصة التي ربطتها (ويسحب ANUM أيضًا صلاحيته لدى المزوّد)، ومسودات البريد التي لم ترسلها، وإشعارات فريقك وملفك في الفريق، ووكلاءك الخاصين مع ذكرياتهم في مساحة العمل هذه، ويعيد ضبط تفضيلاتك (مثل تذكُّر أشياء من محادثاتي) في كل مساحات العمل في هذه المؤسسة. لا يمكن التراجع عن ذلك.',
    'keptNone':
        'يبقى: سجلات مساحة العمل المشتركة (لم تنشئ أيًا منها). يمكن لمالك مساحة العمل إزالتها.',
    'kept.one':
        'يبقى: سجل واحد مشترك في مساحة العمل أنشأته أو قررته. يمكن لمالك مساحة العمل إزالته.',
    'kept.two':
        'يبقى: سجلان مشتركان في مساحة العمل أنشأتهما أو قررتهما. يمكن لمالك مساحة العمل إزالتهما.',
    'kept.few':
        'يبقى: {n} سجلات مشتركة في مساحة العمل أنشأتها أو قررتها. يمكن لمالك مساحة العمل إزالتها.',
    'kept.many':
        'يبقى: {n} سجلًا مشتركًا في مساحة العمل أنشأته أو قررته. يمكن لمالك مساحة العمل إزالتها.',
    'kept.other':
        'يبقى: {n} سجل مشترك في مساحة العمل أنشأته أو قررته. يمكن لمالك مساحة العمل إزالتها.',
    'account': 'حسابك: {note}',
    'typeToConfirm': 'اكتب {phrase} للتأكيد',
    'nothingNow': 'لا يُحفظ لك أي شيء خاص الآن.',
    'willDelete.one': 'سيُحذف عنصر خاص واحد.',
    'willDelete.two': 'سيُحذف عنصران خاصان.',
    'willDelete.few': 'ستُحذف {n} عناصر خاصة.',
    'willDelete.many': 'سيُحذف {n} عنصرًا خاصًا.',
    'willDelete.other': 'سيُحذف {n} عنصر خاص.',
    'deleting': 'جارٍ الحذف...',
    'delete': 'حذف بياناتي',
    'policyNotHttps': 'رابط السياسة ليس عنوان https، لذلك نُسخ بدلًا من فتحه.',
    'policyBrowserFailed':
        'تعذر على المتصفح فتح السياسة. نُسخ الرابط؛ الصقه في متصفح.',
    'noPolicy':
        'لم تنشر مؤسستك رابط سياسة الخصوصية بعد. اطلبه من مالك مساحة العمل.',
    'readPolicy': 'قراءة سياسة الخصوصية',
    'copyPolicy': 'نسخ رابط سياسة الخصوصية',
    'policyCopied': 'نُسخ رابط سياسة الخصوصية.',
    'ret.conversations': 'محادثات المساعد',
    'ret.conversationsDays': 'تُحذف بعد {days} دون نشاط، أو عندما تحذفها.',
    'ret.conversationsKept': 'تبقى حتى تحذفها.',
    'ret.voice': 'نصوص الجلسات الصوتية',
    'ret.voiceText':
        'تختار لكل جلسة: تُزال عند انتهاء الجلسة، أو بعد {days}، أو تبقى حتى تحذفها.',
    'ret.private': 'الذكريات الخاصة',
    'ret.task': 'ذكريات المهام',
    'ret.audit': 'سجل التدقيق',
    'days.one': 'يوم واحد',
    'days.two': 'يومين',
    'days.few': '{n} أيام',
    'days.many': '{n} يومًا',
    'days.other': '{n} يوم',
    'provider':
        'لإنتاج الإجابات، تُرسل رسائلك وأسئلتك الصوتية وأوامر المهام إلى {provider}.',
    'provider.anthropic': 'Anthropic (Claude)، وهي خدمة خارجية',
    'provider.openai': 'خدمة متوافقة مع OpenAI، خارجية ما لم يشغّلها مسؤولك',
    'provider.ollama': 'Ollama، نموذج يعمل على خادم مؤسستك',
    'provider.mock':
        'لا يوجد مزوّد نماذج (نموذج تطوير محلي: لا شيء يغادر الخادم)',
    'memory.on':
        'بعد كل رد، يستخرج طلب قصير ثانٍ إلى المزوّد نفسه حقائق دائمة ذكرتها (مثل اسمك أو دورك أو طريقة الإجابة التي تفضلها) ويحفظها ذكرياتٍ خاصة لا يراها غيرك. تُضاف الذكريات التي تطابق أسئلتك اللاحقة إلى ما يُرسل إلى النموذج.',
    'memory.off_by_user':
        'أوقفت تذكُّر أشياء من محادثاتي، لذلك لا تُستخرج حقائق من محادثاتك ولا تُضاف ذكريات إلى أسئلتك. تبقى الذكريات المحفوظة من محادثاتك حتى تحذفها.',
    'memory.off_on_server':
        'ذاكرة المحادثات متوقفة على هذا الخادم (ANUM_CONVERSATION_MEMORY_ENABLED)، لذلك لا تُستخرج حقائق من محادثاتك ولا تُضاف ذكريات إلى أسئلتك.',
    'memory.off_for_organization':
        'سياسة الذاكرة في مؤسستك لا تسمح بذكريات من المحادثات، لذلك لا تُستخرج حقائق من محادثاتك. {recall} يمكن لمسؤول المؤسسة السماح بالمصدر "conversation" في الحوكمة.',
    'memory.not_for_role':
        'لا يستطيع دورك في مساحة العمل هذه حفظ الذكريات، لذلك لا تُستخرج حقائق من محادثاتك. {recall} يمكن لمالك مساحة العمل تغيير دورك.',
    'memory.unknown':
        'لم يذكر هذا الخادم ما إذا كانت تُستخرج حقائق من محادثاتك. تحقق من تذكُّر أشياء من محادثاتي في الإعدادات، أو حدّث الخادم.',
    'memory.recall':
        'لا تزال الذكريات التي يمكنك قراءتها وتطابق سؤالك (التي حفظتها أنت أو مساحة عملك) تُضاف إلى ما يُرسل إلى النموذج.',
    'export.cancelled': 'أُلغي التنزيل. لم يُحفظ شيء.',
    'export.saved': 'حُفظت بياناتك ({size}).',
    'result.deleted.one': 'حُذف عنصر خاص واحد.',
    'result.deleted.two': 'حُذف عنصران خاصان.',
    'result.deleted.few': 'حُذفت {n} عناصر خاصة.',
    'result.deleted.many': 'حُذف {n} عنصرًا خاصًا.',
    'result.deleted.other': 'حُذف {n} عنصر خاص.',
    'result.nothing': 'لم يكن هناك شيء خاص لحذفه.',
    'result.stays.one': 'يبقى سجل واحد مشترك في مساحة العمل.',
    'result.stays.two': 'يبقى سجلان مشتركان في مساحة العمل.',
    'result.stays.few': 'تبقى {n} سجلات مشتركة في مساحة العمل.',
    'result.stays.many': 'يبقى {n} سجلًا مشتركًا في مساحة العمل.',
    'result.stays.other': 'يبقى {n} سجل مشترك في مساحة العمل.',
    'error.signIn': 'انتهت جلسة الدخول. سجّل الدخول مرة أخرى، ثم أعد المحاولة.',
    'error.offline': 'أنت غير متصل. أعد الاتصال، ثم حاول مرة أخرى.',
    'error.load': 'تعذر تحميل ملخص خصوصيتك',
    'error.export': 'تعذر تجهيز التنزيل',
    'error.delete': 'تعذر حذف بياناتك',
    'error.fixDelete': 'أعد التحميل لترى ما لا يزال محفوظًا، ثم حاول مرة أخرى.',
    'error.fixOther': 'تحقق من اتصالك وحاول مرة أخرى.',
    'error.generic': '{what}: {reason}. {fix}',
    'tile.title': 'بياناتك وخصوصيتك',
    'tile.subtitle': 'اطّلع على ما يُحفظ، أو نزّله، أو احذفه.',
    'private.conversations': 'محادثات المساعد',
    'private.conversation_turns': 'الرسائل في تلك المحادثات',
    'private.private_memories': 'الذكريات الخاصة (من محادثاتك)',
    'private.retrieval_index_entries': 'مدخلات فهرس البحث لتلك الذكريات',
    'private.voice_sessions': 'الجلسات الصوتية',
    'private.voice_transcript_segments': 'أسطر نصوص الجلسات الصوتية',
    'private.notification_preferences': 'تفضيلات الإشعارات',
    'private.user_preferences': 'تفضيلاتك (كل مساحات العمل)',
    'private.push_devices': 'الهواتف المسجلة للإشعارات',
    'private.learning_signals': 'الملاحظات التي تعلّم منها ANUM',
    'private.learned_items':
        'الأسلوب والتفضيلات والعادات والملاحظات المتعلَّمة',
    'private.favorites': 'مفضلاتك',
    'private.private_collections': 'مجموعات المعرفة الخاصة',
    'private.private_reports': 'التقارير الخاصة',
    'private.email_mailboxes': 'صناديق البريد الخاصة التي ربطتها',
    'private.email_messages': 'تفاصيل البريد المزامنة من تلك الصناديق',
    'private.email_drafts': 'مسودات البريد التي لم ترسلها',
    'private.team_notifications': 'إشعارات فريقك',
    'private.team_profile': 'ملفك في الفريق (الاسم والبريد والمسمى)',
    'private.private_agents': 'وكلاؤك الخاصون',
    'private.private_agent_memories': 'ذكريات وكلائك الخاصين',
    'private.finance_records': 'السجلات المالية',
    'private.app_calls':
        'مكالماتك مع زملائك (يحتفظ الطرف الآخر بمكالماته، ويظهر اسمك مستخدمًا محذوفًا)',
    'private.app_call_signals': 'رسائل إعداد المكالمات',
    'shared.tasks_created': 'المهام التي أنشأتها (مع تشغيلاتها)',
    'shared.approvals_decided': 'الموافقات التي قررتها',
    'shared.shared_memories_created': 'الذكريات المشتركة التي أضفتها',
    'shared.files_uploaded': 'الملفات التي رفعتها',
    'shared.email_mailboxes_connected': 'صناديق بريد مساحة العمل التي ربطتها',
    'shared.email_drafts_sent': 'رسائل البريد التي أرسلتها أو أرسلتها للموافقة',
    'shared.whatsapp_messages_sent': 'رسائل واتساب التي أرسلتها',
    'shared.autonomy_actions_requested': 'الإجراءات التي اتخذها ANUM لك',
    'shared.work_items_created': 'بنود العمل التي أنشأتها',
    'shared.work_items_assigned': 'بنود العمل المسندة إليك',
    'shared.tyre_connections_created': 'اتصال بيانات الإطارات الذي أعددته',
    'shared.tyre_reports_created': 'تقارير الإطارات التي أنشأتها',
    'shared.commerce_connectors_created': 'قنوات البيع التي ربطتها',
    'shared.marketing_accounts_connected': 'حسابات التواصل التي ربطتها',
    'shared.marketing_campaigns_created': 'الحملات التسويقية التي أنشأتها',
    'shared.calendar_events_created': 'أحداث التقويم التي أنشأتها',
    'shared.automation_triggers_created': 'مشغّلات الأتمتة التي أضفتها',
    'shared.shared_agents_owned': 'الوكلاء الذين شاركتهم مع مساحة العمل',
    'shared.shared_agent_memories': 'ذكريات الوكلاء الذين شاركتهم',
  };
}
