"""What ANUM can do in this workspace right now, for the Assistant chat and voice
(docs/conversations.md#what-anum-can-do, docs/voice.md#what-anum-can-do).

The chat and voice models write text only; ANUM acts through governed tasks. Without a
list of what the workspace actually has, a model describes itself as a generic chatbot
("I can't browse or take actions"). ``workspace_capabilities`` reads the real state:
which tools this runtime registered, whether the caller's role may use them, whether an
account is connected (mail, WhatsApp, sales channels, the voice provider) and whether an
owner switched an integration on (web search, browser research, the action browser).
Each capability is on, or off with the one-line reason and where to turn it on. Nothing
is invented: a module whose state cannot be read says so.

The state is read in the caller's tenant scope (RLS), each module through its own short
store session, so callers run it in a worker thread (``asyncio.to_thread``), never on the
event loop. It is cached for ``CACHE_SECONDS`` per tenant, workspace, user and roles.

``capability_block`` is the text the chat sends as its own trailing system part, after
the static, cached system prompt (anthropic_gateway.py keeps it out of the system cache
breakpoint because it starts with ``CAPABILITY_HEADER``). ``spoken_capabilities`` is the
short form voice says aloud and gives its model.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from threading import Lock
from typing import Any

from .authorization import Permission
from .schemas import TenantContext

logger = logging.getLogger("anum.capabilities")

CACHE_SECONDS = 30.0
CACHE_ENTRIES = 1024
# The first line of the chat's capability system part: the Anthropic gateway sends a system
# part that starts with it after the cache breakpoint (it changes with the workspace).
CAPABILITY_HEADER = "ANUM in this workspace right now (live state, not a guess):"


@dataclass(frozen=True)
class Capability:
    id: str
    available: bool
    # Why it is off and where to turn it on (None when available).
    reason: str | None = None
    reason_ar: str | None = None
    # A short extra fact about an available capability (placing calls not connected, ...).
    note: str | None = None
    note_ar: str | None = None


# id: (name, name in Arabic, what ANUM does with it, the same in Arabic)
LABELS: dict[str, tuple[str, str, str, str]] = {
    "tasks": ("Tasks and agents", "المهام والوكلاء",
              "create, run and track tasks, hand work to agents and teammates, run quick commands",
              "إنشاء المهام وتشغيلها ومتابعتها، وإسناد العمل إلى الوكلاء والزملاء، وتشغيل الأوامر السريعة"),
    "approvals": ("Approvals", "الموافقات",
                  "anything that sends, pays, deletes or acts outside ANUM waits for an approval in Approvals",
                  "كل ما يرسل أو يدفع أو يحذف أو يتصرف خارج ANUM ينتظر موافقة في صفحة الموافقات"),
    "email": ("Email", "البريد",
              "search, read and summarise mail, label it and draft replies (sending waits for approval)",
              "البحث في البريد وقراءته وتلخيصه وتصنيفه وكتابة مسودات الردود (الإرسال ينتظر الموافقة)"),
    "whatsapp": ("WhatsApp", "واتساب",
                 "see which conversations need a reply (replies are approved in WhatsApp)",
                 "معرفة المحادثات التي تحتاج ردا (الردود تعتمد في صفحة واتساب)"),
    "calendar": ("Calendar", "التقويم",
                 "events and meeting invitations (invitations wait for approval)",
                 "المواعيد ودعوات الاجتماعات (الدعوات تنتظر الموافقة)"),
    "files": ("Files and knowledge base", "الملفات وقاعدة المعرفة",
              "answer from uploaded files and shared notes",
              "الإجابة من الملفات المرفوعة والملاحظات المشتركة"),
    "reports": ("Reports", "التقارير",
                "structured analyses saved as report cards",
                "تحليلات منظمة تحفظ كبطاقات تقارير"),
    "web_search": ("Web search", "البحث في الويب", "search the web", "البحث في الويب"),
    "browse": ("Browser research", "تصفح الويب",
               "open an https page and read it", "فتح صفحة https وقراءتها"),
    "act": ("Action browser", "متصفح الإجراءات",
            "do a task on a website: book, buy, fill and submit forms, download statements "
            "(every step that commits waits for approval)",
            "تنفيذ مهمة على موقع: الحجز والشراء وتعبئة النماذج وإرسالها وتنزيل الكشوف "
            "(كل خطوة ملزمة تنتظر الموافقة)"),
    "business": ("Business invoices", "فواتير الأعمال",
                 "invoices, proposals and outreach (sending waits for approval)",
                 "الفواتير والعروض والتواصل مع العملاء (الإرسال ينتظر الموافقة)"),
    "finance": ("Finance", "المالية",
                "spending, bills, balances and budgets from your own ledger",
                "المصاريف والفواتير والأرصدة والميزانيات من سجلك"),
    "commerce": ("Commerce", "التجارة",
                 "sales, orders and stock from connected sales channels",
                 "المبيعات والطلبات والمخزون من قنوات البيع المربوطة"),
    "tyre": ("Tyre planning", "تخطيط الإطارات",
             "fleet tyres due, alerts and purchase plans", "إطارات الأسطول المستحقة والتنبيهات وخطط الشراء"),
    "meetings": ("Meetings", "الاجتماعات",
                 "meeting notes, transcripts and follow-ups", "ملاحظات الاجتماعات ونصوصها والمتابعات"),
    "calls": ("Phone calls", "المكالمات",
              "the call log and contacts, and calls through the connected voice provider",
              "سجل المكالمات وجهات الاتصال، والمكالمات عبر مزود الصوت المربوط"),
    "automations": ("Automations", "الأتمتة",
                    "scheduled and event-triggered workflows", "سير عمل مجدول أو يبدأ عند حدث"),
    "marketing": ("Social media marketing", "التسويق عبر وسائل التواصل",
                  "draft and schedule posts (posting waits for approval) and read post results",
                  "كتابة المنشورات وجدولتها (النشر ينتظر الموافقة) وقراءة نتائجها"),
}

# Which capability a chat tool call needs (preflight, chat and voice routing).
TOOL_CAPABILITY = {"web.act": "act", "web.browse_page": "browse", "web_search": "web_search"}

_ROLE_EN = "your role cannot use it: a workspace owner can give you the member role in Settings > Members"
_ROLE_AR = "دورك لا يسمح باستخدامها: يستطيع مالك مساحة العمل منحك دور العضو من الإعدادات > الأعضاء"
_SERVER_EN = "not available on this server: the operator has not set it up"
_SERVER_AR = "غير متاحة على هذا الخادم: لم يجهزها المشغل"
_UNKNOWN_EN = "its state could not be read just now; try again in a moment"
_UNKNOWN_AR = "تعذرت قراءة حالتها الآن؛ حاول بعد قليل"


def _on(cap_id: str, note: str | None = None, note_ar: str | None = None) -> Capability:
    return Capability(cap_id, True, note=note, note_ar=note_ar)


def _off(cap_id: str, reason: str, reason_ar: str) -> Capability:
    return Capability(cap_id, False, reason=reason, reason_ar=reason_ar)


# -- integrations (set by main.py) -----------------------------------------------------------

_integrations: Any = None


def configure(integrations: Any) -> None:
    """The API's ``IntegrationRegistry``: an owner's workspace switches are read from it."""
    global _integrations
    _integrations = integrations


def _switched_on(integration_id: str, context: TenantContext, *, default: bool) -> bool:
    if _integrations is None:
        return default
    return bool(_integrations.configuration(integration_id, context).enabled)


# -- one check per capability ----------------------------------------------------------------


def _allowed(context: TenantContext, permission: Permission) -> bool:
    from .dependencies import has_permission

    return has_permission(context, permission)


def _setup_status(item_id: str, context: TenantContext) -> tuple[str, int | None, int]:
    """(status, connections, failing) of one Connection setup entry (integration_setup.py)."""
    from .integration_setup import ENTRIES, evaluate_item

    entry = next(item for item in ENTRIES if item.id == item_id)
    item = evaluate_item(entry, context, details=False)
    return item.status.value, item.connections, item.failing


def _email(context: TenantContext, tools: Collection[str]) -> Capability:
    if "search_email" not in tools:
        return _off("email", _SERVER_EN, _SERVER_AR)
    if not _allowed(context, Permission.EMAIL_READ):
        return _off("email", _ROLE_EN, _ROLE_AR)
    # The Email Center first (ANUM_MAILSERVER_ENABLED, docs/email.md): the planner routes mail
    # requests to its tools when it is on, so a mailbox the caller owns or was shared counts.
    center = _email_center(context)
    if center == "connected":
        return _on("email")
    states = [_setup_status(item, context) for item in ("email_gmail", "email_outlook", "email_zoho")]
    if any(state == "connected" for state, _, _ in states):
        return _on("email")
    if center == "failing":
        return _off("email", "your mailbox in the Email Center cannot be reached: open Email and check it",
                    "تعذر الوصول إلى صندوق بريدك في مركز البريد: افتح البريد وتحقق منه")
    if any(state == "error" and (connections or 0) > 0 for state, connections, _ in states):
        return _off("email", "a connected mailbox needs signing in again: open Email and reconnect it",
                    "صندوق بريد مربوط يحتاج تسجيل الدخول من جديد: افتح البريد وأعد ربطه")
    if any(state in {"ready", "error"} for state, _, _ in states):
        return _off("email", "not connected: connect a mailbox on the Email page",
                    "غير مربوط: اربط صندوق بريد من صفحة البريد")
    if _email_center_enabled():
        return _off("email", "not connected: connect a mailbox on the Email page",
                    "غير مربوط: اربط صندوق بريد من صفحة البريد")
    return _off("email", "not set up on this server: the operator adds a mail provider",
                "غير مجهز على هذا الخادم: يضيف المشغل مزود بريد")


def _email_center_enabled() -> bool:
    from .mailbox.config import mail_settings

    return bool(mail_settings.enabled)


def _email_center(context: TenantContext) -> str | None:
    """``connected`` or ``failing`` for the caller's Email Center mailboxes (own and shared
    with them), or None when the Email Center is off or the caller has none."""
    if not _email_center_enabled():
        return None
    from .mailbox.store import AccountStatus, open_mail_store

    with open_mail_store(context) as store:
        accounts = [*store.list_accounts(), *(account for account, _grant in store.list_shared_accounts())]
    if not accounts:
        return None
    return "connected" if any(account.status == AccountStatus.CONNECTED for account in accounts) else "failing"


def _whatsapp(context: TenantContext, tools: Collection[str]) -> Capability:
    if "list_whatsapp_conversations" not in tools:
        return _off("whatsapp", _SERVER_EN, _SERVER_AR)
    if not _allowed(context, Permission.WHATSAPP_READ):
        return _off("whatsapp", _ROLE_EN, _ROLE_AR)
    state, connections, failing = _setup_status("whatsapp", context)
    if state == "connected":
        return _on("whatsapp")
    if failing:
        return _off("whatsapp", "the WhatsApp account has an error: open WhatsApp and reconnect it",
                    "حساب واتساب فيه خطأ: افتح واتساب وأعد ربطه")
    if state == "ready" or connections == 0:
        return _off("whatsapp", "not connected: connect a WhatsApp Business number in WhatsApp",
                    "غير مربوط: اربط رقم واتساب للأعمال من صفحة واتساب")
    return _off("whatsapp", "not set up on this server: the operator sets the WhatsApp settings",
                "غير مجهز على هذا الخادم: يضبط المشغل إعدادات واتساب")


def _web_search(context: TenantContext, tools: Collection[str]) -> Capability:
    from .web_search import SEARXNG_INTEGRATION_ID

    if "web_search" not in tools:
        return _off("web_search", "not set up on this server: the operator sets ANUM_SEARXNG_URL",
                    "غير مجهز على هذا الخادم: يضبط المشغل ANUM_SEARXNG_URL")
    if not _switched_on(SEARXNG_INTEGRATION_ID, context, default=True):
        return _off("web_search", "off: a workspace owner turns it on under Integrations > SearXNG web search",
                    "متوقف: يشغله مالك مساحة العمل من التكاملات > البحث في الويب SearXNG")
    return _on("web_search")


def _browse(context: TenantContext, tools: Collection[str]) -> Capability:
    from .browser_research import BROWSER_INTEGRATION_ID
    from .browser_research.tool import service_problem
    from .settings import settings

    if "web.browse_page" not in tools or service_problem(settings.browser_research_url, settings.browser_research_token):
        return _off("browse", "not set up on this server: the operator sets up the browser service",
                    "غير مجهز على هذا الخادم: يجهز المشغل خدمة المتصفح")
    if not _switched_on(BROWSER_INTEGRATION_ID, context, default=False):
        return _off("browse", "off: a workspace owner turns it on under Integrations > Browser research",
                    "متوقف: يشغله مالك مساحة العمل من التكاملات > تصفح الويب")
    return _on("browse")


def _act(context: TenantContext, tools: Collection[str]) -> Capability:
    from .action_browser import ACTION_INTEGRATION_ID
    from .browser_research.tool import service_problem
    from .settings import settings

    # The reasons name the operator's switch (docs/action-browser.md#switches-and-availability); the
    # third one, the browser service's action mode, is reported by the tool and Integrations health.
    if "web.act" not in tools or service_problem(settings.browser_research_url, settings.browser_research_token):
        return _off("act", "not set up on this server: the operator sets up the browser service "
                    "(ANUM_BROWSER_RESEARCH_URL and ANUM_BROWSER_RESEARCH_TOKEN)",
                    "غير مجهز على هذا الخادم: يجهز المشغل خدمة المتصفح "
                    "(ANUM_BROWSER_RESEARCH_URL و ANUM_BROWSER_RESEARCH_TOKEN)")
    if not settings.action_browser_enabled:
        return _off("act", "not set up on this server: the operator turns on the action browser "
                    "(ANUM_ACTION_BROWSER_ENABLED=true)",
                    "غير مجهز على هذا الخادم: يشغل المشغل متصفح الإجراءات (ANUM_ACTION_BROWSER_ENABLED=true)")
    if not _switched_on(ACTION_INTEGRATION_ID, context, default=False):
        return _off("act", "off: a workspace owner turns it on under Integrations > Action browser",
                    "متوقف: يشغله مالك مساحة العمل من التكاملات > متصفح الإجراءات")
    return _on("act")


def _commerce(context: TenantContext, tools: Collection[str]) -> Capability:
    if "commerce.sales_summary" not in tools:
        return _off("commerce", _SERVER_EN, _SERVER_AR)
    if not _allowed(context, Permission.COMMERCE_READ):
        return _off("commerce", _ROLE_EN, _ROLE_AR)
    state, _, _ = _setup_status("commerce", context)
    if state in {"connected", "error"}:
        return _on("commerce")
    return _off("commerce", "not connected: connect a sales channel in Commerce",
                "غير مربوط: اربط قناة بيع من صفحة التجارة")


def _calls(context: TenantContext, tools: Collection[str]) -> Capability:
    if "call.search_log" not in tools:
        return _off("calls", _SERVER_EN, _SERVER_AR)
    if not _allowed(context, Permission.CALL_READ):
        return _off("calls", _ROLE_EN, _ROLE_AR)
    from .calls.service import provider_problem
    from .calls.store import open_calls_store

    with open_calls_store(context) as store:
        account = store.get_provider()
    if provider_problem(account) is None:
        return _on("calls")
    return _on("calls", note="placing calls is not connected yet: a workspace owner connects a provider under Calls > Provider",
               note_ar="إجراء المكالمات غير مربوط بعد: يربط مالك مساحة العمل مزودا من المكالمات > المزود")


def _tool_and_role(cap_id: str, tool: str, permission: Permission | None) -> Callable[[TenantContext, Collection[str]], Capability]:
    def check(context: TenantContext, tools: Collection[str]) -> Capability:
        if tool and tool not in tools:
            return _off(cap_id, _SERVER_EN, _SERVER_AR)
        if permission is not None and not _allowed(context, permission):
            return _off(cap_id, _ROLE_EN, _ROLE_AR)
        return _on(cap_id)

    return check


def _finance(context: TenantContext, tools: Collection[str]) -> Capability:
    """The ledger, and whether this person's iPhone SMS forwarding records bank messages (docs/finance-sms.md)."""
    base = _tool_and_role("finance", "finance.summary", Permission.FINANCE_READ)(context, tools)
    if not base.available:
        return base
    from .finance.sms_store import open_sms_stores

    with open_sms_stores(context) as (_finance, store):
        sms = store.get_settings(context)
        waiting = store.status_counts(context).get("review", 0) if sms is not None and sms.enabled else 0
    if sms is None or not sms.enabled:
        return _on("finance", note="iPhone SMS forwarding is off: the person turns it on under Finance, iPhone SMS",
                   note_ar="تحويل رسائل الآيفون متوقف: يشغله الشخص من المالية > رسائل الآيفون")
    return _on("finance", note=f"bank messages forwarded from the iPhone are recorded in the ledger; {waiting} waiting for review",
               note_ar=f"رسائل البنك المحولة من الآيفون تسجل في السجل؛ {waiting} بانتظار المراجعة")


