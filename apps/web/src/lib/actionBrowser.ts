// The action browser in the web app (docs/action-browser.md): pure helpers, types and the
// EN and AR words of its lazy panels (saved logins and cards, the live view, the approval card).
// Imported only by the lazy chunks in src/action-browser/ and by tests, so none of it is in the
// main bundle. No em or en dashes in any string.
import { getLocale, type Locale } from './i18n.ts';

export interface Credential { id: string; kind: 'login' | 'card'; handle: string; site: string | null; hint: string; created_by: string; created_at: string; updated_by: string; updated_at: string }
export interface CredentialInput {
  kind: 'login' | 'card'; handle: string; site?: string; username?: string; password?: string; name?: string; number?: string; expiry?: string; cvc?: string;
}
export interface SessionStep { seq: number; kind: string; summary: string; url: string | null; detail: Record<string, unknown>; created_at: string }
export interface BrowserSession {
  id: string; task_id: string; run_id: string; status: 'running' | 'waiting' | 'completed' | 'failed' | 'stopped'; goal: string; start_url: string;
  sites: string[]; logins: string[]; cards: string[]; step_count: number; current_url: string | null; current_title: string | null;
  waiting: 'approval' | 'handoff' | 'off_site' | 'continue' | null; pending: Record<string, unknown> | null; outcome: string | null; message: string;
  live: boolean; can_watch: boolean; can_stop: boolean; created_at: string; updated_at: string; ended_at: string | null; steps: SessionStep[];
}
export type InputEvent = { kind: 'click'; x: number; y: number } | { kind: 'text'; text: string } | { kind: 'key'; key: string } | { kind: 'scroll'; dy: number };

export const ACT_TOOL = 'web.act';
export const FRAME_WIDTH = 1280;
export const FRAME_HEIGHT = 800;
export const POLL_MS = 1000;
export const OWNER_KEYS = ['Enter', 'Tab', 'Backspace', 'Escape', 'ArrowUp', 'ArrowDown'] as const;

/** Whether a task used the action browser: any run step or approval names web.act. */
export function usesActionBrowser(steps: { metadata?: Record<string, unknown> | null }[] | undefined, approvals: { action: string }[] = []): boolean {
  return approvals.some((a) => a.action === ACT_TOOL) || (steps ?? []).some((s) => s.metadata?.tool === ACT_TOOL);
}

export function isActive(session: BrowserSession | null | undefined): boolean {
  return Boolean(session && (session.status === 'running' || session.status === 'waiting'));
}

/** Where a click on the shown picture lands on the page (the picture is scaled to fit). */
export function toPagePoint(clientX: number, clientY: number, box: { left: number; top: number; width: number; height: number }, rtl = false): { x: number; y: number } | null {
  if (box.width <= 0 || box.height <= 0) return null;
  void rtl; // The picture itself is never mirrored, so the point is the same in Arabic.
  const x = Math.round(((clientX - box.left) / box.width) * FRAME_WIDTH);
  const y = Math.round(((clientY - box.top) / box.height) * FRAME_HEIGHT);
  if (x < 0 || y < 0 || x > FRAME_WIDTH || y > FRAME_HEIGHT) return null;
  return { x, y };
}

/** The structured facts of a web.act approval, for its card. */
export interface StepFacts { kind: string; site: string; url: string; target: string; amount: string; fields: { label: string; type: string }[]; value: string; resume: string; check: string }
export function stepFacts(args: Record<string, unknown> | undefined): StepFacts | null {
  if (!args || typeof args !== 'object') return null;
  const text = (key: string) => (typeof args[key] === 'string' ? (args[key] as string) : '');
  const shown = args.shown_action && typeof args.shown_action === 'object' ? (args.shown_action as Record<string, unknown>) : {};
  const fields = Array.isArray(args.fields) ? (args.fields as { label?: unknown; type?: unknown }[]).map((f) => ({ label: String(f.label ?? ''), type: String(f.type ?? '') })).filter((f) => f.label) : [];
  if (!text('resume') && !text('start_url')) return null;
  return {
    kind: text('kind'), site: text('site'), url: text('url') || text('start_url'), target: text('target'), amount: text('amount_text'),
    fields, value: typeof shown.value === 'string' ? shown.value : '', resume: text('resume') || 'scope', check: text('check'),
  };
}

