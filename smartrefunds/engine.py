"""Pipeline: cleaned requests + AI facts + rules → one decision per request."""
from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .clean import REQUIRED_COLUMNS, Request
from .facts import Facts
from .rules import Outcome, RuleSet, evaluate

OUTPUT_COLUMNS = list(REQUIRED_COLUMNS) + [
    "outcome", "lane", "rule_id", "reasons", "draft_rejection", "cleaning_changes",
]


class FactsProvider(Protocol):
    mode: str  # "live" | "saved" | "off"

    def get(self, r: Request) -> Facts | None: ...


class OffProvider:
    """No AI: every row has no facts, so safety rule S4 sends anything with notes to a person."""

    mode = "off"

    def get(self, r: Request) -> Facts | None:
        return None


class FixtureProvider:
    """Hand-labelled facts keyed by (request_id, row_index). Used by tests and the offline demo fixture."""

    mode = "saved"

    def __init__(self, path: str | Path):
        self._facts: dict[tuple[str, int], Facts] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                self._facts[(rec["request_id"], rec["row_index"])] = Facts.model_validate(rec["facts"])

    def get(self, r: Request) -> Facts | None:
        return self._facts.get((r.request_id, r.row_index))


@dataclass
class Decision:
    request: Request
    facts: Facts | None
    outcome: Outcome


def decide_all(requests: list[Request], rs: RuleSet, provider: FactsProvider) -> list[Decision]:
    out = []
    for r in requests:
        facts = provider.get(r)
        out.append(Decision(r, facts, evaluate(rs, r, facts)))
    return out


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ", timespec="minutes") if hasattr(v, "hour") else v.isoformat()
    return str(v)


def write_decisions_csv(decisions: list[Decision], path: Path) -> None:
    """Writes the cleaned input columns plus the decision. Raw values that cleaning changed are listed
    in `cleaning_changes`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(OUTPUT_COLUMNS)
        for d in decisions:
            r, o = d.request, d.outcome
            row = [_cell(getattr(r, c)) for c in REQUIRED_COLUMNS]
            row += [o.outcome, o.lane or "", o.rule_id, "; ".join(o.reasons), o.draft_rejection or "",
                    "; ".join(r.cleaning_changes)]
            w.writerow(row)


def summarize(decisions: list[Decision], ai_mode: str) -> str:
    auto = [d for d in decisions if d.outcome.outcome == "auto_approved"]
    review = Counter(d.outcome.lane for d in decisions if d.outcome.outcome == "needs_review")
    total = sum(d.request.amount_usd or 0 for d in auto)
    lines = [
        f"{len(decisions)} requests | AI mode: {ai_mode}",
        f"  auto-approved: {len(auto)} (${total:,.2f})",
        f"  needs review:  {sum(review.values())}",
    ]
    lines += [f"    {lane}: {n}" for lane, n in sorted(review.items())]
    return "\n".join(lines)
