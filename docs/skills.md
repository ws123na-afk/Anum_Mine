# Skills

Skills are reusable capability packages that guide ANUM agents through a class of work. A skill may include instructions, schemas, example workflows, tool requirements, validation rules, and risk metadata. Skills should improve reliability without bypassing runtime policy.

## Skill Contents

A skill should be declarative where possible. It can define purpose, trigger conditions, required tools, input schema, output schema, allowed memory scopes, approval requirements, and test cases. Implementation code should live in normal application packages or tool adapters, not hidden inside a skill document.

## Loading and Selection

The runtime should select skills based on task intent, tenant policy, user permissions, and tool availability. Skill selection should be recorded in the task trace so users and developers can understand why an agent behaved a certain way.

## Safety

Skills are not trusted code by default. They should not grant access to tools or memory. They can request capabilities, but the runtime and authorization layer decide what is actually available. Skill updates should be versioned and auditable.

## Repository Placement

The monorepo should eventually include a `skills/` workspace for first-party skill definitions, examples, and tests. Skill packages should avoid secrets and environment-specific configuration.

## Implemented Control Plane

The Phase 2 API provides immutable semantic versions, tenant-owned publishing, workspace installations, declared and administrator-approved tool grants, and execution-time resolution. Resolution intersects the installed grants with runtime tool availability and rejects skills above the caller's risk ceiling. Skill instructions never grant tools by themselves.

Endpoints are under `/api/v1/skills`: `POST /versions`, `GET /versions`, `POST /installations`, `GET /installations`, and `POST /resolve`.

## Remaining Distribution Work

Marketplace signing, organization-wide promotion workflows, quality scoring, and durable PostgreSQL persistence remain part of the distribution and production-hardening work.
