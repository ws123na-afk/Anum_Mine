"""Accept-Language for the API's user-facing sentences (docs/web-localization.md).

The clients show some sentences the API writes: a refusal's ``error.message`` (403, 404, 409,
402 with the budget's reset date, 422, 429, 500), and the retention and account notes on the
Privacy page. A request that asks for Arabic (``Accept-Language: ar``, the web sends the page
language) gets them in Arabic; everything else stays English. Error ``code`` values, field
names, ids and ``details`` never change, so clients and scripts keep matching on codes.

English is the source: a message without an Arabic entry is answered in English.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Literal

from fastapi import Request

Language = Literal["en", "ar"]
SUPPORTED: tuple[Language, ...] = ("en", "ar")
DEFAULT_LANGUAGE: Language = "en"


def negotiate_language(header: str | None) -> Language:
    """The best supported language in an ``Accept-Language`` value (q-weights honoured)."""
    if not header:
        return DEFAULT_LANGUAGE
    best: tuple[float, int, Language] | None = None
    for position, part in enumerate(header.split(",")[:20]):
        tag, _, params = part.strip().partition(";")
        primary = tag.strip().lower().split("-")[0].split("_")[0]
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            continue
        if primary in SUPPORTED:
            candidate = (quality, -position, primary)  # type: ignore[assignment]
            if best is None or candidate > best:
                best = candidate  # type: ignore[assignment]
        elif primary == "*" and best is None:
            best = (quality, -position, DEFAULT_LANGUAGE)
    return best[2] if best else DEFAULT_LANGUAGE


def request_language(request: Request) -> Language:
    """FastAPI dependency: the caller's language from ``Accept-Language``."""
    return negotiate_language(request.headers.get("accept-language"))


def scope_language(headers: Mapping[str, str] | list[tuple[bytes, bytes]]) -> Language:
    """The language from raw ASGI headers (middleware that answers before FastAPI)."""
    if isinstance(headers, Mapping):
        return negotiate_language(headers.get("accept-language"))
    for name, value in headers:
        if name.lower() == b"accept-language":
            return negotiate_language(value.decode("latin-1"))
    return DEFAULT_LANGUAGE


# --------------------------------------------------------------------------- refusals

