# PHASE 12 — Complete security, correctness, financial research and reliability validation

## Mandatory amendment — October 20, 2026: Laptop-free training

**This amendment supersedes any earlier suggestion that a personal laptop is needed for model retraining or manual Pi model deployment.** Read and implement [Autonomous cloud training amendment](AMENDMENT_2026-10-20_AUTONOMOUS_CLOUD_TRAINING.md). GitHub-hosted Actions runs recurring model training/evaluation, a private versioned model registry stores approved artifacts, and Raspberry Pi autonomously verifies, loads and rolls back models. Integrate into existing work without restarting or overwriting it. Required implementation: Phase 06 train/validation; Phase 11 deployment/refresh; Phase 12 chaos/security; Phase 13 laptop-free E2E acceptance. Offline DEMO must work without accounts. All model promotion must satisfy honest point-in-time/OOS risk and calibration gates; $0 recurring is contingent on quota.

**Additional required test suite:** end-to-end GitHub-hosted training to private signed registry to unattended Pi refresh (mock credentials in DEMO), unchanged champion after failed candidate, corrupted/unsigned artifact rejection, concurrent promotion suppression, GitHub cron missed-run detection, quota exhaustion graceful degradation, Pi offline catchup, no GitHub Actions PR secret exposure, no arbitrary pickle deserialization, verified no recurring laptop steps. Include measured minutes and version checks.

## Agent mission

Act as an independent QA lead and hostile reviewer. Challenge assumptions, test cross-system behavior, verify empirically derived claims, inspect security, correct root causes and prepare **real release evidence**. This is not a checkbox-only review.

## Automated test pyramid and minimum scenarios

### Unit
- Pure feature computation: ATR, average volume, relative strength, seasonality, correlations, gap/returns, timezone conversions, null handling.
- Signal ranking deterministic and no future data references. Null risk factor causes penalty/veto, not randomly high confidence.
- Position lot arithmetic: multiple entries, fractional shares, FIFO allocations, realized vs unrealized, partial exit, full close, correction and fee handling.
- Decimal equality and rounding; property testing negative, NaN, infinity, huge quantities and invalid tickers.
- Session calendar: Good Friday, MLK Day, Juneteenth, Thanksgiving, Christmas observed, half sessions and DST. Use pinned actual exchange schedule; dates chosen should have known actual status.
- Target/stop collision in same OHLC bar; overnight gaps; split factor errors.
- Insight text includes contradictory evidence, source URLs and accurate data age.

### Integration
- Supabase migrations from empty DB and upgrade from previous schema; RLS tests direct and through API.
- HMAC signature algorithm using known fixed vectors and raw-body hash; replay nonce denied; key rotation overlap; revoked device rejected; expiration and clock skew.
- Agent event ingestion idempotency under concurrent submission and partial network failure.
- Signed Pi provisioning: key pair/challenge proof, short expiry and safe encrypted delivery, no plaintext in REST responses to browsers.
- Market provider timeout, 429 Retry-After, partial batch results, missing asset, stale quote, incorrect IEX feed mapping, SEC throttling.
- Outbox: exactly-once user-visible effects where possible; Discord 429, dropped connection, duplicate delivery ack, failed DM, retry to dead letter.
- Member scopes: A/B/C records; no cross-user read via endpoint pagination, lookup, search filters, malformed UUID, audit API, chart views or slash commands.
- Owner app auth distinct from Vercel project protection; all routes fail closed to anonymous and spoofed identity.

### End-to-end
- Clean no-secret clone works in DEMO and visibly labels it.
- Admin configures fake test credentials in isolated local integration fixture, tests/rotates/revokes without leaks.
- One signal published → one Discord embed with correct ID → member A records 20 shares @180, B records 5 shares @190, C watches → each gets different private assessment → A adds 10, sells 15, closes remaining → accurate ledger and personalized notifications → model/global signal performance remains independent.
- Restart Pi midway with unsent events; idempotent recovery/replay and no duplicate trades.
- Two different Discord guilds: only allowlisted guild works; removing member revokes access.
- Discord DMs blocked: private pending state, no public leak.
- Browser 390px iPhone and desktop 1440px; keyboard navigation, settings save, filter state, aria labels, light/dark contrast.
- Production-looking page with NO data presents honest actionable error, not placeholder ticker advice.

## Quantitative/model red-team

