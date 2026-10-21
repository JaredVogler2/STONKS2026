# 0009 — Transactional outbox and private delivery

**Decision.**
- Every notification is an outbox row written in the same transaction as the event that caused it, with a unique event key.
- The bot leases rows, sends them with a stable nonce, and acknowledges. Retries honour Discord's `retry-after`; repeated failures go to a dead letter that can be requeued.
- Personal updates have a DM route bound to their owner. They can never carry a rendered public body, and the bot refuses to post them to channels.
- When DMs are blocked, the update is held (`deferred`) and shown privately in the member's next bot reply.
- An owner STOP switch withholds all investment notices.

**Consequences.** At-least-once delivery with idempotent effects. Exactly-once user-visible posts depend on Discord honouring the nonce, which the bot also guards against locally.
