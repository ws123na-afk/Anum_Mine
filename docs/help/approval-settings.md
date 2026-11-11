---
id: policy
title: Approval policy
summary: Decide who must approve risky agent actions in this workspace with two switches.
keywords: approval policy, two-person rule, medium risk, high risk, approve, owner, safety, policy
docs: ../approvals-and-risk.md
---

# Approval policy

The approval policy sets who must approve risky agent actions in this workspace. High-risk tools always pause for an owner's approval; two switches add to that. Every change is recorded in the audit log.

## Turn on the two-person rule

1. Open **Approval policy** in the sidebar under **Admin**.
2. Under **Approval rules**, turn on **Two-person rule for high-risk actions**.
3. Read **When on** and **When off** below the switch: when on, whoever created a task or started its run cannot approve its high-risk actions, and another owner must.
4. If the page warns that the workspace has only one active owner, invite a second owner in **Members** first.
5. Click **Save policy**.

## Require approval for medium-risk actions

1. Turn on **Approval for medium-risk actions**.
2. Medium-risk tools then pause and wait for an owner, like high-risk ones.
3. Click **Save policy**. The change applies from the next approval decision.

## Undo or check a change

1. Before saving, click **Undo changes** to put the switches back as they are in force.
2. The line under the switches says when the policy last changed and by whom, or that it was never changed (both switches are off by default).
3. Click **Refresh** to reload the policy in force.

## Who can do this

- Anyone with an active membership in this workspace, viewers included: read the policy in force.
- Workspace owners: change the switches and save. Everyone else sees the switches read only, with their role and a note to ask an owner.

## Learn more

[Approvals and Risk](../approvals-and-risk.md)
