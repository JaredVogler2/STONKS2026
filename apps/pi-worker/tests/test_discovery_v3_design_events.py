"""Discovery V3 design study `design-study-2` (docs/evidence/DESIGN_STUDY_2.md): the registered plan, causal SEC-event
features, the cadence rule shared with catalysts.earnings, exactly neutral indicator tilts, the risk-only proposal and the
aggregates-only output. Events are fictional (research.sec_history.synthetic_history or written here)."""

from __future__ import annotations

import dataclasses
import functools
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from apex_worker.discovery.v3 import design as D
from apex_worker.discovery.v3 import design_events as E
from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.market.calendar import default_calendar
from apex_worker.research import sec_history as SH

DESIGN_STUDY_2_SHA256 = "b562c7a6fb2fb6cf772e8958909c2f6dd89448680cf2851d33096e7ec3487375"  # DESIGN_STUDY_2.md
CAL = default_calendar()


def test_plan_is_the_registered_one():
    """If this fails the plan or an event rule changed after registration: register a NEW plan id instead."""
    assert E.DESIGN_STUDY_2.sha256() == DESIGN_STUDY_2_SHA256
    doc = (Path(__file__).resolve().parents[3] / "docs" / "evidence" / "DESIGN_STUDY_2.md").read_text()
    assert DESIGN_STUDY_2_SHA256 in doc
    assert list(E.DESIGN_STUDY_2.design_specs()) == ["insider-full", "eap-full", "ear-full"]
    assert E.DESIGN_STUDY_2.to_dict()["event_rules"] == SH.EVENT_RULES
    assert set(E.EVENT_PLANS).isdisjoint(D.DESIGN_PLANS)


@functools.cache
def _panel():
    from apex_worker.research.realeq.fixture_v2 import factor_frames_v2

    return TB.light_panel(factor_frames_v2(per_sector=4, seed=7, start=pd.Timestamp("2022-01-03").date(),
                                           end=pd.Timestamp("2024-06-28").date()))


def _history(pre) -> SH.SecHistory:
    return SH.synthetic_history(pre.stocks, [d.date() for d in pre.sessions], seed=11)


def test_the_cadence_estimate_is_catalysts_rule():
    from apex_worker.catalysts.earnings import estimate_next_from_8k
    from apex_worker.catalysts.sec import FilingMeta

    def metas(days):
        return [FilingMeta(cik="0009990001", company_name="DM", accession_number=f"0009990001-26-{i:06d}", form="8-K",
                           filing_date=d, acceptance_at=datetime(d.year, d.month, d.day, 12, tzinfo=UTC),
                           items=("2.02",)) for i, d in enumerate(days)]

    quarterly = [date(2023, 2, 2) + timedelta(days=91 * i + (i % 3)) for i in range(7)]  # a year-ago anchor exists
    young = [date(2024, 1, 25), date(2024, 4, 25), date(2024, 7, 30)]  # median cadence only
    for days in (quarterly, young, quarterly[:5]):
        assert E.next_release_day(days, E.DESIGN_STUDY_2) == estimate_next_from_8k("DM", metas(days), cal=CAL).day
    assert E.next_release_day(young[:2], E.DESIGN_STUDY_2) is None


def test_event_features_are_causal():
    """Dropping every event usable after a cut changes no feature at or before the cut."""
    pre = _panel()
    inp, h = D.inputs(pre), _history(pre)
    full = E.event_features(inp, h, E.DESIGN_STUDY_2, CAL)
    days = [d.date() for d in pre.sessions]
    for cut in (300, 450, len(days) - 40):
        cut_day = days[cut]
        early = dataclasses.replace(
            h, releases={s: [r for r in rs if r.session <= cut_day] for s, rs in h.releases.items()},
            purchases={s: [p for p in ps if p.session <= cut_day] for s, ps in h.purchases.items()})
        part = E.event_features(inp, early, E.DESIGN_STUDY_2, CAL)
        for f in ("insider", "eap", "ear"):
            np.testing.assert_array_equal(getattr(part, f)[: cut + 1], getattr(full, f)[: cut + 1], err_msg=f)
    assert np.nansum(full.insider) > 0 and np.nansum(full.eap == 1.0) > 0 and np.isfinite(full.ear).any()


def test_event_windows_and_the_announcement_return():
    pre = _panel()
    inp = D.inputs(pre)
    days = [d.date() for d in pre.sessions]
    s, j = pre.stocks[0], 0
    plan = E.DESIGN_STUDY_2
    buy = SH.Purchase("0009990001", "0009991001", days[99], days[100], 100.0, None)
    rel = [SH.Release("0009990001", days[i], datetime(days[i].year, days[i].month, days[i].day, 13, tzinfo=UTC),
                      days[i]) for i in (40, 103, 166, 229)]
    h = SH.SecHistory({s: rel}, {s: [buy]}, list(pre.stocks), {s: "0009990001"}, days[0], days[-1])
    ev = E.event_features(inp, h, plan, CAL)
    assert ev.insider[99, j] == 0 and ev.insider[100, j] == 1 and ev.insider[159, j] == 1 and ev.insider[160, j] == 0
    # the announcement return is known at the close of its second session and lasts until the next one replaces it
    assert np.isnan(ev.ear[103, j]) and np.isfinite(ev.ear[104, j])
    x = inp.logret[103:105, j] - inp.spy_logret[103:105]
    assert ev.ear[104, j] == pytest.approx(float(np.expm1(x.sum())))
    assert ev.ear[104 + 59, j] == ev.ear[104, j] and ev.ear[164, j] != ev.ear[104, j]
    # three releases known only from the third one's session: an estimate, never before
    assert np.isnan(ev.eap[165, j]) and np.isfinite(ev.eap[166, j])
    guess = E.next_release_day([r.day for r in rel[:3]], plan)
    flagged = [t for t in range(166, 229) if ev.eap[t, j] == 1.0]
    assert flagged and all(CAL.add_sessions(days[t], 1) < guess <= CAL.add_sessions(days[t], 10) for t in flagged)


