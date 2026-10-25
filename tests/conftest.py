from dataclasses import replace
from datetime import date

import pytest

from smartrefunds.clean import Request
from smartrefunds.facts import Facts, FACT_NAMES

_BASE = Request(
    row_index=0, request_id="RF-TEST", requested_at=date(2026, 9, 1), customer_id="CU-1",
    tenure_months=24, prior_refunds_12m=0, amount_usd=30.0, category="apparel", payment_method="card",
    stated_reason="wrong_size", customer_note="", support_note="", days_since_delivery=5, fraud_flag="no",
)


def make_request(**overrides) -> Request:
    return replace(_BASE, **overrides)


def make_facts(**overrides) -> Facts:
    """All facts 'not_mentioned' unless overridden; overrides are plain values, e.g. carrier_signature_or_photo='yes'."""
    data = {name: {"value": "not_mentioned", "evidence": ""} for name in FACT_NAMES}
    data["location_tag"] = {"value": None, "evidence": ""}
    data["summary"] = "test"
    data["language"] = "en"
    for name, value in overrides.items():
        data[name] = {"value": value, "evidence": f"quote for {name}"}
    return Facts.model_validate(data)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch, tmp_path):
    """Tests never call the API: blank the key so .env (loaded by the CLI and app) can't supply one.
    Tests also never see the developer's demo state: rules published while clicking through var/ stay out."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("SMARTREFUNDS_VAR", str(tmp_path / "test-var"))


@pytest.fixture
def v1():
    from smartrefunds.rules import load_rules

    return load_rules("rules/v1.yaml")
