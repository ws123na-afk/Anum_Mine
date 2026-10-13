"""Week 1 pilot checks of a setup plan (docs/setup-plan.md#pilot-checks).

The blueprint holds the test cases (input, expected answer or action, approval gate, pass
criteria). A person runs each case on synthetic test accounts and records the result here:
pass, fail or not run, a note and an evidence reference (a task or approval id, or a link).
Results are per tenant (the pilot tests the organization's setup, whichever workspace it is
recorded from), keep who and when, and every change is an audit record in the same
transaction. ANUM never runs these cases against live data; the parts it can check on its
own governance are API tests (``tests/test_setup_plan_pilot_checks.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit import AuditRecord
from .schemas import TenantContext
from .scoped_store import open_scoped_store


class CheckStatus(StrEnum):
    PASSED = "pass"
    FAILED = "fail"
    NOT_RUN = "not_run"


def _no_control_characters(value: str) -> str:
    value = value.strip()
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ValueError("Remove control characters from the text")
    return value


class CheckResultUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CheckStatus
    note: str = Field(default="", max_length=1000)
    # A task or approval id, or an https link to the evidence.
    evidence: str = Field(default="", max_length=300)

    @field_validator("note", "evidence")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _no_control_characters(value)

    @field_validator("evidence")
    @classmethod
    def _evidence(cls, value: str) -> str:
        if "\n" in value or "\t" in value:
            raise ValueError("Evidence is one task id, approval id or link")
        lowered = value.lower()
        if "://" in lowered and not lowered.startswith("https://"):
            raise ValueError("Evidence links must start with https://")
        return value


class CheckResult(BaseModel):
    plan_id: str
    check_id: str
    status: CheckStatus
    note: str = ""
    evidence: str = ""
    updated_by: str
    updated_at: datetime


class CheckStore(Protocol):
    def list(self, context: TenantContext, plan_id: str) -> list[CheckResult]: ...
    def save(self, context: TenantContext, result: CheckResult) -> CheckResult: ...
    def record_audit(self, record: AuditRecord) -> None: ...


class InMemoryCheckStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], CheckResult] = {}
        self._lock = RLock()

    def clear(self) -> None:
        with self._lock:
            self.rows.clear()

    def list(self, context: TenantContext, plan_id: str) -> list[CheckResult]:
        with self._lock:
            return [r.model_copy() for (tenant, plan, _), r in self.rows.items() if tenant == context.tenant_id and plan == plan_id]

    def save(self, context: TenantContext, result: CheckResult) -> CheckResult:
        with self._lock:
            self.rows[(context.tenant_id, result.plan_id, result.check_id)] = result.model_copy()
        return result

    def record_audit(self, record: AuditRecord) -> None:
        from .dependencies import memory_repository

        memory_repository.record_audit(record)


memory_check_store = InMemoryCheckStore()


class SqlCheckStore:
    def __init__(self, session, context: TenantContext) -> None:  # type: ignore[no-untyped-def]
        self.session = session
        self.context = context

    def list(self, context: TenantContext, plan_id: str) -> list[CheckResult]:
        from sqlalchemy import select

        from .db.models import SetupPlanCheckRecord as R

        rows = self.session.scalars(select(R).where(R.tenant_id == context.tenant_id, R.plan_id == plan_id)).all()
        return [CheckResult(plan_id=r.plan_id, check_id=r.check_id, status=CheckStatus(r.status), note=r.note, evidence=r.evidence,
                            updated_by=r.updated_by, updated_at=r.updated_at) for r in rows]

    def save(self, context: TenantContext, result: CheckResult) -> CheckResult:
        from sqlalchemy.dialects.postgresql import insert

        from .db.models import SetupPlanCheckRecord as R
        from .db.scope import require_tenant

        require_tenant(self.session, context.tenant_id)
        values = {"tenant_id": context.tenant_id, "plan_id": result.plan_id, "check_id": result.check_id, "status": result.status.value,
                  "note": result.note, "evidence": result.evidence, "updated_by": result.updated_by, "updated_at": result.updated_at}
        self.session.execute(insert(R).values(**values).on_conflict_do_update(
            index_elements=["tenant_id", "plan_id", "check_id"],
            set_={key: values[key] for key in ("status", "note", "evidence", "updated_by", "updated_at")},
        ))
        return result

    def record_audit(self, record: AuditRecord) -> None:
        from .db.repository import SqlAlchemyRepository

        SqlAlchemyRepository(self.session, created_by_user_id=self.context.user_id).record_audit(record)


@contextmanager
def open_check_store(context: TenantContext) -> Iterator[CheckStore]:
    with open_scoped_store(context, memory_check_store, lambda session: SqlCheckStore(session, context)) as store:
        yield store
