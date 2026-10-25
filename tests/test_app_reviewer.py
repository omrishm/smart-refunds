from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from smartrefunds.store import Store

APP = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@pytest.fixture
def var(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTREFUNDS_FIXTURE_FACTS", "tests/fixtures/pending_facts.jsonl")
    monkeypatch.setenv("SMARTREFUNDS_VAR", str(tmp_path / "var"))
    monkeypatch.setenv("SMARTREFUNDS_AI", "off")
    return tmp_path / "var"


def start():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def pick(widget, prefix):
    """Select the option whose label starts with `prefix`."""
    option = next(o for o in widget.options if o.startswith(prefix))
    return widget.set_value(option)


def open_case(at, lane, request_id, copy=None):
    pick(at.radio(key="lane"), lane).run()
    label = request_id if copy is None else f"{request_id} "
    options = [o for o in at.selectbox(key="case").options if o.startswith(label)]
    at.selectbox(key="case").set_value(options[0 if copy is None else copy]).run()
    assert not at.exception, at.exception
    return at


def all_text(at):
    parts = [e.value for e in at.markdown] + [e.value for e in at.caption] + [e.value for e in at.error]
    parts += [e.value for e in at.warning] + [e.value for e in at.info] + [e.value for e in at.success]
    return "\n".join(str(p) for p in parts)


def test_reviewer_tabs_counts(var):
    at = start()
    options = at.radio(key="lane").options
    for label in ["Fraud team (3)", "Delivery disputes (9)", "Damage & evidence (2)", "Policy rejections (1)",
                  "Other (3)", "Waiting (0)"]:
        assert label in options
    assert "Payments are simulated" in all_text(at)


def test_fraud_role_sees_only_fraud_lane(var):
    at = start()
    at.sidebar.radio(key="role").set_value("Fraud team").run()
    assert [o for o in at.radio(key="lane").options if not o.startswith("Waiting")] == ["Fraud team (3)"]


def test_northfield_incident_card(var):
    at = start()
    pick(at.radio(key="lane"), "Delivery disputes").run()
    text = all_text(at)
    assert "Northfield" in text and "5 cases" in text


def test_approve_writes_audit_and_payment(var):
    at = open_case(start(), "Damage & evidence", "RF-1076")
    at.selectbox(key="reason_code").set_value("Evidence in notes")
    at.button(key="approve_full").click().run()
    store = Store(var_dir=var)
    assert len(store.audit("RF-1076")) == 1
    assert [p["request_id"] for p in store.payments()] == ["RF-1076"]
    assert "Damage & evidence (1)" in at.radio(key="lane").options


def test_override_requires_reason_code(var):
    at = open_case(start(), "Delivery disputes", "RF-2181")
    at.button(key="approve_full").click().run()
    assert any("reason" in e.value.lower() for e in at.error)
    store = Store(var_dir=var)
    assert store.audit("RF-2181") == [] and store.payments() == []


def test_duplicate_copy_payment_refused(var):
    at = open_case(start(), "Other", "RF-2183", copy=0)
    at.button(key="approve_full").click().run()
    at = open_case(at, "Other", "RF-2183", copy=0)  # the remaining copy is now first in the list
    at.button(key="approve_full").click().run()
    assert any("already refunded" in e.value for e in at.error)
    assert len(Store(var_dir=var).payments()) == 1


def test_injection_warning_shown(var):
    at = open_case(start(), "Delivery disputes", "RF-1073")
    assert any("instruct" in e.value.lower() for e in at.error)


def test_request_info_moves_to_waiting_and_resume(var):
    at = open_case(start(), "Damage & evidence", "RF-1076")
    at.radio(key="info_what").set_value("photo")
    at.button(key="request_info").click().run()
    assert "Waiting (1)" in at.radio(key="lane").options
    at = open_case(at, "Waiting", "RF-1076")
    at.button(key="resume").click().run()
    assert "Waiting (0)" in at.radio(key="lane").options
