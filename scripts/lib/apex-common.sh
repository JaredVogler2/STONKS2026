# Shared helpers for the APEX Pi scripts. Sourced, not executed.
# shellcheck shell=bash disable=SC2034
set -euo pipefail

APEX_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APEX_APP="$APEX_REPO/apps/pi-worker"
APEX_ENV_FILE="${APEX_ENV_FILE:-$HOME/.config/apex/apex.env}"
APEX_STATE_DIR="${APEX_STATE_DIR:-$HOME/.apex}"
APEX_SERVICES=(apex-worker apex-discord-bot)
LEGACY_SERVICE=apex-pi-bot
ASSUME_YES="${ASSUME_YES:-0}"
DRY_RUN="${DRY_RUN:-0}"

if [ -t 1 ]; then BOLD=$'\e[1m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'; else BOLD=""; RED=""; GREEN=""; YELLOW=""; RESET=""; fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s[PASS]%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*"; }
fail() { printf '%s[FAIL]%s %s\n' "$RED" "$RESET" "$*"; }
die()  { fail "$*"; exit 1; }
step() { printf '\n%s== %s ==%s\n' "$BOLD" "$*" "$RESET"; }

# run CMD... : prints the command; skipped under DRY_RUN=1
run() {
  printf '  $ %s\n' "$*"
  if [ "$DRY_RUN" = "1" ]; then return 0; fi
  "$@"
}

confirm() {
  local prompt="$1"
  if [ "$ASSUME_YES" = "1" ]; then return 0; fi
  if [ ! -t 0 ]; then die "$prompt (needs confirmation; re-run interactively or with --yes)"; fi
  read -r -p "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

require_not_root() {
  if [ "$(id -u)" = "0" ]; then die "Run as your normal Pi login user, not with sudo. The script asks for sudo only when it needs it."; fi
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  if [ -x "$HOME/.local/bin/uv" ]; then echo "$HOME/.local/bin/uv"; return; fi
  echo ""
}

service_active() { systemctl is-active --quiet "$1" 2>/dev/null; }
service_exists() { systemctl list-unit-files "$1.service" --no-legend 2>/dev/null | grep -q "$1"; }

# env_has KEY : true when KEY has a non-empty value in the env file (never prints the value)
env_has() {
  [ -f "$APEX_ENV_FILE" ] || return 1
  grep -Eq "^[[:space:]]*$1=[^[:space:]#]+" "$APEX_ENV_FILE"
}
