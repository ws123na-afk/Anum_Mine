"""Health probes for routing targets (docs/governance-and-scale.md#routing-targets).

A routing target names a provider and a model, not credentials, so a probe borrows the model
configuration of a workspace (Settings, Model): on demand the caller's workspace
(``POST /routing/targets/{id}/probe``), on the schedule a workspace of the same organisation
whose configuration is for the target's provider (the one that probed it last while it still
is, otherwise the first such workspace by id). The probe sends the same tiny prompt as the model connection
test and stores what it measured: ``healthy`` (answered), ``degraded`` (the provider answered
with an error or took longer than ``ANUM_ROUTING_PROBE_DEGRADED_MS``), ``offline`` (not
reachable), or ``unverified`` with ``last_error`` when the workspace has no configuration
for the target's provider. Nothing is typed by hand.

The scheduled pass (``routing_probe``, registered with the automation scheduler) probes every
enabled target that has a reachable configuration: never probed ones first, then those whose
last probe is older than ``ANUM_ROUTING_PROBE_INTERVAL_SECONDS``. A target no workspace of its
organisation is configured for is left alone (it stays "not yet probed", or keeps its last
result) until one is. With PostgreSQL the due targets and the workspace to probe from are
discovered as ``anum_maintenance`` (ids, workspace, provider name and time only, migrations
0166 and 0176) and each probe runs as the application role inside that tenant and workspace.
The model call never runs inside a transaction (docs/realtime.md#event-loop).
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter

import httpx
from sqlalchemy import text

from .model_config_store import StoredModelConfig, open_model_config_store
from .model_gateway import build_model_gateway, normalize_provider
from .phase5 import RegionStatus, RoutingTarget, open_scale_store, store as memory_scale_store
from .schemas import TenantContext, utc_now
from .settings import settings

logger = logging.getLogger(__name__)

PROBE_PROMPT = "Reply with ANUM_OK only."
PROBE_USER = "system:routing-probe"


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    status: RegionStatus
    latency_ms: int | None
    error: str | None


def _client_factory() -> httpx.AsyncClient | None:
    """Tests inject an httpx transport here; production uses the gateway's default client."""
    from . import onboarding

    return onboarding._test_client_factory()


def no_configuration_error(target: RoutingTarget, config: StoredModelConfig | None) -> str:
    if config is None:
        return (
            f"Not probed: this workspace has no model configuration to reach {target.provider}. "
            "Save one under Settings, Model, then probe again."
        )
    return (
        f"Not probed: this workspace's model configuration is for {config.provider}, not {target.provider}. "
        f"Probe from a workspace configured for {target.provider}, or change the target's provider."
    )


async def probe(target: RoutingTarget, config: StoredModelConfig | None) -> ProbeOutcome:
    """One measured call to the target's model through the borrowed configuration."""
    provider = normalize_provider(target.provider)
    if provider != "mock" and (config is None or normalize_provider(config.provider) != provider):
        return ProbeOutcome(RegionStatus.UNVERIFIED, None, no_configuration_error(target, config))
    gateway = build_model_gateway(
        provider,
        api_key=config.api_key.get_secret_value() if config and config.api_key else None,
        model=target.model,
        base_url=config.base_url if config else "",
        client=_client_factory(),
        egress_guard=True,
    )
    started = perf_counter()
    try:
        await gateway.generate_text(PROBE_PROMPT)
    except httpx.HTTPStatusError as exc:
        latency = round((perf_counter() - started) * 1000)
        return ProbeOutcome(RegionStatus.DEGRADED, latency, _failure(target, config, exc))
    except Exception as exc:  # transport errors, timeouts, refused endpoints
        return ProbeOutcome(RegionStatus.OFFLINE, None, _failure(target, config, exc))
    latency = round((perf_counter() - started) * 1000)
    if latency >= settings.routing_probe_degraded_ms:
        return ProbeOutcome(RegionStatus.DEGRADED, latency, f"Answered in {latency} ms, above the {settings.routing_probe_degraded_ms} ms limit.")
    return ProbeOutcome(RegionStatus.HEALTHY, latency, None)


def _failure(target: RoutingTarget, config: StoredModelConfig | None, exc: Exception) -> str:
    # The same wording, and the same egress secrecy, as the model connection test.
    from .onboarding import _connection_failure_detail

    return _connection_failure_detail(normalize_provider(target.provider), target.model, config.base_url if config else "", exc)


def apply(target: RoutingTarget, outcome: ProbeOutcome, workspace_id: str) -> RoutingTarget:
    return target.model_copy(update={
        "status": outcome.status,
        "last_checked_at": utc_now(),
        "measured_latency_ms": outcome.latency_ms,
        "last_error": outcome.error,
        "probe_workspace_id": workspace_id,
    })


def _read_config(context: TenantContext) -> StoredModelConfig | None:
    with open_model_config_store(context) as configs:
        return configs.get(context)


