"""Google and Apple sign-in, self sign-up and pending accounts for the `anum` realm (docs/identity.md#google-and-apple-sign-in).

    python3 social_login.py apply [--config <kcadm config>] -- <kcadm command...>
    python3 social_login.py set-provider --provider google|apple [--config ...] -- <kcadm...>
    python3 social_login.py remove-provider --provider google|apple [--config ...] -- <kcadm...>
    python3 social_login.py enable-sign-up --privacy-url <https URL> [--config ...] -- <kcadm...>
    python3 social_login.py disable-sign-up [--config ...] -- <kcadm...>
    python3 social_login.py pending-users [--config ...] -- <kcadm...>
    python3 social_login.py assign-tenant --username <name> --tenant <id> [--workspace <id>] ... -- <kcadm...>
    python3 social_login.py status [--config ...] -- <kcadm...>

`infra/oracle/keycloak-2fa.sh` logs kcadm in and runs this (``google``, ``apple``,
``sign-up``, ``pending-users``, ``assign-tenant``; its ``apply`` runs ``apply`` here too).
Any kcadm logged in to a master-realm admin works.

``set-provider`` reads the provider's values from **stdin**, one per line, never from the
command line: Google: the OAuth client ID, then the client secret. Apple: the Services ID,
the Team ID, the Key ID, then the private key file's contents (``AuthKey_<KEYID>.p8``,
PEM) to the end of input. They reach Keycloak in the request body on kcadm's stdin.

What it converges (``apply`` and every other write first):

* ``anum-first-broker-login``, the flow every Google or Apple sign-in of a new identity
  runs. A new email creates a new Keycloak user with **no** ``tenant_id``: such an account
  is *pending* and the API refuses it (``403 account_pending``) until an administrator
  assigns a tenant (``assign-tenant``) and a workspace owner invites it. An identity whose
  email already belongs to an account is linked only after the person proves they own that
  account: the link sent to its email address, or its password plus its second factor.
  Keycloak's "automatically link" authenticator is never part of it::

      Review Profile                        REQUIRED     (only missing fields)
      anum-broker-user                      REQUIRED
        Create User If Unique               ALTERNATIVE
        anum-broker-existing                ALTERNATIVE
          Confirm link existing account     REQUIRED
          anum-broker-verify                REQUIRED
            Verify existing account by Email  ALTERNATIVE
            anum-broker-reauth              ALTERNATIVE
              Username Password Form (idp)  REQUIRED
              anum-broker-reauth-2fa        CONDITIONAL
                Condition - user configured REQUIRED
                OTP Form                    ALTERNATIVE
                Recovery Authentication Code Form  ALTERNATIVE

* ``anum-post-broker-2fa``, run after every Google or Apple sign-in. It keeps the realm's
  two-factor rule (docs/identity.md#two-factor-sign-in): someone with an authenticator
  app, a passkey or recovery codes uses one of them; someone with none sets up an
  authenticator app before the sign-in completes::

      anum-broker-2fa-verify                CONDITIONAL
        Condition - user configured         REQUIRED
        OTP Form                            ALTERNATIVE
        Passkey (WebAuthn Passwordless)     ALTERNATIVE
        Recovery Authentication Code Form   ALTERNATIVE
      anum-broker-2fa-enrol                 CONDITIONAL
        Condition - sub-flow executed       REQUIRED  anum-broker-2fa-verify not executed
        OTP Form                            REQUIRED

* Every configured ``google`` or ``apple`` identity provider uses both flows, trusts the
  provider's verified email, stores no provider tokens and imports the profile only on
  the first sign-in.

``enable-sign-up`` turns on Keycloak's own registration page (``prompt=create``):
email as username, a verified email, the realm password policy below (the ANUM login
theme lists it live), and the "Terms and Conditions" required action as a default action
for every new account, with text that links the privacy policy. It refuses without an
SMTP server (realm settings, Email), because nobody could verify their address.

Standard library only, so the VM's python3 runs it without installing anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from two_factor import REALM, Kcadm, KcadmError, Step

FIRST_BROKER_FLOW = "anum-first-broker-login"
POST_BROKER_FLOW = "anum-post-broker-2fa"
REGISTRATION_FLOW = "anum-registration"
LOGIN_THEME = "anum"
PROVIDERS = ("google", "apple")
DISPLAY_NAMES = {"google": "Google", "apple": "Apple"}

# Kept in step with apps/web and Flutter (they show the rules too) by
# services/api/tests/test_keycloak_social_login.py.
REALM_PASSWORD_RULES = (
    "length(12) and maxLength(128) and lowerCase(1) and upperCase(1) and digits(1) "
    "and notUsername and notEmail and passwordHistory(3)"
)

SCOPE_ID = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
GOOGLE_CLIENT_ID = re.compile(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$")
APPLE_SERVICES_ID = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
APPLE_TEN = re.compile(r"^[A-Z0-9]{10}$")


@dataclass(frozen=True)
class FlowSpec:
    alias: str
    description: str
    steps: tuple[Step, ...]
    config_aliases: dict[str, str]


FIRST_BROKER = FlowSpec(
    FIRST_BROKER_FLOW,
    "ANUM: first Google or Apple sign-in. New accounts stay pending (no tenant); an "
    "existing account is linked only after its owner proves it (email link or password "
    "and second factor). Never links automatically.",
    (
        Step(0, "REQUIRED", provider="idp-review-profile", config={"update.profile.on.first.login": "missing"}),
        Step(0, "REQUIRED", flow="anum-broker-user", description="Create a new account or link an existing one."),
        Step(1, "ALTERNATIVE", provider="idp-create-user-if-unique", config={"require.password.update.after.registration": "false"}),
        Step(1, "ALTERNATIVE", flow="anum-broker-existing", description="The email belongs to an account already."),
        Step(2, "REQUIRED", provider="idp-confirm-link"),
        Step(2, "REQUIRED", flow="anum-broker-verify", description="Prove ownership of the existing account."),
        Step(3, "ALTERNATIVE", provider="idp-email-verification"),
        Step(3, "ALTERNATIVE", flow="anum-broker-reauth", description="Password and second factor of the existing account."),
        Step(4, "REQUIRED", provider="idp-username-password-form"),
        Step(4, "CONDITIONAL", flow="anum-broker-reauth-2fa", description="The existing account's second factor."),
        Step(5, "REQUIRED", provider="conditional-user-configured"),
        Step(5, "ALTERNATIVE", provider="auth-otp-form"),
        Step(5, "ALTERNATIVE", provider="auth-recovery-authn-code-form"),
    ),
    {
        "idp-review-profile": "anum-broker-review-profile",
        "idp-create-user-if-unique": "anum-broker-create-user",
    },
)

POST_BROKER = FlowSpec(
    POST_BROKER_FLOW,
    "ANUM: second factor after every Google or Apple sign-in (authenticator app, passkey "
    "or recovery code; set up an authenticator app if there is none).",
    (
        Step(0, "CONDITIONAL", flow="anum-broker-2fa-verify", description="A second factor the user already has."),
        Step(1, "REQUIRED", provider="conditional-user-configured"),
        Step(1, "ALTERNATIVE", provider="auth-otp-form"),
        Step(1, "ALTERNATIVE", provider="webauthn-authenticator-passwordless"),
        Step(1, "ALTERNATIVE", provider="auth-recovery-authn-code-form"),
        Step(0, "CONDITIONAL", flow="anum-broker-2fa-enrol", description="No second factor yet: set up an authenticator app now."),
        Step(
            1,
            "REQUIRED",
            provider="conditional-sub-flow-executed",
            config={"flow_to_check": "anum-broker-2fa-verify", "check_result": "not-executed"},
        ),
        Step(1, "REQUIRED", provider="auth-otp-form"),
    ),
    {"conditional-sub-flow-executed": "anum-broker-2fa-enrol-unless-verified"},
)


class Flows:
    """Builds and checks ANUM's own top-level flows (no copy of a built-in flow)."""

    def __init__(self, kcadm: Kcadm, log: Callable[[str], None]) -> None:
        self.kcadm = kcadm
        self.log = log

    def _path(self, alias: str) -> str:
        return f"authentication/flows/{quote(alias)}/executions"

    def _executions(self, alias: str) -> list[dict[str, Any]]:
        return self.kcadm.get(self._path(alias)) or []

    def actual(self, alias: str) -> list[tuple[Any, ...]]:
        steps = []
        for execution in self._executions(alias):
            config: dict[str, str] = {}
            if execution.get("authenticationConfig"):
                config = (
                    self.kcadm.get(f"authentication/config/{execution['authenticationConfig']}").get(
                        "config"
                    )
                    or {}
                )
            flow = ""
            if execution.get("authenticationFlow"):
                flow = self.kcadm.get(f"authentication/flows/{execution['flowId']}")["alias"]
            steps.append(
                Step(
                    execution["level"],
                    execution.get("requirement", ""),
                    provider="" if flow else execution.get("providerId", ""),
                    flow=flow,
                    config=config,
                ).key()
            )
        return steps

    def _set_requirement(self, top: str, execution_id: str, requirement: str) -> None:
        for execution in self._executions(top):
            if execution["id"] == execution_id:
                if execution.get("requirement") != requirement:
                    self.kcadm.update(self._path(top), {**execution, "requirement": requirement})
                return
        raise KcadmError(f"execution {execution_id} vanished from {top}")

    def _build(self, spec: FlowSpec) -> None:
        parents = {0: spec.alias}
        for step in spec.steps:
            parent = parents[step.level]
            if step.flow:
                self.kcadm.create(
                    f"authentication/flows/{quote(parent)}/executions/flow",
                    {
                        "alias": step.flow,
                        "type": "basic-flow",
                        "provider": "registration-page-form",
                        "description": step.description,
                    },
                )
                parents[step.level + 1] = step.flow
                execution_id = next(
                    e["id"]
                    for e in self._executions(spec.alias)
                    if e.get("authenticationFlow") and e.get("displayName") == step.flow
                )
            else:
                execution_id = self.kcadm.create(
                    f"authentication/flows/{quote(parent)}/executions/execution",
                    {"provider": step.provider},
                )
                if step.config:
                    self.kcadm.create(
                        f"authentication/executions/{execution_id}/config",
                        {"alias": spec.config_aliases[step.provider], "config": step.config},
                    )
            self._set_requirement(spec.alias, execution_id, step.requirement)

    def converge(self, spec: FlowSpec) -> bool:
        """Make ``spec.alias`` match ``spec.steps``; True when something changed."""
        if spec.alias not in {flow["alias"] for flow in self.kcadm.get("authentication/flows")}:
            self.kcadm.create(
                "authentication/flows",
                {
                    "alias": spec.alias,
                    "description": spec.description,
                    "providerId": "basic-flow",
                    "topLevel": True,
                    "builtIn": False,
                },
            )
            self.log(f"created flow {spec.alias}")
        elif self.actual(spec.alias) == [step.key() for step in spec.steps]:
            return False
        # Empty or drifted (someone changed it in the console): rebuild its contents. The
        # top-level flow stays, so identity providers keep pointing at it.
        top_level = [e for e in self._executions(spec.alias) if e["level"] == 0]
        for execution in reversed(top_level):
            self.kcadm.delete(f"authentication/executions/{execution['id']}")
        if top_level:
            self.log(f"removed the drifted contents of {spec.alias}")
        self._build(spec)
        self.log(f"built {spec.alias}")
        return True


