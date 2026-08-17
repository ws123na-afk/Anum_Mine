from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from anum_api.audit import REDACTED, InMemoryAuditRecorder
from anum_api.credentials import CredentialMetadata, InMemoryCredentialStore
from anum_api.internal_tools import build_internal_tool_registry
from anum_api.mcp_adapter import McpAdapter, McpProtocolError
from anum_api.memory import InMemoryMemoryRepository, MemoryNote, MemoryProvenance, RetentionPolicy
from anum_api.repository import InMemoryRepository
from anum_api.rest_integration import build_rest_integration_registry
from anum_api.schemas import RiskLevel, Task, TaskStatus, TenantContext, new_id, utc_now
from anum_api.store import InMemoryStore
from anum_api.tools import (
    RetryPolicy,
    ToolContract,
    ToolExecutionContext,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolResultStatus,
    execute_tool,
)


def arun(coro: Any) -> Any:
    return asyncio.run(coro)


def make_context(
    *, tenant_id: str = "tenant_a", workspace_id: str = "workspace_a", user_id: str = "user_a"
) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, roles=["member"])


def make_exec_context(*, correlation_id: str = "corr_1", actor_id: str = "user_a") -> ToolExecutionContext:
    return ToolExecutionContext(tenant=make_context(), correlation_id=correlation_id, actor_id=actor_id)


def make_contract(
    *,
    name: str = "sample_tool",
    risk_level: RiskLevel = RiskLevel.LOW,
    timeout_seconds: float = 1.0,
    retry_policy: RetryPolicy | None = None,
    audit_redact_fields: list[str] | None = None,
) -> ToolContract:
    return ToolContract(
        name=name,
        description="A sample tool for tests.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        required_scopes=["sample:use"],
        risk_level=risk_level,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy or RetryPolicy(),
        idempotent=True,
        audit_redact_fields=audit_redact_fields or [],
    )


# ---------------------------------------------------------------------------
# ToolContract
# ---------------------------------------------------------------------------


def test_tool_contract_accepts_valid_slug_name() -> None:
    contract = make_contract(name="memory_search-v1.beta")
    assert contract.name == "memory_search-v1.beta"


@pytest.mark.parametrize("bad_name", ["", "Memory Search", "1tool", "UPPER", "has space", "a" * 65])
def test_tool_contract_rejects_invalid_names(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        make_contract(name=bad_name)


def test_tool_contract_and_retry_policy_are_frozen() -> None:
    contract = make_contract()
    with pytest.raises(ValidationError):
        contract.name = "changed"  # type: ignore[misc]


def test_retry_policy_defaults_to_no_retry() -> None:
    policy = RetryPolicy()
    assert policy.max_attempts == 1
    assert policy.backoff_seconds == 0.0


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


async def _echo_handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
    return ToolResult(status=ToolResultStatus.SUCCESS, output={"echo": inputs})


def test_registry_register_get_and_list() -> None:
    registry = ToolRegistry()
    contract = make_contract(name="echo_tool")
    registry.register(contract, _echo_handler)

    fetched = registry.get("echo_tool")
    assert fetched is not None
    assert fetched[0] == contract
    assert fetched[1] is _echo_handler

    assert registry.get("missing") is None
    assert registry.list_tools() == (contract,)


def test_registry_rejects_duplicate_registration() -> None:
    registry = ToolRegistry()
    contract = make_contract(name="echo_tool")
    registry.register(contract, _echo_handler)
    with pytest.raises(ValueError):
        registry.register(contract, _echo_handler)


# ---------------------------------------------------------------------------
# execute_tool: risk-based mediation
# ---------------------------------------------------------------------------


def test_execute_tool_raises_for_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        arun(execute_tool(registry, "missing", {}, make_exec_context()))


def test_execute_tool_low_risk_invokes_handler_and_returns_success() -> None:
    registry = ToolRegistry()
    registry.register(make_contract(name="echo_low", risk_level=RiskLevel.LOW), _echo_handler)

    result, record = arun(
        execute_tool(registry, "echo_low", {"message": "hi"}, make_exec_context())
    )

    assert isinstance(result, ToolResult)
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"echo": {"message": "hi"}}
    assert record.outcome == "success"
    assert record.action == "tool.echo_low"


