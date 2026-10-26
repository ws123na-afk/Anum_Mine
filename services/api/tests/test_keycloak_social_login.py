"""Google and Apple sign-in, self sign-up and pending accounts on the `anum` realm.

infra/keycloak/social_login.py converges a running Keycloak through kcadm; the Oracle
wrapper (infra/oracle/keycloak-2fa.sh) runs it in the Keycloak container. These tests
drive it against the in-memory stand-in for Keycloak 26.4's admin API from
test_keycloak_two_factor.py, extended with identity providers, top-level flows, users,
localization texts and the registration flow, and check the wrapper's secret handling.
No network, no Docker. The flows, provider and sign-up steps were also run against a
real Keycloak 26.4.0 (docs/identity.md#google-and-apple-sign-in).
"""

from __future__ import annotations

import copy
import importlib.util
import io
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOCIAL = REPO_ROOT / "infra" / "keycloak" / "social_login.py"
WRAPPER = REPO_ROOT / "infra" / "oracle" / "keycloak-2fa.sh"
THEME = REPO_ROOT / "infra" / "keycloak" / "themes" / "anum" / "login"
DOCKERFILE = REPO_ROOT / "infra" / "render" / "keycloak.Dockerfile"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = _load("anum_two_factor_fake", Path(__file__).with_name("test_keycloak_two_factor.py"))
sl = _load("anum_social_login", SOCIAL)

base.NAMES.update(
    {
        "idp-review-profile": "Review Profile",
        "idp-create-user-if-unique": "Create User If Unique",
        "idp-confirm-link": "Confirm link existing account",
        "idp-email-verification": "Verify existing account by Email",
        "idp-username-password-form": "Username Password Form for identity provider reauthentication",
        "idp-auto-link": "Automatically set existing user",
        "webauthn-authenticator-passwordless": "WebAuthn Passwordless Authenticator",
        "registration-page-form": "registration form",
        "registration-user-creation": "Registration User Profile Creation",
        "registration-password-action": "Password Validation",
        "registration-recaptcha-action": "reCAPTCHA",
        "registration-terms-and-conditions": "Terms and conditions",
    }
)

GOOGLE_INPUT = "1234567890-example.apps.googleusercontent.com\nexample-secret-value\n"
APPLE_KEY = "-----BEGIN PRIVATE KEY-----\nexamplekeymaterial\n-----END PRIVATE KEY-----"
APPLE_INPUT = f"com.example.signin\nABCDE12345\nKEYID12345\n{APPLE_KEY}\n"


