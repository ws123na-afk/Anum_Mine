"""Temporal worker for durable agent runs: ``python -m anum_api.worker``.

Connects to ``ANUM_TEMPORAL_TARGET`` / ``ANUM_TEMPORAL_NAMESPACE``, polls
``ANUM_TEMPORAL_TASK_QUEUE`` and executes :class:`AgentRunWorkflow` with the
``anum.advance_run`` activity. It uses the same repository, model gateway, tool
registry, event bus and run-lock settings as the API, so it must be configured
with the same ``ANUM_*`` environment (in particular
``ANUM_REPOSITORY_BACKEND=postgresql``; the in-memory repository is per process).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from .agent_tools import ToolRegistry, default_tool_registry
from .durable_runs import AgentRunActivities
from .error_reporting import setup_error_reporting, shutdown_error_reporting
from .integration_tools import configured_external_handler
from .integrations import default_integration_registry
from .web_search import SEARXNG_INTEGRATION_ID, configured_web_search_handler
from .model_gateway import ModelGateway, build_default_model_gateway
from .repository import AnumRepository
from .runtime import AgentRuntime
from .schemas import TenantContext
from .settings import Settings, settings
from .telemetry import (
    LOG_FORMAT,
    install_log_correlation,
    setup_telemetry,
    shutdown_telemetry,
    sqlalchemy_engines,
    temporal_interceptors,
)
from .temporal_workflow import AgentRunWorkflow
from .valkey import build_run_lock_manager

logger = logging.getLogger("anum_api.worker")


def build_activities(
    config: Settings,
    *,
    model_gateway: ModelGateway | None = None,
    tools: ToolRegistry | None = None,
) -> AgentRunActivities:
    from .onboarding import budgeted_model_gateway

    gateway = model_gateway or build_default_model_gateway(config)
    from . import email_inbox
    from .commerce.tools import register_commerce_tools
    from .developer import register_developer_tools
    from .whatsapp import register_whatsapp_tools

    integrations = default_integration_registry(config)
    email_inbox.integration_enabled = lambda context, integration_id: not integrations.switched_off(integration_id, context)
    registry = tools or register_commerce_tools(email_inbox.register_email_tools(register_whatsapp_tools(register_developer_tools(
        default_tool_registry(
            configured_external_handler(config, integrations),
            configured_web_search_handler(config, integrations.workspace_enabled(SEARXNG_INTEGRATION_ID)),
        )
    ))))

    def runtime_factory(context: TenantContext, repository: AnumRepository) -> AgentRuntime:
        from .retrieval_api import retriever_for

        return AgentRuntime(
            budgeted_model_gateway(context, gateway),
            repository,
            tools=registry,
            retriever=retriever_for(context, gateway),
        )

    return AgentRunActivities(
        runtime_factory, locks=build_run_lock_manager(config, metric_source="worker")
    )


def sandbox_runner() -> SandboxedWorkflowRunner:
    """The default workflow sandbox, with OpenTelemetry passed through.

    The tracing interceptor (anum_api.telemetry.temporal_interceptors) creates spans
    inside workflow code; re-importing OpenTelemetry per workflow run would be slow
    and would break its global state.
    """
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("opentelemetry")
    )


def build_worker(client: Client, config: Settings, activities: AgentRunActivities, **options: Any) -> Worker:
    options.setdefault("workflow_runner", sandbox_runner())
    return Worker(
        client,
        task_queue=config.temporal_task_queue,
        workflows=[AgentRunWorkflow],
        activities=[activities.advance_run],
        **options,
    )


async def run_worker(config: Settings = settings) -> None:
    from .dependencies import event_runtime
    from .identity import validate_auth_configuration
    from .hardening import enforce_startup_policy

    # The worker refuses the same insecure configurations as the API.
    validate_auth_configuration(config)
    enforce_startup_policy(config)
    if config.repository_backend == "memory" and config.environment not in {"local", "test"}:
        raise RuntimeError("The worker needs ANUM_REPOSITORY_BACKEND=postgresql outside local")

    setup_telemetry(config, service_name="anum-worker", engines=sqlalchemy_engines(event_runtime))
    # Errors only, without tenant tags: activities share one scope (anum_api/error_reporting.py).
    setup_error_reporting(config, service_name="anum-worker")
    client = await Client.connect(
        config.temporal_target,
        namespace=config.temporal_namespace,
        interceptors=temporal_interceptors(),
    )
    worker = build_worker(client, config, build_activities(config))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    # Runs finished by this worker push to phones too (docs/notifications.md).
    from .push import push_dispatcher

    event_runtime.add_listener(push_dispatcher.submit)
    await event_runtime.start()
    push_dispatcher.start()
    try:
        async with worker:
            logger.info(
                "ANUM worker polling %s on %s (namespace %s)",
                config.temporal_task_queue,
                config.temporal_target,
                config.temporal_namespace,
            )
            await stop.wait()
            logger.info("Shutting down; in-flight activities are retried by Temporal")
    finally:
        await push_dispatcher.stop()
        await event_runtime.stop()
        shutdown_telemetry()
        shutdown_error_reporting()


def main() -> None:
    # Log lines carry trace_id, span_id and correlation_id (anum_api.telemetry).
    install_log_correlation()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    asyncio.run(run_worker())
    # Clean shutdown is complete (polling stopped, activities handed back, telemetry
    # flushed). Skip interpreter finalization: garbage-collecting the Temporal SDK's
    # native objects at exit can hang until the orchestrator kills the process.
    # Errors still raise above and exit non-zero.
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
