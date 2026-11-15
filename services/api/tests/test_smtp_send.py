"""The SMTP path of system email (docs/notifications.md#email, docs/deploy-oracle.md#outgoing-mail).

``smtplib`` is replaced by a fake server that records what it was asked to do, so these tests
check the bytes ANUM would hand to a real mail server: the sender and envelope, the headers
receivers require, the TLS mode for the port, and that a failure is logged without the
recipient's address or a credential.
"""

from __future__ import annotations

import logging
import smtplib
from email import message_from_bytes
from typing import Any

import pytest

from anum_api import notifications
from anum_api.notifications import EmailRefused
from anum_api.settings import settings


class FakeSMTP:
    sent: list[dict[str, Any]] = []
    starttls_offered = True

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        self.host, self.port, self.tls = host, port, False

    def ehlo(self) -> None:
        pass

    def has_extn(self, name: str) -> bool:
        return name == "starttls" and self.starttls_offered

    def starttls(self, context: Any = None) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.login_as = user

    def send_message(self, message: Any, from_addr: str | None = None, to_addrs: list[str] | None = None) -> None:
        FakeSMTP.sent.append({"raw": message.as_bytes(), "from": from_addr, "to": to_addrs, "tls": self.tls, "port": self.port})

    def quit(self) -> None:
        pass


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        super().__init__(host, port, timeout, context)
        self.tls = True


@pytest.fixture(autouse=True)
def server(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.sent = []
    FakeSMTP.starttls_offered = True
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(settings, "smtp_host", "smtp-relay.example.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_security", "starttls")
    monkeypatch.setattr(settings, "smtp_from", "ANUM <notifications@anum.example>")
    monkeypatch.setattr(settings, "smtp_username", None)
    monkeypatch.setattr(settings, "smtp_password", None)


def test_a_message_has_the_headers_receivers_require_and_a_matching_envelope() -> None:
    notifications.send_email("person@example.org", "ANUM: Task\r\nBcc: victim@example.net", "Body")
    [sent] = FakeSMTP.sent
    assert sent["tls"] and sent["port"] == 587
    assert sent["from"] == "notifications@anum.example" and sent["to"] == ["person@example.org"]
    message = message_from_bytes(sent["raw"])
    assert message["From"] == "ANUM <notifications@anum.example>"
    assert message["To"] == "person@example.org"
    assert message["Date"]
    assert message["Message-ID"].endswith("@anum.example>")
    # A title with a line break stays one Subject line: it can never add a header.
    assert message["Subject"] == "ANUM: Task Bcc: victim@example.net"
    assert message["Bcc"] is None


def test_implicit_tls_on_465() -> None:
    settings.smtp_security, settings.smtp_port = "tls", 465
    notifications.send_email("person@example.org", "s", "t")
    assert FakeSMTP.sent[0]["tls"] and FakeSMTP.sent[0]["port"] == 465


@pytest.mark.parametrize(("security", "port"), [("starttls", 465), ("tls", 587), ("tls", 25)])
def test_a_port_that_does_not_match_the_tls_mode_is_refused_at_once(security: str, port: int) -> None:
    settings.smtp_security, settings.smtp_port = security, port
    with pytest.raises(EmailRefused) as refused:
        notifications.send_email("person@example.org", "s", "t")
    assert not refused.value.transient and "ANUM_SMTP_PORT" in str(refused.value)
    assert FakeSMTP.sent == []


@pytest.mark.parametrize("sender", ["notifications", "a@b", "one@example.com, two@example.com", "x@example.com\nBcc: y@example.com"])
def test_an_unusable_sender_is_refused_with_the_setting_named(sender: str) -> None:
    settings.smtp_from = sender
    with pytest.raises(EmailRefused) as refused:
        notifications.send_email("person@example.org", "s", "t")
    assert "ANUM_SMTP_FROM" in str(refused.value) and not refused.value.transient
    assert notifications.sender_problem()


def test_a_bad_recipient_is_refused_not_raised_as_a_crash() -> None:
    with pytest.raises(EmailRefused):
        notifications.send_email("not an address", "s", "t")
    with pytest.raises(EmailRefused):
        notifications.send_email("a@example.org\r\nBcc: b@example.org", "s", "t")


def test_a_login_without_its_password_is_refused() -> None:
    settings.smtp_username = "notifications@anum.example"
    with pytest.raises(EmailRefused) as refused:
        notifications.send_email("person@example.org", "s", "t")
    assert "ANUM_SMTP_PASSWORD" in str(refused.value)


def test_no_starttls_means_no_send() -> None:
    FakeSMTP.starttls_offered = False
    with pytest.raises(EmailRefused):
        notifications.send_email("person@example.org", "s", "t")
    assert FakeSMTP.sent == []


def test_a_failure_is_logged_with_the_domain_only(caplog: pytest.LogCaptureFixture) -> None:
    settings.smtp_from = "broken"
    with caplog.at_level(logging.WARNING, logger="anum_api.notifications"):
        outcome = notifications.send_with_retries("private.person@example.org", "s", "t", backoff_seconds=0)
    assert outcome.status == "failed" and outcome.attempts == 1 and not outcome.transient
    [record] = [r for r in caplog.records if r.getMessage() == "System email not sent"]
    assert record.anum_mail_domain == "example.org"
    assert "private.person" not in repr(record.__dict__)


def test_setup_page_names_the_setting_to_fix() -> None:
    from anum_api.integration_setup import _smtp
    from anum_api.schemas import TenantContext

    context = TenantContext(tenant_id="t", workspace_id="w", user_id="u", roles=["owner"])
    assert _smtp(context)["status"] == "connected"
    settings.smtp_port = 465
    assert _smtp(context)["missing"] == ["ANUM_SMTP_PORT", "ANUM_SMTP_SECURITY"]
    settings.smtp_port, settings.smtp_from = 587, "nobody"
    assert _smtp(context)["missing"] == ["ANUM_SMTP_FROM"]