class FakeKeycloak(base.FakeKeycloak):
    def __init__(self, *, apple: bool = True, smtp: bool = True, theme: bool = True) -> None:
        super().__init__()
        self.realm.update({"registrationAllowed": False, "passwordPolicy": None, "registrationFlow": "registration"})
        if smtp:
            self.realm["smtpServer"] = {"host": "smtp.example.com", "from": "noreply@example.com"}
        self.required_actions.append({"alias": "TERMS_AND_CONDITIONS", "name": "Terms and Conditions", "enabled": False, "defaultAction": False})
        self.idps: dict[str, dict[str, Any]] = {}
        self.localization: dict[str, dict[str, str]] = {}
        self.social_types = ["google", "github"] + (["apple"] if apple else [])
        self.themes = ["base", "keycloak", "keycloak.v2"] + (["anum"] if theme else [])
        self.users = {
            "u1": {"id": "u1", "username": "shahzeb", "email": "owner@example.com", "emailVerified": True, "attributes": {"tenant_id": ["acme"]}, "createdTimestamp": 1},
            "u2": {"id": "u2", "username": "new.person@example.com", "email": "new.person@example.com", "emailVerified": True, "attributes": {}, "createdTimestamp": 3},
            "u3": {"id": "u3", "username": "typed@example.com", "email": "typed@example.com", "emailVerified": False, "createdTimestamp": 2},
        }
        self.links = {"u2": [{"identityProvider": "google", "userId": "g-1"}]}
        registration = self._flow("registration", top=True)
        form = self._flow("registration form")
        self._add(registration, "REQUIRED", subflow=form)
        self._add(form, "REQUIRED", "registration-user-creation")
        self._add(form, "REQUIRED", "registration-password-action")
        self._add(form, "DISABLED", "registration-recaptcha-action")
        self._add(form, "DISABLED", "registration-terms-and-conditions")

    def handle(self, verb: str, path: str, query: dict[str, str], body: Any) -> Any:
        parts = path.split("/")
        if path == "authentication/flows" and verb == "create":
            assert body["providerId"] == "basic-flow" and body["topLevel"] is True
            if any(f["alias"] == body["alias"] for f in self.flows.values()):
                raise LookupError(f"flow {body['alias']} exists")
            self._flow(body["alias"], top=True)
            return ""
        if path == "serverinfo":
            return {
                "socialProviders": [{"id": p} for p in self.social_types],
                "identityProviders": [{"id": "oidc"}, {"id": "saml"}],
                "themes": {"login": [{"name": name} for name in self.themes]},
            }
        if path == "identity-provider/instances":
            if verb == "get":
                return [copy.deepcopy(self._masked(i)) for i in self.idps.values()]
            assert body["alias"] not in self.idps
            self.idps[body["alias"]] = copy.deepcopy(body)
            return ""
        if parts[:2] == ["identity-provider", "instances"] and len(parts) == 3:
            if parts[2] not in self.idps:
                raise LookupError(parts[2])
            if verb == "delete":
                del self.idps[parts[2]]
                return ""
            if verb == "update":
                stored = self.idps[parts[2]]
                incoming = copy.deepcopy(body)
                # Keycloak keeps the stored secret when the masked value comes back.
                if incoming.get("config", {}).get("clientSecret") == "**********":
                    incoming["config"]["clientSecret"] = stored["config"]["clientSecret"]
                self.idps[parts[2]] = incoming
                return ""
            return copy.deepcopy(self._masked(self.idps[parts[2]]))
        if parts[0] == "localization" and verb == "create":
            self.localization.setdefault(parts[1], {}).update(body)
            return ""
        if path == "users" and "username" not in query:
            users = sorted(self.users.values(), key=lambda u: u["id"])
            first, size = int(query.get("first", "0")), int(query.get("max", "100"))
            return copy.deepcopy(users[first : first + size])
        if path == "users":
            return [copy.deepcopy(u) for u in self.users.values() if u["username"] == query["username"]]
        if parts[0] == "users" and len(parts) == 2:
            if verb == "update":
                self.users[parts[1]] = copy.deepcopy(body)
                return ""
            return copy.deepcopy(self.users[parts[1]])
        if parts[0] == "users" and parts[2:] == ["federated-identity"]:
            return self.links.get(parts[1], [])
        return super().handle(verb, path, query, body)

    @staticmethod
    def _masked(idp: dict[str, Any]) -> dict[str, Any]:
        masked = copy.deepcopy(idp)
        if "clientSecret" in masked.get("config", {}):
            masked["config"]["clientSecret"] = "**********"
        if "p8Content" in masked.get("config", {}):
            masked["config"]["p8Content"] = "**********"
        return masked

    def tree(self, alias: str) -> list[tuple[int, str, str, dict[str, str]]]:
        rows = []
        for row in self.listing(self._by_alias(alias)):
            config = self.configs[row["authenticationConfig"]]["config"] if row.get("authenticationConfig") else {}
            rows.append((row["level"], row["displayName"] if row.get("authenticationFlow") else row["providerId"], row["requirement"], config))
        return rows


def kcadm(keycloak: FakeKeycloak) -> Any:
    return sl.Kcadm(["kcadm"], runner=keycloak)


@pytest.fixture
def keycloak() -> FakeKeycloak:
    return FakeKeycloak()


def quiet() -> tuple[list[str], Any]:
    log: list[str] = []
    return log, log.append


# --------------------------------------------------------------------------- flows


def test_first_broker_flow_never_links_without_proof_and_never_assigns_a_tenant(keycloak: FakeKeycloak) -> None:
    sl.converge_flows(kcadm(keycloak), lambda _: None)
    tree = keycloak.tree(sl.FIRST_BROKER_FLOW)
    providers = [name for _, name, _, _ in tree]
    assert "idp-auto-link" not in providers
    assert tree[0] == (0, "idp-review-profile", "REQUIRED", {"update.profile.on.first.login": "missing"})
    assert (1, "idp-create-user-if-unique", "ALTERNATIVE", {"require.password.update.after.registration": "false"}) in tree
    # An existing account is linked only after its owner confirms and proves it.
    assert (2, "idp-confirm-link", "REQUIRED", {}) in tree
    assert (3, "idp-email-verification", "ALTERNATIVE", {}) in tree
    assert (4, "idp-username-password-form", "REQUIRED", {}) in tree
    assert (5, "auth-otp-form", "ALTERNATIVE", {}) in tree
    # Nothing in it can set tenant_id: there is no attribute or role step at all.
    assert all(not name.startswith(("idp-add", "oidc-hardcoded", "hardcoded")) for name in providers)


