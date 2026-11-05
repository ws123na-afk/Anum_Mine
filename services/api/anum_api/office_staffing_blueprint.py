"""Office staffing blueprint for the ANUM MINE headquarters (docs/ANUM_MINE_OFFICE_STAFFING.md).

Pure data and pure functions. Nothing here reads a database or creates anything.

* ``SOURCES`` are the data sources an office's agents read: connectors (a stored connection row decides
  Connected or Not connected), built in modules (always available) and one server setting (web search).
* ``RULES`` is the reviewed library of things an agent never does, in English and Arabic.
* ``ROLES`` are the recommended roles. Each one points at an existing governed role template
  (``office_provisioning.TEMPLATES`` or ``office_staff_templates.EXTRA_TEMPLATES``), so its tools and
  skills are registry ids that already exist; the blueprint adds the office specific purpose, the rules
  (highest tool risk, approvals, cost cap, never list) and the data sources it reads.
* ``UNITS`` group roles by department kind; ``OFFICE_KINDS`` group units per kind of office. A building is
  classified by its stable id (the starter campus and Green Concrete) or, for any other building, by the
  departments stored on its rooms.
* ``match_agents`` and ``agent_gaps`` compare the office's real agents with the blueprint; ``action_hash``
  binds one apply action to the tenant, workspace, confirming user, office and exact content.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from . import green_concrete_kit as gk
from . import office_provisioning as op
from . import office_staff_templates as st
from .office_provisioning import Template

BLUEPRINT_VERSION: Final = 1
MARKER_PREFIX: Final = "bp:"
RULES_MARKER: Final = f"Office rules (blueprint v{BLUEPRINT_VERSION})"
RISK_ORDER: Final = {"low": 0, "medium": 1, "high": 2, "blocked": 3}
AUTONOMY_ORDER: Final = {"off": 0, "ask": 1, "auto": 2}


# --------------------------------------------------------------------------- data sources


@dataclass(frozen=True)
class Source:
    id: str
    kind: str              # connector | module | setting
    en: str
    ar: str
    permission: str        # the permission needed to see its state
    view: str | None       # the shell view that connects or opens it (None: set by the server operator)
    tables: tuple[str, ...] = ()   # connector: a visible row in any of these means Connected
    setting: str | None = None     # setting: a non empty value means Connected


SOURCES: tuple[Source, ...] = (
    Source("commerce", "connector", "Store and marketplace orders", "طلبات المتجر والسوق", "commerce:read", "commerce",
           ("commerce_connectors",)),
    Source("tyre_pulse", "connector", "Tyre Pulse fleet sync", "مزامنة أسطول تاير بلس", "tyre:read", "tyre", ("tyre_connections",)),
    Source("email_center", "connector", "Email mailboxes", "صناديق البريد", "email:read", "email",
           ("mail_accounts", "email_accounts")),
    Source("whatsapp", "connector", "WhatsApp Business", "واتساب للأعمال", "whatsapp:read", "whatsapp", ("whatsapp_accounts",)),
    Source("calendar", "connector", "Calendar", "التقويم", "calendar:read", "calendar", ("calendar_connections",)),
    Source("finance_ledger", "connector", "Finance ledger", "السجل المالي", "finance:read", "finance",
           ("finance_accounts", "finance_transactions")),
    Source("brevo", "connector", "Brevo email campaigns", "حملات بريد Brevo", "marketing:read", "marketing", ("brevo_connections",)),
    Source("social_accounts", "connector", "Social media accounts", "حسابات التواصل الاجتماعي", "marketing:read", "marketing",
           ("marketing_accounts",)),
    Source("phone_calls", "connector", "Phone calls provider", "مزود المكالمات الهاتفية", "call:read", "calls",
           ("call_provider_accounts",)),
    Source("developer_projects", "connector", "Code projects (Developer mode)", "مشاريع البرمجة (وضع المطور)", "task:read",
           "developer", ("developer_projects",)),
    Source("web_search", "setting", "Web search service", "خدمة البحث في الويب", "task:read", None, setting="searxng_url"),
    Source("sales_crm", "module", "Sales leads", "العملاء المحتملون", "whatsapp:read", "business"),
    Source("files", "module", "Documents library", "مكتبة المستندات", "file:read", "files"),
    Source("knowledge", "module", "Knowledge base", "قاعدة المعرفة", "memory:read", "knowledge"),
    Source("tasks_approvals", "module", "Tasks and approvals", "المهام والموافقات", "task:read", "tasks"),
    Source("visitor_register", "module", "Visitor register", "سجل الزوار", "task:read", "office"),
    Source(gk.SEATS_SOURCE, "module", "Green Concrete seats and working hours", "مقاعد جرين كونكريت وساعات العمل",
           "task:read", "office"),
    Source("audit_log", "module", "Audit log", "سجل التدقيق", "audit:read", "audit"),
)
SOURCE_BY_ID: Final = {s.id: s for s in SOURCES}


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class Rule:
    id: str
    en: str
    ar: str


RULES: tuple[Rule, ...] = (
    Rule("no_outward_without_approval", "Send, publish or pay anything without a person's approval",
         "يرسل أو ينشر أو يدفع أي شيء دون موافقة شخص"),
    Rule("no_self_approval", "Approve its own action or anyone else's approval", "يوافق على إجراء خاص به أو على موافقة غيره"),
    Rule("no_invented_figures", "State a figure, date or fact it did not read from a tool or a record",
         "يذكر رقما أو تاريخا أو معلومة لم يقرأها من أداة أو سجل"),
    Rule("no_data_outside_workspace", "Copy personal data outside this workspace", "ينسخ بيانات شخصية خارج مساحة العمل هذه"),
    Rule("no_money_movement", "Move money, pay a bill or change a financial record", "ينقل أموالا أو يدفع فاتورة أو يغيّر سجلا ماليا"),
    Rule("no_live_trading", "Place a live trade; live trading stays off", "ينفذ صفقة حقيقية؛ التداول الحقيقي يبقى متوقفا"),
    Rule("no_legal_advice", "Give legal advice or say a clause is valid", "يقدم استشارة قانونية أو يقول إن بندا صحيح"),
    Rule("no_people_decisions", "Decide on hiring, pay, leave or discipline", "يقرر في التوظيف أو الأجور أو الإجازات أو التأديب"),
    Rule("no_order_changes", "Change an order, a price or stock in the store", "يغيّر طلبا أو سعرا أو مخزونا في المتجر"),
    Rule("no_customer_promises", "Promise a customer a refund, a discount or a date", "يعد عميلا باسترداد أو خصم أو موعد"),
    Rule("no_publish", "Publish or schedule a post or a campaign", "ينشر أو يجدول منشورا أو حملة"),
    Rule("no_code_changes", "Change code, settings or a deployment", "يغيّر الشيفرة أو الإعدادات أو النشر"),
    Rule("no_access_changes", "Change who can sign in or what they can see", "يغيّر من يمكنه الدخول أو ما يمكنه رؤيته"),
    Rule("no_owner_commitments", "Accept a meeting or make a commitment in the owner's name", "يقبل اجتماعا أو يلتزم بشيء باسم المالك"),
    Rule("no_vehicle_record_changes", "Close a work order or change a vehicle record", "يغلق أمر عمل أو يغيّر سجل مركبة"),
)
RULE_BY_ID: Final = {r.id: r for r in RULES}
BASE_RULES: Final = ("no_outward_without_approval", "no_self_approval", "no_invented_figures", "no_data_outside_workspace")


# --------------------------------------------------------------------------- roles


@dataclass(frozen=True)
class Role:
    key: str                      # unique in the blueprint; at most 22 characters (it is part of the agent marker)
    template_id: str              # an existing governed role template
    unit: str
    purpose_en: str
    purpose_ar: str
    max_risk: str                 # the highest tool risk its rules allow (never high)
    never: tuple[str, ...]        # rule ids beyond BASE_RULES
    sources: tuple[str, ...]      # data source ids
    match_departments: tuple[str, ...] = ()   # stored agent departments that count as this role (normalised)
    room_departments: tuple[str, ...] = ()    # rooms where it prefers to sit

    @property
    def template(self) -> Template:
        """The role's template with the skills and tools its kit adds (only adds; ``green_concrete_kit.ROLE_KIT``)."""
        base = st.STAFF_TEMPLATE_BY_ID[self.template_id]
        skills, tools = gk.kit_for(self.key)
        if not skills and not tools:
            return base
        return replace(base, tools=tuple(dict.fromkeys((*base.tools, *tools))),
                       skills=tuple(dict.fromkeys((*base.skills, *skills))))

    @property
    def knowledge_collection(self) -> bool:
        """Whether its agents read the shared Green Concrete knowledge collection."""
        return self.unit == "green_concrete"

    @property
    def rules(self) -> tuple[str, ...]:
        return (*BASE_RULES, *(r for r in self.never if r not in BASE_RULES))


