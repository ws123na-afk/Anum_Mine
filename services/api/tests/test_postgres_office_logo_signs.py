"""Building logo signs on PostgreSQL as the non-owner app role (pytest -m database).

Covers the migration (nullable columns, new table with forced RLS), the upload route (content sniffing, re-encoding,
size limit, malware scan hook, click only, workspace:manage, revision check), the authenticated read route (private
cache headers, ETag and 304), brand colour and sign style through the building PATCH, the audit record (ids and
hashes, never bytes), tenant isolation and the cascade on building delete.
"""
from __future__ import annotations

import io
import json

import pytest
from conftest import APP_ROLE, TENANT_A, TENANT_B, WORKSPACE_A, WORKSPACE_B
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, text
from test_postgres_office_hq import EXEC, OWNER_A, _scoped, headers, hq, seed_member  # noqa: F401
from test_postgres_office_starter_campus import rows
from test_postgres_control_plane import postgres_backend  # noqa: F401

from anum_api import file_policy

pytestmark = pytest.mark.database
SP = "/api/v1/office-spaces"


def OWNER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, OWNER_A)


def VIEWER() -> dict[str, str]:
    return headers(TENANT_A, WORKSPACE_A, EXEC, "viewer")


def OWNER_B() -> dict[str, str]:
    return headers(TENANT_B, WORKSPACE_B, EXEC, "owner")


def image(fmt: str = "PNG", size=(300, 120), color=(15, 90, 60), **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt, **save)
    return out.getvalue()


def ok(response, code: int = 200):  # type: ignore[no-untyped-def]
    assert response.status_code == code, response.text
    return response.json() if response.content and response.headers.get("content-type", "").startswith("application/json") else None


def building(hq: TestClient, building_id: str = "b1", user=None) -> dict:
    h = user or OWNER()
    created = hq.post(f"{SP}/buildings", headers=h, json={"id": building_id, "name": f"Name {building_id}", "timezone": "Asia/Dubai"})
    assert created.status_code == 201, created.text
    return created.json()


def put(hq: TestClient, b: dict, content: bytes, *, mode: str = "click", user=None, revision: int | None = None,
        ctype: str = "image/png"):
    return hq.put(f"{SP}/buildings/{b['id']}/logo", headers={**(user or OWNER()), "content-type": ctype},
                  params={"expected_revision": revision or b["revision"], "input_mode": mode}, content=content)


# --------------------------------------------------------------------------- schema


def test_columns_are_nullable_and_the_logo_table_forces_row_level_security(database_engine: Engine) -> None:
    cols = rows(database_engine, """select column_name, is_nullable from information_schema.columns
        where table_name = 'office_buildings' and column_name in ('sign_style','brand_color','logo_sha256') order by 1""")
    assert cols == [("brand_color", "YES"), ("logo_sha256", "YES"), ("sign_style", "YES")]
    assert rows(database_engine, "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'office_building_logos'") == [(True, True)]
    assert rows(database_engine, "select count(*) from pg_policies where tablename = 'office_building_logos'") == [(2,)]


# --------------------------------------------------------------------------- upload and read


def test_upload_reencodes_stores_and_serves_the_logo_privately(hq: TestClient, database_engine: Engine) -> None:
    b = building(hq)
    assert b["logo_sha256"] is None and b["sign_style"] is None and b["brand_color"] is None
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    original = image("JPEG", (1600, 800), exif=exif)
    updated = ok(put(hq, b, original, ctype="application/octet-stream"))
    assert updated["revision"] == b["revision"] + 1 and len(updated["logo_sha256"]) == 64

    got = hq.get(f"{SP}/buildings/b1/logo", headers=OWNER())
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == "private, no-cache"
    assert got.headers["etag"] == f'"{updated["logo_sha256"]}"'
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in got.headers["content-security-policy"]
    picture = Image.open(io.BytesIO(got.content))
    assert picture.format == "PNG" and picture.size == (512, 256) and not picture.getexif()
    assert b"SecretCamera" not in got.content and got.content != original

    again = hq.get(f"{SP}/buildings/b1/logo", headers={**OWNER(), "if-none-match": got.headers["etag"]})
    assert again.status_code == 304 and not again.content and again.headers["etag"] == got.headers["etag"]
    stale = hq.get(f"{SP}/buildings/b1/logo", headers={**OWNER(), "if-none-match": '"0000"'})
    assert stale.status_code == 200

    # The building record carries the hash only; a list never carries bytes.
    listing = ok(hq.get(f"{SP}/buildings", headers=OWNER()))["items"][0]
    assert listing["logo_sha256"] == updated["logo_sha256"] and "png" not in listing
    assert rows(database_engine, "select width, height, byte_size from office_building_logos where tenant_id = :t", t=TENANT_A) == [(512, 256, len(got.content))]


