---
id: developer
title: Developer
summary: Ask a coding agent to change one of your GitHub projects, and approve the merge yourself.
keywords: developer, code, github, repository, pull request, merge, project, workflow, token, coding agent
docs: ../developer-mode.md
---

# Developer

Developer mode lets a workspace owner ask for a change to one of the workspace's GitHub projects. A coding agent makes it on a branch, runs CI and opens a draft pull request. Nothing merges until you approve it here.

## Add a project

1. Open **Developer** in the sidebar, under **Admin**, and open the **Projects** tab.
2. Click **Add a project**. Fill **Name**, **GitHub repository** and **Branch to build on**.
3. Under **GitHub token**, pick **A token for this repository** and paste a **Fine-grained token**, or use the API's token.
4. Add **Protected paths (optional)**, leave **Accept requests for this project** ticked and click **Save project**.
5. Click **Test connection**, then **Install workflow** (or **Copy the workflow file** and add it yourself).

## Ask for a change

1. Open the **Requests** tab.
2. Pick the **Project** and describe it under **What should change?**.
3. Tick **Allow changes to workflows, secret handling and protected paths** only if the change needs it.
4. Click **Ask the developer**. Follow its progress, open the **Pull request** or **Coding run** link, and click **Check now** to update it.
5. Click **Cancel** to stop a request. Use **Search requests** and **Load older requests** to find past ones.

## Approve and merge

1. When the pull request is ready, click **Approve & merge**.
2. Read exactly what will run, then click **Confirm merge**, or **Not now** to decide later.
3. To pause a project, click **Turn off** on the **Projects** tab. **Edit** and **Delete** are there too.

## Who can do this

- Workspace owners only: add projects, ask for changes and approve merges. The page is shown only to owners.
- Everyone else who opens it by link sees what it is and is asked to contact an owner.
- Daily request limits and model budgets apply.

## Learn more

[Developer mode](../developer-mode.md)
