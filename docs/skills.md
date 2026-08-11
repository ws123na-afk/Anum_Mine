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

## Now

Define the skill manifest shape, ship a few internal skills for task planning and document drafting, and create validation tests.

## Later

Add a governed skill registry, user-installed skills, organization-approved skill packs, skill quality scoring, and marketplace-style distribution.