ROLES: tuple[Role, ...] = (
    # Executive and command centre
    Role("executive_pa", "executive_assistant", "executive",
         "Keeps the owner's day in order: reads mail and messages, drafts replies and hands work to the right person.",
         "ينظم يوم المالك: يقرأ البريد والرسائل ويكتب الردود ويحيل العمل إلى الشخص المناسب.",
         "medium", ("no_owner_commitments",), ("email_center", "whatsapp", "calendar", "tasks_approvals"),
         ("executive",), ("executive",)),
    Role("command_compliance", "compliance_reviewer", "executive",
         "Watches held approvals and money rules from the command centre and writes review notes. It never decides.",
         "يتابع الموافقات المعلقة وقواعد المال من مركز القيادة ويكتب ملاحظات المراجعة. لا يقرر أبدا.",
         "low", ("no_money_movement",), ("tasks_approvals", "finance_ledger", "audit_log"), ("compliance",), ("compliance",)),
    # Reception
    Role("receptionist", "receptionist", "reception",
         "Greets visitors and callers: reads the call log and WhatsApp, drafts answers and passes requests on.",
         "يستقبل الزوار والمتصلين: يقرأ سجل المكالمات وواتساب ويكتب الردود ويحيل الطلبات.",
         "medium", ("no_owner_commitments",), ("visitor_register", "phone_calls", "whatsapp", "calendar"), ("reception",)),
    # Operations
    Role("dispatcher", "operations_dispatcher", "operations",
         "Turns stuck or unowned work into tasks for the right person.",
         "يحوّل الأعمال العالقة أو بلا مالك إلى مهام للشخص المناسب.",
         "medium", (), ("tasks_approvals", "phone_calls", "email_center"), ("operations",), ("operations",)),
    # Security
    Role("security_reviewer", "security_reviewer", "security",
         "Reviews sign in and money rule signals and writes findings for a person. It changes no access.",
         "يراجع إشارات الدخول وقواعد المال ويكتب النتائج لشخص. لا يغيّر أي صلاحية.",
         "low", ("no_access_changes",), ("audit_log", "email_center"), ("security",), ("security",)),
    # Personal office
    Role("personal_assistant", "executive_assistant", "personal",
         "Handles the owner's personal mail and messages: summaries, drafts and reminders. Personal data stays here.",
         "يتولى بريد المالك ورسائله الشخصية: ملخصات ومسودات وتذكيرات. تبقى البيانات الشخصية هنا.",
         "medium", ("no_owner_commitments",), ("email_center", "whatsapp", "calendar", "files"), ("personal",)),
    Role("personal_finance", "finance_reconciliation", "personal",
         "Reads personal accounts and bills and lists what needs a look. Live trading stays off.",
         "يقرأ الحسابات والفواتير الشخصية ويذكر ما يحتاج مراجعة. التداول الحقيقي يبقى متوقفا.",
         "low", ("no_money_movement", "no_live_trading"), ("finance_ledger",), ("personal_finance",)),
    # Career and employment
    Role("career_hr", "hr_people", "career",
         "Follows job applications and interviews by mail and drafts replies. It decides nothing about people.",
         "يتابع طلبات التوظيف والمقابلات عبر البريد ويكتب الردود. لا يقرر شيئا بشأن الأشخاص.",
         "medium", ("no_people_decisions",), ("email_center", "calendar", "files"), ("hr", "career")),
    Role("career_research", "research_analyst", "career",
         "Researches employers, roles and the job market and writes short briefs.",
         "يبحث عن أصحاب العمل والوظائف وسوق العمل ويكتب ملخصات قصيرة.",
         "low", (), ("web_search", "knowledge", "email_center"), ("career_research",)),
    # Green Concrete (the people of the owner's company; matched by the role stored as their department)
    Role("gc_pmv_manager", "operations_dispatcher", "green_concrete",
         "PMV manager: hands out the team's work and follows what is stuck.",
         "مدير PMV: يوزع عمل الفريق ويتابع ما هو عالق.",
         "medium", ("no_vehicle_record_changes",),
         ("tasks_approvals", "tyre_pulse", "email_center", gk.SEATS_SOURCE, "calendar", "knowledge"), ("pmv_manager",)),
    Role("gc_business_analyst", "research_analyst", "green_concrete",
         "Business analyst: turns fleet and cost data into short findings for the manager.",
         "محلل أعمال: يحوّل بيانات الأسطول والتكاليف إلى نتائج قصيرة للمدير.",
         "low", (), ("tyre_pulse", "files", "knowledge"), ("business_analyst",)),
    Role("gc_tyre_planner", "fleet_maintenance", "green_concrete",
         "Tyre planning engineer: plans rotations, replacements and purchases from Tyre Pulse data.",
         "مهندس تخطيط الإطارات: يخطط للتدوير والاستبدال والشراء من بيانات تاير بلس.",
         "low", ("no_vehicle_record_changes",), ("tyre_pulse", "knowledge"), ("tyre_planning_engineer",)),
    Role("gc_data_monitoring", "fleet_maintenance", "green_concrete",
         "Data monitoring: watches tyre alerts, inspections and due dates and reports what changed.",
         "مراقبة البيانات: يتابع تنبيهات الإطارات والفحوصات والمواعيد ويبلّغ عما تغيّر.",
         "low", ("no_vehicle_record_changes",), ("tyre_pulse", gk.SEATS_SOURCE, "tasks_approvals", "knowledge"),
         ("data_monitoring",)),
    Role("gc_data_collector", "fleet_maintenance", "green_concrete",
         "Data collector: checks that readings and inspections arrived in Tyre Pulse and lists what is missing.",
         "جامع بيانات: يتحقق من وصول القراءات والفحوصات إلى تاير بلس ويذكر ما ينقص.",
         "low", ("no_vehicle_record_changes",), ("tyre_pulse", gk.SEATS_SOURCE, "tasks_approvals", "knowledge"),
         ("data_collector",)),
    # Tyre Pulse
    Role("fleet_maintenance", "fleet_maintenance", "tyre_pulse",
         "Plans tyre rotations, inspections and purchases from the fleet data.",
         "يخطط لتدوير الإطارات والفحوصات والمشتريات من بيانات الأسطول.",
         "low", ("no_vehicle_record_changes",), ("tyre_pulse",), ("tyre_engineering",), ("tyre_engineering",)),
    Role("fleet_logistics", "operations_dispatcher", "tyre_pulse",
         "Turns due work in the workshop and warehouse into tasks for the right person.",
         "يحوّل الأعمال المستحقة في الورشة والمستودع إلى مهام للشخص المناسب.",
         "medium", ("no_vehicle_record_changes",), ("tyre_pulse", "tasks_approvals", "phone_calls"), ("logistics",), ("operations",)),
    # Commerce
    Role("orders_desk", "orders_desk", "commerce",
         "Owns incoming orders: finds what is open or late and drafts customer follow-ups.",
         "يتولى الطلبات الواردة: يجد المفتوح والمتأخر ويكتب متابعات العملاء.",
         "medium", ("no_order_changes", "no_customer_promises"), ("commerce", "email_center", "whatsapp"), (), ("operations",)),
    Role("sales_followup", "sales_followup", "commerce",
         "Follows up sales leads and drafts the next message for a person to send.",
         "يتابع العملاء المحتملين ويكتب الرسالة التالية ليرسلها شخص.",
         "medium", ("no_customer_promises",), ("sales_crm", "whatsapp", "email_center"), ("sales",), ("sales",)),
    Role("support_triage", "support_triage", "commerce",
         "Sorts customer messages by topic and urgency and drafts first replies.",
         "يفرز رسائل العملاء حسب الموضوع والأهمية ويكتب الردود الأولى.",
         "medium", ("no_customer_promises",), ("email_center", "whatsapp"), ("support",), ("support",)),
    # Marketing
    Role("marketing_specialist", "marketing_specialist", "marketing",
         "Reads campaign results and drafts campaigns and posts. A person publishes.",
         "يقرأ نتائج الحملات ويكتب مسودات الحملات والمنشورات. شخص ينشرها.",
         "low", ("no_publish",), ("brevo", "social_accounts"), ("marketing",), ("marketing",)),
    Role("marketing_content", "marketing_content", "marketing",
         "Drafts social posts and email copy in the brand voice for review.",
         "يكتب مسودات المنشورات ونصوص البريد بأسلوب العلامة للمراجعة.",
         "low", ("no_publish",), ("social_accounts", "brevo", "files"), ("marketing_content",), ("marketing",)),
    # Finance
    Role("finance_clerk", "finance_reconciliation", "finance",
         "Reconciles transactions and bills and lists what does not match. Live trading stays off.",
         "يطابق المعاملات والفواتير ويذكر ما لا يتطابق. التداول الحقيقي يبقى متوقفا.",
         "low", ("no_money_movement", "no_live_trading"), ("finance_ledger",), ("finance",), ("finance",)),
    # HR
    Role("hr_people", "hr_people", "hr",
         "Reads people related mail, drafts replies and hands follow-ups to the right person.",
         "يقرأ البريد المتعلق بالموظفين ويكتب الردود ويحيل المتابعات إلى الشخص المناسب.",
         "medium", ("no_people_decisions",), ("email_center", "files"), ("hr",), ("hr",)),
    # Procurement
    Role("procurement", "procurement", "procurement",
         "Watches low stock and upcoming bills and drafts supplier requests for review.",
         "يتابع المخزون المنخفض والفواتير القادمة ويكتب طلبات الموردين للمراجعة.",
         "medium", ("no_money_movement", "no_order_changes"), ("commerce", "finance_ledger", "email_center"), ("procurement",),
         ("procurement",)),
    # Legal and compliance
    Role("legal_reviewer", "legal_reviewer", "legal_compliance",
         "Reads contract mail and writes review notes with risks as questions for a qualified person.",
         "يقرأ بريد العقود ويكتب ملاحظات مراجعة مع المخاطر كأسئلة لشخص مختص.",
         "medium", ("no_legal_advice",), ("email_center", "files", "knowledge"), ("legal",), ("legal",)),
    Role("compliance_reviewer", "compliance_reviewer", "legal_compliance",
         "Prepares review notes on held approvals and money rules. It never decides an approval.",
         "يعد ملاحظات مراجعة عن الموافقات المعلقة وقواعد المال. لا يقرر أي موافقة.",
         "low", ("no_money_movement",), ("tasks_approvals", "audit_log", "finance_ledger"), ("compliance",), ("compliance",)),
    # Knowledge and research
    Role("research_analyst", "research_analyst", "research",
         "Researches questions from the web, mail and the knowledge base and writes sourced briefs.",
         "يبحث في الأسئلة من الويب والبريد وقاعدة المعرفة ويكتب ملخصات بمصادرها.",
         "low", (), ("web_search", "knowledge", "files"), ("research",), ("research",)),
    # Technology
    Role("technology_coordinator", "technology_coordinator", "technology",
         "Collects technical requests and writes briefs a person can paste into Developer mode.",
         "يجمع الطلبات التقنية ويكتب ملخصات يمكن لشخص لصقها في وضع المطور.",
         "medium", ("no_code_changes",), ("developer_projects", "email_center"), ("technology",), ("technology",)),
    Role("engineering_devops", "engineering_devops", "technology",
         "Reads incident and deployment mail and pages and hands fixes to the right person.",
         "يقرأ بريد الحوادث والنشر وصفحاته ويحيل الإصلاحات إلى الشخص المناسب.",
         "medium", ("no_code_changes",), ("developer_projects", "email_center"), ("engineering",), ("technology",)),
    # New ventures
    Role("venture_scout", "research_analyst", "new_ventures",
         "Researches new business ideas and markets and writes short opportunity briefs.",
         "يبحث في أفكار الأعمال والأسواق الجديدة ويكتب ملخصات قصيرة للفرص.",
         "low", (), ("web_search", "knowledge", "files"), ("ventures",), ("research",)),
    Role("venture_finance", "finance_reconciliation", "new_ventures",
         "Reads investment accounts and bills and lists what needs a look. Live trading stays off.",
         "يقرأ حسابات الاستثمار والفواتير ويذكر ما يحتاج مراجعة. التداول الحقيقي يبقى متوقفا.",
         "low", ("no_money_movement", "no_live_trading"), ("finance_ledger",), ("investments",), ("finance",)),
)
ROLE_BY_KEY: Final = {r.key: r for r in ROLES}


