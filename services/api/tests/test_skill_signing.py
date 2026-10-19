"""Ed25519 signing of skill versions (docs/skills.md#signing).

Canonical digest, tampering, unknown, rotated and revoked keys, install and upgrade refusals,
rollback to a signed version only, platform and first-party trust, permissions and audit.
Every key here is derived at runtime from a plain label (skill_signing_support), never committed.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from skill_signing_support import KEYS, digest_of, private_key, public_key, sign, trust

from anum_api import main, skill_signing
from anum_api.dependencies import memory_repository
from anum_api.governance import governance_store
from anum_api.schemas import TenantContext
from anum_api.settings import settings
from anum_api.skill_signing import (
    PACKAGE_FORMAT,
    VerificationStatus,
    canonical_package,
    key_fingerprint,
    package_digest,
    signature_matches,
)
from anum_api.skills_api import skill_store

OWNER = {"x-tenant-id": "tenant_sig", "x-workspace-id": "workspace_sig", "x-user-id": "owner_sig", "x-user-roles": "owner"}
ADMIN = {**OWNER, "x-user-roles": "owner,org_admin"}
MEMBER = {**OWNER, "x-user-id": "member_sig", "x-user-roles": "member"}
OTHER_TENANT = {**ADMIN, "x-tenant-id": "tenant_sig_other", "x-workspace-id": "workspace_sig_other"}
CONTEXT = TenantContext(tenant_id="tenant_sig", workspace_id="workspace_sig", user_id="owner_sig", roles=["owner"])
VERSIONS = "/api/v1/skills/versions"
INSTALLS = "/api/v1/skills/installations"


def skill(version: str = "1.0.0", instructions: str = "Summarize changes.", tools: list[str] | None = None) -> dict:
    return {
        "skill_id": "acme.notes", "version": version, "name": "Notes", "description": "Writes notes",
        "instructions": instructions, "required_tools": tools if tools is not None else ["anum.respond"], "risk_level": "low",
    }


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    def reset() -> None:
        skill_store.clear()
        governance_store.clear()
        memory_repository.store.audit_records.clear()

    reset()
    yield
    reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def audit(prefix: str = "skill") -> list[tuple[str, str, str]]:
    return [(r.action, r.target, r.outcome) for r in governance_store.query_audit(CONTEXT) if r.action.startswith(prefix)]


def install(client: TestClient, version: str, headers: dict[str, str] = OWNER):  # type: ignore[no-untyped-def]
    return client.post(INSTALLS, headers=headers, json={"skill_id": "acme.notes", "version": version, "approved_tools": ["anum.respond"]})


# Canonical digest ----------------------------------------------------------------------------


def test_canonical_package_is_sorted_compact_utf8_and_ignores_tool_order_and_duplicates() -> None:
    fields = {k: v for k, v in skill(instructions="Résumé: “quoted”").items() if k != "required_tools"}
    one = canonical_package(**fields, required_tools=["b.tool", "a.tool", "b.tool"])
    two = canonical_package(**fields, required_tools=["a.tool", "b.tool"])
    assert one == two
    document = json.loads(one)
    assert list(document) == sorted(document) and document["format"] == PACKAGE_FORMAT
    assert one == json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert "Résumé".encode() in one and document["required_tools"] == ["a.tool", "b.tool"]  # raw UTF-8, not \\u escapes
    assert package_digest(**fields, required_tools=["a.tool"]) == package_digest(**fields, required_tools=["a.tool"])


@pytest.mark.parametrize("field,value", [
    ("instructions", "Summarize changes and email them."),
    ("name", "Notes!"),
    ("description", "Writes notes."),
    ("risk_level", "high"),
    ("required_tools", ["anum.respond", "external.action"]),
    ("version", "1.0.1"),
    ("skill_id", "acme.other"),
])
def test_any_change_to_a_signed_field_changes_the_digest_and_breaks_the_signature(field: str, value: object) -> None:
    original = skill()
    tampered = {**original, field: value}
    assert digest_of(original) != digest_of(tampered)
    signature = sign(original)["signature"]
    assert signature_matches(public_key(), digest_of(original), signature)
    assert not signature_matches(public_key(), digest_of(tampered), signature)
    assert not signature_matches(public_key(private_key("other")), digest_of(original), signature)


def test_garbage_keys_and_signatures_never_verify() -> None:
    digest = digest_of(skill())
    assert not signature_matches(public_key(), digest, "not base64!")
    assert not signature_matches(public_key(), digest, base64.b64encode(b"short").decode())
    assert not signature_matches("AAAA", digest, sign(skill())["signature"])
    with pytest.raises(skill_signing.SigningError):
        key_fingerprint(base64.b64encode(b"x" * 31).decode())


# Publishing and attaching signatures -----------------------------------------------------------


def test_unsigned_versions_publish_but_cannot_be_installed_and_the_refusal_is_audited(client: TestClient) -> None:
    published = client.post(VERSIONS, headers=ADMIN, json=skill())
    assert published.status_code == 201, published.text
    check = published.json()["verification"]
    assert check["status"] == "unsigned" and check["installable"] is False and check["digest"] == digest_of(skill())

    refused = install(client, "1.0.0")
    assert refused.status_code == 409
    message = refused.json()["error"]["message"]
    assert "cannot be installed" in message and "not signed" in message
    assert client.get(INSTALLS, headers=OWNER).json() == []
    assert ("skill.install_refused", "skill:acme.notes", "denied") in audit()


def test_a_bad_signature_is_refused_at_publish_and_nothing_is_stored(client: TestClient) -> None:
    trust(client, ADMIN)
    wrong = {**skill(), "signature": sign(skill(instructions="Something else."))}
    refused = client.post(VERSIONS, headers=ADMIN, json=wrong)
    assert refused.status_code == 422
    assert refused.json()["error"]["message"].startswith("Signature refused: The signature does not match")
    unknown = client.post(VERSIONS, headers=ADMIN, json={**skill(), "signature": sign(skill(), key_id="nobody-key")})
    assert unknown.status_code == 422 and "not a trusted publisher key" in unknown.json()["error"]["message"]
    assert client.get(VERSIONS, headers=MEMBER).json() == []
    assert [a for a in audit() if a[0] == "skill.signature_refused"] == [("skill.signature_refused", "skill:acme.notes", "denied")] * 2


def test_signed_version_installs_and_carries_signer_digest_and_audit(client: TestClient) -> None:
    trust(client, ADMIN)
    published = client.post(VERSIONS, headers=ADMIN, json={**skill(), "signature": sign(skill())})
    assert published.status_code == 201, published.text
    check = published.json()["verification"]
    assert check["status"] == "verified" and check["signer"] == "Test publisher" and check["key_source"] == "tenant"
    assert check["fingerprint"] == key_fingerprint(public_key())

    installed = install(client, "1.0.0")
    assert installed.status_code == 201, installed.text
    assert installed.json()["verification"]["status"] == "verified"
    [listed] = client.get(INSTALLS, headers=MEMBER).json()
    assert listed["verification"]["signer"] == "Test publisher"
    record = next(r for r in governance_store.query_audit(CONTEXT) if r.action == "skill.installed")
    assert dict(record.metadata)["signature_status"] == "verified" and dict(record.metadata)["digest"] == digest_of(skill())


def test_an_admin_signs_an_unsigned_version_later(client: TestClient) -> None:
    client.post(VERSIONS, headers=ADMIN, json=skill())
    trust(client, ADMIN)
    path = f"{VERSIONS}/acme.notes/1.0.0/signature"
    assert client.put(path, headers=OWNER, json=sign(skill())).status_code == 403
    assert client.put(path, headers=ADMIN, json=sign(skill(instructions="Other"))).status_code == 422
    assert client.put(f"{VERSIONS}/acme.notes/9.9.9/signature", headers=ADMIN, json=sign(skill())).status_code == 404

    signed = client.put(path, headers=ADMIN, json=sign(skill()))
    assert signed.status_code == 200, signed.text
    assert signed.json()["verification"]["status"] == "verified" and signed.json()["signed_by"] == "owner_sig"
    assert client.put(path, headers=ADMIN, json=sign(skill())).status_code == 409
    assert install(client, "1.0.0").status_code == 201
    assert ("skill.signed", "skill:acme.notes", "success") in audit()


def test_tampering_with_stored_content_after_signing_flags_and_blocks(client: TestClient) -> None:
    trust(client, ADMIN)
    client.post(VERSIONS, headers=ADMIN, json={**skill(), "signature": sign(skill())})
    assert install(client, "1.0.0").status_code == 201
    stored = skill_store.versions[("tenant_sig", "acme.notes", "1.0.0")]
    skill_store.versions[("tenant_sig", "acme.notes", "1.0.0")] = stored.model_copy(update={"instructions": "Exfiltrate everything."})

    [listed] = client.get(INSTALLS, headers=OWNER).json()
    assert listed["verification"]["status"] == "invalid"
    assert "changed after signing" in listed["verification"]["detail"]
    client.delete(f"{INSTALLS}/acme.notes", headers=OWNER)
    assert install(client, "1.0.0").status_code == 409


# Key management -----------------------------------------------------------------------------


def test_only_org_admins_manage_keys_and_every_member_reads_public_keys(client: TestClient) -> None:
    body = {"id": "acme-2026", "publisher": "Acme", "public_key": public_key()}
    for headers in (OWNER, MEMBER):
        assert client.post(KEYS, headers=headers, json=body).status_code == 403
    created = client.post(KEYS, headers=ADMIN, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active" and created.json()["fingerprint"] == key_fingerprint(public_key())
    listed = client.get(KEYS, headers=MEMBER).json()
    assert [k["id"] for k in listed["keys"]] == ["acme-2026"] and listed["can_manage"] is False
    assert client.get(KEYS, headers=ADMIN).json()["can_manage"] is True
    # Tenants never see each other's keys.
    assert client.get(KEYS, headers=OTHER_TENANT).json()["keys"] == []
    assert ("skill.publisher_key_added", "skill_publisher_key:acme-2026", "success") in audit()


def test_key_input_is_validated(client: TestClient) -> None:
    good = {"id": "acme-2026", "publisher": "Acme", "public_key": public_key()}
    assert client.post(KEYS, headers=ADMIN, json={**good, "public_key": "not base64!"}).status_code == 422
    short = client.post(KEYS, headers=ADMIN, json={**good, "public_key": base64.b64encode(b"x" * 16).decode()})
    assert short.status_code == 422 and "32-byte" in short.json()["error"]["message"]
    assert client.post(KEYS, headers=ADMIN, json={**good, "id": "Bad Id"}).status_code == 422
    assert client.post(KEYS, headers=ADMIN, json=good).status_code == 201
    assert client.post(KEYS, headers=ADMIN, json=good).status_code == 409
    same_key = client.post(KEYS, headers=ADMIN, json={**good, "id": "acme-copy"})
    assert same_key.status_code == 409 and "already trusted as acme-2026" in same_key.json()["error"]["message"]


def test_rotation_keeps_old_signatures_valid_and_refuses_new_ones_with_the_old_key(client: TestClient) -> None:
    trust(client, ADMIN)
    client.post(VERSIONS, headers=ADMIN, json={**skill("1.0.0"), "signature": sign(skill("1.0.0"))})
    newer = private_key("rotated")
    rotated = client.post(f"{KEYS}/test-publisher/rotate", headers=ADMIN, json={"new_id": "test-publisher-2", "public_key": public_key(newer)})
    assert rotated.status_code == 201, rotated.text
    keys = {k["id"]: k for k in client.get(KEYS, headers=ADMIN).json()["keys"]}
    assert keys["test-publisher"]["status"] == "rotated" and keys["test-publisher"]["replaced_by"] == "test-publisher-2"
    assert keys["test-publisher-2"]["publisher"] == "Test publisher"

    # Signed before rotation: still verifies and installs.
    assert install(client, "1.0.0").status_code == 201
    # A new signature with the rotated key is refused; the new key works.
    old_key = client.post(VERSIONS, headers=ADMIN, json={**skill("1.1.0"), "signature": sign(skill("1.1.0"))})
    assert old_key.status_code == 422 and "after it was rotated" in old_key.json()["error"]["message"]
    fresh = client.post(VERSIONS, headers=ADMIN, json={**skill("1.1.0"), "signature": sign(skill("1.1.0"), newer, "test-publisher-2")})
    assert fresh.status_code == 201, fresh.text
    assert install(client, "1.1.0").status_code == 201
    assert client.post(f"{KEYS}/test-publisher/rotate", headers=ADMIN, json={"new_id": "x-3", "public_key": public_key(private_key("x"))}).status_code == 409
    assert client.post(f"{KEYS}/missing/rotate", headers=ADMIN, json={"new_id": "x-3", "public_key": public_key(private_key("x"))}).status_code == 404
    assert ("skill.publisher_key_rotated", "skill_publisher_key:test-publisher", "success") in audit()


def test_revocation_blocks_installs_and_upgrades_and_flags_installed_versions(client: TestClient) -> None:
    trust(client, ADMIN)
    for version in ("1.0.0", "1.1.0"):
        client.post(VERSIONS, headers=ADMIN, json={**skill(version), "signature": sign(skill(version))})
    assert install(client, "1.0.0").status_code == 201
    assert client.post(f"{KEYS}/test-publisher/revoke", headers=OWNER, json={"reason": "Leaked"}).status_code == 403

    revoked = client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "Private key leaked"})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["affected_versions"] == 2 and revoked.json()["flagged_installations"] == 1
    assert revoked.json()["key"]["status"] == "revoked" and revoked.json()["key"]["revoked_by"] == "owner_sig"

    [flagged] = client.get(INSTALLS, headers=MEMBER).json()
    assert flagged["verification"]["status"] == VerificationStatus.REVOKED.value
    assert "Private key leaked" in flagged["verification"]["detail"]
    upgrade = install(client, "1.1.0")
    assert upgrade.status_code == 409 and "revoked" in upgrade.json()["error"]["message"]
    assert client.get(INSTALLS, headers=OWNER).json()[0]["version"] == "1.0.0"
    # Revocation is final, and a revoked public key cannot come back under a new id.
    assert client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "again"}).status_code == 409
    back = client.post(KEYS, headers=ADMIN, json={"id": "comeback", "publisher": "Acme", "public_key": public_key()})
    assert back.status_code == 409 and "revoked" in back.json()["error"]["message"]

    actions = audit()
    assert ("skill.publisher_key_revoked", "skill_publisher_key:test-publisher", "success") in actions
    assert ("skill.installation_flagged", "skill:acme.notes", "flagged") in actions
    assert ("skill.install_refused", "skill:acme.notes", "denied") in actions
    detail = client.get("/api/v1/skills/acme.notes", headers=MEMBER).json()
    assert detail["installation"]["verification"]["status"] == "revoked"
    assert {v["verification"]["status"] for v in detail["versions"]} == {"revoked"}


# Rollback ------------------------------------------------------------------------------------


def test_rollback_only_to_a_previous_signed_version(client: TestClient) -> None:
    trust(client, ADMIN)
    client.post(VERSIONS, headers=ADMIN, json=skill("1.0.0"))  # unsigned
    client.post(VERSIONS, headers=ADMIN, json={**skill("1.1.0"), "signature": sign(skill("1.1.0"))})
    client.post(VERSIONS, headers=ADMIN, json={**skill("2.0.0"), "signature": sign(skill("2.0.0"))})
    assert install(client, "2.0.0").status_code == 201

    unsigned = client.post(f"{INSTALLS}/acme.notes/rollback", headers=ADMIN, json={"version": "1.0.0"})
    assert unsigned.status_code == 409
    assert "only a signed version can be rolled back to" in unsigned.json()["error"]["message"]
    assert client.get(INSTALLS, headers=OWNER).json()[0]["version"] == "2.0.0"

    rolled = client.post(f"{INSTALLS}/acme.notes/rollback", headers=ADMIN, json={"version": "1.1.0"})
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["version"] == "1.1.0" and rolled.json()["verification"]["status"] == "verified"
    actions = audit()
    assert ("skill.rollback_refused", "skill:acme.notes", "denied") in actions
    assert ("skill.rolled_back", "skill:acme.notes", "success") in actions


# Platform and first-party trust ---------------------------------------------------------------


def test_platform_keys_verify_first_party_skills_and_cannot_be_shadowed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    platform = private_key("platform")
    monkeypatch.setattr(settings, "skill_platform_keys", f"anum-platform={public_key(platform)}, broken=nope")
    published = client.post(VERSIONS, headers=ADMIN, json={**skill(), "signature": sign(skill(), platform, "anum-platform")})
    assert published.status_code == 201, published.text
    assert published.json()["verification"]["key_source"] == "platform"
    assert published.json()["verification"]["signer"] == "ANUM platform"
    listed = client.get(KEYS, headers=MEMBER).json()
    assert [k["id"] for k in listed["platform_keys"]] == ["anum-platform"]  # the malformed entry is ignored
    assert client.post(KEYS, headers=ADMIN, json={"id": "anum-platform", "publisher": "Fake", "public_key": public_key()}).status_code == 409
    assert install(client, "1.0.0").status_code == 201


def test_shipped_blueprint_skills_are_first_party_without_a_signature(client: TestClient) -> None:
    from anum_api.setup_plans import load_blueprints

    blueprint_skill = next(s for b in load_blueprints().values() for s in b.skills)
    body = {
        "skill_id": blueprint_skill.skill_id, "version": blueprint_skill.version, "name": blueprint_skill.name,
        "description": blueprint_skill.description, "instructions": blueprint_skill.instructions,
        "required_tools": blueprint_skill.tools[:1], "risk_level": blueprint_skill.risk_level,
    }
    published = client.post(VERSIONS, headers=ADMIN, json=body)
    assert published.json()["verification"]["key_source"] == "first_party"
    assert published.json()["verification"]["installable"] is True
    # Changing one word makes it an ordinary unsigned version.
    changed = client.post(VERSIONS, headers=ADMIN, json={**body, "version": "99.0.0", "instructions": body["instructions"] + " Also."})
    assert changed.json()["verification"]["status"] == "unsigned"


def test_migration_is_expand_only_with_forced_tenant_rls() -> None:
    versions = Path(__file__).parents[1] / "migrations" / "versions"
    name = "0082_skill_signing"
    text = (versions / f"{name}.py").read_text(encoding="utf-8")
    assert f'revision = "{name}"' in text and len(name) <= 32
    down = text.split('down_revision = "', 1)[1].split('"', 1)[0]
    others = [path for path in versions.glob("*.py") if path.name != f"{name}.py"]
    # Chained onto an existing revision that nothing else extends (one head).
    assert any(f'revision = "{down}"' in path.read_text(encoding="utf-8") for path in others)
    assert not [path for path in others if f'down_revision = "{down}"' in path.read_text(encoding="utf-8")]
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0].lower()
    assert upgrade.count("op.create_table(") == 2 and "force row level security" in text
    assert 'TABLES = ("skill_publisher_keys", "skill_signing_settings")' in text and "for table in tables:\n        _enable_rls(table)" in upgrade
    assert "current_setting('anum.tenant_id', true)" in text and "with check (" in text
    assert "nullable=true" in upgrade.replace(" ", "") and "drop" not in upgrade and "alter_column" not in upgrade
    # anum_maintenance discovers scope, id, version and the enabled flag only, and cannot write.
    assert "grant select (tenant_id, workspace_id, skill_id, version, enabled) on skill_installations to anum_maintenance" in upgrade
    assert "grant select (tenant_id, workspace_id, package_id, enabled) on marketplace_installs to anum_maintenance" in upgrade
    assert upgrade.count("grant ") == 2 and "approved_tools" not in upgrade.split("grant ", 1)[1]
    assert "bypassrls" not in upgrade and "security definer" not in upgrade


def test_whitespace_publisher_is_a_validation_error_not_a_crash(client: TestClient) -> None:
    response = client.post(KEYS, headers=ADMIN, json={"id": "acme-2026", "publisher": "   ", "public_key": public_key()})
    assert response.status_code == 422 and "not only spaces" in response.json()["error"]["message"]


def test_a_rotated_key_can_still_be_revoked_and_a_revoked_key_cannot_be_rotated(client: TestClient) -> None:
    trust(client, ADMIN)
    client.post(VERSIONS, headers=ADMIN, json={**skill(), "signature": sign(skill())})
    assert install(client, "1.0.0").status_code == 201
    client.post(f"{KEYS}/test-publisher/rotate", headers=ADMIN, json={"new_id": "test-publisher-2", "public_key": public_key(private_key("r"))})
    revoked = client.post(f"{KEYS}/test-publisher/revoke", headers=ADMIN, json={"reason": "Old laptop leaked"})
    assert revoked.status_code == 200 and revoked.json()["key"]["status"] == "revoked"
    assert client.post(f"{KEYS}/test-publisher/rotate", headers=ADMIN, json={"new_id": "test-publisher-3", "public_key": public_key(private_key("s"))}).status_code == 409
    [installed] = client.get(INSTALLS, headers=OWNER).json()
    assert installed["verification"]["status"] == "revoked" and installed["enabled"] is False
