"""The scope of a new ``web.act`` call: the saved logins and cards it may use (docs/action-browser.md#how-it-is-used).

Filled at planning time, before the call is checkpointed and hashed, so the scope approval
shows exactly the handles the task may use. Reads handles and sites only, never a secret.
"""

from __future__ import annotations

import asyncio

from ..agent_tools import ToolCall
from ..schemas import Task, TenantContext


async def action_scope(task: Task, call: ToolCall) -> ToolCall:
    from .vault import scope_for

    args = dict(call.arguments)
    sites = [str(site) for site in args.get("allowed_domains") or []]
    card_names = [str(name) for name in args.pop("card_names", []) or []]
    any_card = bool(args.pop("any_card", False))
    context = TenantContext(tenant_id=task.tenant_id, workspace_id=task.workspace_id, user_id=task.created_by or "", roles=[])
    try:
        logins, cards, _ = await asyncio.to_thread(scope_for, context, sites, card_names, any_card=any_card)
    except Exception:
        # The vault could not be read: the task may use no saved secret (the owner can retry).
        logins, cards = {}, []
    args["logins"] = logins
    args["cards"] = cards
    return ToolCall(name=call.name, arguments=args)