def _tasks(context: TenantContext, tools: Collection[str]) -> Capability:
    if not tools:
        return _off("tasks", "not available on this server: the task runtime is not running here",
                    "غير متاحة على هذا الخادم: مشغل المهام لا يعمل هنا")
    if not (_allowed(context, Permission.TASK_CREATE) and _allowed(context, Permission.TASK_RUN)):
        return _off("tasks", _ROLE_EN, _ROLE_AR)
    return _on("tasks")


CHECKS: list[tuple[str, Callable[[TenantContext, Collection[str]], Capability]]] = [
    ("tasks", _tasks),
    ("approvals", _tool_and_role("approvals", "", Permission.APPROVAL_READ)),
    ("email", _email),
    ("whatsapp", _whatsapp),
    ("calendar", _tool_and_role("calendar", "", Permission.CALENDAR_READ)),
    ("files", _tool_and_role("files", "", Permission.FILE_READ)),
    ("reports", _tool_and_role("reports", "", None)),
    ("web_search", _web_search),
    ("browse", _browse),
    ("act", _act),
    ("business", _tool_and_role("business", "business.send_invoice", Permission.BUSINESS_READ)),
    ("finance", _finance),
    ("commerce", _commerce),
    ("tyre", _tool_and_role("tyre", "tyres_due", Permission.TYRE_READ)),
    ("meetings", _tool_and_role("meetings", "", Permission.MEETING_READ)),
    ("calls", _calls),
    ("automations", _tool_and_role("automations", "", Permission.AUTOMATION_READ)),
    ("marketing", _tool_and_role("marketing", "marketing.summary", Permission.MARKETING_READ)),
]


