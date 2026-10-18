"""Live DNS checks of a mail domain: MX, SPF, DKIM and DMARC (docs/email.md#mail-domains).

Each lookup is a plain DNS query with a timeout (``ANUM_MAILCOW_DNS_TIMEOUT_SECONDS``); the four
run side by side, so a check takes at most about one timeout. Nothing is cached: the page shows
what DNS says now. ANUM never changes DNS; it shows the exact records the owner must set at the
DNS host and flags dangerous ones (an SPF record that lets any server send as the domain).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from ..schemas import utc_now

CheckStatus = Literal["ok", "warning", "danger", "missing", "error"]
DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class LookupFailed(Exception):
    """A lookup that did not give an answer: ``nxdomain`` and ``empty`` mean no record."""

    def __init__(self, kind: Literal["nxdomain", "empty", "timeout", "error"]) -> None:
        super().__init__(kind)
        self.kind = kind


class DnsCheck(BaseModel):
    status: CheckStatus
    found: list[str] = Field(default_factory=list)
    message: str


class ExpectedRecord(BaseModel):
    """A record the owner sets at the DNS host, exactly as shown."""

    kind: Literal["mx", "spf", "dkim", "dmarc"]
    type: Literal["MX", "TXT"]
    name: str
    value: str
    note: str | None = None


class DnsReport(BaseModel):
    domain: str
    checked_at: str
    mail_host: str | None
    mx: DnsCheck
    spf: DnsCheck
    dkim: DnsCheck
    dmarc: DnsCheck
    records: list[ExpectedRecord]


def valid_domain(value: str) -> str | None:
    """The domain in lower case when it is a plain DNS name, else None."""
    domain = value.strip().lower().rstrip(".")
    return domain if DOMAIN_PATTERN.match(domain) else None


def _host(value: str) -> str:
    return value.strip().lower().rstrip(".")


def lookup(name: str, rdtype: Literal["MX", "TXT"], timeout: float) -> list[str]:
    """MX answers as ``"<preference> <host>"``, TXT answers as one joined string each."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    try:
        answer = resolver.resolve(name, rdtype)
    except dns.resolver.NXDOMAIN as exc:
        raise LookupFailed("nxdomain") from exc
    except dns.resolver.NoAnswer as exc:
        raise LookupFailed("empty") from exc
    except (dns.resolver.LifetimeTimeout, dns.exception.Timeout) as exc:
        raise LookupFailed("timeout") from exc
    except dns.exception.DNSException as exc:
        raise LookupFailed("error") from exc
    out: list[str] = []
    for record in answer:
        if rdtype == "MX":
            out.append(f"{record.preference} {_host(record.exchange.to_text())}")
        else:
            out.append(b"".join(record.strings).decode("utf-8", "replace"))
    return out[:20]


def _failed(kind: str, what: str) -> DnsCheck:
    if kind in {"nxdomain", "empty"}:
        return DnsCheck(status="missing", message=f"No {what} record found.")
    if kind == "timeout":
        return DnsCheck(status="error", message=f"The {what} lookup timed out. Check again in a moment.")
    return DnsCheck(status="error", message=f"The {what} lookup failed. Check again in a moment.")


def check_mx(found: list[str], mail_host: str | None) -> DnsCheck:
    hosts = [_host(item.split(" ", 1)[-1]) for item in found]
    if not hosts:
        return DnsCheck(status="missing", message="No MX record: mail for this domain has nowhere to go.")
    if hosts == [""] or "" in hosts:
        return DnsCheck(status="danger", found=found, message="A null MX record says this domain receives no mail.")
    if mail_host is None:
        return DnsCheck(status="ok", found=found, message="MX records exist. Set ANUM_MAILCOW_MAIL_HOST to check they point to the mail server.")
    if mail_host in hosts:
        others = [h for h in hosts if h != mail_host]
        if others:
            return DnsCheck(status="warning", found=found, message=f"MX also points to {', '.join(others)}: mail may go to another server.")
        return DnsCheck(status="ok", found=found, message=f"MX points to {mail_host}.")
    return DnsCheck(status="warning", found=found, message=f"MX points to {', '.join(hosts)}, not to the mail server {mail_host}.")


def spf_records(txt: list[str]) -> list[str]:
    return [item.strip() for item in txt if item.strip().lower().startswith("v=spf1")]


def check_spf(txt: list[str], mail_host: str | None) -> DnsCheck:
    found = spf_records(txt)
    if not found:
        return DnsCheck(status="missing", message="No SPF record: receivers cannot tell which servers may send as this domain.")
    if len(found) > 1:
        return DnsCheck(status="danger", found=found, message="More than one SPF record: receivers treat SPF as broken. Keep exactly one.")
    terms = found[0].lower().split()[1:]
    if any(term in {"+all", "all"} for term in terms):
        return DnsCheck(status="danger", found=found, message="Dangerous: '+all' lets any server on the internet send as this domain. Replace it with '~all' or '-all'.")
    if "?all" in terms:
        return DnsCheck(status="warning", found=found, message="'?all' is neutral: it does not stop other servers sending as this domain. Use '~all' or '-all'.")
    if not any(term.lstrip("+-~?") == "all" for term in terms) and not any(term.startswith("redirect=") for term in terms):
        return DnsCheck(status="warning", found=found, message="The SPF record has no 'all' at the end. End it with '~all' or '-all'.")
    names_server = any(term in {"mx", "+mx", "a", "+a"} or term.startswith(("mx:", "+mx:", "include:", "+include:", "ip4:", "+ip4:", "ip6:", "+ip6:", "redirect=")) for term in terms)
    if mail_host and any(term in {f"a:{mail_host}", f"+a:{mail_host}"} for term in terms):
        names_server = True
    if not names_server:
        return DnsCheck(status="warning", found=found, message="The SPF record does not name the mail server. Add 'mx' before the 'all'.")
    return DnsCheck(status="ok", found=found, message="One SPF record that ends with a safe 'all'.")