def test_post_broker_flow_keeps_the_two_factor_rule(keycloak: FakeKeycloak) -> None:
    sl.converge_flows(kcadm(keycloak), lambda _: None)
    assert keycloak.tree(sl.POST_BROKER_FLOW) == [
        (0, "anum-broker-2fa-verify", "CONDITIONAL", {}),
        (1, "conditional-user-configured", "REQUIRED", {}),
        (1, "auth-otp-form", "ALTERNATIVE", {}),
        (1, "webauthn-authenticator-passwordless", "ALTERNATIVE", {}),
        (1, "auth-recovery-authn-code-form", "ALTERNATIVE", {}),
        (0, "anum-broker-2fa-enrol", "CONDITIONAL", {}),
        (1, "conditional-sub-flow-executed", "REQUIRED", {"flow_to_check": "anum-broker-2fa-verify", "check_result": "not-executed"}),
        (1, "auth-otp-form", "REQUIRED", {}),
    ]


def test_flows_are_idempotent_and_repaired_in_place(keycloak: FakeKeycloak) -> None:
    sl.converge_flows(kcadm(keycloak), lambda _: None)
    ids = {f["alias"]: f["id"] for f in keycloak.flows.values() if f["topLevel"]}
    keycloak.calls.clear()
    log, sink = quiet()
    sl.converge_flows(kcadm(keycloak), sink)
    assert log == [] and all(call[1] == "get" for call in keycloak.calls)
    # Someone made the enrolment optional and added automatic linking in the console.
    enrol = keycloak._by_alias("anum-broker-2fa-enrol")
    enrol["executions"][1]["requirement"] = "DISABLED"
    keycloak._add(keycloak._by_alias("anum-broker-user"), "ALTERNATIVE", "idp-auto-link")
    sl.converge_flows(kcadm(keycloak), lambda _: None)
    assert "idp-auto-link" not in [name for _, name, _, _ in keycloak.tree(sl.FIRST_BROKER_FLOW)]
    assert keycloak.tree(sl.POST_BROKER_FLOW)[-1] == (1, "auth-otp-form", "REQUIRED", {})
    # The top-level flows are the same objects, so providers keep pointing at them.
    assert {f["alias"]: f["id"] for f in keycloak.flows.values() if f["topLevel"]} == ids


# --------------------------------------------------------------------------- providers


def test_google_is_created_with_the_anum_flows(keycloak: FakeKeycloak) -> None:
    log, sink = quiet()
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=sink)
    idp = keycloak.idps["google"]
    assert idp["providerId"] == "google" and idp["enabled"] is True
    assert idp["firstBrokerLoginFlowAlias"] == sl.FIRST_BROKER_FLOW
    assert idp["postBrokerLoginFlowAlias"] == sl.POST_BROKER_FLOW
    assert idp["trustEmail"] is True and idp["storeToken"] is False and idp["linkOnly"] is False
    assert idp["config"]["clientId"] == "1234567890-example.apps.googleusercontent.com"
    assert idp["config"]["clientSecret"] == "example-secret-value"
    assert idp["config"]["syncMode"] == "IMPORT"
    assert idp["config"]["pkceEnabled"] == "true"
    assert any("/realms/anum/broker/google/endpoint" in line for line in log)
    assert not any("example-secret-value" in line for line in log)


def test_apple_takes_services_id_team_key_and_private_key(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "apple", APPLE_INPUT, log=lambda _: None)
    config = keycloak.idps["apple"]["config"]
    assert (config["clientId"], config["teamId"], config["keyId"]) == ("com.example.signin", "ABCDE12345", "KEYID12345")
    assert config["p8Content"] == APPLE_KEY + "\n"
    assert config["defaultScope"] == "openid name email"
    assert keycloak.idps["apple"]["postBrokerLoginFlowAlias"] == sl.POST_BROKER_FLOW


