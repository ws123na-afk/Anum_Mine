/// Admin and workspace depth on the phone, English and Arabic: private tasks
/// (docs/tasks.md#private-tasks), the spend breakdown and alert threshold on
/// Model budgets (docs/model-gateway.md#spend-breakdown), the Approvals
/// settings sections (docs/approvals-and-risk.md#approvals-settings),
/// workspace memories (docs/memory.md#workspace-memories), the policy pack
/// editor and role templates (docs/governance-and-scale.md). The wording
/// follows the web catalogs (`adminDepth`, `routing`, `en.governance`,
/// `en.owner`). Looked up by `AnumLocalizations.t` after the main catalogs;
/// both maps hold the same keys (test/admin/admin_depth_test.dart).
const adminDepthEn = <String, String>{
  // ---- Private tasks ----
  'ad.task.private': 'Private to you',
  'ad.task.privateHint':
      'Only you can see this task, its runs and its results. It came from your private chat, or it read your own data.',
  'ad.task.private.data':
      'It read your own data (Finance or your own mailbox), so it stays private for good and you decide its approvals yourself.',
  'ad.task.private.chat':
      'It came from your private chat. If a run waits for someone else\'s approval, it is shared with the workspace at that moment.',

  // ---- Favorites: search, sort and the person's own order ----
  'ad.fav.search': 'Search favorites',
  'ad.fav.searchHint': 'Name of a starred item',
  'ad.fav.clearSearch': 'Clear search',
  'ad.fav.sort': 'Sort',
  'ad.fav.sort.custom': 'My order',
  'ad.fav.sort.newest': 'Newest first',
  'ad.fav.sort.oldest': 'Oldest first',
  'ad.fav.sort.name': 'Name, A to Z',
  'ad.fav.arrangeHint':
      'Drag a row by its handle, or use Move up and Move down, to put your favorites in your own order. It is saved for every device.',
  'ad.fav.arrangeOff': 'To arrange favorites by hand, sort by {sort}.',
  'ad.fav.noMatch': 'Nothing starred matches "{query}"',
  'ad.fav.noMatchText':
      'Search looks at the names of starred items. Try another word or clear the search.',
  'ad.fav.noneOfType': 'Nothing of this type is starred',
  'ad.fav.noneOfTypeText': 'Star one where it lives, or show every type.',
  'ad.fav.showAll': 'Show every type',
  'ad.fav.arrange': 'Arrange {name}',
  'ad.fav.drag': 'Drag {name} to move it',
  'ad.fav.moveUp': 'Move up',
  'ad.fav.moveDown': 'Move down',
  'ad.fav.movedTo': 'Moved {name} {where} {other}.',
  'ad.fav.before': 'before',
  'ad.fav.after': 'after',
  'ad.fav.moveFailed':
      'Could not save the new order: {message} The list is back as it was.',

  // ---- Budgets: alert threshold ----
  'ad.bud.alertPercent': 'Alert owners at (%)',
  'ad.bud.alertHint':
      'Owners are told in the app at 50, 75, 90 and 100 percent of a limit. Add your own threshold here, 1 to 99.',
  'ad.bud.err.alert':
      'The alert threshold must be a whole number from 1 to 99, or empty for the fixed alerts only.',
  'ad.bud.err.alertNeedsLimit':
      'An alert threshold needs a cost or token limit to measure against.',
  'ad.bud.alertAt': 'Owners are also alerted at {percent}%.',

  // ---- Budgets: where the spend goes ----
  'ad.use.eyebrow': 'Recorded spend',
  'ad.use.title': 'Where the spend goes',
  'ad.use.intro':
      'Every model call the budget metered, grouped as you choose. Nothing here is estimated beyond the price of each call.',
  'ad.use.groupBy': 'Group by',
  'ad.use.group.day': 'Day',
  'ad.use.group.month': 'Month',
  'ad.use.group.user': 'Person',
  'ad.use.group.agent': 'Agent',
  'ad.use.group.feature': 'Feature',
  'ad.use.scope': 'Scope',
  'ad.use.scope.workspace': 'This workspace',
  'ad.use.scope.tenant': 'Whole organization',
  'ad.use.range': 'Period',
  'ad.use.range.month': 'This month',
  'ad.use.range.30d': 'Last 30 days',
  'ad.use.range.3m': 'Last 3 months',
  'ad.use.range.12m': 'Last 12 months',
  'ad.use.range.custom': 'Custom dates',
  'ad.use.pickDates': 'Choose dates',
  'ad.use.badRange':
      'The end date must not be before the start date, and the period may cover at most 400 days.',
  'ad.use.measure': 'Measure',
  'ad.use.measure.cost': 'Estimated cost',
  'ad.use.measure.tokens': 'Tokens',
  'ad.use.period': '{from} to {to}',
  'ad.use.loading': 'Loading the spend breakdown...',
  'ad.use.error': 'The spend breakdown could not be loaded. {message}',
  'ad.use.denied':
      'Only workspace owners see where the model spend goes. Ask an owner of this workspace.',
  'ad.use.retry': 'Try again',
  'ad.use.empty': 'No model calls in this period',
  'ad.use.emptyText':
      'Nothing was metered for this scope between {from} and {to}. Pick a longer period or the whole organization.',
  'ad.use.longer': 'Show the last 12 months',
  'ad.use.recordedSince':
      'Breakdowns by day, person, agent and feature exist since {date}. Earlier spend is in the monthly totals: group by month to see it.',
  'ad.use.showing': 'Showing {shown} of {count} groups.',
  'ad.use.loadMore': 'Load more',
  'ad.use.moreFailed': 'More could not be loaded: {message}',
  'ad.use.export': 'Export CSV',
  'ad.use.exporting': 'Preparing the CSV...',
  'ad.use.exported': 'The CSV was saved.',
  'ad.use.exportCancelled': 'The CSV was not saved.',
  'ad.use.exportFailed': 'The CSV could not be exported. {message}',
  'ad.use.total': 'Total',
  'ad.use.calls': 'Calls: {count}',
  'ad.use.tokens': 'Tokens: {count}',
  'ad.use.share': '{percent}% of the period',
  'ad.use.unpriced': 'Unpriced calls: {count}',
  'ad.use.rowLabel': '{label}: {value}, {share}',
  'ad.use.user.you': '{user} (you)',
  'ad.use.feature.tasks': 'Tasks and agent runs',
  'ad.use.feature.voice': 'Voice',
  'ad.use.feature.assistant': 'AI Chat',
  'ad.use.feature.email': 'Email',
  'ad.use.feature.knowledge': 'Knowledge base and retrieval',
  'ad.use.feature.meetings': 'Meetings',
  'ad.use.feature.business': 'Business',
  'ad.use.feature.marketing': 'Marketing',
  'ad.use.feature.finance': 'Finance',
  'ad.use.feature.calls': 'Phone calls',
  'ad.use.feature.speech': 'Speech recognition and synthesis',
  'ad.use.feature.browser': 'Browser research and actions',
  'ad.use.feature.attendance': 'Attendance',
  'ad.use.feature.executive': 'Executive PA',
  'ad.use.feature.whatsapp': 'WhatsApp',
  'ad.use.feature.commerce': 'Commerce',
  'ad.use.feature.developer': 'Developer mode',
  'ad.use.feature.other': 'Other',

  // ---- Approvals settings: sections ----
  'ad.apr.rules.eyebrow': 'Organization rules',
  'ad.apr.rules.title': 'Approval rules',
  'ad.apr.rules.intro':
      'Rules apply to every workspace of the organization: which tool patterns pause, the AED bands, and who approves each step.',
  'ad.apr.rules.loading': 'Loading the approval rules...',
  'ad.apr.rules.error': 'The approval rules could not be loaded. {message}',
  'ad.apr.rules.denied':
      'Approval rules are shown to members of this workspace. Ask a workspace owner for access.',
  'ad.apr.rules.empty':
      'No organization rules yet: every action that pauses goes to the workspace owners.',
  'ad.apr.rules.readOnly':
      'Rules are shown here and changed on the web Governance page by an organization administrator.',
  'ad.apr.rules.manage': 'Edit rules on the web',
  'ad.apr.rules.webHint':
      'Open ANUM on the web, then Governance, Approval rules. Only an organization administrator can change them.',
  'ad.apr.rules.count': 'Rules: {count}, on: {enabled}',
  'ad.apr.rules.pattern': 'Applies to {pattern}',
  'ad.apr.rules.everyAmount': 'every amount',
  'ad.apr.rules.on': 'On',
  'ad.apr.rules.off': 'Off',
  'ad.apr.band.above': 'Above {amount}',
  'ad.apr.band.atMost': 'Up to {amount}',
  'ad.apr.band.between': 'Above {above}, up to {atMost}',
  'ad.apr.band.percent': 'At most {percent}% of the order',
  'ad.apr.band.disputed': 'Disputed only',
  'ad.apr.band.notDisputed': 'Not disputed',
  'ad.apr.step': 'Step {step}: {who}',
  'ad.apr.legacy': 'Owners decide ({roles}), {count} needed',
  'ad.apr.who.owners': 'Owners',
  'ad.apr.who.orgAdmins': 'Organization administrators',
  'ad.apr.who.members': 'Members',
  'ad.apr.or': '{head} or {last}',
  'ad.apr.routing.eyebrow': 'How a pausing action is routed',
  'ad.apr.routing.title': 'Routing',
  'ad.apr.routing.intro':
      'Read from the rules and the policy above, in the order the API applies them when an action pauses.',
  'ad.apr.routing.step.pause':
      'A high-risk tool call pauses. Medium-risk calls pause too while the policy switch is on.',
  'ad.apr.routing.step.match':
      'Rules whose tool pattern matches are read; the amounts of the task\'s other pending approvals under the same rule are added, so nothing is split.',
  'ad.apr.routing.step.band':
      'The first band that holds decides who approves, in one or two steps.',
  'ad.apr.routing.step.fallback':
      'Any amount no band covers, an unknown amount or another currency goes to the workspace owners.',
  'ad.apr.routing.step.owners':
      'Owners can always approve; everyone else only when the route names them for the current step. Nobody approves their own request.',
  'ad.apr.routing.bands': 'Bands in force',
  'ad.apr.routing.band': '{name}: {band}, {who}',
  'ad.apr.routing.noBands':
      'No rule routes by amount yet: workspace owners approve everything that pauses.',
  'ad.apr.routing.rulesUnknown':
      'The rules could not be read, so the bands in force are not shown.',
  'ad.apr.routing.off': 'Rules switched off, which do not route: {count}',
  'ad.apr.routing.twoPersonOn':
      'Two-person rule on: the person who created the task or started the run never approves its high-risk actions.',
  'ad.apr.routing.twoPersonOff':
      'Two-person rule off: one owner can approve the actions of a task they started.',
  'ad.apr.routing.mediumOn': 'Medium-risk actions pause for approval.',
  'ad.apr.routing.mediumOff': 'Medium-risk actions run without approval.',
  'ad.apr.routing.draft':
      'Shown with the switches as set above; save the policy to put them in force.',
  'ad.apr.rem.eyebrow': 'Waiting approvals',
  'ad.apr.rem.title': 'Remind approvers',
  'ad.apr.rem.help':
      'When an approval has waited this long, this workspace\'s owners get one reminder in the app (no email). Approvals still expire after 24 hours.',
  'ad.apr.rem.label': 'Send a reminder after',
  'ad.apr.rem.off': 'Off',
  'ad.apr.rem.hours': '{hours} hours',
  'ad.apr.rem.hour': '1 hour',
  'ad.apr.rem.save': 'Save reminder',
  'ad.apr.rem.saving': 'Saving...',
  'ad.apr.rem.saved': 'Approvals now remind owners after {hours} hours.',
  'ad.apr.rem.savedOff': 'Approval reminders are off.',
  'ad.apr.rem.denied':
      'Only a workspace owner can change when waiting approvals send a reminder.',
  'ad.apr.rem.loading': 'Loading the reminder setting...',
  'ad.apr.rem.error': 'The reminder setting could not be loaded: {message}',
  'ad.apr.rem.saveFailed': 'The reminder was not saved: {message}',
  'ad.apr.rem.updated': 'Changed {time} by {user}.',
  'ad.apr.rem.never': 'Never changed: reminders are off by default.',

  // ---- Memories ----
  'ad.mem.title': 'Teach your agents',
  'ad.mem.subtitle':
      'A memory belongs to a task, or to the whole workspace when no task fits.',
  'ad.mem.related': 'Belongs to',
  'ad.mem.wholeWorkspace': 'Whole workspace (no task)',
  'ad.mem.keep': 'Keep',
  'ad.mem.keep.indefinite': 'Until someone deletes it',
  'ad.mem.keep.days': 'For {count} days',
  'ad.mem.workspaceHint':
      'A workspace memory is shared with everyone here and belongs to no task. The organization\'s memory governance may shorten how long it is kept or refuse its source type.',
  'ad.mem.content': 'What should they remember?',
  'ad.mem.save': 'Save memory',
  'ad.mem.saved': 'Memory saved.',
  'ad.mem.workspaceNote': 'Workspace note',
  'ad.mem.privateNote': 'Private to you, from your chats',
  'ad.mem.fromTask': 'From: {task}',
  'ad.mem.expires': 'Kept until {date}',
  'ad.mem.edit': 'Edit memory',
  'ad.mem.editLabel': 'Memory text',
  'ad.mem.saveEdit': 'Save memory',
  'ad.mem.cancel': 'Cancel',
  'ad.mem.edited': 'Memory updated. The index follows the new text.',
  'ad.mem.editedBy': 'Edited {when} by {user}.',
  'ad.mem.editEmpty': 'A memory needs some text.',
  'ad.mem.editDenied':
      'Only the person who added this memory or a workspace owner can change it.',
  'ad.mem.editFailed': 'The memory was not changed: {message}',
  'ad.mem.showing': 'Memories shown: {count}',
  'ad.mem.loadMore': 'Load older memories',
  'ad.mem.loadingMore': 'Loading older memories...',
  'ad.mem.moreFailed': 'Older memories could not be loaded: {message}',
  'ad.mem.delete': 'Delete memory',
  'ad.mem.empty': 'No memories yet',
  'ad.mem.emptyText':
      'Add the first one above: a fact for one task, or a note for the whole workspace.',

  // ---- Governance: policy packs ----
  'ad.gov.packs.title': 'Policy packs',
  'ad.gov.packs.new': 'New policy pack',
  'ad.gov.packs.emptyAdmin':
      'No policy packs yet. Publish the first one: start from the organization baseline or from a blank pack.',
  'ad.gov.packs.emptyOther':
      'No policy packs yet. An organization administrator publishes the first one.',
  'ad.gov.packs.search': 'Search policy packs',
  'ad.gov.packs.noMatch': 'No policy pack matches "{query}".',
  'ad.gov.packs.shown': 'Packs shown: {shown} of {count}',
  'ad.gov.packs.showMore': 'Show more packs',
  'ad.gov.packs.versions': 'Versions: {count}',
  'ad.gov.packs.activeVersion': 'Active: v{version}',
  'ad.gov.packs.noActive': 'No active version',
  'ad.gov.packs.rules': 'Rules: {count}',
  'ad.gov.packs.active': 'Active',
  'ad.gov.packs.archived': 'Archived',
  'ad.gov.packs.activate': 'Activate',
  'ad.gov.packs.archive': 'Archive',
  'ad.gov.packs.newVersion': 'New version',
  'ad.gov.packs.compare': 'Compare versions',
  'ad.gov.packs.versionsTitle': 'Versions of {name}',
  'ad.gov.packs.version': 'v{version}',
  'ad.gov.packs.published': 'Published {when}',
  'ad.gov.packs.delete': 'Delete',
  'ad.gov.packs.deleteLabel': 'Delete {name} v{version}',
  'ad.gov.packs.deleteTitle': 'Delete {name} v{version}?',
  'ad.gov.packs.deleteBody':
      'Only an archived version can be deleted. The versions that stay keep their numbers and the audit log keeps the record.',
  'ad.gov.packs.deleteOnlyArchived':
      'Archive this version first, then delete it.',
  'ad.gov.packs.deleted': '{name} v{version} deleted.',
  'ad.gov.packs.activated': '{name} v{version} activated.',
  'ad.gov.packs.archivedDone': '{name} v{version} archived.',
  'ad.gov.packs.failed': 'Not changed: {message}',
  'ad.gov.packs.cancel': 'Cancel',
  'ad.gov.compare.title': 'Compare {name}',
  'ad.gov.compare.from': 'From version',
  'ad.gov.compare.to': 'To version',
  'ad.gov.compare.same': 'These two versions have the same rules.',
  'ad.gov.change.added': 'Added',
  'ad.gov.change.removed': 'Removed',
  'ad.gov.change.changed': 'Changed',
  'ad.gov.change.same': 'Unchanged',
  'ad.gov.change.before': 'Before: {rule}',
  'ad.gov.baseline.name': 'Organization baseline',
  'ad.gov.baseline.description':
      'Approval-first defaults: high-risk integration writes and payments wait for approval, and deleting memories is denied.',
  'ad.gov.editor.title': 'New policy pack',
  'ad.gov.editor.titleVersion': 'New version of {name}',
  'ad.gov.editor.intro':
      'Publishing creates a new, active version; the current version is archived and stays in the history.',
  'ad.gov.editor.startFrom': 'Start from',
  'ad.gov.editor.start.blank': 'Blank pack',
  'ad.gov.editor.start.baseline':
      'Organization baseline (approval-first defaults)',
  'ad.gov.editor.name': 'Pack name',
  'ad.gov.editor.nameHint':
      'A pack with this name gets its next version; a new name starts at v1.',
  'ad.gov.editor.description': 'Description',
  'ad.gov.editor.rules': 'Rules',
  'ad.gov.editor.rulesHint':
      'Rules are evaluated in this order. Deny wins over approval and allow.',
  'ad.gov.editor.rule': 'Rule {row}',
  'ad.gov.editor.action': 'Action pattern',
  'ad.gov.editor.effect': 'Effect',
  'ad.gov.editor.conditions': 'Conditions (JSON, optional)',
  'ad.gov.editor.addRule': 'Add rule',
  'ad.gov.editor.removeRule': 'Remove rule {row}',
  'ad.gov.editor.moveUp': 'Move rule {row} up',
  'ad.gov.editor.moveDown': 'Move rule {row} down',
  'ad.gov.editor.publish': 'Publish',
  'ad.gov.editor.publishing': 'Publishing...',
  'ad.gov.editor.published': '{name} v{version} published and active.',
  'ad.gov.editor.err.name': 'Name the policy pack.',
  'ad.gov.editor.err.nameLength': 'Pack names are at most 160 characters.',
  'ad.gov.editor.err.description':
      'The description is at most 1000 characters.',
  'ad.gov.editor.err.noRules': 'Add at least one rule.',
  'ad.gov.editor.err.tooManyRules': 'A pack holds at most 100 rules.',
  'ad.gov.editor.err.action':
      'Rule {row}: give the action pattern (for example integration.*.write), at most 160 characters.',
  'ad.gov.editor.err.conditions':
      'Rule {row}: conditions must be a JSON object, for example {"risk": "high"}, or empty.',
  'ad.gov.effect.allow': 'Allow',
  'ad.gov.effect.require_approval': 'Require approval',
  'ad.gov.effect.deny': 'Deny',

  // ---- Role templates on members ----
  'ad.tpl.label': 'Role template',
  'ad.tpl.none': 'None (the role alone)',
  'ad.tpl.for': 'Role template for {user}',
  'ad.tpl.badge': 'Template: {template}',
  'ad.tpl.change': 'Change template',
  'ad.tpl.changed': '{user} now has the role template {template}.',
  'ad.tpl.removed': '{user} no longer has a role template.',
  'ad.tpl.effect': 'Permissions that apply: {count}',
  'ad.tpl.outside': 'Outside the {role} role, so they do not apply: {list}',
  'ad.tpl.owner':
      'Owners keep the whole owner role; a template applies to members and viewers.',
  'ad.tpl.unavailable': 'Role templates could not be loaded: {message}',
  'ad.tpl.noneYet':
      'No role templates yet. An organization administrator adds them under Organization.',
  'ad.tpl.confirmTitle': 'Give {user} the template {template}?',
  'ad.tpl.confirmRemoveTitle': 'Remove the role template from {user}?',
  'ad.tpl.confirmText':
      'From their next request, {user} keeps only the permissions the {role} role and the template share.',
  'ad.tpl.confirmRemoveText':
      'From their next request, {user} has the whole {role} role again.',
  'ad.tpl.confirm': 'Confirm',
  'ad.tpl.cancel': 'Cancel',
  'ad.tpl.failed': 'The template was not changed: {message}',
  'ad.tpl.howUsed':
      'A workspace owner gives a template to a member or viewer under Members. That person then keeps only the permissions their role and the template share; owners always keep the whole owner role.',
};

