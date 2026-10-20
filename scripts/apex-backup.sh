#!/usr/bin/env bash
# Encrypted backups. Supabase Free has no point-in-time recovery, so export what matters yourself.
#   ./scripts/apex-backup.sh --supabase   pg_dump of the apex schema (minus high-volume tables), encrypted with age
#   ./scripts/apex-backup.sh --pi         consistent snapshot of this Pi's local SQLite store (nightly via apex-backup.timer)
# Backups go to ~/apex-backups (0700). NEVER commit or upload them to the repository.
# shellcheck source=lib/apex-common.sh
source "$(dirname "$0")/lib/apex-common.sh"
OUT="${APEX_BACKUP_DIR:-$HOME/apex-backups}"
KEEP="${APEX_BACKUP_KEEP:-7}"
RECIPIENT="${APEX_BACKUP_AGE_RECIPIENT:-}"   # age public key (age1...). Without it you are asked for a passphrase.
mode="${1:-}"
umask 077
mkdir -p "$OUT"; chmod 700 "$OUT"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

encrypt() { # encrypt SRC DEST
  command -v age >/dev/null || die "age is required for encryption: sudo apt install -y age"
  if [ -n "$RECIPIENT" ]; then age -r "$RECIPIENT" -o "$2" "$1"; else age -p -o "$2" "$1"; fi
}

prune() { # prune GLOB
  # shellcheck disable=SC2012
  ls -1t "$OUT"/$1 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
}

case "$mode" in
  --supabase)
    command -v pg_dump >/dev/null || die "pg_dump missing: sudo apt install -y postgresql-client (major version must be >= the Supabase server's)"
    url="${APEX_BACKUP_DATABASE_URL:-}"
    if [ -z "$url" ]; then
      [ -t 0 ] || die "set APEX_BACKUP_DATABASE_URL or run interactively"
      say "Paste the Supabase connection string (Session pooler or direct). It is used once and not stored."
      read -r -s -p "DATABASE_URL: " url; echo
    fi
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    step "Dumping schema apex"
    pg_dump "$url" --schema=apex --format=custom --no-owner --no-privileges \
      --exclude-table-data='apex.quote_events' --exclude-table-data='apex.agent_nonces' --exclude-table-data='apex.agent_heartbeats' \
      --exclude-table-data='apex.delivery_attempts' --file="$tmp/apex.dump"
    pg_restore --list "$tmp/apex.dump" > "$tmp/toc.txt" || die "dump is unreadable"
    ok "$(grep -c 'TABLE DATA' "$tmp/toc.txt") tables captured ($(du -h "$tmp/apex.dump" | cut -f1))"
    step "Encrypting"
    encrypt "$tmp/apex.dump" "$OUT/supabase-apex-$stamp.dump.age"
    sha256sum "$OUT/supabase-apex-$stamp.dump.age" > "$OUT/supabase-apex-$stamp.dump.age.sha256"
    prune 'supabase-apex-*.dump.age'
    ok "saved $OUT/supabase-apex-$stamp.dump.age. Test it: ./scripts/apex-restore-check.sh $OUT/supabase-apex-$stamp.dump.age"
    say "Note: the encryption key or passphrase is required to restore; store it in your password manager, not on this device."
    ;;
  --pi)
    db="${APEX_LOCAL_DB:-$APEX_STATE_DIR/apex_local.sqlite3}"
    [ -f "$db" ] || { warn "no local database at $db yet"; exit 0; }
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    python3 - "$db" "$tmp/apex_local.sqlite3" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)  # consistent online snapshot (WAL-safe)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
sys.exit(0 if ok == "ok" else 1)
PY
    if [ -n "$RECIPIENT" ]; then
      encrypt "$tmp/apex_local.sqlite3" "$OUT/pi-local-$stamp.sqlite3.age"
      prune 'pi-local-*.sqlite3.age'
      ok "encrypted snapshot $OUT/pi-local-$stamp.sqlite3.age"
    else
      cp "$tmp/apex_local.sqlite3" "$OUT/pi-local-$stamp.sqlite3"
      prune 'pi-local-*.sqlite3'
      warn "snapshot stored UNENCRYPTED (owner-only file). Set APEX_BACKUP_AGE_RECIPIENT to an age public key to encrypt."
    fi
    ;;
  *) sed -n '2,6p' "$0"; exit 2 ;;
esac
