---
id: enterprise
title: Enterprise
summary: Configure regional routing targets for model requests and manage the organization's package catalog.
keywords: enterprise, routing target, region, failover, provider, model, marketplace, package, install
docs: ../governance-and-scale.md
---

# Enterprise

Enterprise shows where model requests can run: routing targets by region, their health and whether failover is ready. It also holds the marketplace catalog of integration and skill packages your organization distributes.

## Add or edit a routing target

1. Open **Enterprise** in the sidebar under **Admin**.
2. Click **Add routing target**, or the edit button on a row under **Routing targets**.
3. Fill **Target id**, **Region**, **Provider**, **Model**, **Status**, **Modalities (comma separated)**, **Sensitivity (comma separated)**, **Cost per 1k tokens (USD)** and **Latency (ms)**.
4. Click **Save target**. Targets cannot be deleted: set **Status** to offline to take one out of routing.
5. Click **Refresh** to reload the regions, healthy targets, installed packages and failover figures.

## Install a package

1. Under **Marketplace catalog**, use **Search packages**, pick a **Kind**, or tick **Verified only** or **Installed here**.
2. Click **Install** on a package and review the permissions it requests.
3. Click **Install** in the dialog to confirm. The install is recorded in the audit log, and approvals still apply to its tools.
4. To remove a package, click **Uninstall** and confirm.

## Publish a package

1. Click **Publish package**.
2. Fill **Package id**, **Name**, **Version**, **Publisher**, **Kind**, **Permissions it requests (comma separated)** and **Regions (comma separated)**. Tick **Publisher verified by the organization** if it is.
3. Click **Publish**. Use the edit button to change a package later, or **Delete** to remove it from the catalog.

## Who can do this

- Every role, viewers included: see routing targets, the figures and the catalog.
- Organization administrators: add and edit routing targets and publish, edit or delete catalog packages. Everyone else sees a read only notice.
- Workspace owners: install and uninstall packages in their workspace.

## Learn more

[Governance and Scale](../governance-and-scale.md)
