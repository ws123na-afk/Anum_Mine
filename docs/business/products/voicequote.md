# VoiceQuote: sales kit

Slug `voicequote` (aliases `Voice Quote`, `EasyVoiceQuote`). Catalog entry: [`scripts/business/products.json`](../../../scripts/business/products.json), loaded by `scripts/business/seed-products.py` ([Product sales kits](index.md#loading-the-catalog)).

**Most facts are missing.** The only sources available were this repository's docs; there is no local VoiceQuote folder under the owner's Documents. Every field without a source is `-` and listed under [Owner questions](#owner-questions). Nothing here is invented.

What the sources say:
- Roadmap section 2.1: target segment "Service businesses" with the pain "Slow quotation turnaround"; starter solution "VoiceQuote tailored pilot"; buyer "Owner/operations manager"; sales motion "Problem interview and short demo".
- Roadmap section 2.2: the VoiceQuote Pilot package is "Configured catalog subset, quote approval/export, training", priced "Quote after scoping", with catalog, accuracy and external integrations explicitly bounded.
- Roadmap section 1 lists the code at `github.com/ws123na-afk/VoiceQuote` (not read for this kit).
- Domain `easyvoicequote.com` ([Email](../../email.md), [Pending work](../../pending-work.md)): its SPF record is `+all` and MX points to Amazon inbound; both are open owner DNS steps. Fix SPF before sending any outreach from that domain.
- ANUM's setup plan creates a VoiceQuote workspace ([Setup plan](../../setup-plan.md)) and the home page can pin a link to it ([Home](../../home.md)).

## What it does (verified features)

| Feature | Status |
|---|---|
| Quotation tool for service businesses | From the roadmap only |
| Configured service catalog | Pilot deliverable (roadmap 2.2) |
| Quote approval and export | Pilot deliverable (roadmap 2.2) |
| Voice input, languages, platforms, integrations | `-` |

## Ideal customer profile

| Field | Value |
|---|---|
| Industry | Service businesses that send quotations (roadmap 2.1); which trades: `-` |
| Size | `-` |
| Region | GCC first (UAE, Saudi Arabia), as for every ANUM product; confirm |
| Buyer roles | Owner, operations manager (roadmap 2.1) |
| Trigger events | Jobs lost because quotes go out late (roadmap 2.1 pain) |
| Disqualifiers | `-` |

## Value proposition

Faster quotes for service businesses, so fewer jobs are lost to a competitor who answered first. Stated as a hypothesis: the roadmap says to validate the pain in problem interviews first.

| Pain | Outcome |
|---|---|
| Quotes take days because prices and line items are rebuilt each time | `-` (needs verified features; the pilot's configured catalog is the likely answer) |
| Quotes go out without a check and with mistakes | Quote approval before export (pilot deliverable) |
| `-` | `-` |

## Packaging and pricing

| Package | Contents | Price |
|---|---|---|
| VoiceQuote pilot | Configured catalog subset, quote approval and export, training (roadmap 2.2) | `-` |
| Subscription after the pilot | `-` | `-` |

**Recommendation (owner to confirm):** quote after scoping, as the roadmap says, with the pricing formula (labor hours x internal hourly cost + verified external costs + contingency + profit). The seed loads the pilot as a draft `quote` offer.

## Short pitch

`-` until the feature list is confirmed. Draft for the owner to correct: "VoiceQuote helps service businesses send accurate quotes the same day: your services and prices are set up once, each quote is checked before it goes out, and your team is trained on it in the pilot."

## Email opener template

> Subject: Quotes at {{company}}
>
> Hello {{first_name}}, I saw that {{company}} {{verified_observation}}. Service businesses we speak with often lose jobs because quotes take days to prepare. I am testing VoiceQuote, which sets up your services and prices once so a checked quote can go out the same day. Could I ask you three questions about how you quote today? It takes 15 minutes, and if it does not fit, reply "no" and I will not write again.

This opener asks for a problem interview, not a sale, as the roadmap's sales motion says. Do not send it from `easyvoicequote.com` until SPF is fixed.

## Objection handling

| Objection | Answer |
|---|---|
| "We quote in Excel and it works." | Ask how long a quote takes and how many are lost to slower replies; if the answer is "fast and none", disqualify. |
| "Will it get prices wrong?" | Prices come from the catalog configured with you, and every quote is approved before export. Accuracy limits are written into the pilot scope. |
| Anything about features, integrations or price | `-`: answer only after the owner confirms the facts. |

## Proof points (verified only)

`-`. No customers, pilots or metrics are recorded.

## Owner questions

1. What does VoiceQuote do today: the verified feature list, how voice is used, languages, web or mobile?
2. Who is it for: which service trades, company size and regions?
3. Maturity: idea, prototype, pilot or live? (The seed sets `idea` only because nothing is verified.)
4. Pilot price and any subscription price.
5. Any users, pilots or results that may be cited?
6. Integrations (accounting, CRM, WhatsApp, email)?
7. Will the `easyvoicequote.com` SPF and MX be fixed before outreach?