const adminDepthAr = <String, String>{
  // ---- المهام الخاصة ----
  'ad.task.private': 'خاصة بك',
  'ad.task.privateHint':
      'أنت وحدك ترى هذه المهمة وتشغيلاتها ونتائجها. جاءت من محادثتك الخاصة أو قرأت بياناتك أنت.',
  'ad.task.private.data':
      'قرأت بياناتك أنت (المالية أو صندوق بريدك)، لذا تبقى خاصة دائمًا وتقرر موافقاتها بنفسك.',
  'ad.task.private.chat':
      'جاءت من محادثتك الخاصة. إذا انتظر تشغيلها موافقة شخص آخر، تُشارك مع مساحة العمل في تلك اللحظة.',

  // ---- المفضلة: البحث والترتيب وترتيب الشخص ----
  'ad.fav.search': 'ابحث في المفضلة',
  'ad.fav.searchHint': 'اسم عنصر مميز بنجمة',
  'ad.fav.clearSearch': 'مسح البحث',
  'ad.fav.sort': 'الترتيب',
  'ad.fav.sort.custom': 'ترتيبي',
  'ad.fav.sort.newest': 'الأحدث أولًا',
  'ad.fav.sort.oldest': 'الأقدم أولًا',
  'ad.fav.sort.name': 'الاسم، أبجديًا',
  'ad.fav.arrangeHint':
      'اسحب صفًا من مقبضه، أو استخدم نقل للأعلى ونقل للأسفل، لترتيب المفضلة كما تريد. يُحفظ الترتيب لكل أجهزتك.',
  'ad.fav.arrangeOff': 'لترتيب المفضلة يدويًا، اختر الترتيب {sort}.',
  'ad.fav.noMatch': 'لا شيء في المفضلة يطابق "{query}"',
  'ad.fav.noMatchText':
      'يبحث البحث في أسماء العناصر المميزة بنجمة. جرّب كلمة أخرى أو امسح البحث.',
  'ad.fav.noneOfType': 'لا شيء من هذا النوع مميز بنجمة',
  'ad.fav.noneOfTypeText': 'ميّز عنصرًا بنجمة حيث يوجد، أو اعرض كل الأنواع.',
  'ad.fav.showAll': 'عرض كل الأنواع',
  'ad.fav.arrange': 'ترتيب {name}',
  'ad.fav.drag': 'اسحب {name} لنقله',
  'ad.fav.moveUp': 'نقل للأعلى',
  'ad.fav.moveDown': 'نقل للأسفل',
  'ad.fav.movedTo': 'نُقل {name} {where} {other}.',
  'ad.fav.before': 'قبل',
  'ad.fav.after': 'بعد',
  'ad.fav.moveFailed':
      'تعذّر حفظ الترتيب الجديد: {message} عادت القائمة كما كانت.',

  // ---- الميزانيات: حد التنبيه ----
  'ad.bud.alertPercent': 'تنبيه المالكين عند (%)',
  'ad.bud.alertHint':
      'يُبلَّغ المالكون في التطبيق عند 50 و75 و90 و100 بالمئة من الحد. أضف حدك الخاص هنا، من 1 إلى 99.',
  'ad.bud.err.alert':
      'يجب أن يكون حد التنبيه عددًا صحيحًا من 1 إلى 99، أو فارغًا للاكتفاء بالتنبيهات الثابتة.',
  'ad.bud.err.alertNeedsLimit':
      'يحتاج حد التنبيه إلى حد تكلفة أو حد رموز يُقاس عليه.',
  'ad.bud.alertAt': 'يُنبَّه المالكون أيضًا عند {percent}%.',

  // ---- الميزانيات: أين يذهب الإنفاق ----
  'ad.use.eyebrow': 'الإنفاق المسجَّل',
  'ad.use.title': 'أين يذهب الإنفاق',
  'ad.use.intro':
      'كل استدعاء للنموذج قاسته الميزانية، مجمَّعًا كما تختار. لا شيء هنا تقديري سوى سعر كل استدعاء.',
  'ad.use.groupBy': 'التجميع حسب',
  'ad.use.group.day': 'اليوم',
  'ad.use.group.month': 'الشهر',
  'ad.use.group.user': 'الشخص',
  'ad.use.group.agent': 'الوكيل',
  'ad.use.group.feature': 'الميزة',
  'ad.use.scope': 'النطاق',
  'ad.use.scope.workspace': 'مساحة العمل هذه',
  'ad.use.scope.tenant': 'المؤسسة كلها',
  'ad.use.range': 'الفترة',
  'ad.use.range.month': 'هذا الشهر',
  'ad.use.range.30d': 'آخر 30 يومًا',
  'ad.use.range.3m': 'آخر 3 أشهر',
  'ad.use.range.12m': 'آخر 12 شهرًا',
  'ad.use.range.custom': 'تاريخان مخصصان',
  'ad.use.pickDates': 'اختر التاريخين',
  'ad.use.badRange':
      'لا يجوز أن يسبق تاريخ النهاية تاريخ البداية، ولا تتجاوز الفترة 400 يوم.',
  'ad.use.measure': 'المقياس',
  'ad.use.measure.cost': 'التكلفة التقديرية',
  'ad.use.measure.tokens': 'الرموز',
  'ad.use.period': 'من {from} إلى {to}',
  'ad.use.loading': 'جارٍ تحميل تفصيل الإنفاق...',
  'ad.use.error': 'تعذر تحميل تفصيل الإنفاق. {message}',
  'ad.use.denied':
      'لا يرى أين يذهب إنفاق النماذج إلا مالكو مساحة العمل. اطلب ذلك من أحد مالكيها.',
  'ad.use.retry': 'حاول مرة أخرى',
  'ad.use.empty': 'لا استدعاءات للنموذج في هذه الفترة',
  'ad.use.emptyText':
      'لم يُقَس شيء لهذا النطاق بين {from} و{to}. اختر فترة أطول أو المؤسسة كلها.',
  'ad.use.longer': 'اعرض آخر 12 شهرًا',
  'ad.use.recordedSince':
      'التفصيل حسب اليوم والشخص والوكيل والميزة موجود منذ {date}. الإنفاق الأقدم في المجاميع الشهرية: جمّع حسب الشهر لرؤيته.',
  'ad.use.showing': 'تُعرض {shown} من {count} مجموعة.',
  'ad.use.loadMore': 'تحميل المزيد',
  'ad.use.moreFailed': 'تعذر تحميل المزيد: {message}',
  'ad.use.export': 'تصدير CSV',
  'ad.use.exporting': 'جارٍ تجهيز ملف CSV...',
  'ad.use.exported': 'حُفظ ملف CSV.',
  'ad.use.exportCancelled': 'لم يُحفظ ملف CSV.',
  'ad.use.exportFailed': 'تعذر تصدير ملف CSV. {message}',
  'ad.use.total': 'المجموع',
  'ad.use.calls': 'الاستدعاءات: {count}',
  'ad.use.tokens': 'الرموز: {count}',
  'ad.use.share': '{percent}% من الفترة',
  'ad.use.unpriced': 'استدعاءات بلا سعر: {count}',
  'ad.use.rowLabel': '{label}: {value}، {share}',
  'ad.use.user.you': '{user} (أنت)',
  'ad.use.feature.tasks': 'المهام وتشغيل الوكلاء',
  'ad.use.feature.voice': 'الصوت',
  'ad.use.feature.assistant': 'الدردشة الذكية',
  'ad.use.feature.email': 'البريد الإلكتروني',
  'ad.use.feature.knowledge': 'قاعدة المعرفة والاسترجاع',
  'ad.use.feature.meetings': 'الاجتماعات',
  'ad.use.feature.business': 'الأعمال',
  'ad.use.feature.marketing': 'التسويق',
  'ad.use.feature.finance': 'المالية',
  'ad.use.feature.calls': 'المكالمات الهاتفية',
  'ad.use.feature.speech': 'التعرف على الكلام ونطقه',
  'ad.use.feature.browser': 'بحث المتصفح وإجراءاته',
  'ad.use.feature.attendance': 'الحضور',
  'ad.use.feature.executive': 'المساعد التنفيذي',
  'ad.use.feature.whatsapp': 'واتساب',
  'ad.use.feature.commerce': 'التجارة',
  'ad.use.feature.developer': 'وضع المطوّر',
  'ad.use.feature.other': 'أخرى',

  // ---- إعدادات الموافقات: الأقسام ----
  'ad.apr.rules.eyebrow': 'قواعد المؤسسة',
  'ad.apr.rules.title': 'قواعد الموافقة',
  'ad.apr.rules.intro':
      'تسري القواعد على كل مساحات العمل في المؤسسة: أي أنماط أدوات تتوقف، ونطاقات المبالغ بالدرهم، ومن يوافق في كل خطوة.',
  'ad.apr.rules.loading': 'جارٍ تحميل قواعد الموافقة...',
  'ad.apr.rules.error': 'تعذر تحميل قواعد الموافقة. {message}',
  'ad.apr.rules.denied':
      'تُعرض قواعد الموافقة لأعضاء مساحة العمل هذه. اطلب الوصول من مالك مساحة العمل.',
  'ad.apr.rules.empty':
      'لا قواعد في المؤسسة بعد: كل إجراء يتوقف يذهب إلى مالكي مساحة العمل.',
  'ad.apr.rules.readOnly':
      'تُعرض القواعد هنا ويغيّرها مسؤول المؤسسة في صفحة الحوكمة على الويب.',
  'ad.apr.rules.manage': 'تعديل القواعد على الويب',
  'ad.apr.rules.webHint':
      'افتح ANUM على الويب، ثم الحوكمة، قواعد الموافقة. لا يغيّرها إلا مسؤول المؤسسة.',
  'ad.apr.rules.count': 'القواعد: {count}، المفعّلة: {enabled}',
  'ad.apr.rules.pattern': 'تنطبق على {pattern}',
  'ad.apr.rules.everyAmount': 'كل مبلغ',
  'ad.apr.rules.on': 'مفعّلة',
  'ad.apr.rules.off': 'متوقفة',
  'ad.apr.band.above': 'أكثر من {amount}',
  'ad.apr.band.atMost': 'حتى {amount}',
  'ad.apr.band.between': 'أكثر من {above} وحتى {atMost}',
  'ad.apr.band.percent': 'لا يزيد عن {percent}% من قيمة الطلب',
  'ad.apr.band.disputed': 'المتنازع عليها فقط',
  'ad.apr.band.notDisputed': 'غير المتنازع عليها',
  'ad.apr.step': 'الخطوة {step}: {who}',
  'ad.apr.legacy': 'يقرر المالكون ({roles})، والعدد المطلوب {count}',
  'ad.apr.who.owners': 'المالكون',
  'ad.apr.who.orgAdmins': 'مسؤولو المؤسسة',
  'ad.apr.who.members': 'الأعضاء',
  'ad.apr.or': '{head} أو {last}',
  'ad.apr.routing.eyebrow': 'كيف يُوجَّه الإجراء المتوقف',
  'ad.apr.routing.title': 'التوجيه',
  'ad.apr.routing.intro':
      'مقروء من القواعد والسياسة أعلاه، بالترتيب الذي تطبّقه واجهة البرمجة عندما يتوقف إجراء.',
  'ad.apr.routing.step.pause':
      'يتوقف استدعاء الأداة عالي الخطورة. وتتوقف الاستدعاءات متوسطة الخطورة أيضًا ما دام مفتاح السياسة مفعّلًا.',
  'ad.apr.routing.step.match':
      'تُقرأ القواعد التي يطابق نمط أداتها، وتُجمع مبالغ موافقات المهمة الأخرى المعلّقة تحت القاعدة نفسها، فلا يُقسَّم شيء.',
  'ad.apr.routing.step.band':
      'أول نطاق ينطبق يحدد من يوافق، في خطوة أو خطوتين.',
  'ad.apr.routing.step.fallback':
      'أي مبلغ لا يغطيه نطاق، أو مبلغ غير معروف، أو بعملة أخرى، يذهب إلى مالكي مساحة العمل.',
  'ad.apr.routing.step.owners':
      'يستطيع المالكون الموافقة دائمًا؛ وغيرهم فقط عندما يسمّيهم المسار للخطوة الحالية. لا يوافق أحد على طلبه.',
  'ad.apr.routing.bands': 'النطاقات السارية',
  'ad.apr.routing.band': '{name}: {band}، {who}',
  'ad.apr.routing.noBands':
      'لا توجّه أي قاعدة حسب المبلغ بعد: يوافق مالكو مساحة العمل على كل ما يتوقف.',
  'ad.apr.routing.rulesUnknown':
      'تعذرت قراءة القواعد، لذا لا تُعرض النطاقات السارية.',
  'ad.apr.routing.off': 'قواعد متوقفة لا توجّه: {count}',
  'ad.apr.routing.twoPersonOn':
      'قاعدة الشخصين مفعّلة: من أنشأ المهمة أو بدأ تشغيلها لا يوافق أبدًا على إجراءاتها عالية الخطورة.',
  'ad.apr.routing.twoPersonOff':
      'قاعدة الشخصين متوقفة: يستطيع مالك واحد الموافقة على إجراءات مهمة بدأها هو.',
  'ad.apr.routing.mediumOn': 'تتوقف الإجراءات متوسطة الخطورة للموافقة.',
  'ad.apr.routing.mediumOff': 'تعمل الإجراءات متوسطة الخطورة دون موافقة.',
  'ad.apr.routing.draft':
      'معروض بالمفاتيح كما ضُبطت أعلاه؛ احفظ السياسة لتسري.',
  'ad.apr.rem.eyebrow': 'الموافقات المنتظرة',
  'ad.apr.rem.title': 'تذكير الموافقين',
  'ad.apr.rem.help':
      'حين تنتظر موافقة هذه المدة، يتلقى مالكو مساحة العمل تذكيرًا واحدًا داخل التطبيق (بلا بريد). وتنتهي الموافقات بعد 24 ساعة كما هي.',
  'ad.apr.rem.label': 'أرسل تذكيرًا بعد',
  'ad.apr.rem.off': 'متوقف',
  'ad.apr.rem.hours': '{hours} ساعات',
  'ad.apr.rem.hour': 'ساعة واحدة',
  'ad.apr.rem.save': 'حفظ التذكير',
  'ad.apr.rem.saving': 'جارٍ الحفظ...',
  'ad.apr.rem.saved': 'تذكّر الموافقات المالكين الآن بعد {hours} ساعات.',
  'ad.apr.rem.savedOff': 'تذكيرات الموافقات متوقفة.',
  'ad.apr.rem.denied':
      'لا يغيّر موعد تذكير الموافقات المنتظرة إلا مالك مساحة العمل.',
  'ad.apr.rem.loading': 'جارٍ تحميل إعداد التذكير...',
  'ad.apr.rem.error': 'تعذر تحميل إعداد التذكير: {message}',
  'ad.apr.rem.saveFailed': 'لم يُحفظ التذكير: {message}',
  'ad.apr.rem.updated': 'غيّره {user} في {time}.',
  'ad.apr.rem.never': 'لم يتغير قط: التذكيرات متوقفة افتراضيًا.',

  // ---- الذكريات ----
  'ad.mem.title': 'علّم وكلاءك',
  'ad.mem.subtitle':
      'تنتمي الذكرى إلى مهمة، أو إلى مساحة العمل كلها حين لا تناسبها مهمة.',
  'ad.mem.related': 'تنتمي إلى',
  'ad.mem.wholeWorkspace': 'مساحة العمل كلها (بلا مهمة)',
  'ad.mem.keep': 'الاحتفاظ',
  'ad.mem.keep.indefinite': 'حتى يحذفها أحد',
  'ad.mem.keep.days': 'لمدة {count} يومًا',
  'ad.mem.workspaceHint':
      'ذكرى مساحة العمل مشتركة مع كل من هنا ولا تنتمي إلى مهمة. قد تُقصّر حوكمة الذاكرة في المؤسسة مدة الاحتفاظ بها أو ترفض نوع مصدرها.',
  'ad.mem.content': 'ماذا يجب أن يتذكروا؟',
  'ad.mem.save': 'حفظ الذكرى',
  'ad.mem.saved': 'حُفظت الذكرى.',
  'ad.mem.workspaceNote': 'ملاحظة مساحة العمل',
  'ad.mem.privateNote': 'خاصة بك، من محادثاتك',
  'ad.mem.fromTask': 'من: {task}',
  'ad.mem.expires': 'محفوظة حتى {date}',
  'ad.mem.edit': 'تعديل الذكرى',
  'ad.mem.editLabel': 'نص الذكرى',
  'ad.mem.saveEdit': 'حفظ الذكرى',
  'ad.mem.cancel': 'إلغاء',
  'ad.mem.edited': 'حُدّثت الذكرى. يتبع الفهرس النص الجديد.',
  'ad.mem.editedBy': 'عُدّلت في {when} بواسطة {user}.',
  'ad.mem.editEmpty': 'تحتاج الذكرى إلى نص.',
  'ad.mem.editDenied': 'لا يغيّر هذه الذكرى إلا من أضافها أو مالك مساحة العمل.',
  'ad.mem.editFailed': 'لم تتغير الذكرى: {message}',
  'ad.mem.showing': 'الذكريات المعروضة: {count}',
  'ad.mem.loadMore': 'تحميل ذكريات أقدم',
  'ad.mem.loadingMore': 'جارٍ تحميل الذكريات الأقدم...',
  'ad.mem.moreFailed': 'تعذر تحميل الذكريات الأقدم: {message}',
  'ad.mem.delete': 'حذف الذكرى',
  'ad.mem.empty': 'لا ذكريات بعد',
  'ad.mem.emptyText':
      'أضف الأولى أعلاه: معلومة لمهمة واحدة، أو ملاحظة لمساحة العمل كلها.',

  // ---- الحوكمة: حزم السياسات ----
  'ad.gov.packs.title': 'حزم السياسات',
  'ad.gov.packs.new': 'حزمة سياسات جديدة',
  'ad.gov.packs.emptyAdmin':
      'لا توجد حزم سياسات بعد. انشر الأولى: ابدأ من أساس المؤسسة أو من حزمة فارغة.',
  'ad.gov.packs.emptyOther':
      'لا توجد حزم سياسات بعد. مسؤول المؤسسة هو من ينشر الأولى.',
  'ad.gov.packs.search': 'ابحث في حزم السياسات',
  'ad.gov.packs.noMatch': 'لا توجد حزمة سياسات تطابق "{query}".',
  'ad.gov.packs.shown': 'الحزم المعروضة: {shown} من {count}',
  'ad.gov.packs.showMore': 'عرض حزم أخرى',
  'ad.gov.packs.versions': 'الإصدارات: {count}',
  'ad.gov.packs.activeVersion': 'النشط: v{version}',
  'ad.gov.packs.noActive': 'لا يوجد إصدار نشط',
  'ad.gov.packs.rules': 'القواعد: {count}',
  'ad.gov.packs.active': 'نشطة',
  'ad.gov.packs.archived': 'مؤرشفة',
  'ad.gov.packs.activate': 'تفعيل',
  'ad.gov.packs.archive': 'أرشفة',
  'ad.gov.packs.newVersion': 'إصدار جديد',
  'ad.gov.packs.compare': 'مقارنة الإصدارات',
  'ad.gov.packs.versionsTitle': 'إصدارات {name}',
  'ad.gov.packs.version': 'v{version}',
  'ad.gov.packs.published': 'نُشر {when}',
  'ad.gov.packs.delete': 'حذف',
  'ad.gov.packs.deleteLabel': 'حذف {name} v{version}',
  'ad.gov.packs.deleteTitle': 'حذف {name} v{version}؟',
  'ad.gov.packs.deleteBody':
      'لا يُحذف إلا إصدار مؤرشف. تحتفظ الإصدارات الباقية بأرقامها ويحتفظ سجل التدقيق بالأثر.',
  'ad.gov.packs.deleteOnlyArchived': 'أرشف هذا الإصدار أولًا، ثم احذفه.',
  'ad.gov.packs.deleted': 'حُذف {name} v{version}.',
  'ad.gov.packs.activated': 'فُعّلت {name} v{version}.',
  'ad.gov.packs.archivedDone': 'أُرشفت {name} v{version}.',
  'ad.gov.packs.failed': 'لم يتغير شيء: {message}',
  'ad.gov.packs.cancel': 'إلغاء',
  'ad.gov.compare.title': 'مقارنة {name}',
  'ad.gov.compare.from': 'من الإصدار',
  'ad.gov.compare.to': 'إلى الإصدار',
  'ad.gov.compare.same': 'لهذين الإصدارين القواعد نفسها.',
  'ad.gov.change.added': 'أُضيفت',
  'ad.gov.change.removed': 'أُزيلت',
  'ad.gov.change.changed': 'تغيّرت',
  'ad.gov.change.same': 'بلا تغيير',
  'ad.gov.change.before': 'قبل: {rule}',
  'ad.gov.baseline.name': 'أساس المؤسسة',
  'ad.gov.baseline.description':
      'إعدادات افتراضية تبدأ بالموافقة: عمليات الكتابة عالية المخاطر في التكاملات والمدفوعات تنتظر الموافقة، وحذف الذكريات مرفوض.',
  'ad.gov.editor.title': 'حزمة سياسات جديدة',
  'ad.gov.editor.titleVersion': 'إصدار جديد من {name}',
  'ad.gov.editor.intro':
      'النشر ينشئ إصدارًا جديدًا نشطًا؛ يُؤرشف الإصدار الحالي ويبقى في السجل.',
  'ad.gov.editor.startFrom': 'ابدأ من',
  'ad.gov.editor.start.blank': 'حزمة فارغة',
  'ad.gov.editor.start.baseline': 'أساس المؤسسة (إعدادات تبدأ بالموافقة)',
  'ad.gov.editor.name': 'اسم الحزمة',
  'ad.gov.editor.nameHint':
      'حزمة بهذا الاسم تحصل على إصدارها التالي؛ الاسم الجديد يبدأ من v1.',
  'ad.gov.editor.description': 'الوصف',
  'ad.gov.editor.rules': 'القواعد',
  'ad.gov.editor.rulesHint':
      'تُقيَّم القواعد بهذا الترتيب. الرفض يتقدّم على الموافقة والسماح.',
  'ad.gov.editor.rule': 'القاعدة {row}',
  'ad.gov.editor.action': 'نمط الإجراء',
  'ad.gov.editor.effect': 'الأثر',
  'ad.gov.editor.conditions': 'الشروط (JSON، اختيارية)',
  'ad.gov.editor.addRule': 'إضافة قاعدة',
  'ad.gov.editor.removeRule': 'إزالة القاعدة {row}',
  'ad.gov.editor.moveUp': 'نقل القاعدة {row} لأعلى',
  'ad.gov.editor.moveDown': 'نقل القاعدة {row} لأسفل',
  'ad.gov.editor.publish': 'نشر',
  'ad.gov.editor.publishing': 'جارٍ النشر...',
  'ad.gov.editor.published': 'نُشر {name} v{version} وأصبح نشطًا.',
  'ad.gov.editor.err.name': 'سمِّ حزمة السياسات.',
  'ad.gov.editor.err.nameLength': 'اسم الحزمة 160 حرفًا على الأكثر.',
  'ad.gov.editor.err.description': 'الوصف 1000 حرف على الأكثر.',
  'ad.gov.editor.err.noRules': 'أضف قاعدة واحدة على الأقل.',
  'ad.gov.editor.err.tooManyRules': 'تحمل الحزمة 100 قاعدة على الأكثر.',
  'ad.gov.editor.err.action':
      'القاعدة {row}: أدخل نمط الإجراء (مثلًا integration.*.write) بما لا يتجاوز 160 حرفًا.',
  'ad.gov.editor.err.conditions':
      'القاعدة {row}: يجب أن تكون الشروط كائن JSON، مثلًا {"risk": "high"}، أو فارغة.',
  'ad.gov.effect.allow': 'سماح',
  'ad.gov.effect.require_approval': 'تتطلب موافقة',
  'ad.gov.effect.deny': 'رفض',

  // ---- قوالب الأدوار على الأعضاء ----
  'ad.tpl.label': 'قالب الدور',
  'ad.tpl.none': 'بلا قالب (الدور وحده)',
  'ad.tpl.for': 'قالب الدور لـ {user}',
  'ad.tpl.badge': 'القالب: {template}',
  'ad.tpl.change': 'تغيير القالب',
  'ad.tpl.changed': 'أصبح لدى {user} قالب الدور {template}.',
  'ad.tpl.removed': 'لم يعد لدى {user} قالب دور.',
  'ad.tpl.effect': 'الصلاحيات السارية: {count}',
  'ad.tpl.outside': 'خارج دور {role} فلا تسري: {list}',
  'ad.tpl.owner':
      'يحتفظ المالكون بدور المالك كاملًا؛ القالب يسري على الأعضاء والمشاهدين.',
  'ad.tpl.unavailable': 'تعذّر تحميل قوالب الأدوار: {message}',
  'ad.tpl.noneYet':
      'لا توجد قوالب أدوار بعد. يضيفها مسؤول المؤسسة من شاشة المؤسسة.',
  'ad.tpl.confirmTitle': 'منح {user} القالب {template}؟',
  'ad.tpl.confirmRemoveTitle': 'إزالة قالب الدور من {user}؟',
  'ad.tpl.confirmText':
      'من طلبه التالي، يحتفظ {user} بالصلاحيات المشتركة بين دور {role} والقالب فقط.',
  'ad.tpl.confirmRemoveText':
      'من طلبه التالي، يعود لدى {user} دور {role} كاملًا.',
  'ad.tpl.confirm': 'تأكيد',
  'ad.tpl.cancel': 'إلغاء',
  'ad.tpl.failed': 'لم يتغير القالب: {message}',
  'ad.tpl.howUsed':
      'يمنح مالك مساحة العمل القالب لعضو أو مشاهد من صفحة الأعضاء. يحتفظ ذلك الشخص عندها بالصلاحيات المشتركة بين دوره والقالب فقط؛ أما المالكون فيحتفظون دائمًا بدور المالك كاملًا.',
};