- Backtest runs fresh raw data to features and independent expected outcomes; inspect leakage manually in random examples.
- Use deliberately impossible synthetic alpha dataset to confirm model can capture real simulated relationship; use randomized future labels to confirm OOS performance collapses to baseline. If model still reports high confidence on shuffled labels => release FAIL.
- Compare intended entry price vs actual fill model, same-bar target/stop, gaps, latency, fees.
- Verify all reported win percentages map to exact event type and sample denominator; report bootstrap CI and effective independent sample size.
- Check per-sector concentration; train/test leakage when correlated symbols events overlap; assess nonstationary regime splits and strategy parameters tuned too often.
- Evaluate whether calibration remains stable on the latest unseen holdout, and re-run after market/data revisions.
- Validate “NO TRADE” during poor data, no edge, missing tradability, source outages, unvalidated model or market closed.
- Rerun research with higher transaction costs and worse execution: flag fragile edges.
- Insiders and politicians: no pre-publication information; no claim legal insider transaction is real-time; distinguish P vs A/S award codes.
- Report failed model attempts, selection bias and areas where free data cannot support claims. Never optimize final OOS set post hoc.

## Security and compliance adversarial tests

Threat scenarios:
1. Anonymous hits `/api/v1/admin/settings` or `/api/v1/admin/signals` → unauthorized.
2. Revoked Pi attempts signed upload → 403.
3. Replay captured agent request within timestamp window → 409/deny.
4. Malicious Discord member sends another member's lot ID → deny.
5. Member submits fake signal ID / spoofed published message component → deny.
6. Agent sends 50MB payload / malicious JSON nested bombs → safe bound/413.
7. Stored XSS through ticker/news title/notes rendered in Next.js or Discord → escaped.
8. SSRF via configured source URL/webhook; allowlisted HTTPS endpoints only; no localhost/private IPv4/IPv6 fetch.
9. CI untrusted pull request cannot read Vercel env or deploy production.
10. Tokens in git history, logs, crash dumps, exceptions, telemetry, HTML, Next.js bundle and DB backup? Scan all; rotate on detection.
11. Message content displayed to a user after DM-block failure? None.
12. Dashboard not accidentally public at default Vercel production alias. Check preview too.
13. API keys copied through UI form: not echoed back in server response, history, telemetry.
14. Raw SEC/market source content treated as untrusted data; document/news text cannot instruct agent/code execution or override system prompt.

## Reliability/operations chaos

- Kill Python scanner process; systemd restarts; state is consistent.
- Reboot Pi during Discord network partition; queues survive.
- Supabase Free paused/read-only; app becomes degraded (not silently healthy); no new fake persistence success.
- Alpaca Basic IEX stream disconnects during price spike: missed-data backfill; no unverified price-target alert.
- Hit limit for 30-symbol IEX stream: healthy bounded priority rotation; held positions prioritized.
- Fast 5xx from Vercel: retry backoff, no duplicate events; avoid retry storm.
- Clock skew 3 minutes: agent blocked with clear NTP fix, no bypass.
- Disk under 5% free: stop writing raw high-volume bars, preserve positions/outbox.
- Model has expired OOS validation: disable confidence display and new high conviction posts.
- Pi stops beating heartbeat: admin system alert via optional independent scheduler, subject to its own availability.
- Internet restored after 8h: stale state clearly maintained until fresh verified observations and new model evaluation.
- Discord API changes or keys revoked: graceful error + admin action list.

## Performance/cost load test

Use 500+ simulated stocks, 25 IEX priority slots, 20–50 members and up to ~100 individual positions to evaluate Pi 5 4GB, Supabase writes and Vercel quotas. Benchmark memory peak, CPU duty cycle, REST RPM, DB growth/day, Discord sends/day, worst-case latency. Goal: predictable bounded resources rather than marketing “real time”. Include a tested throttle profile. If cannot run benchmark on actual Pi, give measured local machine details and label Pi numbers unverified.

## Gates to pass before any real-data alerts

- Tests pass, fresh data and license allowed.
- Out-of-sample model has defensible positive expectancy after costs with calibration validated.
- Quote provenance and staleness visible in every alert.
- Full privacy/security review passed; production Vercel authenticated.
- Discord group personalized actionable recommendation feature gated OFF until legal/market-data/hosting compliance reviewed.
- Owner has seen DEMO end-to-end including failure scenarios and consent.
- No paid dependency introduced silently.

## Report format

Generate `docs/RELEASE_AUDIT.md` with:
- commit SHA and dependency versions
- exact executed commands + PASS/FAIL counts
- code coverage by domain and missing tests
- replay screenshot/link and synthetic fixtures list
- quant research results & confidence limitations
- vulnerability matrix risk/mitigation/residual
- performance/cost results with hardware details
- actual service provider plan/limit verification date
- signed checklist of owner action required
- list of deferred capabilities NOT marketed as working.

**No fabricated success. Any critical security or model-integrity failure blocks release.**