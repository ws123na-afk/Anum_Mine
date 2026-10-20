---
id: agent-controls
title: Agent controls
summary: Stop every agent and outbound send at once, see what is held, and cap each agent's monthly cost and tokens.
keywords: agent controls, emergency stop, kill switch, stop agents, held, cost cap, token cap, default assistant, runaway agent
docs: ../agent-controls.md
---

# Agent controls

Agent controls hold the organization's emergency stop and a monthly cost and token cap per agent. While the stop is on, no agent, chat or voice answer calls the model, no tool runs, pending sends cannot be approved and automations do not start.

## Turn the emergency stop on

1. Open **Agent controls** in the sidebar under **Admin**.
2. In **Emergency stop**, type a reason. It is kept in the audit log.
3. Click **Stop all agents and sends**, then confirm.
4. Running and queued tasks are cancelled, and the other owners get a notification.

## See what is held

1. While the stop is on, **Held while the stop is on** lists pending approvals, your scheduled email and scheduled posts.
2. Use **Previous** and **Next** to page through them.
3. Pending approvals can still be rejected. Scheduled mail and posts go out after the stop is turned off unless you withdraw them.

## Cap an agent

1. In **Monthly cost caps per agent**, find the agent, or **ANUM assistant** for chat and voice.
2. Enter a monthly cap in US dollars, a monthly token cap, or both. Leave both empty for no cap.
3. Click **Save**. Once a cap is reached, that agent stops calling the model until the month resets.

## Turn the emergency stop off

1. Type a reason in **Emergency stop**.
2. Click **Turn the stop off**, then confirm.

## Who can do this

- Every member sees whether the stop is on.
- Workspace owners and the organization administrator turn the stop on, see what is held and see the caps.
- Only the organization owner (organization administrator) turns the stop off.
- Workspace owners change the caps.

## Learn more

[Agent controls](../agent-controls.md)