@dataclass(frozen=True)
class Unit:
    id: str
    en: str
    ar: str
    roles: tuple[str, ...]


UNITS: tuple[Unit, ...] = (
    Unit("executive", "Executive and command centre", "التنفيذي ومركز القيادة", ("executive_pa", "command_compliance")),
    Unit("reception", "Reception", "الاستقبال", ("receptionist",)),
    Unit("operations", "Operations", "العمليات", ("dispatcher",)),
    Unit("security", "Security", "الأمن", ("security_reviewer",)),
    Unit("personal", "Personal office", "المكتب الشخصي", ("personal_assistant", "personal_finance")),
    Unit("career", "Career and employment", "المسار المهني والتوظيف", ("career_hr", "career_research")),
    Unit("green_concrete", "Green Concrete operations", "عمليات جرين كونكريت",
         ("gc_pmv_manager", "gc_business_analyst", "gc_tyre_planner", "gc_data_monitoring", "gc_data_collector")),
    Unit("tyre_pulse", "Tyre Pulse fleet", "أسطول تاير بلس", ("fleet_maintenance", "fleet_logistics")),
    Unit("commerce", "Commerce: orders, sales and support", "التجارة: الطلبات والمبيعات والدعم",
         ("orders_desk", "sales_followup", "support_triage")),
    Unit("marketing", "Marketing", "التسويق", ("marketing_specialist", "marketing_content")),
    Unit("finance", "Finance", "المالية", ("finance_clerk",)),
    Unit("hr", "Human resources", "الموارد البشرية", ("hr_people",)),
    Unit("procurement", "Procurement", "المشتريات", ("procurement",)),
    Unit("legal_compliance", "Legal and compliance", "القانونية والامتثال", ("legal_reviewer", "compliance_reviewer")),
    Unit("research", "Knowledge and research", "المعرفة والبحث", ("research_analyst",)),
    Unit("technology", "Technology", "التقنية", ("technology_coordinator", "engineering_devops")),
    Unit("new_ventures", "New ventures", "المشاريع الجديدة", ("venture_scout", "venture_finance")),
)
UNIT_BY_ID: Final = {u.id: u for u in UNITS}


