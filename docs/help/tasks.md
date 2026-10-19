---
id: tasks
title: Tasks
summary: Ask ANUM to do a piece of work and follow it to the end.
keywords: task, run, retry, cancel, resume, archive, export, agent
docs: ../tasks.md
---

# Tasks

A task is a piece of work you give ANUM; each attempt at it is a run. Tasks lets you start work, follow each run step by step and act on what it needs.

## Start a task

1. Open **Tasks** in the sidebar.
2. Type what you want under **Task instructions**.
3. Press **Run task** (or Ctrl+Enter).
4. Read the result on the **Answer** tab. If the run pauses before a risky action, the **Approvals** tab shows it; press **Open approvals** to decide.

## Follow and act on a task

1. Pick a task from the history to open its detail.
2. Use **Runs** to see each run and its steps, and **Activity** for its events.
3. Press **Retry** for a failed or cancelled task, **Resume** for a run that stopped midway, or **Cancel** to stop it.
4. Press **Edit** to change the title (and the instructions while it has never run), then **Save**.
5. Press **Export JSON** or **Export Markdown** to download the run log.

## Find, archive and restore tasks

1. Type in **Search tasks**, or filter by **Status**, **Created by** and the dates.
2. Turn on **Show archived** to see archived tasks; press **Clear filters** to reset.
3. Press **Load more tasks** for older ones.
4. On a finished task, press **Archive**; on an archived one, press **Restore**. Its history is kept.
5. Tick several tasks, or **Select all loaded**, to archive, restore or cancel them together.

## Who can do this

- Viewers can read every task, its runs, events and exports.
- Members can also create, run and retry tasks, and edit or archive tasks they created.
- Owners can also cancel any task and edit or archive any task. Actions your role cannot do are disabled with a line saying who can.

## Learn more

[Tasks and agent runs](../tasks.md)
