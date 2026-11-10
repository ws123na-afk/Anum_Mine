"""Whether this workspace's AI answers come from a real model or the built-in placeholder.

The server's default model is ``ANUM_MODEL_PROVIDER`` (``mock`` unless the operator sets one); a
workspace that saved its own usable model in Settings uses that instead (``workspace_model_gateway``).
The ``mock`` provider writes placeholder text, so the web app shows a banner while it is in use
(docs/model-gateway.md#placeholder-answers) and the Ready to sell checklist lists it
(docs/selling-readiness.md#checklist).

``GET /api/v1/model-status`` reads the workspace's saved model in a plain ``def`` route, so the
store is opened in FastAPI's thread pool, never on the event loop (docs/realtime.md#event-loop).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .authorization import Permission
from .dependencies import require_permission, tenant_context
from .model_config_store import open_model_config_store
from .model_gateway import normalize_provider, server_key_missing
from .schemas import TenantContext
from .settings import settings

router = APIRouter(prefix="/api/v1", tags=["model"])

PLACEHOLDER_PROVIDER = "mock"
# Providers that call a hosted API and need ANUM_MODEL_API_KEY.
KEYED_PROVIDERS = frozenset({"openai-compatible", "anthropic"})


class ModelStatus(BaseModel):
    # True when answers come from the built-in placeholder model, not a real one.
    placeholder: bool
    # The provider in use: "mock", "anthropic", "openai-compatible" or "ollama".
    provider: str
    # "workspace" when this workspace saved its own model in Settings, else "server".
    source: Literal["workspace", "server"]


def server_model_state() -> dict[str, object]:
    """The server's default model from ``ANUM_MODEL_*``: provider, placeholder and a missing key."""
    provider = normalize_provider(settings.model_provider or PLACEHOLDER_PROVIDER)
    key = settings.model_api_key.strip() if settings.model_api_key else ""
    return {
        "provider": provider,
        "placeholder": provider == PLACEHOLDER_PROVIDER,
        "key_missing": provider in KEYED_PROVIDERS and not key,
    }


def workspace_model_status(context: TenantContext) -> ModelStatus:
    """The model that answers in this workspace. Opens the model config store: call it off the loop."""
    with open_model_config_store(context) as store:
        saved = store.get(context, include_secret=False)
    if saved is not None and saved.usable:
        provider = normalize_provider(saved.provider)
        return ModelStatus(placeholder=provider == PLACEHOLDER_PROVIDER, provider=provider, source="workspace")
    server = server_model_state()
    # A hosted provider whose key was missing at startup answers with the placeholder model.
    return ModelStatus(placeholder=bool(server["placeholder"]) or server_key_missing(), provider=str(server["provider"]), source="server")


@router.get("/model-status", response_model=ModelStatus)
def get_model_status(context: TenantContext = Depends(tenant_context)) -> ModelStatus:
    """Any member who can read tasks may see whether answers are placeholders: they read those answers."""
    require_permission(context, Permission.TASK_READ)
    return workspace_model_status(context)
