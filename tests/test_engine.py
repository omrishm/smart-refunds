import csv
import subprocess
import sys
from collections import Counter

import pytest

from smartrefunds.clean import REQUIRED_COLUMNS, load_requests
from smartrefunds.engine import FixtureProvider, OffProvider, decide_all, summarize

PENDING = "data/pending.csv"
FIXTURE = "tests/fixtures/pending_facts.jsonl"
OUT_EXTRA = ["outcome", "lane", "rule_id", "reasons", "draft_rejection", "cleaning_changes"]


@pytest.fixture
def pending():
    return load_requests(PENDING).requests


def cli(*args):
    return subprocess.run([sys.executable, "-m", "smartrefunds", *args], capture_output=True, text=True)


def test_run_pending_off_mode(pending, v1):
    decisions = decide_all(pending, v1, OffProvider())
    auto = [d.request.request_id for d in decisions if d.outcome.outcome == "auto_approved"]
    assert auto == ["RF-1069"]
    rule = {(d.request.request_id, d.request.row_index): d.outcome.rule_id for d in decisions}
    assert [v for (rid, _), v in rule.items() if rid == "RF-2183"] == ["S2", "S2"]
    assert rule[("RF-1067", 0)] == rule[("RF-1068", 1)] == "R3"
    s4 = [d for d in decisions if d.outcome.rule_id == "S4"]
    assert len(s4) == 16
    fraud = {d.request.request_id for d in decisions if d.outcome.lane == "Fraud team"}
    assert fraud == {"RF-1070", "RF-1077", "RF-2182"}


def test_run_pending_with_fixture_facts(pending, v1):
    decisions = decide_all(pending, v1, FixtureProvider(FIXTURE))
    auto = {d.request.request_id for d in decisions if d.outcome.outcome == "auto_approved"}
    assert auto == {"RF-1069", "RF-1071", "RF-2198"}
    lanes = Counter(d.outcome.lane for d in decisions if d.outcome.outcome == "needs_review")
    assert lanes == {"Fraud team": 3, "Delivery disputes": 9, "Policy rejections": 1, "Other": 3,
                     "Damage & evidence": 2}


def test_summary_mentions_mode_and_counts(pending, v1):
    text = summarize(decide_all(pending, v1, OffProvider()), "off")
    assert "AI mode: off" in text and "auto-approved: 1" in text.lower()


def test_decisions_csv_columns(tmp_path):
    res = cli("run", PENDING, "--ai", "off", "--out", str(tmp_path))
    assert res.returncode == 0, res.stderr
    with open(tmp_path / "decisions_pending.csv", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == list(REQUIRED_COLUMNS) + OUT_EXTRA


def test_run_twice_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert cli("run", PENDING, "--ai", "off", "--out", str(a)).returncode == 0
    assert cli("run", PENDING, "--ai", "off", "--out", str(b)).returncode == 0
    assert (a / "decisions_pending.csv").read_bytes() == (b / "decisions_pending.csv").read_bytes()


def test_cli_bad_input(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("request_id,requested_at\nRF-1,2026-01-01\n", encoding="utf-8")
    for path in [str(tmp_path / "nope.csv"), str(bad)]:
        res = cli("run", path, "--ai", "off", "--out", str(tmp_path))
        assert res.returncode == 2
        assert "Traceback" not in res.stderr
        lines = [l for l in res.stderr.splitlines() if l.strip()]
        assert len(lines) == 1 and lines[0].startswith("error:")


def test_cli_bad_rules_file(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text("version: 1\nrules: [ {id: X, title: t, when: [], then: {kind: approve}} ]\n", encoding="utf-8")
    for rules in [str(tmp_path / "missing.yaml"), str(bad)]:
        res = cli("run", PENDING, "--ai", "off", "--rules", rules, "--out", str(tmp_path))
        assert res.returncode == 2 and "Traceback" not in res.stderr, res.stderr
        assert res.stderr.strip().startswith("error:")


def test_run_uses_the_latest_published_rules(tmp_path):
    import copy

    from smartrefunds.store import Store

    var = tmp_path / "var"
    store = Store(var_dir=var)
    rs = copy.deepcopy(store.live_rules())
    next(r for r in rs.rules if r.id == "R10").when[0].value = 20  # cap below RF-1069's $25
    assert store.publish(rs, reason="tiny cap") == 2
    res = cli("run", PENDING, "--ai", "off", "--var", str(var), "--out", str(tmp_path))
    assert res.returncode == 0, res.stderr
    assert "rules v2" in res.stdout and "auto-approved: 0" in res.stdout
    seed = cli("run", PENDING, "--ai", "off", "--var", str(tmp_path / "empty"), "--out", str(tmp_path))
    assert "rules v1" in seed.stdout and "auto-approved: 1" in seed.stdout
