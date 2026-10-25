"""Replay a rule set over history and today's queue, and say what would change before anything goes live.

One engine serves both the manager's preview and the slide evidence (backtest), so the numbers agree.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from .clean import Request
from .engine import Decision, FactsProvider, decide_all
from .rules import RuleSet

HistoryLabel = Literal["bad", "should_approve", "ok"]


def history_label(r: Request) -> HistoryLabel:
    """What a full auto-approval of this historical request would have meant, judged by what happened after."""
    note = r.outcome_note.lower()
    overturned = "overturned" in note or "refunded on appeal" in note
    if "chargeback" in note:
        return "bad"
    if r.decision == "rejected":
        return "should_approve" if overturned else "bad"
    if r.decision == "partial":
        return "bad"
    return "ok"


def describe_outcome(r: Request) -> tuple[str, int]:
    """What happened to a past request, in plain words for a reviewer, plus a sort key (money lost first).

    Unlike history_label (which answers "would a full auto-approval have been a mistake?" for the backtest),
    this describes the decision that was actually made and what followed it."""
    note = r.outcome_note.lower()
    if "chargeback" in note:
        return "Approved → chargeback (cost money)", 0
    if r.decision == "rejected" and ("overturned" in note or "refunded on appeal" in note):
        return "Rejected → overturned on appeal", 1
    if r.decision == "partial":
        return f"Partial refund ({r.outcome_note})" if r.outcome_note else "Partial refund", 2
    if r.decision == "rejected":
        return "Rejected → stood", 3
    if r.decision == "approved":
        return "Approved → no issues", 4
    return r.decision or "Not decided", 5


@dataclass
class Change:
    request_id: str
    row_index: int
    amount: float | None
    before: str
    after: str


@dataclass
class DatasetPreview:
    n_rows: int = 0
    auto_count: int = 0
    auto_amount: float = 0.0
    minutes_saved: float = 0.0
    bad_let_through: list[str] = field(default_factory=list)
    lane_counts: dict[str, int] = field(default_factory=dict)
    changed: list[Change] = field(default_factory=list)
    largest_new_auto: tuple[str, float] | None = None


@dataclass
class PreviewResult:
    history: DatasetPreview
    pending: DatasetPreview
    warnings: list[str]
    # For a new rule with no effect: earlier rules that already decide the cases it matches (rule id -> count).
    decided_first_by: dict[str, int] = field(default_factory=dict)


def _state(d: Decision) -> str:
    if d.outcome.outcome == "auto_approved":
        return "auto_approved"
    return f"{d.outcome.lane}" + (" (draft rejection)" if d.outcome.draft_rejection else "")


def _money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def _dataset(cand: list[Decision], live: list[Decision], is_history: bool) -> DatasetPreview:
    p = DatasetPreview(n_rows=len(cand))
    auto = [d for d in cand if d.outcome.outcome == "auto_approved"]
    p.auto_count = len(auto)
    p.auto_amount = sum(d.request.amount_usd or 0 for d in auto)
    p.lane_counts = dict(Counter(d.outcome.lane for d in cand if d.outcome.outcome == "needs_review"))
    if is_history:
        p.bad_let_through = [d.request.request_id for d in auto if history_label(d.request) == "bad"]
    new_auto = []
    for c, l in zip(cand, live):
        before, after = _state(l), _state(c)
        if before != after:
            p.changed.append(Change(c.request.request_id, c.request.row_index, c.request.amount_usd, before, after))
        if after == "auto_approved" and before != "auto_approved":
            new_auto.append(c)
            if is_history and c.request.decided_by != "auto":
                p.minutes_saved += c.request.review_minutes or 0
    if new_auto:
        top = max(new_auto, key=lambda d: d.request.amount_usd or 0)
        p.largest_new_auto = (top.request.request_id, top.request.amount_usd or 0.0)
    return p


def preview(candidate: RuleSet, live: RuleSet, history: list[Request], pending: list[Request],
            provider: FactsProvider) -> PreviewResult:
    h_c, h_l = decide_all(history, candidate, provider), decide_all(history, live, provider)
    p_c, p_l = decide_all(pending, candidate, provider), decide_all(pending, live, provider)
    hist, pend = _dataset(h_c, h_l, True), _dataset(p_c, p_l, False)

    warnings: list[str] = []
    decided_first_by: dict[str, int] = {}
    if not hist.changed and not pend.changed:
        new_ids = {r.id for r in candidate.rules} - {r.id for r in live.rules}
        if not new_ids:
            warnings.append("No effect on history or today's queue")
        else:
            counts = Counter(d.outcome.rule_id for d in h_c + p_c
                             if new_ids & set(d.outcome.matched_rule_ids) and d.outcome.rule_id not in new_ids)
            decided_first_by = dict(counts.most_common())
            if not counts:
                warnings.append("No effect on history or today's queue: this rule matches no past or current request")
            else:
                titles = {r.id: r.title for r in candidate.rules}
                listed = ", ".join(f"{rid} “{titles.get(rid, rid)}” ({n} cases)" for rid, n in counts.most_common(3))
                warnings.append(f"No effect on history or today's queue: the {sum(counts.values())} cases this rule "
                                f"matches are already decided by earlier rules: {listed}")
    skipped = Counter(ch.before.split(" (")[0] for ch in hist.changed + pend.changed
                      if ch.after == "auto_approved" and ch.before != "auto_approved")
    for lane, n in sorted(skipped.items()):
        warnings.append(f"Skips the {lane} for {n} cases")
    largest = [x for x in (hist.largest_new_auto, pend.largest_new_auto) if x]
    if largest:
        rid, amount = max(largest, key=lambda x: x[1])
        warnings.append(f"Largest single new auto-approval: {_money(amount)} ({rid})")
    if 1 <= len(hist.changed) < 20:
        warnings.append(f"Small sample: only {len(hist.changed)} history cases change")
    live_bad = {d.request.request_id for d in h_l if d.outcome.outcome == "auto_approved"
                and history_label(d.request) == "bad"}
    new_bad = [rid for rid in hist.bad_let_through if rid not in live_bad]
    if new_bad:
        warnings.append(f"Lets through {len(new_bad)} cases with bad outcomes in history ({', '.join(new_bad)})")
    return PreviewResult(hist, pend, warnings, decided_first_by)


def backtest_summary(rs: RuleSet, history: list[Request], provider: FactsProvider) -> str:
    """The line-slide evidence: this rule set on history, next to what the legacy auto rule actually did."""
    decisions = decide_all(history, rs, provider)
    auto = [d for d in decisions if d.outcome.outcome == "auto_approved"]
    bad = [d.request.request_id for d in auto if history_label(d.request) == "bad"]
    saved = sum(d.request.review_minutes or 0 for d in auto if d.request.decided_by != "auto")
    human_minutes = sum(r.review_minutes or 0 for r in history if r.decided_by != "auto")
    legacy = [r for r in history if r.decided_by == "auto"]
    legacy_bad = [r.request_id for r in legacy if history_label(r) == "bad"]
    n = len(history)
    lanes = Counter(d.outcome.lane for d in decisions if d.outcome.outcome == "needs_review")
    lines = [
        f"Backtest on {n} historical requests (AI mode: {provider.mode})",
        f"  Rule set v{rs.version}: auto-approves {len(auto)} ({len(auto) / n:.0%}), "
        f"{_money(sum(d.request.amount_usd or 0 for d in auto))}; bad outcomes let through: {len(bad)} {bad}",
        f"    reviewer minutes saved vs today: {saved:g} of {human_minutes:g}",
        f"    to people by lane: " + ", ".join(f"{k} {v}" for k, v in sorted(lanes.items())),
        f"  Legacy auto-approve rule (actual): {len(legacy)} ({len(legacy) / n:.0%}), "
        f"{_money(sum(r.amount_usd or 0 for r in legacy))}; bad outcomes: {len(legacy_bad)} {legacy_bad}",
    ]
    return "\n".join(lines)
