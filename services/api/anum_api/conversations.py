"""Assistant conversations: ask within a conversation and stream the reply.

A conversation belongs to the user who started it, in one tenant and workspace. With
``ANUM_REPOSITORY_BACKEND=memory`` conversations live in process memory
(``ConversationStore``); with ``postgresql`` they live in ``conversations`` and
``conversation_turns`` under tenant, workspace and user RLS (migration 0018,
``anum_api.db.conversation_repository``). See docs/conversations.md.

Plain conversation is text only: the chat model has no tools. A question that needs
workspace data or an action (mail, WhatsApp, the web, files, "send ...") is turned into a
task and run through the runtime like any task (planner, ToolPolicy, autonomy, approvals);
the outcome streams back as a card on the assistant turn (``anum_api.chat_tools``), so the
runtime's tool mediation and approvals are never bypassed.

``POST /api/v1/conversations/{id}/ask`` answers with server-sent events:

- ``turn``: ``{"user_turn": ConversationTurn}`` once the question is stored;
- ``tool``: ``{"card": ChatCard}`` when the question became a task (running, then settled);
- ``delta``: ``{"text": "..."}`` for each piece of the reply as the model produces it;
- ``done``: ``{"assistant_turn": ConversationTurn, "report": ReportCard | null}`` when the
  reply is complete (``report`` when the answer was a structured analysis, docs/reports.md);
- ``error``: ``{"code", "message", "assistant_turn"}`` when the model failed.

The stream body never reads through the request's repository session (it is closed
once the response starts): each write opens its own unit of work. When the client
disconnects the body is cancelled, the provider stream is closed (which stops
generation) and the partial reply is stored with status ``cancelled``.

Long-term memory (``anum_api.conversation_memory``): each ask recalls the user's
relevant memories into the system prompt, and once a reply is complete a background
pass, run after the whole body has been sent, remembers durable facts the user stated.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import chat_tools
from .authorization import Permission
from .chat_tools import ChatCard, ChatCategory
from .conversation_memory import (
    RECALL_PREFACE,
    CompletedReply,
    extraction_enabled,
    recall_memories,
    remember_after_reply,
)
from .dependencies import has_permission, require_permission, tenant_context
from .localization import Language, localize_message, request_language
from .learning import SignalKind, Surface
from .learning_store import learned_instructions, new_signal, record_server_signal
from .model_budget import ModelBudgetExceededError, budget_exceeded_message, check_model_budget
from .model_gateway import ChatMessage, ModelGateway, ModelRoute, route_gateway
from .prompt_provenance import Provenance, label_untrusted, with_untrusted_rules
from .reports import REPORT_BLOCK_INSTRUCTIONS, ReportCard, cards_for_conversation, save_chat_report, split_report_block
from .schemas import TenantContext, new_id, utc_now
from .scoped_store import open_scoped_store
from .settings import settings
from .sse import SSE_HEADERS, sse
from .tyre.tools import tyre_grounding
from .voice import voice_model_gateway

logger = logging.getLogger("anum.conversations")

MAX_TURNS_PER_CONVERSATION = 400
MAX_CONVERSATIONS_LISTED = 50
MAX_QUESTION_CHARS = 8000
TITLE_CHARS = 80

SYSTEM_PROMPT = (
    "You are Anum, a fast, friendly assistant inside ANUM, a governed agent workbench. "
    "Answer conversationally and get to the point: lead with the answer, keep it short "
    "unless the user asks for depth, and use Markdown lists or code blocks only when they help. "
    "You have no tools in this reply: you cannot approve, delete, pay, send messages or change "
    "permissions. Questions about the user's email, WhatsApp, files or the web, and requests "
    "to act, are run by ANUM as governed tasks and shown as cards in this chat; if the user "
    "wants something done that you cannot see, suggest asking for it directly (for example "
    "\"any urgent customer emails?\") or creating a task, which runs under the workspace's approvals.\n\n"
    + REPORT_BLOCK_INSTRUCTIONS
)


class TurnRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TurnStatus(StrEnum):
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_CHARS)


class ConversationAsk(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=MAX_QUESTION_CHARS)
    # Ask again for an earlier question (the latest user turn) instead of adding a new
    # one: the Retry button after a failed or stopped reply.
    retry_turn_id: str | None = Field(default=None, max_length=80)


class Conversation(BaseModel):
    id: str
    tenant_id: str
    workspace_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationTurn(BaseModel):
    id: str
    conversation_id: str
    role: TurnRole
    content: str
    status: TurnStatus
    created_at: datetime
    # Set when the question was run as a task: its results, approval or failure
    # (docs/conversations.md#tools-in-chat). None for plain conversation.
    card: ChatCard | None = None


class ConversationDetail(Conversation):
    turns: list[ConversationTurn]
    # Report cards for the Assistant's structured answers (docs/reports.md#chat-answer-cards).
    reports: list[ReportCard] = Field(default_factory=list)


class ConversationStoreProtocol(Protocol):
    def create(self, payload: ConversationCreate, context: TenantContext) -> Conversation: ...
    def list(self, context: TenantContext, limit: int) -> list[Conversation]: ...
    def get(self, conversation_id: str, context: TenantContext) -> Conversation | None: ...
    def turns(self, conversation: Conversation, *, last: int | None = None) -> list[ConversationTurn]: ...
    def count_turns(self, conversation: Conversation) -> int: ...
    def add_turn(
        self,
        conversation: Conversation,
        role: TurnRole,
        content: str,
        turn_status: TurnStatus,
        card: ChatCard | None = None,
    ) -> ConversationTurn: ...
    def set_title(self, conversation: Conversation, title: str) -> None: ...
    def delete(self, conversation: Conversation) -> None: ...


def _owned(conversation: Conversation, context: TenantContext) -> bool:
    return (conversation.tenant_id, conversation.workspace_id, conversation.user_id) == (
        context.tenant_id,
        context.workspace_id,
        context.user_id,
    )


class ConversationStore:
    """Thread-safe in-process store for ``ANUM_REPOSITORY_BACKEND=memory``."""

    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.turn_log: dict[str, list[ConversationTurn]] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.conversations.clear()
            self.turn_log.clear()

    def create(self, payload: ConversationCreate, context: TenantContext) -> Conversation:
        now = utc_now()
        conversation = Conversation(
            id=new_id("conv"),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            title=(payload.title or "").strip(),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self.conversations[conversation.id] = conversation
            self.turn_log[conversation.id] = []
        return conversation

    def list(self, context: TenantContext, limit: int) -> list[Conversation]:
        with self._lock:
            owned = [item for item in self.conversations.values() if _owned(item, context)]
        owned.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return [item.model_copy() for item in owned[:limit]]

    def get(self, conversation_id: str, context: TenantContext) -> Conversation | None:
        conversation = self.conversations.get(conversation_id)
        if conversation is None or not _owned(conversation, context):
            return None
        return conversation.model_copy()

    def turns(self, conversation: Conversation, *, last: int | None = None) -> list[ConversationTurn]:
        with self._lock:
            items = list(self.turn_log.get(conversation.id, []))
        return items[-last:] if last else items

    def count_turns(self, conversation: Conversation) -> int:
        with self._lock:
            return len(self.turn_log.get(conversation.id, []))

    def add_turn(
        self,
        conversation: Conversation,
        role: TurnRole,
        content: str,
        turn_status: TurnStatus,
        card: ChatCard | None = None,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            id=new_id("turn"),
            conversation_id=conversation.id,
            role=role,
            content=content,
            status=turn_status,
            created_at=utc_now(),
            card=card.model_copy(deep=True) if card is not None else None,
        )
        with self._lock:
            if conversation.id not in self.conversations:
                raise KeyError(conversation.id)
            self.turn_log[conversation.id].append(turn)
            self.conversations[conversation.id].updated_at = turn.created_at
        return turn

    def set_title(self, conversation: Conversation, title: str) -> None:
        with self._lock:
            stored = self.conversations.get(conversation.id)
            if stored is not None:
                stored.title = title

    def delete(self, conversation: Conversation) -> None:
        with self._lock:
            self.conversations.pop(conversation.id, None)
            self.turn_log.pop(conversation.id, None)


conversation_store = ConversationStore()


@contextmanager
def open_conversation_store(context: TenantContext) -> Iterator[ConversationStoreProtocol]:
    """One unit of work in the caller's tenant, workspace and user scope."""

    def sql_store(session):  # type: ignore[no-untyped-def]
        from .db.conversation_repository import SqlAlchemyConversationStore

        return SqlAlchemyConversationStore(session, context)

    with open_scoped_store(context, conversation_store, sql_store, user_scoped=True) as store:
        yield store


