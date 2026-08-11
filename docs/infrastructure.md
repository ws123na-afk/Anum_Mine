# Infrastructure

ANUM infrastructure should be reproducible, observable, and environment-aware from the first implementation. Docker should support local development and service packaging. OpenTofu should manage cloud infrastructure. GitHub Actions should run checks and deployment workflows.

## Local Development

A local Docker composition should eventually provide PostgreSQL with pgvector, Valkey, NATS JetStream, Temporal, Keycloak, S3-compatible storage, and the ANUM backend. Developers should be able to run the web app separately with Vite.

## Environments

Recommended environments are local, preview, staging, and production. Each environment should have separate secrets, databases, object buckets, identity realm settings, and telemetry configuration.

## OpenTofu

OpenTofu should define networks, compute, managed databases where used, object storage, secrets integration, DNS, certificates, queues, and observability wiring. State must be stored remotely with locking for shared environments.

## GitHub Actions

CI should run formatting, linting, type checks, unit tests, contract checks, docs checks, container builds, and infrastructure validation. Deployment workflows should require environment approvals for production.

## Now

Define infrastructure conventions and add local development services when implementation begins.

## Later

Add blue/green or rolling deploys, preview environments per PR, autoscaling, cross-region design, backup drills, disaster recovery tests, and cost controls.