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
    # Agent controls (anum_api/agent_controls.py)
    "The emergency stop is on for this organization: agents, automations and outbound sends are paused. The organization owner can turn it off under Agent controls.": "الإيقاف الطارئ مفعّل لهذه المؤسسة: الوكلاء والأتمتة والإرسال الخارجي متوقفة مؤقتًا. يمكن لمالك المؤسسة إيقافه من «تحكم الوكلاء».",
    "Only a workspace owner or the organization administrator can turn on the emergency stop.": "لا يمكن تفعيل الإيقاف الطارئ إلا لمالك مساحة العمل أو مسؤول المؤسسة.",
    "Only the organization owner (the organization administrator role) can turn the emergency stop off.": "لا يمكن إيقاف الإيقاف الطارئ إلا لمالك المؤسسة (دور مسؤول المؤسسة).",
    "Only a workspace owner or the organization administrator can see agent cost caps.": "لا يمكن عرض حدود تكلفة الوكلاء إلا لمالك مساحة العمل أو مسؤول المؤسسة.",
    "Only a workspace owner can change an agent's monthly cost cap.": "لا يمكن تغيير حد التكلفة الشهري للوكيل إلا لمالك مساحة العمل.",
    "Only a workspace owner or the organization administrator can see what the emergency stop holds.": "لا يمكن عرض ما يحتجزه الإيقاف الطارئ إلا لمالك مساحة العمل أو مسؤول المؤسسة.",
    "The emergency stop is already on.": "الإيقاف الطارئ مفعّل بالفعل.",
    "The emergency stop is already off.": "الإيقاف الطارئ متوقف بالفعل.",
    # Live agent meetings (anum_api/live_agent_meeting_api.py)
    "Only a workspace owner can read meeting transcripts. You can still see that the meeting took place, who attended and its status.": "لا يقرأ نصوص الاجتماعات إلا مالك مساحة العمل. ما زال بإمكانك رؤية انعقاد الاجتماع ومن حضره وحالته.",
    # In-app calls (anum_api/app_calls.py)
    "Calls are off on this server until a relay (TURN) server is configured": "المكالمات متوقفة على هذا الخادم حتى يُضبط خادم ترحيل (TURN).",
    "Video calls are turned off on this server": "مكالمات الفيديو متوقفة على هذا الخادم.",
    "You cannot call yourself": "لا يمكنك الاتصال بنفسك.",
    "This person is not an active member of this workspace": "هذا الشخص ليس عضوًا نشطًا في مساحة العمل هذه.",
    "You are already in a call": "أنت في مكالمة بالفعل.",
    "Call not found": "لم يُعثر على المكالمة.",
    "Only the person being called can answer": "لا يرد على المكالمة إلا الشخص المتصَل به.",
    "This call is no longer ringing": "لم تعد هذه المكالمة ترن.",
    "This call is not in progress": "هذه المكالمة ليست جارية.",
    "Only the caller sends the offer": "المتصل وحده يرسل العرض.",
    "Only the person called sends the answer": "الشخص المتصَل به وحده يرسل الرد.",
    "Too many signals for this call": "إشارات كثيرة جدًا لهذه المكالمة.",
    "An offer or answer carries an sdp and no candidates": "العرض أو الرد يحمل وصف جلسة ولا يحمل مرشحين.",
    "An ice signal carries candidates and no sdp": "إشارة ICE تحمل مرشحين ولا تحمل وصف جلسة.",
    # Customer billing (anum_api/billing/)
    "This organisation is read-only because an invoice is overdue and the grace period has ended. "
    "Everything stays readable. A billing admin can settle the invoice in Settings > Billing; "
    "writes come back as soon as the payment is recorded.": (
        "هذه المؤسسة للقراءة فقط لأن فاتورة متأخرة وانتهت مهلة السماح. تبقى كل البيانات متاحة للقراءة. "
        "يستطيع مسؤول الفوترة سداد الفاتورة من الإعدادات > الفوترة، وتعود الكتابة فور تسجيل الدفعة."
    ),
    "Only a workspace owner of this organisation or an organisation administrator (realm role org_admin) "
    "can see and change billing. Ask one of them, or ask a workspace owner to make you an owner.": (
        "لا يرى الفوترة ولا يغيّرها إلا مالك مساحة عمل في هذه المؤسسة أو مسؤول المؤسسة (دور النطاق org_admin). "
        "اطلب من أحدهم، أو اطلب من مالك مساحة عمل أن يجعلك مالكًا."
    ),
    "Invoice not found.": "الفاتورة غير موجودة.",
    "Verify your email address first: open the link Keycloak sent you, then try again.": (
        "وثّق بريدك الإلكتروني أولًا: افتح الرابط الذي أرسله Keycloak إليك، ثم حاول مرة أخرى."
    ),
    "This account already created an organisation; each account creates one.": (
        "أنشأ هذا الحساب مؤسسة من قبل؛ ينشئ كل حساب مؤسسة واحدة."
    ),
    "Too many sign-ups from your network in the last hour. Try again later.": (
        "عمليات تسجيل كثيرة جدًا من شبكتك في الساعة الأخيرة. حاول لاحقًا."
    ),
    "Self-serve sign-up is not open on this server; contact the ANUM team for an account.": (
        "التسجيل الذاتي غير متاح على هذا الخادم؛ تواصل مع فريق ANUM للحصول على حساب."
    ),
    "That plan is not offered; choose one of the plans on the pricing page.": (
        "هذه الخطة غير معروضة؛ اختر إحدى الخطط في صفحة الأسعار."
    ),
    # Reception visitor register (anum_api/office_visitors_api.py)
    "Confirm this on screen with a click. A spoken or typed answer cannot change the visitor register.": (
        "أكّد ذلك على الشاشة بنقرة. لا تغيّر إجابة منطوقة أو مكتوبة سجل الزوار."
    ),
    "Visit not found": "الزيارة غير موجودة.",
    "The visit changed; reload and try again": "تغيّرت الزيارة؛ أعد التحميل وحاول مرة أخرى.",
    "A visit with this id already exists": "توجد زيارة بهذا المعرّف بالفعل.",
    "A finished visit cannot be changed; delete it or add a new visit": "لا يمكن تغيير زيارة انتهت؛ احذفها أو أضف زيارة جديدة.",
    "Only an expected visitor can be checked in": "لا يُسجَّل دخول إلا زائر متوقَّع.",
    "Only a checked-in visitor can be checked out": "لا يُسجَّل خروج إلا زائر سُجّل دخوله.",
    "Only an expected visit can be cancelled": "لا تُلغى إلا زيارة متوقَّعة.",
    "That building or room does not exist in this workspace": "هذا المبنى أو هذه الغرفة غير موجودة في مساحة العمل هذه.",
    "The room is not in that building": "الغرفة ليست في هذا المبنى.",
    "The host is not an active member or a visible agent of this workspace": "المضيف ليس عضوًا نشطًا ولا وكيلًا ظاهرًا في مساحة العمل هذه.",
    "Give both the host kind and the host, or neither": "حدّد نوع المضيف والمضيف معًا، أو اتركهما معًا.",
    "The expected time must have a time zone and be within 400 days of today": "يجب أن يحمل الوقت المتوقَّع منطقة زمنية وأن يقع خلال 400 يوم من اليوم.",
    "Give both since and until, with since before until": "حدّد بداية الفترة ونهايتها معًا، على أن تسبق البداية النهاية.",
    "The visitor's name, the purpose and the expected time cannot be empty": "لا يمكن ترك اسم الزائر والغرض والوقت المتوقَّع فارغة.",
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
    # Owner-triggered office actions (anum_api/office_green_concrete_api.py, office_starter_campus_api.py)
    "Confirm this on screen with a click. A spoken or typed answer cannot confirm it.": "أكّد هذا على الشاشة بنقرة. لا يمكن تأكيده بإجابة منطوقة أو مكتوبة.",
    "What would be created has changed since you opened it. Review the preview again.": "تغيّر ما سيُنشأ منذ فتحت المعاينة. راجع المعاينة مرة أخرى.",
    "Invalid IANA time zone": "منطقة زمنية غير صالحة.",
    "A room with the same name already exists on the floor. Nothing was created.": "توجد غرفة بالاسم نفسه على هذا الطابق. لم يُنشأ شيء.",
    "Another floor already uses this level. Nothing was created.": "طابق آخر يستخدم هذا المستوى بالفعل. لم يُنشأ شيء.",
    "The room would overlap another room. Nothing was created.": "ستتداخل الغرفة مع غرفة أخرى. لم يُنشأ شيء.",
    "The room would fall outside its floor. Nothing was created.": "ستقع الغرفة خارج طابقها. لم يُنشأ شيء.",
    "A record conflicts with one that already exists. Nothing was created.": "يتعارض سجل مع سجل موجود بالفعل. لم يُنشأ شيء.",
    # Building logo signs (anum_api/office_logo_api.py, office_logo_image.py)
    "Confirm this on screen with a click. A spoken or typed answer cannot change a building's logo.": "أكّد هذا على الشاشة بنقرة. لا يمكن تغيير شعار المبنى بإجابة منطوقة أو مكتوبة.",
    "This building has no logo.": "لا يوجد شعار لهذا المبنى.",
    "SVG logos are not accepted because they can carry script. Export the logo as PNG, JPEG or WebP.": "لا تُقبل شعارات SVG لأنها قد تحمل شيفرة. صدّر الشعار بصيغة PNG أو JPEG أو WebP.",
    "GIF logos are not accepted. Use a PNG, JPEG or WebP picture.": "لا تُقبل شعارات GIF. استخدم صورة بصيغة PNG أو JPEG أو WebP.",
    "This file is not a PNG, JPEG or WebP picture. Choose a logo in one of those formats.": "هذا الملف ليس صورة PNG أو JPEG أو WebP. اختر شعارًا بإحدى هذه الصيغ.",
    "The file is empty. Choose a logo picture.": "الملف فارغ. اختر صورة للشعار.",
    "The logo is larger than 1 MB. Export a smaller picture (512 by 512 pixels is plenty).": "حجم الشعار أكبر من 1 ميغابايت. صدّر صورة أصغر (512 في 512 بكسل تكفي).",
    "The content of this file does not match a PNG, JPEG or WebP picture.": "محتوى هذا الملف لا يطابق صورة PNG أو JPEG أو WebP.",
    "The picture is too large (over 4096 pixels on a side). Export it at 512 by 512 pixels.": "الصورة كبيرة جدًا (أكثر من 4096 بكسل في أحد الأضلاع). صدّرها بحجم 512 في 512 بكسل.",
    "Animated pictures are not accepted. Use a still PNG, JPEG or WebP picture.": "الصور المتحركة غير مقبولة. استخدم صورة ثابتة بصيغة PNG أو JPEG أو WebP.",
    "The picture is too small. Use at least 16 by 16 pixels, 256 by 256 or more looks best.": "الصورة صغيرة جدًا. استخدم 16 في 16 بكسل على الأقل، والأفضل 256 في 256 أو أكثر.",
    "This picture could not be read. It may be damaged. Export it again as PNG, JPEG or WebP.": "تعذّرت قراءة هذه الصورة، وقد تكون تالفة. صدّرها مرة أخرى بصيغة PNG أو JPEG أو WebP.",
    "The picture is too detailed to store. Export a simpler version of the logo.": "الصورة معقدة جدًا للتخزين. صدّر نسخة أبسط من الشعار.",
    "The picture is fully transparent. Choose a logo that shows something.": "الصورة شفافة بالكامل. اختر شعارًا يظهر فيه شيء.",
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
        # live_agent_meeting_budget.budget_message
        re.compile(
            r"^Monthly agent meeting budget reached: this workspace has used USD (?P<used>[\d.]+) of its USD (?P<limit>[\d.]+) "
            r"for agent meetings this month\. It resets on (?P<date>\d{4}-\d{2}-\d{2}) \(UTC\); "
            r"a workspace owner can raise the limit under Budgets\.$"
        ),
        lambda m: (
            f"تم بلوغ ميزانية اجتماعات الوكلاء الشهرية: استهلكت مساحة العمل {m['used']} دولارًا من أصل {m['limit']} دولارًا "
            f"لاجتماعات الوكلاء هذا الشهر. يُعاد ضبطها في {m['date']} (UTC)؛ ويستطيع مالك مساحة العمل رفع الحد من «الميزانيات»."
        ),
    ),
    (
        # live_agent_meeting_rate.refusal_message
        re.compile(
            r"^(?P<who>You have|This workspace has) started the most agent meeting turns allowed in one minute "
            r"\((?P<limit>\d+)\)\. Wait (?P<seconds>\d+) seconds and try again\.$"
        ),
        lambda m: (
            f"{'بدأت' if m['who'] == 'You have' else 'بدأت مساحة العمل'} أقصى عدد مسموح من أدوار اجتماعات الوكلاء "
            f"في الدقيقة الواحدة ({m['limit']}). انتظر {m['seconds']} ثانية ثم حاول مرة أخرى."
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
