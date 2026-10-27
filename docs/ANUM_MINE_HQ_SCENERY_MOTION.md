# ANUM MINE headquarters: exterior scenery motion

Status: built on 26 October 2026 with the exterior pass ([building look](ANUM_MINE_HQ_BUILDING_LOOK.md)). The owner said the scene felt static.
The interior counterpart (lift indicators, glints, rack lights, leaves, fans) is in [interiors](ANUM_MINE_HQ_INTERIORS.md) and shares the pin `localStorage["anum.hq.ambient"]` ("on"/"off" there, 0 to 2 or "on"/"off" here). This page says what now moves outside, what never does, and how the motion stops.

## The rule

Everything here is scenery. Nothing in it is read from a record, and nothing in it means a person, an agent, work or a number. A car on the ring
road is not "someone driving to a building"; a window that drifts in brightness is not "someone at a desk". Movement of agents and people is a
different system, backed by presence records (see [presence and campus](ANUM_MINE_PRESENCE_AND_CAMPUS.md)), and is not touched by this pass.

## What moves

| Motion | How it is drawn | Cost |
|---|---|---|
| Traffic: up to 18 cars, SUVs and vans on two lanes of the ring road, keeping right, and up to 3 more on one lane round the [centre plaza](ANUM_MINE_HQ_CENTER_PLAZA.md) roundabout | One instanced mesh in three parts (paint, dark trim, head and tail lights), matrices written per frame into preallocated objects (`lib/hqSceneryLayer.ts`). Pose is a pure function of the scene clock (`hqAssets/environment/scenery.ts`). One speed per lane, so no vehicle catches another | about 120 triangles per vehicle, one draw call per part |
| Water: swell, rings from the fountain and ponds, a few glints on the sun side | Fragment shader on the water class; up to four ripple sources come from the campus plan | none beyond the water pixels |
| Trees and fronds sway, flags and pennants ripple | Surface classes `foliage` and `cloth` (`MAT.foliage`, `MAT.cloth`); a vertex shader offsets by height and phase. A flag's `u` texture coordinate is its distance from the pole | vertex work only |
| Clouds drift across the sky | Noise in the sky dome shader; cover and tint follow day, blue hour and night | sky pixels only |
| Window light drifts a little in a few panes | A slow brightness change (at most 16 percent) on about one pane in ten. Panes never switch on and off, so there is no pattern of life | fragment work |
| Rooftop beacon blinks | Surface class `beacon`; steady glow when motion is off | fragment work |

## What never moves

People, agents, desks, anything in an interior, anything that shows a count, a status or a name. The sign lettering is static.

## Levels and when they apply

Level 0 is still, 1 runs at 15 frames a second (traffic, water, clouds), 2 at 30 (adds sway, window drift and the beacon).
`sceneryLevelFor` in `ambient.ts` decides, and the renderer re-checks it every frame:

| Condition | Level |
|---|---|
| prefers-reduced-motion, or the tab is hidden | 0 (the scene clock stops; resuming does not jump) |
| a software rasteriser (SwiftShader, llvmpipe), which CI runs | 0, unless a person or a test pins a level |
| more than 4 million device pixels | 1 |
| otherwise | 2 |
| sustained slow frames (3 seconds) | one notch down, before bloom, shadows or the pixel ratio are touched |

A person or a test can pin the level with `localStorage["anum.hq.ambient"]` = 0, 1 or 2; reduced motion and a hidden tab still win.
The canvas reports `data-scenery-level`, `data-scenery-frames`, `data-scenery-clock` and `data-scenery-vehicles`.

## Light presets

The night toggle has a third state, the blue hour ("Blue hour" button). It is a keyframe of the same blend as day and night (`DUSK` in
`lib/hqLighting.ts`): a deep blue zenith, a warm orange band at the horizon, a lavender haze, a low warm sun, and windows already lit.
The choice is kept in `localStorage["anum.hq.preset"]` (`lib/hqLightPreset.ts`), so the day and night boolean the pages pass around did not change.

## Tests

`test/hqSceneryMotion.test.ts` (lanes, spacing, poses, ripples, levels, vehicle parts, light presets, surface classes) and
`e2e/hq-exterior-look.spec.ts` (still by default on software GL, runs when switched on, frozen under reduced motion, day, blue hour and night differ, five viewports).

## Software rasterisers

On SwiftShader or llvmpipe (CI) the scene also leaves out what would never move there and what costs shader compile time before the first frame: vehicles are not placed, leaves, flags and rooftop lights are drawn as plain surfaces, and the glass, water and sky programs are the lean ones (no lit rooms, no rings, no clouds). Pinning `anum.hq.ambient` brings all of it back, which is how `e2e/hq-exterior-look.spec.ts` checks the motion and the screenshots were taken.

## Screens are not scenery

The live screens in the rooms are not scenery motion: they change only when a record changes (at most once a second per screen) and the scan line shows only while a fresh record says work is running. Under reduced motion the content still updates and the scan line stands still. See [live screens](ANUM_MINE_HQ_LIVE_SCREENS.md).
