#!/usr/bin/env bash
# Installs the reusable (global) Claude Code skills into ~/.claude/skills from
# their upstream GitHub repositories, pinned to reviewed commits. Project
# skills live in .claude/skills (committed); these are fetched, never copied
# into this repository (two of the sources publish no licence).
#
# Idempotent and quiet: a skill already at the pinned commit is skipped, and a
# network failure never fails the session (it reports and exits 0).
#
#   scripts/install-agent-skills.sh           # install / update
#   scripts/install-agent-skills.sh --check   # list what is installed
set -u

DEST="${CLAUDE_GLOBAL_SKILLS_DIR:-$HOME/.claude/skills}"
WORK="${TMPDIR:-/tmp}/anum-agent-skills"
STAMP="$DEST/.anum-skills-lock"
mkdir -p "$DEST" "$WORK"

# repo | pinned commit | "src_path:skill_name" ... (a *.md file becomes <name>/SKILL.md)
SOURCES=(
  "jojojobring/claude-skills|ff69789|full-review:full-review owasp-security:owasp-security webapp-testing:webapp-testing"
  "dweinstein/mobile-security-skills|cb7ce51|skills/secure-storage-audit:secure-storage-audit skills/crypto-review:crypto-review skills/auth-assessment:auth-assessment skills/network-security-check:network-security-check skills/platform-interaction-review:platform-interaction-review skills/code-quality-scan:code-quality-scan skills/resilience-assessment:resilience-assessment skills/privacy-audit:privacy-audit skills/mobile-threat-model:mobile-threat-model skills/masvs-checklist:masvs-checklist skills/secure-mobile-dev-guide:secure-mobile-dev-guide skills/mobile-pentest-plan:mobile-pentest-plan"
  "ShadmanSakibRahman/claude-skills-hub|b90edd9|skills/frontend/responsive-design.md:responsive-design-hub skills/frontend/accessibility-audit.md:accessibility-audit skills/frontend/performance-audit.md:performance-audit skills/frontend/design-system.md:design-system skills/frontend/react-component.md:react-component skills/frontend/nextjs-app.md:nextjs-app skills/frontend/form-builder.md:form-builder skills/frontend/state-management.md:state-management"
)

if [ "${1:-}" = "--check" ]; then
  [ -f "$STAMP" ] && cat "$STAMP" || echo "no global skills installed by this script"
  exit 0
fi

ok=0; failed=0
for entry in "${SOURCES[@]}"; do
  IFS='|' read -r repo sha maps <<<"$entry"
  if grep -qs "^$repo $sha\$" "$STAMP"; then continue; fi
  dir="$WORK/$(echo "$repo" | tr '/' '_')"
  if [ ! -d "$dir/.git" ]; then
    if ! timeout 120 git clone -q --filter=blob:none "https://github.com/$repo.git" "$dir" 2>/dev/null; then
      echo "skills: could not fetch $repo (network?) - skipped"; failed=$((failed + 1)); continue
    fi
  fi
  if ! git -C "$dir" -c advice.detachedHead=false checkout -q "$sha" 2>/dev/null; then
    timeout 120 git -C "$dir" fetch -q origin 2>/dev/null
    git -C "$dir" -c advice.detachedHead=false checkout -q "$sha" 2>/dev/null || { echo "skills: $repo@$sha not found - skipped"; failed=$((failed + 1)); continue; }
  fi
  for m in $maps; do
    src="${m%%:*}"; name="${m##*:}"; out="$DEST/$name"
    rm -rf "$out"; mkdir -p "$out"
    if [ -d "$dir/$src" ]; then
      cp -R "$dir/$src/." "$out/"
    elif [ -f "$dir/$src" ]; then
      # Single-file command skills: add the `name:` the skill loader needs.
      if head -1 "$dir/$src" | grep -q '^---$'; then
        { echo '---'; echo "name: $name"; tail -n +2 "$dir/$src"; } > "$out/SKILL.md"
      else
        { echo '---'; echo "name: $name"; echo "description: $name (from $repo)"; echo '---'; cat "$dir/$src"; } > "$out/SKILL.md"
      fi
    else
      echo "skills: $repo has no $src - skipped"; failed=$((failed + 1)); continue
    fi
    printf 'Source: https://github.com/%s/tree/%s/%s\n' "$repo" "$sha" "$src" > "$out/SOURCE.txt"
    ok=$((ok + 1))
  done
  { grep -v "^$repo " "$STAMP" 2>/dev/null; echo "$repo $sha"; } > "$STAMP.tmp" && mv "$STAMP.tmp" "$STAMP"
done
[ "$ok" -gt 0 ] && echo "skills: installed $ok global skill(s) into $DEST"
[ "$failed" -gt 0 ] && echo "skills: $failed source(s) failed; project skills in .claude/skills still work"
exit 0
