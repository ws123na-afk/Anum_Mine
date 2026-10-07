"""Turn the development realm (infra/keycloak/anum-realm.json) into a shared-environment realm.

    python keycloak_realm.py <source realm.json> <output realm.json>

Used by infra/render/keycloak.Dockerfile at image build (docs/deploy-render.md). The
development realm seeds a `dev` user with a published password and allows only
localhost redirect URIs. A shared Keycloak gets:

* no users at all (the realm's `users` list is dropped; people are created in the
  admin console with their `tenant_id` attribute, docs/identity.md);
* the `anum-web` client's redirect URIs, web origins and post-logout redirects set to
  the placeholder ``${ANUM_WEB_ORIGIN}``, which Keycloak resolves from the container's
  environment when it imports the realm (`kc.sh start --import-realm`);
* the `anum-desktop` client without the Vite dev-server entries (`http://localhost:5173`),
  keeping the Tauri origins and the RFC 8252 loopback redirect (`http://127.0.0.1/*`);
* every other client, role, mapper and the user profile unchanged.

Standard library only, so it runs in a bare Python build stage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

WEB_ORIGIN_PLACEHOLDER = "${ANUM_WEB_ORIGIN}"
DEV_SERVER_PREFIXES = ("http://localhost:5173", "http://127.0.0.1:5173")


def _without_dev_server(values: list[str]) -> list[str]:
    return [value for value in values if not value.startswith(DEV_SERVER_PREFIXES)]


def shared_realm(realm: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``realm`` that is safe to import into a shared Keycloak."""
    result = json.loads(json.dumps(realm))
    result.pop("users", None)
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
