# Third-party skills

Project skills (committed in `.claude/skills`):
- `frontend-design`: Anthropic's frontend-design skill from the Claude Code plugins
  (https://github.com/anthropics/claude-code/tree/main/plugins/frontend-design), © Anthropic PBC,
  subject to Anthropic's terms. Unchanged. The same skill is also enabled as the
  `frontend-design@claude-plugins-official` plugin.
- `frontend-visual-qa` (SKILL.md, references, scripts): vendored from
  https://github.com/daymade/claude-code-skills/tree/main/frontend-visual-qa (MIT License, see
  `.claude/skills/frontend-visual-qa/LICENSE`). Unchanged; the evals and tests folders were left out.
- `ui-ux-pro-max`: vendored from https://github.com/nextlevelbuilder/ui-ux-pro-max-skill (MIT License,
  see `.claude/skills/ui-ux-pro-max/LICENSE`). Its scripts only read the local CSV/JSON data.
- `design-accessibility-review`, `design-critique`, `design-ux-copy`, `eng-code-review`,
  `eng-tech-debt`, `eng-testing-strategy`, `eng-deploy-checklist`: adapted from the Design and
  Engineering plugins in https://github.com/anthropics/knowledge-work-plugins (Apache License 2.0).
  Changes: `design-`/`eng-` prefixes, connector notes removed.
- Taste skills `design-taste-frontend`, `redesign-existing-projects`, `high-end-visual-design`: from
  https://github.com/Leonxlnx/taste-skill (hashes in `/skills-lock.json`). Instruction text only, no
  scripts. No licence file upstream. Unchanged. ANUM's rules and design tokens win over their defaults.

## Global skills (fetched, not vendored)

`scripts/install-agent-skills.sh` installs these into `~/.claude/skills` at pinned commits. A
SessionStart hook in `.claude/settings.json` re-runs it, because cloud containers start with an empty
home folder. A network failure never fails the session.
- https://github.com/jojojobring/claude-skills @ff69789: `full-review`, `owasp-security` (no licence
  upstream), `webapp-testing` (Apache License 2.0, Anthropic's skill).
- https://github.com/dweinstein/mobile-security-skills @cb7ce51: `secure-storage-audit`, `crypto-review`,
  `auth-assessment`, `network-security-check`, `platform-interaction-review`, `code-quality-scan`,
  `resilience-assessment`, `privacy-audit`, `mobile-threat-model`, `masvs-checklist`,
  `secure-mobile-dev-guide`, `mobile-pentest-plan` (no licence upstream).
- https://github.com/ShadmanSakibRahman/claude-skills-hub @b90edd9 (CC BY 4.0, © ShadmanSakibRahman):
  `accessibility-audit`, `performance-audit`, `design-system`, `react-component`, `nextjs-app`,
  `form-builder`, `state-management`, and `responsive-design` installed as `responsive-design-hub`.
  Change: a `name:` line is added to each single-file skill so the loader registers it.