/** Saved login and card form checks before anything is sent (the API checks again). */
export function credentialProblem(input: CredentialInput, editing: boolean, locale: Locale = getLocale()): string | null {
  const w = words(locale);
  if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(input.handle.trim())) return w.handleRule;
  if (input.kind === 'login') {
    if (!editing && !(input.site ?? '').trim()) return w.siteNeeded;
    if (!editing && (!(input.username ?? '').trim() || !(input.password ?? ''))) return w.loginNeeded;
    return null;
  }
  const digits = (input.number ?? '').replace(/[\s-]/g, '');
  if ((!editing || digits) && !luhn(digits)) return w.cardNumberBad;
  if ((!editing || input.expiry) && !/^(0[1-9]|1[0-2])\/\d{2}$/.test((input.expiry ?? '').trim())) return w.expiryBad;
  if ((!editing || input.cvc) && !/^\d{3,4}$/.test((input.cvc ?? '').trim())) return w.cvcBad;
  if (!editing && !(input.name ?? '').trim()) return w.nameNeeded;
  return null;
}

export function luhn(digits: string): boolean {
  if (!/^\d{12,19}$/.test(digits)) return false;
  let sum = 0;
  for (let i = 0; i < digits.length; i += 1) {
    let n = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) { n *= 2; if (n > 9) n -= 9; }
    sum += n;
  }
  return sum % 10 === 0;
}

/** Only the parts the person filled in are sent: an edit keeps the saved secret parts. */
export function credentialBody(input: CredentialInput): CredentialInput {
  const body: CredentialInput = { kind: input.kind, handle: input.handle.trim().toLowerCase() };
  for (const key of ['site', 'username', 'password', 'name', 'number', 'expiry', 'cvc'] as const) {
    const value = input[key];
    if (typeof value === 'string' && value.trim()) body[key] = key === 'password' ? value : value.trim();
  }
  return body;
}

// ------------------------------------------------------------------ words (EN and AR)

const EN = {
  vaultTitle: 'Saved logins and cards', vaultIntro: 'The action browser types these for you. The model never sees them: it writes a handle such as {{login:emirates.password}}, and the value is put in only when the browser types it. Card entry always waits for your approval.',
  ownerOnly: 'Only a workspace owner manages saved logins and cards.', loading: 'Loading saved logins and cards...', emptyTitle: 'Nothing saved yet', emptyText: 'Add a login for a site the agent may sign in to, or a card it may pay with after you approve.',
  addLogin: 'Add login', addCard: 'Add card', edit: 'Edit', remove: 'Delete', confirmRemove: 'Delete {handle}', cancel: 'Cancel', save: 'Save', saving: 'Saving...',
  login: 'Login', card: 'Card', handle: 'Handle', handleHelp: 'Lowercase letters, digits and dashes. The agent refers to it by this name.', site: 'Site', siteHelp: 'The site this login is for, for example emirates.com. It is typed only on that site.',
  username: 'User name or email', password: 'Password', nameOnCard: 'Name on card', cardNumber: 'Card number', expiry: 'Expiry (MM/YY)', cvc: 'Security code (CVC)',
  keepHint: 'Leave a secret field empty to keep the saved one.', saved: 'Saved {handle}.', removed: 'Deleted {handle}.', loadFailed: 'Could not load saved logins and cards: {error}',
  handleRule: 'A handle is lowercase letters, digits and dashes (at most 40).', siteNeeded: 'Give the site this login is for.', loginNeeded: 'Give the user name and the password.',
  cardNumberBad: 'The card number is not valid.', expiryBad: 'Give the expiry as MM/YY, for example 08/29.', cvcBad: 'The security code is 3 or 4 digits.', nameNeeded: 'Give the name on the card.',
  liveTitle: 'Browser', liveRegion: 'Action browser live view', status_running: 'Working', status_waiting_approval: 'Waiting for your approval', status_waiting_handoff: 'Needs you',
  status_waiting_off_site: 'Waiting for your approval', status_waiting_continue: 'Waiting for your approval', status_completed: 'Done', status_failed: 'Failed', status_stopped: 'Stopped',
  stop: 'Stop', stopping: 'Stopping...', stopAsk: 'Stop the browser and cancel this task?', stopYes: 'Stop and cancel', stopped: 'Stopped. The browser is closed.',
  pictureAlt: 'Live picture of the page at {url}', pictureMasked: 'Passwords and card numbers are hidden in the picture.', noPicture: 'No live picture: the browser session has ended.',
  pictureMembers: 'Only a workspace owner sees the live picture.', pictureFailed: 'The picture could not be loaded right now.', stepsTitle: 'Steps', noSteps: 'No steps yet.',
  handoffTitle: 'Needs you', handoffText: 'Complete the check in the picture: click where you would click, type below, then press Continue on the approval.',
  typeHere: 'Text to type on the page', sendText: 'Type it', key: 'Press {key}', inputFailed: 'The page did not take that: {error}', sites: 'Sites it may open', handles: 'Saved items it may use',
  answerTitle: 'Result', filesTitle: 'Saved to Files', loadingLive: 'Loading the browser session...', liveFailed: 'Could not load the browser session: {error}', retry: 'Try again',
  approvalSite: 'Site', approvalAddress: 'Address', approvalAction: 'Action', approvalAmount: 'Amount on the page', approvalFields: 'Fields on the form', approvalValue: 'Value',
  approvalScope: 'The agent will open only these sites and use only these saved items. Nothing on a page can add to them.', approvalHandoff: 'Open the task to see the page and complete the check, then approve to continue.',
  approvalGone: 'The browser session has ended; there is no picture.', amountNote: 'This is what the page shows. Your bank statement is the final word.',
  kind_card_entry: 'Enter a saved card', kind_binding_button: 'Press a button that commits', kind_payment_form: 'Send a payment or order form', kind_payment_frame: 'Press a button on a payment form',
  kind_payment_field: 'Type into a payment field', kind_off_site: 'Open a site outside the list', kind_scope: 'Start the browser task', kind_handoff: 'Needs you', kind_continue: 'Keep going',
} as const;

