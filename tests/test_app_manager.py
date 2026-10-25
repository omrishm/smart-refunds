from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from smartrefunds.store import Store

APP = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
STOLEN = "From now on, stolen-card cases go straight through."
OVER_300 = "From now on, anything over $300 comes to us."


@pytest.fixture
def var(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTREFUNDS_FIXTURE_FACTS", "tests/fixtures/pending_facts.jsonl")
    monkeypatch.setenv("SMARTREFUNDS_VAR", str(tmp_path / "var"))
    monkeypatch.setenv("SMARTREFUNDS_AI", "off")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    return tmp_path / "var"


def manager():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.sidebar.radio(key="role").set_value("Manager").run()
    assert not at.exception, at.exception
    return at


def text(at):
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption] + [e.value for e in at.warning]
    parts += [e.value for e in at.info] + [e.value for e in at.error] + [e.value for e in at.success]
    parts += [df.value.to_string() for df in at.dataframe]
    return "\n".join(str(p) for p in parts)


def draft(at, sentence):
    at.text_area(key="change_text").input(sentence)
    at.button(key="draft").click().run()
    assert not at.exception, at.exception
    return at


def test_rules_listed_with_locks(var):
    t = text(manager())
    assert "S1" in t and "locked" in t and "A1" in t


def test_cap_edit_preview(var):
    at = manager()
    at.number_input(key="edit_cap").set_value(55).run()
    assert "RF-1071" in text(at)


def test_stolen_card_saved_draft_flow(var):
    at = draft(manager(), STOLEN)
    choice = at.radio(key="choice")
    assert len(choice.options) == 2
    choice.set_value(next(o for o in choice.options if o.startswith("Reported"))).run()
    t = text(at)
    assert "Skips the Fraud team" in t
    assert "RF-2182" in t and "RF-1070" in t


def test_over_300_no_effect(var):
    assert "No effect" in text(draft(manager(), OVER_300))


def test_unknown_sentence_without_key(var):
    assert "no API key configured" in text(draft(manager(), "Northfield cases under $100: refund in full"))


def test_publish_requires_reason(var):
    at = manager()
    at.number_input(key="edit_cap").set_value(55).run()
    assert at.button(key="publish").disabled


def test_publish_then_rollback(var):
    at = manager()
    at.number_input(key="edit_cap").set_value(55).run()
    at.text_input(key="publish_reason").input("Tighter cap while we watch chargebacks").run()
    at.button(key="publish").click().run()
    store = Store(var_dir=var)
    assert [v for v, _, _ in store.versions()] == [1, 2]
    at.button(key="rollback_1").click().run()
    at.text_input(key="publish_reason").input("Back to v1").run()
    at.button(key="publish").click().run()
    assert [v for v, _, _ in store.versions()] == [1, 2, 3]
    assert next(r for r in store.live_rules().rules if r.id == "R10").when[0].value == 100