@dataclass(frozen=True)
class OfficeKind:
    id: str
    en: str
    ar: str
    units: tuple[str, ...]


OFFICE_KINDS: tuple[OfficeKind, ...] = (
    OfficeKind("executive_hq", "Executive headquarters and command centre", "المقر التنفيذي ومركز القيادة",
               ("executive", "reception", "operations", "security")),
    OfficeKind("commerce", "Commerce", "التجارة", ("commerce", "marketing")),
    OfficeKind("tyre_pulse", "Tyre Pulse", "تاير بلس", ("tyre_pulse",)),
    OfficeKind("corporate_services", "Corporate services", "الخدمات المؤسسية", ("finance", "hr", "procurement", "legal_compliance")),
    OfficeKind("knowledge_ai", "Knowledge and AI", "المعرفة والذكاء الاصطناعي", ("research", "technology")),
    OfficeKind("new_ventures", "New ventures", "المشاريع الجديدة", ("new_ventures",)),
    OfficeKind("personal", "Personal office", "المكتب الشخصي", ("personal",)),
    OfficeKind("career", "Career and employment", "المسار المهني والتوظيف", ("career",)),
    OfficeKind("green_concrete", "Green Concrete operations", "عمليات جرين كونكريت", ("green_concrete",)),
    OfficeKind("custom", "Office built from its rooms", "مكتب مبني من غرفه", ()),
    OfficeKind("general", "General office", "مكتب عام", ("operations",)),
)
OFFICE_KIND_BY_ID: Final = {k.id: k for k in OFFICE_KINDS}

