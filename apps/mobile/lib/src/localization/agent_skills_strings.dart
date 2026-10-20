/// Agent skills strings (the Skills tab of an agent's page, docs/agents.md#skills), English and
/// Arabic. Looked up by `AnumLocalizations.t` after the main catalogs. Same keys and placeholders
/// in both; test/agents/agent_skills_test.dart checks it.
const agentSkillsEn = <String, String>{
  'agentSkills.title': 'Skills this agent loads',
  'agentSkills.intro':
      'Installed skills add their instructions to every task and chat. Built-in skills join when a task asks for them. Skills never add tools the agent is not allowed to use.',
  'agentSkills.summary': 'Ready: {ready}. Needs attention: {attention}.',
  'agentSkills.refresh': 'Refresh skills',
  'agentSkills.add': 'Add a skill',
  'agentSkills.addTitle': 'Add a skill',
  'agentSkills.done': 'Done',
  'agentSkills.search': 'Search loaded skills',
  'agentSkills.searchAdd': 'Search available skills',
  'agentSkills.searchPlaceholder': 'Name, id or tool',
  'agentSkills.addNone':
      'No skills are available in this workspace yet. A workspace owner installs them in Skills.',
  'agentSkills.addEmpty': 'This agent already loads every available skill.',
  'agentSkills.addNoMatch': 'No available skill matches “{query}”.',
  'agentSkills.addTools': 'Also allows the tool: {tools}',
  'agentSkills.addNamed': 'Add {name}',
  'agentSkills.addButton': 'Add',
  'agentSkills.openSkills': 'Open Skills',
  'agentSkills.loading': 'Loading the skills this agent loads',
  'agentSkills.loadFailed':
      'Could not load this agent’s skills: {reason} Check that the API is reachable, then try again.',
  'agentSkills.saveFailed': 'The skills did not change: {reason}',
  'agentSkills.readOnly':
      'Only this agent’s creator, or a workspace owner for a shared agent, can change its skills.',
  'agentSkills.emptyTitle': 'No skills loaded',
  'agentSkills.emptyText':
      'Without skills it plans with the built-in runtime only. Add a skill to give it reviewed instructions and the tools they need.',
  'agentSkills.emptyReadOnly':
      'Without skills it plans with the built-in runtime only. Its creator, or a workspace owner, can add skills.',
  'agentSkills.noMatch': 'No loaded skill matches “{query}”.',
  'agentSkills.added': '{name} added. The next task and chat load it.',
  'agentSkills.removed': '{name} removed. Tasks that already started keep it.',
  'agentSkills.allowed': 'Allowed {tools} on this agent.',
  'agentSkills.allow': 'Allow {tools}',
  'agentSkills.remove': 'Remove',
  'agentSkills.removeNamed': 'Remove {name} from this agent',
  'agentSkills.source.builtin': 'Built in',
  'agentSkills.source.installed': 'Installed',
  'agentSkills.source.unknown': 'Unknown skill',
  'agentSkills.state.ready': 'Ready',
  'agentSkills.state.needs_tools': 'Needs tools',
  'agentSkills.state.disabled': 'Turned off',
  'agentSkills.state.not_installed': 'Not installed',
  'agentSkills.state.blocked': 'Blocked',
  'agentSkills.state.unavailable': 'Unavailable',
  'agentSkills.version': 'Version {version}',
  'agentSkills.risk': 'Risk {risk}',
  'agentSkills.loadsAlways':
      'Loads its instructions into every task and chat with this agent.',
  'agentSkills.loadsOnRequest': 'Joins a task when you ask about: {words}',
  'agentSkills.loadsOnRequestAny': 'Joins every task this agent plans.',
  'agentSkills.toolsOf': 'Tools {name} needs',
  'agentSkills.tool.ready': 'ready',
  'agentSkills.tool.missing': 'not in this deployment',
  'agentSkills.tool.notGranted': 'not granted by the installation',
  'agentSkills.tool.notAllowed': 'not allowed on this agent',
  'agentSkills.upgrade':
      'Version {version} is available. A workspace owner upgrades it in Skills.',
  'agentSkills.reason.disabled':
      'A workspace owner turned this skill off in Skills. Agents load it again once it is enabled.',
  'agentSkills.reason.notInstalled':
      'It is not installed in this workspace. A workspace owner installs it in Skills.',
  'agentSkills.reason.blocked':
      'This version is marked blocked, so the runtime never loads it.',
  'agentSkills.reason.notGranted':
      'The installation did not grant {tools}. A workspace owner reinstalls it with that tool.',
  'agentSkills.reason.notAllowed':
      'Allow {tools} on this agent so the skill can run.',
  'agentSkills.reason.missingTool':
      'This deployment does not have {tools}. An operator adds it with its connector.',
  'agentSkills.reason.unknown':
      'No skill with this id exists here any more. Remove it from the agent.',
  'agentSkills.stale':
      'Someone changed this agent since you opened it. The page was reloaded: check the skills below, then apply your change again.',
  'agentSkills.catalogLoading': 'Loading the available skills',
  'agentSkills.catalogFailed': 'Could not load the available skills: {reason}',
  'agentSkills.retry': 'Try again',
  'agentSkills.toolState': '{tool}: {state}',
  'agentSkills.listSeparator': ', ',
  'agentSkills.riskLevel.low': 'low',
  'agentSkills.riskLevel.medium': 'medium',
  'agentSkills.riskLevel.high': 'high',
  'agentSkills.riskLevel.blocked': 'blocked',
};

