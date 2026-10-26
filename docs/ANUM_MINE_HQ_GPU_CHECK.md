# ANUM MINE headquarters: graphics check, frame governor and GPU recovery

Date: 26 October 2026.

## What is proven and what is not

The 3D office has **never run on a real graphics card in this project's tests**. The build sandbox and CI only have a software rasteriser (SwiftShader), so shadows, PBR shading, the night glow and the glass shader are unproven on real GPUs and real browsers. Nobody in that environment can get a GPU.

What this work adds is the means for the owner to check it in one click, and for the app to protect itself:

- Verified here and in CI, with software GL: the logic. The tier registry, the governor state machine, the verdict rules, the shape and anonymity of the report, the panel, the pin, a lost and restored context, the fallback to the list and plan, the shader-failure fallback, the owner script's flow, accessibility, English and Arabic, five viewport sizes.
- **Not verified, owner's device only:** how fast any tier is on a real GPU, whether the governor's thresholds suit real frame times, whether shadows, PBR, bloom and the glass shader compile and look right on Windows/Chrome with a GPU, on Intel, AMD, NVIDIA, Apple or phone GPUs. Every number a sandbox run prints about speed describes software drawing and says nothing about a graphics card. The panel says so itself when it finds a software rasteriser.

## The Graphics check panel

Open it from **Office tools, Graphics check**, or by clicking the **Graphics: High** chip in the header (next to the live status chip). It is a separate lazy chunk (`hq-gpu/HqGpuCheckPanel`), fetched only when opened.

| Section | What it shows |
|---|---|
| This device | WebGL version, renderer and vendor strings (`WEBGL_debug_renderer_info`; when the browser hides them, the general name is shown with a note and the software answer is "unknown"), whether it is a software rasteriser, largest texture, device pixel ratio, which extensions the office uses and whether they are present, processor threads, device memory (rounded by the browser) |
| Quality now | the tier in force and why (pinned by you, software rasteriser, very large screen, automatic), the governor's rung, pixel ratio, shadow map, night glow, full or simple materials |
| Measure | the length of each run (2, 5 or 10 seconds), **Measure this tier** and **Benchmark every tier** |
| Results | per tier: median, 95th percentile and worst frame time, frames per second, dropped frames (over 25 ms), draw calls, triangles, a memory estimate |
| Verdict | a plain sentence, for example "Your graphics card can run Ultra at 60 frames a second" or "Use Medium: it holds 60 frames a second here, and the heavier tiers dropped frames", with **Use this tier** and **Back to automatic** |
| Report | **Save report (JSON)** and **Copy report** |

How a run measures: the camera orbits the scene (a full turn every ten seconds with a slight nod), and when a building with cut-away floors is open, one floor is lifted out and put back during the run. The first 0.7 seconds are discarded (shader compilation). Frame time is the gap between animation frames, which is what a person sees; the draw call alone would hide a slow GPU. At campus level there is no floor to change, and the results say so: open a building to include it. The run restores the camera, the floors and the tier in force when it ends or is stopped, and the governor is off while it runs.

"Use this tier" writes `localStorage["anum.hq.quality"]` (the existing pin) and applies it at once. A pin always wins over the governor and the automatic choice. "Back to automatic" removes it.

Verdict rules (`lib/hqGpuReport.ts`, `verdictFor`): the heaviest tier whose median is 18.5 ms or less and whose 95th percentile is 25 ms or less holds 60 frames a second; failing that, the heaviest with a median of 34 ms or less and a 95th percentile of 50 ms or less holds 30; failing that, the lightest tier with "struggles". A software rasteriser always gets the software verdict (lightest tier), whatever the timings say.

Memory estimate: three.js and the browser do not report real GPU memory. The estimate adds geometry buffers, texture sizes (with mipmaps), the canvas, the shadow map and the glow targets.

## The tier registry

