---
id: governance
title: Governance
summary: Set organization approval rules, versioned policy packs and role templates, and export the audit trail.
keywords: governance, approval rule, policy pack, role template, baseline, audit export, organization, org admin
docs: ../governance-and-scale.md
---

# Governance

The governance center holds the controls that apply to every workspace in your organization: approval rules that choose who approves by amount, versioned policy packs, role templates and an audit export.

## Add an approval rule

1. Open **Governance** in the sidebar under **Admin**.
2. Under **Approval rules**, click **Add approval rule**.
3. Fill **Rule name** and **Actions it applies to** (a tool name pattern, for example one that matches every refund tool). Optionally set the **Amount band (AED)**, a share of the order value and **Disputed refunds**.
4. Under **Who approves**, tick **Workspace owners**, **Organization administrators** or **Workspace members**, or list user ids under **Named people (user ids, comma separated)**. Tick **Needs a second approval, by a different person, after the first** for a two step rule.
5. Keep **The rule is on** ticked and click **Save rule**. Later use **Edit**, **Turn off** or **Delete** on the rule.

## Manage policy packs and export the audit

1. Under **Policy packs**, each pack shows its version, its rules and whether it is **Active** or **Archived**.
2. Click **Add baseline** to create the organization baseline pack, where high-risk integration writes need approval.
3. Click **Archive** to retire an active version, or **Activate** to bring a stored version back.
4. Click **Export audit** to download the audit records as a JSON file.

## Add a role template

1. Under **Role templates**, type a **Template name**.
2. Tick the **Permissions** the template grants.
3. Click **Add role template**.

## Who can do this

- Every role, viewers included: read approval rules, policy packs and role templates.
- Organization administrators: add, edit, turn off and delete approval rules, add the baseline, activate or archive packs and add role templates. Owning a workspace is not enough; everyone else sees a read only notice explaining how to get the role.
- Workspace owners and organization administrators: **Export audit**.

## Learn more

[Governance and Scale](../governance-and-scale.md)