# Exact English messages the clients show, with their Arabic. Keep the English identical to the
# raising code: tests/test_localization.py checks that every key is still raised somewhere.
ARABIC_MESSAGES: dict[str, str] = {
    # Generic envelopes
    "Request validation failed": "تعذّر التحقق من الطلب.",
    "An unexpected error occurred": "حدث خطأ غير متوقع.",
    "Too many requests": "طلبات كثيرة جدًا. انتظر قليلًا ثم حاول مرة أخرى.",
    "Invalid Content-Length": "طول المحتوى غير صالح.",
    "Bad Request": "طلب غير صالح.",
    "Unauthorized": "يلزم تسجيل الدخول.",
    "Forbidden": "غير مسموح.",
    "Not Found": "غير موجود.",
    # Who may do what
    "Workspace permission denied": "دورك في مساحة العمل هذه لا يسمح بذلك.",
    "Active workspace membership required": "يلزم أن تكون عضوًا نشطًا في مساحة العمل هذه.",
    "Missing ANUM tenant context headers": "ينقص الطلبَ سياق المؤسسة ومساحة العمل.",
    "Select a workspace with the x-workspace-id header": "اختر مساحة عمل (الترويسة x-workspace-id).",
    "x-workspace-id is not a valid workspace identifier": "قيمة x-workspace-id ليست معرّف مساحة عمل صالحًا.",
    "x-tenant-id does not match the authenticated tenant": "قيمة x-tenant-id لا تطابق المؤسسة التي سجّلت الدخول إليها.",
    "Identity provider is unavailable": "مزوّد الهوية غير متاح.",
    "Organization administrator role required: this setting applies to every workspace in the "
    "organization, so owning a workspace is not enough. Ask the administrator of your ANUM sign-in "
    "(Keycloak) to assign you the realm role org_admin (on a self-hosted server: "
    "infra/oracle/keycloak-2fa.sh grant-org-admin <your username>), then sign out and sign in again": (
        "يلزم دور مسؤول المؤسسة: يسري هذا الإعداد على كل مساحات العمل في المؤسسة، لذا لا يكفي أن تملك "
        "مساحة عمل. اطلب من مسؤول حساب تسجيل الدخول إلى ANUM (Keycloak) أن يمنحك دور النطاق org_admin "
        "(على خادم مستضاف ذاتيًا: infra/oracle/keycloak-2fa.sh grant-org-admin <اسم المستخدم>)، ثم سجّل "
        "الخروج وسجّل الدخول مرة أخرى."
    ),
    "This workspace is archived, so it is read-only. A workspace owner or an organization "
    "administrator can restore it in Settings > Workspace.": (
        "مساحة العمل هذه مؤرشفة، لذا فهي للقراءة فقط. يستطيع مالك مساحة العمل أو مسؤول المؤسسة "
        "استعادتها من الإعدادات > مساحة العمل."
    ),
    # Members and invitations
    "The last active owner cannot be demoted or deactivated": "لا يمكن خفض دور آخر مالك نشط أو إيقافه.",
    "Member not found": "العضو غير موجود.",
    "Invitation not found": "الدعوة غير موجودة.",
    "This invitation is for a different user": "هذه الدعوة لمستخدم آخر.",
    "This invitation is for a different verified email address": "هذه الدعوة لبريد إلكتروني موثّق آخر.",
    "You are already an active member of this workspace": "أنت عضو نشط في مساحة العمل هذه بالفعل.",
    "This workspace is archived. Ask a workspace owner to restore it, then accept again.": (
        "مساحة العمل هذه مؤرشفة. اطلب من مالكها استعادتها، ثم اقبل الدعوة مرة أخرى."
    ),
    "Workspace not found": "مساحة العمل غير موجودة.",
    "That workspace ID is already in use": "معرّف مساحة العمل هذا مستخدم بالفعل.",
    "Type the workspace's current name exactly to confirm archiving it": (
        "اكتب الاسم الحالي لمساحة العمل كما هو تمامًا لتأكيد أرشفتها."
    ),
    "Complete onboarding for this workspace first": "أكمل إعداد مساحة العمل هذه أولًا.",
    # Tasks, runs and approvals
    "Task not found": "المهمة غير موجودة.",
    "Agent run not found": "تشغيل الوكيل غير موجود.",
    "Approval not found": "الموافقة غير موجودة.",
    "Task is being processed by another request": "تُعالَج المهمة في طلب آخر الآن. حاول بعد قليل.",
    "Run coordination is unavailable": "تنسيق التشغيل غير متاح الآن.",
    "Unknown or invalid cursor": "مؤشر الصفحة غير معروف أو غير صالح.",
    "Unknown cursor": "مؤشر الصفحة غير معروف.",
    # Integrations, voice, model settings
    "Integration not found": "التكامل غير موجود.",
    "Marketplace package not found": "حزمة السوق غير موجودة.",
    "Model configuration not found": "إعداد النموذج غير موجود.",
    "Voice session not found": "جلسة الصوت غير موجودة.",
    "Voice session is closed": "جلسة الصوت مغلقة.",
    "Voice question limit reached": "بلغت حد الأسئلة الصوتية.",
    "Action not found": "الإجراء غير موجود.",
    "This action was already undone.": "تم التراجع عن هذا الإجراء بالفعل.",
    "This action cannot be undone from ANUM.": "لا يمكن التراجع عن هذا الإجراء من ANUM.",
    "The undo window has closed; reverse it in the connected service if you still need to.": (
        "انتهت مهلة التراجع؛ اعكس الإجراء في الخدمة المتصلة إن كنت لا تزال تحتاج إلى ذلك."
    ),
}

