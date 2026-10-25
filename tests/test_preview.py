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


def test_backtest_v1_snapshot(v1, history):
    from smartrefunds.notes import SavedProvider

    saved = SavedProvider()
    if not any(saved.lookup(r)[0] for r in history if r.notes_present):
        pytest.skip("no saved history facts for the current model and prompt version")
    res = preview(v1, v1, history, [], saved)
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


def test_describe_outcome_reads_as_what_happened(history):
    from smartrefunds.preview import describe_outcome

    text = {r.request_id: describe_outcome(r)[0] for r in history}
    assert text["RF-1005"] == "Approved → chargeback (cost money)"
    assert text["RF-2020"] == "Rejected → stood"
    assert text["RF-1008"] == "Rejected → overturned on appeal"
    assert text["RF-1017"] == "Partial refund (50 percent issued)"
    assert text["RF-1001"] == "Approved → no issues"


def test_similar_cases_lead_with_the_ones_that_cost_money(history):
    import sys
    from types import SimpleNamespace

    sys.path.insert(0, "app")
    import reviewer
    from smartrefunds.engine import Decision
    from smartrefunds.notes import SavedProvider
    from smartrefunds.rules import Outcome

    saved = SavedProvider()
    if not any(saved.lookup(r)[0] for r in history if r.notes_present):
        pytest.skip("no saved history facts for the current model and prompt version")
    pending = load_requests("data/pending.csv").requests
    r = next(p for p in pending if p.request_id == "RF-2181")
    d = Decision(r, saved.get(r), Outcome("needs_review", "Delivery disputes", "R2"))
    rows = reviewer._similar(SimpleNamespace(history=history, history_provider=saved), d)
    assert [row["What happened"] for row in rows][:2] == ["Approved → chargeback (cost money)"] * 2
    assert "Outcome" not in rows[0]


def _with_rule(v1, rule):
    from smartrefunds.drafting import insert_rule, suggest_position

    return insert_rule(v1, rule, suggest_position(v1, rule))


def test_no_effect_names_the_rule_that_already_decides(v1):
    rule = Rule("M1", "Change of mind after 30 days → draft rejection",
                [Condition("stated_reason", "=", "changed_mind"), Condition("days_since_delivery", ">", 30)],
                Action("route_with_draft_rejection", "Policy rejections", "policy_2"))
    rows = [make_request(request_id=f"RF-C{i}", stated_reason="changed_mind", days_since_delivery=d)
            for i, d in enumerate([20, 35, 40])]
    res = preview(_with_rule(v1, rule), v1, rows, [], FixtureProvider(FIXTURE))
    text = " | ".join(res.warnings)
    assert text.startswith("No effect on history or today's queue")
    assert "R4" in text and "Change of mind after 14 days" in text and "2 cases" in text
    assert res.decided_first_by == {"R4": 2}


def test_no_effect_when_the_rule_matches_nothing(v1):
    rule = Rule("M1", "Huge refunds → person", [Condition("amount_usd", ">", 1_000_000)], Action("route", "Other"))
    res = preview(_with_rule(v1, rule), v1, [make_request()], [], FixtureProvider(FIXTURE))
    assert any("matches no past or current request" in w for w in res.warnings)


def test_manager_points_to_the_editable_value(v1):
    import sys

    sys.path.insert(0, "app")
    import manager

    window = Rule("M1", "Change of mind after 30 days → draft rejection",
                  [Condition("stated_reason", "=", "changed_mind"), Condition("days_since_delivery", ">", 30)],
                  Action("route_with_draft_rejection", "Policy rejections", "policy_2"))
    over_300 = Rule("M1", "Over $300 → person", [Condition("amount_usd", ">", 300)], Action("route", "by_reason"))
    refunds = Rule("M1", "More than 2 refunds → person", [Condition("prior_refunds_12m", ">", 2)],
                   Action("route", "by_reason"))
    late = Rule("M1", "Late over 10 days → person",  # days on another reason: not R4's window
                [Condition("stated_reason", "=", "late_delivery"), Condition("days_since_delivery", ">", 10)],
                Action("route", "by_reason"))
    assert "Change-of-mind window (days)" in manager.edit_hint(window, v1)
    assert "Auto-approve cap ($)" in manager.edit_hint(over_300, v1)
    assert "Prior refunds allowed in 12 months" in manager.edit_hint(refunds, v1)
    assert manager.edit_hint(late, v1) is None
