#!/usr/bin/env bash
# Update the Pi to the latest reviewed commit on main, with an automatic rollback if the services do not come back.
#   ./scripts/apex-update.sh           show incoming commits, confirm, update, restart, verify
#   ./scripts/apex-update.sh --yes     no prompts (for an unattended timer you set up yourself)
# shellcheck source=lib/apex-common.sh disable=SC2034
source "$(dirname "$0")/lib/apex-common.sh"
for arg in "$@"; do case "$arg" in --yes|-y) ASSUME_YES=1 ;; --dry-run) DRY_RUN=1 ;; *) die "unknown option $arg" ;; esac; done
require_not_root
cd "$APEX_REPO" || exit 1
UV="$(find_uv)"; [ -n "$UV" ] || die "uv not found: run ./scripts/apex-install.sh"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  die "local code changes present (git status). Commit or discard them first; updates never overwrite your edits."
fi
branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || warn "on '$branch', not main"
before="$(git rev-parse HEAD)"

step "Fetching"
run git fetch --quiet origin main
incoming="$(git log --oneline HEAD..origin/main)"
if [ -z "$incoming" ]; then ok "already up to date ($(git rev-parse --short HEAD))"; exit 0; fi
say "Incoming commits:"; printf '%s\n' "$incoming" | sed 's/^/  /'
"$APEX_REPO/scripts/lib/schema-guard.sh" origin/main || die "the new version is not compatible with this Pi's local data (see above)"
confirm "Apply these updates and restart APEX?" || { say "No changes made."; exit 0; }

mkdir -p "$APEX_STATE_DIR"
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$before" "update" >> "$APEX_STATE_DIR/deploy_history"

rollback() {
  fail "update failed: $1. Rolling back to ${before:0:12}"
  git merge --abort 2>/dev/null || true
  git reset --hard --quiet "$before"
  "$UV" --directory "$APEX_APP" sync --frozen --extra discord --quiet || true
  for svc in "${APEX_SERVICES[@]}"; do service_exists "$svc" && sudo systemctl restart "$svc" || true; done
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$before" "auto-rollback" >> "$APEX_STATE_DIR/deploy_history"
  exit 1
}

step "Updating"
run git merge --ff-only --quiet origin/main || rollback "fast-forward not possible"
run "$UV" --directory "$APEX_APP" sync --frozen --extra discord || rollback "dependency install failed"
step "Quick self-test"
run "$UV" --directory "$APEX_APP" run --frozen --no-sync pytest -q -x -p no:cacheprovider tests/test_ledger.py tests/test_agent_security.py tests/test_model_registry.py \
  || rollback "self-test failed"

step "Restarting"
for svc in "${APEX_SERVICES[@]}"; do service_exists "$svc" && run sudo systemctl restart "$svc"; done
[ "$DRY_RUN" = "1" ] || sleep 25
for svc in "${APEX_SERVICES[@]}"; do
  if service_exists "$svc" && systemctl is-enabled --quiet "$svc" 2>/dev/null && ! service_active "$svc"; then rollback "$svc did not stay up"; fi
done
ok "updated $(git rev-parse --short "$before") -> $(git rev-parse --short HEAD)"
"$APEX_REPO/scripts/apex-doctor.sh" --offline || true
