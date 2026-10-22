"""Safety contract: unattended workers must terminate persisted meeting state.

A provider failure must not cause the supervisor to repeat paid model calls.
"""
from unittest.mock import patch
import pytest
from anum_api.schemas import TenantContext
from anum_api.unattended_meeting_worker import settle_meeting


def context():
    return TenantContext(tenant_id="t",workspace_id="w",user_id="owner")


class Store:
    def __init__(self,status="active",owner="owner"):
        self.status=status
        self.owner=owner
        self.transitioned=[]
    def get(self,scope,meeting_id,lock=False):
        assert lock is True
        return {"id":meeting_id,"created_by":self.owner,"status":self.status,"revision":7}
    def transition(self,scope,meeting_id,revision,target):
        self.transitioned.append((meeting_id,revision,target))


class Scoped:
    def __init__(self,store):self.store=store
    def __enter__(self):return self.store
    def __exit__(self,*args):return False


@pytest.mark.parametrize("status",["scheduled","paused","finished","cancelled","failed"])
def test_does_not_override_inactive_meetings(status):
    store=Store(status=status)
    with patch("anum_api.unattended_meeting_worker.open_scoped_store",return_value=Scoped(store)):
        assert settle_meeting(context(),"m","finished") is False
    assert not store.transitioned


def test_only_owner_may_settle():
    store=Store(owner="someone_else")
    with patch("anum_api.unattended_meeting_worker.open_scoped_store",return_value=Scoped(store)):
        assert settle_meeting(context(),"m","failed") is False
    assert not store.transitioned


@pytest.mark.parametrize("state",["finished","failed"])
def test_active_meeting_reaches_durable_terminal_state(state):
    store=Store()
    with patch("anum_api.unattended_meeting_worker.open_scoped_store",return_value=Scoped(store)):
        assert settle_meeting(context(),"m",state) is True
    assert store.transitioned==[("m",7,state)]


def test_invalid_target_is_rejected_before_db_access():
    with pytest.raises(ValueError):
        settle_meeting(context(),"m","cancelled")
