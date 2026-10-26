"""Workspace-scoped unattended meeting supervisor.

A continuously running process discovers active meetings owned by its explicitly
configured service principal; each worker uses existing PostgreSQL scoped stores.
No cross-tenant discovery, FastAPI process-local background tasks or impersonation.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from sqlalchemy import text
from .schemas import TenantContext
from .settings import settings
from .scoped_store import open_scoped_store
from .live_agent_meeting_store import SqlLiveMeetingStore
from .unattended_meeting_worker import run_unattended

logger=logging.getLogger("anum.meeting.supervisor")
ACTIVE=text("""
select id from agent_meeting_sessions
where tenant_id=:tenant and workspace_id=:workspace
and created_by=:owner and status in ('active','paused')
order by created_at,id
limit :limit
""")


def discover(context:TenantContext, limit:int)->list[str]:
    with open_scoped_store(context,None,SqlLiveMeetingStore,user_scoped=True) as store:
        rows=store.session.execute(ACTIVE,store.args(context,owner=context.user_id,limit=limit)).scalars()
        return list(rows)


async def supervise(context:TenantContext, *,poll_seconds:float=5,max_meetings:int=8,
                    stop:asyncio.Event|None=None)->None:
    """Discover meetings started in the UI; cancel and reap tasks at shutdown."""
    if settings.repository_backend!="postgresql":
        raise RuntimeError("Meeting supervisor requires PostgreSQL")
    if not 1<=max_meetings<=50 or not 1<=poll_seconds<=60:
        raise ValueError("Invalid supervisor limits")
    stop=stop or asyncio.Event()
    running:dict[str,asyncio.Task[str]]={}
    try:
        while not stop.is_set():
            for mid,task in tuple(running.items()):
                if task.done():
                    try:logger.info("Meeting %s exited: %s",mid,task.result())
                    except Exception:logger.exception("Meeting %s worker failed",mid)
                    del running[mid]
            try:
                eligible=await asyncio.to_thread(discover,context,max_meetings)
                for meeting_id in eligible:
                    if len(running)>=max_meetings:break
                    if meeting_id not in running:
                        running[meeting_id]=asyncio.create_task(
                            run_unattended(context,meeting_id,stop=stop),
                            name="meeting-"+meeting_id,
                        )
            except Exception:
                logger.exception("Meeting discovery failed; retrying")
            try:await asyncio.wait_for(stop.wait(),timeout=poll_seconds)
            except asyncio.TimeoutError:pass
    finally:
        stop.set()
        if running:
            done,pending=await asyncio.wait(running.values(),timeout=90)
            for task in pending:task.cancel()
            if pending:await asyncio.gather(*pending,return_exceptions=True)


def main()->None:
    parser=argparse.ArgumentParser(description="ANUM MINE unattended meeting supervisor")
    parser.add_argument("--tenant",required=True)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--owner",required=True)
    parser.add_argument("--poll",type=float,default=5)
    parser.add_argument("--max-meetings",type=int,default=8)
    a=parser.parse_args()
    context=TenantContext(tenant_id=a.tenant,workspace_id=a.workspace,user_id=a.owner)
    async def runner()->None:
        stop=asyncio.Event()
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):
            try:loop.add_signal_handler(sig,stop.set)
            except NotImplementedError:pass
        await supervise(context,poll_seconds=a.poll,max_meetings=a.max_meetings,stop=stop)
    logging.basicConfig(level=logging.INFO)
    asyncio.run(runner())


if __name__=="__main__":
    main()
