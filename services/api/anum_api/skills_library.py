"""First-party internal skills shipped with the platform.

Per docs/skills.md "Now" scope: task planning and document drafting are the
two internal skills for this slice. Both are registered into
`DEFAULT_SKILL_REGISTRY`, which the rest of the app can import once the
runtime is ready to wire skill selection into the agent run loop.
"""

from .schemas import RiskLevel
from .skills import SkillManifest, SkillRegistry, SkillTestCase

TASK_PLANNING_SKILL = SkillManifest(
    id="task_planning",
    name="Task Planning",
    version="1.0.0",
    purpose=(
        "Break a vague or under-specified user prompt into a concrete, "
        "ordered list of steps before any tool execution begins, so the "
        "agent (and any human approver) can see the intended shape of the "
        "work up front."
    ),
    trigger_conditions=[
        "plan a task",
        "break down work",
        "break this down",
        "make a plan",
        "create a plan",
        "what are the steps",
        "how should i approach",
        "outline the steps",
    ],
    required_tools=["memory_search", "task_status_lookup"],
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
        },
        "required": ["prompt"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
            "estimated_risk": {"type": "string"},
        },
        "required": ["steps", "estimated_risk"],
    },
    allowed_memory_scopes=["task_notes", "task_history"],
    approval_requirements=[
        "No approval required to produce a plan; approval is only required "
        "later, when a planned step proposes a high-risk tool call."
    ],
    risk_level=RiskLevel.LOW,
    test_cases=[
        SkillTestCase(
            input={"prompt": "Help me plan a task to migrate our billing database"},
            expected_output_summary=(
                "Returns an ordered list of steps (e.g. assess current schema, "
                "draft migration plan, identify rollback strategy, schedule "
                "maintenance window) plus an estimated_risk string reflecting "
                "the sensitivity of a database migration."
            ),
        ),
        SkillTestCase(
            input={"prompt": "Break down work for onboarding a new teammate"},
            expected_output_summary=(
                "Returns an ordered list of onboarding steps (e.g. provision "
                "accounts, assign starter tasks, schedule intro meetings) plus "
                "a low estimated_risk string, since onboarding is routine."
            ),
        ),
    ],
)


DOCUMENT_DRAFTING_SKILL = SkillManifest(
    id="document_drafting",
    name="Document Drafting",
    version="1.0.0",
    purpose=(
        "Draft a written artifact -- a memo, summary, or short document -- "
        "from task context and relevant memory, producing a first-pass "
        "text the user can review, edit, and approve rather than a final "
        "publish-ready output."
    ),
    trigger_conditions=[
        "draft a memo",
        "write a summary",
        "draft a document",
        "write up",
        "prepare a memo",
        "summarize this for",
        "draft an email",
    ],
    required_tools=["memory_search", "document_template_lookup"],
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "audience": {"type": "string"},
        },
        "required": ["prompt"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "body"],
    },
    allowed_memory_scopes=["task_notes", "task_history", "document_drafts"],
    approval_requirements=[
        "Draft output is a proposal, not a sent/published artifact; sending "
        "or publishing the draft to an external destination requires a "
        "separate approval step evaluated by the runtime's tool policy."
    ],
    # MEDIUM rather than LOW: unlike task_planning (which only produces an
    # internal-facing plan), a drafted document can read as authoritative
    # and may be copy-pasted straight into an external-facing channel by a
    # user who skips review, so it warrants the higher default risk rating.
    risk_level=RiskLevel.MEDIUM,
    test_cases=[
        SkillTestCase(
            input={
                "prompt": "Draft a memo summarizing this week's incident postmortem",
                "audience": "engineering leadership",
            },
            expected_output_summary=(
                "Returns a title (e.g. 'Incident Postmortem Summary'), a body "
                "covering timeline, root cause, and remediation drawn from "
                "task memory, and open_questions for any gaps in the record."
            ),
        ),
        SkillTestCase(
            input={
                "prompt": "Write a summary of the customer call for the team",
                "audience": "internal team",
            },
            expected_output_summary=(
                "Returns a title (e.g. 'Customer Call Summary'), a body "
                "covering key points and action items from the call, and an "
                "open_questions list for any follow-ups that need clarification."
            ),
        ),
    ],
)


DEFAULT_SKILL_REGISTRY = SkillRegistry()
DEFAULT_SKILL_REGISTRY.register(TASK_PLANNING_SKILL)
DEFAULT_SKILL_REGISTRY.register(DOCUMENT_DRAFTING_SKILL)
