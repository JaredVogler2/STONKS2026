"""Discovery V3 design study `design-study-2` (docs/evidence/DESIGN_STUDY_2.md): SEC-event tilt designs for the evidence
tournament's next cohort.

design-study-1 measured price-signal tilts. This study measures three tilts built on public SEC filings
(research.sec_history: earnings releases from 8-K item 2.02, insider purchases from Form 4), on the same daily bars and
with the construction of design-study-1's `full` variants: sector-, beta- and size-neutral active weights, the
equal-weight twin, the tournament lane's replay and close costs. The designs:
* `insider-full`: names with an insider open-market purchase (a director or an officer, Form 4 code P) usable at one of
  the last `insider_window` sessions;
* `eap-full` (the earnings-announcement premium): names whose next release, expected from their own release cadence as
  known at the close of t (catalysts.earnings' rule), falls after the trade at the close of t + 1 and within
  `release_window` sessions of t;
* `ear-full` (drift after the announcement return): the latest release's return relative to SPY over its first
  `reaction_sessions` usable sessions, ranked within sectors, for `drift_window` sessions after it is known.
An indicator design (`insider`, `eap`) scales its neutral residual by its largest absolute value, so the active weights
stay exactly neutral and a flagged name is overweighted by at most `strength`; the announcement return is ranked and
scaled like design-study-1's `full` variants. Missing data never reads as a signal: a name without an estimate, a
release or a CIK scores 0.

A measurement, not a strategy test: the registered proposal rule reads only risk (tracking error and the worst session).
DEV means are DEV_CONTAMINATED diagnostics and select nothing. Only aggregates leave the runner: no symbol, CIK, price or
per-stock value is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from apex_worker.discovery.v3 import design as D
from apex_worker.discovery.v3 import tournament_books as TB
from apex_worker.research import sec_history as SH


@dataclass(frozen=True)
class EventPlan:
    plan_id: str
    title: str
    designs: tuple[tuple[str, str, int], ...]  # (signal, kind: indicator | ranked, rebalance every n sessions)
    insider_window: int = 60  # sessions: a purchase counts at its usable session and the 59 after
    release_window: int = 10  # sessions: an expected release in (the session after t, the 10th session after t]
    reaction_sessions: int = 2  # the announcement return: the release's first usable session and the next
    drift_window: int = 60  # sessions the announcement return counts once it is known
    min_releases: int = 3  # the cadence estimate needs three known releases
    yoy_days: int = 364
    yoy_tolerance_days: int = 21
    strength: float = 0.5  # weight = equal weight x (1 + strength x score), score in [-1, 1]
    twin_every: int = 20
    beta_lookback: int = 252
    beta_min_obs: int = 126
    twin_loss_grid: tuple[float, ...] = (0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05)
    spy_loss_grid: tuple[float, ...] = (0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2)
    safety: float = 2.0  # proposed loss bound: the smallest grid value >= safety x the worst DEV session
    hypothetical_gross_alpha: tuple[float, ...] = (0.005, 0.01, 0.02)
    claim_level: float = 1000.0  # a twin-only claim at prior mass 0.04 (cohort 1's threshold)
    target_years: float = 10.0
    proposed_mass: float = 0.04
    sec_history_start: str = SH.HISTORY_START.isoformat()
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self) | {"event_rules": dict(SH.EVENT_RULES)}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def design_specs(self) -> dict[str, dict]:
        return {f"{n}-full": {"signal": n, "kind": k, "variant": "full", "every": every} for n, k, every in self.designs}


DESIGN_STUDY_2 = EventPlan(
    plan_id="design-study-2",
    title="Information rate of SEC-event tilts against their equal-weight twin (U1, DEV dates)",
    designs=(("insider", "indicator", 5), ("eap", "indicator", 5), ("ear", "ranked", 5)),
    notes="Registered before the run. Risk only selects: every design is proposed, each with the smallest grid loss "
    "bound at least twice its worst DEV session (against the twin, and against SPY). DEV means are DEV_CONTAMINATED "
    "diagnostics and select nothing. The proposal is for a later, separately registered cohort of the evidence "
    "tournament, at prior mass 0.04 each from the reserve, claimed against the twin only; it changes no recorded series.")

EVENT_PLANS: dict[str, EventPlan] = {p.plan_id: p for p in (DESIGN_STUDY_2,)}


# ---- event features on the session axis -----------------------------------------------------------------------------
def next_release_day(days: list[date], plan: EventPlan) -> date | None:
    """catalysts.earnings.estimate_next_from_8k's day: the same quarter last year + 364 days when a release lies within
    21 days of that anchor, else the last release + the median gap; None with fewer than `min_releases` releases."""
    if len(days) < plan.min_releases:
        return None
    gap = statistics.median((b - a).days for a, b in pairwise(days))
    target = days[-1] + timedelta(days=gap) - timedelta(days=plan.yoy_days)
    anchors = [d for d in days if abs((d - target).days) <= plan.yoy_tolerance_days]
    if anchors:
        return min(anchors, key=lambda d: abs((d - target).days)) + timedelta(days=plan.yoy_days)
    return days[-1] + timedelta(days=gap)


@dataclass
class EventFeatures:
    insider: np.ndarray  # sessions x stocks: purchases usable within the window ending at t
    eap: np.ndarray  # 1 expected release in the window after the trade, 0 not, NaN no estimate
    ear: np.ndarray  # the latest announcement return vs SPY while it counts, NaN none


def event_features(inp: D.Inputs, history: SH.SecHistory, plan: EventPlan, cal) -> EventFeatures:
    """Causal event features: every value at t uses only events whose usable session is at or before t, and bars
    through t."""
    days = [d.date() for d in inp.sessions]
    n, m = len(days), len(inp.stocks)
    ords = np.array([d.toordinal() for d in days])

    def at(day: date) -> int:
        """The first panel session on or after `day` (n: after the panel), so a gap in the panel never drops an event."""
        return int(np.searchsorted(ords, day.toordinal(), side="left"))

    diff = np.zeros((n + 1, m))
    for j, s in enumerate(inp.stocks):
        for p in history.purchases.get(s, []):
            if (i := at(p.session)) < n:
                diff[i, j] += 1.0
                diff[min(i + plan.insider_window, n), j] -= 1.0
    insider = np.cumsum(diff, axis=0)[:n]

    nxt = np.array([cal.add_sessions(d, 1).toordinal() for d in days])
    far = np.array([cal.add_sessions(d, plan.release_window).toordinal() for d in days])
    eap = np.full((n, m), np.nan)
    ear = np.full((n, m), np.nan)
    valid = np.isfinite(inp.logret) & ~((inp.ret == 0.0) & (inp.logret != 0.0))  # a quarantined bar is not a reaction
    rel_log = np.where(valid, inp.logret, np.nan) - inp.spy_logret[:, None]
    for j, s in enumerate(inp.stocks):
        rel = history.releases.get(s, [])
        usable = [at(r.session) for r in rel]  # nondecreasing: releases are sorted by acceptance
        for k in range(plan.min_releases, len(rel) + 1):
            lo = usable[k - 1]
            if lo >= n:
                break
            hi = usable[k] if k < len(rel) else n
            guess = next_release_day([r.day for r in rel[:k]], plan)
            if guess is not None and hi > lo:
                g = guess.toordinal()
                eap[lo:hi, j] = ((nxt[lo:hi] < g) & (g <= far[lo:hi])).astype(float)
        for k, u in enumerate(usable):
            if rel[k].session < days[0]:
                continue  # its reaction precedes the panel
            known = u + plan.reaction_sessions - 1
            if known >= n:
                break
            x = rel_log[u: known + 1, j]
            nu = usable[k + 1] if k + 1 < len(rel) else n
            end = min(known + plan.drift_window, nu + plan.reaction_sessions - 1, n)
            if np.all(np.isfinite(x)) and end > known:
                ear[known:end, j] = float(np.expm1(x.sum()))
    return EventFeatures(insider, eap, ear)


def coverage(ev: EventFeatures, inp: D.Inputs, t0: int, t1: int) -> dict:
    """How much of the masked universe each signal flags, averaged over the decisions t0 .. t1 (aggregates only)."""
    m = inp.mask[:, t0: t1 + 1].T
    per = m.sum(axis=1)
    ok = per > 0

    def share(x: np.ndarray) -> float:
        return float(np.mean((x & m).sum(axis=1)[ok] / per[ok]))

    ins = ev.insider[t0: t1 + 1] > 0
    return {"insider_flagged_share": share(ins),
            "insider_decisions_without_a_flag": float(np.mean((ins & m).sum(axis=1)[ok] == 0)),
            "eap_flagged_share": share(ev.eap[t0: t1 + 1] == 1.0),
            "eap_estimated_share": share(np.isfinite(ev.eap[t0: t1 + 1])),
            "ear_available_share": share(np.isfinite(ev.ear[t0: t1 + 1]))}


# ---- the books ------------------------------------------------------------------------------------------------------
def indicator_score(x: np.ndarray, idx: np.ndarray, sector: np.ndarray, exposures: np.ndarray) -> np.ndarray:
    """A 0/1 flag over the masked stocks idx -> its sector-demeaned residual on the exposures, divided by its largest
    absolute value: exactly sector-, beta- and size-neutral, in [-1, 1]; all zeros when nothing is flagged."""
    v = np.nan_to_num(x[idx].astype(float), nan=0.0)
    if not np.any(v > 0):
        return np.zeros(idx.size)
    z = v.copy()
    for g in np.unique(sector):
        sel = sector == g
        z[sel] -= z[sel].mean()
    e = D._residual(z, exposures, sector)
    top = float(np.max(np.abs(e)))
    return e / top if top > 1e-12 else np.zeros(idx.size)


def decide(inp: D.Inputs, ev: EventFeatures, t: int, phase: int, plan: EventPlan, first: bool) -> tuple[dict, dict]:
    """Targets (micro-weights) decided at the close of t for every book due (all of them at the first decision), and each
    decided tilt's active exposures against the static equal weight at t (as design.decide), plus the share flagged."""
    idx = np.flatnonzero(inp.mask[:, t])
    syms, sec = inp.stocks, inp.sector[idx]
    specs = plan.design_specs()
    out: dict[str, dict[str, int]] = {}
    if first or phase % plan.twin_every == 0:
        out["ew"] = TB._equal([syms[i] for i in idx])
    due = sorted(k for k, d in specs.items() if first or phase % d["every"] == 0)
    if not due or idx.size == 0:
        return out | {k: {TB.CASH: TB.MICRO} for k in due if idx.size == 0}, {}
    beta = D.betas(inp, t, plan)
    dv = inp.frames["dollar_vol_med_20"][t]
    size = np.log(np.where(dv > 0, dv, np.nan))
    expo = np.column_stack([D._fill_by_sector(beta, idx, inp.sector), D._fill_by_sector(size, idx, inp.sector)])
    expos: dict[str, dict] = {}
    for k in due:
        signal = specs[k]["signal"]
        if signal == "insider":
            raw = (ev.insider[t] > 0).astype(float)
            a = indicator_score(raw, idx, sec, expo)
        elif signal == "eap":
            raw = np.nan_to_num(ev.eap[t], nan=0.0)
            a = indicator_score(raw, idx, sec, expo)
        elif signal == "ear":
            raw = np.isfinite(ev.ear[t]).astype(float)
            a = D._scale(D._residual(D._sector_ranks(ev.ear[t], idx, inp), expo, sec))
        else:
            raise ValueError(f"unknown event signal {signal}")
        out[k] = D._weights(a, idx, syms, plan.strength)
        act = np.array([out[k].get(syms[i], 0) for i in idx], float) / TB.MICRO - 1.0 / idx.size
        expos[k] = {"sector_max": float(max(abs(act[sec == g].sum()) for g in np.unique(sec))),
                    "beta": float(act @ expo[:, 0]), "size": float(act @ expo[:, 1]),
                    "score_sd": float(np.std(a)), "flagged": float(np.mean(raw[idx] > 0))}
    return out, expos


