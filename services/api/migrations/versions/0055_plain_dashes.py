"""No long dashes in stored names (owner rule, 13 Oct 2026; docs/web-localization.md#punctuation).

Rewrites em dashes (U+2014), en dashes (U+2013) and horizontal bars (U+2015) in the
display-only text columns of the tables the setup plan writes to, where items made by an
earlier version of the plan still carry names such as "GROUP, em dash, Chief of Staff":

- names and titles: ``workspaces.name``, ``policy_packs.name``, ``approval_rules.name``,
  ``role_templates.name``, ``kb_collections.name``, ``agent_profiles.name``,
  ``assistant_profiles.name``, ``quick_commands.title``, ``automation_workflows.name``,
  ``automation_schedules.name``, ``automation_triggers.name``. "PREFIX dash Rest" becomes
  "Prefix: Rest", exactly the names the current blueprint gives (its ``former_names``);
  the five plan workspaces get the blueprint's workspace names ("Tyre Pulse").
- descriptions: ``policy_packs.description``, ``kb_collections.description``,
  ``agent_profiles.description``, ``automation_workflows.description``: a comma between
  clauses, a hyphen between numbers.

Nothing else is touched: no ids, instructions, prompts, steps, secrets, payload hashes,
ledger rows or ``updated_at``, and never ``audit_records`` (immutable). A rename that
would collide with an existing name (a unique index) is skipped and keeps its old name.

RLS stays forced. Rows are found through a temporary SELECT policy for the migrating
role only, limited to rows whose columns contain a long dash, created and dropped inside
this migration's transaction (no other session ever sees it). Each row is then updated
inside its own tenant, workspace and user context (``set_config``), as the API would.
A superuser (CI) bypasses RLS anyway; the same code runs.

Data only, idempotent (a second run finds no rows) and expand-safe: the previous release
reads any name. ``downgrade`` does not put the dashes back.

Chained after ``0054_routing_suppression``.
"""

import logging
import re

import sqlalchemy as sa
from alembic import op

revision = "0055_plain_dashes"
down_revision = "0054_routing_suppression"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# Built from code points so the source stays free of the characters it removes.
DASHES = chr(0x2013) + chr(0x2014) + chr(0x2015)
SQL_PATTERN = f"[{DASHES}]"
_RUN = re.compile(f"[ \\t{chr(0xA0)}]*[{DASHES}]+[ \\t{chr(0xA0)}]*")
_NAME = re.compile(f"^(?P<prefix>[^{DASHES}]*?\\S)[ \\t{chr(0xA0)}]*[{DASHES}]+[ \\t{chr(0xA0)}]*(?P<rest>\\S.*)$", re.DOTALL)
# Upper-case prefixes the plan used, written the way the current blueprint writes them.
PREFIXES = {
    "GROUP": "Group", "FLEET": "Fleet", "TYRE": "Tyre", "GALLERY": "Gallery", "OPS": "Ops", "VOICE": "Voice",
    "BASE": "Base", "ACCESS": "Access", "COMMS": "Comms", "FINANCE": "Finance", "CONTENT": "Content",
    "MEMORY": "Memory", "RUNTIME": "Runtime", "ADMIN": "Admin",
}
WORKSPACES = {
    f"FLEET {chr(0x2014)} Consulting": "Fleet Consulting",
    f"TYRE {chr(0x2014)} Tyre Pulse": "Tyre Pulse",
    f"GALLERY {chr(0x2014)} ANUM 360": "ANUM 360 Gallery",
    f"OPS {chr(0x2014)} OpsBrain": "OpsBrain",
    f"VOICE {chr(0x2014)} VoiceQuote": "VoiceQuote",
}
ARABIC_COMMA = chr(0x060C)
_SEPARATED = set(",;:.!?([{\"'/" + ARABIC_COMMA + chr(0x061B) + chr(0x061F))
_CLOSING = set(",;:.!?)]}\"'" + ARABIC_COMMA + chr(0x061B) + chr(0x061F))


def _arabic(char: str) -> bool:
    return bool(char) and (0x0600 <= ord(char) <= 0x06FF or 0x0750 <= ord(char) <= 0x08FF or 0xFB50 <= ord(char) <= 0xFEFF)


def _clause(match: re.Match[str]) -> str:
    text = match.string
    before = text[match.start() - 1] if match.start() else ""
    after = text[match.end()] if match.end() < len(text) else ""
    if before.isdigit() and after.isdigit():
        return "-"
    if before in ("", "\n"):
        return "" if after in ("", "\n") else "- "
    if after in ("", "\n"):
        return ":" if before.isalnum() else ""
    if before in _SEPARATED:
        return "" if before in "([{\"'/" else " "
    if after in _CLOSING:
        return ""
    return f"{ARABIC_COMMA} " if _arabic(before) else ", "


