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

## Be told before the limit

1. On a card, enter **Alert owners at (%)**, a whole number from 1 to 99, and click **Save limits**.
2. Owners are already told in the app at 50, 75, 90 and 100 percent of a limit; your own threshold is added to those. Leave the field empty for the fixed alerts only.

## See where the spend goes

1. Under **Where the spend goes**, choose **Group by**: **Day**, **Month**, **Person**, **Agent** or **Feature**.
2. Choose the **Scope** (this workspace or the whole organization), the **Period** (this month, the last 30 days, 3 or 12 months, or custom dates) and the **Measure** (estimated cost or tokens).
3. The chart shows columns over time or ranked bars; the table below lists every group with calls, tokens and cost. **Load more** fetches further groups when a period holds more than one page.
4. Click **Export CSV** to download the whole period as a file.
5. The breakdown by day, person, agent and feature exists from the day it was switched on; earlier spend is in the monthly totals (group by **Month**).

## Remove a limit

1. On a card that has a budget, click **Remove limits**.
2. The card shows **no budget**, and the last change says when and by whom.

## Who can do this

- Workspace owners: see budgets and usage and set or remove the **This workspace** limits. Others see that owner access is required.
- Organization administrators who own the workspace: also set or remove the **Organization** limits. Other owners see a read only notice on that card.

## Learn more

[Model Gateway](../model-gateway.md)

## Agent meeting budget

A separate card limits what live agent meetings may spend in a month across the whole workspace (default USD 50). Owners can change it or return to the default. When it is used up, running meetings pause and none can start or resume until the month resets or you raise the limit. See [live meeting budget](../ANUM_MINE_LIVE_MEETING_BUDGET_AND_TRANSCRIPTS.md).
