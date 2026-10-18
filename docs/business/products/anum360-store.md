# Anum360 Store: sales kit

Slug `anum360-store` (aliases `ANUM 360 Store`, `ANUM 360 Gallery`). Catalog entry: [`scripts/business/products.json`](../../../scripts/business/products.json), loaded by `scripts/business/seed-products.py` ([Product sales kits](index.md#loading-the-catalog)).

**Most facts are missing, starting with what is being sold.** There is no local Anum360 Store folder under the owner's Documents, so only this repository's docs were used. Every field without a source is `-` and listed under [Owner questions](#owner-questions).

What the sources say:
- It is the owner's own online store: FastAPI with MongoDB and an admin API under `/api/admin`, connected to ANUM Commerce as the default own-store mode `anum360` through a dedicated staff account ([Commerce](../../commerce.md#the-owners-own-store)).
- ANUM's setup plan runs it as the "ANUM 360 Gallery" business: agents validate orders (variants, approved prices, addresses, cash on delivery amounts), draft cash on delivery confirmations, build fulfilment checklists and handle bilingual customer care, with prices in AED and Asia/Dubai time ([Setup plan](../../setup-plan.md), blueprint `shahzeb_business_group.json`).
- CLAUDE.md lists it as one of the apps to move onto the Oracle VM behind ANUM's Caddy, after the portfolio, Tyre Pulse and VoiceQuote ([Deploy on Oracle](../../deploy-oracle.md)).
- The domain `anum360.ae` appears in the owner's mail-domain steps ([Pending work](../../pending-work.md)); whether it is the store's address is not stated.
- Roadmap section 1 lists the code at `github.com/ws123na-afk/Anum360_Store` (not read for this kit).

So there are two different things that could be "sold", and the sources do not say which:
1. **The goods the store sells** to consumers (B2C retail). Then the Business sales desk is the wrong tool for it: retail sales run through Commerce, WhatsApp care and marketing, not leads and proposals.
2. **The store software** sold to other merchants (B2B). Then it needs its own feature list, ICP and price.

The seed loads the product with no offer until the owner answers.

## What it does (verified features)

| Feature | Source |
|---|---|
| Online store with an admin API | [Commerce](../../commerce.md) |
| Orders with variants and cash on delivery, priced in AED | Setup plan blueprint |
| Order and stock sync into ANUM Commerce | [Commerce](../../commerce.md) |
| Catalogue, storefront, payments, delivery partners | `-` |

## Ideal customer profile

| Field | Value |
|---|---|
| Industry | `-` |
| Size | `-` |
| Region | UAE (AED pricing, Asia/Dubai time in the blueprint) |
| Buyer roles | `-` |
| Trigger events | `-` |
| Disqualifiers | `-` |

## Value proposition

`-` until the owner says whether the store's goods or its software are the product.

| Pain | Outcome |
|---|---|
| `-` | `-` |
| `-` | `-` |
| `-` | `-` |

## Packaging and pricing

| Package | Contents | Price |
|---|---|---|
| `-` | `-` | `-` |

**Recommendation (owner to confirm):** if the software is for sale, start with a fixed-scope setup package quoted with the roadmap's pricing formula, plus a monthly care plan quoted separately (roadmap 2.2 "Support & Care"). If only the goods are for sale, keep the product in the catalog as a record and sell through Commerce and Marketing instead of the sales desk.

## Short pitch

`-`.

## Email opener template

`-`. Not written: an opener needs a confirmed product and buyer.

## Objection handling

`-`.

## Proof points (verified only)

- The store is connected to ANUM Commerce and its orders flow into ANUM's order validation and care automations (setup plan). Order volumes, revenue and customers: `-`.

## Owner questions

1. What is for sale: the goods in the store, the store software, or both?
2. What does the store sell (categories), and to whom?
3. If the software is for sale: features, who it is for, price.
4. Is `anum360.ae` the store's address?
5. Maturity: is the store live with customers? (The seed sets `idea` only because nothing is verified.)
6. Any figures (orders, customers) that may be cited?
