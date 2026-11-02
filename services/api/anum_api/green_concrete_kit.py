"""Green Concrete agents kit: role skills, their read tools, data gaps and routines (docs/ANUM_MINE_GREEN_CONCRETE_AGENTS.md).

Pure, reviewed data and pure functions. Nothing here reads a database or creates anything.

* ``SKILLS`` are first-party role skills for the people of Green Concrete (``office_green_concrete.PEOPLE``): each
  one names the single read tool it uses, the data it needs, its inputs, its output, its guardrails and English
  and Arabic names. They are built-in runtime skills (``agent_skills.default_skill_registry``) that load their
  instructions into every run and chat of an agent that allows them and are selected only for such an agent.
* ``TOOLS`` are the read tools those skills use (``green_concrete_ops``): low risk, read only, registered in the
  normal tool registry, so ToolPolicy, governance, autonomy, budgets, the emergency stop and the ``tool.call``
  audit apply as to any tool. None of them writes, sends or reaches outside ANUM.
* ``ROLE_KIT`` says which skills and tools each blueprint role of the Green Concrete unit gains
  (``office_staffing_blueprint``). Apply only adds them, and never one above the role's risk ceiling.
* ``GAPS`` are data the owner asked about that ANUM has no source for. No tool is invented for them.
* ``ROUTINES`` are the recurring jobs the owner can add, created turned off.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

KIT_VERSION: Final = 1
SKILL_VERSION: Final = "1.0.0"
PLAN_ID: Final = "gc-agents-kit"   # setup plan ledger key for the collection and the routines
KNOWLEDGE_NAME: Final = "Green Concrete knowledge"
KNOWLEDGE_DESCRIPTION: Final = (
    "Procedures, SOPs, price lists and supplier contacts for the Green Concrete agents. "
    "Agents read what is here and cite it; add notes or upload documents."
)


@dataclass(frozen=True)
class Text:
    en: str
    ar: str


@dataclass(frozen=True)
class GcTool:
    name: str
    label: Text
    description: str
    # The permissions its sections read; a section the caller lacks says "No access".
    permissions: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class GcSkill:
    id: str
    name: Text
    description: Text
    tool: str
    sources: tuple[str, ...]
    inputs: tuple[Text, ...]
    output: Text
    guardrails: tuple[Text, ...]
    triggers: frozenset[str]

    @property
    def instructions(self) -> str:
        tool = TOOL_BY_NAME[self.tool]
        lines = [
            f"{self.name.en} (Green Concrete skill {self.id} {SKILL_VERSION}).",
            f"Purpose: {self.description.en}",
            f"Data: read it with the tool {tool.name} ({tool.label.en}). It returns sections, each with a state: "
            "ok, empty, not_connected, not_synced, no_access or failed.",
            "Inputs: " + "; ".join(i.en for i in self.inputs) + ".",
            f"Output: {self.output.en}",
            "Rules:",
            *(f"- {g.en}" for g in (*COMMON_GUARDRAILS, *self.guardrails)),
            "Instructions never grant tools or approvals.",
        ]
        return "\n".join(lines)


COMMON_GUARDRAILS: Final = (
    Text("Use only figures, names and dates the tool or a cited knowledge passage returned. Never estimate or invent one.",
         "استخدم فقط الأرقام والأسماء والتواريخ التي أعادتها الأداة أو مقطع معرفة مذكور. لا تقدّر ولا تختلق أي رقم."),
    Text("For a section that is not_connected or not_synced, write \"Not connected\" and say where it is connected; never write zero.",
         "للقسم غير المتصل أو غير المتزامن اكتب \"غير متصل\" وأين يتم توصيله؛ لا تكتب صفرا أبدا."),
    Text("For a section that is no_access, say the reader's role cannot see it.",
         "للقسم المحجوب قل إن دور القارئ لا يسمح برؤيته."),
    Text("State when the fleet data was last synced.", "اذكر وقت آخر مزامنة لبيانات الأسطول."),
    Text("Cite a procedure or price from the Green Concrete knowledge collection by its title.",
         "اذكر عنوان الإجراء أو السعر من مجموعة معرفة جرين كونكريت."),
    Text("Never change a vehicle record, close a work order, send a message or approve anything.",
         "لا تغيّر سجل مركبة ولا تغلق أمر عمل ولا ترسل رسالة ولا توافق على أي شيء."),
)

TOOLS: Final[tuple[GcTool, ...]] = (
    GcTool("gc.fleet_health", Text("PMV fleet health", "صحة أسطول المعدات والمركبات"),
           "Plant, machinery and vehicles from the synced fleet data: vehicles by status and site, open and aged work "
           "orders, breakdowns (work types that say breakdown), overdue inspections, tyre alerts, use from odometer and "
           "hour readings and 30 day maintenance cost (date_from, date_to); read-only.",
           ("tyre:read",), ("tyre_pulse",)),
    GcTool("gc.tyre_plan", Text("Tyre plan", "خطة الإطارات"),
           "Tyre health, tyres due this month and next, the purchase plan for the next months (months) and open tyre "
           "alerts from the synced tyre data; read-only.",
           ("tyre:read",), ("tyre_pulse",)),
    GcTool("gc.ops_brief", Text("Daily operations brief", "الموجز اليومي للعمليات"),
           "Open tasks and approvals waiting, who of Green Concrete is on shift now and who starts next, tyre alerts, "
           "open work orders, today's workspace calendar and how fresh the fleet data is; read-only.",
           ("task:read", "tyre:read", "calendar:read"), ("tasks_approvals", "gc_seats", "tyre_pulse", "calendar")),
    GcTool("gc.data_quality", Text("Data quality check", "فحص جودة البيانات"),
           "How complete and fresh the synced fleet data is: rows read, kept and skipped per table with the reasons, "
           "the last sync, vehicles not inspected in 30 days, fitted tyres without a tread reading, vehicles without "
           "odometer or hour readings and work orders without a vehicle or a cost; read-only.",
           ("tyre:read",), ("tyre_pulse",)),
    GcTool("gc.shift_handover", Text("Shift handover", "تسليم المناوبة"),
           "The Green Concrete working hours (each person's own hours on their seat): who is on now, who ends next and "
           "who starts next, tasks changed since the shift began, still open or waiting for approval, and open tyre "
           "alerts; read-only.",
           ("task:read", "tyre:read"), ("gc_seats", "tasks_approvals", "tyre_pulse")),
    GcTool("gc.maintenance_costs", Text("Maintenance cost review", "مراجعة تكاليف الصيانة"),
           "Maintenance cost from synced work orders by work type, site and vehicle for a period (date_from, date_to), "
           "plus tyre costs; fuel is not connected in ANUM; read-only.",
           ("tyre:read",), ("tyre_pulse",)),
    GcTool("gc.fleet_watch", Text("Fleet alerts watch", "مراقبة تنبيهات الأسطول"),
           "Open tyre alerts (most severe first), overdue and upcoming inspections, tyres due this month and failed "
           "workshop checklists in the last 7 days; read-only.",
           ("tyre:read",), ("tyre_pulse",)),
)
TOOL_BY_NAME: Final = {t.name: t for t in TOOLS}
TOOL_NAMES: Final = tuple(t.name for t in TOOLS)

SKILLS: Final[tuple[GcSkill, ...]] = (
    GcSkill(
        "gc.pmv-fleet-health",
        Text("PMV fleet health review", "مراجعة صحة أسطول المعدات والمركبات"),
        Text("Reviews plant, machinery and vehicles: maintenance due, breakdowns, use and cost, from the fleet data the workspace has.",
             "يراجع المعدات والآليات والمركبات: الصيانة المستحقة والأعطال والاستخدام والتكلفة من بيانات الأسطول في مساحة العمل."),
        "gc.fleet_health", ("tyre_pulse",),
        (Text("a period (optional, default the last 30 days)", "فترة (اختيارية، الافتراضي آخر 30 يوما)"),
         Text("a site or vehicle named in the request (optional)", "موقع أو مركبة مذكورة في الطلب (اختياري)")),
        Text("A short review: fleet status, maintenance due and overdue, breakdowns, use per vehicle, cost, then three actions for the PMV manager with the vehicle and the reason.",
             "مراجعة قصيرة: حالة الأسطول، الصيانة المستحقة والمتأخرة، الأعطال، الاستخدام لكل مركبة، التكلفة، ثم ثلاثة إجراءات لمدير PMV مع المركبة والسبب."),
        (Text("Count a breakdown only where the work type says breakdown; if work types are missing, say so.",
              "احسب العطل فقط عندما يذكر نوع العمل كلمة عطل؛ وإذا غابت أنواع العمل فقل ذلك."),
         Text("Use (km or hours) comes only from odometer and hour readings; without them write Not connected.",
              "الاستخدام (كم أو ساعات) يأتي فقط من قراءات العداد والساعات؛ بدونها اكتب غير متصل.")),
        frozenset({"pmv", "plant", "machinery", "equipment", "breakdown", "breakdowns", "utilisation", "utilization",
                   "health", "review", "معدات", "الآليات", "آليات", "عطل", "أعطال", "الأعطال", "استخدام", "صحة", "مراجعة"}),
    ),
    GcSkill(
        "gc.tyre-plan",
        Text("Tyre planning", "تخطيط الإطارات"),
        Text("Plans tyre rotations, replacements and purchases from the Tyre Pulse sync and the tyre planning module.",
             "يخطط لتدوير الإطارات واستبدالها وشرائها من مزامنة تاير بلس ووحدة تخطيط الإطارات."),
        "gc.tyre_plan", ("tyre_pulse",),
        (Text("the number of months to plan (optional, default 3)", "عدد الأشهر للتخطيط (اختياري، الافتراضي 3)"),),
        Text("A plan: tyres due this month and next by vehicle and position, the purchase plan by size and brand with its estimated cost, and the alerts that change the plan.",
             "خطة: الإطارات المستحقة هذا الشهر والقادم حسب المركبة والموضع، وخطة الشراء حسب المقاس والعلامة مع التكلفة التقديرية، والتنبيهات التي تغيّر الخطة."),
        (Text("A purchase is a proposal for a person; never order or reserve stock.",
              "الشراء اقتراح لشخص؛ لا تطلب ولا تحجز مخزونا أبدا."),),
        frozenset({"tyre", "tyres", "tire", "tires", "rotation", "rotations", "replacement", "purchase", "plan",
                   "إطار", "إطارات", "الإطارات", "اطارات", "تدوير", "استبدال", "شراء", "خطة"}),
    ),
    GcSkill(
        "gc.daily-ops-brief",
        Text("Daily operations brief", "الموجز اليومي للعمليات"),
        Text("Prepares the manager's brief for the start of the day from tasks, approvals, the team's hours, the fleet and the calendar.",
             "يعد موجز المدير لبداية اليوم من المهام والموافقات وساعات عمل الفريق والأسطول والتقويم."),
        "gc.ops_brief", ("tasks_approvals", "gc_seats", "tyre_pulse", "calendar"),
        (Text("none; the brief covers today in the building's time zone", "لا شيء؛ الموجز يغطي اليوم بتوقيت المبنى"),),
        Text("A brief in this order: urgent alerts, approvals waiting, overdue tasks, who is on shift and who starts next, open work orders, today's calendar, then three priorities. Facts first, recommendations after.",
             "موجز بهذا الترتيب: التنبيهات العاجلة، الموافقات المنتظرة، المهام المتأخرة، من في المناوبة ومن يبدأ بعده، أوامر العمل المفتوحة، تقويم اليوم، ثم ثلاث أولويات. الحقائق أولا ثم التوصيات."),
        (Text("Name a person only as the seat and hours show them; never guess who is absent.",
              "اذكر الشخص فقط كما يظهر في المقعد والساعات؛ لا تخمّن من هو غائب."),),
        frozenset({"brief", "briefing", "morning", "daily", "today", "operations", "موجز", "ملخص", "صباح", "اليوم", "يومي", "العمليات"}),
    ),
    GcSkill(
        "gc.data-quality-check",
        Text("Data quality check", "فحص جودة البيانات"),
        Text("Checks that readings, inspections and work orders arrived complete in the Tyre Pulse sync and lists what is missing.",
             "يتحقق من وصول القراءات والفحوصات وأوامر العمل كاملة في مزامنة تاير بلس ويذكر ما ينقص."),
        "gc.data_quality", ("tyre_pulse",),
        (Text("none", "لا شيء"),),
        Text("A checklist: last sync and whether it is stale, rows skipped per table with the reason, then what is missing (vehicles not inspected, tyres without tread, vehicles without readings, work orders without a vehicle or cost), each with a count and the next step for the data collector.",
             "قائمة: آخر مزامنة وهل هي قديمة، الصفوف المتروكة لكل جدول مع السبب، ثم ما ينقص (مركبات بلا فحص، إطارات بلا قياس مداس، مركبات بلا قراءات، أوامر عمل بلا مركبة أو تكلفة)، كل منها مع العدد والخطوة التالية لجامع البيانات."),
        (Text("Report counts and ids only; never copy a row's contents.", "اذكر الأعداد والمعرّفات فقط؛ لا تنسخ محتوى أي صف."),),
        frozenset({"quality", "missing", "complete", "completeness", "sync", "readings", "stale", "check",
                   "جودة", "ناقص", "ناقصة", "مزامنة", "قراءات", "فحص"}),
    ),
    GcSkill(
        "gc.shift-handover",
        Text("Shift handover note", "ملاحظة تسليم المناوبة"),
        Text("Writes the handover note for the end of a shift, using each person's real working hours on their seat.",
             "يكتب ملاحظة التسليم لنهاية المناوبة باستخدام ساعات العمل الحقيقية لكل شخص على مقعده."),
        "gc.shift_handover", ("gc_seats", "tasks_approvals", "tyre_pulse"),
        (Text("none; the shift is read from the seats and the clock", "لا شيء؛ المناوبة تُقرأ من المقاعد والساعة"),),
        Text("A handover note: who hands over and who takes over (with their hours), what was done, what is still open or waiting for approval, and the alerts the next shift must watch.",
             "ملاحظة تسليم: من يسلّم ومن يستلم (مع ساعاتهم)، ما أنجز، ما زال مفتوحا أو بانتظار الموافقة، والتنبيهات التي يجب أن تتابعها المناوبة التالية."),
        (Text("Use only the hours stored on the seats; if a seat has none, say the hours are not set.",
              "استخدم فقط الساعات المحفوظة على المقاعد؛ وإذا لم يكن للمقعد ساعات فقل إنها غير محددة."),),
        frozenset({"handover", "hand-over", "shift", "shifts", "تسليم", "مناوبة", "المناوبة", "وردية", "الوردية"}),
    ),
    GcSkill(
        "gc.maintenance-cost-review",
        Text("Maintenance cost review", "مراجعة تكاليف الصيانة"),
        Text("Turns synced work order and tyre costs into short findings for the manager. Fuel is reviewed only when a fuel source exists; ANUM has none yet.",
             "يحوّل تكاليف أوامر العمل والإطارات المتزامنة إلى نتائج قصيرة للمدير. يُراجع الوقود فقط عند وجود مصدر له؛ لا يوجد في ANUM بعد."),
        "gc.maintenance_costs", ("tyre_pulse",),
        (Text("a period (optional, default the last 30 days)", "فترة (اختيارية، الافتراضي آخر 30 يوما)"),),
        Text("Findings: total cost and its currency, the largest work types, sites and vehicles, tyre cost, work orders without a cost, and a fuel line that says Not connected.",
             "نتائج: التكلفة الإجمالية وعملتها، أكبر أنواع العمل والمواقع والمركبات، تكلفة الإطارات، أوامر العمل بلا تكلفة، وسطر للوقود يقول غير متصل."),
        (Text("Never compare with a budget or a price you did not read from the knowledge collection.",
              "لا تقارن بميزانية أو سعر لم تقرأه من مجموعة المعرفة."),),
        frozenset({"cost", "costs", "spend", "spending", "expense", "expenses", "fuel", "تكلفة", "تكاليف", "مصاريف", "وقود", "الوقود"}),
    ),
    GcSkill(
        "gc.fleet-alerts-watch",
        Text("Fleet alerts watch", "مراقبة تنبيهات الأسطول"),
        Text("Watches tyre alerts, inspections and due dates and reports what needs attention now.",
             "يتابع تنبيهات الإطارات والفحوصات والمواعيد المستحقة ويبلّغ عما يحتاج انتباها الآن."),
        "gc.fleet_watch", ("tyre_pulse",),
        (Text("none", "لا شيء"),),
        Text("A watch list: critical alerts first, overdue inspections, inspections in the next 14 days, tyres due this month and failed checklists, each with the vehicle and what to check.",
             "قائمة متابعة: التنبيهات الحرجة أولا، الفحوصات المتأخرة، فحوصات الأيام 14 القادمة، الإطارات المستحقة هذا الشهر، والقوائم الفاشلة، كل منها مع المركبة وما يجب فحصه."),
        (Text("Never resolve or dismiss an alert.", "لا تغلق تنبيها ولا تتجاهله."),),
        frozenset({"alert", "alerts", "watch", "monitor", "monitoring", "overdue", "due", "inspection", "inspections",
                   "تنبيه", "تنبيهات", "مراقبة", "متأخر", "متأخرة", "مستحق", "فحوصات"}),
    ),
)
SKILL_BY_ID: Final = {s.id: s for s in SKILLS}

# Blueprint role key -> (skills, tools) it gains. Every tool is low risk, so no role's ceiling is reached.
ROLE_KIT: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "gc_pmv_manager": (("gc.daily-ops-brief", "gc.pmv-fleet-health", "gc.shift-handover", "gc.maintenance-cost-review"),
                       ("gc.ops_brief", "gc.fleet_health", "gc.shift_handover", "gc.maintenance_costs")),
    "gc_business_analyst": (("gc.pmv-fleet-health", "gc.maintenance-cost-review"),
                            ("gc.fleet_health", "gc.maintenance_costs")),
    "gc_tyre_planner": (("gc.tyre-plan", "gc.fleet-alerts-watch"), ("gc.tyre_plan", "gc.fleet_watch")),
    "gc_data_monitoring": (("gc.fleet-alerts-watch", "gc.data-quality-check", "gc.shift-handover"),
                           ("gc.fleet_watch", "gc.data_quality", "gc.shift_handover")),
    "gc_data_collector": (("gc.data-quality-check", "gc.shift-handover"), ("gc.data_quality", "gc.shift_handover")),
}


@dataclass(frozen=True)
class Gap:
    id: str
    name: Text
    detail: Text


GAPS: Final[tuple[Gap, ...]] = (
    Gap("fuel", Text("Fuel use and fuel cost", "استهلاك الوقود وتكلفته"),
        Text("ANUM has no fuel data source (no fuel card, telematics or fuel log connector). The cost review says Not connected for fuel.",
             "لا يوجد في ANUM مصدر لبيانات الوقود (لا بطاقة وقود ولا تتبع ولا سجل وقود). مراجعة التكاليف تقول غير متصل للوقود.")),
    Gap("service_intervals", Text("Planned service intervals for plant and machinery", "فترات الصيانة المخططة للمعدات والآليات"),
        Text("The fleet sync carries work orders and inspection schedules, not service intervals (hours or km between services), so maintenance due beyond the inspection schedule is not computed.",
             "تحمل مزامنة الأسطول أوامر العمل وجداول الفحص وليس فترات الصيانة (ساعات أو كم بين الصيانات)، لذلك لا تُحسب الصيانة المستحقة خارج جدول الفحص.")),
    Gap("spreadsheet_rows", Text("Figures inside spreadsheets in Documents", "الأرقام داخل جداول البيانات في المستندات"),
        Text("Agents read documents in the knowledge collection as cited text passages. They do not compute over spreadsheet rows; figures for a review must come from the fleet sync.",
             "يقرأ الوكلاء المستندات في مجموعة المعرفة كمقاطع نصية مذكورة. لا يحسبون على صفوف جداول البيانات؛ أرقام المراجعة يجب أن تأتي من مزامنة الأسطول.")),
    Gap("calendar_per_person", Text("Each person's own calendar", "التقويم الخاص بكل شخص"),
        Text("The brief reads the shared workspace calendar. Personal calendars synced by one person are not read for others.",
             "يقرأ الموجز تقويم مساحة العمل المشترك. التقاويم الشخصية التي يزامنها شخص لا تُقرأ للآخرين.")),
)


@dataclass(frozen=True)
class Routine:
    key: str
    name: Text
    description: Text
    role: str      # the blueprint role whose agent runs it
    skill: str
    cron: str      # in the Green Concrete building's own time zone
    prompt: str


ROUTINES: Final[tuple[Routine, ...]] = (
    Routine("daily_ops_brief", Text("Green Concrete: daily operations brief", "جرين كونكريت: الموجز اليومي للعمليات"),
            Text("Every day at 06:00, the start of the morning shift, the PMV manager agent prepares the brief.",
                 "كل يوم الساعة 06:00، بداية المناوبة الصباحية، يعد وكيل مدير PMV الموجز."),
            "gc_pmv_manager", "gc.daily-ops-brief", "0 6 * * *",
            "Prepare today's daily operations brief for the Green Concrete PMV manager. Write Not connected for any "
            "section the data is missing for; never invent a figure."),
    Routine("weekly_pmv_health", Text("Green Concrete: weekly PMV health report", "جرين كونكريت: تقرير صحة الأسطول الأسبوعي"),
            Text("Every Monday at 08:00 the PMV manager agent reviews plant, machinery and vehicles for the last 7 days.",
                 "كل يوم اثنين الساعة 08:00 يراجع وكيل مدير PMV المعدات والآليات والمركبات لآخر 7 أيام."),
            "gc_pmv_manager", "gc.pmv-fleet-health", "0 8 * * 1",
            "Prepare the weekly PMV fleet health review for the last 7 days: maintenance due, breakdowns, use and "
            "cost. Write Not connected for any section the data is missing for; never invent a figure."),
    Routine("morning_handover", Text("Green Concrete: morning shift handover", "جرين كونكريت: تسليم المناوبة الصباحية"),
            Text("Every day at 13:45, before the morning shift ends at 14:00, the data collector agent writes the handover note.",
                 "كل يوم الساعة 13:45، قبل نهاية المناوبة الصباحية في 14:00، يكتب وكيل جامع البيانات ملاحظة التسليم."),
            "gc_data_collector", "gc.shift-handover", "45 13 * * *",
            "Write the shift handover note for the end of the Green Concrete morning shift, using each person's "
            "working hours on their seat. Write Not connected for any section the data is missing for."),
)
ROUTINE_BY_KEY: Final = {r.key: r for r in ROUTINES}

# New data sources this kit reads (added to office_staffing_blueprint.SOURCES).
SEATS_SOURCE: Final = "gc_seats"


# --------------------------------------------------------------------------- runtime glue


def skill_manifests() -> list[Any]:
    """The skills as built-in runtime manifests (agent only, with their reviewed instructions)."""
    from .agent_skills import SkillManifest

    return [
        SkillManifest(
            id=s.id, version=SKILL_VERSION, name=s.name.en, description=s.description.en,
            name_ar=s.name.ar, description_ar=s.description.ar, triggers=s.triggers,
            required_tools=frozenset({s.tool}), instructions=s.instructions, agent_only=True,
        )
        for s in SKILLS
    ]


def _words(prompt: str) -> set[str]:
    from .agent_skills import prompt_words

    return prompt_words(prompt)


def choose_call(prompt: str, skill_ids: Sequence[str], today: date | None = None) -> Any:
    """The read tool for the Green Concrete skill the user's own words call for, with a period if they name one."""
    from .agent_tools import ToolCall
    from .schemas import utc_now
    from .tyre.fleet_tools import parse_period

    words = _words(prompt)
    candidates = [SKILL_BY_ID[s] for s in skill_ids if s in SKILL_BY_ID]
    if not candidates:
        return None
    best = max(candidates, key=lambda s: (len(words & s.triggers), -SKILLS.index(s)))
    arguments: dict[str, Any] = {}
    day = today or utc_now().date()
    start, end = parse_period(prompt, day)
    if start is not None:
        arguments["date_from"] = start.isoformat()
    if end is not None:
        arguments["date_to"] = end.isoformat()
    months = re.search(r"\b(\d{1,2})\s+months?\b", prompt.lower())
    if best.tool == "gc.tyre_plan" and months:
        arguments["months"] = max(1, min(int(months.group(1)), 36))
    return ToolCall(name=best.tool, arguments=arguments)


