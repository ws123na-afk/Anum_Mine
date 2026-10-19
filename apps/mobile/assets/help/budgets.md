---
id: budgets
title: Budgets
summary: See this month's model spend and set monthly cost and token limits for the organization and this workspace.
keywords: budgets, model budget, spend, cost limit, token limit, usage, monthly, 402, organization, workspace
docs: ../model-gateway.md
---

# Budgets

Budgets show the estimated model cost and tokens used this UTC calendar month, and let you cap them. When a budget is used up, task runs are refused and voice answers say so until the month resets or the limit is raised.

## Check usage

1. Open **Budgets** in the sidebar under **Admin**.
2. The page shows the current month and the day usage resets.
3. Two cards show **Organization** (all workspaces together) and **This workspace**. Each has **Estimated cost** and **Tokens** meters, and a badge such as **limited**, **no budget** or **budget used up**.
4. Below the meters read **Model calls**, **Input tokens**, **Output tokens** and **Unpriced calls**. Click **Refresh** to update.

## Set or change a limit

1. On a card, enter **Monthly cost limit (USD)**, **Monthly token limit**, or both.
2. Leave a field empty for no limit. A limit of 0 blocks every model call.
3. Click **Save limits**.
4. If unpriced calls appear, set a token limit too: they add tokens but no cost.

## Remove a limit

1. On a card that has a budget, click **Remove limits**.
2. The card shows **no budget**, and the last change says when and by whom.

## Who can do this

- Workspace owners: see budgets and usage and set or remove the **This workspace** limits. Others see that owner access is required.
- Organization administrators who own the workspace: also set or remove the **Organization** limits. Other owners see a read only notice on that card.

## Learn more

[Model Gateway](../model-gateway.md)
