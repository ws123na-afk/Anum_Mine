# Skill sources

Project skills Claude Code loads from `.claude/skills/<name>/SKILL.md`. Third-party skills are vendored as plain files after review: each was read in full, contains only Markdown instructions (no scripts, hooks, MCP configuration or credential access) and is pinned to the commit below. Vendored `SKILL.md` files are byte-for-byte copies; to update one, re-review the new upstream version and change the SHA here.

ANUM rules win over vendored guidance: `CLAUDE.md`, `apps/mobile/README.md` (responsive contract: 600 and 840 breakpoints, 200 percent text, 48x48 targets) and `docs/mobile.md` (strict analysis, no `// ignore:` or `as dynamic` to silence findings, `dart format` after `flutter pub get`). Upstream examples that add packages (`provider`, `mockito`, `freezed`) do not apply here.

## Vendored

| Skill | Source | Commit | License |
|---|---|---|---|
| `flutter-add-widget-test` | https://github.com/flutter/skills/tree/0ef3972f93e2baa4156ba1cbb1e515cd53079c68/skills/flutter-add-widget-test | `0ef3972f93e2baa4156ba1cbb1e515cd53079c68` | BSD-3-Clause, The Flutter Authors (`LICENSE` beside it) |
| `flutter-build-responsive-layout` | https://github.com/flutter/skills/tree/0ef3972f93e2baa4156ba1cbb1e515cd53079c68/skills/flutter-build-responsive-layout | `0ef3972f93e2baa4156ba1cbb1e515cd53079c68` | BSD-3-Clause, The Flutter Authors |
| `flutter-fix-layout-issues` | https://github.com/flutter/skills/tree/0ef3972f93e2baa4156ba1cbb1e515cd53079c68/skills/flutter-fix-layout-issues | `0ef3972f93e2baa4156ba1cbb1e515cd53079c68` | BSD-3-Clause, The Flutter Authors |
| `dart-run-static-analysis` | https://github.com/dart-lang/skills/tree/0d9f1c4a0ae29d6f5180bf6143d7997ec3bacf49/skills/dart-run-static-analysis | `0d9f1c4a0ae29d6f5180bf6143d7997ec3bacf49` | BSD-3-Clause, the Dart project authors |

`flutter/skills` is the Flutter team's agent plugin repository (its plugin manifest names `https://github.com/flutter/agent-plugins`); `dart-lang/skills` is the Dart team's. The Flutter repository's Dart skills are synced copies of `dart-lang/skills` at the commit above.

## ANUM's own

| Skill | Purpose |
|---|---|
| `anum-verify`, `steward`, `anum-release-gate` | Local checks, red CI, release evidence. |
| `mockup-to-flutter` | Mockup image to an ANUM Flutter screen. Written here because no official or well-known image-to-Flutter skill was found (the Figma MCP design-to-code workflow covers Figma links only). |

## Reviewed and not vendored

- **Flutter AI rules file** (`docs/rules/rules.md` in `flutter/flutter`): withdrawn upstream. At `flutter/flutter` commit `33aadb51de4a494175c745a1d9101d9ef22ee53e` the directory's README says it "no longer contains rule files for AI coding assistants" and points to agent plugins and skills instead, so there is no `apps/mobile/AI_RULES.md`. `docs.flutter.dev` was not reachable from the sandbox.
- **Dart and Flutter MCP server** (`dart mcp-server`, ships with the Dart SDK; the `flutter/skills` Claude plugin configures it): not configured in the repo, because it is a per-developer tool that works best against a running app, and starting the `dart` tool in this sandbox tried to reach Google analytics endpoints (blocked here). To use it locally: `claude mcp add --scope user dart -- dart mcp-server`. `flutter-fix-layout-issues` and `dart-run-static-analysis` mention its tools and fall back to the CLI.
- **The `flutter/skills` Claude plugin as a whole**: its `.agents/hooks.json` runs Dart scripts on every Stop and it registers the MCP server; we take only the reviewed skill files.
- Other `flutter/skills` skills: `flutter-apply-architecture-best-practices` (prescribes `freezed`, `provider`/`get_it` and a different folder layout), `flutter-setup-localization` (`gen-l10n`; ANUM uses `AnumLocalizations`), `flutter-setup-declarative-routing` (`go_router`, not used), `dart-add-unit-test` and `dart-generate-test-mocks` (`mockito` + `build_runner`; ANUM uses `mocktail`), `dart-fix-runtime-errors` (description does not match its content and it duplicates static analysis), `flutter-add-integration-test` (Flutter Driver through the MCP server), `flutter-add-widget-preview`, `flutter-use-http-package`, `flutter-implement-json-serialization` (ANUM has `ApiClient` and `JsonReader`), and the FFI, CLI, path, pattern, constructor, coverage, documentation and `checks` skills (not relevant now).
- **Anthropic skills** (`anthropics/skills` at `dbd4588f9e1033efb41dad4bef2f7947c8993d44`, Apache-2.0 for these):
  - `frontend-design`: already enabled through the official `frontend-design@claude-plugins-official` plugin in `.claude/settings.json`; a second copy would duplicate it.
  - `webapp-testing`: Python Playwright (not installed here) with a helper that starts servers through `shell=True`; ANUM's browser checks use the web app's TypeScript Playwright setup with `/opt/pw-browsers/chromium` and a mocked API.
  - `mcp-builder`: ANUM consumes MCP servers through its mediated adapter rather than building them; the skill runs `npx @modelcontextprotocol/inspector` unpinned, fetches SDK READMEs from `main`, and its evaluation script installs packages and needs an Anthropic API key.
  - `skill-creator`: its eval viewer loads a script from `cdn.sheetjs.com` and its scripts launch `claude -p` subprocesses. It is available from Anthropic's `example-skills` plugin and in claude.ai for anyone who needs it.
  - Document-format and creative skills (`docx`, `pdf`, `pptx`, `xlsx`, art, GIF, brand, theme): not useful for this repository.
