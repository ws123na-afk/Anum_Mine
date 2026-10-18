"""Phone calls in "Download my data" and "Delete my data" (docs/privacy.md, docs/calls.md#privacy).

The calls a person logged or placed (with notes, summaries, transcripts and recordings) and
the AI calls they asked for are their personal data: exported and deleted. Contacts are the
workspace's shared address book: the ones they added are exported and kept (counted), like
other shared workspace records. Opt-outs are kept: they protect the person who opted out.
"""

from __future__ import annotations

import logging
from typing import Any

from ..schemas import TenantContext
from .store import CallFilter, open_calls_store

logger = logging.getLogger("anum.calls")
_BATCH = 100


def _all(fetch: Any) -> list[Any]:
    items: list[Any] = []
    while True:
        page, total = fetch(len(items))
        items.extend(page)
        if not page or len(items) >= total:
            return items


def _mine(context: TenantContext) -> tuple[list[Any], list[Any], list[Any]]:
    with open_calls_store(context) as store:
        calls = _all(lambda offset: store.list_calls(CallFilter(user_id=context.user_id), offset=offset, limit=_BATCH))
        contacts = [item for item in _all(lambda offset: store.list_contacts(query=None, favourites=False, offset=offset, limit=_BATCH)) if item.created_by == context.user_id]
        requests = [item for item in _all(lambda offset: store.list_requests(offset=offset, limit=_BATCH)) if item.requested_by == context.user_id]
    return calls, contacts, requests


def calls_privacy_counts(context: TenantContext) -> dict[str, int]:
    calls, _, _ = _mine(context)
    return {"calls_logged": len(calls)}


def calls_export(context: TenantContext) -> dict[str, Any]:
    calls, contacts, requests = _mine(context)
    return {
        "calls_logged": [item.model_dump(mode="json") for item in calls],
        "ai_calls_requested": [item.model_dump(mode="json") for item in requests],
        # Kept on deletion: the workspace's shared address book.
        "contacts_added": [item.model_dump(mode="json") for item in contacts],
    }


def delete_my_calls(context: TenantContext) -> int:
    """Delete the caller's calls and their recordings; returns how many calls went."""
    from ..files import remove_workspace_file

    calls, _, _ = _mine(context)
    with open_calls_store(context) as store:
        for item in calls:
            store.delete_call(item.id)
    for item in calls:
        if item.recording_file_id:
            try:
                remove_workspace_file(context, item.recording_file_id)
            except Exception as exc:  # the call is gone, so the file is unreachable from it
                logger.warning("privacy_call_recording_delete_failed", extra={"anum_error_class": type(exc).__name__})
    return len(calls)