def marketing_blocks(context: TenantContext, question: str) -> list[str]:
    """For "how did last week's Instagram posts do?": the marketing summary tool's numbers,
    as a labelled data block the reply can quote (docs/marketing.md#assistant)."""
    from .marketing.tools import days_in, is_arabic, is_marketing_question, platform_in, summary_for

    if not is_marketing_question(question) or not has_permission(context, Permission.MARKETING_READ):
        return []
    try:
        text = summary_for(context, platform=platform_in(question), days=days_in(question), arabic=is_arabic(question))
    except Exception:
        logger.warning("conversation_marketing_summary_failed", extra={"anum_conversation": {"event": "marketing_summary_failed"}})
        return []
    return [label_untrusted(text, source=Provenance.TOOL_OUTPUT, origin="marketing.summary")]


def title_from(question: str) -> str:
    words = " ".join(question.split())
    return words if len(words) <= TITLE_CHARS else words[: TITLE_CHARS - 1].rstrip() + "…"


def build_messages(
    history: list[ConversationTurn],
    question: str,
    *,
    max_turns: int | None = None,
    max_chars: int | None = None,
    memory_blocks: Sequence[str] = (),
    learned_block: str | None = None,
    data_blocks: Sequence[str] = (),
) -> list[ChatMessage]:
    """The system prompt, the most recent earlier turns that fit, then the question.

    Bounded twice: at most ``max_turns`` earlier turns and at most ``max_chars`` of their
    text, newest kept first. Failed replies carry no text and are left out; consecutive
    turns of one role are joined so the roles alternate as every provider expects.
    Recalled memories (already labeled untrusted and bounded by the recall budget) follow
    the system prompt, after the untrusted-data rules. ``learned_block`` (the user's
    learned style and preferences, docs/learning.md) follows the system prompt itself.
    """
    max_turns = settings.conversation_history_turns if max_turns is None else max_turns
    max_chars = settings.conversation_history_chars if max_chars is None else max_chars
    usable = [turn for turn in history if turn.content.strip()]
    kept: list[ConversationTurn] = []
    used = 0
    for turn in reversed(usable[-max_turns:] if max_turns else []):
        if used + len(turn.content) > max_chars:
            break
        kept.append(turn)
        used += len(turn.content)
    kept.reverse()
    # The model's context starts with a question, never with an orphan reply.
    while kept and kept[0].role != TurnRole.USER:
        kept.pop(0)
    base = f"{SYSTEM_PROMPT}\n\n{learned_block}" if learned_block else SYSTEM_PROMPT
    system = with_untrusted_rules(f"{base}\n\n{RECALL_PREFACE}", *memory_blocks) if memory_blocks else base
    if data_blocks:
        # Read-only tool output (tyre planning): labeled untrusted data, after the rules.
        from .tyre.tools import TYRE_PREFACE

        system = with_untrusted_rules(f"{system}\n\n{TYRE_PREFACE}", *data_blocks)
    messages: list[ChatMessage] = [{"role": "system", "content": system}]
    for role, content in [(turn.role.value, turn.content) for turn in kept] + [("user", question)]:
        if messages[-1]["role"] == role:
            messages[-1] = {"role": role, "content": f"{messages[-1]['content']}\n\n{content}"}
        else:
            messages.append({"role": role, "content": content})
    return messages


