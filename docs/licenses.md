# Third-party licenses, notices and SBOMs

Every dependency that ships in an ANUM artifact is checked against a written license policy in CI, listed in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) at the repository root, and recorded with its exact version in a CycloneDX SBOM per app. This closes the "no license scan and no `THIRD_PARTY_NOTICES.md`" gap in the [business gap register](business/ANUM_BUSINESS_GAP_REGISTER.md) and covers the "commercial open source" item of section 9 of the [business master roadmap](ANUM_OS_Business_and_Product_Master_Roadmap.md). Outside projects we might reuse are judged separately in the [reuse decision register](business/REUSE_DECISION_REGISTER.md).

This is an engineering control, **not legal advice**. Before selling ANUM to customers, have the policy and the exceptions reviewed by a qualified adviser.

## What is scanned

Only what ships: production dependencies, never test or build-only tooling that stays on the developer's machine.

| App | Ecosystem | What counts as shipped | Where the list comes from |
|---|---|---|---|
| `api` (FastAPI image) | Python | everything pip installs for `services/api` (no `test` extra) | `pip install --dry-run --report` for CPython 3.13 on manylinux x86_64, the API image's interpreter; licenses from the core metadata pip reports (`License-Expression`, `License`, then trove classifiers) |
| `web` (browser bundle, web image) and `desktop` (Tauri bundles the web build) | npm | the production closure of `apps/web`, `apps/desktop` and `packages/contracts` in `pnpm-lock.yaml`, every platform variant | the lockfile; licenses from the npm registry manifest of each exact locked version |
| `desktop` | Rust | normal and build dependencies of `apps/desktop/src-tauri` for every target platform | `cargo metadata --locked`; licenses from each crate's `Cargo.toml` (or its license file) |
| `mobile` (Flutter) | Dart | the non-dev closure of `apps/mobile` | `flutter pub deps --json`; licenses read from each package's `LICENSE` in the pub cache; packages from the Flutter SDK are covered by the SDK's BSD-3-Clause `LICENSE` |
| `android` (Kotlin) | Maven | `releaseRuntimeClasspath` of `apps/android/app` | Gradle's own resolution (an init script, nothing added to the build); licenses from the Maven POMs, following parent POMs |

The scanner is [`scripts/licenses/license_scan.py`](../scripts/licenses/license_scan.py) (standard library plus PyYAML 6.0.3 for the pnpm lockfile). It reads each package manager's own resolution and metadata instead of an installed `node_modules` or virtual environment, so the result is the same on macOS, Linux and Windows. No paid service is involved: the only network calls are to the public npm registry, Google Maven and Maven Central.

Why these tools rather than a single scanner: Trivy (already used for container images) has no license data for `Cargo.lock`, `pubspec.lock` or a pip requirements file, and reads npm licenses only from an installed `node_modules`, which differs per operating system. `cargo metadata` is what `cargo-deny` and `cargo-license` read; reading it directly keeps one policy file for all five ecosystems instead of a separate `deny.toml`.

