---
id: integrations
title: Integrations
summary: See which services ANUM is connected to, what each one still needs, and the health of the services it runs on.
keywords: integrations, connect, setup, gmail, whatsapp, push, health, token, connection, services
docs: ../integrations-setup.md
---

# Integrations

Integrations shows the services ANUM works with, what each one still needs, and the health of the services this deployment runs on. Keys are set on the server and are never shown here.

## Find an integration and its status

1. Open **Integrations** in the sidebar.
2. Pick **All**, **Connected**, **Needs setup** or **Error** to filter, or type a name such as Gmail or WhatsApp in the search box.
3. Integrations are grouped in sections. Click a section heading to fold it open or closed.
4. Each row shows a status badge and one button: **Connect**, **Set up**, **Fix**, **Manage** or **Details**.
5. Click **Refresh** to read every status again.

## Set up or fix a connection

1. Click the row's button to open its details.
2. Read what it does and its status. Click **Setup guide** for the step by step instructions.
3. If shown, add the names under **Server settings to add** on the server, and register the **Callback URL to register with the provider** (use **Copy**).
4. When the integration is ready, click **Open** followed by the page name (for example Email) to connect it there.
5. Click **Check connection** to confirm it now works.

## Manage a deployment service

1. In the services section, click **Manage** on a service.
2. Click **Test connection** to check it now. A **How to fix** line appears when it has a problem.
3. Tick or clear **Use** followed by the service name **in this workspace**, then click **Save configuration**.
4. Where a service accepts a workspace token, paste it and click **Store token**. Use **Replace token** or **Remove token...** to change it later. The token is stored encrypted and only its last four characters are shown.

## Who can do this

- Every role, viewers included: see what is connected and the health of each service.
- Workspace owners and organization administrators: see missing server settings and callback URLs.
- Organization administrators only: run **Check connection**.
- Workspace owners only: test services, turn them on or off for the workspace and store tokens.

## Learn more

[Connecting Integrations: the Owner's Runbook](../integrations-setup.md)
