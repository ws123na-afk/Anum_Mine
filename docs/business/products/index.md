# Product sales kits

The owner's own products, written up so the [Business sales desk](../../business.md) can sell them: what each does (verified features only), the ideal customer, the value proposition, packaging, a pitch, an email opener, objection handling and proof points. A field without a verified source is `-` and becomes an owner question; nothing is invented, and no price is set until the owner confirms it.

| Product | Slug | Kit | Facts available |
|---|---|---|---|
| ANUM OS | `anum-os` | [ANUM OS](anum-os.md) | Full (this repository) |
| Tyre Pulse | `tyre-pulse` | [Tyre Pulse](tyre-pulse.md) | Full (the Tyre Pulse repository, read only) |
| VoiceQuote | `voicequote` | [VoiceQuote](voicequote.md) | Roadmap only; most fields `-` |
| Anum360 Store | `anum360-store` | [Anum360 Store](anum360-store.md) | Commerce and setup plan only; what is for sale is unknown |

Prices in every kit are `-` with a recommendation flagged "owner to confirm". Region order everywhere: GCC first (UAE and Saudi Arabia).

## Loading the catalog

`scripts/business/seed-products.py` loads the products and their offers from [`scripts/business/products.json`](../../../scripts/business/products.json) into one workspace through the public API (`/api/v1/business`). It uses the standard library only and never reads business code.

```bash
# Offline dry run: prints the payloads, needs nothing.
python3 scripts/business/seed-products.py

# Dry run against a tenant: reads the catalog and prints the plan, writes nothing.
export ANUM_API_URL=https://api.anum360os.com
read -s ANUM_API_TOKEN && export ANUM_API_TOKEN   # an owner's access token; never in a file or chat
export ANUM_WORKSPACE_ID=...                      # optional, else the token's default workspace
python3 scripts/business/seed-products.py

# Write.
python3 scripts/business/seed-products.py --apply
```

| Rule | How |
|---|---|
| Dry run by default | Nothing is written without `--apply`; without a token the script works offline |
| Idempotent | A product matches by slug: the slug of an existing product's name, or of one of the entry's `aliases` (so a product typed in by hand as `TyrePulse` is found). A second run makes no writes. |
| Changes | Changed product fields are patched (the API cannot change `kind`; the plan says so). A draft offer is updated in place; if the active version already matches, nothing happens; otherwise a new draft version is added. |
| Prices | Every offer is `quote` and stays a **draft**: the script never activates a version. The owner sets the price and activates it in Business, Catalog. |
| Token | Read from `ANUM_API_TOKEN` only, sent as a bearer header, never printed. Plain HTTP is refused for anything but localhost. Needs `business:manage` (an owner). |
| Target profile | Each entry's `target_profile` block is sent as the product's target customer profile (`PUT /api/v1/business/pipeline/products/{id}/target`, [Business prospecting](../../business.md#prospecting-pipeline)): `buyer_roles` go to `roles`, the free-text `company_size` is mapped to the size bands (micro under 10, small under 50, medium under 250, large under 1000, enterprise) and kept word for word in `company_size_note`, `trigger_events` are kept for conversations, and `-` means unknown. |

Tests: `services/api/tests/test_seed_products_script.py` checks every payload against the API's own input models and runs the seed twice in process against the app (the second run writes nothing). No network.

## Owner questions (all products)

Each kit ends with its own list. The ones that block selling:

1. Prices: the ANUM OS pilot, the Tyre Pulse pilot (roadmap hypothesis SAR 750 to 1,500) and plans, the VoiceQuote pilot.
2. VoiceQuote: the verified feature list, target trades, maturity.
3. Anum360 Store: is the store's software for sale, or only its goods?
4. References: may the Tyre Pulse production customer, and the owner's own businesses, be named?