def runtime_tools() -> set[str]:
    """The tools the chat's task runtime registered (empty without one)."""
    from . import chat_tools

    runtime = chat_tools.configured()
    return set(runtime.tools.names) if runtime is not None else set()


def read_capabilities(context: TenantContext, tools: Collection[str]) -> list[Capability]:
    """Every capability's live state, uncached. Blocking: run it in a worker thread."""
    found: list[Capability] = []
    for cap_id, check in CHECKS:
        try:
            found.append(check(context, tools))
        except Exception as exc:  # one module that cannot be read never hides the rest
            logger.warning("capability_check_failed capability=%s error_class=%s", cap_id, type(exc).__name__)
            found.append(_off(cap_id, _UNKNOWN_EN, _UNKNOWN_AR))
    return found


_cache: OrderedDict[tuple[Any, ...], tuple[float, list[Capability]]] = OrderedDict()
_cache_lock = Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def workspace_capabilities(
    context: TenantContext, *, tools: Collection[str] | None = None, now: Callable[[], float] = time.monotonic
) -> list[Capability]:
    """``read_capabilities`` for the runtime's tools, cached briefly per caller. Blocking."""
    names = runtime_tools() if tools is None else set(tools)
    key = (context.tenant_id, context.workspace_id, context.user_id, tuple(sorted(context.roles)),
           tuple(sorted(context.organization_roles)), tuple(sorted(names)))
    moment = now()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and moment - hit[0] < CACHE_SECONDS:
            _cache.move_to_end(key)
            return list(hit[1])
    fresh = read_capabilities(context, names)
    with _cache_lock:
        _cache[key] = (moment, fresh)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)
    return list(fresh)


