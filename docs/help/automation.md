---
id: automation
title: Automations
summary: Chain agent work into automations that run on demand, on a schedule or when something happens.
keywords: automation, workflow, schedule, trigger, webhook, run now, run history
docs: ../automation.md
---

# Automations

An automation is a list of steps, such as an agent task, a notification, an email or a wait for approval, that ANUM runs in order. Start it yourself, on a schedule or from a trigger, and follow every run.

## Create an automation

1. Open **Automations** in the sidebar and press **New automation**.
2. Type a **Name**.
3. For each step, choose what it **Does**, for example **Create agent task**, **Notify me**, **Send email** or **Wait for approval**, and fill in its fields.
4. Press **Add step** for more steps. Steps run in order and a run stops at the first step that fails or waits.
5. Press **Create automation**.

## Run it and follow the runs

1. Select the automation and press **Run now**.
2. Under **Run history**, follow each run and its steps.
3. Use **Resume run**, **Retry run** or **Cancel run** on a run when it applies.
4. Press **Pause** to stop Run now and its schedules, and **Resume** to turn them back on.
5. Press **Edit** and **Save changes** to change it, or **Delete** and confirm with **Delete automation**.

## Run it on a schedule or a trigger

1. Under **Schedules**, press **Add schedule**, give a **Schedule name**, choose **Repeat**, **When** and **Time zone**, then press **Add schedule**.
2. Under **Triggers**, press **Add event trigger**, choose **When this happens**, and press **Add trigger**.
3. Or press **Add webhook** so another system can start it. Copy the address and secret, then press **I saved the secret**; the secret is shown only once.
4. Use **Turn off** or **Turn on** on a schedule or trigger at any time.

## Who can do this

- Viewers can read automations, their schedules, triggers and runs.
- Members can create automations and run any of them, and change only the automations, schedules and triggers they created.
- Owners can change any automation, and take over a schedule or trigger so it runs as them.

## Learn more

[Automation](../automation.md)