# Stable ids of the starter campus (office_starter_campus.CAMPUS) and Green Concrete (office_green_concrete.BUILDING_ID).
BUILDING_KINDS: Final = {
    "campus-01-hq": "executive_hq", "campus-02-commerce": "commerce", "campus-03-tyre-pulse": "tyre_pulse",
    "campus-04-corporate-services": "corporate_services", "campus-05-knowledge-ai": "knowledge_ai",
    "campus-06-new-ventures": "new_ventures", "campus-07-personal": "personal", "campus-08-career": "career",
    "gc-green-concrete": "green_concrete",
}
# A room's stored team department (or kind) to the unit it asks for, for any other building.
DEPARTMENT_UNITS: Final = {
    "sales": "commerce", "support": "commerce", "operations": "operations", "finance": "finance",
    "tyre_engineering": "tyre_pulse", "marketing": "marketing", "technology": "technology", "research": "research",
    "legal": "legal_compliance", "compliance": "legal_compliance", "security": "security", "hr": "hr",
    "procurement": "procurement", "executive": "executive",
}
KIND_UNITS: Final = {"reception": "reception", "command": "operations"}


def classify(building_id: str, rooms: Iterable[Mapping[str, Any]]) -> tuple[str, tuple[str, ...]]:
    """(office kind id, unit ids) for one building, from its stable id or its stored rooms."""
    kind = BUILDING_KINDS.get(building_id)
    if kind is not None:
        return kind, OFFICE_KIND_BY_ID[kind].units
    units: list[str] = []
    for room in sorted(rooms, key=lambda r: str(r.get("id"))):
        unit = DEPARTMENT_UNITS.get(op.norm_department(room.get("team_department"))) or KIND_UNITS.get(str(room.get("kind")))
        if unit and unit not in units:
            units.append(unit)
    if not units:
        return "general", OFFICE_KIND_BY_ID["general"].units
    order = [u.id for u in UNITS]
    return "custom", tuple(sorted(units, key=order.index))


