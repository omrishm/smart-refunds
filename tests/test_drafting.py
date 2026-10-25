from types import SimpleNamespace

import pytest

from smartrefunds.clean import load_requests
from smartrefunds.drafting import (
    NO_KEY_ERROR,
    DraftCondition,
    DraftOutput,
    DraftRule,
    DraftChoiceOut,
    draft_rule,
    insert_rule,
    overridden_rules,
    suggest_position,
)
from smartrefunds.engine import FixtureProvider
from smartrefunds.preview import preview

STOLEN = "From now on, stolen-card cases go straight through."
OVER_300 = "From now on, anything over $300 comes to us."
FIXTURE = "tests/fixtures/pending_facts.jsonl"


@pytest.fixture(scope="module")
def pending():
    return load_requests("data/pending.csv").requests


def fake_client(output):
    msgs = SimpleNamespace(calls=[])

    def parse(**kw):
        msgs.calls.append(kw)
        return SimpleNamespace(parsed_output=output, stop_reason="end_turn", model="claude-opus-5-5")

    msgs.parse = parse
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs))


def test_saved_draft_matches_variants(v1):
    for text in [STOLEN, "from now on   stolen-card cases go straight through"]:
        d = draft_rule(text, v1)
        assert d.source == "saved" and d.error is None
        assert d.question and len(d.choices) == 2
        assert d.choices[1].label.startswith("Reported")


def test_no_key_unknown_text_error(v1):
    d = draft_rule("Northfield cases under $100: refund in full", v1, client=None)
    assert d.error == NO_KEY_ERROR and d.choices == [] and d.source == "none"
    assert "no API key configured" in NO_KEY_ERROR


def test_live_draft_with_fake_client(v1):
    out = DraftOutput(question=None, choices=[DraftChoiceOut(
        label="Northfield under $100", rule=DraftRule(
            title="Northfield never-arrived under $100 → approve",
            when=[DraftCondition(field="facts.location_tag", op="=", value="northfield"),
                  DraftCondition(field="amount_usd", op="<", value=100)],
            kind="approve", lane=None, policy=None))])
    client = fake_client(out)
    d = draft_rule("Northfield cases under $100: refund in full", v1, client=client)
    assert d.error is None and d.source == "live"
    (choice,) = d.choices
    assert choice.rule.id == "M1" and choice.rule.then.kind == "approve"
    assert client.beta.messages.calls[0]["model"] == "claude-opus-5-5"


def test_live_draft_invalid_field_rejected(v1):
    bad = DraftOutput.model_construct(question=None, choices=[DraftChoiceOut.model_construct(
        label="x", rule=DraftRule.model_construct(
            title="bad", when=[DraftCondition.model_construct(field="colour", op="=", value="red")],
            kind="route", lane="Other", policy=None))])
    d = draft_rule("send red ones to us", v1, client=fake_client(bad))
    assert d.choices == [] and d.error.startswith("Couldn't turn that into a valid rule:")


def test_suggest_position(v1):
    stolen = draft_rule(STOLEN, v1).choices[1].rule
    over = draft_rule(OVER_300, v1).choices[0].rule
    assert suggest_position(v1, stolen) == 4
    assert suggest_position(v1, over) == [r.id for r in v1.rules].index("A1")


def _rows(pending):
    fx = FixtureProvider(FIXTURE)
    return [(r, fx.get(r)) for r in pending]


def test_overridden_rules_stolen(v1, pending):
    rule = draft_rule(STOLEN, v1).choices[1].rule
    cand = insert_rule(v1, rule, suggest_position(v1, rule))
    assert cand.version == v1.version + 1 and [r.id for r in cand.rules][4] == "M1"
    # every check these cases would otherwise hit, in list order, not just the one that used to decide
    assert overridden_rules(v1, cand, _rows(pending)) == ["R1", "R3", "R9", "R10"]
    res = preview(cand, v1, [], pending, FixtureProvider(FIXTURE))
    assert {c.request_id for c in res.pending.changed} == {"RF-2182", "RF-1070"}


def test_over_300_no_effect(v1, pending):
    rule = draft_rule(OVER_300, v1).choices[0].rule
    cand = insert_rule(v1, rule, suggest_position(v1, rule))
    res = preview(cand, v1, [], pending, FixtureProvider(FIXTURE))
    assert res.pending.changed == []
    assert overridden_rules(v1, cand, _rows(pending)) == []


def test_live_failure_keeps_the_error_message(v1, caplog):
    import logging

    class Boom:
        def parse(self, **kw):
            raise TypeError("'NoneType' object is not iterable")

    client = SimpleNamespace(beta=SimpleNamespace(messages=Boom()))
    with caplog.at_level(logging.ERROR, logger="smartrefunds.drafting"):
        d = draft_rule("send odd ones to us", v1, client=client)
    assert "TypeError: 'NoneType' object is not iterable" in d.error
    assert any("Traceback" in (r.exc_text or "") or r.exc_info for r in caplog.records)
