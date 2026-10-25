"""Group open cases that share a place or cause (the AI's location_tag) into incidents, e.g. a depot backlog."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .engine import Decision
from .places import normalise_place


@dataclass
class Incident:
    tag: str
    request_ids: list[str]
    total_amount: float
    first_seen: date
    spellings: list[str]


def find_incidents(decisions: list[Decision], open_ids: set[str] | None = None, min_cases: int = 3,
                   window_days: int = 14) -> list[Incident]:
    by_tag: dict[str, list[Decision]] = {}
    for d in decisions:
        if d.outcome.outcome != "needs_review" or d.facts is None or d.request.requested_at is None:
            continue
        if open_ids is not None and d.request.request_id not in open_ids:
            continue
        tag = normalise_place(d.facts.location_tag.value)
        if tag:
            by_tag.setdefault(tag, []).append(d)

    incidents = []
    for tag, group in sorted(by_tag.items()):
        group.sort(key=lambda d: (d.request.requested_at, d.request.request_id))
        best: list[Decision] = []
        start = 0
        for end in range(len(group)):  # largest set inside any window_days span
            while group[end].request.requested_at - group[start].request.requested_at > timedelta(days=window_days):
                start += 1
            if end - start + 1 > len(best):
                best = group[start:end + 1]
        if len(best) >= min_cases:
            spellings = list(dict.fromkeys(d.facts.location_tag.evidence for d in best if d.facts.location_tag.evidence))
            incidents.append(Incident(
                tag=tag,
                request_ids=sorted(d.request.request_id for d in best),
                total_amount=sum(d.request.amount_usd or 0 for d in best),
                first_seen=best[0].request.requested_at,
                spellings=spellings,
            ))
    return incidents