def converge_flows(kcadm: Kcadm, log: Callable[[str], None]) -> None:
    flows = Flows(kcadm, log)
    flows.converge(FIRST_BROKER)
    flows.converge(POST_BROKER)


# --------------------------------------------------------------------------- providers


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def read_provider_values(provider: str, text: str) -> dict[str, str]:
    """Parses and checks what the owner typed or piped in (see the module docstring)."""
    lines = _lines(text)
    if provider == "google":
        if len([line for line in lines if line]) != 2:
            raise KcadmError("Google needs two lines on stdin: the client ID, then the client secret")
        client_id, secret = [line for line in lines if line]
        if not GOOGLE_CLIENT_ID.match(client_id):
            raise KcadmError(
                "that is not a Google OAuth client ID (it ends in .apps.googleusercontent.com); "
                "copy it from Google Cloud console -> Google Auth Platform -> Clients"
            )
        if len(secret) < 10 or any(character.isspace() for character in secret):
            raise KcadmError("the Google client secret looks wrong (empty, short or with spaces)")
        return {"clientId": client_id, "clientSecret": secret}
    if provider == "apple":
        if len(lines) < 4:
            raise KcadmError(
                "Apple needs, on stdin: the Services ID, the Team ID, the Key ID, then the "
                "private key (.p8) contents"
            )
        services_id, team_id, key_id = lines[0], lines[1], lines[2]
        key = "\n".join(line for line in lines[3:] if line)
        if not APPLE_SERVICES_ID.match(services_id):
            raise KcadmError(
                "the Apple Services ID is a reverse-DNS identifier such as com.example.signin "
                "(Apple Developer -> Certificates, Identifiers & Profiles -> Identifiers -> Services IDs)"
            )
        if not APPLE_TEN.match(team_id):
            raise KcadmError("the Apple Team ID is 10 capital letters and digits (Membership details)")
        if not APPLE_TEN.match(key_id):
            raise KcadmError("the Apple Key ID is 10 capital letters and digits (Keys -> your key)")
        if not (key.startswith("-----BEGIN PRIVATE KEY-----") and key.endswith("-----END PRIVATE KEY-----")):
            raise KcadmError(
                "the Apple private key must be the whole AuthKey_<KEYID>.p8 file, from "
                "-----BEGIN PRIVATE KEY----- to -----END PRIVATE KEY-----"
            )
        return {"clientId": services_id, "teamId": team_id, "keyId": key_id, "p8Content": key + "\n"}
    raise KcadmError(f"unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")


