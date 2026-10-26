---
id: office-history
title: Office history
summary: Search what was recorded in your office, open any record with its source, and step through the recorded changes to a room.
keywords: history, office, audit, timeline, room, agent, correlation, slider, changes, export, headquarters, owner
docs: ../ANUM_MINE_HQ_HISTORY_PANEL.md
---

# Office history

Office history is a timeline of the records your workspace has stored: room and desk changes, tasks, approvals and meetings. Every row is a real audit record. If nothing was recorded, nothing is shown, and a missing record never means that nothing happened.

## Open it

1. Open **ANUM MINE**, then **Office tools**, then **History** for the whole office.
2. Select a room, then **Room history**, for the records that name that room or its desks.
3. Select an agent, then **Agent history**, or use the history button on a card in **Team**.

## Search and filter

1. Type words in **Search**: they are matched in who did it, what was done and what it was done to.
2. Open **Filters** to choose an event type, a room, an agent, an exact user or agent id, and a start and end day. The end day is included.
3. Press **Apply filters**. **Clear filters** shows everything again.

Long histories load as you scroll. If more is waiting, press **Load older records**. Room and agent views keep the loaded records that name that room or agent, so load more to reach older ones.

## Open a record

Select a row to open it in the side panel. You see who did it, when, the correlation id and, for office changes, the values before and after. **Source records** shows the task, run, approval or meeting the record points at; select **Show** to read a short summary without leaving the office. **Show records of the same request** lists every record that shares the correlation id.

## Step through a room

Open the **Room changes** tab and choose a room. Each step on the slider is one stored record about that room, with the values before and after. Steps are records, not equal spans of time. Nothing between two records is drawn or guessed.

Where the records cannot say what the room looked like, the slider says **Cannot be reconstructed for this time**. That happens before the oldest loaded record, in a gap where the room changed without a record, and for times that have not happened yet. Who sat where, and who was present, is not stored and is not shown.

## Export

When the filters can be matched by the audit export, **Export these records (CSV)** downloads them. It is not offered for room or agent views or for the "Other" type.

## Who can do this

Only workspace owners and organisation administrators can read office history. Others see a message that says so. Exporting needs the audit export permission as well.