def by_id(capabilities: list[Capability], cap_id: str) -> Capability | None:
    return next((item for item in capabilities if item.id == cap_id), None)


# -- text ------------------------------------------------------------------------------------

CHAT_RULES = (
    "Use this list to answer what ANUM can do here, in ANUM's terms: what is on, and for what is "
    "off, the reason and where to turn it on. Never answer with a generic description of a chatbot. "
    "For a request that something on the list can do (read mail, browse or act on a website, send, "
    "book, create or assign work), never say you cannot do it: say ANUM does it as a task that runs "
    "under the workspace's approvals, and give the words that start it (a website task names the "
    "site's https address). For something that is off, say it is off and how to turn it on."
)


_PARENTHESES = re.compile(r"\s*\([^)]*\)")
SPOKEN_NAMES = 7


def _spoken_list(names: list[str], *, arabic: bool) -> str:
    if len(names) <= SPOKEN_NAMES:
        return ("، " if arabic else ", ").join(names)
    shown = ("، " if arabic else ", ").join(names[:SPOKEN_NAMES])
    more = len(names) - SPOKEN_NAMES
    return f"{shown} و{more} غيرها" if arabic else f"{shown} and {more} more"


def capability_lines(capabilities: list[Capability], *, arabic: bool = False) -> tuple[list[str], list[str]]:
    """(on lines, off lines) for the chat block and its tests."""
    on: list[str] = []
    off: list[str] = []
    for item in capabilities:
        name, name_ar, does, does_ar = LABELS[item.id]
        if item.available:
            note = item.note_ar if arabic else item.note
            line = f"- {name_ar if arabic else name}: {does_ar if arabic else does}"
            on.append(f"{line}; {note}" if note else line)
        else:
            short = _PARENTHESES.sub("", does_ar if arabic else does)
            off.append(f"- {name_ar if arabic else name} ({short}): {item.reason_ar if arabic else item.reason}")
    return on, off