def roles_for(units: Sequence[str]) -> list[Role]:
    seen: list[Role] = []
    for unit in units:
        for key in UNIT_BY_ID[unit].roles:
            role = ROLE_BY_KEY[key]
            if role not in seen:
                seen.append(role)
    return seen


# --------------------------------------------------------------------------- per role rules


def autonomy(role: Role) -> dict[str, str]:
    return {"read": "auto", "write": role.template.write_autonomy, "external": "off"}


def rules_text(role: Role) -> str:
    never = "; ".join(RULE_BY_ID[r].en.lower() for r in role.rules)
    return f"{RULES_MARKER}: you never {never}."


def instructions(role: Role) -> str:
    return f"{role.template.instructions}\n\n{rules_text(role)}"[:8000]


def marker(building_id: str, role_key: str) -> str:
    """The created agent's ``template_id`` and the ledger key: one agent per (office, role), at most 40 characters."""
    return f"{MARKER_PREFIX}{hashlib.sha256(building_id.encode()).hexdigest()[:12]}:{role_key}"[:40]


def role_view(role: Role, tool_info: Mapping[str, Mapping[str, Any]], skill_info: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The role resolved against this deployment and caller (``office_provisioning.resolve_capabilities``)."""
    t = role.template
    resolved = op.resolve_capabilities(t, tool_info=tool_info, skill_info=skill_info)
    tools = []
    for item in resolved.tools:
        over = item["risk"] is not None and RISK_ORDER.get(item["risk"], 3) > RISK_ORDER[role.max_risk]
        tools.append({**item, "state": "over_risk" if over else item["state"]})
    attach_tools = [x["name"] for x in tools if x["state"] == "present"]
    attach_skills = [s for s in resolved.attach_skills
                     if set((skill_info.get(s) or {}).get("required_tools") or ()) <= set(attach_tools) | {"anum.respond"}
                     or (skill_info.get(s) or {}).get("source") != "builtin"]
    return {
        "key": role.key, "template_id": t.id, "unit": role.unit,
        "name": {"en": t.name_en, "ar": t.name_ar}, "purpose": {"en": role.purpose_en, "ar": role.purpose_ar},
        "department": t.department,
        "rules": {
            "autonomy": autonomy(role), "max_risk": role.max_risk,
            "approvals": _approvals(role),
            "monthly_cost_limit_usd": t.monthly_cost_limit_usd,
            "never": [{"id": r, "en": RULE_BY_ID[r].en, "ar": RULE_BY_ID[r].ar} for r in role.rules],
        },
        "tools": tools, "skills": resolved.skills,
        "attach_tools": sorted(attach_tools), "attach_skills": sorted(attach_skills),
        "missing": [{"kind": m["kind"], "id": m["id"], "en": m["en"], "ar": m["ar"]} for m in resolved.missing],
        "limited": bool(resolved.missing),
        "sources": list(role.sources),
    }


def _approvals(role: Role) -> list[dict[str, str]]:
    write = role.template.write_autonomy
    out = [{"id": "read_auto", "en": "Reading runs on its own", "ar": "القراءة تعمل تلقائيا"}]
    if write == "ask":
        out.append({"id": "write_ask", "en": "Every draft or change waits for a person's approval",
                    "ar": "كل مسودة أو تغيير ينتظر موافقة شخص"})
    else:
        out.append({"id": "write_off", "en": "Drafts and changes are off", "ar": "المسودات والتغييرات متوقفة"})
    out.append({"id": "external_off", "en": "Sending, publishing and paying are off", "ar": "الإرسال والنشر والدفع متوقفة"})
    return out


# --------------------------------------------------------------------------- matching real agents


@dataclass(frozen=True)
class OfficeAgent:
    id: str
    name: str
    status: str
    department: str | None
    template_id: str | None
    allowed_tools: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    autonomy: Mapping[str, str]
    has_rules: bool
    cap_usd: float | None
    can_edit: bool
    version: int
    desk_id: str | None
    knowledge_mode: str = "none"
    knowledge_sources: tuple[tuple[str, str], ...] = ()

    def reads_collection(self, collection_id: str) -> bool:
        return self.knowledge_mode == "workspace" or ("collection", collection_id) in self.knowledge_sources


def match_agents(building_id: str, roles: Sequence[Role], agents: Sequence[OfficeAgent]) -> tuple[dict[str, list[OfficeAgent]], list[OfficeAgent]]:
    """Each agent of the office counts for at most one role: its blueprint marker first, then the template
    it was built from, then its stored department. Agents that match no role are returned apart."""
    by_role: dict[str, list[OfficeAgent]] = {r.key: [] for r in roles}
    left = sorted(agents, key=lambda a: a.id)

    def take(test) -> None:  # type: ignore[no-untyped-def]
        nonlocal left
        rest = []
        for a in left:
            role = next((r for r in roles if test(r, a)), None)
            (by_role[role.key].append(a) if role else rest.append(a))
        left = rest

    take(lambda r, a: a.template_id == marker(building_id, r.key))
    take(lambda r, a: bool(a.template_id) and (a.template_id == f"{op.TEMPLATE_PREFIX}{r.template_id}"
                                                  or a.template_id.startswith(f"{op.TEMPLATE_PREFIX}{r.template_id}:")))
    take(lambda r, a: op.norm_department(a.department) in (r.match_departments or (r.template.department,)))
    return by_role, left


def agent_gaps(role: Role, view: Mapping[str, Any], agent: OfficeAgent, tool_risk: Mapping[str, str | None],
               collection_id: str | None = None) -> dict[str, Any]:
    """What this agent lacks against its role: skills, tools, rules, its knowledge collection; and tools above the role's risk."""
    tools = [t for t in view["attach_tools"] if t not in agent.allowed_tools]
    skills = [s for s in view["attach_skills"] if s not in agent.allowed_skills]
    want = autonomy(role)
    rules: list[str] = []
    if any(AUTONOMY_ORDER.get(str(agent.autonomy.get(c, "ask")), 2) > AUTONOMY_ORDER[want[c]] for c in want):
        rules.append("autonomy")
    cap = role.template.monthly_cost_limit_usd
    if agent.cap_usd is None or agent.cap_usd > cap:
        rules.append("cost_cap")
    if not agent.has_rules:
        rules.append("never_list")
    over = sorted(t for t in agent.allowed_tools
                  if RISK_ORDER.get(tool_risk.get(t) or "high", 3) > RISK_ORDER[role.max_risk])
    knowledge = bool(collection_id and role.knowledge_collection and not agent.reads_collection(collection_id))
    return {"missing_tools": tools, "missing_skills": skills, "missing_rules": rules, "over_risk_tools": over,
            "missing_knowledge": knowledge, "complete": not (tools or skills or rules or knowledge)}


def with_collection(mode: str, sources: Sequence[Mapping[str, str]], collection_id: str) -> tuple[str, list[dict[str, str]]]:
    """The agent's knowledge with the collection added; never removes a source and never narrows ``workspace``."""
    if mode == "workspace":
        return mode, [dict(s) for s in sources]
    kept = [dict(s) for s in sources] if mode == "selected" else []
    if not any(s.get("type") == "collection" and s.get("id") == collection_id for s in kept):
        kept.append({"type": "collection", "id": collection_id})
    return "selected", kept[:50]


def tightened(current: Mapping[str, str], role: Role) -> dict[str, str]:
    """The stricter of the agent's own autonomy and the role's, per category. Never loosens."""
    want = autonomy(role)
    out = {}
    for c in ("read", "write", "external"):
        mine = str(current.get(c, "ask"))
        out[c] = mine if AUTONOMY_ORDER.get(mine, 2) <= AUTONOMY_ORDER[want[c]] else want[c]
    return out


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def action_hash(tenant_id: str, workspace_id: str, user_id: str, building_id: str, content: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical({"v": BLUEPRINT_VERSION, "t": tenant_id, "w": workspace_id, "u": user_id,
                                     "b": building_id, "c": content}).encode()).hexdigest()


def blueprint_document() -> dict[str, Any]:
    """The reviewed data as one document (sources, rules, roles, units, office kinds), without live state."""
    return {
        "version": BLUEPRINT_VERSION,
        "sources": [{"id": s.id, "kind": s.kind, "name": {"en": s.en, "ar": s.ar}, "view": s.view} for s in SOURCES],
        "rules": [{"id": r.id, "en": r.en, "ar": r.ar, "base": r.id in BASE_RULES} for r in RULES],
        "units": [{"id": u.id, "name": {"en": u.en, "ar": u.ar}, "roles": list(u.roles)} for u in UNITS],
        "office_kinds": [{"id": k.id, "name": {"en": k.en, "ar": k.ar}, "units": list(k.units)} for k in OFFICE_KINDS],
    }