def kit_for(role_key: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return ROLE_KIT.get(role_key, ((), ()))


def skill_view(skill: GcSkill, source_states: dict[str, str], tool_registered: bool) -> dict[str, Any]:
    """One skill for the owner: what it needs, and whether that data is there now."""
    needs = [{"id": s, "state": source_states.get(s, "not_connected")} for s in skill.sources]
    if not tool_registered:
        state = "unavailable"
    elif any(n["state"] == "no_access" for n in needs):
        state = "no_access"
    elif any(n["state"] == "not_connected" for n in needs):
        state = "not_connected"
    else:
        state = "ready"
    return {
        "id": skill.id, "version": SKILL_VERSION, "name": _text(skill.name), "description": _text(skill.description),
        "tool": skill.tool, "inputs": [_text(i) for i in skill.inputs], "output": _text(skill.output),
        "guardrails": [_text(g) for g in (*COMMON_GUARDRAILS, *skill.guardrails)],
        "sources": needs, "state": state, "roles": [r for r, (skills, _) in ROLE_KIT.items() if skill.id in skills],
    }


def _text(value: Text) -> dict[str, str]:
    return {"en": value.en, "ar": value.ar}


def strings() -> Iterable[Text]:
    """Every reviewed text, for the Arabic coverage test."""
    for tool in TOOLS:
        yield tool.label
    for skill in SKILLS:
        yield from (skill.name, skill.description, *skill.inputs, skill.output, *skill.guardrails)
    yield from COMMON_GUARDRAILS
    for gap in GAPS:
        yield from (gap.name, gap.detail)
    for routine in ROUTINES:
        yield from (routine.name, routine.description)
