"""Roles and room rules for "Staff my campus" (docs/ANUM_MINE_STAFF_DESKS.md).

Pure data and pure functions. Nothing here reads a database or creates anything.

* ``EXTRA_TEMPLATES`` are the roles the seven governed provisioning templates do not cover (HR and
  People, Procurement, Marketing, Engineering and DevOps, Research Analyst, Security Reviewer,
  Executive Assistant, Receptionist). They use the same ``Template`` shape, so a role is resolved and
  built by exactly the same code as the first seven. Every tool and skill is named by its registry id;
  a capability the platform does not have is a ``Need`` and the agent is marked Limited. Nothing is invented.
* ``template_for_room`` picks the role for the desks of one room from the room's STORED team
  department, its kind and its stored name. A room that matches nothing gets no role and its desks stay
  empty (the preview says so); the owner can set the room's team department to fix that.
* These roles are kept out of ``office_provisioning.TEMPLATES`` on purpose: that list drives the
  Organisation gaps coverage, and a role with no work signal there would add noise.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from . import office_provisioning as op
from .office_provisioning import SAFETY, Need, Template

CATALOG_VERSION = 1

EXTRA_TEMPLATES: tuple[Template, ...] = (
    Template(
        id="hr_people", department="hr", room_department=None,
        name_en="HR and People Assistant", name_ar="مساعد الموارد البشرية",
        description_en="Reads people related email, drafts replies and hands work to the right person. It holds no employee records.",
        description_ar="يقرأ البريد المتعلق بالموظفين ويكتب الردود ويحيل العمل إلى الشخص المناسب. لا يملك سجلات موظفين.",
        instructions=("You assist the HR desk. Read people related email, summarise each in one sentence, draft the reply for a person "
                      "to review and assign follow-up work with the assign tool. You never decide on hiring, pay or discipline." + SAFETY),
        icon="user", color="#be185d", model_route="fast",
        tools=("search_email", "read_email", "summarize_mail", "draft_reply", "team.assign_task"),
        skills=("anum.email", "anum.team-assign", "anum.document-drafting"),
        missing=(
            Need("employee_records", "Tools that read employee records, leave and attendance",
                 "أدوات لقراءة سجلات الموظفين والإجازات والحضور", ("hr.*", "people.*", "employee.*", "employees.*")),
            Need("recruiting_pipeline", "A recruiting pipeline to read candidates from",
                 "مسار توظيف لقراءة المرشحين منه", ("recruiting.*", "candidates.*", "ats.*")),
        ),
    ),
    Template(
        id="procurement", department="procurement", room_department=None,
        name_en="Procurement Assistant", name_ar="مساعد المشتريات",
        description_en="Watches low stock and upcoming bills, reads supplier email and drafts purchase follow-ups for a person to send.",
        description_ar="يراقب المخزون المنخفض والفواتير القادمة ويقرأ بريد الموردين ويكتب متابعات الشراء ليرسلها شخص.",
        instructions=("You assist procurement. Check low stock and upcoming bills, read supplier email and draft the follow-up for a "
                      "person to send. You never place an order and never commit money." + SAFETY),
        icon="shopping-cart", color="#a16207", model_route="fast",
        tools=("commerce.low_stock", "commerce.sales_summary", "finance.bills.upcoming", "search_email", "read_email", "draft_reply",
               "team.assign_task"),
        skills=("anum.commerce", "anum.email", "anum.team-assign", "anum.document-drafting"),
        missing=(
            Need("supplier_records", "Supplier and purchase order records",
                 "سجلات الموردين وأوامر الشراء", ("suppliers.*", "supplier.*", "purchase_orders.*", "procurement.*")),
        ),
    ),
    Template(
        id="marketing_content", department="marketing", room_department=None,
        name_en="Marketing Content Assistant", name_ar="مساعد محتوى التسويق",
        description_en="Drafts social posts and email campaigns and reads campaign results. A person publishes and sends.",
        description_ar="يكتب منشورات التواصل وحملات البريد ويقرأ نتائج الحملات. ينشر ويرسل شخص.",
        instructions=("You assist marketing. Draft social posts and campaign emails, read the campaign summaries and say what worked. "
                      "You only draft; a person schedules, publishes and sends." + SAFETY),
        icon="sparkles", color="#c2410c", model_route="fast",
        tools=("marketing.draft_social_post", "marketing.summary", "brevo.list_lists", "brevo.list_campaigns",
               "brevo.campaign_report", "brevo.draft_campaign"),
        skills=("anum.brevo", "anum.document-drafting"),
        missing=(
            Need("web_analytics", "Website and advertising analytics",
                 "تحليلات الموقع والإعلانات", ("analytics.*", "ads.*", "seo.*")),
        ),
    ),
    Template(
        id="engineering_devops", department="engineering", room_department=None,
        name_en="Engineering and DevOps Assistant", name_ar="مساعد الهندسة والعمليات التقنية",
        description_en="Reads pages and email about engineering work and turns them into assigned tasks. It cannot read code or deployments yet.",
        description_ar="يقرأ الصفحات والبريد عن العمل الهندسي ويحوّلها إلى مهام مسندة. لا يستطيع قراءة الشيفرة أو النشر بعد.",
        instructions=("You assist the engineering desk. Read the pages and email you are given, summarise the technical issue, and assign "
                      "follow-up work with the assign tool. You say plainly that you cannot see code, builds or servers." + SAFETY),
        icon="wrench", color="#1d4ed8", model_route="capable",
        tools=("web.fetch_page", "search_email", "read_email", "summarize_mail", "team.assign_task"),
        skills=("anum.email", "anum.team-assign", "anum.document-drafting"),
        missing=(
            Need("code_repositories", "Read access to code repositories and pull requests",
                 "قراءة مستودعات الشيفرة وطلبات الدمج", ("developer.list*", "developer.get*", "developer.read*", "repo.*", "github.*")),
            Need("deployments_and_logs", "Deployment status, logs and incident feeds",
                 "حالة النشر والسجلات ومصادر الحوادث", ("deploy.*", "deployments.*", "logs.*", "incident.*", "incidents.*")),
        ),
    ),
    Template(
        id="research_analyst", department="research", room_department=None,
        name_en="Research Analyst", name_ar="محلل أبحاث",
        description_en="Reads pages and email you point it at, summarises them with sources and drafts briefing notes.",
        description_ar="يقرأ الصفحات والبريد التي تحددها ويلخصها مع المصادر ويكتب مذكرات إحاطة.",
        instructions=("You are a research analyst. Read the pages and email you are given, summarise them, name the source of every "
                      "claim and say what is not known. You never state a figure that is not in a source you read." + SAFETY),
        icon="search", color="#6d28d9", model_route="capable",
        tools=("web.fetch_page", "search_email", "read_email", "summarize_mail"),
        skills=("anum.email", "anum.document-drafting"),
        missing=(
            Need("data_sources", "Market, company or research data sources",
                 "مصادر بيانات السوق أو الشركات أو الأبحاث", ("research.*", "market.*", "company.*", "news.*")),
        ),
        knowledge_mode="workspace",
    ),
    Template(
        id="security_reviewer", department="security", room_department=None,
        name_en="Security Reviewer", name_ar="مراجع الأمن",
        description_en="Prepares review notes on rules and suspicious mail for a person to act on. It never changes access.",
        description_ar="يعدّ ملاحظات مراجعة للقواعد والبريد المشبوه ليتصرف شخص. لا يغيّر الصلاحيات أبدًا.",
        instructions=("You prepare security review notes. Read the rules and the mail you are shown, flag what looks wrong and say what "
                      "a person should check. You never change access, block or release anything." + SAFETY),
        icon="file-text", color="#9f1239", model_route="capable",
        tools=("finance.guardian.rules", "finance.guardian.check", "classify_mail", "search_email", "read_email"),
        skills=("anum.email", "anum.document-drafting"),
        missing=(
            Need("audit_log_read", "A tool that reads the audit log",
                 "أداة لقراءة سجل التدقيق", ("audit.*",)),
            Need("access_review", "An access and sign-in review feed",
                 "مصدر لمراجعة الصلاحيات وتسجيلات الدخول", ("access.*", "security.*", "identity.*")),
        ),
        knowledge_mode="workspace", write_autonomy="off",
    ),
    Template(
        id="executive_assistant", department="executive", room_department=None,
        name_en="Executive Assistant", name_ar="مساعد تنفيذي",
        description_en="Sorts email and messages, drafts replies and assigns follow-ups. It cannot read or book the calendar yet.",
        description_ar="يرتب البريد والرسائل ويكتب الردود ويسند المتابعات. لا يستطيع قراءة التقويم أو الحجز فيه بعد.",
        instructions=("You assist an executive. Sort email and WhatsApp by importance, summarise each in one sentence, draft the reply "
                      "for the executive to review and assign follow-ups with the assign tool. You never send for them." + SAFETY),
        icon="briefcase", color="#7c3aed", model_route="fast",
        tools=("search_email", "read_email", "summarize_mail", "draft_reply", "list_whatsapp_conversations", "read_whatsapp",
               "draft_whatsapp_reply", "team.assign_task"),
        skills=("anum.email", "anum.whatsapp-inbox", "anum.team-assign", "anum.document-drafting"),
        missing=(
            Need("calendar_read", "A tool that reads or books calendar events",
                 "أداة لقراءة أحداث التقويم أو الحجز فيه",
                 ("calendar.list*", "calendar.read*", "calendar.get*", "calendar.find*", "calendar.create*", "calendar.book*")),
        ),
    ),
    Template(
        id="receptionist", department="reception", room_department=None,
        name_en="Receptionist", name_ar="موظف استقبال",
        description_en="Reads the call log and WhatsApp conversations, drafts replies and hands callers to the right person.",
        description_ar="يقرأ سجل المكالمات ومحادثات واتساب ويكتب الردود ويحيل المتصلين إلى الشخص المناسب.",
        instructions=("You are the receptionist. Read the call log and WhatsApp conversations, say who called or wrote and what they "
                      "need, draft a reply for a person to review and hand the caller on with the assign tool." + SAFETY),
        icon="phone", color="#0e7490", model_route="fast",
        tools=("call.search_log", "call.search_contacts", "call.get", "list_whatsapp_conversations", "read_whatsapp",
               "draft_whatsapp_reply", "team.assign_task"),
        skills=("anum.whatsapp-inbox", "anum.team-assign"),
        missing=(
            Need("visitor_log", "A visitor log and the calendar for appointments",
                 "سجل زوار وتقويم للمواعيد", ("visitors.*", "visitor.*", "appointments.*", "calendar.list*", "calendar.read*")),
        ),
    ),
)

# Every role that can staff a desk: the seven governed ones first, then the eight above.
STAFF_TEMPLATES: tuple[Template, ...] = (*op.TEMPLATES, *EXTRA_TEMPLATES)
STAFF_TEMPLATE_BY_ID: dict[str, Template] = {t.id: t for t in STAFF_TEMPLATES}


# --------------------------------------------------------------------------- room to role

# Specific words in the stored room name win over the department (a "Procurement" room under the operations
# department is procurement). Whole words only, lower case, English names as the builder stores them.
_NAME_FIRST: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(p), t) for p, t in (
    (r"\b(hr|human resources|people|career|employment|recruit\w*|interview\w*|payroll)\b", "hr_people"),
    (r"\b(procurement|purchasing|suppliers?|vendors?)\b", "procurement"),
    (r"\b(marketing|brand|content|social|campaigns?)\b", "marketing_content"),
    (r"\b(security|guards?)\b", "security_reviewer"),
    (r"\b(legal|compliance|governance|audit\w*)\b", "compliance_reviewer"),
    (r"\b(reception|front desk|lobby|visitors?)\b", "receptionist"),
    (r"\b(executive|personal|chief|ceo)\b", "executive_assistant"),
    (r"\b(fleet|tyres?|tires?|inspections?)\b", "fleet_maintenance"),
    (r"\b(orders?|marketplace)\b", "orders_desk"),
))
_BY_DEPARTMENT: dict[str, str] = {
    "tyre_engineering": "fleet_maintenance", "finance": "finance_reconciliation", "support": "support_triage",
    "sales": "sales_followup", "operations": "operations_dispatcher", "compliance": "compliance_reviewer",
}
_NAME_THEN: tuple[tuple[re.Pattern[str], str], ...] = tuple((re.compile(p), t) for p, t in (
    (r"\b(support|tickets?|customer service|messages?|helpdesk)\b", "support_triage"),
    (r"\b(sales|leads?|e-commerce|retail)\b", "sales_followup"),
    (r"\b(finance|wealth|accounts?|treasury)\b", "finance_reconciliation"),
    (r"\b(command|facilities|parking|logistics|warehouse|assets?|operations?|dispatch\w*)\b", "operations_dispatcher"),
    (r"\b(research|intelligence|analytics|data|insights?|r&d|investments?|ventures?|future|briefing)\b", "research_analyst"),
    (r"\b(dev\w*|engineering|deploy\w*|incidents?|automation|ai|labs?|platform|infrastructure)\b", "engineering_devops"),
))


def template_for_room(room: Mapping[str, Any]) -> tuple[str | None, str]:
    """(template id or None, source). Source is ``kind``, ``name``, ``department`` or ``none``."""
    name = str(room.get("name") or "").strip().lower()
    department = op.norm_department(room.get("team_department"))
    if room.get("kind") == "reception":
        return "receptionist", "kind"
    for pattern, template in _NAME_FIRST:
        if pattern.search(name):
            return template, "name"
    if department in _BY_DEPARTMENT:
        return _BY_DEPARTMENT[department], "department"
    for pattern, template in _NAME_THEN:
        if pattern.search(name):
            return template, "name"
    return None, "none"


def desk_marker(template_id: str, desk_id: str) -> str:
    """The agent's ``template_id`` for one desk. A retry finds the agent an interrupted run already built by this
    marker (same role, same desk), so a desk never gets two agents. At most 40 characters."""
    return f"{op.TEMPLATE_PREFIX}{template_id}:{hashlib.sha256(desk_id.encode()).hexdigest()[:12]}"[:40]


def agent_name(template: Template, room_en: str, room_ar: str | None, number: int, lang: str) -> str:
    """'Orders Desk, Orders 2'. A role plus the room plus a number; never an invented person's name."""
    if lang == "ar":
        base, room, sep = template.name_ar, room_ar or room_en, "، "
    else:
        base, room, sep = template.name_en, room_en, ", "
    tail = f"{sep}{room} {number}"
    return (base[: max(10, 80 - len(tail))] + tail)[:80]
