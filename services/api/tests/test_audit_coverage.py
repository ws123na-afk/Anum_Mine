"""Audit coverage of the email, WhatsApp, autonomy, developer, learning, automation, agents, business (and its support tickets) and Brevo APIs.

Each module's own test file walks every state-changing route of its router (from the
OpenAPI schema) through ``audit_probe.AuditProbe`` and asserts an audit record without
secrets or content for each. This file checks the frame around those walks: every module
has one, and every exemption names a real state-changing route with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

from audit_probe import EXEMPT, MODULE_PREFIXES, mutating_routes

TESTS = Path(__file__).parent
WALKS = {
    "agents": "test_agents.py",
    "automation": "test_automation_management.py",
    "calls": "test_calls.py",
    "brevo": "test_brevo.py",
    "billing": "test_billing.py",
    "platform_billing": "test_billing.py",
    "business": "test_business.py",
    "business_pipeline": "test_business_pipeline.py",
    "business_outreach": "test_business_pipeline.py",
    "business_support": "test_business_support.py",
    "business_invoices": "test_business_invoices.py",
    "business_portal": "test_business_portal.py",
    "portal": "test_business_portal.py",
    "autonomy": "test_autonomy.py",
    "developer": "test_developer.py",
    "email": "test_email.py",
    "learning": "test_learning.py",
    "mail": "test_mail_center.py",
    "mail_ai": "test_mail_ai.py",
    "whatsapp": "test_whatsapp.py",
}


def test_every_module_walks_its_state_changing_routes() -> None:
    assert set(WALKS) == set(MODULE_PREFIXES)
    for module, file_name in WALKS.items():
        source = (TESTS / file_name).read_text(encoding="utf-8")
        assert re.search(rf'AuditProbe\(.*"{module}"', source), f"{file_name} has no audit walk for {module}"
        assert "probe.assert_covered()" in source
        assert mutating_routes(MODULE_PREFIXES[module]), f"no state-changing routes found under {MODULE_PREFIXES[module]}"


def test_exemptions_are_real_state_changing_routes_with_a_reason() -> None:
    every = set().union(*(mutating_routes(prefix) for prefix in MODULE_PREFIXES.values()))
    for route, reason in EXEMPT.items():
        assert route in every, f"stale exemption: {route}"
        assert len(reason) > 20

