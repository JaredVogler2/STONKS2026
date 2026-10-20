#!/usr/bin/env bash
# APEX health check for the Raspberry Pi. Read-only, prints no secret values, and ends with a specific fix per problem.
#   ./scripts/apex-doctor.sh            full check (includes `apex doctor --network`)
#   ./scripts/apex-doctor.sh --offline  skip network probes
# shellcheck source=lib/apex-common.sh
source "$(dirname "$0")/lib/apex-common.sh"
set +e
NETWORK=1
[ "${1:-}" = "--offline" ] && NETWORK=0
problems=0
bad() { fail "$1"; say "       fix: $2"; problems=$((problems + 1)); }

step "System"
temp_raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
if [ -n "$temp_raw" ]; then t=$((temp_raw / 1000)); if [ "$t" -ge 80 ]; then bad "CPU ${t} °C (throttling)" "check the fan and case airflow"; else ok "CPU ${t} °C"; fi; fi
if command -v vcgencmd >/dev/null 2>&1; then
  thr=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
  if [ -n "$thr" ] && [ "$thr" != "0x0" ]; then bad "power/thermal throttling flags $thr" "use the official 27 W USB-C supply; see raspberry-pi/06_OPERATIONS_TROUBLESHOOTING.md"; else ok "no under-voltage or throttling"; fi
fi
avail=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
if [ "$avail" -lt 300 ]; then bad "only ${avail} MB memory available" "restart services; check 'systemctl status apex-worker' memory"; else ok "${avail} MB memory available"; fi
swap_used=$(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {print int((t-f)/1024)}' /proc/meminfo)
[ "$swap_used" -gt 512 ] && warn "${swap_used} MB swap in use (SD-card wear; consider fewer symbols)" || ok "swap ${swap_used} MB"
free_pct=$(df -P "$HOME" | awk 'NR==2 {print 100-int($5)}')
if [ "$free_pct" -lt 10 ]; then bad "${free_pct}% disk free" "sudo journalctl --vacuum-size=100M; delete old backups in ~/apex-backups"; else ok "${free_pct}% disk free"; fi
if command -v timedatectl >/dev/null 2>&1; then
  if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then ok "clock synchronized"; else bad "clock not synchronized" "sudo timedatectl set-ntp true (signed requests fail beyond 60 s of skew)"; fi
fi

step "Services"
if command -v systemctl >/dev/null 2>&1; then
  for svc in "${APEX_SERVICES[@]}"; do
    if service_active "$svc"; then
      restarts=$(systemctl show "$svc" -p NRestarts --value 2>/dev/null)
      ok "$svc running (restarts since boot: ${restarts:-0})"
    elif service_exists "$svc"; then
      code=$(systemctl show "$svc" -p ExecMainStatus --value 2>/dev/null)
      case "$code" in
        2) bad "$svc stopped: configuration error" "journalctl -u $svc -n 30 shows which setting; fix $APEX_ENV_FILE then: sudo systemctl restart $svc" ;;
        3) bad "$svc stopped: another instance holds the lock" "ps aux | grep 'apex '; stop the manual copy, then sudo systemctl restart $svc" ;;
        4) bad "$svc stopped: the starter bot is still running" "sudo systemctl disable --now $LEGACY_SERVICE; sudo systemctl restart $svc" ;;
        *) bad "$svc not running (last exit ${code:-?})" "journalctl -u $svc -n 50 --no-pager; sudo systemctl reset-failed $svc; sudo systemctl restart $svc" ;;
      esac
    else
      warn "$svc not installed (run ./scripts/apex-install.sh)"
    fi
  done
  if service_active "$LEGACY_SERVICE" && service_active apex-discord-bot; then
    bad "starter bot AND APEX bot both running on one token" "sudo systemctl disable --now $LEGACY_SERVICE"
  elif service_active "$LEGACY_SERVICE"; then
    warn "starter bot '$LEGACY_SERVICE' is running (fine until you switch to APEX)"
  fi
  errs=$(journalctl -u apex-worker -u apex-discord-bot --since "-1h" -p err --no-pager -q 2>/dev/null | wc -l)
  [ "${errs:-0}" -gt 0 ] && warn "$errs error log lines in the last hour: journalctl -u apex-worker -u apex-discord-bot -p err --since -1h" || ok "no error log lines in the last hour"
fi

step "Settings (presence only)"
if [ -f "$APEX_ENV_FILE" ]; then
  perms=$(stat -c '%a' "$APEX_ENV_FILE")
  [ "$perms" = "600" ] && ok "$APEX_ENV_FILE is private (600)" || bad "$APEX_ENV_FILE is mode $perms" "chmod 600 $APEX_ENV_FILE"
  for key in DISCORD_BOT_TOKEN DISCORD_GUILD_ID APEX_OWNER_DISCORD_IDS; do env_has "$key" && ok "$key set" || warn "$key not set"; done
  if grep -Eq '^[[:space:]]*ALPACA_(KEY_ID|SECRET_KEY)=[^[:space:]#]+' "$APEX_ENV_FILE"; then warn "Alpaca keys are in the env file; prefer Dashboard -> Settings (delivered encrypted to this Pi)"; fi
else
  bad "no settings file at $APEX_ENV_FILE" "./scripts/apex-install.sh creates a template"
fi
[ -f "$APEX_STATE_DIR/agent.json" ] && ok "paired (identity in $APEX_STATE_DIR/agent.json)" || warn "not paired with the dashboard yet"

step "Network exposure"
if command -v ss >/dev/null 2>&1; then
  listening=$(ss -Hltn 2>/dev/null | awk '{print $4}' | grep -Ev '^(127\.|\[::1\]|::1)' | sed 's/.*://' | sort -un | tr '\n' ' ')
  extra=$(echo "$listening" | tr ' ' '\n' | grep -Ev '^(22|)$' | tr '\n' ' ')
  if [ -n "${extra// }" ]; then warn "ports listening on the network besides SSH: $extra (APEX itself never listens)"; else ok "no inbound ports besides SSH (APEX only connects outbound)"; fi
fi

step "APEX application checks"
UV="$(find_uv)"
if [ -n "$UV" ]; then
  args=(--directory "$APEX_APP" run --frozen --no-sync apex doctor)
  [ "$NETWORK" = "1" ] && args+=(--network)
  APEX_ENV_FILE="$APEX_ENV_FILE" APEX_STATE_DIR="$APEX_STATE_DIR" "$UV" "${args[@]}" || problems=$((problems + 1))
else
  bad "uv not found" "./scripts/apex-install.sh"
fi

echo
if [ "$problems" -eq 0 ]; then ok "all checks passed"; else fail "$problems problem(s) above, each with a fix"; fi
exit $(( problems > 0 ? 1 : 0 ))
