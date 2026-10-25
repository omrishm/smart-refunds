import copy

import pytest

from smartrefunds.rules import rule_to_dict
from smartrefunds.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(var_dir=tmp_path / "var")


def test_pay_once(store):
    assert store.pay("RF-2183", 52.0, "card").ok
    second = store.pay("RF-2183", 49.0, "card")
    assert not second.ok and "already refunded" in second.message
    assert len(store.payments()) == 1


def test_publish_requires_reason_and_valid(store):
    rs = copy.deepcopy(store.live_rules())
    with pytest.raises(ValueError, match="reason"):
        store.publish(rs, reason="  ")
    bad = copy.deepcopy(rs)
    bad.rules[0].title = "edited safety rule"
    with pytest.raises(ValueError, match="locked"):
        store.publish(bad, reason="try")


def test_publish_and_live(store):
    rs = copy.deepcopy(store.live_rules())
    next(r for r in rs.rules if r.id == "R10").when[0].value = 55
    assert store.publish(rs, reason="Lower cap") == 2
    live = store.live_rules()
    assert live.version == 2 and live.reason == "Lower cap"
    assert next(r for r in live.rules if r.id == "R10").when[0].value == 55


def test_rollback_creates_new_version(store):
    v1 = store.live_rules()
    rs = copy.deepcopy(v1)
    rs.rules.pop(-2)  # drop R15
    store.publish(rs, reason="drop R15")
    assert store.rollback(1, reason="undo") == 3
    v3 = store.live_rules()
    assert [rule_to_dict(r) for r in v3.rules] == [rule_to_dict(r) for r in v1.rules]
    assert [v for v, _, _ in store.versions()] == [1, 2, 3]


def test_waiting_resume(store):
    assert store.status("RF-1076", 9) == "open"
    store.set_waiting("RF-1076", 9, "photo")
    assert store.status("RF-1076", 9) == "waiting"
    store.resume("RF-1076", 9)
    assert store.status("RF-1076", 9) == "open"


def test_audit_records_rule_version(store):
    store.record_decision("RF-1076", 9, "Reviewer", "approve_full", 78.0, "Evidence in notes", "photo came in",
                          rule_version=1, suggestion="needs_review: Damage & evidence",
                          flag_feedback={"photo_or_video_evidence": False})
    (entry,) = store.audit("RF-1076")
    assert entry["rule_version"] == 1 and entry["reason_code"] == "Evidence in notes"
    assert entry["flag_feedback"] == {"photo_or_video_evidence": False}
    assert store.status("RF-1076", 9) == "decided"


def test_reset(store):
    store.pay("RF-1", 10.0, "card")
    store.publish(copy.deepcopy(store.live_rules()), reason="same again")
    store.reset()
    assert store.payments() == [] and store.live_rules().version == 1


def test_reset_demo_command(tmp_path):
    from smartrefunds.cli import main

    s = Store(var_dir=tmp_path / "var")
    s.pay("RF-1", 10.0, "card")
    assert main(["reset-demo", "--var", str(tmp_path / "var")]) == 0
    assert Store(var_dir=tmp_path / "var").payments() == []


def test_lane_override(store):
    assert store.lane_override("RF-1072", 5) is None
    store.set_lane("RF-1072", 5, "Fraud team")
    assert store.lane_override("RF-1072", 5) == "Fraud team"
    store.set_waiting("RF-1072", 5, "photo")
    assert store.lane_override("RF-1072", 5) == "Fraud team" and store.status("RF-1072", 5) == "waiting"
