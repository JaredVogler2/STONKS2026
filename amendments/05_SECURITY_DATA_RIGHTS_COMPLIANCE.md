# Amendment 05 — Security, Data Entitlements, Financial Advice and Multi-user Privacy

## Purpose

APEX shares research signals to a private Discord server and tracks each member's independently reported financial positions. “Private group,” “free API,” and “not executing orders” do not automatically eliminate data redistribution rules, investment-adviser registration concerns, exchange licensing requirements or software host acceptable-use restrictions. This is a technical/legal review plan, not legal clearance.

## 1. Asset inventory and threat model

Assets: Alpaca keys, Discord bot token/application secrets, Supabase service role and encryption key, Pi/HMAC device secrets, model Ed25519 signing keys, private training artifacts, self-reported lots and fills, P&L, user role/Discord ID, notification subscriptions, inference evidence and strategy logic.

Adversaries: public internet unauthenticated user; removed server member; server owner with wrong privileges; malicious member tricking button custom ID; forged Pi HTTP event; compromised GitHub Actions third-party action; malicious news source prompt injection/XSS; accidental public Vercel preview; lost Pi microSD; accidentally shared API keys; unauthorized model release; service outage.

Boundaries:
- **Vercel Authentication is perimeter**, not sole app authorization; owner identity must be verified with trustworthy signed claims or an explicitly configured trusted auth provider. Anonymous defaults denied for all privileged APIs.
- **Pi outbound-only**: no router port forwarding. Machine API uses HMAC over method/path/raw body hash/time/nonce, nonce persistence, revocation and TLS.
- **Discord Gateway**: actual member ID from event, allowlisted guild, authorized interactions; button custom ID maps to published immutable signal; do not trust user-supplied user IDs or guessed lot IDs.
- **Supabase**: explicit RLS/denied anon and server-ownership checks. Direct client must not reach service-role key. Exports/admin support require audit and member consent.
- **GitHub Actions**: environment-scoped token/signing key only in trusted reviewed default branch, least permissions, pinned actions, no secrets on PR/fork/untrusted branch.
- **Model artifacts**: checksum+trusted signature+schema/expiry, bounded JSON/approved ONNX only. No Python pickle as untrusted remote artifact. Atomic activation and safe rollback.
- **External source content**: metadata/filing text/headlines are untrusted, never instructions, HTML escaped and network URLs allowlisted to prevent SSRF.

## 2. Privacy design

### Group research channels
General shared alerts may contain symbol, source timestamps, strategy evidence, publicly licensed market context and risk levels if allowed. NEVER contain recipient member's fill, quantity, broker account, hypothetical budget, unrealized P&L or private alerts.

### Private interactions
`Record My Entry` opens ephemeral modal; server persists immutable lot if authorized, ephemeral ack includes personalized state. Persistent updates use private Discord DM only with user opt-in or authenticated portal. DMs can be blocked; preserve pending notices and offer private retrieval, never fallback to public channel. Plain “Bought 20 @...” replies are NOT private; do not encourage without explicit user warning and approved privacy design.

### Data minimization
Use Discord ID as an identifier only where needed; safe pseudonymous aggregates and metrics. Do not track credentials to Robinhood or execute brokers. Member may self-report fills but the app must explicitly label them “self-reported” and permit correction. Per-member export/delete with legal retention exceptions and audit. Prune high-volume quote ticks, protect logs, encrypt backups, scrub debug traces/screenshots.

## 3. Market-data rights audit

For each data provider/source:
- Account plan, endpoint, live vs delayed entitlements, display vs non-display rights, permissible private downstream display, retention, caching, historical redistribution and limits.
- IEX single venue is not full consolidated quote or NBBO. Number of IEX subscriptions, stale handling and display labels.
- Are price levels (targets/stops) computed outputs considered derived data under vendor terms? Get provider-specific guidance, do not assume “derived” means freely redistributable.
- Check exchange terms separately from Alpaca account permissions if needed; a free personal-use API key may not license redistribution to friends.
- If group display prohibited, disable quote-containing shared alerts and use permitted abstract signal/event notices where lawful, or ask owner to obtain suitable rights. No hiding quotation as screenshot to evade licensing.
- SEC published filings and publicly accessible reports have their own fair-access, extraction, accuracy and citation constraints.
- News snippets may be copyrighted; source-link and metadata rather than full reproductions.

Output a source-rights matrix `provider | evidence URL+checked date | endpoint/feed | personal/non-display/display/group rights | latency | volume quota | storage | whether permitted | fallback`. Update on release and materially changed provider plans.

## 4. Financial advice boundary and consent

Multi-member tailored “ADD NOW / SELL NOW / position size” can be personal investment advice regardless of no subscription or autoplace. Seek qualified advice on applicable US federal/state regulation, broker-dealer/adviser obligations, compensation, publication vs personalized advice, and Discord distribution audience. Do not pretend a disclaimer, friend group or admin toggle automatically solves it.

Default release profiles:
- `DEMO` deterministic fictional tickers and educational alerts.
- `PAPER_LIVE_DATA` actual data only where licensed; paper outcomes; research phrasing.
- `PERSONAL_RESEARCH` only owner/admin where permitted.
- `GROUP_ACTIONABLE` **disabled** until provider + legal + hosting reviews completed and configured compliance release approval exists. Additional authentication/access permission required beyond a Boolean toggle.

Position tracking may still be permitted for private recordkeeping, but trade-specific action recommendations must be separately gated. Verify before release.

## 5. Vercel Hobby and cloud account conditions

Confirm production/protected preview deployment is private and Hobby usage remains personal/noncommercial under current terms. Private server and no payment do not guarantee Hobby eligibility where functionality distributes investment guidance to others. If terms not satisfied, stop live deployment and propose zero-software-subscription self-hosted alternatives (Pi reverse-proxied via allowed zero-cost access if secure, or other eligible host), with realistic uptime/operating cost. Never silently upgrade to paid provider.

## 6. Security test catalog

1. Web anonymous requests against ALL routes (including preview, custom alias and redirects) denied.
2. Owner-auth bypass spoofing email/header/Next.js direct route invocation denied.
3. Member A attempts to export/edit member B's lot via Discord component and API IDOR denied.
4. Revoked server member cannot create/modify/inspect private records.
5. Discord blocked DM is queued privately, not sent to channel.
6. API HMAC replay, stale timestamp, nonce reuse, wrong path/altered body, device revoked denied.
7. AES ciphertext tampered/wrong key version fails closed without plaintext.
8. Canary token in logs, frontend bundle, source, GitHub Actions artifacts and Discord all absent.
9. Unreviewed GitHub PR cannot access model signing keys or production deploy tokens.
10. Model tampered/downgraded/incompatible/expired refused; rollback only to trusted version.
11. SEC/title/note XSS and malicious URL SSRF blocked.
12. Database policy tests: row ownership, function grants, event immutability, aggregate leak/pagination leaks.
13. Rate limit brute-force config tests and bounded request body.
14. Request deletion/export from member A leaves B data unchanged, logs redacted.

## 7. Deliverables and sign-off

`docs/SECURITY_COMPLIANCE_AUDIT.md`: threat table, reproducible tests, secret key inventory by location (never values), network diagram, permission matrix, retention, source-rights matrix, unresolved legal questions, host terms assessment, individual member consent text, environment-specific allowed capabilities. Requires human owner approval where provider/legal rights cannot be solved by code. No false “compliant” claim without independent review.