async def stream_reply(
    *,
    context: TenantContext,
    conversation: Conversation,
    user_turn: ConversationTurn,
    messages: list[ChatMessage],
    gateway: ModelGateway,
    completed: CompletedReply | None = None,
    memories_used: Sequence[str] = (),
    language: Language = "en",
) -> AsyncIterator[str]:
    """The ``ask`` response body. Writes go through fresh units of work only.

    ``completed`` is filled when the reply is complete and stored, for the memory pass
    that runs after the body.
    """
    yield sse("turn", {"user_turn": user_turn.model_dump(mode="json"), "memories_used": list(memories_used)})
    parts: list[str] = []
    outcome = TurnStatus.FAILED
    error: tuple[str, str] | None = None
    try:
        # A chat reply is a quick reply: the fast model (docs/model-gateway.md#model-routing).
        async for chunk in route_gateway(gateway, ModelRoute.FAST).stream_chat(messages):
            parts.append(chunk)
            yield sse("delta", {"text": chunk})
        outcome = TurnStatus.COMPLETE
    except (asyncio.CancelledError, GeneratorExit):
        # The client went away (or pressed Stop): keep what was said so far.
        outcome = TurnStatus.CANCELLED
        _store_reply(context, conversation, "".join(parts), outcome)
        raise
    except ModelBudgetExceededError as exc:
        # Accept-Language of the request (docs/web-localization.md): the code stays the same.
        error = ("model_budget_exceeded", localize_message(budget_exceeded_message(exc.scope, exc.kind, exc.resets_on), language))
    except Exception as exc:  # reported to the client; only the class name is logged
        logger.warning(
            "conversation_reply_failed error_class=%s",
            type(exc).__name__,
            extra={"anum_conversation": {"event": "reply_failed", "error_class": type(exc).__name__}},
        )
        error = ("model_unavailable", "The model did not answer. Try again in a moment.")
    text = "".join(parts)
    block = None
    if outcome == TurnStatus.COMPLETE:
        # A structured answer ends with an ``anum-report`` block: it becomes a stored
        # report and a card, and is left out of the stored turn (docs/reports.md).
        text, block = split_report_block(text)
    assistant_turn = _store_reply(context, conversation, text, outcome)
    if outcome == TurnStatus.COMPLETE and assistant_turn is not None and completed is not None:
        completed.text = assistant_turn.content
        completed.complete = True
    if outcome == TurnStatus.COMPLETE:
        report = (
            save_chat_report(context, block, conversation_id=conversation.id, turn_id=assistant_turn.id)
            if block is not None and assistant_turn is not None
            else None
        )
        yield sse(
            "done",
            {
                "assistant_turn": assistant_turn.model_dump(mode="json") if assistant_turn else None,
                "report": report.model_dump(mode="json") if report else None,
            },
        )
    else:
        code, message = error or ("model_unavailable", "The model did not answer.")
        yield sse(
            "error",
            {
                "code": code,
                "message": message,
                "assistant_turn": assistant_turn.model_dump(mode="json") if assistant_turn else None,
            },
        )