def test_apple_needs_the_provider_extension() -> None:
    keycloak = FakeKeycloak(apple=False)
    with pytest.raises(sl.KcadmError, match="Apple provider extension"):
        sl.set_provider(kcadm(keycloak), "apple", APPLE_INPUT, log=lambda _: None)
    assert keycloak.idps == {}


@pytest.mark.parametrize(
    ("provider", "text", "message"),
    [
        ("google", "only-one-line\n", "two lines"),
        ("google", "not-a-client-id\nexample-secret-value\n", "Google OAuth client ID"),
        ("google", "1234567890-example.apps.googleusercontent.com\nshort\n", "client secret"),
        ("apple", "com.example.signin\nABCDE12345\nKEYID12345\n", "private key"),
        ("apple", "signin\nABCDE12345\nKEYID12345\n" + APPLE_KEY, "Services ID"),
        ("apple", "com.example.signin\nabc\nKEYID12345\n" + APPLE_KEY, "Team ID"),
        ("apple", "com.example.signin\nABCDE12345\nKEY\n" + APPLE_KEY, "Key ID"),
        ("apple", "com.example.signin\nABCDE12345\nKEYID12345\nnot a key", "AuthKey"),
        ("facebook", "x\ny\n", "unknown provider"),
    ],
)
def test_provider_values_are_checked_before_anything_changes(keycloak: FakeKeycloak, provider: str, text: str, message: str) -> None:
    with pytest.raises(sl.KcadmError, match=message):
        sl.set_provider(kcadm(keycloak), provider, text, log=lambda _: None)
    assert keycloak.idps == {} and not [c for c in keycloak.calls if c[1] != "get"]


def test_running_it_again_replaces_the_secret_and_keeps_other_settings(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=lambda _: None)
    keycloak.idps["google"]["config"]["hostedDomain"] = "example.com"  # set by hand in the console
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT.replace("example-secret-value", "rotated-secret-value"), log=lambda _: None)
    assert keycloak.idps["google"]["config"]["clientSecret"] == "rotated-secret-value"
    assert keycloak.idps["google"]["config"]["hostedDomain"] == "example.com"


def test_apply_points_drifted_providers_back_at_the_anum_flows_without_touching_secrets(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=lambda _: None)
    keycloak.idps["google"]["firstBrokerLoginFlowAlias"] = "first broker login"
    keycloak.idps["google"]["postBrokerLoginFlowAlias"] = ""
    log, sink = quiet()
    assert sl.repoint_providers(kcadm(keycloak), sink) == ["google"]
    assert keycloak.idps["google"]["firstBrokerLoginFlowAlias"] == sl.FIRST_BROKER_FLOW
    assert keycloak.idps["google"]["postBrokerLoginFlowAlias"] == sl.POST_BROKER_FLOW
    assert keycloak.idps["google"]["config"]["clientSecret"] == "example-secret-value"
    assert log == ["identity provider google: reset firstBrokerLoginFlowAlias, postBrokerLoginFlowAlias"]
    keycloak.calls.clear()
    assert sl.repoint_providers(kcadm(keycloak), lambda _: None) == ["google"]
    assert all(call[1] == "get" for call in keycloak.calls)


def test_remove_provider(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=lambda _: None)
    assert sl.remove_provider(kcadm(keycloak), "google", log=lambda _: None) is True
    assert keycloak.idps == {}
    assert sl.remove_provider(kcadm(keycloak), "google", log=lambda _: None) is False


def test_provider_secrets_travel_on_stdin_only(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=lambda _: None)
    sl.set_provider(kcadm(keycloak), "apple", APPLE_INPUT, log=lambda _: None)
    for argv in keycloak.calls:
        assert not any("example-secret-value" in arg or "PRIVATE KEY" in arg for arg in argv)


