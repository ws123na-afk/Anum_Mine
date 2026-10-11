"""Spoken commerce answers: "ANUM, how were Amazon sales today?" (docs/commerce.md#assistant-and-voice).

Read-only like every spoken answer: a price or stock change is only *proposed* as a task
(the ``confirm`` tier) that the person creates on screen, and the change itself then
waits for an approval with its payload hash.
"""

from __future__ import annotations

from ..authorization import Permission
from ..schemas import TenantContext
from . import tools as commerce_tools
from .insights import low_stock, sales_summary
from .models import OPEN_ORDER_STATUSES
from .store import open_commerce_store
from .sync import workspace_settings

READ, CONFIRM = "read", "confirm"


def commerce_voice_reply(context: TenantContext, text: str, arabic: bool) -> tuple[str, str, str | None] | None:
    """``(risk tier, reply, proposed task)`` for a commerce question, or None for anything else."""
    from ..dependencies import has_permission

    if commerce_tools.is_change_request(text) and commerce_tools.detect_channel(text):
        if not has_permission(context, Permission.COMMERCE_OPERATE):
            return READ, (
                "تغيير الأسعار والمخزون متاح لمالكي مساحة العمل وأعضائها فقط."
                if arabic
                else "Only owners and members of this workspace can change prices or stock."
            ), None
        reply = (
            "سأجهّز هذا التغيير كمهمة. اضغط إنشاء، ولن يُرسل شيء قبل موافقتك على الشاشة."
            if arabic
            else "I'll set that change up as a task. Tap Create task; nothing is sent until you approve it on screen."
        )
        return CONFIRM, reply, text
    if not commerce_tools.is_commerce_question(text):
        return None
    if not has_permission(context, Permission.COMMERCE_READ):
        return READ, ("لا تملك صلاحية عرض المبيعات في هذه المساحة." if arabic else "You don't have access to sales in this workspace."), None
    call = commerce_tools.plan_call(text, "ar" if arabic else "en")
    if call is None or call.name not in ("commerce.sales_summary", "commerce.low_stock", "commerce.list_orders"):
        return None
    with open_commerce_store(context) as store:
        if not store.list_connectors(context):
            return READ, (
                "لم تربط أي قناة بيع بعد. افتح التجارة واربط أمازون أو نون أو متجرك."
                if arabic
                else "No sales channel is connected yet. Open Commerce to connect Amazon, noon or your store."
            ), None
        if call.name == "commerce.sales_summary":
            spoken, _ = sales_summary(
                store,
                context,
                channel=commerce_tools._channel(call.arguments.get("channel")),
                period=str(call.arguments.get("period") or "today"),
                arabic=arabic,
            )
            return READ, spoken, None
        if call.name == "commerce.low_stock":
            rows = low_stock(store, context, workspace_settings(context, store), limit=100)
            if not rows:
                return READ, ("لا شيء منخفض في المخزون." if arabic else "Nothing is low on stock."), None
            first = ", ".join(f"{row.sku} ({row.quantity})" for row in rows[:3])
            if arabic:
                return READ, f"{len(rows)} من القوائم منخفضة المخزون، منها: {first}.", None
            return READ, f"{len(rows)} listing{'s are' if len(rows) != 1 else ' is'} low on stock: {first}.", None
        _, total = store.list_orders(context, statuses=list(OPEN_ORDER_STATUSES), limit=1, offset=0)
    if total == 0:
        return READ, ("لا توجد طلبات بانتظار الشحن." if arabic else "No orders are waiting to ship."), None
    if arabic:
        return READ, f"لديك {total} من الطلبات بانتظار الشحن.", None
    return READ, f"You have {total} order{'s' if total != 1 else ''} waiting to ship.", None