def _providers(kcadm: Kcadm) -> dict[str, dict[str, Any]]:
    return {idp["alias"]: idp for idp in kcadm.get("identity-provider/instances") or []}


def _available_types(kcadm: Kcadm) -> set[str]:
    info = kcadm.get("serverinfo") or {}
    return {p.get("id") for p in [*(info.get("socialProviders") or []), *(info.get("identityProviders") or [])]}


def provider_representation(provider: str, values: dict[str, str], existing: dict[str, Any] | None) -> dict[str, Any]:
    """The full identity provider representation ANUM wants, keeping unrelated settings."""
    config = dict((existing or {}).get("config") or {})
    config.update(values)
    config.update(
        {
            "syncMode": "IMPORT",
            "guiOrder": "1" if provider == "google" else "2",
            "defaultScope": "openid profile email" if provider == "google" else "openid name email",
        }
    )
    if provider == "google":
        config.update({"pkceEnabled": "true", "pkceMethod": "S256"})
    return {
        **(existing or {}),
        "alias": provider,
        "providerId": provider,
        "displayName": DISPLAY_NAMES[provider],
        "enabled": True,
        # The provider has verified the address (Google's email_verified, Apple always),
        # so the email-bound invitations (docs/identity.md#invitations-and-membership-management) work.
        "trustEmail": True,
        "storeToken": False,
        "addReadTokenRoleOnCreate": False,
        "linkOnly": False,
        "firstBrokerLoginFlowAlias": FIRST_BROKER_FLOW,
        "postBrokerLoginFlowAlias": POST_BROKER_FLOW,
        "config": config,
    }


