"""Optional error reporting to a Sentry-compatible server (anum_api/error_reporting.py).

The scrubbing rules are tested on plain event dictionaries, and once end to end through
the real SDK in a child process (the SDK patches Starlette process-wide, so it never
starts inside the test session): an exception raised by a FastAPI route with an
Authorization header, a cookie, a query string and a request body reaches the transport
with none of them.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from anum_api import error_reporting
from anum_api.error_reporting import (
    CORRELATION_TAG,
    REDACTED,
    TENANT_TAG,
    dsn_problem,
    insecure_dsn_problem,
    scrub_breadcrumb,
    scrub_event,
    setup_error_reporting,
)
from anum_api.hardening import insecure_configuration_problems
from anum_api.request_context import _correlation_id
from anum_api.settings import Settings

API_ROOT = Path(__file__).resolve().parents[1]
# Plainly fake, low-entropy values (gitleaks): the shape of a DSN, not a real key.
INTERNAL_DSN = "http://examplekey@glitchtip:8000/1"
PUBLIC_DSN = "https://examplekey@errors.example.org/1"


# --------------------------------------------------------------------------- settings


@pytest.mark.parametrize("dsn", [INTERNAL_DSN, PUBLIC_DSN, "https://examplekey@errors.example.org/sub/path/42", ""])
def test_valid_dsns_are_accepted(dsn: str) -> None:
    assert dsn_problem(dsn) is None


@pytest.mark.parametrize(
    "dsn",
    [
        "glitchtip:8000/1",
        "ftp://examplekey@errors.example.org/1",
        "https://errors.example.org/1",  # no public key
        "https://examplekey@errors.example.org/",  # no project id
        "https://examplekey@errors.example.org/project",
        "https://examplekey@errors.example.org/1?x=1",
        "https://examplekey@errors.example.org:notaport/1",
    ],
)
def test_malformed_dsns_are_refused_without_echoing_them(dsn: str) -> None:
    assert dsn_problem(dsn) is not None
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, sentry_dsn=dsn)
    assert "examplekey" not in str(caught.value)
    assert "ANUM_SENTRY_DSN" in str(caught.value)


def test_dsn_is_off_by_default_and_never_printed() -> None:
    assert Settings(_env_file=None).sentry_dsn is None
    configured = Settings(_env_file=None, sentry_dsn=PUBLIC_DSN)
    assert "examplekey" not in repr(configured)
    assert error_reporting.configured_dsn(configured) == PUBLIC_DSN
    assert error_reporting.configured_dsn(Settings(_env_file=None, sentry_dsn="  ")) == ""


@pytest.mark.parametrize(
    ("dsn", "refused"),
    [
        (PUBLIC_DSN, False),
        (INTERNAL_DSN, False),  # a private compose or Kubernetes service name
        ("http://examplekey@errors.example.org/1", True),
        ("http://examplekey@10.0.0.5:8000/1", True),
        ("http://examplekey@localhost:8000/1", True),
        ("http://examplekey@[::1]:8000/1", True),
    ],
)
def test_startup_policy_refuses_clear_text_dsns_outside_local(dsn: str, refused: bool) -> None:
    assert (insecure_dsn_problem(dsn) is not None) is refused
    settings = Settings(
        _env_file=None,
        environment="staging",
        secrets_key=Fernet.generate_key().decode(),
        database_url="postgresql+psycopg://anum_app:example@db:5432/anum",
        cors_origins=["https://app.example.org"],
        sentry_dsn=dsn,
    )
    problems = insecure_configuration_problems(settings)
    assert any("ANUM_SENTRY_DSN" in problem for problem in problems) is refused
    assert not any("examplekey" in problem for problem in problems)
    # Local development may point anywhere.
    local = Settings(_env_file=None, environment="local", sentry_dsn=dsn)
    assert insecure_configuration_problems(local) == []


def test_setup_is_a_no_op_without_a_dsn() -> None:
    assert setup_error_reporting(SimpleNamespace(sentry_dsn=None, environment="staging"), service_name="x") is False
    assert setup_error_reporting(SimpleNamespace(sentry_dsn=SecretStr(""), environment="staging"), service_name="x") is False
    assert error_reporting.is_enabled() is False
    error_reporting.tag_tenant("tenant_a")  # never imports or touches the SDK when off
    error_reporting.shutdown_error_reporting()


# --------------------------------------------------------------------------- scrubbing


def _event() -> dict:
    return {
        "user": {"id": "user_1", "email": "person@example.org", "ip_address": "203.0.113.7"},
        "extra": {"prompt": "secret prompt"},
        "request": {
            "method": "POST",
            "url": "https://api.example.org/api/v1/tasks?token=abc#frag",
            "query_string": "token=abc",
            "cookies": {"session": "abc"},
            "data": {"prompt": "secret prompt"},
            "env": {"REMOTE_ADDR": "203.0.113.7"},
            "headers": {
                "Authorization": "Bearer abc",
                "Cookie": "session=abc",
                "X-Api-Key": "abc",
                "User-Agent": "Mozilla",
                "Content-Type": "application/json",
            },
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad input: secret prompt",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"password": "abc"}}]},
                }
            ]
        },
        "threads": {"values": [{"stacktrace": {"frames": [{"function": "g", "vars": {"token": "abc"}}]}}]},
        "logentry": {"message": "run %s failed", "params": ["secret prompt"], "formatted": "run secret prompt failed"},
        "contexts": {"runtime": {"name": "CPython"}, "trace": {"trace_id": "t"}, "state": {"prompt": "x"}},
        "breadcrumbs": {
            "values": [
                {"category": "httplib", "data": {"url": "https://x.example/a?key=abc", "method": "GET", "body": "x"}},
                {"category": "query", "message": "select 1", "data": {"params": ["abc"]}},
            ]
        },
        "tags": {TENANT_TAG: "tenant_a"},
    }


def test_scrub_event_keeps_only_types_frames_routes_and_ids() -> None:
    token = _correlation_id.set("corr_example")
    try:
        event = scrub_event(_event(), {})
    finally:
        _correlation_id.reset(token)
    assert "user" not in event and "extra" not in event
    assert event["request"] == {
        "method": "POST",
        "url": "https://api.example.org/api/v1/tasks",
        "headers": {"Content-Type": "application/json"},
    }
    exception = event["exception"]["values"][0]
    assert exception["type"] == "ValueError" and exception["value"] == REDACTED
    assert exception["stacktrace"]["frames"] == [{"function": "f"}]
    assert event["threads"]["values"][0]["stacktrace"]["frames"] == [{"function": "g"}]
    assert event["logentry"] == {"message": "run %s failed"}
    assert set(event["contexts"]) == {"runtime", "trace"}
    crumbs = event["breadcrumbs"]["values"]
    assert crumbs[0]["data"] == {"url": "https://x.example/a", "method": "GET"}
    assert "data" not in crumbs[1]
    assert event["tags"] == {TENANT_TAG: "tenant_a", CORRELATION_TAG: "corr_example"}
    text = json.dumps(event)
    for leaked in ("abc", "secret prompt", "203.0.113.7", "person@example.org", "Mozilla"):
        assert leaked not in text, leaked


def test_log_breadcrumbs_keep_the_template_only() -> None:
    record = logging.LogRecord("anum", logging.INFO, __file__, 1, "user %s signed in", ("person@example.org",), None)
    crumb = scrub_breadcrumb(
        {"category": "anum", "message": "user person@example.org signed in", "data": {"extra": "x"}},
        {"log_record": record},
    )
    assert crumb == {"category": "anum", "message": "user %s signed in"}


# --------------------------------------------------------------------------- the real SDK

CHILD = textwrap.dedent(
    """
    import json, logging, sys
    from types import SimpleNamespace

    import sentry_sdk
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sentry_sdk.transport import Transport

    from anum_api import error_reporting
    from anum_api.request_context import CorrelationIdMiddleware

    events = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.type == "event":
                    events.append(item.payload.json)

    config = SimpleNamespace(sentry_dsn=SecretStr(sys.argv[1]), environment="staging")
    assert error_reporting.setup_error_reporting(config, service_name="anum-api", transport=Capture)

    def tenant():
        error_reporting.tag_tenant("tenant_a")
        return "tenant_a"

    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.post("/api/v1/boom/{item_id}")
    def boom(item_id: str, _: str = Depends(tenant)):
        password = "examplelocal"  # a local variable that must not be sent
        logging.getLogger("anum.test").info("handling %s", "examplelogarg")
        raise RuntimeError("examplemessage " + password)

    @app.get("/api/v1/other")
    def other():
        raise KeyError("exampleother")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/boom/item_1?token=examplequery",
        headers={"Authorization": "Bearer exampletoken", "Cookie": "session=examplecookie",
                 "X-Correlation-ID": "corr_child"},
        json={"prompt": "examplebody"},
    )
    assert response.status_code == 500
    # A later request without a tenant must not inherit the first request's tag.
    assert client.get("/api/v1/other").status_code == 500
    sentry_sdk.flush(2)
    print(json.dumps(events))
    """
)


def test_real_sdk_sends_scrubbed_events_with_the_tenant_tag_only(tmp_path: Path) -> None:
    pytest.importorskip("sentry_sdk")
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("ANUM_")}
    environment["PYTHONPATH"] = str(API_ROOT)
    result = subprocess.run(
        [sys.executable, str(script), PUBLIC_DSN],
        capture_output=True,
        text=True,
        env=environment,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    events = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(events) == 2, events
    first, second = events
    assert first["exception"]["values"][-1]["type"] == "RuntimeError"
    assert first["exception"]["values"][-1]["value"] == REDACTED
    assert first["tags"][TENANT_TAG] == "tenant_a"
    assert first["tags"][CORRELATION_TAG] == "corr_child"
    assert first["request"]["method"] == "POST"
    assert first["request"]["url"].endswith("/api/v1/boom/item_1")
    assert first["environment"] == "staging" and first["server_name"] == "anum-api"
    assert "user" not in first
    assert TENANT_TAG not in second.get("tags", {})
    text = result.stdout
    for leaked in (
        "exampletoken",
        "examplecookie",
        "examplequery",
        "examplebody",
        "examplemessage",
        "examplelocal",
        "examplelogarg",
        "exampleother",
        "examplekey",
    ):
        assert leaked not in text, leaked
