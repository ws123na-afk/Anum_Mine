# Commerce

ANUM brings the owner's sales channels into one place: **Amazon** (Amazon.sa and Amazon.ae through the Selling Partner API), **noon** (Saudi Arabia and the UAE) and **the owner's own store**. It syncs orders, listings (SKU, ASIN or channel id, title, price, stock), inventory, returns and, where the channel allows, fees and settlements into workspace tables under row-level security; shows dashboards on the web and a summary with the order list on the phone; raises alerts; and gives the assistant tools so "ANUM, how were Amazon sales today?" is answered from the synced data. Price and stock changes are high-risk tool calls: nothing reaches a channel until someone approves the exact change, bound to its payload hash.

Code: `services/api/anum_api/commerce/` (API), `apps/web/src/CommerceView.tsx` (web), `apps/mobile/lib/features/commerce/` (Flutter), contracts in `packages/contracts/src/commerce.ts`, migration `0038_commerce`.

## Who can do what

| Permission | Roles | Allows |
|---|---|---|
| `commerce:read` | owner, member, viewer | Dashboard, summary, orders (without buyer details), catalogue, listings, returns, alerts, connection health, alert rules (read) |
| `commerce:operate` | owner, member | Edit the catalogue, sync now, acknowledge alerts, propose price and stock changes (they still need approval) |
| `commerce:manage` | owner | Connect, change, turn off and remove channels; replace credentials; alert rules |
| `commerce:pii` | owner | Buyer name, e-mail, phone and address on an order (each view is audited) |

Every route checks these on the server; the web and phone hide what a role cannot do and say who can. The change tools require the `owner` or `member` role in tool policy and re-check `commerce:operate` when they run.

## Owner setup

Credentials are entered once in **Commerce › Connections** (owners only), encrypted with `ANUM_SECRETS_KEY` (Fernet) before they are stored and never shown again: the page shows a hint (the last characters, or the staff e-mail for the ANUM 360 store). **Test connection** calls the channel once and records the result as the connection's health.

### Amazon (Selling Partner API)

1. **Register as a developer.** In Seller Central (the Amazon.sa account that also sells on Amazon.ae), open *Apps and Services › Develop Apps* and complete the developer profile as a *private developer* (an app for your own seller account). Request the roles the connector uses: *Product Listing* (listings and price/stock changes), *Inventory and Order Tracking* (orders), *Pricing* (buy box and competitor prices), *Amazon Fulfillment* (FBA stock) and *Finance and Accounting* (fees, refunds, settlements). Buyer details need the restricted *Direct-to-Consumer Shipping* role; request it only if you ship orders yourself and want the buyer's name and address in ANUM.
2. **Create the app.** In *Develop Apps*, add a new app client (type *SP API*). Its *LWA credentials* are the **LWA client ID** and **LWA client secret**.
3. **Authorize it.** On the app, choose *Authorize app*: Amazon shows a **refresh token** for your seller account. Copy it once; it does not expire unless you revoke the authorization.
4. **Find your Merchant Token** (Seller ID) in *Settings › Account Info › Merchant Token*.
5. In ANUM, **Add connection › Amazon**: tick Amazon.sa (`A17E79C6D8DWNP`) and/or Amazon.ae (`A2VIGQ35RCS4UG`), enter the Merchant Token, the LWA client ID and secret and the refresh token, and turn on **Fetch buyer details** only if you have the restricted role and want it. **Test connection** checks that the account sells on the chosen marketplaces.

Both marketplaces use the Europe endpoint (`https://sellingpartnerapi-eu.amazon.com`). ANUM exchanges the refresh token at Login with Amazon for an hourly access token; SP-API no longer needs AWS signing.

### noon

noon's API Platform covers catalogue, orders, fulfilment and notifications; access is granted to sellers on request (noon's integration team, automation@noon.com, or your account manager), with credentials for your partner account.

In ANUM, **Add connection › noon**: tick noon Saudi and/or noon UAE, paste the **API key** noon issued and, if noon gave you one, the **Partner ID**. The key is sent as a bearer token and the partner id as `X-Partner-Id`.

