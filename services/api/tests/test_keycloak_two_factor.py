"""Two-factor sign-in for the shared `anum` realm (docs/identity.md#two-factor-sign-in).

infra/keycloak/two_factor.py converges a running Keycloak to the anum-browser-2fa flow
and the OTP and passkey policies through kcadm; infra/oracle/keycloak-2fa.sh runs it in
the Oracle VM's Keycloak container. These tests drive it against an in-memory stand-in
for Keycloak 26.4's admin API (the shape of the built-in browser flow, flow copies,
partial realm updates) and check the wrapper's secret handling. No network, no Docker.
The same code was run against a real Keycloak 26.4.0 when it was written.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TWO_FACTOR = REPO_ROOT / "infra" / "keycloak" / "two_factor.py"
REALM_SCRIPT = REPO_ROOT / "infra" / "render" / "keycloak_realm.py"
REALM = REPO_ROOT / "infra" / "keycloak" / "anum-realm.json"
WRAPPER = REPO_ROOT / "infra" / "oracle" / "keycloak-2fa.sh"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


tf = _load("anum_two_factor", TWO_FACTOR)

PASSWORDLESS_DEFAULTS = {
    "webAuthnPolicyPasswordlessRpEntityName": "keycloak",
    "webAuthnPolicyPasswordlessRpId": "",
    "webAuthnPolicyPasswordlessUserVerificationRequirement": "not specified",
    "webAuthnPolicyPasswordlessRequireResidentKey": "not specified",
    "webAuthnPolicyPasswordlessSignatureAlgorithms": ["ES256"],
    "webAuthnPolicyPasswordlessPasskeysEnabled": False,
}
NAMES = {
    "auth-cookie": "Cookie",
    "auth-spnego": "Kerberos",
    "identity-provider-redirector": "Identity Provider Redirector",
    "conditional-user-configured": "Condition - user configured",
    "organization": "Organization Identity-First Login",
    "auth-username-password-form": "Username Password Form",
    "conditional-credential": "Condition - credential",
    "conditional-sub-flow-executed": "Condition - sub-flow executed",
    "auth-otp-form": "OTP Form",
    "webauthn-authenticator": "WebAuthn Authenticator",
    "auth-recovery-authn-code-form": "Recovery Authentication Code Form",
}


class FakeKeycloak:
    """Enough of Keycloak 26.4's admin REST API, reached through kcadm argv, for two_factor.py."""

    def __init__(self) -> None:
        self.realm: dict[str, Any] = {
            "realm": "anum",
            "browserFlow": "browser",
            "otpPolicyType": "totp",
            "otpPolicyAlgorithm": "HmacSHA1",
            "otpPolicyDigits": 6,
            "otpPolicyPeriod": 30,
            "otpPolicyLookAheadWindow": 1,
            "otpPolicyCodeReusable": False,
            **copy.deepcopy(PASSWORDLESS_DEFAULTS),
        }
        self.required_actions = [
            {"alias": alias, "name": alias, "enabled": True, "defaultAction": False}
            for alias in (
                "CONFIGURE_TOTP",
                "UPDATE_PASSWORD",
                "webauthn-register-passwordless",
                "CONFIGURE_RECOVERY_AUTHN_CODES",
            )
        ]
        self.flows: dict[str, dict[str, Any]] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.users = {"u1": {"id": "u1", "username": "shahzeb"}}
        self.credentials = {
            "u1": [
                {"id": "c1", "type": "password"},
                {"id": "c2", "type": "otp", "userLabel": "phone"},
                {"id": "c3", "type": "recovery-authn-codes"},
                {"id": "c4", "type": "webauthn-passwordless"},
            ]
        }
        self.lockouts_cleared: list[str] = []
        self.calls: list[list[str]] = []
        self._browser_flow()

    # --- model -------------------------------------------------------------------------
    def _flow(self, alias: str, top: bool = False) -> dict[str, Any]:
        flow = {"id": str(uuid.uuid4()), "alias": alias, "topLevel": top, "executions": []}
        self.flows[flow["id"]] = flow
        return flow

    def _add(
        self,
        flow: dict[str, Any],
        requirement: str,
        provider: str = "",
        subflow: dict[str, Any] | None = None,
        config: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        execution = {
            "id": str(uuid.uuid4()),
            "requirement": requirement,
            "provider": provider,
            "flow": subflow["id"] if subflow else None,
            "config": None,
        }
        if config is not None:
            config_id = str(uuid.uuid4())
            self.configs[config_id] = {
                "id": config_id,
                "alias": f"cfg-{config_id[:8]}",
                "config": config,
            }
            execution["config"] = config_id
        flow["executions"].append(execution)
        return execution

    def _browser_flow(self) -> None:
        browser = self._flow("browser", top=True)
        self._add(browser, "ALTERNATIVE", "auth-cookie")
        self._add(browser, "DISABLED", "auth-spnego")
        self._add(browser, "ALTERNATIVE", "identity-provider-redirector")
        organization = self._flow("Organization")
        self._add(browser, "ALTERNATIVE", subflow=organization)
        conditional_org = self._flow("Browser - Conditional Organization")
        self._add(organization, "CONDITIONAL", subflow=conditional_org)
        self._add(conditional_org, "REQUIRED", "conditional-user-configured")
        self._add(conditional_org, "ALTERNATIVE", "organization")
        forms = self._flow("forms")
        self._add(browser, "ALTERNATIVE", subflow=forms)
        self._add(forms, "REQUIRED", "auth-username-password-form")
        second = self._flow("Browser - Conditional 2FA")
        self._add(forms, "CONDITIONAL", subflow=second)
        self._add(second, "REQUIRED", "conditional-user-configured")
        self._add(
            second,
            "REQUIRED",
            "conditional-credential",
            config={"credentials": "webauthn-passwordless"},
        )
        self._add(second, "ALTERNATIVE", "auth-otp-form")
        self._add(second, "DISABLED", "webauthn-authenticator")
        self._add(second, "DISABLED", "auth-recovery-authn-code-form")

    def _by_alias(self, alias: str) -> dict[str, Any]:
        matches = [flow for flow in self.flows.values() if flow["alias"] == alias]
        if not matches:
            raise LookupError(f"flow {alias} not found")
        return matches[0]

    def _copy(self, flow: dict[str, Any], new_alias: str, prefix: str) -> dict[str, Any]:
        clone = self._flow(new_alias, top=flow["topLevel"])
        for execution in flow["executions"]:
            sub = None
            if execution["flow"]:
                original = self.flows[execution["flow"]]
                sub = self._copy(original, f"{prefix} {original['alias']}", prefix)
            config = self.configs[execution["config"]]["config"] if execution["config"] else None
            self._add(
                clone, execution["requirement"], execution["provider"], sub, copy.deepcopy(config)
            )
        return clone

    def _delete_flow(self, flow_id: str) -> None:
        for execution in self.flows[flow_id]["executions"]:
            if execution["flow"]:
                self._delete_flow(execution["flow"])
        del self.flows[flow_id]

    def listing(self, flow: dict[str, Any], level: int = 0) -> list[dict[str, Any]]:
        rows = []
        for index, execution in enumerate(flow["executions"]):
            row = {
                "id": execution["id"],
                "requirement": execution["requirement"],
                "level": level,
                "index": index,
                "priority": index * 10,
            }
            if execution["flow"]:
                sub = self.flows[execution["flow"]]
                row.update(displayName=sub["alias"], authenticationFlow=True, flowId=sub["id"])
                rows.append(row)
                rows.extend(self.listing(sub, level + 1))
            else:
                row.update(
                    displayName=NAMES[execution["provider"]], providerId=execution["provider"]
                )
                if execution["config"]:
                    row["authenticationConfig"] = execution["config"]
                rows.append(row)
        return rows

    def _execution(self, execution_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for flow in self.flows.values():
            for execution in flow["executions"]:
                if execution["id"] == execution_id:
                    return flow, execution
        raise LookupError(execution_id)

    # --- kcadm ---------------------------------------------------------------------------
    def __call__(
        self, argv: list[str], input: str | None = None, **_: Any
    ) -> subprocess.CompletedProcess[str]:
        assert argv[:1] == ["kcadm"]
        self.calls.append(argv)
        args = argv[1:]
        verb, path = args[0], args[1]
        query = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args) if arg == "-q")
        assert args[args.index("-r") + 1] == "anum"
        body = json.loads(input) if input else None
        try:
            out = self.handle(verb, path, query, body)
        except LookupError as error:
            return subprocess.CompletedProcess(argv, 1, "", f"Resource not found: {error}\n")
        return subprocess.CompletedProcess(
            argv, 0, out if isinstance(out, str) else json.dumps(out), ""
        )

    def handle(self, verb: str, path: str, query: dict[str, str], body: Any) -> Any:
        parts = path.split("/")
        if path == "realms/anum":
            if verb == "get":
                return self.realm
            # Keycloak rebuilds the passwordless policy from the request: fields left out reset.
            for key, value in PASSWORDLESS_DEFAULTS.items():
                if key not in body:
                    self.realm[key] = copy.deepcopy(value)
            self.realm.update(body)
            return ""
        if path == "authentication/required-actions":
            return self.required_actions
        if parts[:2] == ["authentication", "required-actions"] and verb == "update":
            action = next(a for a in self.required_actions if a["alias"] == parts[2])
            action.update(body)
            return ""
        if path == "authentication/flows":
            return [
                {"id": f["id"], "alias": f["alias"]} for f in self.flows.values() if f["topLevel"]
            ]
        if parts[:2] == ["authentication", "flows"] and len(parts) == 3:
            flow = self.flows.get(parts[2])
            if flow is None:
                raise LookupError(parts[2])
            return {"id": flow["id"], "alias": flow["alias"]}
        if parts[:2] == ["authentication", "flows"] and parts[-1] == "copy":
            source = self._by_alias(parts[2])
            self._copy(source, body["newName"], body["newName"])
            return ""
        if parts[:2] == ["authentication", "flows"] and parts[3:] == ["executions"]:
            flow = self._by_alias(parts[2].replace("%20", " "))
            if verb == "get":
                return self.listing(flow)
            _, execution = self._execution(body["id"])
            execution["requirement"] = body["requirement"]
            return ""
        if parts[:2] == ["authentication", "flows"] and parts[3:5] == ["executions", "flow"]:
            parent = self._by_alias(parts[2].replace("%20", " "))
            if any(f["alias"] == body["alias"] for f in self.flows.values()):
                raise LookupError(f"alias {body['alias']} already exists")
            self._add(parent, "DISABLED", subflow=self._flow(body["alias"]))
            return ""
        if parts[:2] == ["authentication", "flows"] and parts[3:5] == ["executions", "execution"]:
            parent = self._by_alias(parts[2].replace("%20", " "))
            return self._add(parent, "DISABLED", body["provider"])["id"] + "\n"
        if parts[:2] == ["authentication", "executions"] and len(parts) == 3 and verb == "delete":
            flow, execution = self._execution(parts[2])
            flow["executions"].remove(execution)
            if execution["flow"]:
                self._delete_flow(execution["flow"])
            return ""
        if parts[:2] == ["authentication", "executions"] and parts[3:] == ["config"]:
            _, execution = self._execution(parts[2])
            config_id = str(uuid.uuid4())
            self.configs[config_id] = {"id": config_id, **body}
            execution["config"] = config_id
            return config_id
        if parts[:2] == ["authentication", "config"]:
            if verb == "get":
                return self.configs[parts[2]]
            self.configs[parts[2]].update(body)
            return ""
        if path == "users":
            return [u for u in self.users.values() if u["username"] == query["username"]]
        if parts[0] == "users" and parts[2:] == ["credentials"]:
            return self.credentials[parts[1]]
        if parts[0] == "users" and parts[2] == "credentials" and verb == "delete":
            self.credentials[parts[1]] = [
                c for c in self.credentials[parts[1]] if c["id"] != parts[3]
            ]
            return ""
        if parts[:3] == ["attack-detection", "brute-force", "users"]:
            self.lockouts_cleared.append(parts[3])
            return ""
        raise AssertionError(f"unexpected kcadm {verb} {path}")

    def second_factor_tree(self) -> list[tuple[int, str, str, dict[str, str]]]:
        rows = self.listing(self._by_alias(tf.FLOW_ALIAS))
        start = next(i for i, r in enumerate(rows) if r.get("displayName") == tf.SECOND_FACTOR)
        base = rows[start]["level"]
        tree = []
        for row in rows[start:]:
            if row["level"] < base or (row["level"] == base and row is not rows[start]):
                break
            config = (
                self.configs[row["authenticationConfig"]]["config"]
                if row.get("authenticationConfig")
                else {}
            )
            name = row["displayName"] if row.get("authenticationFlow") else row["providerId"]
            tree.append((row["level"] - base, name, row["requirement"], config))
        return tree