def test_indicator_tilts_are_exactly_neutral_and_flat_without_a_flag():
    rng = np.random.default_rng(5)
    n = 60
    sector = np.array(["a", "b", "c"] * 20, dtype=object)
    idx = np.arange(n)
    expo = np.column_stack([rng.normal(1, 0.3, n), rng.normal(20, 1, n)])
    flags = (rng.random(n) < 0.15).astype(float)
    a = E.indicator_score(flags, idx, sector, expo)
    assert np.max(np.abs(a)) == pytest.approx(1.0) and np.all(a[flags > 0] > 0)
    assert abs(a @ expo[:, 0]) < 1e-9 and abs(a @ expo[:, 1]) < 1e-9
    assert all(abs(a[sector == g].sum()) < 1e-12 for g in "abc")
    assert not E.indicator_score(np.zeros(n), idx, sector, expo).any()


def test_books_follow_the_schedule_and_flagless_books_are_the_twin():
    pre = _panel()
    inp = D.inputs(pre)
    ev = E.event_features(inp, _history(pre), E.DESIGN_STUDY_2, CAL)
    t = len(pre.sessions) - 30
    first, ex = E.decide(inp, ev, t, 0, E.DESIGN_STUDY_2, True)
    assert set(first) == {"ew", "insider-full", "eap-full", "ear-full"}
    for k, e in ex.items():
        tol = 1e-5 if k != "ear-full" else 1e-3  # the ranked design clips at +-1, as design-study-1's full variants
        assert e["sector_max"] < tol and abs(e["beta"]) < tol and abs(e["size"]) < 10 * tol, k
        assert sum(first[k].values()) == TB.MICRO and min(first[k].values()) > 0  # long-only, fully invested
    assert set(E.decide(inp, ev, t, 5, E.DESIGN_STUDY_2, False)[0]) == {"insider-full", "eap-full", "ear-full"}
    assert E.decide(inp, ev, t, 3, E.DESIGN_STUDY_2, False)[0] == {}
    assert set(E.decide(inp, ev, t, 20, E.DESIGN_STUDY_2, False)[0]) == {"ew", "insider-full", "eap-full", "ear-full"}
    quiet = dataclasses.replace(ev, insider=np.zeros_like(ev.insider))
    assert E.decide(inp, quiet, t, 0, E.DESIGN_STUDY_2, True)[0]["insider-full"] == first["ew"]


def test_the_study_writes_aggregates_only(tmp_path):
    pre = _panel()
    n = len(pre.sessions)
    data = SimpleNamespace(pre=pre, b=SimpleNamespace(dev_range=(n - 300, n - 1)), quarantine={},
                           provenance=SimpleNamespace(data_class="SYNTHETIC_FIXTURE_DEMO"))
    h = _history(pre)
    s = E.run_event_design_study(E.DESIGN_STUDY_2, data, h, tmp_path, experiment_id="demo-design-2", cal=CAL,
                                 progress=lambda _m: None)
    assert s["status"] == "COMPLETED" and s["sessions"] == 299
    raw = (tmp_path / "design_study.json").read_text()
    body = json.loads(raw)
    assert body["plan_sha256"] == DESIGN_STUDY_2_SHA256 and body["window"]["label"] == "DEV_CONTAMINATED"
    assert not any(f'"{x}"' in raw for x in [*pre.stocks, *h.ciks.values()])  # no symbol or CIK anywhere
    for k in ("insider-full", "eap-full"):
        act = body["designs"][k]["mean_abs_active"]
        assert act["beta"] < 1e-5 and act["size"] < 1e-4 and act["sector_max"] < 1e-5
    p = body["proposal"]
    assert [c["design"] for c in p["candidates"]] == ["insider-full", "eap-full", "ear-full"]
    assert p["prior_mass_total"] == 0.12 and all(c["claims"] == "against the twin only" for c in p["candidates"])
    assert body["sec_events"]["symbols"] == len(pre.stocks) and "rules" in body["sec_events"]
    md = (tmp_path / "DESIGN_STUDY.md").read_text()
    assert "DEV_CONTAMINATED" in md and "1000" in md


def test_cli_lists_the_plan_and_runs_the_demo_without_a_contact(tmp_path, capsys, monkeypatch):
    from apex_worker.cli import main

    capsys.readouterr()
    assert main(["research", "discovery", "plans"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["design-study-2"]["sha256"] == DESIGN_STUDY_2_SHA256
    assert listed["design-study-2"]["needs"] == "APEX_SEC_USER_AGENT"
    # a real run without the owner's contact stops before any download: BLOCKED (exit 2), nothing fetched
    monkeypatch.delenv("APEX_SEC_USER_AGENT", raising=False)

    def no_download(*_a, **_k):
        raise AssertionError("no data may be fetched without the SEC contact")

    monkeypatch.setattr("apex_worker.discovery.v3.pipeline.load_data", no_download)
    assert main(["research", "discovery", "design", "--plan", "design-study-2", "--source", "alpaca",
                 "--experiment-id", "ds2-blocked", "--out", str(tmp_path / "real")]) == 2
    assert "BLOCKED" in capsys.readouterr().err
