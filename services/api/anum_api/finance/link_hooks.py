"""What a finance unit of work changed that finance links must follow (docs/finance-links.md#corrections).

``sms_service.reverse`` notes the canonical transaction it reversed; reconciliation notes a
statement copy merged into a recorded transaction. ``open_sms_stores`` collects the notes for its
own unit of work and, before it commits, lets ``links.settle`` void, flag or move the links in the
same transaction and under the owner's lock. No imports: every finance module may use it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_pending: ContextVar[list[tuple[str, str, str]] | None] = ContextVar("finance_link_hooks", default=None)


def begin() -> Token[list[tuple[str, str, str]] | None]:
    return _pending.set([])


def end(token: Token[list[tuple[str, str, str]] | None]) -> None:
    _pending.reset(token)


def take() -> list[tuple[str, str, str]]:
    notes = _pending.get()
    if not notes:
        return []
    out = list(notes)
    notes.clear()
    return out


def note_reversed(canonical_id: str | None) -> None:
    notes = _pending.get()
    if notes is not None and canonical_id:
        notes.append(("sync", canonical_id, canonical_id))


def note_moved(from_canonical: str | None, to_canonical: str | None) -> None:
    """A statement copy (``from``) became a source of a recorded transaction (``to``): links move with it."""
    notes = _pending.get()
    if notes is not None and from_canonical and to_canonical and from_canonical != to_canonical:
        notes.append(("move", from_canonical, to_canonical))
        notes.append(("sync", to_canonical, to_canonical))
