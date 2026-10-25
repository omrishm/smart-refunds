"""Shared state for the Streamlit demo: data, AI facts, store, live rules, today's decisions.

Environment overrides (used by tests): SMARTREFUNDS_PENDING, SMARTREFUNDS_HISTORY, SMARTREFUNDS_VAR,
SMARTREFUNDS_AI (auto|live|saved|off), SMARTREFUNDS_FACTS_DIR, SMARTREFUNDS_FIXTURE_FACTS.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from smartrefunds.clean import Request, load_requests
from smartrefunds.engine import Decision, FactsProvider, FixtureProvider, decide_all
from smartrefunds.facts import Facts
from smartrefunds.incidents import Incident, find_incidents
from smartrefunds.notes import make_provider
from smartrefunds.rules import RuleSet
from smartrefunds.store import Store


class MemoProvider:
    """Remembers facts per row for the session, so Streamlit reruns never repeat a paid API call."""

    def __init__(self, inner: FactsProvider):
        self.inner, self.mode, self._memo = inner, inner.mode, {}

    def get(self, r: Request) -> Facts | None:
        key = (r.request_id, r.row_index, r.customer_note, r.support_note)
        if key not in self._memo:
            self._memo[key] = self.inner.get(r)
        return self._memo[key]


@dataclass
class Context:
    history: list[Request]
    pending: list[Request]
    provider: FactsProvider
    store: Store
    live: RuleSet
    decisions: list[Decision]
    incidents: list[Incident]
    facts_source: str


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@st.cache_resource(show_spinner=False)
def _load(pending_path: str, history_path: str, ai: str, facts_dir: str, fixture: str):
    pending = load_requests(pending_path).requests
    history = load_requests(history_path).requests
    if fixture:
        provider: FactsProvider = FixtureProvider(fixture)
        source = f"hand-labelled fixture ({fixture})"
    else:
        provider = make_provider(ai, Path(facts_dir))
        source = f"AI mode: {provider.mode} · saved facts in {facts_dir}"
    return pending, history, MemoProvider(provider), source


def load_context() -> Context:
    pending, history, provider, source = _load(
        _env("SMARTREFUNDS_PENDING", "data/pending.csv"), _env("SMARTREFUNDS_HISTORY", "data/history.csv"),
        _env("SMARTREFUNDS_AI", "auto"), _env("SMARTREFUNDS_FACTS_DIR", "data/facts"),
        os.environ.get("SMARTREFUNDS_FIXTURE_FACTS", ""),
    )
    store = Store(var_dir=_env("SMARTREFUNDS_VAR", "var"))
    live = store.live_rules()
    decisions = decide_all(pending, live, provider)
    open_ids = {d.request.request_id for d in decisions
                if store.status(d.request.request_id, d.request.row_index) == "open"}
    return Context(history, pending, provider, store, live, decisions, find_incidents(decisions, open_ids), source)