def test_main_reads_the_values_from_stdin(keycloak: FakeKeycloak, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real = sl.Kcadm
    monkeypatch.setattr(sl, "Kcadm", lambda command, config="": real(command, config=config, runner=keycloak))
    assert sl.main(["set-provider", "--provider", "google", "--", "kcadm"], stdin=io.StringIO(GOOGLE_INPUT)) == 0
    assert "created identity provider google" in capsys.readouterr().out
    assert sl.main(["set-provider", "--provider", "google", "--", "kcadm"], stdin=io.StringIO("bad\n")) == 1
    assert "ERROR:" in capsys.readouterr().err


# --------------------------------------------------------------------------- sign-up


def test_enable_sign_up_sets_policy_terms_theme_and_verification(keycloak: FakeKeycloak) -> None:
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    realm = keycloak.realm
    assert realm["registrationAllowed"] is True and realm["verifyEmail"] is True
    assert realm["registrationEmailAsUsername"] is True and realm["duplicateEmailsAllowed"] is False
    assert realm["passwordPolicy"] == sl.REALM_PASSWORD_RULES
    assert realm["loginTheme"] == "anum"
    assert realm["registrationFlow"] == sl.REGISTRATION_FLOW
    terms = next(a for a in keycloak.required_actions if a["alias"] == "TERMS_AND_CONDITIONS")
    assert terms["enabled"] and terms["defaultAction"]
    for locale in ("en", "ar"):
        assert 'href="https://example.com/privacy"' in keycloak.localization[locale]["termsText"]


def test_enable_sign_up_is_idempotent(keycloak: FakeKeycloak) -> None:
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    before = copy.deepcopy(keycloak.realm)
    keycloak.calls.clear()
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    assert keycloak.realm == before
    # Only reads and the localization upsert (same values) on a second run.
    assert {call[1] for call in keycloak.calls} <= {"get", "create"}
    assert all(call[2].startswith("localization/") for call in keycloak.calls if call[1] == "create")


def test_sign_up_refuses_without_an_email_server() -> None:
    keycloak = FakeKeycloak(smtp=False)
    with pytest.raises(sl.KcadmError, match="Realm settings -> Email"):
        sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    assert keycloak.realm["registrationAllowed"] is False


@pytest.mark.parametrize("url", ["", "http://example.com/privacy", "https://", 'https://example.com/"><script>'])
def test_sign_up_needs_an_https_privacy_policy(keycloak: FakeKeycloak, url: str) -> None:
    with pytest.raises(sl.KcadmError, match="privacy"):
        sl.sign_up(kcadm(keycloak), True, url, log=lambda _: None)
    assert keycloak.realm["registrationAllowed"] is False


def test_sign_up_without_the_theme_still_works_and_says_so() -> None:
    keycloak = FakeKeycloak(theme=False)
    log, sink = quiet()
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=sink)
    assert keycloak.realm["registrationAllowed"] is True and "loginTheme" not in keycloak.realm
    assert any(line.startswith("WARNING: the anum login theme") for line in log)


def test_disable_sign_up(keycloak: FakeKeycloak) -> None:
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    sl.sign_up(kcadm(keycloak), False, log=lambda _: None)
    assert keycloak.realm["registrationAllowed"] is False


# --------------------------------------------------------------------------- pending accounts


def test_pending_users_lists_accounts_without_a_tenant(keycloak: FakeKeycloak) -> None:
    lines = sl.pending_users(kcadm(keycloak))
    assert lines[0].startswith("2 pending account(s)")
    assert "typed@example.com email typed@example.com (not verified) via password" in lines[1]
    assert "new.person@example.com email new.person@example.com via google" in lines[2]
    assert "shahzeb" not in "\n".join(lines)


def test_pending_users_pages_through_every_user(keycloak: FakeKeycloak) -> None:
    for index in range(450):
        keycloak.users[f"z{index:03}"] = {"id": f"z{index:03}", "username": f"user{index}", "attributes": {"tenant_id": ["acme"]}}
    assert sl.pending_users(kcadm(keycloak))[0].startswith("2 pending")
    assert len(sl._all_users(kcadm(keycloak))) == 453


def test_assign_tenant_sets_the_attributes_and_grants_nothing_else(keycloak: FakeKeycloak) -> None:
    log, sink = quiet()
    assert sl.assign_tenant(kcadm(keycloak), "new.person@example.com", "acme", "workspace_sales", log=sink) is True
    user = keycloak.users["u2"]
    assert user["attributes"] == {"tenant_id": ["acme"], "default_workspace_id": ["workspace_sales"]}
    assert "invites them" in log[0]
    # No role mapping or membership call: access still needs an invitation.
    assert not [c for c in keycloak.calls if "role-mappings" in c[2]]
    assert sl.assign_tenant(kcadm(keycloak), "new.person@example.com", "acme", log=lambda _: None) is False