`lib/hqQualityTiers.ts` is the one list of tiers (`HQ_TIERS`): an id (the pin value), a key (its message key and the name in reports), a shadow map size and whether the night glow is on. The renderer, the governor, the chip, the panel's benchmark and the tests all read it. **A new tier is one entry in that list plus two messages** (`hqGpu.tier.<key>` in `hqGpu.en.ts` and `hqGpu.ar.ts`); the panel then benchmarks it, a pin accepts it and the unit test checks its names. The photographic tier from the materials and lighting work adds its entry there and its extra settings to `TierSpec`, `ladderFor` and the renderer.

## The frame governor

`lib/hqGovernor.ts` is a pure state machine (the renderer feeds it frame times and applies what it says), so every rule is unit tested with simulated frame times.

- The ladder, per tier (`ladderFor`): pixel ratio 1, 0.85, 0.7, then the shadow map (smaller, then off), then the night glow, then the simple materials (the glass and reflection effects). Rungs that change nothing for a tier are left out.
- It watches the rolling median of the last 40 frames. Above 26 ms (about 38 frames a second) it steps down one rung; at 19 ms or less for 12 seconds of rendering it steps up one rung. Between the two it does nothing. One hitch never moves it (median), a frame over 250 ms is ignored (shader compile, an idle gap), and there are 3 seconds between changes.
- A step down that follows a step up within 30 seconds doubles the wait before the next step up (up to 96 seconds), so a borderline GPU settles instead of flip-flopping.
- Ambient scenery motion still steps down first, as before. Then the ladder.
- It never runs while a tier is pinned, on a software rasteriser (which already takes the lean path), or during a Graphics check run. It forgets its samples when the tab is hidden and takes none while hidden.
- Frame time while the camera or a cut-away moves is the gap between draws; when ambient motion paces the frames it is the draw call alone, because the gap is then the motion's own rate.
- The simple materials rung is the last resort. It rebuilds the materials, which costs one hitch.

The header chip names the tier, adds "reduced" when the governor has lowered anything, and its tooltip says why (pinned, software, very large screen, automatic) and what is lowered. It is a button that opens the Graphics check.

## Context loss and shader failure

- `webglcontextlost`: a non-blocking banner ("Restoring the 3D view...", it does not cover or block the page) and drawing stops. The list, the plan and the tools stay usable.
- `webglcontextrestored`: three.js re-creates its own state; the renderer also drops what lived in render targets (reflection maps, the glow pass, the shadow map), restarts the governor and rebuilds the scene from the stage's current scene. The banner goes away.
- A context that does not come back within 8 seconds falls back to the list and the 2D plan with the existing notice, which now has a **Try 3D again** button.
- A browser that cannot start WebGL 2 gets the list and the plan at once (unchanged), with the same button.
- A shader that fails to compile or link (`renderer.debug.onShaderError`): a short, non-sensitive diagnostic is kept (the first 160 printable characters of the program log, never any scene or account data), the renderer switches to the simple materials and keeps the scene on screen. If the simple materials fail too, it falls back to the list and the plan.
- Optional telemetry hook, **off by default** (`lib/hqGpuTelemetry.ts`): with `VITE_ANUM_HQ_GPU_TELEMETRY=on` at build time the app dispatches a `anum:hq-gpu` event on `window` for tier changes, context loss and restore. Nothing is sent anywhere by the app; a deployment that wants numbers listens and forwards them. Events hold a tier key, a rung number and a reason code only.

## The report

`Save report (JSON)` writes `anum-hq-graphics-report.json` (schema `anum.hq.gpu-report/1`). It holds: the WebGL version, renderer and vendor strings, the software answer, limits and extensions, the device pixel ratio, the window size, processor threads, device memory, browser family and major version, operating system family, the quality state, and per tier the frame statistics, draw calls, triangles and the memory estimate, plus the verdict. It holds no name, account, tenant or workspace id, address, page link or user agent string. The report is built from a fixed list of fields (anything else is dropped by construction), strings are clipped to printable ASCII, and `test/hqGpu.test.ts` and `e2e/hq-gpu-check.spec.ts` check this.

## Run it on your PC (owner steps)

