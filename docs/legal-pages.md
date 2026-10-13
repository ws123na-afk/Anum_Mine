# Public legal pages: Privacy Policy and Terms of Service

The web host serves two public pages that need no sign-in:

| URL | File |
|---|---|
| `https://<WEB>/privacy` (and `/privacy/`) | `apps/web/public/legal/privacy.html` |
| `https://<WEB>/terms` (and `/terms/`) | `apps/web/public/legal/terms.html` |

For the owner's deployment `<WEB>` is `app.anum360os.com`, so the addresses are `https://app.anum360os.com/privacy` and `https://app.anum360os.com/terms`. Use them wherever a platform asks for a privacy policy or terms URL: TikTok for Developers, the other social apps in [Social media marketing](marketing.md), Google Play and the App Store ([Store data safety](store-data-safety.md)), and `ANUM_PRIVACY_POLICY_URL` ([Privacy](privacy.md#configuration)).

## How they are served

- The pages are plain HTML with inline CSS only: no scripts, no fonts or images from another origin, so they work under the web CSP (`style-src` allows inline styles; scripts are `'self'` only). Dark by default in ANUM's colours, light when the device prefers it, readable at 390 px wide.
- Vite copies `public/` into `dist/` unchanged, so they ship in the `anum-web` image.
- `apps/web/nginx/default.conf.template` has exact-match `location = /privacy`, `/privacy/`, `/terms` and `/terms/` blocks. Exact matches win over the single-page fallback (`location /`), so the app never swallows them. Each block repeats the security headers (a location's `add_header` replaces the server-level set) and sends `Cache-Control: no-cache`, like `index.html`.
- `apps/web/test/legalPages.test.ts` fails if a page gains a script or a long dash, loses its cross links, or if a location block is removed or drifts from the single-page fallback's headers.
- The web sign-in and create-account screens link to both pages. The desktop app loads the build from disk, where these paths are not served, so it shows no links there. `pnpm dev` (Vite) does not map `/privacy`; open `/legal/privacy.html` there.

## Changing the text

Edit the two HTML files, update the "Effective" date, and keep the facts in step with what the code does ([Privacy](privacy.md), [Social media marketing](marketing.md)). Use short, plain sentences and no em or en dashes. The [privacy policy template](privacy-policy.md) lists everything a deployment may store if the policy needs to grow.
