# APEX Raspberry Pi worker: connected mode, pairing and model sync

`apex worker` is the Pi's market and monitoring process. `apex bot` is the Discord Gateway process
([DISCORD_BOT.md](DISCORD_BOT.md)). Both run under systemd (`apps/pi-worker/deploy/systemd/`), share one state
directory, and make **outbound HTTPS only**. Nothing listens on the Pi. APEX never places orders and never connects
to a brokerage account.

Status: implemented and tested offline. The tests use an in-process fake of the agent API, fake Discord transports,
replay market data and DEMO-signed fixture models. **Not yet verified against a live Vercel deployment, live
Alpaca or Discord.**

## 1. Run modes

| Mode | When | Writes go to | Market data |
|---|---|---|---|
| **Connected** | `apex pair` done, device approved | Signed agent API (cloud Postgres is canonical); a local spool covers outages | Alpaca credentials delivered as an encrypted envelope (manual env fallback) |
| **Offline local** | `APEX_WORKER_OFFLINE=1` | Local SQLite (`apex_local.sqlite3`); the bot delivers from it | Manual `ALPACA_*` env only |
| **DEMO replay** | `apex replay --all`, `apex demo run` | Temporary stores; reports under `$APEX_STATE_DIR/replay/` | Synthetic DEMO fixtures, no keys |

`apex worker --once` runs one pass of every due job and prints the health components.

### Exit codes

The systemd unit sets `RestartPreventExitStatus=2 3`.

| Code | Meaning |
|---|---|
| 0 | Clean stop (SIGTERM/SIGINT, or `--once`) |
| 2 | Configuration error that a restart cannot fix. One "how to fix" line is logged first. Causes: not paired, invalid `AGENT_HMAC_SECRET` / `APEX_MODEL_TRUSTED_KEYS` / `APEX_MODE`, missing `device_keys.json`, or wrong permissions on `device_keys.json` |
| 3 | Another worker holds `$APEX_STATE_DIR/worker.lock` |
| other | Crash; systemd restarts it after 20 s, at most 5 times in 10 minutes |

On SIGTERM a job that is still running gets 20 s and is then cancelled. Every write is transactional, and an
interrupted end-of-day scan simply reruns after the restart.

### Schedule

The schedule follows the XNYS calendar (holidays, half days, DST) and the server's `/config`.

| Job | When |
|---|---|
| heartbeat | Every 60 s. Sends components and resources, never secrets. Replays the spool once the server answers |
| work / outbox bridge | Every 30 s |
| config | At start and every 30 min. Carries cadence, slots, risk policy, strategy toggles (`strategies`), `budgets`, `training` and `models.poll_minutes` |
| intraday | During the regular session at open + k × 15 or 30 min. Latest prints for at most 30 symbols (the IEX Basic cap): open positions first, then open signals, then top candidates |
| end of day | Session close + 15 min, early closes included. Once per session, and never after the next open (a scan published then would be stale). Skipped when the benchmark bars are not fresh |
| nightly | `cadence.nightly_refresh_et` (default 19:30 ET) on session days. Refreshes the universe (SIP bars, chunked) and the open signals |
| models | Every `models.poll_minutes` (default 45). See §3 |
| notices | Hourly: missed training, registry unreachable, model expiring, quota low |

**Fail closed when stale.** When quotes miss the 120 s SLA, signals get `STALE_DATA` annotations and no
price-based alert is sent. After two stale polls in a row, one private `#system-status` STALE_DATA notice is queued
(at most one per hour).

## 2. Pairing (`apex pair CODE --server URL [--name NAME] [--wait SECONDS]`)

1. In the dashboard, open Settings → Devices → Pair a Pi. It shows a one-time code (10 minutes, `XXXX-XXXX`).
2. On the Pi, put `AGENT_HMAC_SECRET` in the env file. It must be the same value as in Vercel; generate it with
   `apex keygen hmac`.
3. Run `apex pair ABCD-EF23 --server https://<dashboard>.vercel.app`. The Pi:
   - generates Ed25519 and X25519 device keys in `$APEX_STATE_DIR/device_keys.json` (mode 0600) and a random agent
     id `agt_…`;
   - signs `"apex-pairing-proof-v1\n" + canonical_json({agent_id, code, server_nonce, x25519})`, where `code` is the
     normalized code and `server_nonce = sha256("apex-pairing|" + code)[:16]`;
   - checks that the server's fingerprint equals its own (it aborts on a mismatch);
   - saves `agent.json` (0600, no secrets) and prints the fingerprint.

   The cross-language test vector is `packages/quant/vectors/pairing.json`. It was checked against the real
   `pairing.ts`, `canonical.ts` and `ed25519.ts` functions.
4. Compare the fingerprint in the dashboard and approve the device. Until then the device is **pending**: heartbeat
   and config work, and everything else answers `DEVICE_PENDING_APPROVAL`. The worker keeps retrying and its writes
   wait in the spool. `--wait 600` polls until you approve.
5. After a revocation, run `apex pair NEW-CODE --server URL --new-identity`.

**Secrets.** `GET /secrets?provider=alpaca` returns an envelope encrypted to this device's X25519 key with a
5-minute expiry. The Pi opens it, keeps the credentials in memory only and acks the version. `/config` and
`/heartbeat` announce `secret_versions.alpaca` (the heartbeat returns `{}` until the device is approved). A newer
version is refetched within one heartbeat and the market adapter is rebuilt; an Alpaca 401/403 also forces a
refetch.

## 3. Model sync: safety rules (laptop-free amendment)