def capability_block(capabilities: list[Capability]) -> str:
    """The chat's trailing system part (English: the model answers in the user's language)."""
    on, off = capability_lines(capabilities)
    parts = [CAPABILITY_HEADER, "On:", *(on or ["- none"])]
    if off:
        parts += ["Not available yet:", *off]
    parts.append(CHAT_RULES)
    return "\n".join(parts)


# The task planner's version (agent_planning.py): a task, a Command Center step or a chat or
# voice request that became a task is answered as text by the planner model when no tool fits.
TASK_RULES = (
    "You are ANUM answering this task in text. Never say you have no access to, or cannot use, something "
    "on this list, and never describe yourself as a chatbot without tools: ANUM does it as a task under the "
    "workspace's approvals. A website errand (book, buy, a form) runs in the Action browser once the request "
    "names the site's https address, and stops for the owner's approval before paying or booking. For "
    "something listed as not available, say it is off, the reason and who turns it on. Read a mistyped word "
    "from its context (a ticker to Riyadh is a ticket) without remarking on the spelling."
)


def task_capability_block(capabilities: list[Capability]) -> str:
    """What ANUM can do here, for the task planner's text answer (short; no secrets)."""
    on, off = capability_lines(capabilities)
    parts = [CAPABILITY_HEADER, "On:", *(on or ["- none"])]
    if off:
        parts += ["Not available yet:", *off]
    parts.append(TASK_RULES)
    return "\n".join(parts)


