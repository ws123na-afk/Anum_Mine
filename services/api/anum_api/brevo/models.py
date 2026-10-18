"""Brevo email campaigns: the stored records and the request shapes (docs/brevo.md)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_HTML_CHARS = 512 * 1024
MAX_LISTS = 10


class ConnectionStatus(StrEnum):
    CONNECTED = "connected"
    # Brevo refused the key (401/403): an owner pastes a new one.
    NEEDS_KEY = "needs_key"
    # Brevo could not be reached or answered with an error that is not about the key.
    ERROR = "error"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    # Claimed by the approved send: ANUM is talking to Brevo now.
    SENDING = "sending"
    SCHEDULED = "scheduled"
    SENT = "sent"
    FAILED = "failed"
    # Interrupted while talking to Brevo: it may have gone out. Never retried blindly.
    UNKNOWN = "unknown"


EDITABLE = frozenset({DraftStatus.DRAFT, DraftStatus.PENDING_APPROVAL, DraftStatus.FAILED})
DELETABLE = frozenset({DraftStatus.DRAFT, DraftStatus.PENDING_APPROVAL, DraftStatus.FAILED, DraftStatus.SENT, DraftStatus.UNKNOWN})


class BrevoConnection(BaseModel):
    tenant_id: str
    workspace_id: str
    # Fernet ciphertext of the API key (``secret_box``); never returned or logged.
    api_key_ciphertext: str
    key_last4: str
    status: ConnectionStatus = ConnectionStatus.CONNECTED
    account_email: str = ""
    account_name: str = ""
    company: str = ""
    plan: str = ""
    last_checked_at: datetime | None = None
    last_error: str | None = None
    connected_by: str
    created_at: datetime
    updated_at: datetime


class SendError(BaseModel):
    code: str
    message: str
    fix: str


class CampaignDraft(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    name: str
    subject: str
    preview_text: str = ""
    sender_id: int
    sender_name: str
    sender_email: str
    html: str
    content_hash: str
    list_ids: list[int]
    list_names: list[str] = Field(default_factory=list)
    status: DraftStatus = DraftStatus.DRAFT
    # Requested send time (UTC). None: send as soon as it is approved.
    scheduled_at: datetime | None = None
    approval_id: str | None = None
    approval_task_id: str | None = None
    approved_hash: str | None = None
    approved_by: str | None = None
    # The one Brevo campaign this draft owns (created on the first test or the approved send).
    brevo_campaign_id: int | None = None
    send_attempts: int = 0
    last_error: SendError | None = None
    test_sent_at: datetime | None = None
    sent_at: datetime | None = None
    # "assistant" when an agent wrote it (brevo.draft_campaign): shown as such for review.
    source: str = "person"
    created_by: str
    created_at: datetime
    updated_at: datetime


class ConnectionSave(BaseModel):
    # Brevo v3 API key; accepted here only, never returned.
    api_key: str = Field(min_length=8, max_length=300, repr=False)


class DraftInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=200)
    preview_text: str = Field(default="", max_length=200)
    sender_id: int = Field(ge=1)
    html: str = Field(min_length=1, max_length=MAX_HTML_CHARS)
    list_ids: list[int] = Field(min_length=1, max_length=MAX_LISTS)

    @field_validator("name", "subject", "preview_text")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("list_ids")
    @classmethod
    def _positive_unique(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("list ids are positive numbers")
        return sorted(set(value))


class SendRequest(BaseModel):
    # None: send as soon as an owner approves. Otherwise Brevo sends at this time.
    scheduled_at: datetime | None = None


class Page(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int
