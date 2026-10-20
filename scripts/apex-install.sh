#!/usr/bin/env bash
# APEX Raspberry Pi installer. Safe to re-run: it never overwrites your env file, asks before every system change,
# and refuses to leave two Discord bots running on one token.
#
#   ./scripts/apex-install.sh            interactive install / repair
#   ./scripts/apex-install.sh --dry-run  show what would happen, change nothing
#   ./scripts/apex-install.sh --yes      accept every prompt (unattended re-install)
#   ./scripts/apex-install.sh --no-services   install code + env only, no systemd units
# shellcheck source=lib/apex-common.sh disable=SC2034
source "$(dirname "$0")/lib/apex-common.sh"

WITH_SERVICES=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --no-services) WITH_SERVICES=0 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown option $arg" ;;
  esac
done

require_not_root
[ "$DRY_RUN" = "1" ] && warn "DRY RUN: nothing will be changed"

step "1/7 Preflight"
[ "$(uname -s)" = "Linux" ] || die "This installer targets Raspberry Pi OS (Linux). On a laptop use: cd apps/pi-worker && uv sync && uv run apex demo"
arch="$(uname -m)"
case "$arch" in aarch64|arm64) ok "64-bit ARM ($arch)" ;; x86_64) warn "x86_64: fine for testing, the guides assume a Raspberry Pi 5" ;; *) die "unsupported CPU $arch (64-bit OS required)" ;; esac
mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem_mb" -lt 1800 ]; then warn "${mem_mb} MB RAM: 4 GB recommended"; else ok "${mem_mb} MB RAM"; fi
free_pct=$(df -P "$HOME" | awk 'NR==2 {print 100-int($5)}')
if [ "$free_pct" -lt 15 ]; then die "only ${free_pct}% disk free; free space before installing"; else ok "${free_pct}% disk free"; fi
if command -v timedatectl >/dev/null 2>&1; then
  if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then ok "clock synchronized (NTP)"; else warn "clock not NTP-synchronized: signed requests are rejected beyond 60 seconds of skew (sudo timedatectl set-ntp true)"; fi
fi
command -v git >/dev/null || die "git missing: sudo apt install -y git"
py=$(command -v python3 || true)
[ -n "$py" ] || die "python3 missing: sudo apt install -y python3"
"$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python 3.11+ required (Raspberry Pi OS Bookworm ships 3.11)"
ok "$("$py" --version)"

UV="$(find_uv)"
if [ -z "$UV" ]; then
  warn "uv (Python package manager) is not installed"
  say "  It installs pinned dependencies from apps/pi-worker/uv.lock. Installing from PyPI into your user account:"
  if confirm "Install uv with 'python3 -m pip install --user --break-system-packages uv'?"; then
    run "$py" -m pip install --user --break-system-packages uv
    UV="$(find_uv)"
  fi
  [ -n "$UV" ] || [ "$DRY_RUN" = "1" ] || die "uv is required. See https://docs.astral.sh/uv/getting-started/installation/"
  UV="${UV:-$HOME/.local/bin/uv}"
fi
ok "uv at $UV"

step "2/7 Starter bot check (one bot per token)"
LEGACY_ACTIVE=0
if service_exists "$LEGACY_SERVICE"; then
  if service_active "$LEGACY_SERVICE"; then LEGACY_ACTIVE=1; warn "the starter bot service '$LEGACY_SERVICE' is RUNNING"; else ok "starter bot service present but stopped"; fi
else
  ok "no starter bot service installed"
fi

step "3/7 Python environment"
run "$UV" --directory "$APEX_APP" sync --frozen --extra discord
ok "dependencies installed from the lock file"

step "4/7 Private settings file"
env_dir="$(dirname "$APEX_ENV_FILE")"
if [ -f "$APEX_ENV_FILE" ]; then
  ok "keeping your existing $APEX_ENV_FILE (never overwritten)"
else
  say "  No settings file yet. A template with placeholders (no secrets) will be created at $APEX_ENV_FILE."
  if confirm "Create it now?"; then
    run mkdir -p "$env_dir"
    run chmod 700 "$env_dir"
    if [ "$DRY_RUN" != "1" ]; then
      umask 077
      cp "$APEX_APP/.env.example" "$APEX_ENV_FILE"
      chmod 600 "$APEX_ENV_FILE"
    fi
    ok "created; edit it with: nano $APEX_ENV_FILE"
  fi
fi
if [ -f "$APEX_ENV_FILE" ]; then
  perms=$(stat -c '%a' "$APEX_ENV_FILE")
  if [ "$perms" != "600" ]; then warn "$APEX_ENV_FILE is mode $perms"; confirm "Restrict it to your user (chmod 600)?" && run chmod 600 "$APEX_ENV_FILE"; fi
fi
run mkdir -p "$APEX_STATE_DIR"
run chmod 700 "$APEX_STATE_DIR"