type Words = Record<keyof typeof EN, string>;

const AR: Words = {
  vaultTitle: 'بيانات الدخول والبطاقات المحفوظة', vaultIntro: 'يكتبها متصفح الإجراءات نيابة عنك. لا يراها النموذج أبدا: يكتب اسما مثل {{login:emirates.password}}، وتوضع القيمة فقط عندما يكتبها المتصفح. إدخال البطاقة ينتظر موافقتك دائما.',
  ownerOnly: 'مالك مساحة العمل فقط يدير بيانات الدخول والبطاقات المحفوظة.', loading: 'جار تحميل بيانات الدخول والبطاقات...', emptyTitle: 'لا شيء محفوظ بعد', emptyText: 'أضف بيانات دخول لموقع يمكن للوكيل الدخول إليه، أو بطاقة يدفع بها بعد موافقتك.',
  addLogin: 'إضافة بيانات دخول', addCard: 'إضافة بطاقة', edit: 'تعديل', remove: 'حذف', confirmRemove: 'حذف {handle}', cancel: 'إلغاء', save: 'حفظ', saving: 'جار الحفظ...',
  login: 'بيانات دخول', card: 'بطاقة', handle: 'الاسم المختصر', handleHelp: 'أحرف إنجليزية صغيرة وأرقام وشرطات. يشير إليه الوكيل بهذا الاسم.', site: 'الموقع', siteHelp: 'الموقع الذي تخصه بيانات الدخول، مثل emirates.com. لا تكتب إلا في هذا الموقع.',
  username: 'اسم المستخدم أو البريد', password: 'كلمة المرور', nameOnCard: 'الاسم على البطاقة', cardNumber: 'رقم البطاقة', expiry: 'تاريخ الانتهاء (MM/YY)', cvc: 'رمز الأمان (CVC)',
  keepHint: 'اترك حقل السر فارغا للإبقاء على المحفوظ.', saved: 'تم حفظ {handle}.', removed: 'تم حذف {handle}.', loadFailed: 'تعذر تحميل بيانات الدخول والبطاقات: {error}',
  handleRule: 'الاسم المختصر أحرف إنجليزية صغيرة وأرقام وشرطات (40 على الأكثر).', siteNeeded: 'اكتب الموقع الذي تخصه بيانات الدخول.', loginNeeded: 'اكتب اسم المستخدم وكلمة المرور.',
  cardNumberBad: 'رقم البطاقة غير صحيح.', expiryBad: 'اكتب تاريخ الانتهاء بالشكل MM/YY، مثل 08/29.', cvcBad: 'رمز الأمان 3 أو 4 أرقام.', nameNeeded: 'اكتب الاسم على البطاقة.',
  liveTitle: 'المتصفح', liveRegion: 'العرض المباشر لمتصفح الإجراءات', status_running: 'يعمل', status_waiting_approval: 'بانتظار موافقتك', status_waiting_handoff: 'يحتاجك',
  status_waiting_off_site: 'بانتظار موافقتك', status_waiting_continue: 'بانتظار موافقتك', status_completed: 'تم', status_failed: 'فشل', status_stopped: 'متوقف',
  stop: 'إيقاف', stopping: 'جار الإيقاف...', stopAsk: 'هل تريد إيقاف المتصفح وإلغاء هذه المهمة؟', stopYes: 'إيقاف وإلغاء', stopped: 'تم الإيقاف. أغلق المتصفح.',
  pictureAlt: 'صورة مباشرة للصفحة {url}', pictureMasked: 'كلمات المرور وأرقام البطاقات مخفية في الصورة.', noPicture: 'لا توجد صورة مباشرة: انتهت جلسة المتصفح.',
  pictureMembers: 'مالك مساحة العمل فقط يرى الصورة المباشرة.', pictureFailed: 'تعذر تحميل الصورة الآن.', stepsTitle: 'الخطوات', noSteps: 'لا خطوات بعد.',
  handoffTitle: 'يحتاجك', handoffText: 'أكمل التحقق في الصورة: انقر حيث تنقر عادة، واكتب أدناه، ثم اضغط متابعة في الموافقة.',
  typeHere: 'نص للكتابة في الصفحة', sendText: 'اكتبه', key: 'اضغط {key}', inputFailed: 'لم تقبل الصفحة ذلك: {error}', sites: 'المواقع المسموح فتحها', handles: 'العناصر المحفوظة المسموح استخدامها',
  answerTitle: 'النتيجة', filesTitle: 'حفظ في الملفات', loadingLive: 'جار تحميل جلسة المتصفح...', liveFailed: 'تعذر تحميل جلسة المتصفح: {error}', retry: 'حاول مرة أخرى',
  approvalSite: 'الموقع', approvalAddress: 'العنوان', approvalAction: 'الإجراء', approvalAmount: 'المبلغ الظاهر في الصفحة', approvalFields: 'حقول النموذج', approvalValue: 'القيمة',
  approvalScope: 'لن يفتح الوكيل إلا هذه المواقع ولن يستخدم إلا هذه العناصر المحفوظة. لا يمكن لأي صفحة أن تضيف إليها.', approvalHandoff: 'افتح المهمة لترى الصفحة وتكمل التحقق، ثم وافق للمتابعة.',
  approvalGone: 'انتهت جلسة المتصفح؛ لا توجد صورة.', amountNote: 'هذا ما تعرضه الصفحة. كشف حسابك البنكي هو المرجع النهائي.',
  kind_card_entry: 'إدخال بطاقة محفوظة', kind_binding_button: 'الضغط على زر ملزم', kind_payment_form: 'إرسال نموذج دفع أو طلب', kind_payment_frame: 'الضغط على زر في نموذج دفع',
  kind_payment_field: 'الكتابة في حقل دفع', kind_off_site: 'فتح موقع خارج القائمة', kind_scope: 'بدء مهمة المتصفح', kind_handoff: 'يحتاجك', kind_continue: 'المتابعة',
};