def spoken_capabilities(capabilities: list[Capability], *, arabic: bool = False) -> str:
    """What voice says to "what can you do?" (and gives its model): two short sentences."""
    on = [LABELS[item.id][1 if arabic else 0] for item in capabilities if item.available and item.id != "approvals"]
    off = [item for item in capabilities if not item.available]
    if arabic:
        text = ("في مساحة العمل هذه أستطيع عبر ANUM: " + _spoken_list(on, arabic=True) + ". ") if on else "لا شيء مفعل بعد في مساحة العمل هذه. "
        text += "كل ما يرسل أو يدفع أو يتصرف خارج ANUM ينتظر موافقتك."
        if off:
            first = off[0]
            text += f" غير متاح بعد: {'، '.join(LABELS[item.id][1] for item in off[:4])}؛ {LABELS[first.id][1]}: {first.reason_ar}."
        return text
    text = ("Here ANUM can handle: " + _spoken_list(on, arabic=False) + ". ") if on else "Nothing is switched on in this workspace yet. "
    text += "Anything that sends, pays or acts outside ANUM waits for your approval."
    if off:
        first = off[0]
        text += f" Not on yet: {', '.join(LABELS[item.id][0] for item in off[:4])}; {LABELS[first.id][0]} is {first.reason}."
    return text


def voice_block(capabilities: list[Capability]) -> str:
    """Voice's trailing system part: the spoken summary plus the routing rule, kept short."""
    return (
        f"{CAPABILITY_HEADER} {spoken_capabilities(capabilities)} "
        "Answer what you can do from this, never as a generic chatbot. For a request ANUM can do, never "
        "say you cannot: say ANUM does it as a task under approvals and give the words that start it."
    )


# -- routing words (the user's own words only, threat model G5) ------------------------------

