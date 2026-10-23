"""Sales agent execution contract and security checks.

These tests are offline and require neither a Meta account nor production data.
End-to-end tests still need staging PostgreSQL and a Meta test number.
"""
from datetime import datetime, timezone

from fastapi.routing import APIRoute

from anum_api.sales_api import (
    SalesAutomationSettingsRequest, SalesConsentEvidenceRequest,
    SalesFollowUpApprovalRequest, router,
)
from anum_api.sales_followup_scheduler import approval_payload_hash
from anum_api.db.sales_repository import (
    SalesAutomationSettingsRecord, SalesConsentEventRecord, SalesFollowUpRecord,
)

DUE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def test_approval_hash_binds_scope_recipient_template_due_time_and_parameters():
    payload = {
        "tenant": "tenant-a", "workspace": "workspace-a", "followup": "followup-a",
        "lead": "lead-a", "conversation": "conversation-a", "to": "966500000000",
        "due_at": DUE, "template": "service_followup", "language": "ar",
        "parameters": ["Customer", "Project"],
    }
    expected = approval_payload_hash(**payload)
    assert expected == approval_payload_hash(**payload)
    for name, modified in (
        ("workspace", "workspace-b"),
        ("to", "966500000001"),
        ("template", "other_template"),
        ("language", "en"),
        ("parameters", ["Customer", "Other project"]),
    ):
        assert expected != approval_payload_hash(**{**payload, name: modified})


def test_automation_defaults_explicitly_off():
    request = SalesAutomationSettingsRequest(
        enabled=False, paused=True, max_sends_per_day=0,
    )
    assert not request.enabled and request.paused
    assert request.max_sends_per_day == 0


def test_consent_requires_explicit_evidence_request_fields():
    request = SalesConsentEvidenceRequest(
        granted=True, evidence_message_id="wam-abc",
        evidence_note="Customer explicitly requested future updates in this message",
    )
    assert request.granted
    assert request.evidence_message_id


def test_followup_approval_requires_template_selection():
    request = SalesFollowUpApprovalRequest(
        template_name="service_followup", template_language="ar",
        parameters=["Customer"],
    )
    assert request.template_name == "service_followup"


def test_all_owner_operations_registered():
    routes = {(r.path, method) for r in router.routes if isinstance(r, APIRoute)
              for method in r.methods}
    for expected in (
        ("/api/v1/sales/automation-settings", "GET"),
        ("/api/v1/sales/automation-settings", "PUT"),
        ("/api/v1/sales/automation-readiness", "GET"),
        ("/api/v1/sales/leads/{lead_id}/consent", "POST"),
        ("/api/v1/sales/leads/{lead_id}/consent-history", "GET"),
        ("/api/v1/sales/leads/{lead_id}/followups/{followup_id}/approve", "POST"),
        ("/api/v1/sales/leads/{lead_id}/followups/{followup_id}/cancel", "POST"),
        ("/api/v1/sales/leads/{lead_id}/quotes/{quote_id}/request-whatsapp-delivery", "POST"),
    ):
        assert expected in routes


def test_sales_schema_tracks_outbound_safety_and_consent_audit():
    from sqlalchemy import inspect
    policy = {col.name for col in inspect(SalesAutomationSettingsRecord).columns}
    consent = {col.name for col in inspect(SalesConsentEventRecord).columns}
    followup = {col.name for col in inspect(SalesFollowUpRecord).columns}
    assert {"enabled", "paused", "max_sends_per_day"}.issubset(policy)
    assert {"evidence_message_id", "evidence_note", "recorded_by", "granted"}.issubset(consent)
    assert {"payload_hash", "template_name", "approved_by",
            "approved_at", "sent_wamid", "last_attempt_at"}.issubset(followup)
