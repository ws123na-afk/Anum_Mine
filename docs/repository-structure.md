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

`services/api` should own FastAPI routes and backend composition. `runtime` should own agent execution concepts and adapters. `packages/contracts` should hold OpenAPI-derived types and shared schemas. `apps/web` should be the primary UI. `apps/desktop` should wrap web functionality with Tauri. `apps/android` should hold the Kotlin client.

## Docs

Architecture decisions should live in `docs/decisions`. Product and platform docs should remain close to the code so major changes can update docs in the same PR.

## Now

Only documentation is required. The future folder layout is a guide, not a mandate to create empty implementation directories.

## Later

Add workspace tooling for Python, Node/TypeScript, Android, infrastructure validation, and cross-package contract generation.