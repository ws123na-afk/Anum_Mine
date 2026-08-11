# Automation

Automation in ANUM turns recurring intent into governed execution. It should support scheduled tasks, event-triggered workflows, reminders, monitors, and long-running processes without losing user control.

## Automation Types

- Scheduled automations: run at a defined time or interval.
- Event automations: react to inbound webhooks, NATS events, or integration changes.
- Monitors: periodically check a condition and report or act.
- Follow-ups: resume a task after a delay or external signal.
- Human-in-the-loop flows: pause until approval or additional input is available.

## Temporal Role

Temporal should own durable workflow execution, retries, timers, waits, and resumability. The application database remains the source of truth for user-visible tasks and policy. Temporal workflow IDs should be stored with ANUM tasks for traceability.

## Event Role

NATS JetStream should carry domain events that notify clients, workers, and integrations. Event consumers should be idempotent because retries and duplicate delivery are normal in distributed systems.

## Safety

Automations must run under an explicit actor and tenant context. They should have scopes, expiration, audit history, and clear cancellation. High-risk automated actions require policy-backed approval or preauthorization.

## Now

Build scheduled task reminders, event emission, workflow pause/resume, and cancellation.

## Later

Add complex triggers, natural-language automation builder, organization automation libraries, monitor dashboards, and preapproved low-risk action bundles.