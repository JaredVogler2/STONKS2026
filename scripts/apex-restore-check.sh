#!/usr/bin/env bash
# Prove a backup can be restored. Decrypts to a temp dir, lists its contents and, if RESTORE_DATABASE_URL points at a
# SCRATCH database (never production), restores it there and runs sanity counts.
#   ./scripts/apex-restore-check.sh ~/apex-backups/supabase-apex-<stamp>.dump.age
# shellcheck source=lib/apex-common.sh
source "$(dirname "$0")/lib/apex-common.sh"
file="${1:-}"
[ -f "$file" ] || die "usage: $0 <backup.age>"
command -v age >/dev/null || die "age is required: sudo apt install -y age"
if [ -f "$file.sha256" ]; then (cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null) && ok "checksum matches" || die "checksum mismatch: file corrupted"; fi
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
umask 077
if [ -n "${APEX_BACKUP_AGE_IDENTITY:-}" ]; then age -d -i "$APEX_BACKUP_AGE_IDENTITY" -o "$tmp/plain" "$file"; else age -d -o "$tmp/plain" "$file"; fi
ok "decrypted"
case "$file" in
  *.dump.age)
    pg_restore --list "$tmp/plain" > "$tmp/toc.txt" || die "not a valid pg_dump archive"
    ok "$(grep -c 'TABLE DATA' "$tmp/toc.txt") tables in archive"
    if [ -n "${RESTORE_DATABASE_URL:-}" ]; then
      case "$RESTORE_DATABASE_URL" in *supabase.co*) confirm "RESTORE_DATABASE_URL looks like Supabase. Restore into it anyway? (use a scratch project)" || exit 1 ;; esac
      pg_restore --dbname="$RESTORE_DATABASE_URL" --no-owner --no-privileges --clean --if-exists "$tmp/plain"
      psql "$RESTORE_DATABASE_URL" -Atc "SELECT 'signals', count(*) FROM apex.signals UNION ALL SELECT 'positions', count(*) FROM apex.positions UNION ALL SELECT 'model_versions', count(*) FROM apex.model_versions UNION ALL SELECT 'audit_events', count(*) FROM apex.audit_events" \
        | sed 's/^/  restored /'
      ok "restore test passed"
    else
      say "Set RESTORE_DATABASE_URL to a scratch Postgres (e.g. ./scripts/local-postgres.sh) for a full restore test."
    fi
    ;;
  *.sqlite3.age)
    python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print('  integrity', c.execute('PRAGMA integrity_check').fetchone()[0]); print('  positions', c.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 'objects')" "$tmp/plain"
    ok "local snapshot readable"
    ;;
esac
