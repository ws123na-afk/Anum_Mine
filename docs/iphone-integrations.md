# iPhone integrations

One device enrollment for every iPhone Shortcut: Settings, Integrations, iPhone (`#iphone`). A device token reaches only the integrations it carries as **scopes**, each integration has its own switch, and the page shows the last connection, failed events, a synthetic test and the exact Shortcut setup for each one.

Code: `services/api/anum_api/finance/devices.py` (scopes, the integration registry, `authorize_scope`, the module adapter), `finance/links_api.py` (routes), the device rows of [iPhone SMS](finance-sms.md) (`finance_sms_devices`), events in `finance_device_events` (migration `0134_finance_links`); web `apps/web/src/IphoneIntegrations.tsx`, `src/lib/iphoneIntegrations.ts` (the guide registry), `src/lib/iphoneApi.ts`, catalogs `messages/iphone.{en,ar}.ts`; contracts `packages/contracts/src/iphoneIntegrations.ts`. Tests: `tests/test_finance_links.py`, `tests/test_postgres_finance_links.py`, `apps/web/test/iphoneIntegrations.test.ts`, `apps/web/e2e/iphone-integrations.spec.ts`.

## Scopes and integrations

| Scope | Integration | Address | Permission | Limits per device | Default |
|---|---|---|---|---|---|
| `finance.sms` | Bank SMS | `POST /api/v1/finance/sms/ingest` | `finance:manage` | 30 a minute, 1,000 a day | the SMS forwarding switch (off) |
| `finance.wallet` | Apple Wallet payments | `POST /api/v1/finance/wallet/ingest` | `finance:manage` | 30, 1,000 | on once SMS forwarding is on |
| `attendance` | Attendance arrive and leave | `POST /api/v1/attendance/device/events` | `attendance:use` | 10, 200 | off |
| `documents` | Document scans | `POST /api/v1/documents/device/upload` | `document:write` | 10, 200 (and the vault's own hourly cap) | off |
| `vehicle` | Vehicle driving sessions ([Vehicles](vehicles.md#iphone-driving-sessions)) | `POST /api/v1/vehicles/device/sessions` | `vehicle:write` | 10, 300 | off |
| `purchases` | Shared order and delivery messages ([Purchases](purchases.md#iphone-share)) | `POST /api/v1/purchases/device/share` | `purchase:write` | 10, 200 | off |

A scope with no registered integration cannot be put on a token and its requests answer `404 integration_unavailable`.

## Checks

`devices.authorize_scope(authorization, scope)` is the helper every module calls (from a worker thread). For `Authorization: Bearer <device token>` it checks, in order: the token's form; the owner is still an active member and holds the integration's permission; the workspace is not archived; the device row, looked up in the owner's own tenant, workspace and user scope, matches the secret in constant time; the device is not revoked or paused; the token carries the scope; the integration is switched on; the per-device, per-scope rate limit. It answers 401, 403, 404, 409 or 429 (with `Retry-After`) and records an event (`ok`, or `denied` with the code) once the secret matched. A wrong or unknown token records nothing. The rate limit counts accepted requests only, so a refused retry never keeps a device locked out. When the module then refuses or fails the request, it calls `devices.report_failure(context, device_id, scope, code)` (the vault upload and the attendance route do), so the page shows the integration as failing. `devices.require_https(request)` refuses plain HTTP outside local and test environments.

**Modules.** A module exposes a seam (`register_device_authority`, as `anum_api/attendance/devices.py` and `anum_api/vault/devices.py` do). `devices.plug_module_authorities()` (called once when the API starts) registers `ScopedDeviceAuthority` with every installed seam: a header that is not a device token falls through to the ordinary sign-in.

Tokens: an id and a 256-bit secret, only the secret's SHA-256 is stored, shown once, rotatable (the old token stops at once), pausable and revocable (the hash is replaced). Tokens never appear in a URL, a log, an audit row or an export.

## The page

- **Devices:** each device with its status, scopes, token hint, last connection and last error; pause or resume, a new token, change scopes (adding a scope needs its permission, and a finance scope the safety check; removing never does; scopes the person's role cannot issue stay as they are) and disconnect. A new device takes a name and the scopes the person's role may use; the token is shown once with the headers. Up to 5 devices.
- **Integrations:** one card per integration from the API: the switch (Bank SMS uses the forwarding switch, which still needs the finance safety check), the state (`Off`, `On, no device has this permission`, `On, waiting for the first Shortcut`, `On, last request failed`, `Working`), devices with the scope, last connection, last success, failures in 7 days and the limits; **Run test** (a synthetic check that records nothing in the module); **Failed events** (refused and failed requests, paged); **Shortcut setup** (address, method, headers, JSON body, step by step, and the phone-side connection test).
- States: loading, load error with retry, permission denied, no devices (with the next step), no integration for the role. English and Arabic (right to left).

**Adding an integration** (a module PR): register it in the API (`devices.register_integration(Integration(key, scope, permission, ingest_path, ...))`), call `authorize_scope` (or expose a `DeviceAuthority` seam) from its route, and append one row to `IPHONE_GUIDES` in `apps/web/src/lib/iphoneIntegrations.ts` with its words in the module's own catalog. `IphoneIntegrations.tsx` does not change.

## Test from the phone

`POST /api/v1/integrations/iphone/ping` with the device token and `{"integration": "<key>"}` proves the address, the token, its scope and the switch, records a `test` event and nothing else, and answers `{"status": "test_ok", "text": "Connected. ..."}`.

## API

All under `/api/v1/integrations/iphone`; any active member (`integration:read`) sees only their own devices and events.

| Route | |
|---|---|
| `GET /` | devices, integrations with their state, scopes, limits and the ping address |
| `POST /devices` (`{name, scopes}`) | token shown once; each scope's permission; finance scopes need the finance safety check |
| `POST /devices/{id}/rotate`, `/pause`, `/resume`, `/revoke`, `/scopes` (`{scopes}`) | |
| `PUT /integrations/{key}` (`{enabled}`) | the integration's permission |
| `POST /integrations/{key}/test` | synthetic test |
| `GET /integrations/{key}/events?outcome=problem` | failed and refused events, paged |
| `POST /ping` | device token only |

Device events are kept 30 days and deleted with the person's finance data.

## Limits to know

The token sits inside the Shortcut, so anyone with the unlocked phone can read it: rotate it or disconnect the device if the phone is lost. iOS may delay or skip automations when the phone is locked or in Low Power Mode. Every Shortcut needs testing on a real phone.
