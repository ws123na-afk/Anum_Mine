---
id: audit
title: Audit log
summary: See who changed members, invitations, policies, budgets and settings, filter the records and export them.
keywords: audit log, audit, record, actor, action, target, outcome, correlation id, export, csv, json, retention
docs: ../governance-and-scale.md
---

# Audit log

The audit log records every change to members, invitations, policies, budgets and settings in this workspace: who did it, when, and to what. Records are listed newest first and cannot be edited.

## Find a record

1. Open **Audit log** in the sidebar under **Admin**.
2. Type in **Search**, or fill **Actor**, **Action**, **Target** and **Outcome**, and pick a date range with **From** and **To**.
3. Click **Apply filters**. Click **Reset** to see everything again.
4. Click **Load older records** at the end of the list to page back. The retention period is shown under the list.

## Open a record and follow it

1. Click the time of a record to open **Audit record**.
2. Read the actor, action, target, outcome, correlation id, workspace and the record's details.
3. Click **Only this actor**, **Only this target** or **Same correlation id** to filter the log to related records.

## Export the records

1. Set the filters you want, or leave them empty for everything.
2. Click **Export CSV** or **Export JSON** at the top.
3. ANUM downloads the filtered range and says which file it saved.

## Who can do this

- Workspace owners and organization administrators with a membership in this workspace: read, filter and export the audit log.
- Members and viewers: see a notice explaining that only owners and organization administrators can read it. Ask a workspace owner to show you a record.

## Learn more

[Governance and Scale](../governance-and-scale.md)
