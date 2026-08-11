# Development Standards

ANUM should be built with standards that make future autonomy safe to operate. Code style, tests, contracts, migrations, and reviews are part of the product quality bar.

## General Rules

- Keep implementation aligned with documented boundaries.
- Prefer typed interfaces and generated contracts where practical.
- Avoid hidden global state in agent execution.
- Make tenant context explicit in requests, jobs, events, and tests.
- Treat migrations, policies, and infrastructure as reviewed code.
- Keep examples small and separate from production paths.

## Backend

Python backend code should use FastAPI, typed request and response models, dependency injection for infrastructure boundaries, explicit transactions, and clear domain services. Agent runtime code should be testable without real model providers.

## Frontend

React+TypeScript+Vite code should use shared generated API types, predictable state management, accessible components, and clear task timelines. The web app should be reusable inside Tauri.

## Testing

The baseline should include unit tests, integration tests with local services, contract tests, RLS tests, authorization tests, workflow tests, and model gateway adapter tests with mocks.

## Reviews

Pull requests should explain behavior changes, data model changes, risk implications, and validation. Security-sensitive changes require extra scrutiny.

## Now

Adopt formatting, linting, type checks, migration review, test conventions, and docs updates for architecture changes.

## Later

Add performance budgets, formal threat-model reviews, evaluation suites, compatibility policies, and release gates.