Not covered yet (listed in [pending work](pending-work.md)): the native Android and iOS libraries that Flutter plugins pull in through Gradle and CocoaPods (for example the Firebase SDKs), the Tauri WebView runtime, and operating system packages inside the container images (those are in the Trivy image SBOMs of the deploy workflows, see [Deployment](deployment.md#image-supply-chain)).

## Policy

[`scripts/licenses/policy.toml`](../scripts/licenses/policy.toml) puts every SPDX license id in one of three lists:

- **allowed**: permissive licenses (MIT, Apache-2.0, BSD-2/3-Clause, ISC, 0BSD, Zlib, Unicode, PSF, BSL-1.0, CC0, Unlicense, BlueOak, PostgreSQL, OpenSSL, CDLA-Permissive-2.0) and MPL-2.0, which is file-level copyleft: fine for unmodified runtime use, but changes to MPL files themselves must be published.
- **review**: weak copyleft (LGPL, EPL, CDDL, MPL-1.1, CC-BY-SA). Fails until the owner records an exception that says how the obligations are met.
- **denied**: GPL, AGPL, SSPL, BUSL, Elastic, Commons Clause, non-commercial or no-derivatives Creative Commons. Fails until an exception exists.

A license id in none of the lists, or a package with no detectable license, is **unknown** and fails. `A OR B` passes when either side passes (we use the permissive option); `A AND B` needs both. Changing the lists is an owner decision and the pull request must say why.

## Exceptions and corrections

[`scripts/licenses/exceptions.toml`](../scripts/licenses/exceptions.toml) holds two kinds of reviewed entry:

- `[[override]]` corrects a license the scan detected wrongly or not at all, after someone read the package's real license. It needs the detected value, the corrected SPDX expression, a link to the license text at a fixed revision and a reason. The corrected license is still checked against the policy, so an override can never accept a copyleft license.
- `[[exception]]` accepts a package whose license is in review, denied or unknown. Only the owner adds these, with a reason, `reviewed_by` and `reviewed_on`. The license must equal what the scan reports, so a license change upstream makes the exception stale.

Both are matched exactly. An entry that no longer matches anything fails the scan, so the file never accumulates forgotten approvals.

## Run it locally

GitHub Actions does not run on most merges at the moment (pull requests merge with `[skip ci]`), so the scan is also one local command:

```bash
python3 -m pip install --upgrade 'pip>=25.0' 'PyYAML==6.0.3'   # once; or set PYTHON to a venv's python
pnpm licenses:check     # policy check; THIRD_PARTY_NOTICES.md must match
pnpm licenses:update    # rewrite THIRD_PARTY_NOTICES.md after a dependency change
```

Both run `scripts/licenses/regenerate.sh --available`: every ecosystem whose tool is installed is scanned (Python and npm need only Python; Rust needs `cargo`; Dart needs `flutter`; Android needs Gradle with Google Maven reachable). An ecosystem whose tool is missing is skipped, its section of `THIRD_PARTY_NOTICES.md` is kept exactly as committed, and the command ends with a `NOT scanned here: ...` line naming it, so a partial run never claims more than it checked. `--skip gradle` (or any ecosystem) skips one on purpose, for example when its registry is unreachable. Without `--available` the script needs every tool, as CI does.

`pip` must be 25.0 or newer: older versions leave `License-Expression` (Core Metadata 2.4) out of their install report, and most current packages (FastAPI, psycopg, cryptography and others) then look unlicensed. The Python collector refuses an older pip instead of reporting false unknowns.

Results go to `LICENSE_SCAN_DIR` (a temporary directory by default): `results.json` lists every package, its license and its class. `--sbom-dir DIR` also writes the SBOMs of the apps whose ecosystems were scanned.

## CI

The **Licenses and SBOMs** job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on every pull request and every push to `main` that runs CI:

1. Unit tests of the scanner (`python -m unittest discover -s scripts/licenses`).
2. `scripts/licenses/regenerate.sh --check --sbom-dir ...`: collects all five ecosystems, prints a summary table (also in the job summary) and fails on any policy violation or stale exception.
3. Fails if the committed `THIRD_PARTY_NOTICES.md` differs from what the scan generates. The regenerated file is uploaded as the `third-party-notices` artifact when the job fails, together with `results.json` (every package, its license and its class).
4. Uploads the `sbom` artifact: `sbom-api.cdx.json`, `sbom-web.cdx.json`, `sbom-desktop.cdx.json` (Rust crates plus the web bundle), `sbom-mobile.cdx.json` and `sbom-android.cdx.json`, CycloneDX 1.6 JSON with package URLs, exact versions and license expressions. File names follow the image SBOMs the deploy workflows attach to each signed image (`sbom-api.cdx.json`, `sbom-web.cdx.json`).

The "Web and contracts" and "Tauri desktop" jobs also run `scripts/licenses/assert_not_bundled.py`, which fails if `sharp` or libvips ever appears in `apps/web/dist` or the Tauri bundle configuration (see the current result below).

Tool versions: Python 3.13 with pip 25.0 or newer, PyYAML 6.0.3, the stable Rust toolchain from the commit-pinned `dtolnay/rust-toolchain`, Flutter stable from the commit-pinned `subosito/flutter-action`, Temurin JDK 17 and Gradle 8.10.2 (the same as the Android job).

## Regenerating THIRD_PARTY_NOTICES.md

After adding, removing or bumping a dependency run `pnpm licenses:update` (above). With every tool installed, `scripts/licenses/regenerate.sh` without flags scans all five ecosystems; it needs `cargo`, `flutter` and Gradle 8.10.2 on JDK 17 (`GRADLE=/path/to/gradle` if it is not on `PATH`; an Android SDK is not needed to resolve the classpath). It rewrites the file even when the policy check fails, and exits non-zero in that case. If a tool is missing locally, the sections it covers stay as committed; take them from a CI run's `third-party-notices` artifact when CI runs.

The notices list component names, licenses, the apps that ship them and a source link, but not versions, so a routine version bump only changes the file when it adds, removes or relicenses a package; exact versions are in the SBOMs. The full license texts and copyright lines ship inside each package; bundling them into the installers and an in-app licenses screen is listed in [pending work](pending-work.md).

## Current result

Scan of 4 November 2026 on `main` (`1e934b0`), run locally with `regenerate.sh --available` (pip 26.3, Flutter 3.47.9, cargo). Package counts include every locked version.

| Ecosystem | Packages | Allowed | Violations | Scanned |
|---|---|---|---|---|
| Python (`api`) | 71 | 69 | 2 | 4 November |
| npm (`web`, `desktop`) | 176 | 162 | 14 | 4 November |
| Rust (`desktop`) | 532 | 532 | 0 | 4 November |
| Dart (`mobile`) | 103 | 103 | 0 | 4 November |
| Maven (`android`) | 98 | 98 | 0 | 18 October (Google Maven is not reachable from the machine that ran the 4 November scan; `apps/android` declares the same dependencies as on 18 October; CI's full scan re-checks it) |

No GPL, AGPL, SSPL, BUSL or unknown license is shipped. Both findings are weak copyleft (LGPL) and are **owner decisions**; none has been exempted, so `pnpm licenses:check` and the CI job fail until the owner decides:

1. **`psycopg` and `psycopg-binary` 3.3.6 (API), LGPL-3.0-only.** The PostgreSQL driver, used unmodified as a separately installed Python package. While ANUM is only offered as a hosted service nothing is distributed, so the LGPL's conditions do not apply; if the API image is ever given to a customer, the license text must ship with it (it does, inside the package) and the customer must be able to replace the library (pip packages can be). Options: accept with an exception, or move to a permissively licensed driver (asyncpg, Apache-2.0), a large change to the database layer.
2. **`@img/sharp-libvips-*` 1.3.4 and `@img/sharp-win32-*`, `@img/sharp-wasm32` 0.35.5 (web, desktop), LGPL-3.0-or-later.** Prebuilt libvips binaries for the `sharp` image library, pulled in by `kokoro-js` (in-browser voice) through `@huggingface/transformers`. `sharp` is a Node.js library and the browser build of transformers.js does not use it. Checked again on 4 November 2026: a production build of `apps/web/dist` (311 files) contains no sharp or libvips file, native addon or module reference, and the Tauri bundle config ships only that build and the Rust binary (no `resources`, no `externalBin`); `scripts/licenses/assert_not_bundled.py` fails the "Web and contracts" and "Tauri desktop" jobs if either ever appears, and the web image copies the same `dist`. Options: accept with an exception that cites this assertion, or stop resolving `sharp` for the web app (for example a pnpm override), which needs a check that voice still works.

Reviewed, not exceptions:

- `python-dateutil` declares only "Dual License"; its `LICENSE` at the 2.9.0.post0 tag is Apache-2.0 for newer contributions and BSD-3-Clause for all code, recorded as an override (18 October).
- `defusedxml` 0.7.1 (new since 18 October, API) declares "PSFL"; the `LICENSE` in its wheel is the Python Software Foundation License Version 2, recorded as an override to `PSF-2.0` (4 November).
- `CDLA-Permissive-2.0` (the Mozilla CA root list in the `webpki-root-certs` crate) is in the allowed list as a permissive data license (18 October).
- `MIT-CMU` was added to the allowed list on 4 November for `pillow` 13.0.0 (new since 18 October, API): an HPND-style permissive license that asks only that the copyright and permission notice stay in copies and that the authors' names are not used for endorsement. **Owner: please confirm this policy addition.** Pillow's manylinux wheel also bundles native libraries (libjpeg, libpng, libtiff, libwebp, FreeType, HarfBuzz, lcms2, OpenJPEG, libavif, dav1d, aom, brotli, zstd, liblzma, libxcb), each under its own permissive license listed in the wheel's `LICENSE`; FreeType is used under the FreeType License, which asks for a credit in the product's documentation if the API image is ever distributed. Native libraries inside Python wheels (also `psycopg-binary`'s bundled libpq and OpenSSL) are not scanned separately yet.

Other changes since 18 October, all permissive: Python `charset-normalizer`, `dnspython`, `pypdf`, `requests`; npm `three`, `web-vitals`; Rust `static_vcruntime`; Dart: `file_picker` 13 is split into `android_file_picker`, `file_picker_*` and `windows_file_picker` (MIT), plus `ffi_leak_tracker` (BSD-3-Clause).

Exceptions may also carry `source_url` and `notice`; both are printed in a dedicated section of `THIRD_PARTY_NOTICES.md` (for LGPL: the license name and where the source is offered). To accept a finding, the owner adds entries like this to `scripts/licenses/exceptions.toml` (one per package name, so `psycopg-binary` and each `@img/sharp-*` package need their own) and the check turns green:

```toml
[[exception]]
ecosystem = "python"
package = "psycopg"
license = "LGPL-3.0-only"
reason = "PostgreSQL driver used unmodified as a separate, replaceable Python package; license text ships in the package."
reviewed_by = "owner"
reviewed_on = "2026-11-04"
```