_CAPABILITY_QUESTION = re.compile(
    r"\b(what\s+(else\s+)?(can|could)\s+(you|anum)\s+(do|help)|what\s+are\s+you\s+able\s+to|what\s+do\s+you\s+do"
    r"|how\s+can\s+you\s+help|what\s+(are\s+)?your\s+(capabilities|features|skills)|what\s+can\s+i\s+ask)\b"
    r"|ماذا\s+تستطيع|ماذا\s+يمكنك|شو\s+بتقدر|ايش\s+تقدر|إيش\s+تقدر|ما\s+الذي\s+تستطيع|كيف\s+تساعدني|ما\s+هي\s+قدراتك",
    re.IGNORECASE,
)
# A request for an errand on a website, without its address: the verb leads the sentence.
_ERRAND_VERBS = frozenset({
    "book", "reserve", "buy", "purchase", "order", "checkout", "fill", "submit", "register", "signup",
    "sign-up", "renew", "subscribe", "download", "apply",
    "احجز", "احجزي", "اشتر", "اشتري", "اطلب", "املأ", "املا", "سجل", "جدد", "نزل", "حمل", "قدم", "اشترك", "أحجز", "أشتري",
})
_LEAD = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay)\s+)?(?:anum[,!.]?\s+)?(?:please\s+|pls\s+|kindly\s+)?"
    r"(?:(?:can|could|would|will)\s+(?:you|anum)\s+(?:please\s+)?|i\s+(?:want|need)\s+(?:you\s+)?to\s+|i(?:'d|\s+would)\s+like\s+(?:you\s+)?to\s+|help\s+me\s+|go\s+(?:and\s+)?)?"
    r"(?:من\s+فضلك\s+|لو\s+سمحت\s+|ممكن\s+|أريد\s+أن\s+|اريد\s+ان\s+)?",
    re.IGNORECASE,
)
# "go to example.com", "open www.example.ae": a site named without https:// (common endings only,
# so "open config.yaml" is not a site).
_GO_TO_SITE = re.compile(
    r"\b(go\s+to|open|visit|log\s*in\s+to|sign\s*in\s+to)\s+(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|ae|sa|qa|om|kw|bh|io|co|uk|gov|edu|info|biz|me|app|store|shop|travel)\b",
    re.IGNORECASE,
)
_READ_SITE = re.compile(r"^\s*(?:please\s+)?(open|visit|read)\b", re.IGNORECASE)


def asks_what_anum_can_do(question: str) -> bool:
    return bool(_CAPABILITY_QUESTION.search(question or ""))


def asks_web_errand(question: str) -> bool:
    """"book me a flight Friday", "go to example.com and download my statement": an errand
    on a website. Only the verb that leads the request counts, so "a good book" never does."""
    text = (question or "").strip()
    if not text:
        return False
    lead = _LEAD.match(text)
    first = re.findall(r"[\w-]+", text[lead.end() if lead else 0:].lower())[:1]
    if first and first[0] in _ERRAND_VERBS:
        return True
    return bool(_GO_TO_SITE.search(text))


# A booking noun (and common typos of it): with a soft verb ("get me a ticket", "find me a flight")
# the request is an errand too. "ticker" next to a booking verb is read as "ticket", never remarked on.
_BOOKING_NOUNS = frozenset({
    "ticket", "tickets", "ticker", "tickers", "tiket", "tikets", "tickt", "flight", "flights", "flght", "fligt",
    "hotel", "hotels", "hotle", "room", "rooms", "table", "seat", "seats", "appointment", "reservation", "visa",
    "تذكرة", "تذكره", "تذاكر", "رحلة", "رحله", "طيران", "فندق", "غرفة", "طاولة", "مقعد", "موعد", "تأشيرة", "حجز",
})
_SOFT_VERBS = frozenset({"get", "find", "arrange", "sort", "need", "want", "أبغى", "ابغى", "ابي", "أبي", "أريد", "اريد", "ابحث"})
_STATUS_WORDS = frozenset({"status", "track", "tracking", "where", "حالة", "تتبع", "وين", "أين"})
# Typos fixed only in the search query built from the request (never in the goal the owner approves).
_QUERY_TYPOS = {
    "ticker": "ticket", "tickers": "tickets", "tiket": "ticket", "tikets": "tickets", "tickt": "ticket",
    "flght": "flight", "fligt": "flight", "hotle": "hotel",
}
# Any site named without https:// ("on emirates.com"): then the address is asked for, not searched.
_BARE_SITE = re.compile(
    r"\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:com|net|org|ae|sa|qa|om|kw|bh|io|co|uk|gov|edu|info|biz|me|app|store|shop|travel)\b",
    re.IGNORECASE,
)


def _lead_words(question: str) -> list[str]:
    text = (question or "").strip()
    lead = _LEAD.match(text)
    return re.findall(r"[\w-]+", text[lead.end() if lead else 0:].lower())


def asks_booking(question: str) -> bool:
    """"get me a ticker to Riyadh", "I need a flight Friday": a soft verb near the start and a booking noun."""
    words = _lead_words(question)
    return bool(set(words[:4]) & _SOFT_VERBS) and bool(set(words) & _BOOKING_NOUNS)


