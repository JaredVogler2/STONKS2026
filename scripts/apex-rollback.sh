#!/usr/bin/env bash
# Roll the Pi back to an earlier commit (the previous deploy by default) after a local-schema compatibility check.
#   ./scripts/apex-rollback.sh              previous deployed commit (from ~/.apex/deploy_history)
#   ./scripts/apex-rollback.sh <commit>     a specific reviewed commit
#   ./scripts/apex-rollback.sh --list       show deploy history
# shellcheck source=lib/apex-common.sh
source "$(dirname "$0")/lib/apex-common.sh"
require_not_root
cd "$APEX_REPO" || exit 1
hist="$APEX_STATE_DIR/deploy_history"
if [ "${1:-}" = "--list" ]; then [ -f "$hist" ] && tail -n 20 "$hist" || say "no deploy history yet"; exit 0; fi
target="${1:-}"
if [ -z "$target" ]; then
  [ -f "$hist" ] || die "no deploy history; pass a commit id (git log --oneline)"
  target="$(awk '$3=="update" {c=$2} END {print c}' "$hist")"
  [ -n "$target" ] || die "no previous update recorded"
fi
git cat-file -e "${target}^{commit}" 2>/dev/null || die "unknown commit $target (git fetch first?)"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "local code changes present; commit or discard them first"
say "Current: $(git log -1 --oneline HEAD)"
say "Target:  $(git log -1 --oneline "$target")"
"$APEX_REPO/scripts/lib/schema-guard.sh" "$target" || die "rollback refused: restore a backup of $APEX_STATE_DIR taken before the schema change"
confirm "Roll back to this commit and restart APEX?" || exit 0
printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(git rev-parse HEAD)" "rollback-from" >> "$hist"
run git -c advice.detachedHead=false checkout --quiet "$target"
UV="$(find_uv)"
run "$UV" --directory "$APEX_APP" sync --frozen --extra discord
for svc in "${APEX_SERVICES[@]}"; do service_exists "$svc" && run sudo systemctl restart "$svc"; done
ok "rolled back to $(git rev-parse --short HEAD) (detached). Return to updates later with: git checkout main && ./scripts/apex-update.sh"
