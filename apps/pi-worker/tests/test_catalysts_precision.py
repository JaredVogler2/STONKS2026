"""Measured precision of the deterministic classifiers on the hand-labelled fictional sample.

The measured numbers are embedded in the test ids (``pytest -v``) and printed by the summary test
(``pytest -s``). CAVEAT: the sample is small (43 items) and was written alongside the rules, so these
are optimistic in-sample figures, not an estimate of real-world precision.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

from apex_worker.catalysts.entities import EntityResolver
from apex_worker.catalysts.form4 import classify_form4, parse_form4
from apex_worker.catalysts.sec import classify_form
from apex_worker.catalysts.taxonomy import classify_headline

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "catalysts"
FALLBACK = "news_other"
# thresholds set just under what was measured when the sample was frozen (see report), not tuned upward
MIN_CATEGORY_PRECISION = 0.66  # every non-fallback category with >= 2 predictions
MIN_FALLBACK_PRECISION = 0.5
MIN_TYPE_ACCURACY = 0.90
MIN_DIRECTION_ACCURACY = 0.75
MIN_ENTITY_ACCURACY = 0.95


@dataclass(frozen=True)
class Row:
    label: str
    kind: str
    expected: dict
    predicted: tuple[str, str, str | None]


def _predict() -> list[Row]:
    labels = json.loads((FIX / "labels.json").read_text())["items"]
    resolver = EntityResolver.from_json((FIX / "entity_aliases.json").read_text())
    rows = []
    for item in labels:
        kind = item["kind"]
        if kind == "headline":
            tax = classify_headline(item["text"])
            ent = resolver.resolve(item["text"]).primary
            pred = (tax.catalyst_type.value, tax.direction.value, ent.symbol if ent else None)
            label = item["text"]
        elif kind == "form4":
            doc = parse_form4((FIX / item["fixture"]).read_bytes())
            cls = classify_form4(doc)
            assert cls is not None
            pred = (cls.catalyst_type.value, cls.direction.value, doc.issuer_symbol)
            label = item["fixture"]
        else:
            cls = classify_form(item["form"], item["items"].split(","))
            pred = (cls.catalyst_type.value if cls.catalyst_type else "none", cls.direction.value, None)
            label = f"{item['form']} items {item['items']}"
        rows.append(Row(label, kind, item["expected"], pred))
    return rows


ROWS = _predict()


def _per_category() -> dict[str, tuple[int, int]]:
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in ROWS:
        acc[r.predicted[0]][1] += 1
        acc[r.predicted[0]][0] += int(r.predicted[0] == r.expected["catalyst_type"])
    return {k: (v[0], v[1]) for k, v in sorted(acc.items())}


PER_CATEGORY = _per_category()
N = len(ROWS)
TYPE_CORRECT = sum(r.predicted[0] == r.expected["catalyst_type"] for r in ROWS)
DIR_CORRECT = sum(r.predicted[1] == r.expected["direction"] for r in ROWS)
ENTITY_ROWS = [r for r in ROWS if r.kind != "8k"]
ENTITY_CORRECT = sum(r.predicted[2] == r.expected.get("symbol") for r in ENTITY_ROWS)


def _ids() -> list[str]:
    return [f"{k}-precision={c / n:.2f}({c}of{n})" for k, (c, n) in PER_CATEGORY.items()]


@pytest.mark.parametrize("category", list(PER_CATEGORY), ids=_ids())
def test_category_precision(category):
    correct, predicted = PER_CATEGORY[category]
    precision = correct / predicted
    if category == FALLBACK:
        assert precision >= MIN_FALLBACK_PRECISION, f"{category} precision {precision:.2f}"
    elif predicted >= 2:
        assert precision >= MIN_CATEGORY_PRECISION, (
            f"{category} precision {precision:.2f} ({correct}/{predicted})"
        )


@pytest.mark.parametrize(
    "metric",
    [
        f"type_accuracy={TYPE_CORRECT / N:.3f}({TYPE_CORRECT}of{N})",
        f"direction_accuracy={DIR_CORRECT / N:.3f}({DIR_CORRECT}of{N})",
        f"entity_accuracy={ENTITY_CORRECT / len(ENTITY_ROWS):.3f}({ENTITY_CORRECT}of{len(ENTITY_ROWS)})",
    ],
)
def test_overall_metrics(metric):
    name = metric.split("=")[0]
    value = float(metric.split("=")[1].split("(")[0])
    floor = {
        "type_accuracy": MIN_TYPE_ACCURACY,
        "direction_accuracy": MIN_DIRECTION_ACCURACY,
        "entity_accuracy": MIN_ENTITY_ACCURACY,
    }[name]
    assert value >= floor, metric


def test_sample_is_big_enough_and_covers_sources():
    assert N >= 20
    kinds = {r.kind for r in ROWS}
    assert kinds == {"headline", "form4", "8k"}


def test_awards_and_planned_trades_never_labelled_bullish():
    for r in ROWS:
        if r.predicted[0] in {"insider_award_or_exercise", "insider_sale"}:
            assert r.predicted[1] != "potentially_positive", r.label


def test_print_measured_summary():
    lines = [f"Catalyst classifier on labels.json (n={N}, in-sample, fictional):"]
    lines += [f"  {k:30s} precision {c / n:.2f} ({c}/{n})" for k, (c, n) in PER_CATEGORY.items()]
    lines.append(f"  type accuracy      {TYPE_CORRECT / N:.3f} ({TYPE_CORRECT}/{N})")
    lines.append(f"  direction accuracy {DIR_CORRECT / N:.3f} ({DIR_CORRECT}/{N})")
    lines.append(
        f"  entity accuracy    {ENTITY_CORRECT / len(ENTITY_ROWS):.3f} ({ENTITY_CORRECT}/{len(ENTITY_ROWS)})"
    )
    misses = [r for r in ROWS if r.predicted[:2] != (r.expected["catalyst_type"], r.expected["direction"])]
    lines.append(f"  known misses ({len(misses)}):")
    lines += [
        f"    - {r.label}: predicted {r.predicted[:2]} "
        f"expected {r.expected['catalyst_type']}/{r.expected['direction']}"
        for r in misses
    ]
    print("\n".join(lines))
    assert misses, "a sample with zero misses would suggest the labels were fitted to the rules"