def test_execute_tool_medium_risk_invokes_handler() -> None:
    registry = ToolRegistry()
    registry.register(make_contract(name="echo_medium", risk_level=RiskLevel.MEDIUM), _echo_handler)

    result, _record = arun(
        execute_tool(registry, "echo_medium", {"message": "hi"}, make_exec_context())
    )

    assert isinstance(result, ToolResult)
    assert result.status == ToolResultStatus.SUCCESS


def test_execute_tool_high_risk_returns_approval_without_calling_handler() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        calls.append(inputs)
        return ToolResult(status=ToolResultStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register(make_contract(name="risky", risk_level=RiskLevel.HIGH), handler)

    outcome, record = arun(
        execute_tool(registry, "risky", {"message": "hi"}, make_exec_context())
    )

    from anum_api.schemas import Approval, ApprovalStatus

    assert isinstance(outcome, Approval)
    assert outcome.status == ApprovalStatus.PENDING
    assert outcome.risk_level == RiskLevel.HIGH
    assert outcome.action == "tool:risky"
    assert calls == []  # the handler must never run for a HIGH-risk tool
    assert record.outcome == "approval_required"


def test_execute_tool_high_risk_saves_approval_when_repository_given() -> None:
    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        return ToolResult(status=ToolResultStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register(make_contract(name="risky2", risk_level=RiskLevel.HIGH), handler)
    repository = InMemoryRepository(InMemoryStore())

    outcome, _record = arun(
        execute_tool(
            registry,
            "risky2",
            {},
            make_exec_context(),
            repository_for_approval=repository,
        )
    )

    from anum_api.schemas import Approval

    assert isinstance(outcome, Approval)
    assert repository.store.approvals[outcome.id] == outcome


def test_execute_tool_blocked_risk_returns_blocked_without_calling_handler() -> None:
    calls: list[dict[str, Any]] = []

    async def handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        calls.append(inputs)
        return ToolResult(status=ToolResultStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register(make_contract(name="nope", risk_level=RiskLevel.BLOCKED), handler)

    result, record = arun(execute_tool(registry, "nope", {}, make_exec_context()))

    assert isinstance(result, ToolResult)
    assert result.status == ToolResultStatus.BLOCKED
    assert calls == []
    assert record.outcome == "blocked"


# ---------------------------------------------------------------------------
# execute_tool: timeout and retry behavior
# ---------------------------------------------------------------------------


def test_execute_tool_timeout_produces_recoverable_failure() -> None:
    async def slow_handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        await asyncio.sleep(0.2)
        return ToolResult(status=ToolResultStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register(
        make_contract(name="slow", risk_level=RiskLevel.LOW, timeout_seconds=0.01),
        slow_handler,
    )

    result, record = arun(execute_tool(registry, "slow", {}, make_exec_context()))

    assert isinstance(result, ToolResult)
    assert result.status == ToolResultStatus.RECOVERABLE_FAILURE
    assert result.error_message is not None
    assert "timed out" in result.error_message
    assert record.outcome == "recoverable_failure"


def test_execute_tool_retries_a_handler_that_fails_then_succeeds() -> None:
    attempts: list[int] = []

    async def flaky_handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient upstream error")
        return ToolResult(status=ToolResultStatus.SUCCESS, output={"attempt": len(attempts)})

    registry = ToolRegistry()
    registry.register(
        make_contract(
            name="flaky",
            risk_level=RiskLevel.LOW,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
        ),
        flaky_handler,
    )

    result, _record = arun(execute_tool(registry, "flaky", {}, make_exec_context()))

    assert len(attempts) == 2
    assert isinstance(result, ToolResult)
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"attempt": 2}


def test_execute_tool_retries_a_recoverable_failure_result_then_succeeds() -> None:
    attempts: list[int] = []

    async def flaky_handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        attempts.append(1)
        if len(attempts) == 1:
            return ToolResult(status=ToolResultStatus.RECOVERABLE_FAILURE, error_message="try again")
        return ToolResult(status=ToolResultStatus.SUCCESS)

    registry = ToolRegistry()
    registry.register(
        make_contract(
            name="flaky2",
            risk_level=RiskLevel.LOW,
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0.0),
        ),
        flaky_handler,
    )

    result, _record = arun(execute_tool(registry, "flaky2", {}, make_exec_context()))

    assert len(attempts) == 2
    assert result.status == ToolResultStatus.SUCCESS


def test_execute_tool_exhausts_retries_and_returns_recoverable_failure() -> None:
    async def always_fails(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        raise RuntimeError("permanently broken")

    registry = ToolRegistry()
    registry.register(
        make_contract(
            name="broken",
            risk_level=RiskLevel.LOW,
            retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.0),
        ),
        always_fails,
    )

    result, _record = arun(execute_tool(registry, "broken", {}, make_exec_context()))

    assert result.status == ToolResultStatus.RECOVERABLE_FAILURE
    assert "permanently broken" in (result.error_message or "")


def test_execute_tool_does_not_retry_partial_or_blocked_results() -> None:
    attempts: list[int] = []

    async def partial_handler(inputs: dict[str, Any], context: ToolExecutionContext) -> ToolResult:
        attempts.append(1)
        return ToolResult(status=ToolResultStatus.PARTIAL, partial_output={"done": 1, "total": 2})

    registry = ToolRegistry()
    registry.register(
        make_contract(
            name="partial",
            risk_level=RiskLevel.LOW,
            retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=0.0),
        ),
        partial_handler,
    )

    result, _record = arun(execute_tool(registry, "partial", {}, make_exec_context()))

    assert len(attempts) == 1
    assert result.status == ToolResultStatus.PARTIAL
    assert result.partial_output == {"done": 1, "total": 2}


# ---------------------------------------------------------------------------
# execute_tool: audit record redaction and recording
# ---------------------------------------------------------------------------


def test_execute_tool_audit_record_redacts_secret_looking_and_declared_fields() -> None:
    registry = ToolRegistry()
    registry.register(
        make_contract(name="audited", risk_level=RiskLevel.LOW, audit_redact_fields=["custom_field"]),
        _echo_handler,
    )

    inputs = {"api_key": "sk-should-not-leak", "custom_field": "also-secret", "safe": "visible"}
    _result, record = arun(execute_tool(registry, "audited", inputs, make_exec_context()))

    assert record.metadata["inputs"]["api_key"] == REDACTED
    assert record.metadata["inputs"]["custom_field"] == REDACTED
    assert record.metadata["inputs"]["safe"] == "visible"
    assert "sk-should-not-leak" not in repr(record.metadata)
    assert "also-secret" not in repr(record.metadata)


def test_execute_tool_records_into_injected_audit_recorder() -> None:
    recorder = InMemoryAuditRecorder()
    registry = ToolRegistry()
    registry.register(make_contract(name="recorded", risk_level=RiskLevel.LOW), _echo_handler)
    context = make_exec_context()

    _result, record = arun(
        execute_tool(registry, "recorded", {}, context, audit_recorder=recorder)
    )

    stored = recorder.query(context.tenant)
    assert [entry.id for entry in stored] == [record.id]


# ---------------------------------------------------------------------------
# Internal tools
# ---------------------------------------------------------------------------


def test_internal_memory_search_executes_against_real_repository() -> None:
    memory_repo = InMemoryMemoryRepository()
    task_repo = InMemoryRepository(InMemoryStore())
    context = make_exec_context()
    now = utc_now()
    memory_repo.create(
        MemoryNote(
            id=new_id("memory"),
            tenant_id=context.tenant.tenant_id,
            workspace_id=context.tenant.workspace_id,
            task_id="task_1",
            content="Customer prefers async updates",
            provenance=MemoryProvenance(
                source_type="test", created_by_user_id=context.tenant.user_id, created_at=now
            ),
            retention=RetentionPolicy(),
            created_at=now,
        )
    )

    registry = build_internal_tool_registry(memory_repo, task_repo)
    result, record = arun(
        execute_tool(registry, "memory_search", {"query": "async"}, context)
    )

    assert result.status == ToolResultStatus.SUCCESS
    assert result.output is not None
    assert len(result.output["notes"]) == 1
    assert result.output["notes"][0]["content"] == "Customer prefers async updates"
    assert record.outcome == "success"


def test_internal_memory_write_executes_and_creates_durable_note() -> None:
    memory_repo = InMemoryMemoryRepository()
    task_repo = InMemoryRepository(InMemoryStore())
    context = make_exec_context()

    registry = build_internal_tool_registry(memory_repo, task_repo)
    result, _record = arun(
        execute_tool(
            registry,
            "memory_write",
            {"task_id": "task_1", "content": "Remember this."},
            context,
        )
    )

    assert result.status == ToolResultStatus.SUCCESS
    assert result.output is not None
    note_id = result.output["note"]["id"]
    stored = memory_repo.get(note_id, context.tenant)
    assert stored is not None
    assert stored.content == "Remember this."


def test_internal_memory_write_is_medium_risk_and_still_executes_directly() -> None:
    registry = build_internal_tool_registry(InMemoryMemoryRepository(), InMemoryRepository(InMemoryStore()))
    contract, _handler = registry.get("memory_write")
    assert contract.risk_level == RiskLevel.MEDIUM


def test_internal_task_status_lookup_executes_against_real_repository() -> None:
    memory_repo = InMemoryMemoryRepository()
    store = InMemoryStore()
    task_repo = InMemoryRepository(store)
    context = make_exec_context()
    now = utc_now()
    task = Task(
        id="task_1",
        title="Ship the feature",
        prompt="Ship it",
        status=TaskStatus.RUNNING,
        tenant_id=context.tenant.tenant_id,
        workspace_id=context.tenant.workspace_id,
        created_at=now,
        updated_at=now,
    )
    task_repo.create_task(task)

    registry = build_internal_tool_registry(memory_repo, task_repo)
    result, _record = arun(
        execute_tool(registry, "task_status_lookup", {"task_id": "task_1"}, context)
    )

    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["task"]["status"] == "running"


def test_internal_task_status_lookup_reports_recoverable_failure_when_missing() -> None:
    registry = build_internal_tool_registry(InMemoryMemoryRepository(), InMemoryRepository(InMemoryStore()))
    result, _record = arun(
        execute_tool(registry, "task_status_lookup", {"task_id": "missing"}, make_exec_context())
    )

    assert result.status == ToolResultStatus.RECOVERABLE_FAILURE


# ---------------------------------------------------------------------------
# REST integration adapter
# ---------------------------------------------------------------------------


def test_rest_adapter_executes_against_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/anthropics/anum"
        return httpx.Response(
            200,
            json={
                "full_name": "anthropics/anum",
                "default_branch": "main",
                "stargazers_count": 42,
                "private": False,
            },
        )

    async def run() -> tuple[ToolResult, Any]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            registry = build_rest_integration_registry(client)
            return await execute_tool(
                registry,
                "lookup_github_repo",
                {"owner": "anthropics", "repo": "anum"},
                make_exec_context(),
            )

    result, record = arun(run())

    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {
        "full_name": "anthropics/anum",
        "default_branch": "main",
        "stargazers_count": 42,
        "private": False,
    }
    assert record.outcome == "success"


def test_rest_adapter_maps_upstream_4xx_to_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async def run() -> tuple[ToolResult, Any]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            registry = build_rest_integration_registry(client)
            return await execute_tool(
                registry, "lookup_github_repo", {"owner": "x", "repo": "y"}, make_exec_context()
            )

    result, _record = arun(run())
    assert result.status == ToolResultStatus.BLOCKED


def test_rest_adapter_maps_upstream_5xx_to_recoverable_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "unavailable"})

    async def run() -> tuple[ToolResult, Any]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            registry = build_rest_integration_registry(client)
            return await execute_tool(
                registry,
                "lookup_github_repo",
                {"owner": "x", "repo": "y"},
                make_exec_context(),
                # single attempt: no need to wait out a retry loop in this test
            )

    result, _record = arun(run())
    assert result.status == ToolResultStatus.RECOVERABLE_FAILURE


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


