---
id: organisations
title: Organisations
summary: Onboard customer companies, watch each organisation's health and change its plan, for platform operators.
keywords: organisations, customer, onboard, tenant, plan, health, first admin, invitation, platform operator
docs: ../customer-organisations.md
---

# Organisations

Organisations is the platform operator's console for customer companies run on this ANUM deployment. It shows counts and health only; the console cannot read titles, messages, files or names inside an organisation.

## Onboard a customer company

1. Open **Organisations** in the sidebar under **Admin**.
2. Click **Onboard a customer company**.
3. Fill **Organisation name**, **Organisation id** (permanent; lowercase letters, digits, "_" or "-"), **First workspace** and **First admin's email**, and pick a **Plan**.
4. Click **Create organisation**. You do not become a member.
5. Copy the invitation with **Copy link** and send it to the first admin. It is shown once and can be used once by that email. Click **Done**.

## Watch how organisations are doing

1. The summary shows organisations, those that need attention, members and overdue work.
2. Search the table with **Search by name or id**, or filter by health: **Needs attention**, **Setting up**, **Active** or **Idle**.
3. Each row shows members, workspaces, pending invitations, open and overdue work, open agent tasks and the last activity.

## Change a plan or reinvite the first admin

1. In the **Plan** column, pick **Trial**, **Standard** or **Enterprise**. The change is saved at once.
2. If an organisation has no active members yet, click **New invitation for the first admin** to issue a fresh single-use link, then copy and send it.

## Who can do this

- Platform operators only: see this console, onboard organisations, change plans and reissue invitations. The entry appears in the sidebar only for them.
- Everyone else who opens it by link sees that the realm role platform_operator is required.

## Learn more

[Customer Organisations](../customer-organisations.md)
