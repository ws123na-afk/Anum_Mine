#!/usr/bin/env python3
"""Create or complete infra/oracle/.env from .env.example (docs/deploy-oracle.md).

    python3 infra/oracle/configure_env.py infra/oracle/.env [--example PATH] [--non-interactive]

* Creates the file with mode 600 when it is missing (and forces 600 on every run).
* Adds variables that exist in the example but not yet in the file (after an update),
  keeping every existing value.
* Replaces each ``generate-on-server`` value with a fresh secret from the operating
  system's random generator: a Fernet key (url-safe base64 of 32 bytes, 44 characters)
  for ``ANUM_SECRETS_KEY``, url-safe base64 of 32 bytes without padding otherwise.
* Asks for each value that still contains ``replace-me`` when a terminal is attached
  (hidden input for secrets), and checks host names and the e-mail address.

It never prints a value. Exit status: 0 when the file is complete, 3 when owner values
are still missing (their names are printed), 2 on bad input. Standard library only:
Ubuntu's python3 runs it before Docker exists.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

GENERATE = "generate-on-server"
PLACEHOLDER = "replace-me"
FERNET_KEYS = {"ANUM_SECRETS_KEY"}
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|TOKEN|PASSPHRASE|_KEY$)")
HOST_NAMES = {"ANUM_WEB_HOST", "ANUM_API_HOST", "ANUM_AUTH_HOST"}
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
# Written without quotes when every character is one of these (compose reads `$` in
# unquoted values as interpolation).
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+,=-]*$")


def new_secret(name: str) -> str:
    raw = secrets.token_bytes(32)
    text = base64.urlsafe_b64encode(raw).decode("ascii")
    return text if name in FERNET_KEYS else text.rstrip("=")


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw


def format_value(value: str) -> str:
    if "\n" in value or "'" in value:
        raise ValueError("values may not contain newlines or single quotes")
    return value if SAFE_VALUE.match(value) else f"'{value}'"


def read_entries(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        match = LINE.match(line)
        if match:
            values[match.group(1)] = parse_value(match.group(2))
    return lines, values


def needs_owner(value: str) -> bool:
    return PLACEHOLDER in value


def validate(name: str, value: str) -> str | None:
    if name in HOST_NAMES and not HOSTNAME.match(value):
        return "a host name such as app.example.com (no https://, no slash)"
    if name == "ACME_EMAIL" and not EMAIL.match(value):
        return "an e-mail address"
    if name.endswith("_ENDPOINT") and not value.startswith("https://"):
        return "an https:// URL"
    if needs_owner(value):
        return "your own value, not the placeholder"
    return None


def ask(name: str) -> str | None:
    prompt = f"{name}: "
    for _ in range(3):
        value = getpass.getpass(prompt) if SECRET_NAME.search(name) else input(prompt)
        value = value.strip()
        problem = validate(name, value)
        if problem is None:
            return value
        print(f"  {name} must be {problem}.", file=sys.stderr)
    return None


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def configure(env_path: Path, example_path: Path, *, interactive: bool) -> int:
    example_lines, example_values = read_entries(example_path)
    if env_path.exists():
        lines, values = read_entries(env_path)
    else:
        lines, values = list(example_lines), dict(example_values)
    present = set(values)
    missing_lines = [line for line in example_lines if (m := LINE.match(line)) and m.group(1) not in present]
    if missing_lines:
        lines.append("")
        lines.append("# Added from .env.example by configure_env.py")
        lines.extend(missing_lines)
        for line in missing_lines:
            match = LINE.match(line)
            assert match
            values[match.group(1)] = parse_value(match.group(2))

    updates: dict[str, str] = {}
    for name, value in values.items():
        if value == GENERATE:
            updates[name] = new_secret(name)

    still_missing = []
    for name, value in values.items():
        if name in updates or not needs_owner(value):
            continue
        answer = ask(name) if interactive else None
        if answer is None:
            still_missing.append(name)
        else:
            updates[name] = answer

    output = []
    for line in lines:
        match = LINE.match(line)
        if match and match.group(1) in updates:
            output.append(f"{match.group(1)}={format_value(updates[match.group(1)])}")
        else:
            output.append(line)
    write_private(env_path, "\n".join(output) + "\n")

    generated = sorted(name for name in updates if values[name] == GENERATE)
    if generated:
        print(f"Generated {len(generated)} secrets in {env_path}: {', '.join(generated)}")
    if still_missing:
        print(
            f"Still to fill in {env_path} (edit with: nano {env_path}): " + ", ".join(still_missing),
            file=sys.stderr,
        )
        return 3
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("env", type=Path)
    parser.add_argument("--example", type=Path, default=Path(__file__).with_name(".env.example"))
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    interactive = not args.non_interactive and sys.stdin.isatty()
    try:
        return configure(args.env, args.example, interactive=interactive)
    except ValueError as error:
        print(f"configure_env: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
