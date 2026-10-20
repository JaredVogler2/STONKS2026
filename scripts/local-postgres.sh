#!/usr/bin/env bash
# Start a throwaway local PostgreSQL cluster for integration tests (no Docker required).
# Usage: scripts/local-postgres.sh start|stop|url   (data dir defaults to .apex-local/pg)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${APEX_PG_DATA:-$ROOT/.apex-local/pg}"
PORT="${APEX_PG_PORT:-54329}"
BIN="${APEX_PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
if [ -z "$BIN" ] && command -v pg_ctl >/dev/null; then BIN="$(dirname "$(command -v pg_ctl)")"; fi
URL="postgres://apex_app:apex_local_only@127.0.0.1:${PORT}/apex"
case "${1:-start}" in
  start)
    [ -n "$BIN" ] || { echo "PostgreSQL server binaries not found; set APEX_PG_BIN" >&2; exit 1; }
    if [ ! -f "$DATA/PG_VERSION" ]; then
      mkdir -p "$DATA"
      if [ "$(id -u)" = "0" ]; then
        id apexpg >/dev/null 2>&1 || useradd -r -M -d "$DATA" apexpg
        chown -R apexpg "$DATA"
        RUN="runuser -u apexpg --"
      else
        RUN=""
      fi
      $RUN "$BIN/initdb" -D "$DATA" -U postgres --auth=trust >/dev/null
    fi
    RUN=""; [ "$(id -u)" = "0" ] && RUN="runuser -u apexpg --"
    if ! $RUN "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then
      $RUN "$BIN/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp -c listen_addresses=127.0.0.1" -l "$DATA/server.log" -w start >/dev/null
    fi
    # emulate Supabase: the app connects as a NON-superuser role that owns the database and has CREATEROLE
    psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -qtAc "SELECT 1 FROM pg_roles WHERE rolname='apex_app'" | grep -q 1 || \
      psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -qc "CREATE ROLE apex_app LOGIN PASSWORD 'apex_local_only' CREATEROLE NOSUPERUSER"
    psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -qtAc "SELECT 1 FROM pg_database WHERE datname='apex'" | grep -q 1 || \
      psql -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -qc "CREATE DATABASE apex OWNER apex_app"
    echo "$URL"
    ;;
  stop)
    RUN=""; [ "$(id -u)" = "0" ] && RUN="runuser -u apexpg --"
    $RUN "$BIN/pg_ctl" -D "$DATA" -m fast stop >/dev/null 2>&1 || true
    ;;
  reset)
    "$0" stop; rm -rf "$DATA"; "$0" start
    ;;
  url) echo "$URL" ;;
  *) echo "usage: $0 start|stop|reset|url" >&2; exit 2 ;;
esac