Needs: Windows, Google Chrome (or Microsoft Edge), Node.js 22 or newer, the repository folder, and a PC that has a graphics card. Do not use a remote desktop session: it hides the GPU.

1. Open **Windows PowerShell** in the repository folder (`cd` to it) and install once: `pnpm install` (if `pnpm` is missing: `npm install -g pnpm`).
2. Run the check against the running app. For the Oracle VM:

```powershell
node scripts/hq-gpu-check.mjs --url https://app.anum360os.com --login
```

3. A Chrome window opens. **Sign in there yourself** (the script never sees or asks for your password), open the Office, then return to PowerShell and press **Enter**. Do not touch the window while it runs (about two minutes). It times every tier on the campus, then opens a building and times every tier again with a floor change.
4. Read the table and the verdicts it prints. Send us the folder it names (`hq-gpu-check-<date time>`): `summary.json`, `report-campus.json`, `report-building.json` and the screenshots (campus day, dusk, night; the building cut-away).

Other forms:

```powershell
# Edge instead of Chrome
node scripts/hq-gpu-check.mjs --url https://app.anum360os.com --login --channel msedge
# A longer, steadier run (10 seconds per tier) into a folder you choose
node scripts/hq-gpu-check.mjs --url https://app.anum360os.com --login --length 10 --out C:\hq-gpu
# A local build (pnpm build, then pnpm --filter @anum/web exec vite preview --port 4173)
node scripts/hq-gpu-check.mjs --url http://127.0.0.1:4173 --login
```

On macOS or Linux the same commands work in a terminal. The same check without any script: open the Office, **Office tools, Graphics check, Benchmark every tier, Save report**.

Sign-in: `--login` is the way; nothing is stored. If you have an access token and accept handling it like a password, `ANUM_HQ_SESSION_TOKEN` (PowerShell: `$env:ANUM_HQ_SESSION_TOKEN = Read-Host`) places it in the page's session storage; the script never prints it and never reads any file for secrets.

The script starts Chromium headed with `--use-angle=default --enable-gpu-rasterization --ignore-gpu-blocklist`. If only a software rasteriser or no WebGL 2 is found it prints a clear warning ("ONLY SOFTWARE GRAPHICS WERE FOUND", what it found, what to try: turn on hardware acceleration, check `chrome://gpu`, update the graphics driver, no remote desktop), still saves everything, marks the results as software and exits with code 2 (`--allow-software` makes it 0; it also lets Chromium use SwiftShader, which is how the repository's own test runs it). Exit codes: 0 done on a GPU, 2 software only, 3 could not run (bad option, no browser, not signed in).

## Tests

- Unit (`test/hqGpu.test.ts`): the tier registry (ordered, unique, named in both languages, the pin parser), the ladder, frame statistics, the governor with simulated frame times (healthy, slow, hitches, slow climb back, hysteresis, no flip-flop, disabled, reset), verdicts, software detection, the report shape and anonymity, the telemetry hook.
- Browser (`e2e/hq-gpu-check.spec.ts`, software GL): the panel opens from Office tools and reports a software rasteriser honestly; the chip; a benchmark of every tier; the pin and "Back to automatic"; the saved report; a lost and restored context (banner does not block, scene returns); a context that never returns (list and plan, then "Try 3D again"); a simulated shader link failure (simple materials, scene stays); axe; English and Arabic (right to left); 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 with no horizontal overflow.
- Browser (`e2e/hq-gpu-script.spec.ts`): the owner script's options and a full run against the mocked office.

## Gaps

- Nothing here has run on a real GPU. Thresholds (26 ms, 19 ms, 12 s, the verdict limits) are reasoned defaults to be tuned from the first real reports.
- GPU time is not measured directly (the timer query extension is blocked or absent in most browsers); frame time stands in for it.
- The memory figure is an estimate.
- Antialiasing is chosen when the context is created and is not part of the ladder.
- The simple-materials rung is one way within a session unless the governor climbs back (it can, slowly); a shader fault keeps it on.
- Phones and tablets are not covered by the script; use the in-app panel there.
