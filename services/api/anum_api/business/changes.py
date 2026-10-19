"""Change orders and project profitability: the pure rules (docs/business.md#change-orders).

A change order amends the version a project currently runs on. Applying it never edits
that version: it produces the content of a new version, reconciled in ``Decimal`` with
the same rounding as proposals (``models.money``, two places, half up). The deltas are
the difference between the two versions' totals, so the accepted price plus every
approved change order always equals the current contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .models import (
    ChangeAction,
    ChangeOrder,
    LineItem,
    MilestonePlan,
    ProposalContent,
    ProposalVersion,
    money,
)
from .service import proposal_totals


class ChangeError(ValueError):
    """Why a change order cannot be applied to the version it amends (shown to the person)."""


@dataclass
class AppliedChange:
    content: ProposalContent
    subtotal_delta: Decimal
    tax_delta: Decimal
    total_delta: Decimal
    # Existing milestone positions whose amount went down, with the new amount.
    reduced: dict[int, Decimal] = field(default_factory=dict)
    # A milestone added for an increase (appended after the existing ones).
    added: MilestonePlan | None = None


def _line_key(item: LineItem) -> tuple[str, str, Decimal, Decimal]:
    return (" ".join(item.description.split()).casefold(), item.kind.value, money(item.quantity), money(item.unit_price))


def apply_change(base: ProposalVersion, order: ChangeOrder, settled_positions: set[int]) -> AppliedChange:
    """The new contract content after ``order``. ``settled_positions`` are the milestones
    already accepted, submitted or paid: a price reduction never touches those."""
    content = ProposalContent.model_validate(base.model_dump())
    lines = [LineItem.model_validate(item.model_dump()) for item in content.line_items]
    for change in order.lines:
        plain_line = LineItem(description=" ".join(change.description.split()), kind=change.kind, quantity=change.quantity, unit_price=change.unit_price)
        if change.action == ChangeAction.REMOVE:
            match = next((index for index, item in enumerate(lines) if _line_key(item) == _line_key(plain_line)), None)
            if match is None:
                raise ChangeError(f"\"{plain_line.description}\" is not a line of version {base.version}; a removal must match a line exactly.")
            lines.pop(match)
        else:
            lines.append(plain_line)
    if not order.lines and order.timeline_days == 0 and order.internal_cost_delta == 0:
        raise ChangeError("A change order changes the scope, the timeline or the internal cost. Add a line, a timeline change or a cost change.")
    if not lines:
        raise ChangeError("The contract would have no line items left.")
    if len(lines) > 60:
        raise ChangeError("The contract would have more than 60 line items.")
    internal_cost = money(base.internal_cost + order.internal_cost_delta)
    if internal_cost < 0:
        raise ChangeError(f"The internal cost would go below zero ({internal_cost}).")
    content.line_items = lines
    content.internal_cost = internal_cost
    before = proposal_totals(base)
    after_lines = proposal_totals(content)
    total_delta = money(after_lines["total"] - before["total"])
    applied = AppliedChange(
        content=content,
        subtotal_delta=money(after_lines["subtotal"] - before["subtotal"]),
        tax_delta=money(after_lines["tax"] - before["tax"]),
        total_delta=total_delta,
    )
    milestones = [MilestonePlan.model_validate(item.model_dump()) for item in content.milestones]
    if milestones and total_delta > 0:
        if len(milestones) >= 20:
            raise ChangeError("The contract already has 20 milestones; a change order cannot add another.")
        last_due = max((item.due_in_days or 0 for item in milestones), default=0)
        applied.added = MilestonePlan(
            title=f"Change order {order.number}: {order.title}"[:200], amount=total_delta,
            due_in_days=min(3650, last_due + max(order.timeline_days, 0)), acceptance="The change order's scope is delivered.",
        )
        milestones.append(applied.added)
    elif milestones and total_delta < 0:
        remaining = -total_delta
        open_value = money(sum((item.amount for position, item in enumerate(milestones) if position not in settled_positions), Decimal(0)))
        if open_value < remaining:
            raise ChangeError(f"The reduction of {content.currency.value} {remaining} is more than the milestones not yet accepted, submitted or paid ({content.currency.value} {open_value}).")
        for position in range(len(milestones) - 1, -1, -1):
            if remaining <= 0:
                break
            if position in settled_positions:
                continue
            take = min(remaining, milestones[position].amount)
            if take > 0:
                milestones[position].amount = money(milestones[position].amount - take)
                applied.reduced[position] = milestones[position].amount
                remaining = money(remaining - take)
    content.milestones = milestones
    issues = proposal_totals(content)["issues"]
    if issues:
        raise ChangeError(" ".join(issues))
    if proposal_totals(content)["total"] <= 0:
        raise ChangeError("The contract total would be zero.")
    return applied


def change_hash(order: ChangeOrder, base_version: int, base_hash: str) -> str:
    """SHA-256 of what the owner approves: the change itself and the exact version it amends."""
    document: dict[str, Any] = {
        "project_id": order.project_id,
        "number": order.number,
        "title": order.title,
        "request": order.request,
        "requested_by": order.requested_by,
        "lines": [item.model_dump(mode="json") for item in order.lines],
        "timeline_days": order.timeline_days,
        "internal_cost_delta": str(money(order.internal_cost_delta)),
        "base_version": base_version,
        "base_hash": base_hash,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
