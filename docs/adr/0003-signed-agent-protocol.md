# 0003 — Signed agent protocol and device pairing

**Context.** The Pi talks to the API across the internet. Vercel Authentication protects the deployment, but its bypass header is a single static secret.

**Decision.**
- Every Pi request is signed with HMAC-SHA256 over `APEX-HMAC-SHA256-V1\nagent\nMETHOD\npath?query\ntimestamp\nnonce\nsha256(body)`. The server checks a 60-second clock window and single-use nonces.
- Pairing uses a one-time code plus an Ed25519 proof of the Pi's own key over canonical JSON. The owner compares fingerprints before approving.
- Secrets reach the Pi only as X25519-HKDF-SHA256-AES-256-GCM envelopes to its device key.
- The bypass header is accepted only on machine routes, and signatures are still required there.
- The Pi opens no ports.

**Alternatives.**
- Static API key: replayable, no device identity.
- mTLS: not available on Vercel Hobby functions.
- Tunnels into the Pi: inbound exposure.

**Consequences.** Python and TypeScript must produce identical bytes. Shared vectors in `packages/quant/vectors/` pin HMAC, envelopes, pairing and the ledger.
