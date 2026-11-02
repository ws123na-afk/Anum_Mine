"""Turn the development realm (infra/keycloak/anum-realm.json) into a shared-environment realm.

    python keycloak_realm.py <source realm.json> <output realm.json>

Used by infra/render/keycloak.Dockerfile at image build (docs/deploy-render.md). The
development realm seeds a `dev` user with a published password and allows only
localhost redirect URIs. A shared Keycloak gets:

* the display name `ANUM` (the development realm's "ANUM (local development)" makes the login theme
  show its non-production notice);
* no users at all (the realm's `users` list is dropped; people are created in the
  admin console with their `tenant_id` attribute, docs/identity.md);
* the `anum-web` client's redirect URIs, web origins and post-logout redirects set to
  the placeholder ``${ANUM_WEB_ORIGIN}``, which Keycloak resolves from the container's
  environment when it imports the realm (`kc.sh start --import-realm`);
* the `anum-desktop` client without the Vite dev-server entries (`http://localhost:5173`),
  keeping the Tauri origins and the RFC 8252 loopback redirect (`http://127.0.0.1/*`);
* the two-factor policies of docs/identity.md#two-factor-sign-in: TOTP (HmacSHA1,
  6 digits, 30 seconds) and passkeys offered on the sign-in page with user verification
  required. The relying party id stays empty (Keycloak then uses its own host name, the
  auth host). The values are the ones ``infra/keycloak/two_factor.py`` applies to a
  running server; a test keeps the two equal;
* the password rules and sign-in lockout of ``SIGN_IN_PROTECTION`` (12 characters with
  mixed case and a digit, not the user name or email, no reuse of the last 3; a temporary
  lockout after 10 failed sign-ins, growing to 15 minutes);
* every other client, role, mapper and the user profile unchanged.

The 2FA browser flow itself is not part of the import: a realm file that lists any
authentication flow must list all of them, so the flow is added by
``infra/keycloak/two_factor.py`` (``infra/oracle/keycloak-2fa.sh``) after the import.

Standard library only, so it runs in a bare Python build stage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

WEB_ORIGIN_PLACEHOLDER = "${ANUM_WEB_ORIGIN}"
DEV_SERVER_PREFIXES = ("http://localhost:5173", "http://127.0.0.1:5173")

# Kept equal to infra/keycloak/two_factor.py (OTP_POLICY, PASSKEY_POLICY) by
# services/api/tests/test_render_blueprint.py; this file runs alone in the image build.
TWO_FACTOR_POLICY: dict[str, Any] = {
    "otpPolicyType": "totp",
    "otpPolicyAlgorithm": "HmacSHA1",
    "otpPolicyDigits": 6,
    "otpPolicyPeriod": 30,
    "otpPolicyLookAheadWindow": 1,
    "otpPolicyCodeReusable": False,
    "webAuthnPolicyPasswordlessRpEntityName": "ANUM",
    "webAuthnPolicyPasswordlessUserVerificationRequirement": "required",
    "webAuthnPolicyPasswordlessRequireResidentKey": "Yes",
    "webAuthnPolicyPasswordlessSignatureAlgorithms": ["ES256", "RS256"],
    "webAuthnPolicyPasswordlessPasskeysEnabled": True,
}


# Password rules and sign-in lockout of a shared realm (docs/security.md#sign-in-protection).
# Keycloak's own defaults are no password rules at all and a lockout only after 30 failed
# attempts. Equal to infra/keycloak/two_factor.py SIGN_IN_PROTECTION (applied to a running
# server) and social_login.REALM_PASSWORD_RULES; a test keeps the three equal.
SIGN_IN_PROTECTION: dict[str, Any] = {
    "passwordPolicy": (
        "length(12) and maxLength(128) and lowerCase(1) and upperCase(1) and digits(1) "
        "and notUsername and notEmail and passwordHistory(3)"
    ),
    "bruteForceProtected": True,
    "permanentLockout": False,
    "failureFactor": 10,
    "waitIncrementSeconds": 60,
    "maxFailureWaitSeconds": 900,
    "maxDeltaTimeSeconds": 43200,
    "quickLoginCheckMilliSeconds": 1000,
    "minimumQuickLoginWaitSeconds": 60,
}


def _without_dev_server(values: list[str]) -> list[str]:
    return [value for value in values if not value.startswith(DEV_SERVER_PREFIXES)]


def shared_realm(realm: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``realm`` that is safe to import into a shared Keycloak."""
    result = json.loads(json.dumps(realm))
    result.pop("users", None)
    # The sign-in pages show the realm's name; "(local development)" belongs to the dev realm only
    # (the ANUM login theme shows its non-production notice from that text).
    result["displayName"] = "ANUM"
    result.update(json.loads(json.dumps(TWO_FACTOR_POLICY)))
    result.update(json.loads(json.dumps(SIGN_IN_PROTECTION)))
    for client in result.get("clients", []):
        if client.get("clientId") not in {"anum-web", "anum-desktop"}:
            continue
        attributes = client.setdefault("attributes", {})
        if client.get("clientId") == "anum-web":
            client["redirectUris"] = [f"{WEB_ORIGIN_PLACEHOLDER}/*"]
            client["webOrigins"] = [WEB_ORIGIN_PLACEHOLDER]
            attributes["post.logout.redirect.uris"] = f"{WEB_ORIGIN_PLACEHOLDER}/*"
        elif client.get("clientId") == "anum-desktop":
            client["redirectUris"] = _without_dev_server(client.get("redirectUris", []))
            client["webOrigins"] = _without_dev_server(client.get("webOrigins", []))
            logout = attributes.get("post.logout.redirect.uris")
            if logout:
                attributes["post.logout.redirect.uris"] = "##".join(
                    _without_dev_server(logout.split("##"))
                )
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    source, target = (Path(arg) for arg in argv)
    realm = json.loads(source.read_text(encoding="utf-8"))
    target.write_text(json.dumps(shared_realm(realm), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
