---
id: skills
title: ANUM Store
summary: Browse reviewed skills for agents, see the tools and risk each one needs, and install, upgrade or turn them off.
keywords: skills, store, install, upgrade, disable, uninstall, version, rollback, tools, risk, agents
docs: ../skills.md
---

# ANUM Store

The ANUM Store holds reviewed instructions for agents, called skills. Each skill lists the tools it needs, their risk and when they wait for approval. Agents load the skills installed in the workspace.

## Find a skill

1. Open **ANUM Store** in the sidebar.
2. Type in **Search skills** (name, id, description or tool).
3. Under **Show**, pick **All**, **Installed**, **Not installed** or **Built in**.
4. Click **Details** on a skill to see its tools and risk, its **Versions**, the **Agents that load it** and its **History in this workspace**. Click **Open Agents** to go to the agents.

## Install or upgrade a skill

1. Click **Install** on a skill, or **Upgrade to** followed by the new version number.
2. Read the risk of the skill and of each tool it is granted.
3. Click **Install and grant**. Tool calls still go through workspace policy and approvals, and the install is recorded in the audit log.
4. To install an older version, open **Details** and click **Install this version** next to it.

## Turn off, remove or roll back a skill

1. Click **Disable** on an installed skill. If agents load it, a warning says how many, then click **Confirm disable**. Click **Enable** to turn it back on.
2. To remove it, click the bin button, then **Uninstall**.
3. To return to an earlier version, open **Details**, click **Roll back to** next to that version, then **Confirm rollback**.
4. Organization administrators can also click **Publish version** to add a new skill version. Versions cannot be edited once published.

## Who can do this

- Every role, viewers included: browse skills and their details.
- Workspace owners only: install, upgrade, enable, disable and uninstall skills.
- Organization administrators only: publish versions and roll an installation back.

## Learn more

[Skills](../skills.md)
