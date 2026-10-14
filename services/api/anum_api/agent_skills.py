from collections.abc import Iterable
import re

from pydantic import BaseModel, Field

from .schemas import RiskLevel

# Words that select the commerce skill (anum_api/commerce/tools.py parses the request).
COMMERCE_TRIGGERS = frozenset(
    {
        "sales", "sold", "revenue", "orders", "order", "amazon", "noon", "stock", "inventory", "listing", "listings",
        "price", "prices", "returns", "unshipped", "sku", "selling", "restock",
        # Arabic (docs/conversations.md#tools-in-chat); commerce/tools.py parses these too.
        "مبيعات", "المبيعات", "بعنا", "طلبات", "الطلبات", "طلب", "مخزون", "المخزون", "أمازون", "امازون",
    }
)

# Words that select the finance skill (anum_api/finance/tools.py parses the request). Moving
# money ("pay", "transfer") is never the finance skill's: those stay with external-action.
FINANCE_TRIGGERS = frozenset(
    {
        "spent", "spend", "spending", "expenses", "expense", "budget", "bills", "bill", "balance", "balances",
        "income", "debts", "debt", "transactions", "finance", "finances", "subscriptions", "savings",
        # Arabic: expenses, I spent, bills, a bill, balance, my balance, budget, debts.
        "مصاريف", "مصروفات", "مصروف", "صرفت", "فواتير", "فاتورة", "رصيد", "رصيدي", "ميزانية", "ديون",
    }
)


class SkillManifest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: str
    description: str
    triggers: frozenset[str] = Field(default_factory=frozenset)
    required_tools: frozenset[str] = Field(default_factory=frozenset)
    risk_level: RiskLevel = RiskLevel.LOW


class SkillRegistry:
    def __init__(self, manifests: Iterable[SkillManifest]) -> None:
        manifest_list = list(manifests)
        self._manifests = {manifest.id: manifest for manifest in manifest_list}
        if len(self._manifests) != len(manifest_list):
            raise ValueError("skill ids must be unique")

    def get(self, skill_id: str) -> SkillManifest | None:
        return self._manifests.get(skill_id)

    def manifests(self) -> list[SkillManifest]:
        """Every skill, in registration order."""
        return list(self._manifests.values())

    def select(self, prompt: str, available_tools: set[str]) -> list[SkillManifest]:
        words = prompt_words(prompt)
        return [
            manifest
            for manifest in self._manifests.values()
            if (not manifest.triggers or bool(words & manifest.triggers))
            and manifest.required_tools <= available_tools
        ]


_ARABIC = re.compile(r"[؀-ۿ]")


def prompt_words(prompt: str) -> set[str]:
    """The words a skill trigger can match, in English and Arabic.

    Arabic words also count without a leading conjunction ("و", and) or article ("ال",
    the), so "وأرسل" (and send) and "البريد" (the mail) match "أرسل" and "بريد".
    """
    words: set[str] = set()
    # Two-word terms a trigger may name: "work order(s)" and "job card(s)" (fleet maintenance).
    for phrase, token in ((r"\bwork\s*orders?\b", "work-order"), (r"\bjob\s*cards?\b", "job-card")):
        if re.search(phrase, prompt, re.IGNORECASE):
            words.add(token)
    for word in re.findall(r"[\w-]+", prompt.lower()):
        words.add(word)
        if not _ARABIC.match(word):
            continue
        bare = word[1:] if word.startswith("و") and len(word) > 3 else word
        words.add(bare)
        for variant in (word, bare):
            if variant.startswith("ال") and len(variant) > 4:
                words.add(variant[2:])
    return words