EXPECTED_TREE = [
    (0, "anum-2fa", "CONDITIONAL", {}),
    (
        1,
        "conditional-credential",
        "REQUIRED",
        {"credentials": "webauthn-passwordless", "included": "false"},
    ),
    (1, "anum-2fa-verify", "CONDITIONAL", {}),
    (2, "conditional-user-configured", "REQUIRED", {}),
    (2, "auth-otp-form", "ALTERNATIVE", {}),
    (2, "auth-recovery-authn-code-form", "ALTERNATIVE", {}),
    (1, "anum-2fa-enrol", "CONDITIONAL", {}),
    (
        2,
        "conditional-sub-flow-executed",
        "REQUIRED",
        {"flow_to_check": "anum-2fa-verify", "check_result": "not-executed"},
    ),
    (2, "auth-otp-form", "REQUIRED", {}),
]


@pytest.fixture
def keycloak() -> FakeKeycloak:
    return FakeKeycloak()


def run_apply(keycloak: FakeKeycloak, rp_id: str = "auth.example.test") -> list[str]:
    log: list[str] = []
    tf.apply(tf.Kcadm(["kcadm"], runner=keycloak), rp_id, log=log.append)
    return log


def test_apply_binds_a_flow_that_asks_every_password_sign_in_for_a_second_factor(
    keycloak: FakeKeycloak,
) -> None:
    run_apply(keycloak)
    assert keycloak.realm["browserFlow"] == "anum-browser-2fa"
    assert keycloak.second_factor_tree() == EXPECTED_TREE
    forms = next(f for f in keycloak.flows.values() if f["alias"] == "anum-browser-2fa forms")
    providers = [e["provider"] or keycloak.flows[e["flow"]]["alias"] for e in forms["executions"]]
    # Keycloak's own "Conditional 2FA" (which skips users without OTP) is gone from the copy.
    assert providers == ["auth-username-password-form", "anum-2fa"]
    # The built-in browser flow is left as Keycloak shipped it.
    browser_rows = keycloak.listing(keycloak._by_alias("browser"))
    assert any(r.get("displayName") == "Browser - Conditional 2FA" for r in browser_rows)