def proposal(rows: dict[str, dict], plan: EventPlan) -> dict:
    """The registered rule (reads only risk): every design, with loss bounds from its worst DEV session x safety,
    rounded up on the grid (None: no grid value is large enough, so no bounded test)."""
    out = [{"design": k, "twin": "ew", "rebalance_every": r["every"], "prior_mass": plan.proposed_mass,
            "claims": "against the twin only",
            "twin_loss_bound": D._grid_up(plan.safety * abs(min(r["vs_twin"]["worst_session"], 0.0)),
                                          plan.twin_loss_grid),
            "loss_bound": D._grid_up(plan.safety * abs(min(r["vs_spy"]["worst_session"], 0.0)), plan.spy_loss_grid)}
           for k, r in rows.items()]
    return {"rule": f"every design; loss bound = smallest grid value >= {plan.safety} x the worst DEV session",
            "candidates": out, "prior_mass_total": round(plan.proposed_mass * len(out), 6),
            "status": "PROPOSAL for a later, separately registered cohort; changes no recorded series"}


def run_event_design_study(plan: EventPlan, data, history: SH.SecHistory, out_dir: Path, *, experiment_id: str,
                           cal=None, progress: Callable[[str], None] = print, code_sha: str | None = None) -> dict:
    """`data`: pipeline.Loaded; `history`: the SEC events of its stocks. Writes design_study.json and DESIGN_STUDY.md."""
    from apex_worker.market.calendar import default_calendar

    out_dir.mkdir(parents=True, exist_ok=True)
    cal = cal or default_calendar()
    inp = D.inputs(data.pre, data.quarantine)
    ev = event_features(inp, history, plan, cal)
    d0, d1 = data.b.dev_range
    t0 = d0 - 1  # the first decision: the close before the DEV window (the agents' convention)
    specs = plan.design_specs()
    targets: dict[str, dict[int, dict]] = {k: {} for k in ["ew", *specs]}
    sums: dict[str, dict[str, list[float]]] = {k: {f: [] for f in ("sector_max", "beta", "size", "score_sd",
                                                                   "flagged")} for k in specs}
    for t in range(t0, d1):
        tg, ex = decide(inp, ev, t, t - t0, plan, t == t0)
        for k, mm in tg.items():
            targets[k][t] = mm
        for k, e in ex.items():
            for f, v in e.items():
                sums[k][f].append(abs(v))
        if (t - t0) % 250 == 0:
            progress(f"decisions through {inp.sessions[t].date()}")
    days = slice(t0 + 2, d1 + 1)  # every evaluated session holds the books' own targets (the first trade is t0 + 1)
    ev_days = inp.sessions[days]
    years = (d1 + 1 - (t0 + 2)) / 252
    rets, books = {}, {}
    for k, tg in targets.items():
        r, turn, paid = D.replay(inp, tg, t0, d1)
        rets[k] = r[days]
        books[k] = {"rebalances": len(tg), "turnover_one_way_per_year": float(np.sum(turn[days])) / years,
                    "cost_per_year": float(np.sum(paid[days])) / years}
    spy = inp.spy_ret[days]
    rows = {}
    for k, d in specs.items():
        tw = D.rel_stats((1 + rets[k]) / (1 + rets["ew"]) - 1, ev_days)
        sp = D.rel_stats((1 + rets[k]) / (1 + spy) - 1, ev_days)
        active = {f: float(np.mean(v)) if v else None for f, v in sums[k].items()}
        tw["tracking_error_per_unit_dispersion"] = (tw["tracking_error"] * D.UNIFORM_SD / active["score_sd"]
                                                    if active["score_sd"] else None)
        lb_twin = D._grid_up(plan.safety * abs(min(tw["worst_session"], 0.0)), plan.twin_loss_grid)
        extra = books[k]["cost_per_year"] - books["ew"]["cost_per_year"]
        rows[k] = d | {"vs_twin": tw, "vs_spy": sp, "book": books[k] | {"extra_cost_per_year_vs_twin": extra},
                       "mean_abs_active": active,
                       "velocity_vs_twin": D.velocity_rows(tw["tracking_error"], extra, lb_twin, plan)}
    twin = {"vs_spy": D.rel_stats((1 + rets["ew"]) / (1 + spy) - 1, ev_days), "book": books["ew"]}
    body = {"schema": "apex-discovery-v3-design-study-2", "experiment_id": experiment_id, "plan": plan.to_dict(),
            "plan_sha256": plan.sha256(), "code_sha": code_sha or os.environ.get("GITHUB_SHA"),
            "provenance": data.provenance.__dict__, "research_only": True, "status": "COMPLETED",
            "window": {"first_decision": inp.sessions[t0].date().isoformat(),
                       "evaluated": [ev_days[0].date().isoformat(), ev_days[-1].date().isoformat()],
                       "sessions": int(ev_days.size), "label": "DEV_CONTAMINATED", "tier": "DEV_DIAGNOSTIC"},
            "universe": {"stocks": len(inp.stocks), "mean_masked": float(inp.mask[:, t0:d1].sum(axis=0).mean())},
            "sec_events": history.summary() | {"coverage_of_masked_names": coverage(ev, inp, t0, d1 - 1)},
            "twin": twin, "designs": rows, "proposal": proposal(rows, plan),
            "publication": "aggregates only: no symbol, CIK, price or per-stock value (provider terms)",
            "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    D._no_symbols(body, set(inp.stocks) | set(history.ciks.values()))
    (out_dir / "design_study.json").write_text(json.dumps(body, indent=1, sort_keys=True, default=str))
    (out_dir / "DESIGN_STUDY.md").write_text(summary_md(body))
    return {"status": body["status"], "sessions": body["window"]["sessions"], "proposal": body["proposal"]}


def summary_md(body: dict) -> str:
    w, plan, sec = body["window"], body["plan"], body["sec_events"]
    cov = sec["coverage_of_masked_names"]
    lines = [f"# Design study `{plan['plan_id']}` ({body['experiment_id']})", "",
             f"Status: **{body['status']}**. Sessions {w['evaluated'][0]} → {w['evaluated'][1]} ({w['sessions']}; "
             f"{w['label']}, {w['tier']}). Research only: paper books, no orders.", "",
             f"SEC events {sec['window'][0]} → {sec['window'][1]}: {sec['earnings_releases']} earnings releases, "
             f"{sec['insider_purchases']} insider purchases; {sec['symbols_with_cik']} of {sec['symbols']} names "
             f"mapped to a CIK. Flagged share of the masked names: insider {D._pct(cov['insider_flagged_share'], 1)}, "
             f"expected release {D._pct(cov['eap_flagged_share'], 1)}; announcement return available "
             f"{D._pct(cov['ear_available_share'], 1)}.", "",
             "Tracking error and worst session are risk measurements; the means are DEV_CONTAMINATED diagnostics and "
             "select nothing.", "",
             f"| Design | every | TE vs twin (per unit dispersion) | worst vs twin | TE vs SPY | worst vs SPY | "
             f"turnover/yr | extra cost/yr | gross alpha for {plan['claim_level']:g} in {plan['target_years']:g} y: "
             "limit · bounded | DEV IR vs twin |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in body["designs"].items():
        tw_, need = r["vs_twin"], r["velocity_vs_twin"]["gross_alpha_needed"]
        lines.append(f"| `{k}` | {r['every']} | {D._pct(tw_['tracking_error'])} "
                     f"({D._pct(tw_['tracking_error_per_unit_dispersion'])}) | "
                     f"{D._pct(tw_['worst_session'])} ({tw_['worst_session_date']}) | "
                     f"{D._pct(r['vs_spy']['tracking_error'])} | {D._pct(r['vs_spy']['worst_session'])} | "
                     f"{r['book']['turnover_one_way_per_year']:.2f} | "
                     f"{D._pct(r['book']['extra_cost_per_year_vs_twin'], 3)} | "
                     f"{D._pct(need['information_limit'])} · {D._pct(need['bounded'])} | "
                     f"{D._ir(tw_['dev_contaminated']['ir'])} |")
    tw = body["twin"]
    p = body["proposal"]
    lines += ["", f"Twin `ew` vs SPY: TE {D._pct(tw['vs_spy']['tracking_error'])}, worst session "
              f"{D._pct(tw['vs_spy']['worst_session'])} ({tw['vs_spy']['worst_session_date']}).", "",
              f"**Proposal** ({p['status']}): {p['rule']}.", "",
              "| Candidate | every | twin loss bound | loss bound vs SPY | prior mass |", "|---|---|---|---|---|"]
    for c in p["candidates"]:
        lines.append(f"| `{c['design']}` | {c['rebalance_every']} | {c['twin_loss_bound']} | {c['loss_bound']} | "
                     f"{c['prior_mass']} |")
    return "\n".join(lines) + "\n"


def history_window(data) -> tuple[date, date]:
    """The SEC history a study needs: from the registered start through its last DEV session."""
    return SH.HISTORY_START, pd.Timestamp(data.pre.sessions[data.b.dev_range[1]]).date()
