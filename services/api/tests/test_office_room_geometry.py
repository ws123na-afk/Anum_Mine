"""Input constraints for persisted ANUM MINE office geometry."""
import pytest
from pydantic import ValidationError
from anum_api.office_space_api import RoomGeometryUpdate


def test_accepts_bounded_positioned_room():
    room=RoomGeometryUpdate(expected_revision=3,x=-9.5,z=14.25,width=8,depth=4)
    assert room.expected_revision==3
    assert room.width==8


@pytest.mark.parametrize("changes",[
    {"width":0},{"depth":0},{"x":10001},{"z":-10001},
    {"expected_revision":0},{"width":1001},{"depth":1001},
    {"width":float("inf")},{"z":float("nan")},
])
def test_rejects_invalid_geometry(changes):
    payload={"expected_revision":1,"x":0,"z":0,"width":6,"depth":4}
    payload.update(changes)
    with pytest.raises(ValidationError):
        RoomGeometryUpdate(**payload)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        RoomGeometryUpdate(expected_revision=1,x=0,z=0,width=6,depth=4,tenant_id="foreign")
