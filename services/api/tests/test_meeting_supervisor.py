"""Contract tests for workspace meeting discovery and supervisor limits."""
import asyncio
from unittest.mock import patch
import pytest
from anum_api.schemas import TenantContext
from anum_api.meeting_supervisor import supervise, discover


def test_discovery_uses_authenticated_scope():
    context=TenantContext(tenant_id="tenant1",workspace_id="workspace1",user_id="owner1")
    class Result:
        def scalars(self):return ["m1","m2"]
    class Session:
        def execute(self,query,params):
            assert params=={"tenant":"tenant1","workspace":"workspace1","owner":"owner1","limit":2}
            return Result()
    class Store:
        session=Session()
        def args(self,context,**kwargs):
            return {"tenant":context.tenant_id,"workspace":context.workspace_id,**kwargs}
    class Scope:
        def __enter__(self):return Store()
        def __exit__(self,*args):pass
    with patch("anum_api.meeting_supervisor.open_scoped_store",return_value=Scope()):
        assert discover(context,2)==["m1","m2"]


@pytest.mark.asyncio
async def test_invalid_supervisor_limits():
    context=TenantContext(tenant_id="tenant1",workspace_id="workspace1",user_id="owner1")
    with patch("anum_api.meeting_supervisor.settings") as settings:
        settings.repository_backend="postgresql"
        with pytest.raises(ValueError):
            await supervise(context,max_meetings=0)
        with pytest.raises(ValueError):
            await supervise(context,poll_seconds=0)
