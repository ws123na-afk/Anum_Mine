"""PostgreSQL store for the autonomy policy (migration ``0031_autonomy``, docs/autonomy.md).

Bound to the caller's session, whose RLS context names the tenant and workspace; every
statement repeats both, so another workspace's rows are neither read nor written even if
a policy were missing. Guardrails are tenant-level (one row per tenant).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from anum_api.autonomy import (
    ActionFilter,
    AutonomousAction,
    AutonomyProposal,
    AutonomyRule,
    NO_EFFECT_KINDS,
    OrganizationGuardrails,
    ProposalStatus,
    UnknownActionCursorError,
    WorkspaceAutonomySettings,
)
from anum_api.schemas import TenantContext, utc_now

from .models import (
    AutonomousActionRecord,
    AutonomyGuardrailsRecord,
    AutonomyProposalRecord,
    AutonomyRecipientRecord,
    AutonomyRuleRecord,
    WorkspaceAutonomyRecord,
)
from .scope import require_tenant, require_workspace

_RULE_FIELDS = ("id", "pattern_key", "tool", "template", "category", "action_kind", "level", "source", "created_by", "created_at")
_PROPOSAL_FIELDS = (
    "id", "pattern_key", "tool", "template", "category", "action_kind", "proposed_level", "approvals",
    "status", "last_approved_at", "created_at", "decided_by", "decided_at",
)
_ACTION_FIELDS = (
    "id", "task_id", "run_id", "tool", "category", "action_kind", "level", "pattern_key", "template", "summary",
    "target", "status", "requested_by", "undo_tool", "undo_arguments", "undo_token", "undo_expires_at",
    "undone_by", "undone_at", "undo_error", "created_at",
)


def _settings_payload(model: OrganizationGuardrails | WorkspaceAutonomySettings) -> dict:
    return model.model_dump(mode="json", exclude={"updated_by", "updated_at"})


class SqlAlchemyAutonomyStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- guardrails and settings ----------------------------------------------------------

    def get_guardrails(self, context: TenantContext) -> OrganizationGuardrails | None:
        record = self.session.get(AutonomyGuardrailsRecord, context.tenant_id)
        if record is None or record.tenant_id != context.tenant_id:
            return None
        return OrganizationGuardrails.model_validate(
            {**record.settings, "updated_by": record.updated_by, "updated_at": record.updated_at}
        )

    def save_guardrails(self, context: TenantContext, guardrails: OrganizationGuardrails) -> OrganizationGuardrails:
        require_tenant(self.session, context.tenant_id)
        record = self.session.scalar(
            select(AutonomyGuardrailsRecord)
            .where(AutonomyGuardrailsRecord.tenant_id == context.tenant_id)
            .with_for_update()
        )
        if record is None:
            record = AutonomyGuardrailsRecord(tenant_id=context.tenant_id)
            self.session.add(record)
        record.settings = _settings_payload(guardrails)
        record.updated_by = guardrails.updated_by or context.user_id
        record.updated_at = guardrails.updated_at or utc_now()
        self.session.flush()
        return self.get_guardrails(context) or guardrails

    def get_settings(self, context: TenantContext) -> WorkspaceAutonomySettings | None:
        record = self.session.get(WorkspaceAutonomyRecord, (context.tenant_id, context.workspace_id))
        if record is None:
            return None
        return WorkspaceAutonomySettings.model_validate(
            {**record.settings, "updated_by": record.updated_by, "updated_at": record.updated_at}
        )

    def save_settings(self, context: TenantContext, settings: WorkspaceAutonomySettings) -> WorkspaceAutonomySettings:
        require_workspace(self.session, context.tenant_id, context.workspace_id)
        record = self.session.scalar(
            select(WorkspaceAutonomyRecord)
            .where(
                WorkspaceAutonomyRecord.tenant_id == context.tenant_id,
                WorkspaceAutonomyRecord.workspace_id == context.workspace_id,
            )
            .with_for_update()
        )
        if record is None:
            record = WorkspaceAutonomyRecord(tenant_id=context.tenant_id, workspace_id=context.workspace_id)
            self.session.add(record)
        record.settings = _settings_payload(settings)
        record.updated_by = settings.updated_by or context.user_id
        record.updated_at = settings.updated_at or utc_now()
        self.session.flush()
        return self.get_settings(context) or settings

    # -- rules ----------------------------------------------------------------------------

    def _scope(self, model, context: TenantContext):  # type: ignore[no-untyped-def]
        return (model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id)

    @staticmethod
    def _rule(record: AutonomyRuleRecord) -> AutonomyRule:
        return AutonomyRule(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            **{name: getattr(record, name) for name in _RULE_FIELDS},
        )

    def list_rules(self, context: TenantContext) -> list[AutonomyRule]:
        records = self.session.scalars(
            select(AutonomyRuleRecord)
            .where(*self._scope(AutonomyRuleRecord, context))
            .order_by(AutonomyRuleRecord.created_at, AutonomyRuleRecord.id)
        ).all()
        return [self._rule(record) for record in records]

    def get_rule(self, rule_id: str, context: TenantContext) -> AutonomyRule | None:
        record = self.session.scalar(
            select(AutonomyRuleRecord).where(*self._scope(AutonomyRuleRecord, context), AutonomyRuleRecord.id == rule_id)
        )
        return self._rule(record) if record is not None else None

    def save_rule(self, rule: AutonomyRule) -> AutonomyRule:
        require_workspace(self.session, rule.tenant_id, rule.workspace_id)
        # One rule per pattern: a new level for the same pattern replaces the old rule.
        self.session.execute(
            delete(AutonomyRuleRecord).where(
                AutonomyRuleRecord.tenant_id == rule.tenant_id,
                AutonomyRuleRecord.workspace_id == rule.workspace_id,
                AutonomyRuleRecord.pattern_key == rule.pattern_key,
                AutonomyRuleRecord.id != rule.id,
            )
        )
        record = self.session.get(AutonomyRuleRecord, (rule.tenant_id, rule.workspace_id, rule.id))
        values = rule.model_dump(mode="python")
        if record is None:
            record = AutonomyRuleRecord(tenant_id=rule.tenant_id, workspace_id=rule.workspace_id, id=rule.id)
            self.session.add(record)
        for name in _RULE_FIELDS:
            setattr(record, name, getattr(values[name], "value", values[name]))
        self.session.flush()
        return rule

    def delete_rule(self, rule_id: str, context: TenantContext) -> bool:
        result = self.session.execute(
            delete(AutonomyRuleRecord).where(*self._scope(AutonomyRuleRecord, context), AutonomyRuleRecord.id == rule_id)
        )
        return bool(result.rowcount)

    # -- proposals ------------------------------------------------------------------------

    @staticmethod
    def _proposal(record: AutonomyProposalRecord) -> AutonomyProposal:
        return AutonomyProposal(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            **{name: getattr(record, name) for name in _PROPOSAL_FIELDS},
        )

    def list_proposals(self, context: TenantContext, status: ProposalStatus | None = None) -> list[AutonomyProposal]:
        query = select(AutonomyProposalRecord).where(*self._scope(AutonomyProposalRecord, context))
        if status is not None:
            query = query.where(AutonomyProposalRecord.status == status.value)
        records = self.session.scalars(
            query.order_by(AutonomyProposalRecord.created_at.desc(), AutonomyProposalRecord.id.desc())
        ).all()
        return [self._proposal(record) for record in records]

    def get_proposal(self, proposal_id: str, context: TenantContext) -> AutonomyProposal | None:
        record = self.session.scalar(
            select(AutonomyProposalRecord)
            .where(*self._scope(AutonomyProposalRecord, context), AutonomyProposalRecord.id == proposal_id)
            .with_for_update()
        )
        return self._proposal(record) if record is not None else None

    def find_proposal(self, key: str, context: TenantContext) -> AutonomyProposal | None:
        record = self.session.scalar(
            select(AutonomyProposalRecord)
            .where(*self._scope(AutonomyProposalRecord, context), AutonomyProposalRecord.pattern_key == key)
            .with_for_update()
        )
        return self._proposal(record) if record is not None else None

    def save_proposal(self, proposal: AutonomyProposal) -> AutonomyProposal:
        require_workspace(self.session, proposal.tenant_id, proposal.workspace_id)
        record = self.session.get(AutonomyProposalRecord, (proposal.tenant_id, proposal.workspace_id, proposal.id))
        values = proposal.model_dump(mode="python")
        if record is None:
            record = AutonomyProposalRecord(tenant_id=proposal.tenant_id, workspace_id=proposal.workspace_id, id=proposal.id)
            self.session.add(record)
        for name in _PROPOSAL_FIELDS:
            setattr(record, name, getattr(values[name], "value", values[name]))
        self.session.flush()
        return proposal

    # -- activity -------------------------------------------------------------------------

    @staticmethod
    def _action(record: AutonomousActionRecord) -> AutonomousAction:
        return AutonomousAction(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            **{name: getattr(record, name) for name in _ACTION_FIELDS},
        )

    def add_action(self, action: AutonomousAction) -> AutonomousAction:
        require_workspace(self.session, action.tenant_id, action.workspace_id)
        record = AutonomousActionRecord(tenant_id=action.tenant_id, workspace_id=action.workspace_id, id=action.id)
        self._apply(record, action)
        self.session.add(record)
        self.session.flush()
        return action

    def _apply(self, record: AutonomousActionRecord, action: AutonomousAction) -> None:
        values = action.model_dump(mode="python")
        for name in _ACTION_FIELDS:
            setattr(record, name, getattr(values[name], "value", values[name]))

    def get_action(self, action_id: str, context: TenantContext, *, for_update: bool = False) -> AutonomousAction | None:
        query = select(AutonomousActionRecord).where(
            *self._scope(AutonomousActionRecord, context), AutonomousActionRecord.id == action_id
        )
        if for_update:
            query = query.with_for_update()
        record = self.session.scalar(query)
        return self._action(record) if record is not None else None

    def save_action(self, action: AutonomousAction) -> AutonomousAction:
        record = self.session.get(AutonomousActionRecord, (action.tenant_id, action.workspace_id, action.id))
        if record is None:
            return self.add_action(action)
        self._apply(record, action)
        self.session.flush()
        return action

    def list_actions(
        self, context: TenantContext, filters: ActionFilter, *, limit: int, after: str | None = None
    ) -> list[AutonomousAction]:
        query = select(AutonomousActionRecord).where(*self._scope(AutonomousActionRecord, context))
        if after:
            anchor = self.session.execute(
                select(AutonomousActionRecord.created_at, AutonomousActionRecord.id).where(
                    *self._scope(AutonomousActionRecord, context), AutonomousActionRecord.id == after
                )
            ).first()
            if anchor is None:
                raise UnknownActionCursorError(after)
            query = query.where(
                tuple_(AutonomousActionRecord.created_at, AutonomousActionRecord.id) < tuple_(anchor[0], anchor[1])
            )
        if filters.category is not None:
            query = query.where(AutonomousActionRecord.category == filters.category.value)
        if filters.level is not None:
            query = query.where(AutonomousActionRecord.level == filters.level.value)
        if filters.status is not None:
            query = query.where(AutonomousActionRecord.status == filters.status.value)
        if filters.query:
            needle = "%" + filters.query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(
                or_(
                    func.lower(AutonomousActionRecord.summary).like(needle, escape="\\"),
                    func.lower(AutonomousActionRecord.tool).like(needle, escape="\\"),
                    func.lower(func.coalesce(AutonomousActionRecord.template, "")).like(needle, escape="\\"),
                )
            )
        records = self.session.scalars(
            query.order_by(AutonomousActionRecord.created_at.desc(), AutonomousActionRecord.id.desc()).limit(limit)
        ).all()
        return [self._action(record) for record in records]

    def count_actions_since(self, context: TenantContext, since: datetime, *, category: str | None, tool: str | None) -> int:
        query = select(func.count()).select_from(AutonomousActionRecord).where(
            *self._scope(AutonomousActionRecord, context),
            AutonomousActionRecord.created_at >= since,
            AutonomousActionRecord.action_kind.not_in([kind.value for kind in NO_EFFECT_KINDS]),
        )
        if category is not None:
            query = query.where(AutonomousActionRecord.category == category)
        if tool is not None:
            query = query.where(AutonomousActionRecord.tool == tool)
        return int(self.session.scalar(query) or 0)

    # -- known recipients -----------------------------------------------------------------

    def known_recipients(self, context: TenantContext, category: str, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        rows = self.session.scalars(
            select(AutonomyRecipientRecord.recipient_hash).where(
                *self._scope(AutonomyRecipientRecord, context),
                AutonomyRecipientRecord.category == category,
                AutonomyRecipientRecord.recipient_hash.in_(hashes),
            )
        ).all()
        return set(rows)

    def remember_recipients(self, context: TenantContext, category: str, hashes: list[str], now: datetime) -> None:
        if not hashes:
            return
        statement = insert(AutonomyRecipientRecord).values(
            [
                {
                    "tenant_id": context.tenant_id,
                    "workspace_id": context.workspace_id,
                    "category": category,
                    "recipient_hash": value,
                    "first_seen_at": now,
                }
                for value in sorted(set(hashes))
            ]
        )
        self.session.execute(statement.on_conflict_do_nothing())