FLOW_FIELDS = ("firstBrokerLoginFlowAlias", "postBrokerLoginFlowAlias", "trustEmail", "storeToken", "linkOnly")


def repoint_providers(kcadm: Kcadm, log: Callable[[str], None]) -> list[str]:
    """Existing google/apple providers use ANUM's flows (someone may have changed them)."""
    found = []
    for alias, idp in _providers(kcadm).items():
        if alias not in PROVIDERS:
            continue
        found.append(alias)
        wanted = provider_representation(alias, {}, idp)
        drift = [key for key in FLOW_FIELDS if idp.get(key) != wanted[key]]
        if drift:
            # GET masks the client secret; Keycloak keeps the stored one when it gets the mask back.
            kcadm.update(f"identity-provider/instances/{alias}", {**idp, **{k: wanted[k] for k in FLOW_FIELDS}})
            log(f"identity provider {alias}: reset {', '.join(drift)}")
    return found


def set_provider(kcadm: Kcadm, provider: str, text: str, log: Callable[[str], None] = print) -> None:
    values = read_provider_values(provider, text)
    if provider not in _available_types(kcadm):
        raise KcadmError(
            f"this Keycloak has no '{provider}' identity provider type. "
            + (
                "Apple sign-in needs the Apple provider extension in the Keycloak image "
                "(infra/render/keycloak.Dockerfile, APPLE_PROVIDER_URL and "
                "APPLE_PROVIDER_SHA256; docs/identity.md#apple)"
                if provider == "apple"
                else "Keycloak 26.4 or later is needed"
            )
        )
    converge_flows(kcadm, log)
    existing = _providers(kcadm).get(provider)
    body = provider_representation(provider, values, existing)
    if existing is None:
        kcadm.add("identity-provider/instances", body)
        log(f"created identity provider {provider} in realm {REALM}")
    else:
        kcadm.update(f"identity-provider/instances/{provider}", body)
        log(f"updated identity provider {provider} in realm {REALM}")
    log(
        f"redirect URI to register with {DISPLAY_NAMES[provider]}: "
        f"https://<auth host>/realms/{REALM}/broker/{provider}/endpoint"
    )


