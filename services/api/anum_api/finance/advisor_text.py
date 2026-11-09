"""The Money advisor's next steps: fixed English and Arabic templates (docs/finance.md#money-advisor).

Deterministic: no model call. Each step has a code, a severity, the figures it names and its text in
both languages. Amounts, dates and names inside the Arabic text are wrapped in Unicode isolates so they
read left to right in a right-to-left sentence. A step that needs data ANUM does not have says what to
add and where. ANUM only advises: no step pays, moves money or touches a bank or card.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from .models import Category

if TYPE_CHECKING:
    from .advisor import Action, AdvisorNote

FSI, PDI = chr(0x2068), chr(0x2069)
SEVERITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "info": 3}

CATEGORY_EN = {
    Category.INCOME: "income", Category.TRANSFER: "transfers", Category.GROCERIES: "groceries", Category.DINING: "dining",
    Category.TRANSPORT: "transport", Category.FUEL: "fuel", Category.UTILITIES: "utilities", Category.TELECOM: "phone and internet",
    Category.HOUSING: "housing", Category.SHOPPING: "shopping", Category.HEALTH: "health", Category.EDUCATION: "education",
    Category.ENTERTAINMENT: "entertainment", Category.SUBSCRIPTIONS: "subscriptions", Category.TRAVEL: "travel",
    Category.INSURANCE: "insurance", Category.FEES: "fees", Category.CASH: "cash", Category.OTHER: "other",
    Category.CASHBACK: "cashback", Category.REFUNDS: "refunds", Category.FOOD: "food", Category.ROOM_RENT: "room rent",
}
CATEGORY_AR = {
    Category.INCOME: "الدخل", Category.TRANSFER: "التحويلات", Category.GROCERIES: "البقالة", Category.DINING: "المطاعم",
    Category.TRANSPORT: "المواصلات", Category.FUEL: "الوقود", Category.UTILITIES: "المرافق", Category.TELECOM: "الهاتف والإنترنت",
    Category.HOUSING: "السكن", Category.SHOPPING: "التسوق", Category.HEALTH: "الصحة", Category.EDUCATION: "التعليم",
    Category.ENTERTAINMENT: "الترفيه", Category.SUBSCRIPTIONS: "الاشتراكات", Category.TRAVEL: "السفر",
    Category.INSURANCE: "التأمين", Category.FEES: "الرسوم", Category.CASH: "النقد", Category.OTHER: "أخرى",
    Category.CASHBACK: "الاسترداد النقدي", Category.REFUNDS: "المبالغ المستردة", Category.FOOD: "الطعام", Category.ROOM_RENT: "إيجار الغرفة",
}

# code: (English, Arabic). Placeholders are filled from already formatted strings.
TEMPLATES: dict[str, tuple[str, str]] = {
    "no_accounts": (
        "Add your bank account under Accounts and import a statement to get advice.",
        "أضف حسابك البنكي في الحسابات واستورد كشفًا للحصول على النصيحة.",
    ),
    "import_statements": (
        "Import your bank and card statements so ANUM can see where your money goes.",
        "استورد كشوف حسابك البنكي وبطاقتك ليرى ANUM أين يذهب مالك.",
    ),
    "stale_records": (
        "Your newest record is from {date}. Import your latest statement for current advice.",
        "أحدث سجل لديك بتاريخ {date}. استورد آخر كشف للحصول على نصيحة محدثة.",
    ),
    "history_needed": (
        "{currency}: you have {days} days of records. Import at least 30 days of statements to see how long your money would last.",
        "{currency}: لديك سجلات لمدة {days} يومًا. استورد كشوفًا لمدة 30 يومًا على الأقل لمعرفة كم سيكفيك مالك.",
    ),
    "runway_low": (
        "{currency}: if your income stopped, your money would cover essentials for {days} days, until {date}. Aim for at least 90 days before new spending.",
        "{currency}: إذا توقف دخلك فسيغطي مالك الأساسيات لمدة {days} يومًا حتى {date}. اجعل هدفك 90 يومًا على الأقل قبل أي إنفاق جديد.",
    ),
    "card_pay_full_rate": (
        "Pay {card} in full ({amount}) by {date}. Paying only the minimum ({minimum}) would cost about {interest} in interest next month.",
        "سدّد {card} بالكامل ({amount}) قبل {date}. دفع الحد الأدنى فقط ({minimum}) سيكلفك نحو {interest} فوائد في الشهر القادم.",
    ),
    "card_pay_full": (
        "Pay {card} in full ({amount}) by {date} if you can. The minimum is {minimum}.",
        "سدّد {card} بالكامل ({amount}) قبل {date} إن استطعت. الحد الأدنى {minimum}.",
    ),
    "card_minimum_too_low": (
        "The minimum payment on {card} does not cover its interest, so the balance would never clear. Pay more than {minimum} each month.",
        "الحد الأدنى لـ {card} لا يغطي الفوائد، فلن ينتهي الرصيد أبدًا. ادفع أكثر من {minimum} كل شهر.",
    ),
    "card_add_debt": (
        "{card} owes {amount}. Add it under Debts with its minimum payment, due day and interest rate so ANUM can advise on it.",
        "على {card} مبلغ {amount}. أضفها في الديون مع الحد الأدنى ويوم الاستحقاق ونسبة الفائدة ليتمكن ANUM من النصيحة.",
    ),
    "card_add_details": (
        "Add the due day and minimum payment of {card} under Debts so ANUM can remind you on time.",
        "أضف يوم الاستحقاق والحد الأدنى لـ {card} في الديون ليذكّرك ANUM في الوقت المناسب.",
    ),
    "card_add_rate": (
        "Add the interest rate of {card} under Debts to see what paying only the minimum would cost.",
        "أضف نسبة الفائدة لـ {card} في الديون لترى كلفة دفع الحد الأدنى فقط.",
    ),
    "card_high_use": (
        "{card} uses {percent}% of its limit. Bringing it under 30% lowers the interest you risk.",
        "تستخدم {card} نسبة {percent}% من حدها. خفضها إلى أقل من 30% يقلل الفوائد المحتملة.",
    ),
    "bill_due": (
        "{name} ({amount}) is due on {date}, in {days} days.",
        "يستحق {name} ({amount}) في {date}، بعد {days} أيام.",
    ),
    "bill_due_today": (
        "{name} ({amount}) is due today.",
        "يستحق {name} ({amount}) اليوم.",
    ),
    "bill_overdue": (
        "{name} ({amount}) was due on {date} and no payment is matched to it. Pay it, or match the payment under Bills.",
        "كان {name} ({amount}) مستحقًا في {date} ولا توجد دفعة مطابقة له. ادفعه أو طابق الدفعة في الفواتير.",
    ),
    "debt_add_due_day": (
        "Add the due day of {name} under Debts so its minimum payment ({amount}) shows on the right date.",
        "أضف يوم استحقاق {name} في الديون ليظهر الحد الأدنى ({amount}) في تاريخه الصحيح.",
    ),
    "spending_up": (
        "{category} is up {percent}% on your 3 month average: {amount} so far this month against about {expected} in a usual month.",
        "ارتفع إنفاق {category} بنسبة {percent}% عن متوسط 3 أشهر: {amount} حتى الآن هذا الشهر مقابل نحو {expected} في شهر عادي.",
    ),
    "spending_new": (
        "{category}: {amount} in the last 7 days, where you usually spend little or nothing. Check it was planned.",
        "{category}: أنفقت {amount} خلال آخر 7 أيام على فئة لا تنفق عليها عادة. تأكد أنه كان مخططًا له.",
    ),
    "subscriptions_review": (
        "Review {count} subscriptions ({amount} in the next 30 days) and cancel the ones you do not use.",
        "راجع {count} اشتراكات ({amount} خلال 30 يومًا القادمة) وألغِ ما لا تستخدمه.",
    ),
    "save_recurring": (
        "Save {count} recurring payments as bills ({amount} in the next 30 days) so they are counted in your plans.",
        "احفظ {count} دفعات متكررة كفواتير ({amount} خلال 30 يومًا القادمة) لتُحسب في خططك.",
    ),
    "no_salary": (
        "No salary found in your records for the last 60 days. The survival figures already assume no income.",
        "لم نجد راتبًا في سجلاتك خلال آخر 60 يومًا. أرقام الصمود تفترض أصلًا عدم وجود دخل.",
    ),
}


def money_en(amount: Decimal, currency: str) -> str:
    return f"{currency} {amount:,}"


def iso(text: str) -> str:
    """Wrap a left-to-right piece (an amount, a date, a name) for a right-to-left sentence."""
    return f"{FSI}{text}{PDI}"


def _action(code: str, severity: str, en: dict[str, str], ar: dict[str, str] | None = None, *, currency: str | None = None,
            tab: str | None = None, sort: int = 0) -> tuple[int, int, Action]:
    from .advisor import Action

    english, arabic = TEMPLATES[code]
    ar_params = ar if ar is not None else {k: iso(v) for k, v in en.items()}
    item = Action(code=code, severity=severity, currency=currency, params=en, text_en=english.format(**en), text_ar=arabic.format(**ar_params), tab=tab)  # type: ignore[arg-type]
    return SEVERITY_RANK[severity], sort, item


def actions(note: AdvisorNote) -> list[Action]:
    """The ranked next steps: most urgent first, then the soonest date."""
    found: list[tuple[int, int, Action]] = []
    today = note.today
    if not note.has_accounts:
        found.append(_action("no_accounts", "high", {}, tab="accounts"))
    elif note.newest_record_on is None:
        found.append(_action("import_statements", "high", {}, tab="import"))
    elif note.newest_record_on < today - timedelta(days=7):
        found.append(_action("stale_records", "medium", {"date": note.newest_record_on.isoformat()}, tab="import"))

    for card in note.cards:
        name = card.name + (f" ({card.last4})" if card.last4 else "")
        c = card.currency
        if card.owed <= 0:
            continue
        if card.never_clears_at_minimum and card.minimum_payment is not None:
            found.append(_action("card_minimum_too_low", "urgent", {"card": name, "minimum": money_en(card.minimum_payment, c)}, currency=c, tab="debts"))
        if card.due_on is not None:
            severity = "urgent" if (card.days_until or 0) <= 7 else "high"
            params = {"card": name, "amount": money_en(card.owed, c), "date": card.due_on.isoformat(),
                      "minimum": money_en(card.minimum_payment, c) if card.minimum_payment is not None else "-"}
            if card.interest_if_minimum is not None and card.interest_if_minimum > 0:
                found.append(_action("card_pay_full_rate", severity, {**params, "interest": money_en(card.interest_if_minimum, c)}, currency=c,
                                     tab="debts", sort=card.days_until or 0))
            else:
                found.append(_action("card_pay_full", severity, params, currency=c, tab="debts", sort=card.days_until or 0))
        if "debt_record" in card.missing:
            found.append(_action("card_add_debt", "medium", {"card": name, "amount": money_en(card.owed, c)}, currency=c, tab="debts"))
        else:
            if "due_day" in card.missing or "minimum" in card.missing:
                found.append(_action("card_add_details", "medium", {"card": name}, currency=c, tab="debts"))
            if "rate" in card.missing:
                found.append(_action("card_add_rate", "medium", {"card": name}, currency=c, tab="debts"))
        if card.utilization_percent is not None and card.utilization_percent >= 30:
            found.append(_action("card_high_use", "medium", {"card": name, "percent": str(card.utilization_percent)},
                                 {"card": iso(name), "percent": iso(str(card.utilization_percent))}, currency=c, tab="accounts"))

    for block in note.currencies:
        c = block.currency
        runway = block.runway
        if runway.status == "not_enough_history":
            found.append(_action("history_needed", "high", {"currency": c, "days": str(runway.history_days)}, currency=c, tab="import"))
        essential = next((s for s in runway.scenarios if s.kind == "essential"), None)
        if essential is not None and essential.days is not None and essential.days < 60 and essential.runs_out_on is not None:
            found.append(_action("runway_low", "urgent" if essential.days < 30 else "high",
                                 {"currency": c, "days": str(essential.days), "date": essential.runs_out_on.isoformat()}, currency=c, sort=essential.days))
        for item in block.coming.items:
            if item.kind in ("bill", "minimum") and item.days_until is not None and item.days_until <= 7:
                params = {"name": item.name, "amount": money_en(item.amount, c), "date": item.due_on.isoformat() if item.due_on else "-",
                          "days": str(item.days_until)}
                code = "bill_overdue" if item.days_until < 0 else "bill_due_today" if item.days_until == 0 else "bill_due"
                found.append(_action(code, "urgent" if item.days_until <= 2 else "high", params, currency=c,
                                     tab="bills" if item.kind == "bill" else "debts", sort=item.days_until))
            elif item.kind == "minimum" and item.due_on is None:
                found.append(_action("debt_add_due_day", "medium", {"name": item.name, "amount": money_en(item.amount, c)}, currency=c, tab="debts"))
        for spike in block.spending.spikes[:2]:
            en = {"category": CATEGORY_EN[spike.category].capitalize(), "amount": money_en(spike.amount, c)}
            ar = {"category": CATEGORY_AR[spike.category], "amount": iso(en["amount"])}
            if spike.percent is None:
                found.append(_action("spending_new", "medium", en, ar, currency=c, tab="transactions"))
                continue
            en |= {"percent": str(spike.percent), "expected": money_en(spike.expected, c)}
            ar |= {"percent": iso(en["percent"]), "expected": iso(en["expected"])}
            found.append(_action("spending_up", "medium", en, ar, currency=c, tab="transactions"))
        subscriptions = [i for i in block.coming.items if i.kind == "subscription"]
        if subscriptions:
            total = sum((i.amount for i in subscriptions), Decimal(0))
            found.append(_action("subscriptions_review", "info", {"count": str(len(subscriptions)), "amount": money_en(total, c)}, currency=c, tab="bills"))
        recurring = [i for i in block.coming.items if i.kind == "recurring"]
        if recurring:
            total = sum((i.amount for i in recurring), Decimal(0))
            found.append(_action("save_recurring", "info", {"count": str(len(recurring)), "amount": money_en(total, c)}, currency=c, tab="bills"))

    if note.history_days >= 30 and not note.income.salary_found:
        found.append(_action("no_salary", "info", {}))
    found.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in found[:10]]


def notice_text(note: AdvisorNote) -> tuple[str, str]:
    """The in-app notification: days and counts only, no amount, no name (push shows fixed text)."""
    title = "Your weekly money note is ready"
    parts: list[str] = []
    for block in note.currencies:
        essential = next((s for s in block.runway.scenarios if s.kind == "essential"), None)
        if essential is not None and essential.days is not None:
            parts.append(f"{block.currency}: about {essential.days} days of essentials if income stopped.")
    soon = sum(1 for block in note.currencies for i in block.coming.items if i.days_until is not None and i.days_until <= 7)
    parts.append(f"{soon} payment{'s' if soon != 1 else ''} due in the next 7 days.")
    parts.append(f"{len(note.actions)} next step{'s' if len(note.actions) != 1 else ''} to look at.")
    parts.append("Open Finance, Money advisor. ANUM only advises: it never moves money.")
    return title, " ".join(parts)


__all__ = ["CATEGORY_AR", "CATEGORY_EN", "TEMPLATES", "actions", "iso", "money_en", "notice_text"]