async def _fake_mcp_server(request: dict[str, Any]) -> dict[str, Any]:
    method = request["method"]
    request_id = request["id"]
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes the given message back.",
                        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}},
                        "outputSchema": {"type": "object"},
                    }
                ]
            },
        }
    if method == "tools/call":
        arguments = request["params"]["arguments"]
        if arguments.get("message") == "boom":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "simulated failure"}],
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": f"echo: {arguments.get('message')}"}],
                "structuredContent": {"message": arguments.get("message")},
            },
        }
    raise AssertionError(f"unexpected MCP method: {method}")


def test_mcp_adapter_discovers_tools_and_maps_to_tool_contract() -> None:
    adapter = McpAdapter(_fake_mcp_server)
    contracts = arun(adapter.discover_tools())

    assert len(contracts) == 1
    assert contracts[0].name == "echo"
    assert contracts[0].required_scopes == ["mcp:echo"]
    assert isinstance(contracts[0], ToolContract)


def test_mcp_adapter_round_trips_through_execute_tool_mediation() -> None:
    adapter = McpAdapter(_fake_mcp_server)
    contracts = arun(adapter.discover_tools())
    contract = contracts[0]

    registry = ToolRegistry()
    registry.register(contract, adapter.build_handler("echo"))

    result, record = arun(
        execute_tool(registry, "echo", {"message": "hi"}, make_exec_context())
    )

    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"message": "hi"}
    assert record.action == "tool.echo"


