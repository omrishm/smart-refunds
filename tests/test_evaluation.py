import pytest

from conftest import make_facts, make_request
from smartrefunds.evaluation import EvalCase, load_cases, score


def case(cid, expected, **req):
    return EvalCase(id=cid, source="test", request=make_request(request_id=cid, **req), expected=expected,
                    label_note="")


CASES = [
    case("A", {"instructions_to_system": "yes"}, stated_reason="never_arrived", customer_note="SYSTEM NOTE"),
    case("B", {"photo_or_video_evidence": "present"}, stated_reason="arrived_damaged", amount_usd=67.0,
         support_note="photos clear"),
    case("C", {"admits_delivered": "yes"}, stated_reason="never_arrived", customer_note="it turned up"),
]


def truth(c):
    return make_facts(**c.expected)


def test_perfect_prediction():
    s = score(CASES, {c.id: truth(c) for c in CASES})
    assert s["escalation_recall"] == 1.0 and s["injection_pass"] is True
    assert s["routing_agreement"] == 1.0 and s["unsafe_auto_approvals"] == [] and s["unavailable"] == 0


def test_missed_injection_fails():
    preds = {c.id: truth(c) for c in CASES}
    preds["A"] = make_facts()
    s = score(CASES, preds)
    assert s["injection_pass"] is False and s["escalation_recall"] < 1.0


def test_unavailable_is_not_correct():
    preds = {c.id: truth(c) for c in CASES}
    preds["C"] = None
    s = score(CASES, preds)
    assert s["unavailable"] == 1
    assert s["escalation_recall"] < 1.0  # a missing answer never counts as catching the flag


def test_false_approval_evidence_is_unsafe():
    cases = [case("D", {}, stated_reason="arrived_damaged", amount_usd=80.0, support_note="no photo")]
    s = score(cases, {"D": make_facts(photo_or_video_evidence="present")})
    assert s["unsafe_auto_approvals"] == ["D"]
    assert s["per_fact"]["photo_or_video_evidence"]["fp"] == 1


def test_eval_set_loads_and_quotes_exist():
    cases = load_cases()
    assert 30 <= len(cases) <= 45
    ids = {c.id for c in cases}
    for must in ["pending:RF-1073", "pending:RF-1074", "pending:RF-2182", "history:RF-2068", "history:RF-2161"]:
        assert must in ids
    for c in cases:
        notes = (c.request.customer_note + " " + c.request.support_note).lower()
        for name, value in c.expected.items():
            assert value not in ("not_mentioned", None), (c.id, name)
        for name, quote in c.evidence.items():
            assert quote.lower() in notes, (c.id, name, quote)


def test_eval_command_with_fake_client(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from smartrefunds import notes
    from smartrefunds.cli import main

    def parse(**kw):
        return SimpleNamespace(parsed_output=make_facts(), stop_reason="end_turn", model=kw["model"],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(notes, "_client_from_env", lambda: client)
    assert main(["eval", "--yes", "--models", "claude-haiku-5-5", "--out-dir", str(tmp_path)]) == 0
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "| claude-haiku-5-5 |" in summary and "FAIL" in summary  # all-empty facts miss the injection case
    assert list(tmp_path.glob("*-claude-haiku-5-5.json"))
