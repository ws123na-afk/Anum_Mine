---
id: approval-settings
title: Approvals settings
summary: One page for who must approve risky actions, the organization's approval rules, how a pausing action is routed, and when approvers are reminded.
keywords: approvals settings, approval policy, two-person rule, medium risk, approval rules, routing, reminder, approve, owner, safety
docs: ../approvals-and-risk.md
---

# Approvals settings

Approvals settings gathers everything that decides how an agent action waits for a person: the workspace policy (two switches), the organization's approval rules, how a pausing action finds its approvers, and the reminder for approvals that wait too long. Each section saves on its own. The old link **#policy** still opens this page.

## Set the policy

1. Open **Approvals settings** in the sidebar under **Admin** (the **Policy** link at the top jumps to this section).
2. Turn on **Two-person rule for high-risk actions**: whoever created a task or started its run cannot approve its high-risk actions, and another owner must. If the page warns that the workspace has only one active owner, invite a second owner in **Members** first.
3. Turn on **Approval for medium-risk actions** to make medium-risk tools pause and wait for an owner, like high-risk ones.
4. Click **Save policy**. Before saving, **Undo changes** puts the switches back as they are in force. The line under the switches says when the policy last changed and by whom.

## Read the rules and the routing

1. **Approval rules** lists the organization's rules: which tool patterns pause, the AED band each rule covers, and who approves each step. Rules apply to every workspace of the organization and are changed on **Governance** by an organization administrator; **Edit rules in Governance** takes you there.
2. **Routing** explains, in the order the API applies them, how a pausing action is routed: the rules that match, the first band that holds, and the fallback to workspace owners. Under **Bands in force** each rule that is on is shown with its band and its approvers; rules that are off are counted but do not route.

## Remind approvers

1. Under **Remind approvers**, choose after how many hours a waiting approval reminds this workspace's owners in the app, or **Off**.
2. Click **Save reminder**. Approvals still expire after 24 hours.

## Who can do this

- Anyone with an active membership in this workspace, viewers included: read every section.
- Workspace owners: save the policy and the reminder. Everyone else sees them read only, with their role and a note to ask an owner.
- Organization administrators: change the rules, on Governance.

## Learn more

[Approvals and Risk](../approvals-and-risk.md)
