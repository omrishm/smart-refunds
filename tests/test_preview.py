import copy
from pathlib import Path

import pytest

from conftest import make_facts, make_request
from smartrefunds.clean import load_requests
from smartrefunds.engine import FixtureProvider
from smartrefunds.preview import history_label, preview
from smartrefunds.rules import Action, Condition, Rule

FIXTURE = "tests/fixtures/pending_facts.jsonl"


@pytest.fixture(scope="module")
def history():
    return load_requests("data/history.csv").requests


@pytest.fixture(scope="module")
def pending():
    return load_requests("data/pending.csv").requests


def test_history_label(history):
    label = {r.request_id: history_label(r) for r in history}
    assert label["RF-2074"] == "bad"            # chargeback
    assert label["RF-1008"] == "should_approve"  # rejected, overturned on appeal
    assert label["RF-2143"] == "should_approve"  # rejected, refunded on appeal
    assert label["RF-2018"] == "bad"            # partial
    assert label["RF-2056"] == "bad"            # rejected
    assert label["RF-1001"] == "ok"


def test_preview_identical_no_effect(v1, pending):
    res = preview(v1, v1, [], pending, FixtureProvider(FIXTURE))
    assert res.pending.changed == [] and res.history.changed == []
    assert any(w.startswith("No effect on history or today's queue") for w in res.warnings)


def test_preview_cap_change(v1, pending):
    cand = copy.deepcopy(v1)
    r10 = next(r for r in cand.rules if r.id == "R10")
    r10.when[0].value = 55
    small_history = [make_request(request_id="RF-H1", amount_usd=70.0, decided_by="D. Patel", decision="approved",
                                  review_minutes=3)]
    res = preview(cand, v1, small_history, pending, FixtureProvider(FIXTURE))
    moved = {c.request_id: (c.before, c.after) for c in res.pending.changed}
    assert moved == {"RF-1071": ("auto_approved", "Damage & evidence")}
    assert [c.request_id for c in res.history.changed] == ["RF-H1"]
    assert res.history.minutes_saved == 0  # fewer approvals, nothing saved


class FraudFacts:
    mode = "saved"

    def get(self, r):
        return make_facts(fraud_reported="yes")


def test_preview_warnings(v1):
    cand = copy.deepcopy(v1)
    cand.rules.insert(4, Rule("M1", "Stolen card → approve", [Condition("facts.fraud_reported", "=", "yes")],
                              Action("approve")))
    rows = [make_request(request_id="RF-2019", amount_usd=610.0, fraud_flag="yes", stated_reason="never_arrived",
                         support_note="Card reported stolen.", decided_by="J. Alvarez", decision="approved",
                         outcome_note="fraud team confirmed", review_minutes=9)]
    res = preview(cand, v1, rows, [], FraudFacts())
    text = " | ".join(res.warnings)
    assert "Skips the Fraud team for 1 cases" in text
    assert "Largest single new auto-approval: $610 (RF-2019)" in text
    assert "Small sample: only 1 history cases change" in text
    assert res.history.minutes_saved == 9
    assert res.history.largest_new_auto == ("RF-2019", 610.0)


def test_preview_flags_bad_outcomes(v1):
    cand = copy.deepcopy(v1)
    cand.rules.insert(4, Rule("M1", "Approve everything wrong-size", [Condition("stated_reason", "=", "wrong_size")],
                              Action("approve")))
    bad = make_request(request_id="RF-X", prior_refunds_12m=2, decision="approved", decided_by="S. Kwan",
                       outcome_note="chargeback filed 2026-08-01")
    res = preview(cand, v1, [bad], [], FixtureProvider(FIXTURE))
    assert res.history.bad_let_through == ["RF-X"]
    assert any("Lets through 1 cases with bad outcomes in history" in w for w in res.warnings)


@pytest.mark.skipif(not Path("data/facts/history.jsonl").exists(), reason="saved history facts not generated yet")
def test_backtest_v1_snapshot(v1, history):
    from smartrefunds.notes import SavedProvider

    res = preview(v1, v1, history, [], SavedProvider())
    # Pinned after the live extraction run (Task 6 Step 5); see docs/ai-usage-log.md "Backtest v1".
    assert res.history.bad_let_through == ["RF-2074"]  # the known miss: $49 "damaged", no notes at all
    assert res.history.auto_count == 103
    assert round(res.history.auto_amount, 2) == 3302.75


def test_backtest_command_offline(capsys):
    from smartrefunds.cli import main

    assert main(["backtest", "--ai", "off"]) == 0
    out = capsys.readouterr().out
    assert "Backtest on 248 historical requests (AI mode: off)" in out
    assert "Legacy auto-approve rule (actual): 111 (45%)" in out
    assert "bad outcomes: 1 ['RF-2074']" in out
