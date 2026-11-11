"""Health probes for routing targets (docs/governance-and-scale.md#routing-targets).

A routing target names a provider and a model, not credentials, so a probe borrows the model
configuration of a workspace (Settings, Model): on demand the caller's workspace
(``POST /routing/targets/{id}/probe``), on the schedule the workspace that probed the target
last (``probe_workspace_id``). The probe sends the same tiny prompt as the model connection
test and stores what it measured: ``healthy`` (answered), ``degraded`` (the provider answered
with an error or took longer than ``ANUM_ROUTING_PROBE_DEGRADED_MS``), ``offline`` (not
reachable), or ``unverified`` with ``last_error`` when the workspace has no configuration
for the target's provider. Nothing is typed by hand.

The scheduled pass (``routing_probe``, registered with the automation scheduler) re-probes
enabled, already probed targets older than ``ANUM_ROUTING_PROBE_INTERVAL_SECONDS``. With
PostgreSQL the due targets are discovered as ``anum_maintenance`` (ids, workspace and time
only, migration 0166) and each probe runs as the application role inside that tenant and
workspace. The model call never runs inside a transaction (docs/realtime.md#event-loop).
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


def _due_scopes(before) -> list[tuple[str, str, str]]:  # type: ignore[no-untyped-def]
    if settings.repository_backend == "memory":
        return memory_scale_store.due_probes(before)
    from .maintenance import default_session_factory, discover

    rows = discover(
        default_session_factory,
        text(
            "select tenant_id, probe_workspace_id, id from routing_targets "
            "where enabled and probe_workspace_id is not null and last_checked_at <= :before "
            "order by last_checked_at limit :limit"
        ),
        {"before": before, "limit": settings.routing_probe_batch_size},
    )
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
    """One scheduler pass: re-probe every enabled, already probed target older than the interval."""
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
