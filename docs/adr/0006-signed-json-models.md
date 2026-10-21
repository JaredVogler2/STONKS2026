# 0006 — Signed JSON models; never pickle

**Decision.**
- A model is a multinomial logistic regression plus isotonic calibrators, serialized as JSON.
- Its manifest (training window, data hash, label definition, feature schema, code SHA, evaluation, validity, artifact size and SHA-256) is signed with Ed25519 over `apex-model-manifest-v1\n` + canonical JSON.
- The Pi trusts only public keys configured on the Pi.
- The Pi verifies, stages and swaps the model atomically, keeps the previous model for rollback, and fails closed.

**Alternatives.** Pickle or joblib: arbitrary code execution on load. ONNX: possible later, after ARM64 runtime review.

**Consequences.** Model families are limited to what can be expressed safely in JSON. That is fine for the current research scale.