def remove_provider(kcadm: Kcadm, provider: str, log: Callable[[str], None] = print) -> bool:
    if provider not in PROVIDERS:
        raise KcadmError(f"unknown provider {provider!r}; use one of {', '.join(PROVIDERS)}")
    if provider not in _providers(kcadm):
        log(f"identity provider {provider} is not configured; nothing changed")
        return False
    kcadm.delete(f"identity-provider/instances/{provider}")
    log(
        f"removed identity provider {provider}. Accounts created through it stay; their "
        f"{DISPLAY_NAMES[provider]} link is gone, so those people sign in with a password "
        "(Forgot password) or another provider."
    )
    return True


# --------------------------------------------------------------------------- sign-up


def _privacy_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or any(c in value for c in "\"'<> "):
        raise KcadmError("--privacy-url must be the https:// address of the published privacy policy")
    return value


def terms_texts(privacy_url: str) -> dict[str, dict[str, str]]:
    link = f'<a href="{privacy_url}" target="_blank" rel="noopener noreferrer">'
    return {
        "en": {
            "termsTitle": "Privacy policy",
            "termsText": f"<p>To use ANUM, accept the {link}ANUM privacy policy</a>. It says what "
            "ANUM stores about you, why, and how to download or delete it.</p>",
        },
        "ar": {
            "termsTitle": "سياسة الخصوصية",
            "termsText": f"<p>لاستخدام ANUM، وافق على {link}سياسة الخصوصية</a>. "
            "توضح ما يحفظه ANUM عنك ولماذا وكيف تنزّله أو تحذفه.</p>",
        },
    }


def converge_registration(kcadm: Kcadm, log: Callable[[str], None]) -> None:
    flows = {flow["alias"] for flow in kcadm.get("authentication/flows")}
    if REGISTRATION_FLOW not in flows:
        kcadm.create("authentication/flows/registration/copy", {"newName": REGISTRATION_FLOW})
        log(f"created flow {REGISTRATION_FLOW} as a copy of registration")
    path = f"authentication/flows/{REGISTRATION_FLOW}/executions"
    executions = kcadm.get(path) or []
    providers = {e.get("providerId"): e for e in executions}
    for provider in ("registration-user-creation", "registration-password-action"):
        execution = providers.get(provider)
        if execution is None:
            raise KcadmError(f"flow {REGISTRATION_FLOW} has no {provider}; delete the flow and run again")
        if execution.get("requirement") != "REQUIRED":
            kcadm.update(path, {**execution, "requirement": "REQUIRED"})
            log(f"{REGISTRATION_FLOW}: {provider} REQUIRED")


