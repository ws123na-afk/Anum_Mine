# UI Research: Depth and Voice Presence

This is a comparison of how leading AI products present voice assistants and agent dashboards (October 2026), and how ANUM's interface is designed to do better. Points marked *observation* are design judgement rather than sourced fact.

## Voice Assistants Today

| Product | What you see | Weakness |
|---|---|---|
| ChatGPT Voice | Fluid blue orb. Since Nov 2025, voice runs inside the chat with a live transcript. | The full-screen orb hid context, and OpenAI made it optional. |
| Gemini Live | Misty "neural" glow, a full-screen edge glow, a waveform overlay. | Redesigned several times in one year. The glow carries brand, not information *(observation)*. |
| Siri (iOS 18 → 26) | Rainbow edge light. Liquid Glass refracting material. | Liquid Glass contrast drops to about 1.5:1 against a 4.5:1 minimum, and Apple was still adjusting it in 26.x. |
| Copilot (Mico) | An emoting blob character. | Compared to Clippy; too playful for operations work. |
| Perplexity | A touchable sphere of dots. | Close to a copy of ChatGPT; shows little state. |
| ElevenLabs UI | An open-source Three.js orb with idle/listening/thinking/talking states. | Now a generic look many teams ship. |

All of them show **mood**: idle, listening, thinking, speaking. None shows **what the assistant is allowed to do or is waiting on**.

## Dashboards

- **Calm through hierarchy:** Linear's refresh dimmed the chrome, softened borders and stepped lightness evenly. Depth comes from light and hierarchy, not decoration.
- **Agent consoles:** these need visible intentions, tool calls, policy decisions and human approvals as a designed surface. Otherwise review becomes a rubber stamp.
- **Overdone patterns:** glass behind data, shine everywhere, stacked blur layers (each one multiplies GPU cost), and repetitive bento grids.

## What ANUM Does

| Their weakness | ANUM's answer |
|---|---|
| Orb shows mood, not authority | The orb's rim turns **amber when an approval is waiting**, and a chip under it ("1 approval waiting for you") opens Approvals. |
| Liquid Glass hurts contrast | Glass only on the frame (sidebar, voice console). Data sits on **solid, lit surfaces**. Supports `prefers-reduced-transparency` and `prefers-contrast`. |
| Ambient motion everywhere | One living element, the orb. Cards move only when you interact (lift and a ≤2° tilt). Full `prefers-reduced-motion` support. |
| Heavy 3D bundles and battery drain | The orb uses **OGL (about 14 KB gzipped)** instead of three.js (about 119 KB). It loads only on the Voice page, caps DPR at 1.5, renders at 30 fps when idle, and pauses when hidden or off screen. Without WebGL it falls back to a CSS orb. |
| Mascot playfulness | An abstract aurora orb: alive but professional. |
| Voice locked to one language | English and Arabic, with RTL-safe layout. |
| Voice can act on its own | Approvals, deletions and payments are never done by voice. Tasks need a tap. All of this is enforced on the server. |

## Design Tokens (dark "Midnight + Aurora")

- **Background:** `#0B1020`, with faint sky, violet and rose washes.
- **Elevation:** surfaces `#111829` → `#172036` → `#1E2944` → `#26335A`, getting lighter as they rise.
  - Each surface has a 1px inner top highlight (the "lit edge").
  - Shadows pair a tight contact shadow with a wide ambient one.
- **Text:** `#EEF1F8`, muted `#A3ABC2`, faint `#737C96`.
- **Accents:** sky `#7DD3FC`, violet `#A78BFA`, rose `#F9A8D4`. Primary buttons use a violet gradient with a soft glow.
- **Status:** ok `#86EFAC`, waiting `#FCD38D` (orb rim `#FFB547`), stop `#FDA4AF`.
- **Shape and motion:** radius 12–16 px on surfaces, pills for chips. Easing `cubic-bezier(.2, .8, .2, 1)`, 160–240 ms.

A light-mode counterpart (depth from shadow instead of lightness, darker accents for 4.5:1 on white) is the next step.

## Desktop Notes

- **Windows (WebView2):** behaves like Chrome.
- **macOS (WKWebView):** supports WebGL2 and `backdrop-filter`.
- **Linux (WebKitGTK) with NVIDIA/Wayland:** may need `WEBKIT_DISABLE_DMABUF_RENDERER=1`. With compositing disabled, WebGL and blur are slow and the orb falls back.

## Sources

**ChatGPT Voice**
- https://www.bgr.com/2036902/openai-fixes-chatgpt-voice-separate-mode/

**Gemini Live**
- https://9to5google.com/2025/11/24/gemini-overlay-fullscreen/

**Siri and Liquid Glass**
- https://9to5mac.com/2025/11/12/ios-26-6-liquid-glass-tweaks/
- https://infinum.com/blog/apples-ios-26-liquid-glass-sleek-shiny-and-questionably-accessible/

**Copilot (Mico)**
- https://www.fastcompany.com/91427839/microsoft-mico-copilot-ai-assistant

**ElevenLabs Orb**
- https://21st.dev/community/components/ElevenLabs/orb

**Linear refresh**
- https://linear.app/now/how-we-redesigned-the-linear-ui

**Agent console patterns**
- https://www.agenticwire.news/article/agent-ux-design-patterns

**3D engine size**
- OGL: https://github.com/oframe/ogl
- three.js: https://minime.stephan-brumme.com/threejs/135

**CSS**
- `@property`: https://web.dev/blog/at-property-baseline

**Tauri on Linux**
- https://v2.tauri.app/develop/debug/linux-graphics/