1. **Trust anchor on the Pi.** Signatures are checked only against `APEX_MODEL_TRUSTED_KEYS` from the Pi's env.
   Keys or hints in a server response are ignored.
2. **Verify before download.** The manifest's schema, Ed25519 signature, model kind, feature-schema version,
   declared size (≤ 2 MB), expiry and DEMO/live provenance are all checked before the artifact is fetched.
3. **Verify after download.** The decoded size must match. `ModelStore.install` re-checks the sha256 and signature,
   loads the **JSON-only** model (never pickle, joblib or any code), warms it on probe rows, then swaps the
   `current` symlink atomically. Scanning and Discord never stop.
4. **Health check and rollback.** After activation the model is probed on a fixed grid. On failure it is rolled back
   to `previous` automatically, quarantined (not reinstalled every poll), acked `rolled_back`, and one private
   notice is sent.
5. **Refusals.** Corrupt, unsigned, untrusted, incompatible, expired or oversized releases are acked `rejected` (or
   `expired`) with a reason code. The previous model keeps serving, and one private `#system-status` notice is sent
   per model id.
6. **Fail closed.** Only verified, **unexpired** models are offered to the policy. With none, there is no forecast:
   no probabilities and no `HIGH_CONVICTION`, only UNVALIDATED research setups. A 91/100 quality score is never a
   probability.
7. **Offline or registry down.** The last verified model keeps serving until its documented expiry. On restart the
   on-disk champion is re-verified from scratch, and a tampered file fails closed. After 3 failed polls, one
   registry-unreachable notice per day.

Layout: `$APEX_STATE_DIR/models/strategies/<strategy_id>/{versions/<model_id>/, current, previous}`.
`apex model status|install|rollback --strategy ID` uses the same layout.

## 4. Outbox bridge and the shared SQLite file

In connected mode the server's `notification_outbox` is authoritative. The worker and the bot share
`$APEX_STATE_DIR/apex_local.sqlite3` (WAL; override with `APEX_LOCAL_DB`):

```
server notification_outbox --GET /work (2-min lease)--> worker OutboxBridge
   --> bot-local notification_outbox (same event_key)  +  agent_outbox_bridge (event_key -> server outbox_id)
   --> bot OutboxDelivery (Discord)  --> local state sent | deferred | dead_letter
   --> worker ack_pass --POST /notifications/ack--> delivered | dm_blocked | failed
```

- **Idempotent.** The local insert and the mapping row commit in one transaction. The local `event_key` is UNIQUE,
  so a re-leased row is never queued twice, and a duplicate `delivered` ack is a server no-op.
- **Acks are batched; the server answers per item** (`{state}` or `{error: {code, status}}`). A 404/409/422 item is
  closed without blocking the rest; a transport error retries the whole batch on the next pass.
- **Rendered alerts.** A delivered **CHANNEL** ack carries `rendered`: the message exactly as the bot renders it
  (`clamp_message(render(intent, component_secret).to_discord())`, at most 12 KB), for the dashboard's Alerts page.
  The opt-in member mention header is left out, so member ids never reach the dashboard. DM acks never carry it.
- **DMs stay DMs.** `route.user_id` is rewritten from the server member UUID to the local member id of the same
  Discord user. The route type never changes. A blocked DM is held privately (`deferred` → `dm_blocked`). When it
  is later shown in an ephemeral reply, it is acked `delivered`.
- **Read-only views for the bot.** The worker also mirrors open signals and latest quotes into the same file, so slash
  commands can resolve signal ids. The bot's position calls go to the cloud ledger through `RemotePositionBackend`.
  If that is unreachable, the bot's own spool says "queued - pending sync" and replays later, with idempotency
  keys. History for `/apex_correct`, `/apex_remove` and starter `#n` references (idempotency key `starter:<id>`)
  comes from `GET positions/history`.
- **Member actions.** `RemoteMemberBackend` keeps the local member mirror (delivery, mutes, held DMs) and syncs to
  `POST members`. Prefs and revocations apply locally at once; the cloud copy is pushed now or spooled
  (`discord_member_action`, replayed in order, last write wins). "Delete my data" goes to the cloud **first**: if
  the server cannot be reached, nothing is deleted and the member is told so. Restoring a revoked member is local
  only, because the server has no un-revoke action.
- **Pi notices.** Pi-originated `#system-status` notices are POSTed to `/notices`, so the dashboard records them,
  and they reach the bot through the bridge under the same `event_key`. Only when the server cannot take them
  (unreachable, device not approved) are they queued directly in the local outbox. A notice is never delivered
  twice: one the server queued is not queued locally, and a later server copy of a locally delivered notice
  collapses on the local unique `event_key`.
- **Worker-only state.** `$APEX_STATE_DIR/agent_state.sqlite3` holds the signed-POST spool (FIFO, deduped per key;
  quotes are shed first when full, signals and events never), the cached open-signal view, last notified
  assessments and notice markers.

## 5. Replay scenarios

`apex replay --all` (or `--scenario NAME`) runs 12 deterministic checks and writes JSON reports. Nothing touches
the network.

- normal publish → activation → target
- same-bar stop-first
- gap through stop
- entry never filled → expired
- earnings veto
- stale feed → no alerts plus one notice
- blocked DM held privately
- duplicate Discord interaction
- oversell rejected
- corrupt or unsigned model rejected
- expired model → UNVALIDATED only
- Pi offline → spool replay without duplicates

Environment variables are documented in `apps/pi-worker/.env.example`.