def test_assign_tenant_refuses_to_move_someone_between_organizations(keycloak: FakeKeycloak) -> None:
    with pytest.raises(sl.KcadmError, match="already belongs to tenant acme"):
        sl.assign_tenant(kcadm(keycloak), "shahzeb", "other_company", log=lambda _: None)
    assert keycloak.users["u1"]["attributes"]["tenant_id"] == ["acme"]


@pytest.mark.parametrize(("tenant", "workspace"), [("a", ""), ("bad tenant", ""), ("acme", "x y")])
def test_assign_tenant_checks_the_ids(keycloak: FakeKeycloak, tenant: str, workspace: str) -> None:
    with pytest.raises(sl.KcadmError):
        sl.assign_tenant(kcadm(keycloak), "new.person@example.com", tenant, workspace, log=lambda _: None)


def test_assign_tenant_warns_when_the_email_is_not_verified(keycloak: FakeKeycloak) -> None:
    log, sink = quiet()
    sl.assign_tenant(kcadm(keycloak), "typed@example.com", "acme", log=sink)
    assert any("not verified" in line for line in log)


def test_status_names_the_api_settings(keycloak: FakeKeycloak) -> None:
    sl.set_provider(kcadm(keycloak), "google", GOOGLE_INPUT, log=lambda _: None)
    sl.sign_up(kcadm(keycloak), True, "https://example.com/privacy", log=lambda _: None)
    lines = sl.status(kcadm(keycloak))
    assert "flow anum-first-broker-login: in place" in lines
    assert "apple: not configured" in lines
    assert lines[-1] == "API setting: ANUM_SIGN_IN_PROVIDERS=google  ANUM_SELF_SIGN_UP=true"


# --------------------------------------------------------------------------- theme, image, wrapper


def test_theme_lists_the_policy_rules_the_script_sets() -> None:
    ftl = (THEME / "password-validation.ftl").read_text(encoding="utf-8")
    for rule in ("length", "maxLength", "lowerCase", "upperCase", "digits", "notUsername", "notEmail"):
        assert rule in sl.REALM_PASSWORD_RULES and f'name: "{rule}"' in ftl
    assert "parent=keycloak.v2" in (THEME / "theme.properties").read_text(encoding="utf-8")
    english = (THEME / "messages" / "messages_en.properties").read_text(encoding="utf-8")
    arabic = (THEME / "messages" / "messages_ar.properties").read_text(encoding="utf-8")
    keys = lambda text: {line.split("=", 1)[0] for line in text.splitlines() if "=" in line}
    assert keys(english) == keys(arabic) and all(f"msg('{key}'" in ftl for key in keys(english) if key.startswith("anumPasswordRule") and key not in {"anumPasswordRuleMet", "anumPasswordRuleUnmet"})


def test_keycloak_image_installs_the_theme_and_pins_the_apple_extension() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --chown=1000:0 infra/keycloak/themes/anum /opt/keycloak/themes/anum" in text
    assert 'ARG APPLE_PROVIDER_URL=""' in text and 'ARG APPLE_PROVIDER_SHA256=""' in text
    assert "APPLE_PROVIDER_SHA256 is required with APPLE_PROVIDER_URL" in text
    build = text[text.index("AS build") : text.index("AS runtime")]
    assert "/opt/keycloak/providers/" in build  # before kc.sh build
    assert "sha256sum -c" in text
    ignore = (DOCKERFILE.parent / "keycloak.Dockerfile.dockerignore").read_text(encoding="utf-8")
    assert "!infra/keycloak/themes/" in ignore


def test_wrapper_reads_provider_secrets_without_echo_and_sends_them_on_stdin() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert "../keycloak/social_login.py" in text
    assert re.search(r'read -r -s -p "Google client secret: "', text)
    assert re.search(r"set-provider --provider google", text) and re.search(r"set-provider --provider apple", text)
    # The Apple key is read from the file by the script's stdin, never named on a command line by value.
    assert 'cat "$apple_key_file"' in text
    assert "unset google_secret" in text
    assert not re.search(r"echo[^\n]*\$(google_secret|password|secret)", text)
    assert '--privacy-url "$PRIVACY_URL"' in text


# --------------------------------------------------------------------------- login theme (look)