The noon reference is not public, so the request paths are configuration, not code: `services/api/anum_api/commerce/clients/noon.py` holds the paths this adapter was written against (`/order/v1/orders`, `/offer/v1/offers`, `/return/v1/returns`, `/partner/v1/whoami`), and a connection's `paths` setting overrides any of them (`PATCH /api/v1/commerce/connectors/{id}` with `{"settings": {"paths": {"orders": "/..."}}}`). **Confirm them against the reference noon gives you** before relying on the sync; the response fields are read under the names noon and its integrators use (`partner_sku`/`sku`, `order_nr`/`id`, ...). If noon's credentials are a service account that signs requests instead of a static key, this connector cannot use them yet (see [What is not done](#what-is-not-done)).

### The owner's own store

One generic connector with four ways in:

| Mode | When | What ANUM needs |
|---|---|---|
| **ANUM 360 store** (`anum360`, default) | The owner's store (FastAPI + MongoDB, admin API under `/api/admin`) | The store's API address (`https://...`) and a **dedicated staff account** |
| **Store API** (`rest`) | Any store that implements the contract below | The API address and an API key (bearer) |
| **Supabase** (`supabase`) | An app whose data is in Supabase | The project URL and a key that can only read |
| **Webhook** (`webhook`) | The store pushes events | Nothing: ANUM shows a URL and a signing secret once |

**ANUM 360.** In the store's admin, *Staff & roles*, create a staff account for ANUM with only the view keys: **orders.view, products.view, inventory.view, returns.view**, plus **inventory.adjust** if approved stock changes may reach the store. Do not give it *orders.contact*: without it the store masks customers' contact details, so no buyer data reaches ANUM. The store requires two-step verification for money keys (*products.price*) and for personal-data-plus-bulk keys; a view-only account needs none, and ANUM cannot answer a two-step code (it says so if the account asks for one). Price changes for this store therefore stay in the store's admin unless the owner gives the account *products.price* without the two-step duty. ANUM reads `/api/admin/inventory` (products, SKU, stock, retail and sale price, English and Arabic names), `/api/admin/orders` (newest first; the last 14 days are re-read every sync so status changes are picked up) and `/api/returns/admin/all`. Stock changes use `/api/admin/inventory/adjust` with the difference to the new quantity, read right before the change. Prices are AED by default. The paths can be overridden with the `paths` setting like noon's.

**Store API contract** (`rest`, bearer API key):

| Request | Answer |
|---|---|
| `GET /products?cursor=&limit=` | `{"products": [{"sku", "id", "title", "title_ar", "brand", "price", "currency", "quantity", "active"}], "next_cursor"}` |
| `GET /orders?updated_since=<ISO>&cursor=&limit=` | `{"orders": [{"id" or "number", "status", "created_at", "updated_at", "ship_by", "total", "currency", "city", "country", "items": [{"sku", "title", "quantity", "unit_price", "total"}]}], "next_cursor"}` |
| `GET /returns?updated_since=<ISO>` | `{"returns": [{"id", "order_id", "sku", "quantity", "reason", "status", "amount", "currency", "created_at"}], "next_cursor"}` |
| `PUT /products/{sku}/price` | body `{"price", "currency"}` |
| `PUT /products/{sku}/stock` | body `{"quantity"}` |

Order statuses map `pending`, `paid`/`confirmed`/`processing` (to ship), `shipped`/`fulfilled`, `delivered`/`completed`, `cancelled`, `refunded`/`returned`.

**Supabase.** ANUM reads through PostgREST (`/rest/v1/<table>`) with the key as `apikey` and bearer. Use a key bound to a database role that can only `select` these tables (or RLS read policies), never the service-role key. Tables default to `orders`, `order_items`, `products` and `returns`; columns default to the contract names (`products.quantity` reads the `stock` column, `products.active` reads `is_active`). Override them with the `tables` and `columns` settings, for example `{"tables": {"products": "items"}, "columns": {"products": {"sku": "code", "price": "price_sar"}}}`; names must be plain identifiers. This mode is read-only: price and stock changes are refused. ANUM never opens a direct PostgreSQL connection to a tenant-given host; a store whose data is in PostgreSQL without PostgREST uses the webhook or the store API.

**Webhook.** After connecting, ANUM shows `POST <API>/api/v1/commerce/webhooks/{tenant}/{workspace}/{connection}` and a signing secret (shown once; **New signing secret** replaces it). The store sends `{"events": [{"type": "order" | "product" | "return", "data": {...}}]}` (at most 500 events, the contract shapes above) with `X-Anum-Timestamp: <unix seconds>` and `X-Anum-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>">`. Events older than five minutes, unsigned or wrongly signed requests are refused with 401.

Owner-supplied addresses (own store, Supabase, a noon override) go through the same guard as workspace model endpoints ([Security](security.md)): outside local development only public HTTPS on the default port, resolved once and pinned for the connection (no DNS rebinding), never following redirects; private, loopback, link-local and metadata addresses are refused when saved and again on every call.

## Sync

Each connection keeps cursors: the time of the last complete pass per collection (orders, returns, money; re-read with a five-minute overlap) and the channel's page token while a pass is unfinished. Listings are a full pass every sync. Each page is written in its own transaction, so nothing fetched is lost when a sync stops. A sync stops after `ANUM_COMMERCE_SYNC_TIME_BUDGET_SECONDS` and continues from the cursor next time.

**Sync now** (owners and members) runs one sync and reports what it did. The background sync runs when `ANUM_COMMERCE_SYNC_ENABLED=true`: every `ANUM_COMMERCE_SYNC_POLL_SECONDS` each API process finds due connections as `anum_maintenance` (which can read only the id, scope, `enabled` and `next_sync_at` of due connections; see [Multi-tenancy](multi-tenancy.md#maintenance-role)), claims each with a compare-and-set update of `next_sync_at` and syncs it as the application role in its workspace. The API login needs `anum_maintenance` granted `WITH INHERIT FALSE, SET TRUE` (as `infra/helm/bootstrap-database.sql` does). The interval per workspace is the alert rule *Sync every (minutes)* (default 30).

| Amazon data | SP-API operation |
|---|---|
| Listings (SKU, ASIN, title, product type, price, quantity, status) | `searchListingsItems` (`/listings/2021-08-01/items/{sellerId}`) |
| FBA stock | `getInventorySummaries` (`/fba/inventory/v1/summaries`) |
| Orders and lines | `getOrders` (`LastUpdatedAfter`), `getOrderItems` |
| Buyer details (optional) | `createRestrictedDataToken` for one order, then `getOrder` with `buyerInfo` and `shippingAddress` |
| Fees and refunds (returns) | `listFinancialEvents` (shipment fees, refund events) |
| Settlements | `listFinancialEventGroups` |
| Buy box, competitor price | `getItemOffers` (`ANUM_COMMERCE_PRICING_CHECKS_PER_SYNC` listings per sync, oldest checked first) |
| Price and stock changes | `patchListingsItem` (`purchasable_offer`, `fulfillment_availability`) |

### Rate limits

Every operation has a token bucket sized from SP-API's published rate and burst (for example `getOrders` 0.0167 requests a second with a burst of 20, `getOrderItems` 0.5/30, `getItemOffers` 0.5/1); `x-amzn-RateLimit-Limit` replaces the rate when Amazon sends it. A call waits for a token; 429 and 503 are retried with `Retry-After` (or backoff) up to `ANUM_COMMERCE_MAX_RETRIES`. When the wait would exceed `ANUM_COMMERCE_MAX_RETRY_WAIT_SECONDS`, the sync stops, the connection shows **Rate limited** and its next sync waits at least that long. Buckets are per connection and operation and shared by every sync in the process. noon and the own store use a conservative fixed rate.

### Health

| Health | Meaning | Next step |
|---|---|---|
| Not synced yet | Saved, never synced | Sync now |
| Connected | The last sync or test worked | — |
| Rate limited | The channel asked ANUM to wait | Nothing: the next sync waits |
| Reconnect needed | The channel refused the credentials | Replace them (Edit); syncs retry every six hours meanwhile |
| Sync failed | Another error; the message says which | Fix what it names, then Sync now |
| Off | Turned off by an owner | Turn on |

Failures raise a *Sync problem* alert; it resolves on the next good sync.

## Data model

All tables carry `tenant_id` and `workspace_id` and are under forced RLS (migration `0038_commerce`):

| Table | Holds |
|---|---|
| `commerce_connectors` | Channel, name, marketplaces, non-secret settings, encrypted credentials, health, cursors, counts |
| `commerce_products` | The owner's SKU, English and Arabic titles, brand, **tyre attributes** (size such as `205/55R16`, width, aspect ratio, construction, rim, load index, speed index, DOT week and year, season), cost, low-stock level |
| `commerce_listings` | One row per connection, marketplace and SKU: channel id (ASIN, noon SKU), price, currency, quantity (stock per channel), status, fulfilment, buy box and lowest competitor price |
| `commerce_orders`, `commerce_order_lines` | Orders (status, purchase and ship-by time, total, currency, ship-to city and country) and their lines |
| `commerce_customers` | Only when buyer details are on: a SHA-256 of the channel's buyer reference and the name, e-mail, phone and address as Fernet ciphertext |
| `commerce_returns`, `commerce_fees` | Returns and refunds; fees, refunds and settlements (amounts as the channel reports them, fees negative) |
| `commerce_alerts`, `commerce_settings` | Alerts (one unresolved per subject) and the workspace's alert rules |

Products are created from listings on first sight; tyre attributes are parsed from titles (`Michelin Primacy 4 205/55R16 91V DOT 2324` gives the size, load 91, speed V, week 23 of 2024) and can be corrected in the catalogue. Catalogue edits stay in ANUM and never change a listing. Search matches SKU, titles (English and Arabic), brand, ASIN and a size typed as `2055516`, `205 55 16` or `205/55R16`. Removing a connection deletes its listings, orders, customers, returns, fees and alerts; the catalogue stays.

**Personal data is minimized.** Orders keep only the ship-to city and country. Buyer details are fetched only when the owner turns them on for Amazon, only for orders the seller ships itself that are still unshipped, each through a Restricted Data Token scoped to that order; they are encrypted at rest, returned only to owners and every view is audited. The ANUM 360 connector never receives contact details.

## Dashboard

`GET /api/v1/commerce/dashboard?from=&to=&tz=&currency=&channel=` (at most `ANUM_COMMERCE_DASHBOARD_MAX_DAYS` days): totals per currency, sales by marketplace, sales by day and channel in one currency (SAR and AED are never added together; the web picks the larger and offers the other), top products with their tyre size, orders to ship (late first), low stock, returns and their rate, buy box and competitor price alerts, channel fees and refunds, open alerts. Days are calendar days in the caller's time zone. Cancelled orders count nowhere.

The web **Commerce** page has Overview, Orders (filters, search, paging, order detail), Catalogue (search, add and edit products with tyre attributes and Arabic titles, price and stock changes), Alerts and Connections (with alert rules). It is in English and Arabic and passes the responsive and accessibility gates. The phone shows a summary for a period and the order list (**Home › Sales and orders**).

## Alerts

Checked after every sync and webhook delivery against the workspace's rules (**Connections › Alert rules**):

| Alert | Rule |
|---|---|
| Low stock | A listing at or below the product's level, else the workspace's (default 4: tyres sell in sets); critical when out of stock |
| Not shipped in time | A merchant-fulfilled order still to ship after its ship-by time (or `handling_hours` after purchase) |
| Return spike | A channel's returns in 7 days reach the count *and* the share of units sold |
| Buy box lost | Amazon reports another seller holding the buy box |
| Competitor below your price | The lowest other offer is below yours by the configured percentage |
| Sync problem | A sync failed or the credentials were refused |

Neither SP-API nor noon exposes product reviews or seller feedback, so there are no review alerts. An alert resolves by itself when its cause clears; **Acknowledge** (owners and members) hides it from Open. A new alert publishes `commerce.alert.raised` ([Events](events.md)): the web and desktop apps show it as a desktop notification and owners' and members' phones get a push ([Notifications](notifications.md)), each only when the person keeps **Store alerts** (`commerce_alerts`) on. The push carries a fixed sentence, never the SKU or order.

## Assistant and voice

| Tool | Risk | Does |
|---|---|---|
| `commerce.sales_summary` | low | Orders, units and revenue per currency for a channel and period, with the best seller |
| `commerce.list_orders` | low | Open, unshipped or other orders, newest first |
| `commerce.get_order` | low | One order with its lines |
| `commerce.low_stock` | low | Listings at or below their level |
| `commerce.amazon.update_price`, `commerce.noon.update_price`, `commerce.own_store.update_price` | high | Change a listing's price after approval |
| `commerce.amazon.update_stock`, `commerce.noon.update_stock`, `commerce.own_store.update_stock` | high | Change a listing's stock after approval |

The *Commerce* skill is selected by words such as *sales*, *orders*, *stock*, *Amazon* or *noon*; the call is parsed from the person's own words only, never from retrieved text or model output. "How were Amazon sales today?" runs `commerce.sales_summary` at once. "Set the price of SKU MX-2055516 on Amazon.sa to 450" proposes `commerce.amazon.update_price` and the run pauses for an approval showing the SKU, marketplace, price and target host; approving needs the displayed payload hash. A request without channel, SKU or value proposes nothing and explains what to say. One tool per channel lets governance treat channels differently (for example, deny `commerce.noon.*`). Every commerce tool declares the autonomy category `commerce` ([Autonomy](autonomy.md)): the read tools are `read` actions, and the change tools are `price_change` and `stock_change`, protected kinds that always ask whatever the autonomy level.

In the Assistant chat the same questions run as tasks and come back as `commerce` cards: the answer above, the orders or low-stock listings below with a link to Commerce, and a change request as an approval card with its payload hash ([Conversations: tools in chat](conversations.md#tools-in-chat)).

By voice, "ANUM, how were Amazon sales today?" (or in Arabic, "كيف كانت مبيعات أمازون اليوم؟") is answered from the synced data without a model call; "what is low on stock?" and "orders to ship" too. A price or stock change said aloud is only proposed as a task to create on screen.

From the web catalogue, **Change price** and **Change stock** propose the same approved tool call (`POST /api/v1/commerce/listings/{id}/price-change` or `/stock-change`) and show the payload hash to look for in Approvals.

## API

All under `/api/v1/commerce`: `GET /setup`, `GET /dashboard`, `GET /summary?period=&lang=`; `GET|POST /connectors`, `GET|PATCH|DELETE /connectors/{id}`, `POST /connectors/{id}/test`, `POST /connectors/{id}/sync`, `POST /connectors/{id}/webhook-secret`, `POST /webhooks/{tenant}/{workspace}/{id}`; `GET|POST /products`, `GET|PATCH|DELETE /products/{id}`, `GET /listings`, `POST /listings/{id}/price-change`, `POST /listings/{id}/stock-change`; `GET /orders`, `GET /orders/{id}`, `GET /returns`; `GET /alerts`, `POST /alerts/{id}/acknowledge`; `GET|PUT /settings`. Lists take `limit` (at most 200) and `offset` and return `{items, total, limit, offset}`.

## Operator settings

| Setting | Default | Meaning |
|---|---|---|
| `ANUM_COMMERCE_SYNC_ENABLED` | `false` | Run the background sync |
| `ANUM_COMMERCE_SYNC_POLL_SECONDS` | 60 | How often to look for due connections |
| `ANUM_COMMERCE_SYNC_BATCH_SIZE` | 20 | Connections per pass |
| `ANUM_COMMERCE_SYNC_TIME_BUDGET_SECONDS` | 50 | Longest single sync |
| `ANUM_COMMERCE_SYNC_MAX_PAGES` | 10 | Pages per collection per sync |
| `ANUM_COMMERCE_PRICING_CHECKS_PER_SYNC` | 10 | Buy box checks per sync |
| `ANUM_COMMERCE_INITIAL_LOOKBACK_DAYS` | 30 | How far the first sync reaches back |
| `ANUM_COMMERCE_MAX_RETRIES`, `ANUM_COMMERCE_MAX_RETRY_WAIT_SECONDS` | 3, 10 | Rate-limit retries and the longest wait |
| `ANUM_COMMERCE_REQUEST_TIMEOUT_SECONDS` | 20 | Per request |
| `ANUM_COMMERCE_AMAZON_LWA_URL`, `ANUM_COMMERCE_AMAZON_EU_ENDPOINT`, `ANUM_COMMERCE_NOON_BASE_URL` | Amazon and noon production | Endpoints (sandbox testing) |
| `ANUM_COMMERCE_DASHBOARD_MAX_DAYS`, `ANUM_COMMERCE_DASHBOARD_MAX_ORDERS` | 366, 50000 | Dashboard bounds |

None of these is a credential.

## What is not done

- **noon paths are unverified.** The adapter is built and tested against the paths above with a fake server; noon's reference is not public and was not available here. Confirm the paths and the authentication scheme with noon before relying on the noon sync, and override paths per connection if they differ. Request signing with a service account is not implemented.
- **Reviews and seller feedback** have no official API on either marketplace, so there are no review alerts.
- **Amazon returns** come from refund events (money returned), not from the returns report, so a return requested but not yet refunded does not show. Reports API ingestion (`GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE`) would add them.
- **Direct PostgreSQL connections** to a store database are deliberately not offered (use Supabase/PostgREST, the store API or the webhook).
- The phone shows the summary and orders only; catalogue edits, connections and price or stock changes are on the web (approvals work on every client).