def _store_result(context: TenantContext, target: RoutingTarget, outcome: ProbeOutcome, *, scheduled: bool) -> RoutingTarget:
    """Write the probe result in its own short unit of work; a status change is audited."""
    from .audit import AuditRecord
    from .phase5 import _stored_target
    from .schemas import new_id

    with open_scale_store(context) as scoped:
        current = _stored_target(scoped, context, target.id)
        probed = apply(current, outcome, context.workspace_id)
        saved = scoped.upsert_target(probed, context)
        if current.status != probed.status or not scheduled:
            scoped.record_audit(AuditRecord(
                id=new_id("audit"), tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                actor=context.user_id, action="routing_target.probed", target=f"routing_target:{target.id}",
                outcome="success" if outcome.status == RegionStatus.HEALTHY else "degraded",
                correlation_id=new_id("correlation"), created_at=utc_now(),
                metadata={"previous_status": current.status.value, "status": probed.status.value,
                          "latency_ms": outcome.latency_ms, "error": outcome.error, "scheduled": scheduled},
            ))
        return saved


async def probe_and_store(context: TenantContext, target: RoutingTarget, *, scheduled: bool = False) -> RoutingTarget:
    """Read the configuration, call the model with nothing open, then store the result."""
    config = await asyncio.to_thread(_read_config, context)
    outcome = await probe(target, config)
    return await asyncio.to_thread(_store_result, context, target, outcome, scheduled=scheduled)


# ------------------------------------------------------------------------ the scheduler pass


# Mirrors model_gateway.normalize_provider in SQL: trimmed, lower case, openai_compatible spelled
# openai-compatible. A target of the ``mock`` provider is reached through any configuration.
_DUE = text(
    """
    select t.tenant_id, c.workspace_id, t.id
    from routing_targets t
    join lateral (
      select c.workspace_id from workspace_model_configs c
      where c.tenant_id = t.tenant_id
        and (
          lower(trim(t.provider)) = 'mock'
          or (case when lower(trim(c.provider)) = 'openai_compatible' then 'openai-compatible' else lower(trim(c.provider)) end)
           = (case when lower(trim(t.provider)) = 'openai_compatible' then 'openai-compatible' else lower(trim(t.provider)) end)
        )
      order by (c.workspace_id = t.probe_workspace_id) desc nulls last, c.workspace_id
      limit 1
    ) c on true
    where t.enabled and (t.last_checked_at is null or t.last_checked_at <= :before)
    order by t.last_checked_at asc nulls first, t.tenant_id, t.id
    limit :limit
    """
)


def pick_workspace(target: RoutingTarget, providers: dict[str, str]) -> str | None:
    """The workspace to probe ``target`` from: one configured for its provider, preferring the
    workspace that probed it last, else the first by id; ``None`` when none is."""
    wanted = normalize_provider(target.provider)
    reachable = sorted(
        workspace for workspace, provider in providers.items() if wanted == "mock" or normalize_provider(provider) == wanted
    )
    if not reachable:
        return None
    return target.probe_workspace_id if target.probe_workspace_id in reachable else reachable[0]


def _due_scopes(before) -> list[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    """(tenant, workspace to probe from, target) of the targets due a scheduled probe."""
    if settings.repository_backend == "memory":
        from .model_config_store import memory_model_config_store

        scopes: list[tuple[str, str, str]] = []
        for tenant_id, target in memory_scale_store.due_probes(before):
            workspace = pick_workspace(target, memory_model_config_store.providers_by_workspace(tenant_id))
            if workspace is not None:
                scopes.append((tenant_id, workspace, target.id))
            if len(scopes) >= settings.routing_probe_batch_size:
                break
        return scopes
    from .maintenance import default_session_factory, discover

    rows = discover(default_session_factory, _DUE, {"before": before, "limit": settings.routing_probe_batch_size})
    return [(row[0], row[1], row[2]) for row in rows]


async def _probe_scope(tenant_id: str, workspace_id: str, target_id: str) -> bool:
    from .phase5 import _read_target

    context = TenantContext(tenant_id=tenant_id, workspace_id=workspace_id, user_id=PROBE_USER, roles=["owner"])
    try:
        target = await asyncio.to_thread(_read_target, context, target_id)
        await probe_and_store(context, target, scheduled=True)
        return True
    except Exception:
        logger.exception("routing probe failed", extra={"anum_routing_probe": {"tenant_id": tenant_id, "target_id": target_id}})
        return False


async def _probe_all(scopes: list[tuple[str, str, str]]) -> int:
    results = [await _probe_scope(*scope) for scope in scopes]
    return sum(results)


def run_due_routing_probes() -> int:
    """One scheduler pass: probe every enabled target with a reachable configuration that was
    never probed or was probed longer ago than the interval."""
    before = utc_now() - timedelta(seconds=settings.routing_probe_interval_seconds)
    scopes = _due_scopes(before)
    if not scopes:
        return 0
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_probe_all(scopes))
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _probe_all(scopes)).result()