def test_apply_sets_authenticator_app_and_passkey_policies(keycloak: FakeKeycloak) -> None:
    run_apply(keycloak, "auth.example.test")
    realm = keycloak.realm
    assert (
        realm["otpPolicyType"],
        realm["otpPolicyAlgorithm"],
        realm["otpPolicyDigits"],
        realm["otpPolicyPeriod"],
    ) == ("totp", "HmacSHA1", 6, 30)
    assert realm["otpPolicyCodeReusable"] is False
    assert realm["webAuthnPolicyPasswordlessRpId"] == "auth.example.test"
    assert realm["webAuthnPolicyPasswordlessUserVerificationRequirement"] == "required"
    assert realm["webAuthnPolicyPasswordlessRequireResidentKey"] == "Yes"
    assert realm["webAuthnPolicyPasswordlessPasskeysEnabled"] is True
    actions = {a["alias"]: a for a in keycloak.required_actions}
    assert actions["CONFIGURE_TOTP"]["enabled"] and actions["CONFIGURE_TOTP"]["defaultAction"]
    assert actions["webauthn-register-passwordless"]["enabled"]
    assert actions["CONFIGURE_RECOVERY_AUTHN_CODES"]["enabled"]


def test_status_reports_the_bound_flow_and_the_passkey_policy(keycloak: FakeKeycloak) -> None:
    run_apply(keycloak)

    lines = tf.status(tf.Kcadm(["kcadm"], runner=keycloak))

    assert lines[0] == f"browser flow: {tf.FLOW_ALIAS}"
    assert any(line.startswith("passkeys: offered (relying party 'auth.example.test'") for line in lines)
    assert f"{tf.FLOW_ALIAS}:" in lines