def test_apply_selects_the_login_theme_and_languages_on_an_existing_realm(keycloak: FakeKeycloak) -> None:
    keycloak.realm["displayName"] = "ANUM (local development)"
    log, sink = quiet()
    sl.converge_appearance(kcadm(keycloak), sink)
    assert keycloak.realm["loginTheme"] == "anum" and keycloak.realm["displayName"] == "ANUM"
    assert keycloak.realm["supportedLocales"] == ["en", "ar"] and keycloak.realm["internationalizationEnabled"] is True
    assert any("loginTheme" in line for line in log)
    keycloak.calls.clear()
    sl.converge_appearance(kcadm(keycloak), lambda _: None)
    assert all(call[1] == "get" for call in keycloak.calls)  # second run only reads


def test_apply_keeps_a_display_name_the_owner_chose(keycloak: FakeKeycloak) -> None:
    keycloak.realm["displayName"] = "Acme Fleet"
    sl.converge_appearance(kcadm(keycloak), lambda _: None)
    assert keycloak.realm["displayName"] == "Acme Fleet"


def test_apply_without_the_theme_installed_warns_and_leaves_the_theme_unset() -> None:
    keycloak = FakeKeycloak(theme=False)
    log, sink = quiet()
    sl.converge_appearance(kcadm(keycloak), sink)
    assert "loginTheme" not in keycloak.realm and any(line.startswith("WARNING: the anum login theme") for line in log)


def test_dev_and_shared_realms_select_the_theme() -> None:
    import json

    realm = json.loads((REPO_ROOT / "infra" / "keycloak" / "anum-realm.json").read_text(encoding="utf-8"))
    assert realm["loginTheme"] == "anum" and realm["supportedLocales"] == ["en", "ar"]
    assert sl.LOGIN_THEME == realm["loginTheme"]


def test_theme_properties_load_the_anum_styles_after_the_parents() -> None:
    props = (THEME / "theme.properties").read_text(encoding="utf-8")
    assert "parent=keycloak.v2" in props
    assert re.search(r"^styles=css/styles\.css css/anum\.css$", props, re.M)
    assert (THEME / "resources" / "css" / "anum.css").is_file()


def test_theme_templates_use_only_messages_both_languages_define() -> None:
    english = (THEME / "messages" / "messages_en.properties").read_text(encoding="utf-8")
    arabic = (THEME / "messages" / "messages_ar.properties").read_text(encoding="utf-8")
    keys = lambda text: {line.split("=", 1)[0] for line in text.splitlines() if "=" in line}
    for name in ("template.ftl", "social-providers.ftl"):
        used = set(re.findall(r"msg\((?:'|\")(anum[A-Za-z]+)", (THEME / name).read_text(encoding="utf-8")))
        assert used and used <= keys(english) and used <= keys(arabic), (name, used - keys(english))
    assert "'" not in english.replace("\n", " ").split("anumBrandName=", 1)[1]  # MessageFormat eats single quotes


def test_theme_keeps_the_layout_accessible_and_right_to_left_safe() -> None:
    css = (THEME / "resources" / "css" / "anum.css").read_text(encoding="utf-8")
    # Logical properties only: Arabic mirrors the page.
    assert not re.search(r"(?<![-\w])(margin|padding|border)-(left|right)\s*:|(?<![-\w])(left|right)\s*:|text-align:\s*(left|right)", css)
    assert ":focus-visible" in css and "prefers-color-scheme: dark" in css and "prefers-reduced-motion" in css
    template = (THEME / "template.ftl").read_text(encoding="utf-8")
    assert "<main " in template and 'id="kc-page-title"' in template and 'role="${(message.type' in template
    assert 'id="login-select-toggle"' in template and "locale.rtl" in template
    # The non-production notice comes from the realm's own display name, never a hard-coded flag.
    assert "local development" in template and "realm.displayName" in template


def test_theme_files_have_no_em_or_en_dashes_and_the_fonts_ship_their_licences() -> None:
    for path in THEME.rglob("*"):
        if path.is_file() and path.suffix in {".ftl", ".css", ".properties", ".js", ".svg"}:
            text = path.read_text(encoding="utf-8")
            assert "\u2014" not in text and "\u2013" not in text, path
    fonts = THEME / "resources" / "fonts"
    assert {p.name for p in fonts.iterdir()} == {"inter-latin-var.woff2", "noto-sans-arabic-var.woff2", "OFL-Inter.txt", "OFL-NotoSansArabic.txt"}
