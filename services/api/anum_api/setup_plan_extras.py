"""The setup plan's parts that came with the features built after it (docs/setup-plan.md):
per-agent monthly caps and loop limits (agent controls), budget alerts, the approval-ageing
reminder, the owner's Home order, the marketing consent rule, the plan's agents in each
business workspace and their seats in the headquarters offices.

Every write still goes through the route function a person's click would call, with the
caller's own context (``agent_controls.set_agent_cap``, ``agent_loop_limits.set_loop_limit``,
``approval_reminders.set_reminder_settings``, ``home.save_layout``,
``contact_consent.set_consent_settings``, ``office_space_api.assign_desk``), so each feature's
permission check, validation and audit record applies. ``PlanExtras`` is mixed into
``setup_plans.Planner``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from .authorization import Permission
from .dependencies import _unit_of_work, has_permission
from .schemas import TenantContext
from .settings import settings
from .setup_plan_ledger import LedgerOrigin, LedgerState, open_setup_plan_ledger

if TYPE_CHECKING:  # pragma: no cover
    from .setup_plans import Applied, BlueprintAgent, Planned

NEEDS_OWNER = "Needs an owner of this workspace."


def _ledger_target(context: TenantContext, plan_id: str, key: str) -> str | None:
    """The object an item made or linked, read now (an earlier item of this apply may have made it)."""
    with open_setup_plan_ledger(context) as ledger:
        for entry in ledger.entries(context, plan_id):
            if entry.item_key == key and entry.state == LedgerState.APPLIED:
                return entry.target_id
    return None


class PlanExtras:
    """Planning methods for the newer parts; ``self`` is a ``setup_plans.Planner``."""

    bp: Any
    context: TenantContext
    phase: Any
    overwrite: bool
    ledger: dict[str, Any]
    options: Any

    # ---- helpers

    def _agent_id_now(self, agent: BlueprintAgent) -> str | None:
        entry = self._existing(agent.key)  # type: ignore[attr-defined]
        return entry.target_id if entry is not None else None

    def _agent_will_exist(self, planned_agents: dict[str, Planned]) -> Callable[[BlueprintAgent], str | None]:
        def state(agent: BlueprintAgent) -> str | None:
            step = planned_agents.get(agent.key)
            if step is None:
                return "missing"
            action = step.item.action.value
            if action in {"present", "update", "kept"} and (step.item.link and step.item.link.id):
                return None
            if action == "create" or (action == "present" and step.run is not None):
                return "pending"
            if action == "kept" and step.item.reason_code == "removed_by_owner":
                return "removed"
            return "missing" if action == "skipped" else None
        return state

    def _setting_item(self, key: str, section: Any, kind: str, name: str, *, desired: Any, current: Any, has_value: bool,
                      allowed: bool, refusal: str, write: Callable[[], Applied], facts: list[str], link: Any = None,
                      create_reason: str, tenant: bool = False, ledger_key: str | None = None) -> Planned:
        """One stored setting the plan sets once and then leaves to the owner."""
        from .setup_plans import ItemAction, Planned

        ledger_key = ledger_key or key
        entry = self._existing(ledger_key)  # type: ignore[attr-defined]
        item = self.item  # type: ignore[attr-defined]
        if has_value and current == desired:
            return Planned(item(key, section, kind, name, ItemAction.PRESENT, "already_set", "Already set to the plan's value.", link=link, facts=facts),
                           ledger_key, tenant)
        if not allowed:
            return Planned(item(key, section, kind, name, ItemAction.SKIPPED, "needs_owner", refusal, link=link, facts=facts), ledger_key, tenant)
        ours = entry is not None and entry.origin == LedgerOrigin.CREATED
        if not has_value and ours and not self.overwrite:
            return Planned(item(key, section, kind, name, ItemAction.KEPT, "removed_by_owner",
                                "Someone turned it off after the plan set it. The plan leaves it off. Turn on 'Replace my changes' to set it again.",
                                link=link, facts=facts), ledger_key, tenant)
        if has_value and not self.overwrite:
            return Planned(item(key, section, kind, name, ItemAction.KEPT, "set_by_someone",
                                "It is already set to another value here. The plan leaves yours. Turn on 'Replace my changes' to use the plan's.",
                                link=link, facts=facts), ledger_key, tenant)
        action = ItemAction.UPDATE if has_value else ItemAction.CREATE
        return Planned(item(key, section, kind, name, action, "will_update" if has_value else "will_create",
                            create_reason, link=link, facts=facts), ledger_key, tenant, write)

    # ---- agent controls: monthly caps and loop limits per agent

    def agent_controls(self, planned_agents: dict[str, Planned]) -> list[Planned]:
        from .agent_controls import AgentCapLimit, open_agent_controls_store, set_agent_cap
        from .agent_loop_limits import LoopLimitWrite, open_loop_limit_store, set_loop_limit
        from .setup_plans import Applied, ItemAction, Link, Planned, Section, fingerprint

        caps: dict[str, Any] = {}
        loops: dict[str, Any] = {}
        with open_agent_controls_store(self.context) as store:
            caps = {cap.agent_id: cap for cap in store.list_caps(self.context)}
        with open_loop_limit_store(self.context) as store:
            loops = {row.agent_id: row for row in store.list(self.context)}
        allowed = has_permission(self.context, Permission.ORGANIZATION_MANAGE)
        will_exist = self._agent_will_exist(planned_agents)
        limits = self.bp.loop_limits
        out: list[Planned] = []
        for agent in self.bp.agents:
            usd = round(agent.budget_aed / self.bp.aed_per_usd, 2)
            step = planned_agents.get(agent.key)
            agent_id = step.item.link.id if step is not None and step.item.link and step.item.link.id else None
            state = will_exist(agent)
            link = Link(view="agentControls", id=agent_id)
            cap_facts = [f"USD {usd:,.2f} a month (AED {agent.budget_aed:,.0f} at {self.bp.aed_per_usd} AED per USD).",
                         "When it is used up, this agent stops until next month. Owners are told at 50, 75, 90 and 100 percent."]
            loop_facts = [f"At most {limits.max_runs_per_task} runs per task (its loop limit) and {limits.max_step_attempts} attempts "
                          "per automation step that runs as it."]

            def agent_target(agent: BlueprintAgent = agent) -> str:
                found = _ledger_target(self.context, self.bp.id, agent.key)
                if not found:
                    raise HTTPException(status_code=409, detail="The agent was not made in this apply. Apply the plan again once it exists.")
                return found

            def write_cap(agent: BlueprintAgent = agent, usd: float = usd) -> Applied:
                set_agent_cap(agent_target(agent), AgentCapLimit(monthly_cost_limit_usd=usd, monthly_token_limit=None), self.context)
                return Applied(target_id=f"agent:{agent.key}", fingerprint=fingerprint(usd))

            def write_loop(agent: BlueprintAgent = agent) -> Applied:
                set_loop_limit(agent_target(agent), LoopLimitWrite(max_runs_per_task=limits.max_runs_per_task,
                                                                   max_step_attempts=limits.max_step_attempts), self.context)
                return Applied(target_id=f"agent:{agent.key}", fingerprint=fingerprint([limits.max_runs_per_task, limits.max_step_attempts]))

            for key, kind, name, facts, write, current, desired in (
                (f"{agent.key}:cap", "agent_cap", f"Monthly AI cap for {agent.name}", cap_facts, write_cap,
                 caps.get(agent_id).monthly_cost_limit_usd if agent_id and caps.get(agent_id) else None, usd),
                (f"{agent.key}:loop", "agent_loop_limit", f"Loop and retry limits for {agent.name}", loop_facts, write_loop,
                 (loops[agent_id].max_runs_per_task, loops[agent_id].max_step_attempts) if agent_id and loops.get(agent_id) else None,
                 (limits.max_runs_per_task, limits.max_step_attempts)),
            ):
                if state in {"missing", "removed"}:
                    out.append(Planned(self.item(key, Section.CONTROLS, kind, name, ItemAction.SKIPPED, "missing_dependency",  # type: ignore[attr-defined]
                                                 "The agent is not here (skipped or removed), so there is nothing to limit.", link=link, facts=facts, week=agent.week),
                                       key, False))
                    continue
                if state == "pending":
                    if not allowed:
                        out.append(Planned(self.item(key, Section.CONTROLS, kind, name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER,  # type: ignore[attr-defined]
                                                     link=link, facts=facts, week=agent.week), key, False))
                    else:
                        out.append(Planned(self.item(key, Section.CONTROLS, kind, name, ItemAction.CREATE, "will_create",  # type: ignore[attr-defined]
                                                     "Set right after the agent is made.", link=link, facts=facts, week=agent.week), key, False, write))
                    continue
                planned = self._setting_item(key, Section.CONTROLS, kind, name, desired=desired, current=current, has_value=current is not None,
                                             allowed=allowed, refusal=NEEDS_OWNER, write=write, facts=facts, link=link,
                                             create_reason="Sets it through Agent controls.")
                planned.item.week = agent.week
                out.append(planned)
        # Budget alerts are built in now; nothing to set.
        out.append(Planned(self.item("budget_alerts", Section.BUDGET, "budget_alerts", "Budget alerts at 50, 75 and 90 percent",  # type: ignore[attr-defined]
                                     ItemAction.BUILTIN, "builtin",
                                     "Built in: owners get an in-app notice when the workspace, the organization or one agent passes 50, 75, 90 "
                                     "and 100 percent of its monthly AI budget. No email is sent for it.", link=Link(view="agentControls")),
                           "budget_alerts", False))
        return out

    # ---- approval-ageing reminder (automation #4)

    def approval_reminder(self, automation: Any) -> Planned:
        from .approval_reminders import (
            ReminderSettingsUpdate,
            open_reminder_store,
            set_reminder_settings,
        )
        from .setup_plans import Applied, Link, Section, fingerprint

        hours = self.bp.approval_reminder_hours
        with open_reminder_store(self.context) as store:
            current = store.get(self.context).reminder_hours

        def write() -> Applied:
            set_reminder_settings(ReminderSettingsUpdate(reminder_hours=hours), self.context)
            return Applied(target_id=f"workspace:{self.context.workspace_id}", fingerprint=fingerprint(hours))

        facts = [f"#{automation.number} · for {self.bp.agent(automation.agent).name}",
                 f"After {hours} hours a pending approval reminds this workspace's owners in the app, once. Approvals still expire after 24 hours."]
        planned = self._setting_item(automation.key, Section.AUTOMATIONS, "approval_reminder", automation.name, desired=hours, current=current,
                                     has_value=current is not None, allowed=has_permission(self.context, Permission.POLICY_MANAGE),
                                     refusal=NEEDS_OWNER, write=write, facts=facts, link=Link(view="policy"),
                                     create_reason="Turns on the approval reminder in Approval policy.")
        planned.item.week = automation.week
        return planned

    # ---- Home order (the applier's own)

    def home_layout(self) -> list[Planned]:
        from .home import HOME_SECTIONS, open_home_store, save_layout
        from .setup_plans import Applied, ItemAction, Link, Planned, Section, fingerprint

        desired = [s for s in self.bp.home_sections if s in HOME_SECTIONS]
        with open_home_store(self.context) as store:
            profile = store.get_profile(self.context)
        current = list(profile.sections) if profile and profile.sections else None
        key, ledger_key = "home_layout", f"home_layout@{self.context.user_id}"
        facts = ["Your Home shows: " + ", ".join(desired) + ".", "Only your own Home. Everyone arranges their own under Home > Arrange."]

        def write() -> Applied:
            save_layout(self.context, desired)
            return Applied(target_id=f"user:{self.context.user_id}", fingerprint=fingerprint(desired))

        if current == desired:
            return [Planned(self.item(key, Section.HOME, "home_layout", "Home priorities", ItemAction.PRESENT, "already_set",  # type: ignore[attr-defined]
                                      "Your Home already has the plan's order.", link=Link(view="home"), facts=facts), ledger_key, False)]
        if current is not None and not self.overwrite:
            return [Planned(self.item(key, Section.HOME, "home_layout", "Home priorities", ItemAction.KEPT, "set_by_someone",  # type: ignore[attr-defined]
                                      "You arranged your Home yourself. The plan keeps your order. Turn on 'Replace my changes' to use the plan's.",
                                      link=Link(view="home"), facts=facts), ledger_key, False)]
        return [Planned(self.item(key, Section.HOME, "home_layout", "Home priorities",  # type: ignore[attr-defined]
                                  ItemAction.UPDATE if current is not None else ItemAction.CREATE, "will_update" if current is not None else "will_create",
                                  "Puts decisions waiting for you first, then the day at a glance and what needs focus.",
                                  link=Link(view="home"), facts=facts), ledger_key, False, write)]

    # ---- marketing consent rule (organisation)

    def consent_rule(self) -> list[Planned]:
        from .contact_consent import ConsentSettingsUpdate, open_consent_store, set_consent_settings
        from .setup_plans import Applied, Link, Section, fingerprint

        if not self.bp.require_marketing_consent:
            return []
        key, name = "consent_marketing", "Marketing sends need a consent record"
        facts = ["Every marketing send checks each recipient's consent record (lawful basis, source and date) as well as the opt-out list.",
                 "Record consents under Contact opt-outs > Consent records before turning on marketing automations."]
        link = Link(view="suppressions")
        with open_consent_store(self.context) as store:
            current = store.get_settings(self.context)
        allowed = has_permission(self.context, Permission.SUPPRESSION_MANAGE) or has_permission(self.context, Permission.ORG_POLICY_MANAGE)

        def write() -> Applied:
            set_consent_settings(ConsentSettingsUpdate(require_for_marketing=True), self.context)
            return Applied(target_id=f"tenant:{self.context.tenant_id}", fingerprint=fingerprint(True))

        planned = self._setting_item(key, Section.GOVERNANCE, "consent_rule", name, desired=True, current=current.require_for_marketing,
                                     has_value=current.updated_by is not None, allowed=allowed,
                                     refusal="Needs a workspace owner or an organization administrator.", write=write, facts=facts, link=link,
                                     create_reason="Turns on the consent rule for the whole organization.", tenant=True)
        return [planned]

    # ---- offices: which HQ office and department each agent sits in, and seats

    def office_data(self) -> dict[str, Any] | None:
        """The workspace's offices through office staffing (PostgreSQL only), or None."""
        if getattr(self, "_offices", False) is not False:
            return self._offices  # type: ignore[has-type,no-any-return]
        self._offices: dict[str, Any] | None = None
        if settings.repository_backend != "postgresql" or not has_permission(self.context, Permission.TASK_READ):
            return None
        try:
            from . import office_staffing_api
            from . import office_staffing_blueprint as obp

            data = office_staffing_api._load(self.context)
            offices = []
            for building in data["buildings"]:
                kind, units = obp.classify(building["id"], [r for r in data["rooms"] if r["building_id"] == building["id"]])
                offices.append({"id": building["id"], "name": building["name"], "kind": kind, "units": list(units)})
            self._offices = {"data": data, "offices": offices}
        except Exception:  # noqa: BLE001 - offices never stop the plan; the item says why
            self._offices = None
        return self._offices

    def office_for(self, agent: BlueprintAgent) -> tuple[dict[str, Any], str] | None:
        """(office, unit id) the plan's department maps to, when such an office exists here."""
        unit = self.bp.department_units.get(agent.department)
        found = self.office_data()
        if not unit or not found:
            return None
        office = next((o for o in found["offices"] if unit in o["units"]), None)
        return (office, unit) if office else None

    def office_fact(self, agent: BlueprintAgent) -> str | None:
        from . import office_staffing_blueprint as obp

        unit = self.bp.department_units.get(agent.department)
        unit_name = obp.UNIT_BY_ID[unit].en if unit in obp.UNIT_BY_ID else None
        place = self.office_for(agent)
        if place is None:
            return f"Department in the headquarters: {unit_name}. No office here has it yet." if unit_name else None
        return f"Sits in: {place[0]['name']}, {unit_name}."

    def offices(self, planned_agents: dict[str, Planned]) -> list[Planned]:
        from . import office_staffing_blueprint as obp
        from .office_provisioning import WORK_ROOM_KINDS
        from .setup_plans import Applied, ItemAction, Link, Planned, Section

        if not self.options.seat_in_offices:
            return []
        found = self.office_data()
        if found is None or not found["offices"]:
            reason = ("Offices need the headquarters (PostgreSQL). Set up the campus in Headquarters, then apply again to seat the agents."
                      if found is None else "This workspace has no office yet. Set up the campus in Headquarters, then apply again to seat the agents.")
            return [Planned(self.item("offices", Section.OFFICES, "office_seats", "Seat agents in the headquarters offices",  # type: ignore[attr-defined]
                                      ItemAction.MANUAL, "no_offices", reason, link=Link(view="office")), "offices", False)]
        allowed = has_permission(self.context, Permission.WORKSPACE_MANAGE)
        will_exist = self._agent_will_exist(planned_agents)
        seated = {d["agent_id"]: d for d in found["data"]["desks"] if d["agent_id"]}
        unit_departments = {u: {d for d, uu in obp.DEPARTMENT_UNITS.items() if uu == u} for u in obp.UNIT_BY_ID}
        out: list[Planned] = []
        for agent in self.bp.agents:
            key, name = f"{agent.key}:desk", f"Seat {agent.name}"
            place = self.office_for(agent)
            step = planned_agents.get(agent.key)
            agent_id = step.item.link.id if step is not None and step.item.link and step.item.link.id else None
            if place is None:
                unit = self.bp.department_units.get(agent.department, "")
                out.append(Planned(self.item(key, Section.OFFICES, "office_seat", name, ItemAction.MANUAL, "no_office_for_department",  # type: ignore[attr-defined]
                                             f"No office here has the {obp.UNIT_BY_ID[unit].en if unit in obp.UNIT_BY_ID else agent.department} department. "
                                             "Add one in Headquarters, or seat the agent at a desk by hand.", link=Link(view="office"),
                                             week=agent.week), key, False))
                continue
            office, unit = place
            facts = [f"Office: {office['name']}.", f"Department: {obp.UNIT_BY_ID[unit].en}."]
            if agent_id and agent_id in seated:
                out.append(Planned(self.item(key, Section.OFFICES, "office_seat", name, ItemAction.PRESENT, "already_seated",  # type: ignore[attr-defined]
                                             f"Already at desk {seated[agent_id]['label']}.", link=Link(view="office", id=office["id"]),
                                             facts=facts, week=agent.week), key, False))
                continue
            if will_exist(agent) in {"missing", "removed"}:
                out.append(Planned(self.item(key, Section.OFFICES, "office_seat", name, ItemAction.SKIPPED, "missing_dependency",  # type: ignore[attr-defined]
                                             "The agent is not here (skipped or removed), so there is no one to seat.", facts=facts, week=agent.week), key, False))
                continue
            if not allowed:
                out.append(Planned(self.item(key, Section.OFFICES, "office_seat", name, ItemAction.SKIPPED, "needs_owner", NEEDS_OWNER,  # type: ignore[attr-defined]
                                             facts=facts, week=agent.week), key, False))
                continue

            def seat(agent: BlueprintAgent = agent, office: dict[str, Any] = office, unit: str = unit) -> Applied:
                from . import office_staffing_api
                from .office_space_api import AssignDesk, assign_desk

                agent_id = _ledger_target(self.context, self.bp.id, agent.key)
                if not agent_id:
                    raise HTTPException(status_code=409, detail="The agent was not made in this apply. Apply the plan again once it exists.")
                data = office_staffing_api._load(self.context)
                if any(d["agent_id"] == agent_id for d in data["desks"]):
                    desk = next(d for d in data["desks"] if d["agent_id"] == agent_id)
                    return Applied(target_id=desk["id"], origin=LedgerOrigin.LINKED)
                rooms = {r["id"]: r for r in data["rooms"] if r["building_id"] == office["id"] and r["kind"] in WORK_ROOM_KINDS}
                vacant = sorted((d for d in data["desks"] if d["room_id"] in rooms and d["agent_id"] is None and d["id"] not in data["held"]),
                                key=lambda d: (rooms[d["room_id"]].get("team_department") not in unit_departments.get(unit, set()),
                                               rooms[d["room_id"]]["name"].lower(), d["id"]))
                if not vacant:
                    raise HTTPException(status_code=409, detail=f"{office['name']} has no free desk. Add a desk in Headquarters, then apply again.")
                desk = vacant[0]
                assign_desk(desk["id"], AssignDesk(agent_id=agent_id, expected_revision=int(desk["revision"])), self.context)
                return Applied(target_id=desk["id"], details={"office": office["id"], "agent_id": agent_id})

            out.append(Planned(self.item(key, Section.OFFICES, "office_seat", name, ItemAction.CREATE, "will_seat",  # type: ignore[attr-defined]
                                         f"Seats it at a free desk in {office['name']}, in a {obp.UNIT_BY_ID[unit].en} room when there is one.",
                                         link=Link(view="office", id=office["id"]), facts=facts, week=agent.week), key, False, seat))
        return out

    # ---- the plan's agents in each named business workspace

    def business_agents(self) -> list[Planned]:
        from .setup_plans import ItemAction, Link, Phase, Planned, Section

        if not self.options.business_workspaces:
            return []
        out: list[Planned] = []
        targets = {ws.key: ws for ws in self.bp.workspaces}
        with _unit_of_work(self.context, require_membership=False) as repository:
            memberships = [m for m in repository.list_caller_memberships(self.context) if m.tenant_id == self.context.tenant_id]
        for business, ws_key in self.bp.business_workspaces.items():
            ws = targets[ws_key]
            agents = [a for a in self.bp.agents if a.business == business]
            if not agents:
                continue
            target_id = (self.ledger.get(ws_key).target_id if self.ledger.get(ws_key) else None)
            membership = next((m for m in memberships if (target_id and m.workspace_id == target_id)
                               or (not target_id and (m.workspace_name or "").casefold() in {n.casefold() for n in [ws.name, *ws.former_names]})), None)

            def skip(code: str, reason: str) -> None:  # noqa: B023 - called at once, inside this loop pass
                for agent in agents:
                    out.append(Planned(self.item(f"{agent.key}@{ws_key}", Section.BUSINESS_AGENTS, "agent", f"{agent.name} in {ws.name}",  # type: ignore[attr-defined]
                                                 ItemAction.SKIPPED if code != "not_in_phase" else ItemAction.LATER, code, reason,
                                                 link=Link(view="members"), week=agent.week), f"{agent.key}@{ws_key}", False))

            if self.phase == Phase.PILOT:
                skip("not_in_phase", f"Added when you apply the whole plan, which also creates {ws.name}.")
                continue
            if membership is None and target_id is None:
                skip("workspace_missing", f"{ws.name} does not exist yet. Applying the whole plan creates it; apply again after that to add its agents.")
                continue
            if membership is None:
                skip("not_a_member", f"You are not a member of {ws.name}, so the plan cannot add agents there. Ask an owner of {ws.name} to apply the plan in it, or to invite you.")
                continue
            roles = [membership.role, *(["org_admin"] if "org_admin" in {r.strip().lower() for r in self.context.roles} else [])]
            target = TenantContext(tenant_id=self.context.tenant_id, workspace_id=membership.workspace_id, user_id=self.context.user_id, roles=roles)
            if not has_permission(target, Permission.TASK_CREATE):
                skip("needs_owner", f"Your role in {ws.name} ({membership.role}) cannot add agents. Ask an owner of {ws.name}.")
                continue
            from .setup_plans import Planner

            sub = Planner(self.bp, target, self.phase, self.overwrite, self.options.model_copy(update={"business_workspaces": False, "seat_in_offices": False}))
            for step in sub.agents(only={a.key for a in agents}):
                step.item.key = f"{step.item.key}@{ws_key}"
                step.item.section = Section.BUSINESS_AGENTS
                step.item.name = f"{step.item.name} in {ws.name}"
                step.item.facts = [f"Made in {ws.name}, with that workspace's tools and installed skills.", *step.item.facts]
                step.context = target
                out.append(step)
        return out
