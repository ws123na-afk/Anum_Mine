"""Logo for an office building's sign (docs/ANUM_MINE_HQ_LOGO_SIGNS.md).

* ``PUT    /api/v1/office-spaces/buildings/{id}/logo``: the raw image bytes as the body (PNG, JPEG or WebP, at most
  1 MB). ``workspace:manage``, ``expected_revision`` (the building's revision you were shown) and
  ``input_mode=click``. The bytes are sniffed, scanned (when a scanner is installed), decoded and re-encoded to a
  PNG of at most 512 x 512 pixels; only that PNG is stored (own table, per tenant and workspace, forced RLS).
* ``GET    /api/v1/office-spaces/buildings/{id}/logo``: the stored PNG through the authenticated API only (there is no
  public URL). ``Cache-Control: private, no-cache`` with an ``ETag`` (the SHA-256 of the stored PNG), so a browser
  revalidates and gets ``304`` while the logo is unchanged. ``task:read``.
* ``DELETE /api/v1/office-spaces/buildings/{id}/logo``: back to the lettered sign. Same rules as the upload.

Brand colour and sign style are ordinary fields of ``PATCH /api/v1/office-spaces/buildings/{id}``. A change
writes one audit record (ids, revisions, the style, the colour and the logo hash; never the file bytes) and the
``office.layout_changed`` event, so other open browsers refresh.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import file_policy, office_logo_image as imaging
from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .files import check_upload_content, read_upload_body
from .office_scope import require_postgres, workspace_session
from .office_space_api import Identifier, _audit, _error, _snap, _store
from .office_space_store import SpaceStoreError
from .schemas import TenantContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/office-spaces/buildings", tags=["office building signs"])

BuildingId = Annotated[str, Path(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
InputMode = Literal["click", "typed", "voice"]
CLICK_ONLY = "Confirm this on screen with a click. A spoken or typed answer cannot change a building's logo."
NO_LOGO = "This building has no logo."
_EXTENSION = {"PNG": "logo.png", "JPEG": "logo.jpg", "WEBP": "logo.webp"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _click(mode: str) -> None:
    if mode != "click":
        raise HTTPException(status.HTTP_403_FORBIDDEN, CLICK_ONLY)


def _screen_and_encode(content: bytes) -> imaging.Logo:
    """Blocking work (scanner round trip, decoding): runs in a worker thread, never on the event loop."""
    kind = imaging.sniff(content)
    allowed = file_policy.resolve_content_type(_EXTENSION[kind], None)
    check_upload_content(_EXTENSION[kind], allowed, content)
    return imaging.prepare(content)


@router.put("/{building_id}/logo")
async def put_logo(
    building_id: BuildingId, request: Request,
    expected_revision: int = Query(ge=1),
    input_mode: InputMode = Query(default="typed"),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    _click(input_mode)
    content = await read_upload_body(request, imaging.MAX_UPLOAD_BYTES)
    try:
        logo = await asyncio.to_thread(_screen_and_encode, content)
    except imaging.LogoRefused as exc:
        raise HTTPException(exc.status, exc.message) from None
    return await asyncio.to_thread(_store_logo, context, building_id, expected_revision, logo)


def _store_logo(context: TenantContext, building_id: str, revision: int, logo: imaging.Logo) -> dict[str, Any]:
    try:
        with _store(context) as store:
            before = store._lock_checked(context, "buildings", building_id, revision)  # noqa: SLF001
            store.session.execute(text("""
                insert into office_building_logos
                    (tenant_id, workspace_id, building_id, png, sha256, width, height, byte_size, uploaded_by)
                values (:t, :w, :b, :png, :sha, :wd, :ht, :n, :u)
                on conflict (tenant_id, workspace_id, building_id) do update set
                    png = excluded.png, sha256 = excluded.sha256, width = excluded.width, height = excluded.height,
                    byte_size = excluded.byte_size, uploaded_by = excluded.uploaded_by, updated_at = now()
            """), {"t": context.tenant_id, "w": context.workspace_id, "b": building_id, "png": logo.png,
                   "sha": logo.sha256, "wd": logo.width, "ht": logo.height, "n": len(logo.png), "u": context.user_id})
            after = store._apply(context, "buildings", building_id, {"logo_sha256": logo.sha256})  # noqa: SLF001
            _audit(store, context, "office.building.logo_set", building_id, kind="buildings",
                   before=_snap("buildings", before),
                   after={**(_snap("buildings", after) or {}), "input_mode": "click", "logo_bytes": len(logo.png),
                          "logo_size": [logo.width, logo.height]})
            return after
    except SpaceStoreError as exc:
        raise _error(exc) from exc


@router.delete("/{building_id}/logo")
def delete_logo(
    building_id: BuildingId,
    expected_revision: int = Query(ge=1),
    input_mode: InputMode = Query(default="typed"),
    context: TenantContext = Depends(tenant_context),
) -> dict[str, Any]:
    require_permission(context, Permission.WORKSPACE_MANAGE)
    require_postgres()
    _click(input_mode)
    try:
        with _store(context) as store:
            before = store._lock_checked(context, "buildings", building_id, expected_revision)  # noqa: SLF001
            if not before.get("logo_sha256"):
                raise HTTPException(status.HTTP_404_NOT_FOUND, NO_LOGO)
            store.session.execute(text("delete from office_building_logos where tenant_id = :t and workspace_id = :w "
                                       "and building_id = :b"),
                                  {"t": context.tenant_id, "w": context.workspace_id, "b": building_id})
            after = store._apply(context, "buildings", building_id, {"logo_sha256": None})  # noqa: SLF001
            _audit(store, context, "office.building.logo_clear", building_id, kind="buildings",
                   before=_snap("buildings", before), after={**(_snap("buildings", after) or {}), "input_mode": "click"})
            return after
    except SpaceStoreError as exc:
        raise _error(exc) from exc


@router.get("/{building_id}/logo")
def get_logo(building_id: BuildingId, request: Request, context: TenantContext = Depends(tenant_context)) -> Response:
    require_permission(context, Permission.TASK_READ)
    require_postgres()
    with workspace_session(context) as session:
        row = session.execute(
            text("select sha256 from office_building_logos where tenant_id = :t and workspace_id = :w and building_id = :b"),
            {"t": context.tenant_id, "w": context.workspace_id, "b": building_id}).first()
        if row is None:
            return JSONResponse({"detail": NO_LOGO}, status_code=404, headers={"Cache-Control": "private, no-store"})
        etag = f'"{row[0]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Authorization, Accept-Encoding",
                   **_SECURITY_HEADERS}
        if etag in [part.strip().removeprefix("W/") for part in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        png = session.execute(
            text("select png from office_building_logos where tenant_id = :t and workspace_id = :w and building_id = :b"),
            {"t": context.tenant_id, "w": context.workspace_id, "b": building_id}).scalar_one_or_none()
    if png is None:  # removed between the two reads
        return JSONResponse({"detail": NO_LOGO}, status_code=404, headers={"Cache-Control": "private, no-store"})
    return Response(bytes(png), media_type="image/png", headers={**headers, "Content-Disposition": 'inline; filename="logo.png"'})
