# Repository Structure

ANUM should be a monorepo so shared contracts, docs, infrastructure, and clients evolve together. The first implementation can add only the folders it needs, but the target structure should be clear.

## Proposed Layout

```text
/
  README.md
  docs/
  apps/
    web/
    desktop/
    android/
    mobile/
  services/
    api/
    workers/
  packages/
    contracts/
    ui/
    sdk/
  runtime/
    agent/
    skills/
    tools/
    model-gateway/
  infra/
    docker/
    opentofu/
    github-actions/
  tests/
    integration/
    contract/
    security/
```

## Ownership

`services/api` owns FastAPI routes and backend composition. `runtime` owns agent execution concepts and adapters. `packages/contracts` holds shared schemas. `apps/web` is the primary browser UI. `apps/desktop` wraps the web experience with Tauri. `apps/android` preserves the native Kotlin client, while `apps/mobile` is the approved Flutter implementation for Android, iOS, and tablets.

## Docs

Architecture decisions should live in `docs/decisions`. Product and platform docs should remain close to the code so major changes can update docs in the same PR.

## Now

The web, desktop, Android, Flutter mobile, API, contracts, infrastructure, and documentation roots are implemented. Future directories remain a guide and should only be created with working code.

## Later

Add workspace tooling for Python, Node/TypeScript, Android, infrastructure validation, and cross-package contract generation.
