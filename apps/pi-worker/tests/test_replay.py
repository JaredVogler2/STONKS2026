import json

import pytest

from apex_worker.cli import main
from apex_worker.demo.replay import SCENARIOS, run_all, run_scenario

REQUIRED = {
    "normal_publish_activation_target", "same_bar_stop_first", "gap_through_stop", "entry_never_filled_expired",
    "earnings_veto", "stale_feed_no_alerts", "dm_blocked_held_privately", "duplicate_discord_interaction",
    "oversell_rejected", "corrupt_or_unsigned_model_rejected", "model_expired_unvalidated_only",
    "pi_offline_spool_replay",
}


@pytest.mark.slow
def test_all_twelve_scenarios_pass_and_write_reports(tmp_path):
    assert set(SCENARIOS) >= REQUIRED
    results = run_all(tmp_path)
    failed = {r["name"]: r["failures"] for r in results if not r["passed"]}
    assert not failed, failed
    for r in results:
        report = json.loads((tmp_path / f"{r['name']}.json").read_text())
        assert report["passed"] and report["is_demo"] and report["checks"] and report["summary"]
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".work-")]  # scratch stores cleaned up


def test_cli_single_scenario_and_unknown(tmp_path, capsys):
    assert main(["replay", "--scenario", "oversell_rejected", "--out", str(tmp_path)]) == 0
    assert "[PASS] oversell_rejected" in capsys.readouterr().out
    assert main(["replay", "--scenario", "nope", "--out", str(tmp_path)]) == 2
    with pytest.raises(SystemExit):
        main(["replay"])  # --all or --scenario is required


def test_a_crashing_scenario_is_reported_not_raised(tmp_path, monkeypatch):
    import apex_worker.demo.replay as replay

    def boom(ctx, r):
        raise RuntimeError("injected")

    monkeypatch.setitem(replay.SCENARIOS, "boom", boom)
    rep = run_scenario("boom", tmp_path)
    assert rep["passed"] is False and "crashed: RuntimeError: injected" in rep["failures"][0]