def site_errand(question: str) -> bool:
    """An errand on a website that names no site at all ("book me a flight to Riyadh on Friday").

    ANUM then searches for sites that can do it and asks which one to use (or asks for the
    address when web search is off); it never refuses and never picks a site itself.
    """
    text = question or ""
    if "https://" in text.lower() or "http://" in text.lower() or _BARE_SITE.search(text):
        return False
    if not (asks_web_errand(text) or asks_booking(text)):
        return False
    if set(re.findall(r"[\w-]+", text.lower())) & _STATUS_WORDS:
        # "order status for 123": a check on something that exists, not an errand to search sites for.
        return False
    return errand_capability(text) == "act"


def errand_query(question: str) -> str:
    """The web search for sites that can do the errand, from the user's own words only."""
    words = re.findall(r"[\w-]+", (question or "").strip())
    lead = _LEAD.match((question or "").strip())
    if lead and lead.end():
        words = re.findall(r"[\w-]+", (question or "").strip()[lead.end():])
    fixed = [_QUERY_TYPOS.get(word.lower(), word) for word in words if word.lower() not in {"me", "please", "pls", "لي"}]
    arabic = any(_ARABIC_CHAR.match(word) for word in fixed)
    query = " ".join(fixed)[:300]
    return f"{query} {'موقع' if arabic else 'online'}".strip()


def errand_route(question: str, category: str | None, tool: str) -> bool:
    """Whether chat or voice treats the request as a website errand without its address: a plain
    reply's route that leads with an errand verb, or the site search the planner chose for one."""
    if category is None:
        return asks_web_errand(question)
    return tool == "web_search" and site_errand(question)


def errand_goal(question: str) -> str:
    """The request as the owner would send it again with a site (typos fixed, one line)."""
    words = (question or "").split()
    fixed = [_QUERY_TYPOS.get(word.lower(), word) for word in words]
    return " ".join(fixed).rstrip(".!?؟")[:300]


_ARABIC_CHAR = re.compile(r"[\u0600-\u06FF]")


def errand_capability(question: str) -> str:
    """Which browser an errand without an address needs: reading a page, or acting on a site."""
    if _READ_SITE.search(question or "") and not (prompt_verbs(question) & _ERRAND_VERBS):
        return "browse"
    return "act"


def prompt_verbs(question: str) -> set[str]:
    return set(re.findall(r"[\w-]+", (question or "").lower()))


def unavailable_text(capability: Capability, *, arabic: bool = False) -> str:
    name, name_ar, _does, _does_ar = LABELS[capability.id]
    if arabic:
        return f"{name_ar} غير متاح الآن، {capability.reason_ar}. عندما يعمل، ينفذ ANUM هذا الطلب كمهمة تحت الموافقات."
    return f"{name} is not available right now, {capability.reason}. Once it is on, ANUM runs this request as a task under approvals."


def needs_site_text(capability_id: str = "act", *, arabic: bool = False) -> str:
    if capability_id == "browse":
        if arabic:
            return ("يستطيع ANUM فتح الصفحة وقراءتها كمهمة، ويحتاج عنوانها الكامل. "
                    "اكتب الطلب مع عنوان https، مثلا: افتح https://www.example.com واقرأها")
        return ("ANUM can open and read the page as a task; it needs the full address. "
                "Ask again with the https address, for example: open https://www.example.com and read it")
    if arabic:
        return ("يستطيع ANUM تنفيذ هذا بمتصفح الإجراءات كمهمة تحت الموافقات، ويحتاج عنوان الموقع. "
                "اكتب الطلب مع عنوان https للموقع، مثلا: احجز رحلة يوم الجمعة على https://www.example.com")
    return ("ANUM can do this with the action browser as a task under approvals; it needs the site to use. "
            "Ask again with the site's https address, for example: book a flight for Friday on https://www.example.com")


def needs_site_spoken(*, arabic: bool = False) -> str:
    """The spoken form: addresses are typed, not said."""
    if arabic:
        return ("يستطيع ANUM تنفيذ هذا بمتصفح الإجراءات كمهمة تحت الموافقات، ويحتاج الموقع. "
                "اكتب الطلب في محادثة المساعد مع عنوان https للموقع.")
    return ("ANUM can do this with the action browser as a task under approvals; it needs the site. "
            "Type the request in the Assistant chat with the site's https address.")