def sign_up(kcadm: Kcadm, enable: bool, privacy_url: str = "", log: Callable[[str], None] = print) -> None:
    realm = kcadm.get(f"realms/{REALM}")
    if not enable:
        if realm.get("registrationAllowed"):
            kcadm.update(f"realms/{REALM}", {**realm, "registrationAllowed": False})
            log("self sign-up is off: the sign-in page no longer offers Register")
        else:
            log("self sign-up is already off; nothing changed")
        return
    url = _privacy_url(privacy_url)
    if not (realm.get("smtpServer") or {}).get("host"):
        raise KcadmError(
            "Keycloak has no email server, so nobody could verify their address. Set one first: "
            "admin console, realm anum -> Realm settings -> Email (docs/identity.md#self-sign-up)"
        )
    converge_flows(kcadm, log)
    converge_registration(kcadm, log)
    actions = {action["alias"]: action for action in kcadm.get("authentication/required-actions")}
    terms = actions.get("TERMS_AND_CONDITIONS")
    if terms is None:
        raise KcadmError("required action TERMS_AND_CONDITIONS is not registered in this Keycloak")
    if not (terms.get("enabled") and terms.get("defaultAction")):
        kcadm.update(
            "authentication/required-actions/TERMS_AND_CONDITIONS",
            {**terms, "enabled": True, "defaultAction": True},
        )
        log("required action Terms and Conditions: enabled, default for new accounts")
    for locale, texts in terms_texts(url).items():
        kcadm.add(f"localization/{locale}", texts)
    themes = {theme.get("name") for theme in ((kcadm.get("serverinfo") or {}).get("themes") or {}).get("login", [])}
    wanted: dict[str, Any] = {
        "registrationAllowed": True,
        "registrationEmailAsUsername": True,
        "verifyEmail": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "registrationFlow": REGISTRATION_FLOW,
        "passwordPolicy": REALM_PASSWORD_RULES,
    }
    if LOGIN_THEME in themes:
        wanted["loginTheme"] = LOGIN_THEME
    else:
        log(
            f"WARNING: the {LOGIN_THEME} login theme is not installed, so the sign-up page "
            "shows the password rules only after a wrong try; rebuild the Keycloak image"
        )
    realm = kcadm.get(f"realms/{REALM}")
    changed = {key: value for key, value in wanted.items() if realm.get(key) != value}
    if changed:
        kcadm.update(f"realms/{REALM}", {**realm, **changed})
        for key in sorted(changed):
            log(f"realm {key}: {realm.get(key)!r} -> {changed[key]!r}")
    log(f"self sign-up is on; new accounts accept the privacy policy at {url} and stay pending until assigned")


# --------------------------------------------------------------------------- pending accounts


def _tenant(user: dict[str, Any]) -> str:
    values = (user.get("attributes") or {}).get("tenant_id") or []
    return values[0] if values else ""


def _all_users(kcadm: Kcadm, page: int = 200) -> list[dict[str, Any]]:
    users: list[dict[str, Any]] = []
    while True:
        batch = kcadm.get("users", briefRepresentation="false", first=str(len(users)), max=str(page)) or []
        users.extend(batch)
        if len(batch) < page:
            return users


def pending_users(kcadm: Kcadm) -> list[str]:
    pending = [user for user in _all_users(kcadm) if not _tenant(user)]
    if not pending:
        return ["no pending accounts: every user of realm anum has a tenant_id"]
    lines = [f"{len(pending)} pending account(s) (no tenant_id; the API refuses them until assigned):"]
    for user in sorted(pending, key=lambda u: u.get("createdTimestamp") or 0):
        links = [link.get("identityProvider") for link in kcadm.get(f"users/{user['id']}/federated-identity") or []]
        lines.append(
            "  {} email {}{} via {}".format(
                user.get("username"),
                user.get("email") or "none",
                "" if user.get("emailVerified") else " (not verified)",
                ", ".join(links) if links else "password",
            )
        )
    lines.append("Assign one: assign-tenant <username> <tenant_id> [<workspace_id>], then invite them in ANUM (Members).")
    return lines


def assign_tenant(
    kcadm: Kcadm, username: str, tenant: str, workspace: str = "", log: Callable[[str], None] = print
) -> bool:
    if not SCOPE_ID.match(tenant or ""):
        raise KcadmError("the tenant id must be 3 to 80 letters, digits, '_' or '-'")
    if workspace and not SCOPE_ID.match(workspace):
        raise KcadmError("the workspace id must be 3 to 80 letters, digits, '_' or '-'")
    users = [u for u in kcadm.get("users", username=username, exact="true") or [] if u.get("username") == username]
    if len(users) != 1:
        raise KcadmError(f"no user named {username!r} in realm {REALM} (see pending-users)")
    user = kcadm.get(f"users/{users[0]['id']}")
    current = _tenant(user)
    if current and current != tenant:
        raise KcadmError(
            f"{username} already belongs to tenant {current}. Moving someone to another "
            "organization is deliberate: change tenant_id in the admin console (realm anum -> "
            f"Users -> {username} -> Attributes) if that is really intended"
        )
    attributes = dict(user.get("attributes") or {})
    wanted = {"tenant_id": [tenant], **({"default_workspace_id": [workspace]} if workspace else {})}
    if all(attributes.get(key) == value for key, value in wanted.items()):
        log(f"{username} already has tenant {tenant}{f' and workspace {workspace}' if workspace else ''}; nothing changed")
        return False
    kcadm.update(f"users/{user['id']}", {**user, "attributes": {**attributes, **wanted}})
    log(
        f"{username} now has tenant {tenant}{f' and default workspace {workspace}' if workspace else ''}. "
        "This grants no access by itself: a workspace owner invites them (Members -> Invite"
        f"{', bound to ' + user['email'] if user.get('email') else ''}). They see it after "
        "\"Check again\" or their next sign-in."
    )
    if user.get("email") and not user.get("emailVerified"):
        log("note: their email is not verified, so only an invitation bound to their user id works")
    return True