const agentSkillsAr = <String, String>{
  'agentSkills.title': 'المهارات التي يحمّلها هذا الوكيل',
  'agentSkills.intro':
      'تضيف المهارات المثبتة تعليماتها إلى كل مهمة ومحادثة. وتنضم المهارات المدمجة عندما تطلبها المهمة. لا تضيف المهارات أدوات غير مسموح للوكيل باستخدامها.',
  'agentSkills.summary': 'جاهزة: {ready}. تحتاج إلى انتباه: {attention}.',
  'agentSkills.refresh': 'تحديث المهارات',
  'agentSkills.add': 'إضافة مهارة',
  'agentSkills.addTitle': 'إضافة مهارة',
  'agentSkills.done': 'تم',
  'agentSkills.search': 'البحث في المهارات المحمّلة',
  'agentSkills.searchAdd': 'البحث في المهارات المتاحة',
  'agentSkills.searchPlaceholder': 'الاسم أو المعرّف أو الأداة',
  'agentSkills.addNone':
      'لا توجد مهارات متاحة في مساحة العمل هذه بعد. يثبّتها مالك مساحة العمل من صفحة المهارات.',
  'agentSkills.addEmpty': 'يحمّل هذا الوكيل كل المهارات المتاحة بالفعل.',
  'agentSkills.addNoMatch': 'لا توجد مهارة متاحة تطابق «{query}».',
  'agentSkills.addTools': 'يسمح أيضًا بالأداة: {tools}',
  'agentSkills.addNamed': 'إضافة {name}',
  'agentSkills.addButton': 'إضافة',
  'agentSkills.openSkills': 'فتح المهارات',
  'agentSkills.loading': 'جارٍ تحميل مهارات هذا الوكيل',
  'agentSkills.loadFailed':
      'تعذّر تحميل مهارات هذا الوكيل: {reason} تحقّق من إمكانية الوصول إلى الواجهة البرمجية ثم أعد المحاولة.',
  'agentSkills.saveFailed': 'لم تتغيّر المهارات: {reason}',
  'agentSkills.readOnly':
      'لا يغيّر مهارات هذا الوكيل إلا منشئه، أو مالك مساحة العمل إذا كان الوكيل مشتركًا.',
  'agentSkills.emptyTitle': 'لا توجد مهارات محمّلة',
  'agentSkills.emptyText':
      'بلا مهارات يخطط ببيئة التشغيل المدمجة فقط. أضف مهارة لتمنحه تعليمات مراجَعة والأدوات التي تحتاجها.',
  'agentSkills.emptyReadOnly':
      'بلا مهارات يخطط ببيئة التشغيل المدمجة فقط. يستطيع منشئه أو مالك مساحة العمل إضافة مهارات.',
  'agentSkills.noMatch': 'لا توجد مهارة محمّلة تطابق «{query}».',
  'agentSkills.added': 'أُضيفت {name}. تحمّلها المهمة والمحادثة التاليتان.',
  'agentSkills.removed': 'أُزيلت {name}. تحتفظ بها المهام التي بدأت بالفعل.',
  'agentSkills.allowed': 'سُمح بالأداة {tools} لهذا الوكيل.',
  'agentSkills.allow': 'السماح بـ {tools}',
  'agentSkills.remove': 'إزالة',
  'agentSkills.removeNamed': 'إزالة {name} من هذا الوكيل',
  'agentSkills.source.builtin': 'مدمجة',
  'agentSkills.source.installed': 'مثبّتة',
  'agentSkills.source.unknown': 'مهارة غير معروفة',
  'agentSkills.state.ready': 'جاهزة',
  'agentSkills.state.needs_tools': 'تحتاج إلى أدوات',
  'agentSkills.state.disabled': 'متوقفة',
  'agentSkills.state.not_installed': 'غير مثبّتة',
  'agentSkills.state.blocked': 'محظورة',
  'agentSkills.state.unavailable': 'غير متاحة',
  'agentSkills.version': 'الإصدار {version}',
  'agentSkills.risk': 'المخاطر {risk}',
  'agentSkills.loadsAlways':
      'تحمّل تعليماتها في كل مهمة ومحادثة مع هذا الوكيل.',
  'agentSkills.loadsOnRequest': 'تنضم إلى المهمة عندما تسأل عن: {words}',
  'agentSkills.loadsOnRequestAny': 'تنضم إلى كل مهمة يخطط لها هذا الوكيل.',
  'agentSkills.toolsOf': 'الأدوات التي تحتاجها {name}',
  'agentSkills.tool.ready': 'جاهزة',
  'agentSkills.tool.missing': 'غير موجودة في هذا النشر',
  'agentSkills.tool.notGranted': 'لم يمنحها التثبيت',
  'agentSkills.tool.notAllowed': 'غير مسموح بها لهذا الوكيل',
  'agentSkills.upgrade':
      'الإصدار {version} متاح. يرقّيه مالك مساحة العمل من صفحة المهارات.',
  'agentSkills.reason.disabled':
      'أوقف مالك مساحة العمل هذه المهارة من صفحة المهارات. يحمّلها الوكلاء مجددًا بعد تفعيلها.',
  'agentSkills.reason.notInstalled':
      'ليست مثبّتة في مساحة العمل هذه. يثبّتها مالك مساحة العمل من صفحة المهارات.',
  'agentSkills.reason.blocked':
      'هذا الإصدار محظور، لذلك لا تحمّله بيئة التشغيل أبدًا.',
  'agentSkills.reason.notGranted':
      'لم يمنح التثبيت {tools}. يعيد مالك مساحة العمل تثبيتها مع تلك الأداة.',
  'agentSkills.reason.notAllowed': 'اسمح بـ {tools} لهذا الوكيل لتعمل المهارة.',
  'agentSkills.reason.missingTool':
      'لا يتوفر {tools} في هذا النشر. يضيفها المشغّل مع الموصل الخاص بها.',
  'agentSkills.reason.unknown':
      'لم تعد هناك مهارة بهذا المعرّف. أزلها من الوكيل.',
  'agentSkills.stale':
      'غيّر شخص آخر هذا الوكيل بعد أن فتحته. أُعيد تحميل الصفحة: راجع المهارات أدناه ثم طبّق تغييرك مرة أخرى.',
  'agentSkills.catalogLoading': 'جارٍ تحميل المهارات المتاحة',
  'agentSkills.catalogFailed': 'تعذّر تحميل المهارات المتاحة: {reason}',
  'agentSkills.retry': 'حاول مرة أخرى',
  'agentSkills.toolState': '{tool}: {state}',
  'agentSkills.listSeparator': '، ',
  'agentSkills.riskLevel.low': 'منخفضة',
  'agentSkills.riskLevel.medium': 'متوسطة',
  'agentSkills.riskLevel.high': 'عالية',
  'agentSkills.riskLevel.blocked': 'محظورة',
};