def test_apply_is_idempotent(keycloak: FakeKeycloak) -> None:
    run_apply(keycloak)
    before = (copy.deepcopy(keycloak.realm), keycloak.second_factor_tree(), set(keycloak.flows))
    keycloak.calls.clear()
    log = run_apply(keycloak)
    assert log == ["two-factor sign-in is configured"]
    assert all(call[1] == "get" for call in keycloak.calls), "a second apply must only read"
    assert (keycloak.realm, keycloak.second_factor_tree(), set(keycloak.flows)) == before


def test_apply_repairs_a_drifted_flow(keycloak: FakeKeycloak) -> None:
    run_apply(keycloak)
    # Someone made the enrolment step optional in the admin console.
    enrol = keycloak._by_alias("anum-2fa-enrol")
    enrol["executions"][1]["requirement"] = "DISABLED"
    run_apply(keycloak)
    assert keycloak.second_factor_tree() == EXPECTED_TREE
    assert len([f for f in keycloak.flows.values() if f["alias"] == "anum-2fa"]) == 1


def test_realm_is_updated_with_the_full_representation(keycloak: FakeKeycloak) -> None:
    # A partial update would reset the passkey fields it leaves out (the fake does what
    # Keycloak 26.4 does); after binding the flow the relying party must still be set.
    run_apply(keycloak, "auth.example.test")
    assert keycloak.realm["webAuthnPolicyPasswordlessRpId"] == "auth.example.test"
    assert keycloak.realm["webAuthnPolicyPasswordlessPasskeysEnabled"] is True