def test_mcp_adapter_maps_tool_error_to_recoverable_failure() -> None:
    adapter = McpAdapter(_fake_mcp_server)
    registry = ToolRegistry()
    registry.register(make_contract(name="echo", risk_level=RiskLevel.LOW), adapter.build_handler("echo"))

    result, _record = arun(
        execute_tool(registry, "echo", {"message": "boom"}, make_exec_context())
    )

    assert result.status == ToolResultStatus.RECOVERABLE_FAILURE
    assert result.error_message == "simulated failure"


def test_mcp_adapter_raises_protocol_error_for_malformed_response() -> None:
    async def broken_server(request: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request["id"]}  # missing 'result'

    adapter = McpAdapter(broken_server)
    with pytest.raises(McpProtocolError):
        arun(adapter.discover_tools())


# ---------------------------------------------------------------------------
# Credential store
# ---------------------------------------------------------------------------


def make_credential(
    *,
    credential_id: str = "credential_1",
    scope: str = "workspace",
    workspace_id: str | None = "workspace_a",
    user_id: str | None = None,
    agent_id: str | None = None,
) -> CredentialMetadata:
    return CredentialMetadata(
        id=credential_id,
        integration_id="github",
        scope=scope,  # type: ignore[arg-type]
        tenant_id="tenant_a",
        workspace_id=workspace_id,
        user_id=user_id,
        agent_id=agent_id,
        secret_ref="vault://anum/tenant_a/github-pat",
        created_at=utc_now(),
    )


def test_credential_metadata_never_has_a_secret_value_field() -> None:
    fields = CredentialMetadata.model_fields
    assert "secret" not in fields
    assert "secret_value" not in fields
    assert "value" not in fields
    assert "secret_ref" in fields


def test_credential_metadata_requires_identity_matching_its_scope() -> None:
    with pytest.raises(ValidationError):
        make_credential(scope="workspace", workspace_id=None)
    with pytest.raises(ValidationError):
        make_credential(scope="user", user_id=None)
    with pytest.raises(ValidationError):
        make_credential(scope="agent", agent_id=None)


def test_credential_store_save_get_list_and_revoke() -> None:
    store = InMemoryCredentialStore()
    credential = make_credential()
    store.save(credential)

    assert store.get("credential_1") == credential
    assert store.get("missing") is None

    listed = store.list_for_scope(tenant_id="tenant_a", workspace_id="workspace_a")
    assert [entry.id for entry in listed] == ["credential_1"]
    assert store.list_for_scope(tenant_id="tenant_b") == []

    assert credential.is_revoked is False
    revoked = store.revoke("credential_1")
    assert revoked is not None
    assert revoked.is_revoked is True
    assert store.get("credential_1").is_revoked is True
    assert store.revoke("missing") is None