def _store_reply(
    context: TenantContext,
    conversation: Conversation,
    text: str,
    outcome: TurnStatus,
    card: ChatCard | None = None,
) -> ConversationTurn | None:
    try:
        with open_conversation_store(context) as store:
            # Deleted while the model was answering: nothing to write back into.
            if store.get(conversation.id, context) is None:
                return None
            return store.add_turn(conversation, TurnRole.ASSISTANT, text, outcome, card)
    except Exception as exc:  # the reply was already streamed; never fail the stream here
        logger.warning(
            "conversation_reply_store_failed error_class=%s",
            type(exc).__name__,
            extra={"anum_conversation": {"event": "store_failed", "error_class": type(exc).__name__}},
        )
        return None


async def stream_tool_reply(
    *,
    context: TenantContext,
    conversation: Conversation,
    user_turn: ConversationTurn,
    category: ChatCategory,
    gateway: ModelGateway,
    runtime_config: chat_tools.ChatTaskRuntime,
    learned_block: str | None = None,
) -> AsyncIterator[str]:
    """The ``ask`` body for a question that needs data or an action (docs/conversations.md#tools-in-chat).

    The question becomes a task, stored in its own committed unit of work, then runs in
    another exactly as ``POST /tasks/{id}/run`` runs it. ``tool`` events carry the card
    (running, then settled); a results card from mail, WhatsApp or the web gets a short
    model-written answer streamed as ``delta``s, with the tool output as untrusted data.
    """
    yield sse("turn", {"user_turn": user_turn.model_dump(mode="json"), "memories_used": []})
    card = ChatCard(state="running", category=category)
    parts: list[str] = []
    try:
        outcome: chat_tools.ChatRunOutcome | None = None
        if not has_permission(context, Permission.TASK_RUN):
            card = chat_tools.denied_card(category)
        else:
            task_id: str | None = None
            try:
                task = await asyncio.to_thread(chat_tools.create_task, user_turn.content, context)
                task_id = task.id
                card = ChatCard(state="running", category=category, task_id=task.id, summary="The task is running.")
                yield sse("tool", {"card": card.model_dump(mode="json")})
                outcome = await chat_tools.run_task(task, category, context, gateway, runtime_config)
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:  # said on the card; only the class name is logged
                logger.warning(
                    "conversation_task_failed error_class=%s",
                    type(exc).__name__,
                    extra={"anum_conversation": {"event": "task_failed", "error_class": type(exc).__name__}},
                )
                code, message = chat_tools.describe_run_error(exc, saved=task_id is not None)
                card = chat_tools.failed_card(category, task_id, message, code)
        if outcome is not None:
            card = outcome.card
            if card.state == "running":
                # Queued for the durable runtime: follow it for a while.
                async for fresh in chat_tools.follow_durable(card, context):
                    card = fresh
        yield sse("tool", {"card": card.model_dump(mode="json")})
        if outcome is not None and outcome.call is not None and chat_tools.needs_summary(card, outcome.result):
            messages = chat_tools.summary_messages(
                user_turn.content,
                outcome.call,
                outcome.result,  # type: ignore[arg-type]
                target=outcome.target,
                learned_block=learned_block,
            )
            try:
                async for chunk in route_gateway(gateway, ModelRoute.FAST).stream_chat(messages):
                    parts.append(chunk)
                    yield sse("delta", {"text": chunk})
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception as exc:  # the card already holds the answer
                logger.warning(
                    "conversation_tool_summary_failed error_class=%s",
                    type(exc).__name__,
                    extra={"anum_conversation": {"event": "summary_failed", "error_class": type(exc).__name__}},
                )
    except (asyncio.CancelledError, GeneratorExit):
        # Stopped: a run that had not settled was rolled back; its task stays in Tasks.
        if card.state == "running":
            card = card.model_copy(update={"state": "cancelled", "summary": chat_tools.STOPPED_SUMMARY})
        _store_reply(context, conversation, "".join(parts), TurnStatus.CANCELLED, card)
        raise
    if not "".join(parts).strip():
        text = chat_tools.turn_text(card)
        parts = [text]
        if text:
            yield sse("delta", {"text": text})
    assistant_turn = _store_reply(context, conversation, "".join(parts), TurnStatus.COMPLETE, card)
    yield sse("done", {"assistant_turn": assistant_turn.model_dump(mode="json") if assistant_turn else None})


