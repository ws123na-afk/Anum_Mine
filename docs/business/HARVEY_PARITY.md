# Harvey parity: prospecting and outreach in ANUM

Every capability of the Harvey sales agent (https://github.com/ethanplusai/harvey, MIT, "Copyright (c) 2026 Ethan Rogers", read at commit `e114e95471b1`) and its native ANUM counterpart, per the treatments in the [reuse decision register](REUSE_DECISION_REGISTER.md#harvey-extract-patterns). No Harvey code was copied or run: its modules were read as reference, and every pattern below is ANUM code, tenant scoped, mediated by the agent runtime's `ToolPolicy` and metered through the budgeted model gateway (never the `claude` command line). The feature is described in [Business](../business.md#prospecting-pipeline).

Part of Order 001: [gap register](ANUM_BUSINESS_GAP_REGISTER.md), [P1 acceptance tests](P1_ACCEPTANCE_TESTS.md).

## Status

| Status | Before this change | After this change |
|---|---|---|
| Working | 2 (scoring, drafting) | 13 |
| Partial | 4 (discover, research loop, dashboard, cost and budget) | 0 |
| Missing | 7 (profile, tech detection, signals, outbox, follow-ups, reply handling, bounce kill switch) | 0 |
| Deliberately excluded | 5 | 5 |

## Capability table

| Harvey capability (file) | ANUM counterpart (files) | Before | After | Notes |
|---|---|---|---|---|
| Discover (`collectors/discover.py`) | Discovery runs: `business/pipeline.py` (`_Runner.discover`), `web_search` tool | Partial: research searched for one known lead only | Working | One product per run; queries from the person's terms and places, or the product's target profile; junk filter (directories, social, media, institutions) and product disqualifiers; dedup by domain, near matches by name offered and never merged; each new company becomes an account, a `discovered` lead and an evidence row. No paid providers. |
| Profile (`collectors/profile.py`) | Profile runs: `business/pipeline.py` (`_Runner.profile`), `web.fetch_page` (`business/fetch.py`) | Missing (no page fetch) | Working | Reads the company's own https pages: home, robots.txt, sitemap, contact and careers pages, within `pages_per_lead`. The fetcher reuses the SSRF guard of `model_egress` (`check_endpoint`, `PinnedEgressTransport`, the guard `commerce/http.py` uses), always on; obeys robots.txt (RFC 9309) and crawl delay; one request per host per second; size, time and type limits; no cookies, credentials or query strings; refuses logins and captchas. |
| Tech detection (`integrations/tech_detect.py`) | `business/signals.py` (`detect_tech`, `TECH_FINGERPRINTS`) | Missing | Working | Distinctive script hosts and generator tags, Gulf platforms (Salla, Zid) added. |
| Signals catalog (`signals.py`) | `business/signals.py` (`CATALOG`), `PUT /business/pipeline/signals` | Missing | Working | Owners confirm what is collected; nothing unconfirmed is stored. Each observation keeps URL, excerpt and the day it was read (`business_observations`). People, e-mail guessing, review counts and rankings are not in the catalog. |
| Scoring (`agents/scout.py`) | Lead score (0 to 30, roadmap 3.2) in `business/service.py`; suggestions `signals.suggest_scores`; product fit `signals.product_fit` | Working (manual score) | Working | Deterministic suggestions for reachable channel, problem evidence and project scale with the rule and the evidence they cite; a person applies them. Urgency and buyer authority stay conversation only. Product fit scores a lead against every product's target profile; the best fit is shown. |
| Research loop (`pipeline.py`, `brain.py`) | `business/pipeline.py`, `business/research.py` | Partial: no estimate, no page fetch | Working | Estimate (searches, page reads, model calls, cost, caps and budget left), confirm, run within the estimate, progress steps, stop, pause, resume after a restart. Facts (evidence, observations) are kept apart from inferences; page text reaches a model only inside labelled untrusted blocks. |
| Drafting (`agents/writer.py`) | `business/assist.py` drafts, `POST /business/leads/{id}/drafts` | Working | Working | Drafts now use the lead's product value proposition (target profile, English and Arabic); follow-up drafts from `outreach.follow_up_draft`. |
| Deterministic gate (`gate.py`) | `business/outreach.py` (`gate`, `content_issues`) | Partial (draft checks only) | Working | Recipient matches the contact, consent basis, opt-out list, do not contact, bounced address, subject and length limits, merge tags, banned phrases, one link, plain text, opt-out line. Runs before the approval and again at send time. |
| Outbox ladder (`agents/sender.py`) | `business/outreach.py`, `business_outbox`, tool `business.send_outreach` | Missing | Working | Every message asks for an approval bound to its payload hash (high risk, always asks, autonomy cannot lower it). The send claims the row in one short locked transaction (`sending`, quota slot reserved), so concurrent or retried runs send it once; interrupted sends become `needs_review`, never resent. Sending is off by default; with it on, the tool re-checks everything and hands the message to the connected e-mail or WhatsApp tool, whose policy and the suppression check still apply. Per-channel daily quotas. A failed send is recorded as failed. |
| Follow-ups (`agents/sender.py` sequences) | `outreach.queue_draft(follow_up_of=...)`, `POST /outreach/items/{id}/follow-up` | Missing | Working | Each follow-up is its own draft and approval, scheduled after `follow_up_min_days`, at most `follow_up_max` per lead. |
| Reply handling, stop-on-reply (`agents/handler.py`) | `outreach.record_reply`, `note_inbound`, hooks in `email_inbox.py` and `whatsapp.py` | Missing | Working | A reply cancels the lead's open rows. Opt-out wording is honoured in code: the contact goes on the tenant's opt-out list and is marked do not contact. Legal wording is flagged for a person. Reply text is classified and never stored. |
| Bounce kill switch (`agents/handler.py`) | `outreach.check_kill_switch`, `business_outreach_settings.paused` | Missing | Working | Above `max_bounce_rate_pct` (after 10 sends in 30 days) outreach pauses itself; the pause also stops research runs and drafting. Only owners resume. |
| Dashboard and metrics (`dashboard.py`, `agents/analyst.py`) | Web Business tabs Prospecting and Outreach, Flutter "Prospecting and outreach", `GET /business/outreach` | Partial (KPI summary) | Working | Sent per channel, replies, opt-outs and bounces each with their denominator, opt-out alarm above 2%, waiting messages, quotas used. |
| Cost estimate and budget (`collectors/discover.py`, `brain.py`) | `pipeline.estimate`, `model_budget.py` | Partial (daily caps) | Working | The estimate shows model cost and the monthly budget left; the run stops when the budgeted gateway refuses a call. |

## Deliberately excluded

| Harvey piece | Why |
|---|---|
| LinkedIn automation (`integrations/linkedin.py`) and any browser automation | Roadmap section 6 and the register rule it out. LinkedIn drafts stay drafts a person sends. |
| Paid data providers (DataForSEO, Serper, Hunter, Precleared) | No paid providers in P1 (register). |
| Pages behind logins or captchas | Roadmap section 6; the fetcher refuses them. |
| E-mail finder and personal phone enrichment (`integrations/email_finder.py`), people from team pages | No hidden enrichment of personal contacts; only addresses the business published (role addresses) or that were given to us. |
| Autonomous outbound (Harvey's autopilot) | Human-approved sends only until roadmap P3; `always_ask` cannot be lowered by autonomy. |

## Per-product targeting

Each product has a target customer profile (`business_target_profiles`, migration `0076_business_pipeline`), edited by owners in Prospecting on the web. Fields a seeding script fills, per product id:

| Field | Type | Meaning |
|---|---|---|
| `industries` | list of text (20) | Industries the product serves; used for discovery queries and fit. |
| `company_sizes` | list of `micro`, `small`, `medium`, `large`, `enterprise` | Target company sizes. |
| `regions` | list of text (20) | Defaults to the six GCC countries; used for queries and fit (city names and GCC domains count). |
| `roles` | list of text (20) | Buyer roles to look for in a conversation. |
| `keywords` | list of text (30) | Words that raise fit when the company's own pages use them. |
| `disqualifiers` | list of text (30) | Words that drop a discovered company and mark its fit disqualified. |
| `trigger_events` | list of text (20) | Events that make a buyer ready (for conversations; not used for fit). |
| `company_size_note` | text (300) | The size in the owner's own words, kept next to the mapped bands. |
| `value_proposition`, `value_proposition_ar` | text (1000) | The pitch drafts use, per language. |

Seed through the API: `PUT /api/v1/business/pipeline/products/{product_id}/target` (owner).

## Tests

- `services/api/tests/test_business_pipeline.py`: every capability above, the concurrency of sends and quota reservations, and the synthetic journey.
- `services/api/tests/test_postgres_business_pipeline.py` (marker `database`): forced RLS on all six tables, cross-tenant and cross-workspace reads and writes refused, scoped foreign keys, the unique indexes.
- `services/api/tests/test_seed_products_script.py`: the seeding script fills the target profiles.