@pytest.mark.parametrize("rp_id", ["", "https://auth.example.test", "auth.example.test:443", "a b"])
def test_apply_refuses_anything_but_a_host_name(keycloak: FakeKeycloak, rp_id: str) -> None:
    with pytest.raises(tf.KcadmError):
        run_apply(keycloak, rp_id)
    assert keycloak.realm["browserFlow"] == "browser"


def test_apply_stops_on_a_keycloak_without_the_required_actions(keycloak: FakeKeycloak) -> None:
    keycloak.required_actions = [
        a for a in keycloak.required_actions if a["alias"] != "CONFIGURE_RECOVERY_AUTHN_CODES"
    ]
    with pytest.raises(tf.KcadmError, match="26.4"):
        run_apply(keycloak)
    assert keycloak.realm["browserFlow"] == "browser"


def test_reset_user_removes_only_the_second_factor_and_lifts_the_lockout(
    keycloak: FakeKeycloak,
) -> None:
    log: list[str] = []
    tf.reset_user(tf.Kcadm(["kcadm"], runner=keycloak), "shahzeb", log=log.append)
    assert [c["type"] for c in keycloak.credentials["u1"]] == ["password", "webauthn-passwordless"]
    assert keycloak.lockouts_cleared == ["u1"]
    with pytest.raises(tf.KcadmError):
        tf.reset_user(tf.Kcadm(["kcadm"], runner=keycloak), "nobody")


