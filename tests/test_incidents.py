from datetime import date, timedelta

from conftest import make_facts, make_request
from smartrefunds.clean import load_requests
from smartrefunds.engine import Decision, FixtureProvider, decide_all
from smartrefunds.incidents import find_incidents
from smartrefunds.rules import Outcome


def test_northfield_pending(v1):
    decisions = decide_all(load_requests("data/pending.csv").requests, v1,
                           FixtureProvider("tests/fixtures/pending_facts.jsonl"))
    (inc,) = find_incidents(decisions)
    assert inc.tag == "northfield"
    assert inc.request_ids == ["RF-2196", "RF-2197", "RF-2199", "RF-2200", "RF-2201"]
    assert inc.total_amount == 726.0
    assert inc.first_seen == date(2026, 8, 26)
    assert "North field depot" in inc.spellings and "NFD hub" in inc.spellings


def _decision(i, day, tag="northfield"):
    r = make_request(request_id=f"RF-{i}", row_index=i, requested_at=date(2026, 8, 1) + timedelta(days=day),
                     stated_reason="never_arrived", support_note="stuck")
    return Decision(r, make_facts(location_tag=tag), Outcome("needs_review", "Delivery disputes", "R3"))


def test_two_cases_no_incident():
    assert find_incidents([_decision(1, 0), _decision(2, 1)]) == []


def test_outside_window_no_incident():
    assert find_incidents([_decision(1, 0), _decision(2, 10), _decision(3, 20)]) == []


def test_closed_cases_excluded():
    ds = [_decision(i, i) for i in range(1, 4)]
    assert len(find_incidents(ds)) == 1
    assert find_incidents(ds, open_ids={"RF-1", "RF-2"}) == []
