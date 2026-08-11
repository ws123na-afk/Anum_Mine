# Approvals and Risk

ANUM should treat approval as a first-class runtime primitive. Agents may plan actions, but the system decides whether an action can run automatically, requires confirmation, or must be blocked.

## Risk Levels

Risk classification should be simple at first:

- Low: read-only or reversible actions with limited exposure.
- Medium: actions that modify private state, create durable records, or use paid services.
- High: actions that send messages, spend money, delete data, change permissions, publish content, or affect external systems.
- Blocked: actions outside policy, outside scope, or requiring unavailable credentials.

## Approval Objects

An approval should capture actor, tenant, workspace, agent run, proposed tool call, target integration, input summary, risk level, policy reason, expiration, approver, decision, and resulting execution event. Approval records must be immutable except for status transitions.

## Runtime Behavior

When an agent proposes a risky action, the runtime should pause execution, emit an event, notify subscribed clients, and wait for a decision. The runtime resumes only after approval is granted and the tool inputs still match the approved request.

## User Experience

Approval prompts should be concrete. Users need to see what will happen, which account or integration will be used, what data will be sent, what can be undone, and why ANUM is asking. The product should avoid vague prompts such as "approve this action".

## Now

Support basic approval records, task pausing, approve/reject decisions, audit logging, and one high-risk sample action.

## Later

Add delegated approvals, organization approval chains, policy-driven auto-approval, approval templates, mobile push approvals, emergency revocation, and simulations that explain why a policy allowed or blocked an action.