# ANUM MINE headquarters: graphics check, frame governor and GPU recovery

Date: 26 October 2026. Updated 2 November 2026 with the first real GPU report ([Real devices](#real-devices)) and the tuning it led to.

## What is proven and what is not

The 3D office has never run on a real graphics card in this project's own tests; one real device has reported so far (the owner's laptop, see [Real devices](#real-devices)). The build sandbox and CI only have a software rasteriser (SwiftShader), so shadows, PBR shading, the night glow and the glass shader are unproven on real GPUs and real browsers. Nobody in that environment can get a GPU.

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
| Results | per tier: median, 95th percentile and worst frame time, frames per second, dropped frames (over 25 ms), GPU time (median, where the browser has the timer query), the warm-up left out of the timing, frames over 50 ms and how many of them compiled shaders, draw calls, triangles, a memory estimate |
| Verdict | a plain sentence, for example "Your graphics card can run Ultra at 60 frames a second" or "Use Medium: it holds 60 frames a second here, and the heavier tiers dropped frames", with **Use this tier** and **Back to automatic**. The box "After a benchmark of every tier, use its result on this device" (ticked by default) writes the verdict's tier as the pin when a full benchmark ends on a graphics card |
| Report | **Save report (JSON)** and **Copy report** |

How a run measures: before the first tier, every shader variant the measured tiers use is compiled ([Hitches and warm-up](#hitches-and-warm-up)). After each tier switch the camera already orbits but nothing is timed until the frames settle: at least 0.7 seconds and 12 frames, then until none of the last 8 frame gaps is longer than 2.5 times their median (or 34 ms), at most 6 seconds; the gap that ends the warm-up is not timed either (`WARM` and `warmedUp` in `hq-gpu/gpuBench.ts`). Then the camera orbits the scene (a full turn every ten seconds with a slight nod), and when a building with cut-away floors is open, one floor is lifted out and put back during the run. Where the browser offers `EXT_disjoint_timer_query_webgl2`, a timer query brackets each frame's draw and the GPU time is reported next to the frame time; every frame over 50 ms is counted with whether it created shader programs or new geometry or textures. Frame time is the gap between animation frames, which is what a person sees; the draw call alone would hide a slow GPU. At campus level there is no floor to change, and the results say so: open a building to include it. The run restores the camera, the floors and the tier in force when it ends or is stopped, and the governor is off while it runs.

"Use this tier" writes `localStorage["anum.hq.quality"]` (the existing pin) and applies it at once. With the remember box ticked, a finished benchmark of every tier on a graphics card does the same for the verdict's tier and says so ("Saved: this device now uses High. Back to automatic undoes it."); a partial run, a stopped run, a masked renderer name or a software rasteriser never pins (`rememberTierFor` in `lib/hqGpuReport.ts`). A pin always wins over the governor and the automatic choice. "Back to automatic" removes it. The owner script unticks the box: it only measures.

Verdict rules (`lib/hqGpuReport.ts`, `verdictFor`): the heaviest tier whose median is 18.5 ms or less and whose 95th percentile is 25 ms or less holds 60 frames a second; failing that, the heaviest with a median of 34 ms or less and a 95th percentile of 50 ms or less holds 30; failing that, the lightest tier with "struggles". A software rasteriser always gets the software verdict (lightest tier), whatever the timings say.

Per tier the report also holds `warmupMs` (milliseconds from the switch until the frames settled, not timed), `gpu` (`medianMs`, `p95Ms`, `samples` from the timer query, or null) and `hitches` (`count` of timed frames over 50 ms, how many of them `compile`d shader programs and how many allocated `buffers`). All numbers.

Memory estimate: three.js and the browser do not report real GPU memory. The estimate adds geometry buffers, texture sizes (with mipmaps), the canvas, the shadow map and the glow targets.

## The tier registry

`lib/hqQualityTiers.ts` is the one list of tiers (`HQ_TIERS`): an id (the pin value), a key (its message key and the name in reports), a shadow map size and whether the night glow is on. The renderer, the governor, the chip, the panel's benchmark and the tests all read it. **A new tier is one entry in that list plus two messages** (`hqGpu.tier.<key>` in `hqGpu.en.ts` and `hqGpu.ar.ts`); the panel then benchmarks it, a pin accepts it and the unit test checks its names. The photographic tier from the materials and lighting work adds its entry there and its extra settings to `TierSpec`, `ladderFor` and the renderer.

## The automatic choice

Before anything is measured, the renderer reads the unmasked renderer string (`WEBGL_debug_renderer_info`) on a scratch canvas and starts at the tier `startTierFor` in `lib/hqQualityTier.ts` gives:

| Graphics | Examples (renderer string) | Start tier |
|---|---|---|
| Software rasteriser | SwiftShader, llvmpipe, Microsoft Basic Render | Low (as before) |
| Discrete, Apple M | NVIDIA GeForce, AMD Radeon RX or Pro, Intel Arc A, Apple M1 to M4 | Ultra |
| Integrated | Intel UHD 620 and newer, Iris, Iris Xe, Intel Arc integrated, AMD Radeon Graphics and Vega APUs | High |
| Older integrated | Intel HD Graphics with a four digit model or none (2012 to 2015), Intel HD or UHD 500, 505, 510, 600, 605, 610 (12 to 18 execution units), AMD Radeon R2 to R7 APU graphics | Medium |
| Phone GPUs | Adreno, Mali, PowerVR, Xclipse, Immortalis | Medium |
| Hidden or unknown name | "Apple GPU" (Safari), an empty string | High |

A canvas over 4 million device pixels never starts above High (as before). The governor then works within that tier, and a pin wins over all of it.

Evidence: the first real report ([Real devices](#real-devices)) came from an integrated Intel UHD Graphics 620. The old rule started every graphics card on Ultra; there, Ultra had a 95th percentile of 66.7 ms and 14 dropped frames in 201, while High and Medium held a 16.8 ms 95th percentile, and the verdict was High. Integrated graphics therefore start at High. The older integrated chips have half the execution units of that UHD 620 or less, so they start one tier lower. The discrete, Apple M and phone rows are reasoned from the class of chip, not measured: no such device has reported yet, and the first report from one should confirm or move them.

## Hitches and warm-up

The first real report showed rare long frames on every tier (500 to 600 ms) and a 3.4 second worst frame on Low, the first tier measured. Reading the code against it found these causes, all fixed:

- **Timed warm-up.** The old run discarded a fixed 0.7 seconds but kept the frame gap that straddled its end, so a tier switch that stalled for 3.4 seconds was timed. Now each tier warms until its frames settle and the closing gap is never timed (see How a run measures).
- **Shader variants compiled on the frame that needed them.** three.js keys a program on whether shadows are on and on whether it draws into a render target (the night glow pass), so the first switch between shadows on and off, and the first glow frame of the evening, compiled every scene material again on the main thread. `lib/hqWarmup.ts` (its own lazy chunk) now compiles every variant ahead with `renderer.compile` and waits with `KHR_parallel_shader_compile`, so the browser compiles on other threads while the page keeps drawing (without the extension, one program is finished per animation frame). It also draws one hidden shadow pass when shadows are off, because the shadow pass has depth programs of its own. On a graphics card it runs 1.5 seconds after the scene settles, for the rungs the governor can reach (`programVariants` in `lib/hqQualityTiers.ts`); the benchmark runs it for every tier before the first one is timed. Performance marks `hq:warm`, `hq:bench:<tier>:switch` and `hq:bench:<tier>:timed` show it in the browser's performance panel.
- **Scene rebuilt on a switch to or from Low.** Low draws the billboards' posts and frames with one flat material, and the switch rebuilt the whole scene (geometry split, occlusion bake, buffer uploads). The renderer now swaps those meshes' material in place.
- **Canvas reallocated on every tier switch.** Every switch forced `setSize`, which reallocates the drawing buffer even at the same size. It now resizes only when the size or pixel ratio really changed.
- **Garbage from the frame loop.** The governor's rolling windows copied and sorted an array on every frame (about 300 young collections per 300,000 frames); they now use fixed buffers and allocate nothing. The draw no longer builds key strings for the canvas size and shadow camera, filters the mesh list, rebuilds the light preset, the effect list or the view projection twice per frame.

Not changed, and still possible causes of a rare long frame: the live screen and billboard atlases (2048 pixels wide) are uploaded again with new mipmaps when a cell changes (at most once a second per cell); a model arriving from its lazy chunk compiles on its first frame; the first visit to dusk or night bakes its reflection map; the simple materials (the governor's last rung) are new material objects and compile when it is reached. The new hitch counts in the report ("compiling shaders", new buffers) tell these apart on the next real run.

## The frame governor

`lib/hqGovernor.ts` is a pure state machine (the renderer feeds it frame times and applies what it says), so every rule is unit tested with simulated frame times.

- The ladder, per tier (`ladderFor`): pixel ratio 1, 0.85, 0.7, then the shadow map (smaller, then off), then the night glow, then the simple materials (the glass and reflection effects). Rungs that change nothing for a tier are left out.
- It watches the rolling median of the last 40 frames and the 95th percentile of the last 120. A median above 26 ms (about 38 frames a second) steps down one rung, and so does a 95th percentile above 40 ms (more than 1 frame in 20 longer than two and a half refreshes), once 60 frames are in. It steps up one rung after 12 seconds of rendering with a median at 19 ms or less and a 95th percentile at 25 ms or less (the verdict's "holds 60" limit). Between the two it does nothing. One hitch or a short burst never moves it, a frame over 250 ms is ignored (shader compile, an idle gap), and there are 3 seconds between changes.
- The hitch rule comes from the first real report: Ultra on an integrated Intel UHD Graphics 620 had a 16.7 ms median, so the median rule never saw anything wrong, but a 66.7 ms 95th percentile. Fed with frame streams rebuilt from that report (each one reproduces the report's count, median, 95th percentile, worst, mean and dropped frames), the governor steps Ultra down within a few seconds and never moves High or Medium; alternating Ultra hitches with High frames settles without flip-flopping (`test/hqGpuTuning.test.ts`).
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
4. Read the table and the verdicts it prints (it now also prints GPU time, warm-up and hitches per tier). Send us the folder it names (`hq-gpu-check-<date time>`): `summary.json`, `report-campus.json`, `report-building.json` and the screenshots (campus day, dusk, night; the building cut-away).

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

## Re-run after an update (compare before and after)

On the same laptop, plugged in, other apps closed, the same browser window size as before (the first report was Chrome at 1707 by 735 CSS pixels, campus by day):

1. Pull the update and open the Office as usual. Open **Office tools, Graphics check**. If a tier is pinned, press **Back to automatic** first, so the automatic choice is what you see: on this laptop the chip should now say **Graphics: High**.
2. Set the length to **5 seconds** (as the first run) and leave the camera on the campus by day. Press **Benchmark every tier** and do not touch the window until it says Done.
3. Press **Save report (JSON)** and send `anum-hq-graphics-report.json`. Compare with the first report: Low's worst frame and 95th percentile should no longer carry the 3.4 s warm-up, the cards show the warm-up separately, and "Frames over 50 ms" says how many long frames are left and whether they compiled shaders.
4. Optional, the fuller run: `node scripts/hq-gpu-check.mjs --url https://app.anum360os.com --login --length 5` and send the folder it names (it adds a building with a floor change).

With the remember box ticked, step 2 also pins the verdict's tier on this laptop; untick it before step 2 to keep the quality automatic.

## Real devices

Results from real hardware, anonymised as in the report (no names, accounts or addresses). Frame times in ms; frames per second are the mean over the run.

### 2 November 2026: integrated Intel UHD Graphics 620 (before the tuning)

Windows, Chrome 156, ANGLE Direct3D 11 (`Intel(R) UHD Graphics 620 (0x00005917)`), unmasked, not software, WebGL 2, 8 processor threads, 8 GB device memory, device pixel ratio 1.13, window 1707 by 735 CSS pixels, campus level (no floor change), by day (no glow drawn on any tier), 5 second runs. `KHR_parallel_shader_compile` and `EXT_disjoint_timer_query_webgl2` present. The automatic choice was Ultra. The report is kept as `apps/web/test/fixtures/gpu-report-2026-11-02-uhd620.json`.

| Tier | Frames | Median | 95th percentile | Worst | Mean | fps | Dropped | Draw calls | Triangles | Shadow map |
|---|---|---|---|---|---|---|---|---|---|---|
| Low | 55 | 16.7 | 516.5 | 3381.5 | 109.33 | 9.1 | 12 | 74 | 141,361 | off |
| Medium | 262 | 16.7 | 16.8 | 566.3 | 19.07 | 52.4 | 4 | 74 | 141,361 | 1024 |
| High | 238 | 16.7 | 16.8 | 616.2 | 21.00 | 47.6 | 3 | 75 | 141,373 | 1024 |
| Ultra | 201 | 16.7 | 66.7 | 516.1 | 24.86 | 40.2 | 14 | 74 | 141,361 | 2048 |

Verdict: reduced to High at 60 frames a second. Reading: High and Medium hold 60 frames a second for the typical frame; Ultra drops about 1 frame in 14. Low, the first tier measured, timed its switch stall (the warm-up bug above), so its figures describe the switch, not Low. What changed because of it: the automatic choice, the warm-up, the hitch rule of the governor (all above). Still to do: the re-run on this laptop after the update, a building run with a floor change, and a night run (the glow).

## Tests

- Unit (`test/hqGpu.test.ts`): the tier registry (ordered, unique, named in both languages, the pin parser), the ladder, frame statistics, the governor with simulated frame times (healthy, slow, hitches, slow climb back, hysteresis, no flip-flop, disabled, reset), verdicts, software detection, the report shape and anonymity, the telemetry hook.
- Browser (`e2e/hq-gpu-check.spec.ts`, software GL): the panel opens from Office tools and reports a software rasteriser honestly; the chip; a benchmark of every tier; the pin and "Back to automatic"; the saved report; a lost and restored context (banner does not block, scene returns); a context that never returns (list and plan, then "Try 3D again"); a simulated shader link failure (simple materials, scene stays); axe; English and Arabic (right to left); 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 with no horizontal overflow.
- Browser (`e2e/hq-gpu-script.spec.ts`): the owner script's options and a full run against the mocked office.
- Unit (`test/hqGpuTuning.test.ts`): the automatic start tier per renderer string (the owner's UHD 620 starts at High), frame streams rebuilt from the real report and checked against every figure in it, the governor on them (Ultra steps down, High and Medium never, no flip-flop), the windows and governor allocating nothing (or next to nothing) per frame, the shader variants per ladder, the warm-up rule, the benchmark compiling first and timing none of a scripted 3.4 s switch stall, the new report fields.
- Browser (`e2e/hq-gpu-check.spec.ts`, "shader warm-up"): after the warm-up, a benchmark that switches through every tier creates no new shader program (without the warm-up the same run creates 9), the cards show the warm-up, and software drawing is never remembered.

## Gaps

- One real device so far, integrated, before the tuning. The hitch rule (40 ms, 25 ms) is set from it; the median thresholds (26 ms, 19 ms, 12 s) and the verdict limits are still reasoned defaults. The start tiers for discrete, Apple M and phone GPUs are reasoned, not measured.
- GPU time is measured by the benchmark only where the browser offers the timer query (Chrome on Windows does; many browsers do not); the governor judges frame time.
- The memory figure is an estimate.
- Antialiasing is chosen when the context is created and is not part of the ladder.
- The simple-materials rung is one way within a session unless the governor climbs back (it can, slowly); a shader fault keeps it on.
- Phones and tablets are not covered by the script; use the in-app panel there.