# Messages with values in them: (pattern, Arabic template using the named groups).
_PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    (
        re.compile(r"^Type (?P<phrase>.+) exactly to confirm\. Nothing was deleted\.$"),
        lambda m: f"اكتب {m['phrase']} كما هي تمامًا للتأكيد. لم يُحذف شيء.",
    ),
    (
        re.compile(
            r"^Your data is larger than this server's export limit \((?P<mb>\d+) MB\)\. Delete conversations you no "
            r"longer need, or ask your administrator to raise ANUM_PRIVACY_EXPORT_MAX_BYTES, then try again\.$"
        ),
        lambda m: (
            f"بياناتك أكبر من حد التصدير في هذا الخادم ({m['mb']} ميغابايت). احذف المحادثات التي لم تعد تحتاج "
            "إليها، أو اطلب من المسؤول رفع ANUM_PRIVACY_EXPORT_MAX_BYTES، ثم حاول مرة أخرى."
        ),
    ),
    (
        # privacy.revoke_mailboxes: a mailbox whose access ANUM could not withdraw.
        re.compile(
            r"^ANUM deleted (?P<address>\S+) and its tokens, but could not withdraw its access at (?P<name>.+?)\. "
            r"Remove ANUM in your (?P=name) account's security settings \(third-party apps\)\.$"
        ),
        lambda m: (
            f"حذف ANUM صندوق البريد {m['address']} ورموزه، لكنه لم يتمكن من سحب صلاحيته لدى {m['name']}. "
            f"أزل ANUM من إعدادات الأمان في حساب {m['name']} (تطبيقات الجهات الخارجية)."
        ),
    ),
    (
        # model_budget.budget_exceeded_message, for refusals that only carry the sentence.
        re.compile(
            r"^This (?P<owner>organization|workspace) has used its monthly model budget \((?P<measure>estimated cost|tokens)\)\. "
            r"It resets on (?P<date>\d{4}-\d{2}-\d{2}) \(UTC\); an owner can raise it in Settings\.$"
        ),
        lambda m: budget_sentence_ar(m["owner"], m["measure"], m["date"]),
    ),
]


def budget_sentence_ar(owner: str, measure: str, iso_date: str) -> str:
    """The Arabic budget refusal; the date stays ISO (Western digits), as in English."""
    whose = "المؤسسة" if owner == "organization" else "مساحة العمل"
    what = "التكلفة التقديرية" if measure == "estimated cost" else "الرموز"
    return (
        f"استهلكت {whose} ميزانية النموذج الشهرية ({what}). يُعاد ضبطها في {iso_date} (UTC)؛ "
        "ويستطيع المالك رفعها من الإعدادات."
    )


def localize_message(message: str, language: Language) -> str:
    """``message`` in ``language``: the Arabic catalog entry or pattern, else unchanged."""
    if language != "ar" or not message:
        return message
    exact = ARABIC_MESSAGES.get(message)
    if exact is not None:
        return exact
    for pattern, render in _PATTERNS:
        match = pattern.match(message)
        if match:
            return render(match)
    return message


# --------------------------------------------------------------------------- privacy notes

PRIVACY_NOTES_AR: dict[str, str] = {
    "private_memories": "تُحفظ حتى تحذفها (من الذاكرة، أو من «احذف بياناتي»).",
    "task_memories": "تُحفظ مع مهمتها، أو حتى موعد الانتهاء الذي حُدّد عند حفظها.",
    "audit_records": "تُحفظ بوصفها سجل الأمان لمساحة العمل؛ وتضم معرّفات وأعدادًا لا محتواك.",
    "account_note": (
        "حساب تسجيل الدخول الخاص بك يديره مزوّد الهوية في مؤسستك، لا ANUM. لإغلاقه، اطلب ذلك من مالك "
        "مساحة العمل أو من مسؤول المؤسسة."
    ),
    "shared_note": (
        "المهام والتشغيلات والموافقات والذكريات المشتركة والملفات وصناديق بريد مساحة العمل التي ربطتها "
        "والرسائل التي أرسلتها أو طلبت الموافقة على إرسالها ورسائل واتساب التي أرسلتها والإجراءات التي نفّذها "
        "ANUM لك تخص مساحة العمل، لذا تبقى. اطلب من مالك مساحة العمل إزالتها."
    ),
}
