"""The Flutter app's sign-in redirect agrees with the deployed Keycloak realm.

The shipping mobile app (apps/mobile, docs/mobile.md) signs in through the `anum-flutter`
client with a private-use URI scheme redirect. Four places must agree or a store build
cannot finish sign-in: the realm (as deployed, infra/render/keycloak_realm.py), the app's
defaults (lib/features/auth/oidc.dart), the Android manifest placeholder that
flutter_appauth turns into the redirect intent filter, and the iOS `CFBundleURLTypes`.
The release workflow must also upload the bundle under the same applicationId, keep the
deferred desktop jobs from failing a mobile release, and fail with a clear message when a run
that uploads lacks a secret (.github/scripts/mobile-release-config.sh). No network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import re
import subprocess
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
    schemes = [
        scheme for entry in plist["CFBundleURLTypes"] for scheme in entry["CFBundleURLSchemes"]
    ]
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


PRODUCTION_API = "https://api.anum360os.com/"
PRODUCTION_ISSUER = "https://auth.anum360os.com/realms/anum"
PRODUCTION_WEB = "https://app.anum360os.com/"
APP_DART = MOBILE / "lib" / "src" / "anum_app.dart"


def test_release_builds_default_to_the_production_hosts() -> None:
    # Without dart-defines a release build uses the owner's domain and Keycloak, never the
    # emulator address or the development sign-in (docs/mobile.md, "Release builds").
    assert f"const productionApiUrl = '{PRODUCTION_API}';" in APP_DART.read_text(encoding="utf-8")
    assert f"const productionOidcIssuer = '{PRODUCTION_ISSUER}';" in OIDC_DART.read_text(
        encoding="utf-8"
    )


def test_mobile_release_jobs_fall_back_to_the_production_hosts() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for name in ("android", "android-apk", "ios"):
        env = workflow["jobs"][name]["env"]
        assert env["PRODUCTION_API_URL"].endswith(f"|| '{PRODUCTION_API}' }}}}"), name
        assert env["PRODUCTION_OIDC_ISSUER"].endswith(f"|| '{PRODUCTION_ISSUER}' }}}}"), name
        assert env["PRODUCTION_WEB_APP_URL"].endswith(f"|| '{PRODUCTION_WEB}' }}}}"), name


def test_installable_apk_is_a_production_release_build() -> None:
    job = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["android-apk"]
    build = next(step for step in job["steps"] if step.get("name") == "Build the release APK")
    assert "flutter build apk --release" in build["run"]
    assert '--dart-define-from-file="$RUNNER_TEMP/production.json"' in build["run"]
    upload = next(step for step in job["steps"] if step.get("name") == "Upload the APK")
    assert upload["with"]["name"] == "anum-android-apk"
    # The decoded keystore never outlives the job.
    cleanup = next(
        step for step in job["steps"] if step.get("name") == "Remove the decoded keystore"
    )
    assert cleanup["if"] == "always()"


RELEASE_CONFIG_SCRIPT = REPO_ROOT / ".github" / "scripts" / "mobile-release-config.sh"
ANDROID_SIGNING = (
    "ANUM_ANDROID_KEYSTORE_BASE64",
    "ANUM_ANDROID_KEYSTORE_PASSWORD",
    "ANUM_ANDROID_KEY_ALIAS",
    "ANUM_ANDROID_KEY_PASSWORD",
)
IOS_SIGNING = (
    "APPLE_TEAM_ID",
    "APP_STORE_CONNECT_API_KEY_ID",
    "APP_STORE_CONNECT_API_ISSUER_ID",
    "APP_STORE_CONNECT_API_KEY_P8",
)
PRODUCTION_URLS = {
    "ANUM_PRODUCTION_API_URL": "https://api.example.invalid/",
    "ANUM_PRODUCTION_OIDC_ISSUER": "https://id.example.invalid/realms/anum",
}
# Plainly fake placeholders; the script only checks that a value is present.
PLACEHOLDER = "placeholder"


def _release_config(
    platform: str, tmp_path: Path, *, upload: bool, values: dict[str, str]
) -> tuple[int, str, dict[str, str]]:
    output = tmp_path / f"{platform}-{upload}-{len(values)}.out"
    output.write_text("", encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GITHUB_OUTPUT": str(output)}
    env["UPLOAD"] = "true" if upload else "false"
    env.update(values)
    result = subprocess.run(  # noqa: S603 - fixed script, test-controlled environment
        ["bash", str(RELEASE_CONFIG_SCRIPT), platform],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(
        line.split("=", 1)
        for line in output.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return result.returncode, result.stdout, outputs


def test_release_jobs_check_their_configuration_with_the_shared_script() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job, platform in (("android", "android"), ("ios", "ios")):
        steps = workflow["jobs"][job]["steps"]
        config = next(step for step in steps if step.get("id") == "config")
        assert config["run"] == f"bash .github/scripts/mobile-release-config.sh {platform}"
        assert config["env"]["UPLOAD"] == "${{ needs.version.outputs.upload }}"
        # The checkout that provides the script comes first.
        assert steps.index(config) > next(
            index
            for index, step in enumerate(steps)
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )


def test_build_only_run_without_credentials_passes_with_a_notice(tmp_path: Path) -> None:
    code, stdout, outputs = _release_config("android", tmp_path, upload=False, values={})
    assert code == 0
    assert "::notice title=Android release skipped::" in stdout
    assert outputs == {"build": "false", "play": "false"}
    code, stdout, outputs = _release_config("ios", tmp_path, upload=False, values={})
    assert code == 0
    assert "::notice title=iOS signing skipped::" in stdout
    assert outputs == {"signed": "false", "upload": "false"}


def test_upload_run_without_credentials_fails_naming_every_missing_value(tmp_path: Path) -> None:
    code, stdout, _ = _release_config("android", tmp_path, upload=True, values={})
    assert code == 1
    assert "::error title=Android release not configured::" in stdout
    for name in (*ANDROID_SIGNING, "PLAY_SERVICE_ACCOUNT_JSON", *PRODUCTION_URLS):
        assert name in stdout
    code, stdout, _ = _release_config("ios", tmp_path, upload=True, values={})
    assert code == 1
    assert "::error title=iOS release not configured::" in stdout
    for name in (*IOS_SIGNING, *PRODUCTION_URLS):
        assert name in stdout


def test_upload_run_names_only_what_is_missing(tmp_path: Path) -> None:
    values = {name: PLACEHOLDER for name in ANDROID_SIGNING} | PRODUCTION_URLS
    code, stdout, _ = _release_config("android", tmp_path, upload=True, values=values)
    assert code == 1
    assert "PLAY_SERVICE_ACCOUNT_JSON (secret)" in stdout
    assert "ANUM_ANDROID_KEY_ALIAS" not in stdout
    # No value is ever printed.
    assert PLACEHOLDER not in stdout


def test_partly_set_signing_secrets_fail_even_without_upload(tmp_path: Path) -> None:
    for platform, signing in (("android", ANDROID_SIGNING), ("ios", IOS_SIGNING)):
        values = {name: PLACEHOLDER for name in signing[:-1]} | PRODUCTION_URLS
        code, stdout, _ = _release_config(platform, tmp_path, upload=False, values=values)
        assert code == 1, platform
        assert "signing incomplete::" in stdout
        assert signing[-1] in stdout
        assert PLACEHOLDER not in stdout


def test_a_non_https_production_url_fails(tmp_path: Path) -> None:
    values = PRODUCTION_URLS | {"ANUM_PRODUCTION_API_URL": "http://api.example.invalid/"}
    code, stdout, _ = _release_config("android", tmp_path, upload=False, values=values)
    assert code == 1
    assert "ANUM_PRODUCTION_API_URL variable must start with https://" in stdout


def test_complete_configuration_builds_signed_and_uploads_when_asked(tmp_path: Path) -> None:
    android = {name: PLACEHOLDER for name in ANDROID_SIGNING} | PRODUCTION_URLS
    code, _, outputs = _release_config(
        "android",
        tmp_path,
        upload=True,
        values=android | {"PLAY_SERVICE_ACCOUNT_JSON": PLACEHOLDER},
    )
    assert (code, outputs) == (0, {"build": "true", "play": "true"})
    code, _, outputs = _release_config("android", tmp_path, upload=False, values=android)
    assert (code, outputs) == (0, {"build": "true", "play": "false"})
    ios = {name: PLACEHOLDER for name in IOS_SIGNING} | PRODUCTION_URLS
    code, _, outputs = _release_config("ios", tmp_path, upload=True, values=ios)
    assert (code, outputs) == (0, {"signed": "true", "upload": "true"})
