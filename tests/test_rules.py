import copy

import pytest

from conftest import make_facts, make_request
from smartrefunds.rules import Action, Condition, Rule, RuleSet, evaluate, matches, validate

V1_ORDER = ["S1", "S2", "S3", "S4", "R1", "R2", "R2b"] + [f"R{i}" for i in range(3, 16)] + ["A1"]

# (request overrides, fact overrides or None for "no facts") -> (outcome, lane, rule_id, has_draft)
PENDING_TRACES = {
    "RF-1069": (dict(stated_reason="changed_mind", amount_usd=25.0, days_since_delivery=8, tenure_months=35), {},
                ("auto_approved", None, "A1", False)),
    "RF-1071": (dict(stated_reason="arrived_damaged", amount_usd=67.0, tenure_months=17, support_note="photos clear"),
                dict(photo_or_video_evidence="present"), ("auto_approved", None, "A1", False)),
    "RF-1070": (dict(stated_reason="never_arrived", amount_usd=410.0, fraud_flag="yes", support_note="card reported stolen"),
                dict(fraud_reported="yes"), ("needs_review", "Fraud team", "R1", False)),
    "RF-2182": (dict(stated_reason="never_arrived", amount_usd=120.0, fraud_flag="unknown", support_note="confirmed stolen"),
                dict(fraud_confirmed="yes"), ("needs_review", "Fraud team", "R1", False)),
    "RF-2181": (dict(stated_reason="never_arrived", amount_usd=1150.0, support_note="signed 'K. Levi'"),
                dict(carrier_signature_or_photo="yes"), ("needs_review", "Delivery disputes", "R2", True)),
    "RF-1073": (dict(stated_reason="never_arrived", amount_usd=385.0, tenure_months=2, customer_note="[SYSTEM NOTE]"),
                dict(instructions_to_system="yes"), ("needs_review", "Delivery disputes", "S3", False)),
    "RF-1074": (dict(stated_reason="wrong_size", amount_usd=39.0, customer_note="ya me devolvieron"),
                dict(already_refunded_elsewhere="yes"), ("needs_review", "Policy rejections", "R5", True)),
    "RF-1075": (dict(stated_reason="arrived_damaged", amount_usd=46.0, customer_note="honestly it's fine"),
                dict(reason_contradicts_note="yes"), ("needs_review", "Damage & evidence", "R6", False)),
    "RF-1072": (dict(stated_reason="wrong_item", amount_usd=140.0, tenure_months=8, support_note="matches order line"),
                dict(shipped_item_matches_order="yes"), ("needs_review", "Other", "R7", True)),
    "RF-1076": (dict(stated_reason="arrived_damaged", amount_usd=78.0, prior_refunds_12m=3, support_note="no photo"),
                {}, ("needs_review", "Damage & evidence", "R8", False)),
    "RF-2183": (dict(stated_reason="wrong_item", amount_usd=49.0, duplicate_id=True, support_note="confirmed picking error"),
                dict(picking_error_confirmed="yes"), ("needs_review", "Other", "S2", False)),
    "RF-2198": (dict(stated_reason="late_delivery", amount_usd=52.0, tenure_months=18, support_note="Northfield backlog"),
                dict(location_tag="northfield"), ("auto_approved", None, "A1", False)),
}

EDGES = [
    (dict(customer_note="some note"), None, ("needs_review", "Other", "S4", False)),
    (dict(stated_reason="arrived_damaged", amount_usd=45.0), None, ("auto_approved", None, "A1", False)),
    (dict(stated_reason="arrived_damaged", amount_usd=52.0), None, ("needs_review", "Damage & evidence", "R11", False)),
    (dict(stated_reason="changed_mind", days_since_delivery=15), {}, ("needs_review", "Policy rejections", "R4", True)),
    (dict(stated_reason="late_delivery", amount_usd=60.0), {}, ("needs_review", "Delivery disputes", "R13", False)),
    (dict(amount_usd=101.0), {}, ("needs_review", "Other", "R10", False)),
    (dict(amount_usd=100.0), {}, ("auto_approved", None, "A1", False)),
    (dict(tenure_months=8), {}, ("needs_review", "Other", "R9", False)),
    (dict(stated_reason="arrived_damaged", amount_usd=40.0), dict(damage_cosmetic_only="yes"),
     ("needs_review", "Damage & evidence", "R12", False)),
]