router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _conversation_or_404(
    store: ConversationStoreProtocol, conversation_id: str, context: TenantContext
) -> Conversation:
    conversation = store.get(conversation_id, context)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.post("", response_model=Conversation, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    context: TenantContext = Depends(tenant_context),
) -> Conversation:
    require_permission(context, Permission.TASK_CREATE)
    with open_conversation_store(context) as store:
        return store.create(payload, context)


@router.get("", response_model=list[Conversation])
async def list_conversations(context: TenantContext = Depends(tenant_context)) -> list[Conversation]:
    require_permission(context, Permission.TASK_READ)
    with open_conversation_store(context) as store:
        return store.list(context, MAX_CONVERSATIONS_LISTED)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    context: TenantContext = Depends(tenant_context),
) -> ConversationDetail:
    require_permission(context, Permission.TASK_READ)
    with open_conversation_store(context) as store:
        conversation = _conversation_or_404(store, conversation_id, context)
        turns = store.turns(conversation)
    # Cards waiting on an approval or a durable run show what happened since.
    for turn in turns:
        if turn.card is not None and turn.card.state in {"running", "approval"}:
            turn.card = chat_tools.refresh_card(turn.card, context)
    return ConversationDetail(
        **conversation.model_dump(), turns=turns, reports=cards_for_conversation(context, conversation.id)
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    context: TenantContext = Depends(tenant_context),
) -> Response:
    require_permission(context, Permission.TASK_CREATE)
    with open_conversation_store(context) as store:
        store.delete(_conversation_or_404(store, conversation_id, context))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/ask")
