# Browser research: the read-only `web.browse_page` tool

`web.browse_page` lets an agent open **one public https page** in a real, throwaway Chromium and read its text and links, for pages that only show their content after their scripts run. It only reads: no logins, no forms, no downloads, no cookies kept, nothing typed or clicked. It is an ordinary registered tool, so every call takes the runtime's normal path (planner, `ToolPolicy`, governance rules, emergency stop, autonomy and approvals, run steps, events, audit). It replaces the draft in PR #239, which was never called by anything and checked DNS separately from the browser's own lookup (open to DNS rebinding).

The same browser service, run in action mode, also serves the [Action browser](action-browser.md) (`web.act`), which signs in, fills forms and clicks through sites and stops for the owner's approval before anything binding; `web.browse_page` is unchanged by it.

For pages that need no scripts, `web.fetch_page` ([Business](business.md#page-fetch)) is lighter and stays the first choice of the prospecting pipeline; `web_search` ([Tools and integrations](tools-and-integrations.md#web-search)) finds candidate pages.

Code: `services/api/anum_api/browser_research/` (`guard.py`, `kernel.py`, `service.py`, `tool.py`). Tests: `services/api/tests/test_browser_research.py` (no browser needed) and `services/api/tests/test_browser_research_chromium.py` (a real Chromium, marker `browser`).

## How it is used

- **From chat or a task.** The user names the page in their own words, for example "Open https://example.com/pricing and read the page" or "افتح https://example.com/ar واقرأ الصفحة". The skill `anum.browser-research` is selected by words such as open, browse, visit, read, page, site, link (Arabic: افتح، تصفح، زر، اقرأ، صفحة، موقع، رابط), and the planner calls the tool only when the prompt itself contains an https address; the address is taken from the prompt, never from a retrieved document, a web page or the model's reply (threat model G5). Without an address the request goes to web search as before.
- **In chat** the answer is a "Web results" card: the page that was read (badge "Page read") and up to nine of its https links (badge "Link on the page"), each opening in a new tab, with a short model-written answer above it. The page text reaches the model only as a labelled, untrusted block (`tool_output_prompt_block`, threat model G3). While it runs the card says "Reading the page…" (Arabic "أقرأ الصفحة…"); a refusal or failure shows the reason the tool gave.
- **Agents.** The Research Agent template has both `web_search` and `web.browse_page`; any agent can be given the tool by name.

## Workspace switch

The tool is **off in every workspace until an owner turns it on**: Integrations, Platform services, Browser research, "Use Browser research in this workspace", Save (`PUT /api/v1/integrations/browser-research/configuration {"enabled": true}`, permission `organization:manage`; members get 403). Until then a call is refused with "Browser research is off for this workspace. A workspace owner turns it on under Integrations, Browser research." and nothing is contacted. The health entry says "Off for this workspace until an owner turns it on." (`IntegrationDefinition.default_enabled=False`, stored in the existing RLS-protected `integration_configurations` table: no migration). The details panel shows whether "Agent tool web.browse_page" is available, degraded or not available here.

## Not available on this server

The browser runs only where the operator set up the browser service. Until then:

- the tool is still registered (so a request for it is never quietly answered by another tool) and every call answers **"Not available on this server: the browser service is not set up (the operator sets ANUM_BROWSER_RESEARCH_URL and ANUM_BROWSER_RESEARCH_TOKEN; docs/browser-research.md)."**;
- the `browser-research` integration is `disabled` and not switchable, with the same text.

If the service runs but its image has no Chromium, `/healthz` answers 503 and every call answers "Not available on this server: Chromium is not installed in this image." The plain API image never contains a browser (see below), so this is the state of the Oracle VM today until the owner turns the profile on.

## The browser service

The browser never runs in the API process or on its event loop. The tool sends `{"url": ...}` to the browser service (`python -m anum_api.browser_research.service`, port 8090) with `Authorization: Bearer <ANUM_BROWSER_RESEARCH_TOKEN>` and awaits the answer over the internal network; with `ANUM_RUNTIME_BACKEND=temporal` the call is made from the worker, otherwise from the API's inline run, in both cases only a socket is awaited.

Why a separate container: the page's own JavaScript runs in Chromium, and a browser exploit must not land next to the database login, `ANUM_SECRETS_KEY` or provider keys. The service reads no ANUM settings; its only secret is the token (constant-time compare, at least 32 characters, the service refuses to start otherwise). It reads one page at a time (`ANUM_BROWSER_CONCURRENCY`, default 1); a call that waits more than 20 seconds for the browser is answered 429 ("The browser is busy with another page; try again shortly."). Its log lines carry the outcome and counts only, never the address or content. A browser crash is an outcome ("The browser stopped while reading the page."), never a stack trace.

On the Oracle VM it is the `browser` compose profile:

- image `anum/browser:local`, the API image built with `ANUM_BROWSER=1`;
- networks `browser` (internal, shared only with the API) and `browser-egress` (its own route to the internet, shared with no other container), so it reaches neither PostgreSQL, Valkey, Keycloak nor the other services;
- read-only root file system, `/tmp` as a 256 MB tmpfs, all capabilities dropped, `no-new-privileges`, 256 processes, 1 CPU, 1 GB of memory. Chromium runs without its own sandbox (`--no-sandbox`: the container has no user namespaces); the container is the boundary.

## The browser image

`docker build --build-arg ANUM_BROWSER=1 services/api` installs the `browser` extra (`playwright==1.64.1`) and, with `playwright install --with-deps --only-shell chromium`, Chromium's headless shell and the libraries and fonts it needs, under `/opt/ms-playwright`. **Size cost: about 650 MB more than the API image** (an estimate from the parts, not a measured build: the headless shell is 323 MB unpacked, the Playwright package 134 MB, and the system libraries and fonts the rest). The default build (`ANUM_BROWSER=0`) is unchanged and carries no browser.

## How a page is read

One call (`kernel.browse`):

1. Checks the address (`guard.check_page_url`): https only (`http`, `file`, `data`, `javascript`, `chrome`, `about`, `blob`, `ftp`, `ws` and every other scheme are refused), the default port, no user name or password, no query string (a research page never needs one, and it is the easy channel for leaking data in an address), at most 2048 characters, the fragment dropped.
2. Reads robots.txt through the same guard and obeys it for `ANUM-Research` and `*` (RFC 9309: 4xx means no rules; 5xx or no answer means nothing on that host is read; at most two redirects, each checked).
3. Starts a fresh headless Chromium with a fresh profile in a temporary directory: no cookies, storage, downloads (`accept_downloads=False`), service workers (blocked), permissions, extensions or saved state, and nothing is kept after the call. Chromium gets no ANUM environment and no proxy variables.
4. Answers **every** request Chromium makes through the gate (below). Chromium itself cannot reach anything: every host name is mapped to "not found" (`--host-resolver-rules=MAP * ~NOTFOUND`), its proxy is an address nobody listens on, QUIC, DNS prefetch, pings, background networking, component updates and non-proxied WebRTC are off, and web sockets are closed.
5. Waits for the document and up to 3 seconds of network quiet, then locks navigation.
6. Refuses pages behind a login or a captcha (401, 403, 407, a password field or a known challenge page): nothing of them is returned.
7. Returns the title (300 characters), the visible text (20,000 characters, `truncated` when cut) and up to 40 https links of the page (no `http`, `javascript:` or other links, no credentials, duplicates removed).

The whole call has one deadline of 30 seconds (each request 10 seconds); the tool waits for the service up to `ANUM_BROWSER_RESEARCH_TIMEOUT_SECONDS` (default 45).

## Network guard

The gate (`kernel.Gate`) decides each request; the fetcher (`guard.PinnedFetcher`) makes it:

- **GET only.** Form posts, `fetch` with another method, beacons and every other write are refused. A refused page navigation is answered "204 No Content", so the page on screen stays in place.
- **Types.** Only the page document, scripts, style sheets and `fetch`/XHR reads. Images, media, fonts, frames (iframes), pings, manifests and the rest are refused.
- **Navigation.** Only the navigation the kernel makes: a script, a meta refresh, a form or a link cannot move the page anywhere else.
- **Addresses.** Every request URL is checked again (https, default port, no credentials, IP literals in standard form) with `model_egress.check_endpoint`, always enforced, in every environment.
- **DNS pinning, no rebinding.** Every request resolves the host **once**, refuses it if **any** answer is loopback, private (RFC 1918), shared (CGNAT), unique-local (`fc00::/7`, including `fd00::/8`), link-local, a cloud metadata address (`169.254.169.254`, `fd00:ec2::254`, `192.0.0.192`, `100.100.100.200`), multicast, unspecified, reserved or documentation space, IPv4 embedded in IPv6 included, and then connects to **that checked address** while keeping the original `Host` header and TLS server name (`model_egress.PinnedEgressTransport`). A second, different DNS answer is never used for the connection it was checked for, and each request (robots.txt, the page, every script) is checked on its own.
- **Redirects.** The fetcher never follows one. A redirect of the page is checked and then visited as a new navigation (at most three); a redirect of a script or a fetch is followed by the gate, each hop checked the same way. A redirect to a private or metadata address is refused before anything is requested from it.
- **No cookies.** Only `Content-Type` and `Content-Language` are handed to Chromium: never `Set-Cookie`, never a redirect header.
- **Limits.** At most 60 requests, 1.5 MB per response, 6 MB per page; identity encoding only (a compressed answer is refused, so no decompression bombs); anything that is not a web page (a PDF, an image, `Content-Disposition: attachment`) is refused as "not a web page".

## Limits, cost and audit

- **Per-workspace cap**: `ANUM_BROWSER_RESEARCH_PER_HOUR` pages per workspace per hour (default 30), counted in each API or worker process. Over it: "This workspace has read 30 pages in the last hour, the most allowed; try again later."
- **Risk and approval**: low risk, read only, idempotent, roles owner and member (viewers are blocked), like `web.fetch_page`. No approval unless a workspace or organization approval rule names `web.browse_page`; a policy pack can deny it; the emergency stop refuses it.
- **Audit**: each call writes a `tool.call` audit record in the caller's tenant and workspace with the address, the final address, the hosts, the status code, the request, refused-request and byte counts, the refusal reasons by count and the outcome code (`succeeded`, `workspace_disabled`, `unavailable`, `refused_address`, `refused_scheme`, `refused_url`, `robots_disallowed`, `robots_unavailable`, `login_required`, `challenge`, `not_text`, `hourly_cap`, `busy`, `timeout` and the rest). **Never** the page text, title or links.

## Setting it up

On the Oracle VM (owner, about 15 minutes and about 650 MB of disk; the VM has room):

1. `cd ~/anum && git pull` (or run `infra/oracle/update.sh`, which adds `ANUM_BROWSER_TOKEN` to `infra/oracle/.env` with a fresh value).
2. In `infra/oracle/.env` add `browser` to `COMPOSE_PROFILES` (comma separated) and set `ANUM_BROWSER_RESEARCH_URL=http://browser:8090`.
3. `infra/oracle/update.sh` (builds `anum/browser:local` and starts the `browser` service).
4. In the web app: Integrations, Platform services, Browser research, Test connection (it should say connected), then turn on "Use Browser research in this workspace" in each workspace that should use it.
5. Recommended: drop traffic from the browser network to the cloud metadata address on the host, so even a browser exploit cannot reach it: `sudo iptables -I DOCKER-USER -s 172.30.70.0/24 -d 169.254.0.0/16 -j DROP` (and add the line to `/etc/iptables/rules.v4` before `COMMIT` in the `*filter` table).

Elsewhere: run the browser image with the command `python -m anum_api.browser_research.service`, `ANUM_BROWSER_SERVICE_TOKEN` and no other ANUM settings, on a network that only the API (or worker) can reach and that has outbound internet, and give the API `ANUM_BROWSER_RESEARCH_URL` and `ANUM_BROWSER_RESEARCH_TOKEN` (on Kubernetes through `extraEnv`; the Helm chart has no browser Deployment yet). Render has no browser service, so the tool says "Not available on this server" there. Locally: `pip install -e "services/api[browser]"`, `python -m playwright install --only-shell chromium`, then `ANUM_BROWSER_SERVICE_TOKEN=<32+ characters> python -m anum_api.browser_research.service` and point the API at `http://127.0.0.1:8090`.

## Not done yet

- No screenshots and no multi-step browsing (clicking through several pages in one call): one page per call. Screenshots would need tenant-scoped object storage and their own retention rules.
- The hourly cap is per process, not shared through Valkey: with several API replicas or workers a workspace can read that many pages per process.
- The Helm chart has no browser Deployment; Kubernetes operators run the image themselves.
- The size of the browser image is an estimate; the first real build on the VM gives the number (`docker image ls anum/browser`).