# --------------------------------------------------------------------------- status


def status(kcadm: Kcadm) -> list[str]:
    realm = kcadm.get(f"realms/{REALM}")
    flows = {flow["alias"] for flow in kcadm.get("authentication/flows")}
    checker = Flows(kcadm, lambda _: None)
    lines = []
    for spec in (FIRST_BROKER, POST_BROKER):
        if spec.alias not in flows:
            state = "missing (run apply)"
        elif checker.actual(spec.alias) == [step.key() for step in spec.steps]:
            state = "in place"
        else:
            state = "changed in the console (apply rebuilds it)"
        lines.append(f"flow {spec.alias}: {state}")
    providers = _providers(kcadm)
    for provider in PROVIDERS:
        idp = providers.get(provider)
        if idp is None:
            lines.append(f"{provider}: not configured")
            continue
        ok = idp.get("firstBrokerLoginFlowAlias") == FIRST_BROKER_FLOW and idp.get("postBrokerLoginFlowAlias") == POST_BROKER_FLOW
        lines.append(
            f"{provider}: {'enabled' if idp.get('enabled') else 'disabled'}, client {idp.get('config', {}).get('clientId')}"
            + ("" if ok else "  (not using the ANUM flows: run apply)")
        )
    lines.append(
        "self sign-up: "
        + (
            f"on (flow {realm.get('registrationFlow')}, theme {realm.get('loginTheme') or 'default'}, policy {realm.get('passwordPolicy') or 'none'})"
            if realm.get("registrationAllowed")
            else "off"
        )
    )
    configured = [p for p in PROVIDERS if p in providers and providers[p].get("enabled")]
    lines.append(
        "API setting: ANUM_SIGN_IN_PROVIDERS=" + ",".join(configured)
        + f"  ANUM_SELF_SIGN_UP={'true' if realm.get('registrationAllowed') else 'false'}"
    )
    return lines


def main(argv: Sequence[str], stdin: Any = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument(
        "action",
        choices=[
            "apply",
            "set-provider",
            "remove-provider",
            "enable-sign-up",
            "disable-sign-up",
            "pending-users",
            "assign-tenant",
            "status",
        ],
    )
    parser.add_argument("--provider", default="", help="set-provider/remove-provider: google or apple")
    parser.add_argument("--privacy-url", default="", help="enable-sign-up: the published privacy policy")
    parser.add_argument("--username", default="", help="assign-tenant: the anum realm user")
    parser.add_argument("--tenant", default="", help="assign-tenant: the tenant id")
    parser.add_argument("--workspace", default="", help="assign-tenant: optional default workspace id")
    parser.add_argument("--config", default="", help="kcadm's --config file, if not the default")
    parser.add_argument("kcadm", nargs="+", help="the logged-in kcadm command, after --")
    args = parser.parse_args(argv)
    kcadm = Kcadm(args.kcadm, config=args.config)
    try:
        if args.action == "apply":
            converge_flows(kcadm, print)
            found = repoint_providers(kcadm, print)
            print(f"social sign-in flows are configured; providers: {', '.join(found) or 'none yet'}")
        elif args.action == "set-provider":
            set_provider(kcadm, args.provider, (stdin or sys.stdin).read())
        elif args.action == "remove-provider":
            remove_provider(kcadm, args.provider)
        elif args.action in ("enable-sign-up", "disable-sign-up"):
            sign_up(kcadm, args.action == "enable-sign-up", args.privacy_url)
        elif args.action == "pending-users":
            print("\n".join(pending_users(kcadm)))
        elif args.action == "assign-tenant":
            assign_tenant(kcadm, args.username, args.tenant, args.workspace)
        else:
            print("\n".join(status(kcadm)))
    except KcadmError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
