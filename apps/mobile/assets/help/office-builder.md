---
id: office-builder
title: Office builder
summary: Arrange the rooms and desks of your office on a plan, edit room details, and save the layout in one step.
keywords: office, builder, layout, plan, room, desk, floor, drag, resize, rotate, template, equipment, team, headquarters, owner
docs: ../ANUM_MINE_OFFICE_REGISTRY_BACKEND.md
---

# Office builder

The office builder is a plan of one floor in metres. You drag rooms, resize them, turn them, put desks inside them and fill in their details. Your moves are a draft until you click **Save layout**, and then they are saved together or not at all.

Only the workspace owner can change the office. Everyone else sees the same plan, read only, with a note that explains why.

## Open the builder

1. Open the headquarters and click **Manage spaces**.
2. Click **Open visual builder**.
3. Pick the **Building** and the **Floor** at the top. If there is no building or floor yet, create them in office management first.

## Arrange rooms

1. Click a room on the plan to select it. Its details open in the **Room** tab.
2. Drag the room to move it, or drag a white square on its edge to resize it. **Snap to grid** keeps the edges on 0.5, 1 or 2 metres, or switch it off.
3. Click **Rotate 90 degrees** to turn the room a quarter turn. Width and depth swap, and the desks and the door turn with it.
4. Type exact numbers under **Position and size (metres)** if you prefer.
5. A room that overlaps another room, or leaves the dashed floor outline, turns red. The list at the bottom, **Problems**, says what is wrong in plain words. You cannot save until the red is gone. Rooms that already overlapped before you started show with a dashed outline and never block a save.
6. Use **Undo** and **Redo** (Control or Command with Z, and Shift with Z), **Discard** to throw the draft away, and **Save layout** (also Control or Command with S) to save.

On a phone or tablet, tap a room to select it, then drag it with your finger. While a room is selected the plan does not scroll under your finger. Tap an empty part of the plan to scroll again. Use **Show details** and **Hide details** to open and close the details sheet.

## Add, copy and delete rooms

1. Open the **Rooms** tab. Click a template (Open office, Meeting room, Reception, Warehouse or Workshop) to create a room in a free spot. Tick **Also create the template desks** first if you want its desks.
2. Or fill in **Blank room** and click **Add blank room**. It appears on the plan as an unsaved change: move it, then **Save layout**.
3. Select a room, then click **Copy room** to make a copy next to it. Tick **Also copy the desks (never the agents)** to copy its desks without the agents.
4. **Delete room** works only on an empty room. If desks are still in it, delete them first. Rooms that have no place yet wait under **Rooms waiting for a place**: click **Place on plan**.

Creating, copying and deleting happen on the server at once. Moves and resizes wait for **Save layout**.

## Room details

In the **Room** tab, under **Details**, set the **Name**, the **Arabic name**, the **Type**, the **Capacity (people)**, the **Colour**, a **Description**, **Notes** and the **Equipment** from the fixed list, each with a quantity and a note. Click **Save details**.

**Linked team** connects the room to a department. The page then shows the real number of people and assistants in that department. With no team linked, or when the numbers cannot be read, it says **Not connected**. Nothing is invented.

## Desks

1. In the **Room** tab, under **Desks**, click a desk to select it. Drag its dot on the plan to place it inside its room, or type its position.
2. Use **Agent at this desk** to seat an agent. An agent who sits at another desk is moved: the old seat becomes vacant.
3. **Rename** changes the desk name. **Delete desk** works when no agent sits there.
4. Type a name under **New desk name** and click **Add desk**. A new desk gets a free spot in its room as an unsaved change.

## If someone else changed the office

If another person (or another window of yours) saved changes to this floor first, saving shows **The office changed while you were editing**. Your draft is kept. Click **Reload and reapply my changes**: your changes go back on top of the latest office, except for anything the other person moved or deleted, which the page lists. Check the plan and save again. **Discard mine and reload** drops your draft instead.

## Floor and building

The **Floor** tab sets the floor name, level and size (width and depth in metres) and renames the building. **Delete floor** and **Delete building** work only when they are empty; if not, the page lists what is still inside.

## Keyboard

Select a room with Tab, then use the arrow keys to move it, Shift with the arrow keys to resize it, R to rotate it, and the square bracket keys to go to the next or previous room. Changes are announced for screen readers. Escape drops the selection.

## Who can do what

- The workspace owner can change everything on this page. The server checks this on every save.
- Other roles can open the builder and look, but every edit is off.
