#!/usr/bin/env bash
# schema-guard.sh <commit>: refuse to run code whose local SQLite schema is OLDER than the data already on this Pi
# (newer code migrates forward automatically; older code cannot read newer data safely).
# shellcheck source=apex-common.sh
source "$(dirname "$0")/apex-common.sh"
target="$1"
db="${APEX_LOCAL_DB:-$APEX_STATE_DIR/apex_local.sqlite3}"
code_ver="$(git -C "$APEX_REPO" show "$target:apps/pi-worker/src/apex_worker/storage/local.py" 2>/dev/null | sed -n 's/^SCHEMA_VERSION = \([0-9]*\).*/\1/p' | head -1)"
[ -n "$code_ver" ] || { warn "cannot read SCHEMA_VERSION at $target; skipping check"; exit 0; }
if [ ! -f "$db" ]; then ok "no local database yet (schema check not needed)"; exit 0; fi
data_ver="$(python3 - "$db" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    print(row[0] if row else "")
except Exception:
    print("")
PY
)"
[ -n "$data_ver" ] || { warn "local database has no schema_version; skipping check"; exit 0; }
if [ "$code_ver" -lt "$data_ver" ]; then
  fail "target code expects local schema v$code_ver but this Pi's data is v$data_ver"
  exit 1
fi
ok "local schema compatible (code v$code_ver, data v$data_ver)"