def plain_text(value: str) -> str:
    """A description: commas between clauses, hyphens between numbers."""
    return _RUN.sub(_clause, value)


def plain_name(value: str, *, table: str = "") -> str:
    """A name or title: "PREFIX dash Rest" becomes "Prefix: Rest"."""
    if table == "workspaces" and value.strip() in WORKSPACES:
        return WORKSPACES[value.strip()]
    match = _NAME.match(value)
    if match and not (match["prefix"][-1].isdigit() and match["rest"][0].isdigit()):
        prefix = match["prefix"]
        return f"{PREFIXES.get(prefix.strip(), prefix)}: {plain_text(match['rest'])}"
    return plain_text(value)


# table: (key columns, context columns (tenant, workspace, user), name columns, text columns)
TABLES: dict[str, tuple[tuple[str, ...], tuple[str | None, str | None, str | None], tuple[str, ...], tuple[str, ...]]] = {
    "workspaces": (("tenant_id", "id"), ("tenant_id", "id", None), ("name",), ()),
    "policy_packs": (("tenant_id", "id"), ("tenant_id", None, None), ("name",), ("description",)),
    "approval_rules": (("tenant_id", "id"), ("tenant_id", None, None), ("name",), ()),
    "role_templates": (("tenant_id", "id"), ("tenant_id", None, None), ("name",), ()),
    "kb_collections": (("tenant_id", "id"), ("tenant_id", "workspace_id", "owner_user_id"), ("name",), ("description",)),
    "agent_profiles": (("tenant_id", "id"), ("tenant_id", "workspace_id", "owner_user_id"), ("name",), ("description",)),
    "assistant_profiles": (("tenant_id", "id"), ("tenant_id", "workspace_id", None), ("name",), ()),
    "quick_commands": (("tenant_id", "id"), ("tenant_id", "workspace_id", "user_id"), ("title",), ()),
    "automation_workflows": (("tenant_id", "id"), ("tenant_id", "workspace_id", None), ("name",), ("description",)),
    "automation_schedules": (("tenant_id", "id"), ("tenant_id", "workspace_id", None), ("name",), ()),
    "automation_triggers": (("tenant_id", "id"), ("tenant_id", "workspace_id", None), ("name",), ()),
}
POLICY = "anum_plain_dashes_discovery"


def _context(bind: sa.Connection, tenant: str, workspace: str, user: str) -> None:
    bind.execute(
        sa.text(
            "select set_config('anum.tenant_id', :tenant, true), set_config('anum.workspace_id', :workspace, true), "
            "set_config('anum.user_id', :user, true)"
        ),
        {"tenant": tenant, "workspace": workspace, "user": user},
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    found: list[tuple[str, dict[str, object]]] = []
    for table, (keys, scope, names, texts) in TABLES.items():
        columns = names + texts
        matches = " or ".join(f"{column} ~ '{SQL_PATTERN}'" for column in columns)
        # Discovery only: SELECT, this role, rows holding a long dash; dropped below.
        op.execute(f"create policy {POLICY} on {table} as permissive for select to current_user using ({matches})")
        selected = ", ".join(dict.fromkeys([*keys, *(c for c in scope if c), *columns]))
        rows = bind.execute(sa.text(f"select {selected} from {table} where {matches}")).mappings().all()  # nosec B608: fixed table and column names
        op.execute(f"drop policy {POLICY} on {table}")
        found.extend((table, dict(row)) for row in rows)

    changed = skipped = 0
    for table, row in found:
        keys, (tenant_col, workspace_col, user_col), names, texts = TABLES[table]
        values = {c: plain_name(str(row[c]), table=table) for c in names if row[c] is not None}
        values.update({c: plain_text(str(row[c])) for c in texts if row[c] is not None})
        values = {c: v for c, v in values.items() if v != row[c]}
        if not values:
            continue
        _context(
            bind,
            str(row[tenant_col]),
            str(row[workspace_col] or "") if workspace_col else "",
            str(row[user_col] or "") if user_col else "",
        )
        assignments = ", ".join(f"{column} = :new_{column}" for column in values)
        where = " and ".join(f"{key} = :key_{key}" for key in keys)
        params = {**{f"new_{c}": v for c, v in values.items()}, **{f"key_{k}": row[k] for k in keys}}
        try:
            with bind.begin_nested():
                bind.execute(sa.text(f"update {table} set {assignments} where {where}"), params)  # nosec B608: fixed table and column names
            changed += 1
        except sa.exc.IntegrityError:
            skipped += 1  # the new name is taken: keep the old one
    _context(bind, "", "", "")
    logger.info("plain dashes: %d rows renamed, %d kept (name already taken)", changed, skipped)


def downgrade() -> None:
    """Nothing to undo: the old names are not restored."""