def default_skill_registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillManifest(
                id="anum.task-planning",
                version="1.0.0",
                name="Task planning",
                description="Turns a task into an observable, policy-checked execution plan.",
            ),
            SkillManifest(
                id="anum.document-drafting",
                version="1.0.0",
                name="Document drafting",
                description="Drafts and summarizes text without external side effects.",
                # Arabic: write, summarize (with and without shadda), summary, prepare, draft.
                triggers=frozenset(
                    {"draft", "write", "summarize", "prepare", "اكتب", "لخص", "لخّص", "ملخص", "جهز", "حضر", "مسودة"}
                ),
                required_tools=frozenset({"anum.respond"}),
            ),
            SkillManifest(
                id="anum.web-search",
                version="1.0.0",
                name="Web search",
                description="Looks things up on the web through the deployment's private SearXNG.",
                triggers=frozenset(
                    {"search", "web", "internet", "online", "lookup", "google", "news",
                     "ابحث", "بحث", "الإنترنت", "الانترنت", "إنترنت", "انترنت", "أخبار", "اخبار"}
                ),
                # Selectable only where ANUM_SEARXNG_URL registered the tool.
                required_tools=frozenset({"web_search"}),
            ),
            SkillManifest(
                id="anum.whatsapp-inbox",
                version="1.0.0",
                name="WhatsApp inbox",
                description="Reads the workspace's WhatsApp conversations; replies are drafted and sent from the inbox with approval.",
                triggers=frozenset({"whatsapp", "واتساب", "الواتساب", "واتس", "الواتس", "وتساب"}),
                required_tools=frozenset({"list_whatsapp_conversations"}),
            ),
            SkillManifest(
                id="anum.email",
                version="1.0.0",
                name="Email inbox",
                description="Finds and summarises emails in the connected Gmail, Outlook and Zoho mailboxes.",
                triggers=frozenset(
                    {"email", "emails", "e-mail", "e-mails", "inbox", "mail", "mails", "gmail", "outlook", "zoho",
                     "بريد", "البريد", "بريدي", "ايميل", "إيميل", "الايميل", "الإيميل", "ايميلات", "إيميلات",
                     "الايميلات", "الإيميلات", "إيميلاتي", "ايميلاتي"}
                ),
                # Selectable wherever the email tools are registered (docs/email.md).
                required_tools=frozenset({"search_email"}),
            ),
            SkillManifest(
                id="anum.commerce",
                version="1.0.0",
                name="Commerce",
                description=(
                    "Answers sales, order and stock questions across Amazon, noon and the own store, and "
                    "proposes price or stock changes that wait for approval (docs/commerce.md)."
                ),
                triggers=COMMERCE_TRIGGERS,
                # Selectable where the commerce tools are registered (the API and the worker).
                required_tools=frozenset({"commerce.sales_summary"}),
            ),
            SkillManifest(
                id="anum.finance",
                version="1.0.0",
                name="Personal finance",
                description=(
                    "Answers questions about your own spending, income, balances, bills and debts from your "
                    "imported statements. Read only: it never pays, transfers or trades (docs/finance.md)."
                ),
                triggers=FINANCE_TRIGGERS,
                # Selectable where the finance tools are registered (the API and the worker).
                required_tools=frozenset({"finance.summary", "finance.transactions.search", "finance.bills.upcoming"}),
            ),
            SkillManifest(
                id="anum.tyre-planning",
                version="1.0.0",
                name="Tyre planning",
                description=(
                    "Answers fleet tyre and vehicle questions from the synced TyrePulse data: health, tyres "
                    "due, purchase plan, alerts, vehicles, inspections, workshop checklists, work orders "
                    "and maintenance costs (docs/tyre-planning.md)."
                ),
                triggers=frozenset(
                    {"tyre", "tyres", "tire", "tires", "tread", "treads", "retread",
                     "inspection", "inspections", "inspected", "checklist", "checklists", "workshop",
                     "fleet", "vehicle", "vehicles", "truck", "trucks", "odometer", "mileage", "maintenance",
                     "work-order", "job-card",
                     "إطار", "إطارات", "اطار", "اطارات", "الإطارات", "الاطارات", "كفر", "كفرات", "الكفرات",
                     "فحص", "الفحوصات", "مركبة", "المركبات", "الشاحنات", "ورشة", "صيانة"}
                ),
                # Selectable where the tyre tools are registered (the API and the worker).
                required_tools=frozenset({"tyre_fleet_summary", "tyres_due", "purchase_plan", "tyre_alerts"}),
            ),
            SkillManifest(
                id="anum.team-assign",
                version="1.0.0",
                name="Team assignment",
                description="Assigns work to a teammate, ANUM or a department assistant and notifies them.",
                triggers=frozenset({"ask", "tell", "remind", "assign"}),
                required_tools=frozenset({"team.assign_task"}),
                risk_level=RiskLevel.MEDIUM,
            ),
            SkillManifest(
                id="anum.marketing",
                version="1.0.0",
                name="Social media marketing",
                description="Drafts posts, asks to publish them (with approval) and reports how posts performed.",
                triggers=frozenset(
                    {"instagram", "facebook", "linkedin", "tiktok", "youtube", "tweet", "tweets", "twitter", "social",
                     "marketing", "campaign", "hashtags", "reel", "reels", "shorts"}
                ),
                # Selectable only where the marketing tools are registered.
                required_tools=frozenset({"marketing.draft_social_post", "marketing.schedule_post", "marketing.summary"}),
            ),
            SkillManifest(
                id="anum.external-action",
                version="1.0.0",
                name="External action",
                description="Plans governed actions that affect systems outside ANUM.",
                triggers=frozenset(
                    {
                        "delete", "send", "publish", "spend", "pay", "transfer", "permission", "credential",
                        "أرسل", "ارسل", "احذف", "انشر", "ادفع",
                    }
                ),
                required_tools=frozenset({"external.action"}),
                risk_level=RiskLevel.HIGH,
            ),
        ]
    )