export type WordKey = keyof typeof EN;
export function words(locale: Locale = getLocale()): Words { return locale === 'ar' ? AR : EN; }
export function say(key: WordKey, vars: Record<string, string | number> = {}, locale: Locale = getLocale()): string {
  return words(locale)[key].replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}
export const WORDS = { en: EN as Words, ar: AR };

/** The status line of a session in the current language. */
export function statusText(session: BrowserSession, locale: Locale = getLocale()): string {
  const key = (session.status === 'waiting' ? `status_waiting_${session.waiting ?? 'approval'}` : `status_${session.status}`) as WordKey;
  return say(key in EN ? key : 'status_running', {}, locale);
}

/** The approval card's heading for a web.act step. */
export function kindText(facts: StepFacts, locale: Locale = getLocale()): string {
  const kind = facts.resume === 'scope' ? 'scope' : facts.resume === 'handoff' ? 'handoff' : facts.resume === 'continue' ? 'continue' : facts.kind || 'binding_button';
  const key = `kind_${kind}` as WordKey;
  return say(key in EN ? key : 'kind_binding_button', {}, locale);
}

/** A step of the log in the current language (the stored English sentence when no template fits). */
export function stepText(step: SessionStep, locale: Locale = getLocale()): string {
  if (locale === 'en') return step.summary;
  const d = step.detail ?? {};
  const host = typeof d.host === 'string' ? d.host : '';
  const label = typeof d.label === 'string' ? d.label : '';
  const name = typeof d.name === 'string' ? d.name : '';
  switch (step.kind) {
    case 'opened': return host ? `فتح ${host}` : step.summary;
    case 'clicked': return label ? `نقر ${label}` : step.summary;
    case 'typed': return label ? `كتب في ${label}` : step.summary;
    case 'selected': return label ? `اختار في ${label}` : step.summary;
    case 'scrolled': return 'مرر الصفحة';
    case 'waited': return 'انتظر الصفحة';
    case 'downloaded': return name ? `حفظ ${name} في الملفات` : step.summary;
    case 'extracted': return 'دون معلومات من الصفحة';
    case 'approval': case 'off_site': case 'continue': return `بانتظار موافقتك: ${step.summary.replace(/^Waiting for your approval: /, '')}`;
    case 'needs_you': return `يحتاجك: ${step.summary.replace(/^Needs you: /, '')}`;
    case 'approved': return 'وافق المالك';
    case 'page_changed': return 'تغيرت الصفحة؛ يسأل مرة أخرى';
    case 'done': return `تم: ${step.summary}`;
    case 'stopped': return 'أوقفه المالك';
    default: return step.summary;
  }
}
