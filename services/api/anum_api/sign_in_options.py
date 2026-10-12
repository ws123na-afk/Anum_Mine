"""What the sign-in screens offer before anyone is signed in (docs/identity.md#google-and-apple-sign-in).

``GET /api/v1/auth/sign-in-options`` is public: the web and Flutter sign-in screens call
it to decide whether to show "Continue with Google", "Continue with Apple" and "Create
account", and where the privacy policy lives. It returns deployment configuration only
(``ANUM_SIGN_IN_PROVIDERS``, ``ANUM_SELF_SIGN_UP``, ``ANUM_PRIVACY_POLICY_URL``), never
anything about a tenant or a user. A button the configuration offers but Keycloak does not
have simply lands on Keycloak's own sign-in page: Keycloak decides every sign-in.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from .settings import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

ProviderId = Literal["google", "apple"]
LABELS: dict[str, str] = {"google": "Google", "apple": "Apple"}


class SignInProvider(BaseModel):
    # The Keycloak identity provider alias the clients pass as kc_idp_hint.
    id: ProviderId
    label: str


class SignInOptions(BaseModel):
    identity_providers: list[SignInProvider]
    self_sign_up: bool
    privacy_policy_url: str | None


def current_sign_in_options() -> SignInOptions:
    names = [name for name in settings.sign_in_providers.split(",") if name]
    return SignInOptions(
        identity_providers=[SignInProvider(id=name, label=LABELS[name]) for name in names],  # type: ignore[arg-type]
        self_sign_up=settings.self_sign_up,
        privacy_policy_url=settings.privacy_policy_url.strip() or None,
    )


@router.get("/sign-in-options", response_model=SignInOptions)
async def sign_in_options(response: Response) -> SignInOptions:
    # Short public caching: a change in .env reaches clients within a minute.
    response.headers["Cache-Control"] = "public, max-age=60"
    return current_sign_in_options()