def _tags(record: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in record.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip().lower()] = value.strip()
    return out


def check_dkim(txt: list[str], public_key: str | None, selector: str) -> DnsCheck:
    found = [item.strip() for item in txt if "p=" in item.replace(" ", "").lower()]
    if not found:
        return DnsCheck(status="missing", message=f"No DKIM record at {selector}._domainkey: receivers cannot verify signed mail.")
    key = "".join(_tags(found[0]).get("p", "").split())
    if not key:
        return DnsCheck(status="danger", found=found, message="The DKIM record has an empty key: it revokes signing for this selector.")
    if public_key and key != public_key:
        return DnsCheck(status="warning", found=found, message="The DKIM key in DNS differs from Mailcow's key. Set the value shown below.")
    if not public_key:
        return DnsCheck(status="ok", found=found, message="A DKIM key is published. Mailcow's key was not read, so it was not compared.")
    return DnsCheck(status="ok", found=found, message="The DKIM key in DNS matches Mailcow's key.")


def check_dmarc(txt: list[str]) -> DnsCheck:
    found = [item.strip() for item in txt if item.strip().lower().startswith("v=dmarc1")]
    if not found:
        return DnsCheck(status="missing", message="No DMARC record: receivers get no policy for mail that fails SPF and DKIM.")
    if len(found) > 1:
        return DnsCheck(status="danger", found=found, message="More than one DMARC record: receivers ignore DMARC. Keep exactly one.")
    policy = _tags(found[0]).get("p", "").lower()
    if policy in {"quarantine", "reject"}:
        return DnsCheck(status="ok", found=found, message=f"DMARC policy '{policy}'.")
    if policy == "none":
        return DnsCheck(status="warning", found=found, message="DMARC policy 'none' only reports. Move to 'quarantine' once the reports show only your servers.")
    return DnsCheck(status="warning", found=found, message="The DMARC record has no valid policy (p=none, quarantine or reject).")


def expected_records(domain: str, mail_host: str | None, dkim_selector: str, dkim_txt: str | None) -> list[ExpectedRecord]:
    """What the owner sets at the DNS host for mail of ``domain`` to work through this server."""
    host = mail_host or "<your mail server host>"
    return [
        ExpectedRecord(kind="mx", type="MX", name=domain, value=f"10 {host}.", note=None if mail_host else "Set ANUM_MAILCOW_MAIL_HOST to show the real host."),
        ExpectedRecord(kind="spf", type="TXT", name=domain, value="v=spf1 mx ~all",
                       note="One SPF record only. Keep any include: the domain really sends through (for example spf.brevo.com). Never '+all'."),
        ExpectedRecord(kind="dkim", type="TXT", name=f"{dkim_selector}._domainkey.{domain}",
                       value=dkim_txt or "Create the DKIM key in Mailcow first (Configuration, ARC/DKIM keys), then check again.",
                       note=None if dkim_txt else "No DKIM key in Mailcow yet."),
        ExpectedRecord(kind="dmarc", type="TXT", name=f"_dmarc.{domain}", value=f"v=DMARC1; p=none; rua=mailto:postmaster@{domain}",
                       note="Start with p=none, then move to p=quarantine once the reports show only your servers."),
    ]


Lookup = Callable[[str, Literal["MX", "TXT"], float], list[str]]


def check_domain(domain: str, mail_host: str | None, dkim_selector: str, dkim_txt: str | None, dkim_key: str | None, timeout: float) -> DnsReport:
    """Look the four records up at the same time and judge each one."""
    queries: dict[str, tuple[str, Literal["MX", "TXT"]]] = {
        "mx": (domain, "MX"), "spf": (domain, "TXT"), "dkim": (f"{dkim_selector}._domainkey.{domain}", "TXT"), "dmarc": (f"_dmarc.{domain}", "TXT"),
    }
    answers: dict[str, list[str] | LookupFailed] = {}

    def run(key: str) -> None:
        name, rdtype = queries[key]
        try:
            answers[key] = lookup(name, rdtype, timeout)
        except LookupFailed as exc:
            answers[key] = exc
        except Exception:  # noqa: BLE001 - any resolver fault is one failed lookup, never a failed page
            answers[key] = LookupFailed("error")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, queries))

    def judged(key: str, what: str, judge: Callable[[list[str]], DnsCheck]) -> DnsCheck:
        answer = answers.get(key)
        if isinstance(answer, LookupFailed) or answer is None:
            failure = answer.kind if isinstance(answer, LookupFailed) else "error"
            if failure in {"nxdomain", "empty"}:
                return judge([])
            return _failed(failure, what)
        return judge(answer)

    return DnsReport(
        domain=domain, checked_at=utc_now().isoformat(), mail_host=mail_host,
        mx=judged("mx", "MX", lambda found: check_mx(found, mail_host)),
        spf=judged("spf", "SPF", lambda found: check_spf(found, mail_host)),
        dkim=judged("dkim", "DKIM", lambda found: check_dkim(found, dkim_key, dkim_selector)),
        dmarc=judged("dmarc", "DMARC", check_dmarc),
        records=expected_records(domain, mail_host, dkim_selector, dkim_txt),
    )