def test_webp_and_png_are_accepted_whatever_the_declared_type(hq: TestClient) -> None:
    b = building(hq)
    b = ok(put(hq, b, image("WEBP"), ctype="text/plain"))
    ok(put(hq, b, image("PNG"), ctype="image/svg+xml"))


def test_refused_files_store_nothing_and_say_why(hq: TestClient, database_engine: Engine) -> None:
    b = building(hq)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    gif = io.BytesIO()
    Image.new("P", (30, 30)).save(gif, "GIF")
    bomb = io.BytesIO()
    Image.new("1", (30000, 30000), 0).save(bomb, "PNG")
    good = image("PNG", (200, 200))
    cases = [
        (svg, 415, "SVG"), (gif.getvalue(), 415, "GIF"), (b"plain text", 415, "PNG, JPEG or WebP"),
        (b"", 422, "empty"), (image("PNG", (1400, 1400), color=(1, 2, 3)) + b"\x00" * 1_100_000, 413, "byte limit"),
        (good[: len(good) // 2], 422, "could not be read"), (bomb.getvalue(), 422, "too large"),
        (image("PNG", (10, 10)), 422, "too small"),
    ]
    for content, status, word in cases:
        response = put(hq, b, content, ctype="image/png")
        assert response.status_code == status, (word, response.text)
        assert word in response.text
    assert rows(database_engine, "select count(*) from office_building_logos") == [(0,)]
    assert ok(hq.get(f"{SP}/buildings/b1", headers=OWNER()))["revision"] == b["revision"]


def test_arabic_clients_get_the_arabic_sentence(hq: TestClient) -> None:
    b = building(hq)
    response = hq.put(f"{SP}/buildings/b1/logo", headers={**OWNER(), "accept-language": "ar"},
                      params={"expected_revision": b["revision"], "input_mode": "click"}, content=b"<svg/>")
    assert response.status_code == 415
    assert "SVG" in response.json()["error"]["message"] and "لا تُقبل" in response.json()["error"]["message"]
    assert response.headers["content-language"] == "ar"


def test_only_a_click_by_a_workspace_manager_with_the_current_revision_changes_the_logo(hq: TestClient) -> None:
    b = building(hq)
    content = image()
    for mode in ("typed", "voice"):
        assert put(hq, b, content, mode=mode).status_code == 403
    assert put(hq, b, content, user=VIEWER()).status_code == 403
    assert put(hq, b, content, revision=b["revision"] + 5).status_code == 409
    updated = ok(put(hq, b, content))
    # The first revision is stale now.
    assert put(hq, b, content).status_code == 409
    assert hq.delete(f"{SP}/buildings/b1/logo", headers=VIEWER(), params={"expected_revision": updated["revision"], "input_mode": "click"}).status_code == 403
    assert hq.delete(f"{SP}/buildings/b1/logo", headers=OWNER(), params={"expected_revision": updated["revision"], "input_mode": "typed"}).status_code == 403
    # A viewer may read it (the campus draws it for everyone who can see the building).
    assert hq.get(f"{SP}/buildings/b1/logo", headers=VIEWER()).status_code == 200


def test_remove_logo_goes_back_to_the_lettered_sign(hq: TestClient, database_engine: Engine) -> None:
    b = building(hq)
    assert hq.delete(f"{SP}/buildings/b1/logo", headers=OWNER(), params={"expected_revision": b["revision"], "input_mode": "click"}).status_code == 404
    b = ok(put(hq, b, image()))
    cleared = ok(hq.delete(f"{SP}/buildings/b1/logo", headers=OWNER(), params={"expected_revision": b["revision"], "input_mode": "click"}))
    assert cleared["logo_sha256"] is None and cleared["revision"] == b["revision"] + 1
    assert hq.get(f"{SP}/buildings/b1/logo", headers=OWNER()).status_code == 404
    assert rows(database_engine, "select count(*) from office_building_logos") == [(0,)]


def test_deleting_an_empty_building_removes_its_logo(hq: TestClient, database_engine: Engine) -> None:
    b = ok(put(hq, building(hq), image()))
    assert rows(database_engine, "select count(*) from office_building_logos") == [(1,)]
    assert hq.delete(f"{SP}/buildings/b1", headers=OWNER(), params={"expected_revision": b["revision"]}).status_code == 204
    assert rows(database_engine, "select count(*) from office_building_logos") == [(0,)]


# --------------------------------------------------------------------------- colour and style


def test_brand_colour_and_sign_style_are_validated_and_clearable(hq: TestClient) -> None:
    b = building(hq)
    patched = ok(hq.patch(f"{SP}/buildings/b1", headers=OWNER(),
                          json={"expected_revision": b["revision"], "brand_color": "#1F6F4A", "sign_style": "plaque"}))
    assert patched["brand_color"] == "#1f6f4a" and patched["sign_style"] == "plaque"
    for bad in ({"brand_color": "green"}, {"brand_color": "#12345"}, {"sign_style": "neon"}, {"logo_sha256": "a" * 64}):
        assert hq.patch(f"{SP}/buildings/b1", headers=OWNER(), json={"expected_revision": patched["revision"], **bad}).status_code == 422
    cleared = ok(hq.patch(f"{SP}/buildings/b1", headers=OWNER(),
                          json={"expected_revision": patched["revision"], "brand_color": None, "sign_style": None}))
    assert cleared["brand_color"] is None and cleared["sign_style"] is None
    assert hq.patch(f"{SP}/buildings/b1", headers=VIEWER(), json={"expected_revision": cleared["revision"], "sign_style": "banner"}).status_code == 403


def test_the_database_itself_refuses_a_bad_colour_or_style(database_engine: Engine) -> None:
    hq_row = ("insert into office_buildings (id, tenant_id, workspace_id, created_by, name, timezone, {col}) "
              "values ('x', :t, :w, 'x', 'X', 'Asia/Dubai', :v)")
    for col, value in (("brand_color", "red"), ("brand_color", "#ABCDEF"), ("sign_style", "neon"), ("logo_sha256", "abc")):
        with pytest.raises(Exception):  # noqa: B017  a check constraint violation of any driver flavour
            with database_engine.begin() as connection:
                connection.execute(text(hq_row.format(col=col)), {"t": TENANT_A, "w": WORKSPACE_A, "v": value})


# --------------------------------------------------------------------------- audit, event, isolation


def test_audit_and_event_carry_ids_and_the_hash_never_the_bytes(hq: TestClient, database_engine: Engine) -> None:
    b = building(hq)
    updated = ok(put(hq, b, image()))
    ok(hq.delete(f"{SP}/buildings/b1/logo", headers=OWNER(), params={"expected_revision": updated["revision"], "input_mode": "click"}))
    audit = rows(database_engine, "select action, metadata from audit_records where action like 'office.building.logo%' and tenant_id = :t order by created_at", t=TENANT_A)
    assert [a[0] for a in audit] == ["office.building.logo_set", "office.building.logo_clear"]
    meta = audit[0][1] if isinstance(audit[0][1], dict) else json.loads(audit[0][1])
    assert meta["before"]["logo_sha256"] is None and meta["after"]["logo_sha256"] == updated["logo_sha256"]
    assert meta["after"]["input_mode"] == "click" and meta["after"]["logo_size"] == [300, 120]
    blob = json.dumps(audit, default=str)
    assert "PNG" not in blob and "base64" not in blob and "\\x89" not in blob
    events = rows(database_engine, "select payload from domain_events where type = 'office.layout_changed' and tenant_id = :t", t=TENANT_A)
    assert any("office.building.logo_set" in json.dumps(e[0], default=str) for e in events)


def test_a_second_tenant_cannot_read_or_overwrite_the_logo(hq: TestClient, database_engine: Engine) -> None:
    b = ok(put(hq, building(hq), image()))
    assert hq.get(f"{SP}/buildings/b1/logo", headers=OWNER_B()).status_code == 404
    assert put(hq, b, image(), user=OWNER_B()).status_code == 404
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_B, WORKSPACE_B)
        assert connection.execute(text("select count(*) from office_building_logos")).scalar_one() == 0
        assert connection.execute(text("update office_building_logos set sha256 = repeat('0', 64)")).rowcount == 0
        tx.rollback()
    with database_engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text(f"set local role {APP_ROLE}"))
        _scoped(connection, TENANT_A, WORKSPACE_A)
        assert connection.execute(text("select count(*) from office_building_logos")).scalar_one() == 1
        tx.rollback()


def test_the_malware_scan_hook_runs_on_the_original_bytes(hq: TestClient, database_engine: Engine) -> None:
    b = building(hq)
    seen: list[bytes] = []

    class Verdict(file_policy.ScanVerdict):
        pass

    class Infected:
        def scan(self, name, content_type, content):  # type: ignore[no-untyped-def]
            seen.append(content)
            return file_policy.ScanVerdict(clean=False, reason="Eicar-Test")

    class Down:
        def scan(self, name, content_type, content):  # type: ignore[no-untyped-def]
            raise ConnectionError("clamd unreachable")

    original = file_policy.file_scanner()
    try:
        content = image()
        file_policy.set_file_scanner(Infected())
        refused = put(hq, b, content)
        assert refused.status_code == 422 and "Eicar-Test" in refused.text and seen == [content]
        file_policy.set_file_scanner(Down())
        assert put(hq, b, content).status_code == 503
    finally:
        file_policy.set_file_scanner(original)
    assert rows(database_engine, "select count(*) from office_building_logos") == [(0,)]
    ok(put(hq, b, content))
