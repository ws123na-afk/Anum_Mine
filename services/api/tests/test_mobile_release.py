"""The Flutter app's sign-in redirect agrees with the deployed Keycloak realm.

The shipping mobile app (apps/mobile, docs/mobile.md) signs in through the `anum-flutter`
client with a private-use URI scheme redirect. Four places must agree or a store build
cannot finish sign-in: the realm (as deployed, infra/render/keycloak_realm.py), the app's
defaults (lib/features/auth/oidc.dart), the Android manifest placeholder that
flutter_appauth turns into the redirect intent filter, and the iOS `CFBundleURLTypes`.
The release workflow must also upload the bundle under the same applicationId and keep the
deferred desktop jobs from failing a mobile release. No network.
"""

from __future__ import annotations

import importlib.util
import json
import plistlib
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
REALM = REPO_ROOT / "infra" / "keycloak" / "anum-realm.json"
REALM_SCRIPT = REPO_ROOT / "infra" / "render" / "keycloak_realm.py"
MOBILE = REPO_ROOT / "apps" / "mobile"
GRADLE = MOBILE / "android" / "app" / "build.gradle.kts"
INFO_PLIST = MOBILE / "ios" / "Runner" / "Info.plist"
PBXPROJ = MOBILE / "ios" / "Runner.xcodeproj" / "project.pbxproj"
OIDC_DART = MOBILE / "lib" / "features" / "auth" / "oidc.dart"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-clients.yml"

CLIENT_ID = "anum-flutter"
APPLICATION_ID = "com.anum.app"
REDIRECT = f"{APPLICATION_ID}:/oauth2redirect"


def _deployed_realm() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("keycloak_realm", REALM_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.shared_realm(json.loads(REALM.read_text(encoding="utf-8")))


def _flutter_client(realm: dict[str, Any]) -> dict[str, Any]:
    clients = [client for client in realm["clients"] if client["clientId"] == CLIENT_ID]
    assert len(clients) == 1
    return clients[0]


def test_deployed_realm_registers_exactly_the_app_redirect() -> None:
    for realm in (_deployed_realm(), json.loads(REALM.read_text(encoding="utf-8"))):
        client = _flutter_client(realm)
        assert client["enabled"] is True
        assert client["publicClient"] is True
        assert client["standardFlowEnabled"] is True
        assert client["implicitFlowEnabled"] is False
        assert client["directAccessGrantsEnabled"] is False
        assert client["attributes"]["pkce.code.challenge.method"] == "S256"
        assert client["redirectUris"] == [REDIRECT]
        assert client["attributes"]["post.logout.redirect.uris"] == REDIRECT
        assert client["webOrigins"] == []
        uris = [*client["redirectUris"], client["attributes"]["post.logout.redirect.uris"]]
        # No wildcards and no "+" (Keycloak's "same as the redirect URIs" shorthand).
        assert all("*" not in uri and uri != "+" for uri in uris)


def test_app_defaults_use_the_realm_client_and_redirect() -> None:
    source = OIDC_DART.read_text(encoding="utf-8")
    assert f"this.clientId = '{CLIENT_ID}'" in source
    assert f"this.redirectUrl = '{REDIRECT}'" in source
    assert f"defaultValue: '{CLIENT_ID}'" in source
    assert f"defaultValue: '{REDIRECT}'" in source


def test_android_registers_the_redirect_scheme_under_the_application_id() -> None:
    gradle = GRADLE.read_text(encoding="utf-8")
    scheme = REDIRECT.split(":", 1)[0]
    assert re.findall(r'manifestPlaceholders\["appAuthRedirectScheme"\] = "([^"]+)"', gradle) == [
        scheme
    ]
    assert re.findall(r'applicationId = "([^"]+)"', gradle) == [APPLICATION_ID]


def test_ios_registers_the_redirect_scheme_under_the_bundle_identifier() -> None:
    plist = plistlib.loads(INFO_PLIST.read_bytes())
    schemes = [scheme for entry in plist["CFBundleURLTypes"] for scheme in entry["CFBundleURLSchemes"]]
    assert schemes == [REDIRECT.split(":", 1)[0]]
    # App Transport Security stays at its HTTPS-only default.
    assert "NSAppTransportSecurity" not in plist
    identifiers = set(re.findall(r"PRODUCT_BUNDLE_IDENTIFIER = ([^;]+);", PBXPROJ.read_text()))
    assert identifiers == {APPLICATION_ID, f"{APPLICATION_ID}.RunnerTests"}


def test_release_workflow_uploads_the_app_and_defers_desktop() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    play = [
        step
        for step in jobs["android"]["steps"]
        if str(step.get("uses", "")).startswith("r0adkll/upload-google-play@")
    ]
    assert len(play) == 1
    assert play[0]["with"]["packageName"] == APPLICATION_ID
    assert play[0]["with"]["track"] == "internal"
    # Desktop releases are deferred: their jobs are skipped, not failed, unless asked for.
    assert jobs["desktop"]["if"] == "needs.version.outputs.desktop == 'true'"
    assert "needs.version.outputs.desktop == 'true'" in jobs["publish"]["if"]
    for name in ("android", "ios"):
        assert "desktop" not in jobs[name].get("needs", [])
    for job in jobs.values():
        assert "continue-on-error" not in job
        for step in job.get("steps", []):
            assert "continue-on-error" not in step
