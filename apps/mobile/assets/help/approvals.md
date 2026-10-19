---
id: approvals
title: Approvals
summary: Review the exact action an agent wants to take, then approve or reject it.
keywords: approval, approve, reject, risk, payload hash, decision, history, routing
docs: ../approvals-and-risk.md
---

# Approvals

When an agent wants to do something risky, such as sending a message or changing data, its run pauses here until a person decides. You see exactly what it will send before anything happens.

## Review a waiting approval

1. Open **Approvals** in the sidebar, under **More**. The counter next to it shows how many wait.
2. Each card names the tool the agent wants to call and why.
3. Read **Exactly what it will send**, where it **Sends to**, the risk, the **Payload hash** and when it expires.
4. If it needs several people, the card shows the progress and **Who can approve**.

## Approve or reject

1. Optionally type a **Reason (optional)**. It is kept in the audit trail.
2. Press **Approve** to let the run continue with exactly what is shown, or **Reject** to stop it.
3. If your decision is not allowed, the card shows **Decision refused** with the reason, and the approval stays waiting.
4. To change who approves what by AED amount, press **Open approval rules**.

## Check past decisions

1. Scroll to **Decisions** below the waiting cards.
2. Open a row to see who decided and when, the **Reason**, the arguments and the payload hash.
3. The counters at the top show how many are **Pending**, **Approved** and **Rejected**.

## Who can do this

- Every member of the workspace, viewers included, can read approvals and their history.
- Workspace owners can approve or reject. A person an approval rule routes the approval to can decide it too.
- With the two-person rule on, a high-risk action cannot be approved by the person who started the task; another owner must approve it.
- Approval rules are set under Governance by organization administrators.

## Learn more

[Approvals and Risk](../approvals-and-risk.md)
