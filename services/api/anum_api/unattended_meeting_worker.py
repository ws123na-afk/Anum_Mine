"""Unattended ANUM MINE meeting runner for a *single configured workspace*.

Invoke as a managed service, not as a FastAPI BackgroundTask. Process restarts
resume from PostgreSQL turns. The caller must provision a per-workspace service
identity; this process does not discover tenants or bypass RLS.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone

from .live_agent_meeting_store import SqlLiveMeetingStore, LiveMeetingStoreError
from .meeting_execution import execute_meeting_turn
from .model_gateway import build_default_model_gateway
from .scoped_store import open_scoped_store
from .schemas import TenantContext
from .settings import settings

log=logging.getLogger("anum.meeting.worker")


async def run_unattended(
    context:TenantContext, meeting_id:str, *,
    interval_seconds:float=2.0,
    stop:asyncio.Event|None=None,
) -> str:
    """Resume an active meeting with idempotent DB-derived turn keys.

    A single meeting is processed sequentially. Startup is safe after process
    restarts; executing two workers simultaneously relies on database leases.
    """
    if settings.repository_backend!="postgresql":
        raise RuntimeError("Unattended meetings require PostgreSQL")
    stop=stop or asyncio.Event()
    gateway=build_default_model_gateway()
    while not stop.is_set():
        with open_scoped_store(context,None,SqlLiveMeetingStore,user_scoped=True) as store:
            meeting=store.get(context,meeting_id)
            if meeting is None:return "missing"
            if meeting["created_by"]!=context.user_id:return "forbidden"
            if meeting["status"] in ("finished","cancelled","failed"):return meeting["status"]
            if meeting["status"]=="paused":
                next_key=None
            elif meeting["status"]=="scheduled":
                return "not_started"
            else:
                turns=store.turns(context,meeting_id,limit=100)
                # The database is authoritative, not a process-local counter.
                from sqlalchemy import text
                seq=store.session.execute(text("""
                    select coalesce(max(sequence),0)+1 from agent_meeting_turns
                    where tenant_id=:tenant and workspace_id=:workspace and session_id=:id
                """),store.args(context,id=meeting_id)).scalar_one()
                next_key="unattended:"+meeting_id+":"+str(seq)
                started=meeting["started_at"]
                expired=bool(started and (datetime.now(timezone.utc)-started).total_seconds()>=meeting["max_duration_seconds"])
                exhausted=seq>meeting["max_turns"] or (
                    meeting["total_input_tokens"]+meeting["total_output_tokens"]>=meeting["max_tokens"])
                exhausted=exhausted or meeting["total_cost_usd"]>=meeting["max_cost_usd"]
                if expired or exhausted:
                    return "limit_reached"
        if next_key:
            try:
                await execute_meeting_turn(context,meeting_id,next_key,gateway)
            except LiveMeetingStoreError as exc:
                if exc.code in ("forbidden","not_found","unavailable"):return exc.code
                # A concurrent worker, pause, cancellation or expired lease
                # can create a conflict. Re-read persisted state before retry.
                log.warning("Meeting turn deferred: %s",exc.code)
            except Exception:
                # Provider failures are not silently retried with a new key:
                # the next tick reuses DB-derived numbering. Operators must
                # inspect provider failures and avoid enabling auto-retry billing.
                log.exception("Meeting provider failure; worker stopped")
                return "provider_failure"
        try:
            await asyncio.wait_for(stop.wait(),timeout=max(1.0,interval_seconds))
        except asyncio.TimeoutError:
            pass
    return "stopped"


def main()->None:
    parser=argparse.ArgumentParser(description="Scoped ANUM meeting worker")
    parser.add_argument("--tenant",required=True)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--owner",required=True)
    parser.add_argument("--meeting",required=True)
    parser.add_argument("--interval",type=float,default=2.0)
    a=parser.parse_args()
    if a.interval<1 or a.interval>60:parser.error("interval must be 1-60 seconds")
    context=TenantContext(tenant_id=a.tenant,workspace_id=a.workspace,
                          user_id=a.owner,roles=[])
    async def _main()->None:
        stop=asyncio.Event()
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            try:loop.add_signal_handler(sig,stop.set)
            except NotImplementedError:pass
        result=await run_unattended(context,a.meeting,interval_seconds=a.interval,stop=stop)
        log.info("Meeting runner stopped: %s",result)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__=="__main__":
    main()
