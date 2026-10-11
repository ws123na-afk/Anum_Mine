"""Two-factor sign-in for the `anum` realm on a shared Keycloak (docs/identity.md#two-factor-sign-in).

    python3 two_factor.py apply --rp-id <auth host> [--config <kcadm config>] -- <kcadm command...>
    python3 two_factor.py status [--config <kcadm config>] -- <kcadm command...>
    python3 two_factor.py reset-user --username <name> [--config <kcadm config>] -- <kcadm command...>

`infra/oracle/keycloak-2fa.sh` logs kcadm in inside the Keycloak container and runs this
with ``docker compose exec -T keycloak /opt/keycloak/bin/kcadm.sh`` as the kcadm command
and the container's token file as ``--config``. Any kcadm already logged in to a
master-realm admin works.

`apply` converges the realm to the state below and changes nothing when it is already
there, so it is safe to run again (after a Keycloak upgrade, for example):

* The browser flow ``anum-browser-2fa`` is bound as the realm's browser flow. It is a
  copy of Keycloak's ``browser`` flow (cookie, identity-provider redirector,
  organizations, the username and password page with passkeys) whose second-factor
  step is replaced by ``anum-2fa``:

      anum-2fa                          CONDITIONAL
        Condition - credential          REQUIRED  passkey (webauthn-passwordless) not used
        anum-2fa-verify                 CONDITIONAL
          Condition - user configured   REQUIRED
          OTP Form                      ALTERNATIVE
          Recovery Authentication Code  ALTERNATIVE
        anum-2fa-enrol                  CONDITIONAL
          Condition - sub-flow executed REQUIRED  anum-2fa-verify not executed
          OTP Form                      REQUIRED

  Every user who signs in with a password needs a one-time code or a recovery code. A
  user with neither (everyone, the first time) is sent to set up an authenticator app
  before the sign-in completes. Signing in with a passkey skips the second step: a
  passkey with user verification is already two factors.
* OTP policy: TOTP, HmacSHA1, 6 digits, 30 seconds, codes not reusable (the settings
  every authenticator app accepts).
* WebAuthn passwordless (passkey) policy: relying party = the auth host, user
  verification required, discoverable credential required, and passkeys offered on the
  username and password page (``webAuthnPolicyPasswordlessPasskeysEnabled``, Keycloak
  26.4 and later).
* Required actions "Configure OTP" (also a default action for new users), "Webauthn
  Register Passwordless" and "Recovery Authentication Codes" enabled.

Clients, users, roles and Keycloak's built-in flows are not touched. Standard library
only, so the VM's python3 runs it without installing anything.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

REALM = "anum"
SOURCE_FLOW = "browser"
FLOW_ALIAS = "anum-browser-2fa"
SECOND_FACTOR = "anum-2fa"
VERIFY = "anum-2fa-verify"
ENROL = "anum-2fa-enrol"

OTP_POLICY: dict[str, Any] = {
    "otpPolicyType": "totp",
    "otpPolicyAlgorithm": "HmacSHA1",
    "otpPolicyDigits": 6,
    "otpPolicyPeriod": 30,
    "otpPolicyLookAheadWindow": 1,
    "otpPolicyCodeReusable": False,
}

# Everything but the relying party id, which is the deployment's auth host.
PASSKEY_POLICY: dict[str, Any] = {
    "webAuthnPolicyPasswordlessRpEntityName": "ANUM",
    "webAuthnPolicyPasswordlessUserVerificationRequirement": "required",
    "webAuthnPolicyPasswordlessRequireResidentKey": "Yes",
    "webAuthnPolicyPasswordlessSignatureAlgorithms": ["ES256", "RS256"],
    "webAuthnPolicyPasswordlessPasskeysEnabled": True,
}

REQUIRED_ACTIONS: dict[str, dict[str, bool]] = {
    "CONFIGURE_TOTP": {"enabled": True, "defaultAction": True},
    "webauthn-register-passwordless": {"enabled": True},
    "CONFIGURE_RECOVERY_AUTHN_CODES": {"enabled": True},
}


@dataclass(frozen=True)
class Step:
    """One execution of the second-factor sub-flow, depth-first."""

    level: int  # relative to anum-2fa itself (0)
    requirement: str
    provider: str = ""  # authenticator provider id; empty for a sub-flow
    flow: str = ""  # sub-flow alias; empty for an authenticator
    config: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def key(self) -> tuple[Any, ...]:
        return (
            self.level,
            self.requirement,
            self.provider,
            self.flow,
            tuple(sorted(self.config.items())),
        )


SECOND_FACTOR_STEPS: tuple[Step, ...] = (
    Step(
        0,
        "CONDITIONAL",
        flow=SECOND_FACTOR,
        description="Second factor for every password sign-in; skipped after a passkey.",
    ),
    Step(
        1,
        "REQUIRED",
        provider="conditional-credential",
        config={"credentials": "webauthn-passwordless", "included": "false"},
    ),
    Step(1, "CONDITIONAL", flow=VERIFY, description="A one-time code or a recovery code."),
    Step(2, "REQUIRED", provider="conditional-user-configured"),
    Step(2, "ALTERNATIVE", provider="auth-otp-form"),
    Step(2, "ALTERNATIVE", provider="auth-recovery-authn-code-form"),
    Step(
        1,
        "CONDITIONAL",
        flow=ENROL,
        description="No second factor yet: set up an authenticator app now.",
    ),
    Step(
        2,
        "REQUIRED",
        provider="conditional-sub-flow-executed",
        config={"flow_to_check": VERIFY, "check_result": "not-executed"},
    ),
    Step(2, "REQUIRED", provider="auth-otp-form"),
)

CONFIG_ALIASES = {
    "conditional-credential": "anum-2fa-unless-passkey",
    "conditional-sub-flow-executed": "anum-2fa-enrol-unless-verified",
}


class KcadmError(RuntimeError):
    pass


@dataclass
class Kcadm:
    """A logged-in kcadm command. JSON bodies go through stdin (``-f -``), never argv."""

    command: Sequence[str]
    config: str = ""  # kcadm's --config file; kcadm wants options after the sub-command
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run

    def _run(self, args: Sequence[str], body: Any = None) -> str:
        argv = [*self.command, *args, "-r", REALM]
        if self.config:
            argv += ["--config", self.config]
        if body is not None:
            argv += ["-f", "-"]
        result = self.runner(
            argv,
            input=None if body is None else json.dumps(body),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
            raise KcadmError(
                f"kcadm {args[0]} {args[1]}: {lines[-1] if lines else f'exit {result.returncode}'}"
            )
        return result.stdout

    def get(self, path: str, **query: str) -> Any:
        args = ["get", path]
        for key, value in query.items():
            args += ["-q", f"{key}={value}"]
        return json.loads(self._run(args) or "null")

    def create(self, path: str, body: Any) -> str:
        return self._run(["create", path, "-i"], body).strip()

    def update(self, path: str, body: Any) -> None:
        self._run(["update", path], body)

    def delete(self, path: str) -> None:
        self._run(["delete", path])


def children(executions: list[dict[str, Any]], parent: int | None) -> list[int]:
    """Indexes of the direct children of ``executions[parent]`` (None: the top level)."""
    level = 0 if parent is None else executions[parent]["level"] + 1
    found = []
    for index in range(0 if parent is None else parent + 1, len(executions)):
        if executions[index]["level"] < level:
            break
        if executions[index]["level"] == level:
            found.append(index)
    return found


def subtree(executions: list[dict[str, Any]], root: int) -> list[dict[str, Any]]:
    end = root + 1
    while end < len(executions) and executions[end]["level"] > executions[root]["level"]:
        end += 1
    return executions[root:end]


def find_forms(executions: list[dict[str, Any]]) -> int:
    """The top-level sub-flow that holds the username and password page."""
    for index in children(executions, None):
        if executions[index].get("authenticationFlow") and any(
            executions[child].get("providerId") == "auth-username-password-form"
            for child in children(executions, index)
        ):
            return index
    raise KcadmError(f"flow {FLOW_ALIAS} has no username and password sub-flow")


class Realm:
    def __init__(self, kcadm: Kcadm, log: Callable[[str], None]) -> None:
        self.kcadm = kcadm
        self.log = log
        self.path = f"authentication/flows/{FLOW_ALIAS}/executions"

    def executions(self) -> list[dict[str, Any]]:
        return self.kcadm.get(self.path)

    def flow_alias(self, execution: dict[str, Any]) -> str:
        return self.kcadm.get(f"authentication/flows/{execution['flowId']}")["alias"]

    def actual_steps(self, executions: list[dict[str, Any]], root: int) -> list[Step]:
        base = executions[root]["level"]
        steps = []
        for execution in subtree(executions, root):
            config: dict[str, str] = {}
            if execution.get("authenticationConfig"):
                config = (
                    self.kcadm.get(
                        f"authentication/config/{execution['authenticationConfig']}"
                    ).get("config")
                    or {}
                )
            steps.append(
                Step(
                    execution["level"] - base,
                    execution.get("requirement", ""),
                    provider=""
                    if execution.get("authenticationFlow")
                    else execution.get("providerId", ""),
                    flow=self.flow_alias(execution) if execution.get("authenticationFlow") else "",
                    config=config,
                )
            )
        return steps

    def set_requirement(self, execution_id: str, requirement: str) -> None:
        for execution in self.executions():
            if execution["id"] == execution_id:
                if execution.get("requirement") != requirement:
                    self.kcadm.update(self.path, {**execution, "requirement": requirement})
                return
        raise KcadmError(f"execution {execution_id} vanished from {FLOW_ALIAS}")

    def build_second_factor(self, forms_alias: str) -> None:
        parents = {0: forms_alias}
        for step in SECOND_FACTOR_STEPS:
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
                    for e in self.executions()
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
                        {"alias": CONFIG_ALIASES[step.provider], "config": step.config},
                    )
            self.set_requirement(execution_id, step.requirement)

    def converge_flow(self) -> bool:
        """Make anum-browser-2fa match SECOND_FACTOR_STEPS; True when something changed."""
        changed = False
        if FLOW_ALIAS not in {flow["alias"] for flow in self.kcadm.get("authentication/flows")}:
            self.kcadm.create(f"authentication/flows/{SOURCE_FLOW}/copy", {"newName": FLOW_ALIAS})
            self.log(f"created flow {FLOW_ALIAS} as a copy of {SOURCE_FLOW}")
            changed = True

        executions = self.executions()
        forms = find_forms(executions)
        forms_alias = self.flow_alias(executions[forms])
        wanted = [step.key() for step in SECOND_FACTOR_STEPS]
        for child in children(executions, forms):
            execution = executions[child]
            if execution.get("providerId") == "auth-username-password-form":
                continue
            if (
                execution.get("authenticationFlow")
                and self.flow_alias(execution) == SECOND_FACTOR
                and [step.key() for step in self.actual_steps(executions, child)] == wanted
            ):
                return changed
        # Anything else after the password page (Keycloak's own "Conditional 2FA" from
        # the copy, or an anum-2fa that drifted) is replaced in one pass.
        for child in reversed(children(executions, forms)):
            execution = executions[child]
            if execution.get("providerId") == "auth-username-password-form":
                continue
            self.kcadm.delete(f"authentication/executions/{execution['id']}")
            self.log(f"removed '{execution.get('displayName')}' from {forms_alias}")
        self.build_second_factor(forms_alias)
        self.log(f"built {SECOND_FACTOR} after the password page")
        return True


def apply(kcadm: Kcadm, rp_id: str, log: Callable[[str], None] = print) -> None:
    if not rp_id or any(character in rp_id for character in "/: "):
        raise KcadmError("--rp-id must be the auth host name alone, for example auth.example.com")

    actions = {action["alias"]: action for action in kcadm.get("authentication/required-actions")}
    for alias, fields in REQUIRED_ACTIONS.items():
        action = actions.get(alias)
        if action is None:
            raise KcadmError(
                f"required action {alias} is not registered; Keycloak 26.4 or later is needed"
            )
        if any(action.get(key) != value for key, value in fields.items()):
            kcadm.update(f"authentication/required-actions/{alias}", {**action, **fields})
            log(f"required action {alias}: {', '.join(f'{k}={v}' for k, v in fields.items())}")

    # The flow is complete before it is bound, so no sign-in ever sees half of it.
    Realm(kcadm, log).converge_flow()

    # One full-representation update: a partial realm update resets every WebAuthn
    # policy field it leaves out (Keycloak 26.4 rebuilds the policy from the request).
    realm = kcadm.get(f"realms/{REALM}")
    wanted = {
        **OTP_POLICY,
        **PASSKEY_POLICY,
        "webAuthnPolicyPasswordlessRpId": rp_id,
        "browserFlow": FLOW_ALIAS,
    }
    changed = {key: value for key, value in wanted.items() if realm.get(key) != value}
    if changed:
        kcadm.update(f"realms/{REALM}", {**realm, **changed})
        for key in sorted(changed):
            log(f"realm {key}: {realm.get(key)!r} -> {changed[key]!r}")
    log("two-factor sign-in is configured")


SECOND_FACTOR_CREDENTIALS = ("otp", "recovery-authn-codes")


def reset_user(kcadm: Kcadm, username: str, log: Callable[[str], None] = print) -> None:
    """Remove a user's authenticator app and recovery codes and lift a brute-force lockout.

    The password and any passkeys stay. At the next password sign-in the user is sent to
    set up an authenticator app again (anum-2fa-enrol).
    """
    users = [
        user
        for user in kcadm.get("users", username=username, exact="true")
        if user.get("username") == username
    ]
    if len(users) != 1:
        raise KcadmError(f"no user named {username!r} in realm {REALM}")
    user_id = users[0]["id"]
    removed = []
    for credential in kcadm.get(f"users/{user_id}/credentials"):
        if credential.get("type") in SECOND_FACTOR_CREDENTIALS:
            kcadm.delete(f"users/{user_id}/credentials/{credential['id']}")
            label = credential.get("userLabel")
            removed.append(credential["type"] + (f" ({label})" if label else ""))
    kcadm.delete(f"attack-detection/brute-force/users/{user_id}")
    log(
        f"{username}: removed {', '.join(removed) if removed else 'nothing (no authenticator app or recovery codes)'}"
        "; sign-in lockout cleared. They set up an authenticator app at the next sign-in."
    )


def _passkey_policy(realm: dict[str, Any]) -> dict[str, Any]:
    """The realm's passkey (WebAuthn passwordless) policy settings, by their short names
    (``RpId``, ``UserVerificationRequirement``, ...). These are public policy values,
    never credentials."""
    prefix = "webAuthnPolicy" + "Passwordless"
    return {key[len(prefix):]: value for key, value in realm.items() if key.startswith(prefix)}


def status(kcadm: Kcadm) -> list[str]:
    realm = kcadm.get(f"realms/{REALM}")
    passkeys = _passkey_policy(realm)
    lines = [
        f"browser flow: {realm.get('browserFlow')}"
        + (
            ""
            if realm.get("browserFlow") == FLOW_ALIAS
            else f"  (expected {FLOW_ALIAS}: run apply)"
        ),
        "OTP policy: {} {} {} digits {}s".format(
            realm.get("otpPolicyType"),
            realm.get("otpPolicyAlgorithm"),
            realm.get("otpPolicyDigits"),
            realm.get("otpPolicyPeriod"),
        ),
        "passkeys: {} (relying party {!r}, user verification {})".format(
            "offered" if passkeys.get("PasskeysEnabled") else "off",
            passkeys.get("RpId") or "request host",
            passkeys.get("UserVerificationRequirement"),
        ),
    ]
    if FLOW_ALIAS in {flow["alias"] for flow in kcadm.get("authentication/flows")}:
        lines.append(f"{FLOW_ALIAS}:")
        for execution in kcadm.get(f"authentication/flows/{FLOW_ALIAS}/executions"):
            lines.append(
                "  " * (execution["level"] + 1)
                + f"{execution.get('displayName')} [{execution.get('requirement')}]"
            )
    return lines


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("action", choices=["apply", "status", "reset-user"])
    parser.add_argument(
        "--rp-id", default="", help="apply: the auth host, for example auth.example.com"
    )
    parser.add_argument("--username", default="", help="reset-user: the anum realm user")
    parser.add_argument("--config", default="", help="kcadm's --config file, if not the default")
    parser.add_argument("kcadm", nargs="+", help="the logged-in kcadm command, after --")
    args = parser.parse_args(argv)
    kcadm = Kcadm(args.kcadm, config=args.config)
    try:
        if args.action == "apply":
            apply(kcadm, args.rp_id)
        elif args.action == "reset-user":
            if not args.username:
                raise KcadmError("reset-user needs --username")
            reset_user(kcadm, args.username)
        else:
            print("\n".join(status(kcadm)))
    except KcadmError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
