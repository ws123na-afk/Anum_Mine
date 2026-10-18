"""``ANUM_MAILSERVER_*`` operator settings for the Email Center (docs/email.md#email-center).

Nothing here is a credential: each person's mailbox password is entered in the web app and
stored encrypted per mailbox. These settings say which mail server ANUM may use and bound
what it does. The feature is off until ``ANUM_MAILSERVER_ENABLED=true``.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Outgoing mail goes to the workspace's own mail server (which may relay it onward), never
# straight to a relay or bulk-mail service: those hosts are refused as the SMTP host.
RELAY_HOST_MARKERS = ("brevo.com", "sendinblue.com", "mailjet.com", "sendgrid.net", "mailgun.org", "amazonaws.com")


class MailServerSettings(BaseSettings):
    enabled: bool = False
    # IMAP over implicit TLS only (port 993 by default); there is no plain-text option.
    imap_host: str = ""
    imap_port: int = Field(default=993, ge=1, le=65535)
    # SMTP submission: "starttls" (587, required upgrade) or "tls" (465). "none" only in local.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    # Mailbox domains people may connect (comma-separated) and the organisations (tenant ids,
    # comma-separated) allowed to use the mail server at all. Empty tenants: nobody outside local.
    allowed_domains: str = "anum360os.com"
    allowed_tenants: str = ""
    timeout_seconds: float = Field(default=20.0, ge=2, le=120)
    # The largest message ANUM opens (bytes, attachments included) and the largest attachment it serves.
    message_max_bytes: int = Field(default=40 * 1024 * 1024, ge=64 * 1024, le=200 * 1024 * 1024)
    attachment_max_bytes: int = Field(default=15 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # Sends per mailbox per hour, recipients per message, and connection attempts per person per hour.
    send_per_hour: int = Field(default=30, ge=1, le=1000)
    max_recipients: int = Field(default=50, ge=1, le=500)
    connect_attempts_per_hour: int = Field(default=5, ge=1, le=100)
    # Outgoing attachments (docs/email.md#attachments): the largest file, the most files and the
    # largest total per message. Every file passes the workspace upload checks and the malware scan.
    outbound_attachment_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    outbound_max_attachments: int = Field(default=10, ge=1, le=50)
    outbound_total_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    # Files held on unsent messages per person (sent ones stay until removed from the list).
    outbound_storage_per_person_bytes: int = Field(default=200 * 1024 * 1024, ge=1024 * 1024, le=10 * 1024 * 1024 * 1024)
    # Scheduled send (docs/email.md#scheduled-send): how often each replica looks for due messages
    # (0 is off: scheduled messages then wait) and how far ahead a message may be scheduled.
    schedule_poll_seconds: float = Field(default=30.0, ge=0, le=3600)
    schedule_max_days: int = Field(default=30, ge=1, le=365)
    # Background sync (docs/email.md#background-sync): how often each replica looks for mailboxes
    # due a check (0 is off: counts then come only from the open Email page), how long a mailbox
    # waits between checks, and how many messages forwarding rules may forward per mailbox per pass.
    sync_poll_seconds: float = Field(default=60.0, ge=0, le=3600)
    sync_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    forward_per_pass: int = Field(default=20, ge=1, le=200)

    model_config = SettingsConfigDict(
        env_prefix="ANUM_MAILSERVER_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    @property
    def domains(self) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in self.allowed_domains.split(",") if part.strip())

    @property
    def tenants(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.allowed_tenants.split(",") if part.strip())

    @property
    def configured(self) -> bool:
        return bool(self.imap_host.strip() and self.smtp_host.strip())


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def mailserver_problems(cfg: MailServerSettings) -> list[str]:
    """Why ``cfg`` must not run outside ``ANUM_ENVIRONMENT=local`` (empty when it may).

    Called by ``hardening.insecure_configuration_problems``; the Helm chart refuses the same.
    """
    if not cfg.enabled:
        return []
    problems: list[str] = []
    for name, host in (("ANUM_MAILSERVER_IMAP_HOST", cfg.imap_host), ("ANUM_MAILSERVER_SMTP_HOST", cfg.smtp_host)):
        value = host.strip().lower()
        if not value:
            problems.append(f"{name} is required when ANUM_MAILSERVER_ENABLED=true")
        elif _is_ip_literal(value) or "." not in value:
            problems.append(f"{name} must be the mail server's DNS name (its TLS certificate name), not {host!r}")
    smtp = cfg.smtp_host.strip().lower()
    if any(smtp == marker or smtp.endswith("." + marker) for marker in RELAY_HOST_MARKERS):
        problems.append("ANUM_MAILSERVER_SMTP_HOST must be your own mail server, never a relay service")
    if cfg.smtp_security == "none":
        problems.append("ANUM_MAILSERVER_SMTP_SECURITY must be starttls or tls outside local")
    if not cfg.domains:
        problems.append("ANUM_MAILSERVER_ALLOWED_DOMAINS must list at least one mailbox domain")
    if not cfg.tenants:
        problems.append("ANUM_MAILSERVER_ALLOWED_TENANTS must list the organisations allowed to use the mail server")
    return problems


mail_settings = MailServerSettings()