step "5/7 Token and settings preflight (values are never printed)"
missing=()
for key in DISCORD_BOT_TOKEN DISCORD_GUILD_ID APEX_OWNER_DISCORD_IDS; do env_has "$key" || missing+=("$key"); done
if [ "${#missing[@]}" -gt 0 ]; then
  warn "not set yet in $APEX_ENV_FILE: ${missing[*]} (the Discord bot will not start until they are)"
else
  ok "Discord settings present"
  if [ "$DRY_RUN" != "1" ]; then
    if APEX_ENV_FILE="$APEX_ENV_FILE" APEX_STATE_DIR="$APEX_STATE_DIR" "$UV" --directory "$APEX_APP" run --frozen --no-sync apex bot --dry-run >/dev/null 2>&1; then
      ok "bot configuration validates (dry run)"
    else
      warn "bot dry run reported a problem: run 'cd $APEX_APP && uv run apex bot --dry-run' to see it"
    fi
  fi
fi
if [ -f "$APEX_STATE_DIR/agent.json" ] || env_has APEX_AGENT_ID; then ok "Pi is paired with the dashboard"; else warn "not paired yet: Dashboard -> Settings -> Pi pairing, then 'uv run apex pair CODE --server URL'"; fi

step "6/7 Services"
if [ "$WITH_SERVICES" = "0" ]; then
  warn "skipping systemd units (--no-services)"
else
  if [ "$LEGACY_ACTIVE" = "1" ]; then
    say "  The starter bot uses the same Discord token. APEX replaces it; its saved entries can be imported first:"
    say "    cd $APEX_APP && uv run apex migrate-starter          (preview, writes nothing)"
    say "    cd $APEX_APP && uv run apex migrate-starter --apply  (backs up and imports)"
    if confirm "Stop and disable the starter bot service '$LEGACY_SERVICE' now? (revert: sudo systemctl enable --now $LEGACY_SERVICE)"; then
      run sudo systemctl disable --now "$LEGACY_SERVICE"
    else
      die "Not installing the APEX bot while the starter bot is running (two bots on one token would fight)."
    fi
  fi
  if confirm "Install and enable the apex-worker and apex-discord-bot systemd services?"; then
    for svc in "${APEX_SERVICES[@]}"; do
      tmp="$(mktemp)"
      sed -e "s#@USER@#$(id -un)#g" -e "s#@APP_DIR@#$APEX_APP#g" -e "s#@ENV_FILE@#$APEX_ENV_FILE#g" \
          -e "s#@STATE_DIR@#$APEX_STATE_DIR#g" -e "s#@UV@#$UV#g" "$APEX_APP/deploy/systemd/$svc.service.in" > "$tmp"
      run sudo install -m 0644 "$tmp" "/etc/systemd/system/$svc.service"
      rm -f "$tmp"
    done
    run sudo install -D -m 0644 "$APEX_APP/deploy/journald-apex.conf" /etc/systemd/journald.conf.d/apex.conf
    run sudo systemctl daemon-reload
    run sudo systemctl restart systemd-journald
    run sudo systemctl enable --now apex-worker.service
    if [ "${#missing[@]}" -eq 0 ]; then run sudo systemctl enable --now apex-discord-bot.service; else warn "apex-discord-bot enabled after you fill the Discord settings: sudo systemctl enable --now apex-discord-bot"; fi
  fi
  if confirm "Install the nightly local backup timer (snapshots ~/.apex to ~/apex-backups, keeps 7)?"; then
    tmp="$(mktemp)"
    sed -e "s#@USER@#$(id -un)#g" -e "s#@STATE_DIR@#$APEX_STATE_DIR#g" -e "s#@REPO@#$APEX_REPO#g" -e "s#@BACKUP_ENV@#$HOME/.config/apex/backup.env#g" \
        "$APEX_APP/deploy/systemd/apex-backup.service.in" > "$tmp"
    run sudo install -m 0644 "$tmp" /etc/systemd/system/apex-backup.service
    rm -f "$tmp"
    run sudo install -m 0644 "$APEX_APP/deploy/systemd/apex-backup.timer" /etc/systemd/system/apex-backup.timer
    run sudo systemctl daemon-reload
    run sudo systemctl enable --now apex-backup.timer
    say "  To encrypt snapshots, put APEX_BACKUP_AGE_RECIPIENT=age1... in ~/.config/apex/backup.env (public key only)."
  fi
fi

step "7/7 Health check"
if [ "$DRY_RUN" = "1" ]; then say "  (skipped in dry run)"; else "$APEX_REPO/scripts/apex-doctor.sh" || true; fi
say ""
say "Done. Day-to-day: ./scripts/apex-doctor.sh   updates: ./scripts/apex-update.sh   logs: journalctl -u apex-worker -n 50"