async def ask_in_conversation(
    conversation_id: str,
    payload: ConversationAsk,
    context: TenantContext = Depends(tenant_context),
    gateway: ModelGateway = Depends(voice_model_gateway),
    language: Language = Depends(request_language),
) -> StreamingResponse:
    """Ask with the conversation's recent turns as context; the reply streams as SSE."""
    require_permission(context, Permission.TASK_CREATE)
    if (payload.content is None) == (payload.retry_turn_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Send either content or retry_turn_id",
        )
    # Refused before anything is stored: 402 with the reset date (main.py's handler).
    check_model_budget(context)
    history_window = settings.conversation_history_turns + 2
    with open_conversation_store(context) as store:
        conversation = _conversation_or_404(store, conversation_id, context)
        if payload.retry_turn_id is not None:
            recent = store.turns(conversation, last=history_window + 1)
            user_turns = [turn for turn in recent if turn.role == TurnRole.USER]
            if not user_turns or user_turns[-1].id != payload.retry_turn_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Only the latest question can be asked again",
                )
            user_turn = user_turns[-1]
            history = recent[: recent.index(user_turn)]
        else:
            if store.count_turns(conversation) + 2 > MAX_TURNS_PER_CONVERSATION:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This conversation is full. Start a new one.",
                )
            history = store.turns(conversation, last=history_window)
            question = (payload.content or "").strip()
            if not question:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Ask a question")
            user_turn = store.add_turn(conversation, TurnRole.USER, question, TurnStatus.COMPLETE)
            if not conversation.title:
                store.set_title(conversation, title_from(question))
    if payload.retry_turn_id is not None:
        # Asking again is a sign the answer missed (docs/learning.md); recorded only while learning is on.
        record_server_signal(context, new_signal(SignalKind.RETRY, Surface.CHAT, subject_id=user_turn.id))
    runtime_config = chat_tools.configured()
    category = (
        chat_tools.chat_intent(user_turn.content, runtime_config.skills, runtime_config.tools.names)
        if runtime_config is not None
        else None
    )
    if runtime_config is not None and category is not None:
        # Data or an action: a governed task, never a tool call from the chat model. No
        # memory pass: the reply quotes mailbox, WhatsApp or web data, not the user.
        return StreamingResponse(
            stream_tool_reply(
                context=context,
                conversation=conversation,
                user_turn=user_turn,
                category=category,
                gateway=gateway,
                runtime_config=runtime_config,
                learned_block=learned_instructions(context, surface=Surface.CHAT.value),
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    recall = await recall_memories(context, user_turn.content)
    # A tyre question is grounded in the workspace's tyre data through a read-only tool,
    # decided by tool policy (anum_api/tyre/tools.py); the model never calls tools here.
    grounding = await tyre_grounding(context, user_turn.content)
    messages = build_messages(
        history,
        user_turn.content,
        memory_blocks=[*recall.blocks, *marketing_blocks(context, user_turn.content)],
        learned_block=learned_instructions(context, surface=Surface.CHAT.value),
        data_blocks=[grounding.block] if grounding else (),
    )
    completed = CompletedReply()
    # Runs after the whole body has been sent: it never delays or fails the stream.
    background = (
        BackgroundTask(
            remember_after_reply,
            context=context,
            conversation_id=conversation.id,
            conversation_title=conversation.title or title_from(user_turn.content),
            question=user_turn.content,
            reply=completed,
            # Fact extraction is a small call: the fast model (Haiku with `anthropic`).
            gateway=route_gateway(gateway, ModelRoute.FAST),
        )
        if extraction_enabled(context)
        else None
    )
    return StreamingResponse(
        stream_reply(
            context=context,
            conversation=conversation,
            user_turn=user_turn,
            messages=messages,
            gateway=gateway,
            completed=completed,
            memories_used=recall.memory_ids,
            language=language,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
        background=background,
    )