def test_bodies_travel_on_stdin_and_the_config_file_is_passed(keycloak: FakeKeycloak) -> None:
    kcadm = tf.Kcadm(
        ["kcadm"],
        config="/tmp/kcadm.config",
        runner=lambda argv, **kw: keycloak(
            [a for a in argv if a not in ("--config", "/tmp/kcadm.config")], **kw
        ),
    )
    seen: list[list[str]] = []
    original = kcadm.runner

    def spy(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return original(argv, **kwargs)

    kcadm.runner = spy
    tf.apply(kcadm, "auth.example.test", log=lambda _: None)
    assert seen and all(argv[argv.index("--config") + 1] == "/tmp/kcadm.config" for argv in seen)
    # kcadm takes options after the sub-command only.
    assert all(argv.index("--config") > 2 for argv in seen)
    assert not [argv for argv in seen if any(arg.startswith("{") for arg in argv)]


# --------------------------------------------------------------------------- realm import


def test_shared_realm_carries_the_same_two_factor_policy() -> None:
    realm_script = _load("anum_keycloak_realm", REALM_SCRIPT)
    shared = realm_script.shared_realm(json.loads(REALM.read_text(encoding="utf-8")))
    assert {**tf.OTP_POLICY, **tf.PASSKEY_POLICY} == realm_script.TWO_FACTOR_POLICY
    for key, value in realm_script.TWO_FACTOR_POLICY.items():
        assert shared[key] == value, key
    # Relying party left to Keycloak's host name at import; two_factor.py pins it.
    assert "webAuthnPolicyPasswordlessRpId" not in shared
    # A realm file that lists flows must list all of them: the flow is added after import.
    assert "authenticationFlows" not in shared and "browserFlow" not in shared
    assert "requiredActions" not in shared


def test_development_realm_stays_password_only() -> None:
    # Local compose runs Keycloak 25 and the authenticated journey posts the password
    # form alone (services/api/scripts/journey.py): no 2FA in the development realm.
    realm = json.loads(REALM.read_text(encoding="utf-8"))
    assert not any(key.startswith(("otpPolicy", "webAuthnPolicy")) for key in realm)
    assert "authenticationFlows" not in realm and "browserFlow" not in realm


def test_public_clients_keep_authorization_code_with_pkce() -> None:
    # The second factor happens on Keycloak's hosted pages; clients need no change.
    clients = {c["clientId"]: c for c in json.loads(REALM.read_text(encoding="utf-8"))["clients"]}
    for client_id in ("anum-web", "anum-desktop", "anum-android", "anum-flutter"):
        client = clients[client_id]
        assert client["publicClient"] and client["standardFlowEnabled"], client_id
        assert not client.get("directAccessGrantsEnabled"), client_id
        assert client["attributes"]["pkce.code.challenge.method"] == "S256", client_id


# --------------------------------------------------------------------------- wrapper


def test_wrapper_never_puts_a_secret_on_a_command_line() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n") and "set -euo pipefail" in text
    assert "--password" not in text and "--secret " not in text
    assert 'read -r -s -p "Password for' in text
    # Secrets go to the container on stdin and become kcadm's documented variables there.
    assert re.search(r'printf .%s\\n. "\$password" \| with_secret KC_CLI_PASSWORD', text)
    assert re.search(r'printf .%s\\n. "\$secret" \| with_secret KC_CLI_CLIENT_SECRET', text)
    assert "--client-secret:env ANUM_TEMPORARY_ADMIN_SECRET" in text
    assert not re.search(r"echo[^\n]*\$(password|secret)", text)
    assert "unset password" in text and "unset secret" in text
    # The token file is removed and a temporary admin deleted whatever happens.
    assert "trap cleanup EXIT" in text and 'rm -f "$KCADM_CONFIG"' in text
    assert 'delete "clients/$id" -r master' in text


def test_wrapper_needs_no_sudo_and_uses_the_auth_host_as_relying_party() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "sudo" not in text.replace("no sudo", "")
    assert 'AUTH_HOST="$(env_value ANUM_AUTH_HOST)"' in text
    assert '--rp-id "$AUTH_HOST"' in text
    assert "../keycloak/two_factor.py" in text
