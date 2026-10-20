"""Discord message limits, enforced on EVERY outgoing payload (shared alerts, DMs and ephemeral replies).

Limits (Discord API, 2026): content 2000 chars; at most 10 embeds per message and 6000 characters across all of
them; embed title 256, description 4096, 25 fields, field name 256 / value 1024, footer 2048, author 256;
5 component rows x 5 buttons; button label 80; custom_id 100; modal title 45, text-input label 45,
placeholder 100, 5 inputs.
"""

from __future__ import annotations

import copy

CONTENT_MAX = 2000
EMBEDS_MAX = 10
EMBED_TOTAL_MAX = 6000
TITLE_MAX = 256
DESCRIPTION_MAX = 4096
FIELDS_MAX = 25
FIELD_NAME_MAX = 256
FIELD_VALUE_MAX = 1024
FOOTER_MAX = 2048
AUTHOR_MAX = 256
ROWS_MAX = 5
BUTTONS_PER_ROW = 5
BUTTON_LABEL_MAX = 80
CUSTOM_ID_MAX = 100
MODAL_TITLE_MAX = 45
INPUT_LABEL_MAX = 45
PLACEHOLDER_MAX = 100
MODAL_INPUTS_MAX = 5
CHOICES_MAX = 25
CHOICE_NAME_MAX = 100
EPHEMERAL = 64


def clip(text: str | None, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def embed_length(embed: dict) -> int:
    """Characters Discord counts towards the 6000 limit."""
    n = len(embed.get("title") or "") + len(embed.get("description") or "")
    n += len((embed.get("footer") or {}).get("text") or "") + len((embed.get("author") or {}).get("name") or "")
    for f in embed.get("fields") or []:
        n += len(f.get("name") or "") + len(f.get("value") or "")
    return n


def clamp_embed(embed: dict, budget: int = EMBED_TOTAL_MAX) -> dict:
    e = copy.deepcopy(embed)
    if "title" in e:
        e["title"] = clip(e["title"], TITLE_MAX)
    if "description" in e:
        e["description"] = clip(e["description"], DESCRIPTION_MAX)
    if e.get("footer"):
        e["footer"] = {**e["footer"], "text": clip(e["footer"].get("text", ""), FOOTER_MAX)}
    if e.get("author"):
        e["author"] = {**e["author"], "name": clip(e["author"].get("name", ""), AUTHOR_MAX)}
    fields = [{**f, "name": clip(f.get("name") or "-", FIELD_NAME_MAX), "value": clip(f.get("value") or "-", FIELD_VALUE_MAX)}
              for f in (e.get("fields") or [])[:FIELDS_MAX]]
    e["fields"] = fields
    while embed_length(e) > budget and e["fields"]:
        e["fields"].pop()
    if embed_length(e) > budget and e.get("description"):
        over = embed_length(e) - budget
        e["description"] = clip(e["description"], max(1, len(e["description"]) - over))
    if not e["fields"]:
        e.pop("fields")
    return e


def fit_embeds(embeds: list[dict], budget: int = EMBED_TOTAL_MAX, max_count: int = EMBEDS_MAX) -> tuple[list[dict], list[dict]]:
    """Greedy: keep embeds (in order) while the message total stays within budget. Returns (kept, overflow)."""
    kept: list[dict] = []
    overflow: list[dict] = []
    used = 0
    for e in embeds:
        e2 = clamp_embed(e)
        n = embed_length(e2)
        if len(kept) < max_count and used + n <= budget:
            kept.append(e2)
            used += n
        else:
            overflow.append(e)
    return kept, overflow


def clamp_components(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows[:ROWS_MAX]:
        items = []
        for c in row.get("components", [])[:BUTTONS_PER_ROW]:
            c2 = dict(c)
            if "label" in c2:
                c2["label"] = clip(c2["label"], BUTTON_LABEL_MAX)
            if len(c2.get("custom_id", "")) > CUSTOM_ID_MAX:
                continue  # never send a truncated (and therefore unverifiable) custom id
            items.append(c2)
        if items:
            out.append({"type": 1, "components": items})
    return out


def clamp_message(msg: dict) -> dict:
    """Return a copy of a Discord message payload that satisfies every size limit."""
    out = dict(msg)
    out["content"] = clip(msg.get("content") or "", CONTENT_MAX)
    kept, _overflow = fit_embeds(list(msg.get("embeds") or []))
    out["embeds"] = kept
    if msg.get("components"):
        out["components"] = clamp_components(msg["components"])
    out.setdefault("allowed_mentions", {"parse": []})
    return out


def message_within_limits(msg: dict) -> bool:
    embeds = msg.get("embeds") or []
    if len(msg.get("content") or "") > CONTENT_MAX or len(embeds) > EMBEDS_MAX:
        return False
    if sum(embed_length(e) for e in embeds) > EMBED_TOTAL_MAX:
        return False
    for e in embeds:
        if len(e.get("title") or "") > TITLE_MAX or len(e.get("description") or "") > DESCRIPTION_MAX:
            return False
        fields = e.get("fields") or []
        if len(fields) > FIELDS_MAX:
            return False
        if any(len(f.get("name") or "") > FIELD_NAME_MAX or len(f.get("value") or "") > FIELD_VALUE_MAX for f in fields):
            return False
        if len((e.get("footer") or {}).get("text") or "") > FOOTER_MAX:
            return False
    rows = msg.get("components") or []
    if len(rows) > ROWS_MAX:
        return False
    for row in rows:
        comps = row.get("components") or []
        if len(comps) > BUTTONS_PER_ROW:
            return False
        if any(len(c.get("custom_id") or "") > CUSTOM_ID_MAX or len(c.get("label") or "") > BUTTON_LABEL_MAX for c in comps):
            return False
    return True


def chunk_text(text: str, limit: int = 1900) -> list[str]:
    """Split on line boundaries so every chunk fits in a message (hard-splits over-long lines)."""
    chunks: list[str] = []
    cur = ""
    for raw in text.splitlines(keepends=True):
        line = raw
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            chunks.append(cur)
            cur = ""
        cur += line
    if cur or not chunks:
        chunks.append(cur)
    return chunks
