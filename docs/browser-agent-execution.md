# ANUM OS: Controlled browser agent execution

## Scope of this implementation

The `browser_agent.py` execution kernel performs public HTTPS research in an
ephemeral Playwright Chromium context. Requests include an explicit per-task host
allowlist. It supports navigation, selecting a single anchor link, reading page
text and taking screenshot evidence, with action and time limits. Each browser
action returns a step record. It does not share cookies between tasks.

**Do not enable an unrestricted browsing tool**. Even this read-only kernel
requires a container or VM worker with egress firewall rules, an unprivileged
account, no cloud instance metadata route, no secrets mounted, CPU/memory/time
limits, disabled downloads and a disposable filesystem. Playwright request
interception is not an adequate security perimeter for DNS rebinding,
WebSockets, browser exploits or other network protocols.

## Deliberately unavailable until separately approved and audited

Form filling, login/session reuse, submitting pages, uploading files,
downloading arbitrary content, purchases, message sending and arbitrary
JavaScript evaluation are disabled. Existing ANUM approval policy, task
permissions, cost controls and emergency stop must apply before an agent may
request a browser action.

## How this integrates with existing ANUM architecture

- SearXNG `web_search` discovers candidate websites.
- An agent task can propose a structured browser request.
- ToolPolicy and governance decide whether to allow or ask approval.
- A dedicated browser worker executes the bounded request, then writes sanitized
  step evidence to the task audit and stores screenshots in tenant-scoped object
  storage. Untrusted webpage content must be labelled before model input.
- Temporal owns durable runs and retries. The agent task ID is the correlation
  key across browser steps, notifications and event stream.
- Existing automation workflows (`task.create`) run tasks on cron schedules,
  manual execution or event/webhook triggers.
- The UI should subscribe to existing events, render stage progress, allow
  pause/cancel/resume and surface final output in the original conversation.

## Deployment gate

This commit adds the execution kernel and deterministic security tests, **not**
a live agent tool, browser-worker deployment or chat-controlled scheduled
execution. Production needs all of the following, verified in CI and staging:

1. Package Playwright and pinned Chromium in a restricted worker image.
2. Register a browser tool through ToolRegistry and ToolPolicy with a strict
   input schema and per-tenant rate limit.
3. Add authenticated worker transport, workspace-scoped screenshot storage,
   step events and runtime cancellation.
4. Connect chat task creation to existing automation schedule APIs, avoiding
   another scheduler implementation.
5. E2E tests for site navigation, hostile redirects, blocked private networks,
   browser crashes, cancellation, approval, background recovery and progress.
6. Verify a real run on deployment and collect evidence before enabling it.

Existing workflows, schedules, Temporal agent tasks, NATS event streaming and
emergency stop are documented in `docs/automation.md`, `docs/agent-controls.md`
and `docs/agent-runtime.md`.