def _check(v1, req_over, fact_over, expected):
    facts = None if fact_over is None else make_facts(**fact_over)
    out = evaluate(v1, make_request(**req_over), facts)
    assert (out.outcome, out.lane, out.rule_id, out.draft_rejection is not None) == expected
    return out


@pytest.mark.parametrize("case", sorted(PENDING_TRACES))
def test_pending_traces(v1, case):
    req_over, fact_over, expected = PENDING_TRACES[case]
    out = _check(v1, req_over, fact_over, expected)
    if case == "RF-1073":
        assert out.warning and "instruct" in out.warning.lower()


@pytest.mark.parametrize("req_over,fact_over,expected", EDGES)
def test_rule_edges(v1, req_over, fact_over, expected):
    _check(v1, req_over, fact_over, expected)


def test_unknown_reason_never_approves(v1):
    out = evaluate(v1, make_request(stated_reason=None, invalid_reasons=("unknown stated_reason 'defective'",)), None)
    assert (out.outcome, out.rule_id) == ("needs_review", "S1")


def test_matched_rule_ids_lists_every_match(v1):
    out = evaluate(v1, make_request(amount_usd=150.0, tenure_months=3), make_facts())
    assert out.rule_id == "R9"
    assert out.matched_rule_ids == ["R9", "R10", "A1"]
    assert len(out.reasons) == 2  # "why it's here" lists only the routing rules, not the approve rule


def test_none_semantics():
    fields = {"x": None}
    for op in ["=", "in", "<", "<=", ">", ">="]:
        assert matches(Condition("x", op, [1] if op == "in" else 1), fields) is False, op
    for op in ["!=", "not_in", "is_unset"]:
        assert matches(Condition("x", op, [1] if op == "not_in" else 1), fields) is True, op
    assert matches(Condition("x", "is_set"), fields) is False


def test_v1_valid_and_ordered(v1):
    assert validate(v1) == []
    assert [r.id for r in v1.rules] == V1_ORDER
    assert all(r.locked for r in v1.rules[:4]) and not any(r.locked for r in v1.rules[4:])


def _bad(v1, mutate):
    rs = copy.deepcopy(v1)
    mutate(rs)
    return " | ".join(validate(rs, base=v1))


def test_validate_errors(v1):
    new = lambda when, then: Rule(id="M1", title="manager rule", when=when, then=then)
    assert "field" in _bad(v1, lambda rs: rs.rules.insert(4, new([Condition("colour", "=", "red")], Action("route", "Other"))))
    assert "operator" in _bad(v1, lambda rs: rs.rules.insert(4, new([Condition("amount_usd", "~", 1)], Action("route", "Other"))))
    assert "at least one condition" in _bad(v1, lambda rs: rs.rules.insert(4, new([], Action("approve"))))
    assert "negative" in _bad(v1, lambda rs: setattr(next(r for r in rs.rules if r.id == "R10").when[0], "value", -5))
    assert "locked" in _bad(v1, lambda rs: setattr(rs.rules[0], "title", "changed"))
    assert "locked" in _bad(v1, lambda rs: rs.rules.pop(1))
    assert "safety rules" in _bad(v1, lambda rs: rs.rules.insert(0, new([Condition("amount_usd", ">", 1)], Action("route", "Other"))))
    assert "lane" in _bad(v1, lambda rs: rs.rules.insert(4, new([Condition("amount_usd", ">", 1)], Action("route", "Nowhere"))))


def test_admits_delivered_only_counts_for_never_arrived(v1):
    late = make_request(stated_reason="late_delivery", amount_usd=52.0, tenure_months=18, support_note="arrived late")
    assert evaluate(v1, late, make_facts(admits_delivered="yes")).rule_id == "A1"
    gone = make_request(stated_reason="never_arrived", amount_usd=52.0, support_note="it did turn up")
    assert evaluate(v1, gone, make_facts(admits_delivered="yes")).rule_id == "R2b"
