"""Plain English → candidate rules. The AI only drafts; the manager confirms the structured rule, sees the
preview, and publishes. Nothing drafted is live until published.

Saved drafts (data/drafts/*.json, hand-written) cover the brief's two example sentences so the demo works
without an API key. Same API call shape as notes.py (client.beta.messages.parse with output_format).
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .clean import Request
from .facts import Facts
from .rules import (FIELDS, LANES, OPS, POLICY_TEXTS, Action, AnyOf, Condition, Rule, RuleSet, evaluate,
                    rule_from_dict, validate)

DEFAULT_MODEL = "claude-opus-5-5"
NO_KEY_ERROR = ("Automatic rule drafting is unavailable (no API key configured). "
                "You can still change the values on the left.")
INVALID_PREFIX = "Couldn't turn that into a valid rule:"


class DraftCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal[*FIELDS]  # type: ignore[valid-type]
    op: Literal[*OPS]  # type: ignore[valid-type]
    value: str | float | bool | list[str] | None = None


class DraftAnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any_of: list[DraftCondition]


class DraftRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    when: list[DraftCondition | DraftAnyOf]
    kind: Literal["approve", "route", "route_with_draft_rejection"]
    lane: Literal[*LANES, "by_reason"] | None = None  # type: ignore[valid-type]
    policy: Literal[*POLICY_TEXTS] | None = None  # type: ignore[valid-type]


class DraftChoiceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    rule: DraftRule


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str | None
    choices: list[DraftChoiceOut]


@dataclass
class DraftChoice:
    label: str
    rule: Rule


@dataclass
class Draft:
    text: str
    question: str | None
    choices: list[DraftChoice]
    error: str | None
    source: Literal["saved", "live", "none"]


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s$]", " ", text.lower()).split())


def next_manager_id(rs: RuleSet) -> str:
    nums = [int(r.id[1:]) for r in rs.rules if re.fullmatch(r"M\d+", r.id)]
    return f"M{max(nums, default=0) + 1}"


def _from_draft(d: DraftRule, rule_id: str) -> Rule:
    def cond(c):
        if isinstance(c, DraftAnyOf):
            return AnyOf([cond(x) for x in c.any_of])
        return Condition(c.field, c.op, c.value)

    return Rule(rule_id, d.title, [cond(c) for c in d.when], Action(d.kind, d.lane, d.policy))


def suggest_position(rs: RuleSet, rule: Rule) -> int:
    """Approve rules go right after the safety rules (so they can override routing rules); rules that send
    cases to a person go right before the first approve rule (so they win over approvals)."""
    last_locked = max((i for i, r in enumerate(rs.rules) if r.locked), default=-1)
    if rule.then.kind == "approve":
        return last_locked + 1
    first_approve = next((i for i, r in enumerate(rs.rules) if r.then.kind == "approve"), len(rs.rules))
    return max(first_approve, last_locked + 1)


def insert_rule(rs: RuleSet, rule: Rule, position: int) -> RuleSet:
    new = copy.deepcopy(rs)
    new.rules.insert(position, copy.deepcopy(rule))
    new.version = rs.version + 1
    new.reason, new.published_at = "", ""
    return new


def overridden_rules(live: RuleSet, candidate: RuleSet, rows: list[tuple[Request, Facts | None]]) -> list[str]:
    ids = set()
    for r, facts in rows:
        before, after = evaluate(live, r, facts), evaluate(candidate, r, facts)
        if (before.outcome, before.lane, before.draft_rejection) != (after.outcome, after.lane, after.draft_rejection):
            ids.add(before.rule_id)
    return sorted(ids)


def _checked(text: str, question: str | None, rules: list[tuple[str, Rule]], live: RuleSet,
             source: Literal["saved", "live"]) -> Draft:
    choices = []
    for label, rule in rules:
        errors = validate(insert_rule(live, rule, suggest_position(live, rule)), base=live)
        if errors:
            return Draft(text, None, [], f"{INVALID_PREFIX} {'; '.join(errors)}", source)
        choices.append(DraftChoice(label, rule))
    if not choices:
        return Draft(text, None, [], f"{INVALID_PREFIX} no rule came back", source)
    return Draft(text, question, choices, None, source)


def _saved(text: str, saved_dir: Path) -> dict | None:
    want = _normalise(text)
    for path in sorted(saved_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if _normalise(data["text"]) == want:
            return data
    return None


SYSTEM_PROMPT = """You turn a refund-operations manager's sentence into candidate rules for a refund system.

Rules are checked in order; the first rule whose conditions all match decides. A rule's action is one of:
approve (refund in full automatically), route (send to a person, in a lane), route_with_draft_rejection
(send to a person with a pre-filled rejection citing a policy). Lanes: {lanes}, or "by_reason" to let the
system pick the lane. Rejection policies: {policies}.

Conditions are field / operator / value. Fields: {fields}.
Fields starting with "facts." are facts an AI read from the notes; their values are "yes", "no",
"not_mentioned" (photo_or_video_evidence uses "present"/"absent"/"not_mentioned"; location_tag is a
lowercase place slug such as "northfield"). stated_reason is one of wrong_size, changed_mind,
arrived_damaged, wrong_item, never_arrived, late_delivery. fraud_flag is yes / no / unknown.
Operators: {ops}. Use "in"/"not_in" with a list.

Current rules, in order:
{current}

Draft the rule the manager asked for. If the sentence has more than one reasonable reading that would
change which requests match, set "question" to one short plain-English question and give one choice per
reading (2-3 choices), each a complete rule. Otherwise set question to null and give one choice.
Titles are short plain English ending in "→ <action>". Never change or restate the existing rules."""


def _system(live: RuleSet) -> str:
    current = "\n".join(f"- {r.id}: {r.title}" for r in live.rules)
    return SYSTEM_PROMPT.format(lanes=", ".join(LANES), policies=", ".join(POLICY_TEXTS),
                                fields=", ".join(FIELDS), ops=", ".join(OPS), current=current)


def draft_rule(text: str, live: RuleSet, client=None, saved_dir: str | Path = Path("data/drafts"),
               model: str = DEFAULT_MODEL) -> Draft:
    rid = next_manager_id(live)
    saved = _saved(text, Path(saved_dir))
    if saved is not None:
        rules = []
        for c in saved["choices"]:
            rule = rule_from_dict(c["rule"])
            rule.id = rid
            rules.append((c["label"], rule))
        return _checked(text, saved.get("question"), rules, live, "saved")
    if client is None:
        return Draft(text, None, [], NO_KEY_ERROR, "none")
    try:
        resp = client.beta.messages.parse(
            model=model, max_tokens=4096, system=_system(live),
            messages=[{"role": "user", "content": text}], output_format=DraftOutput,
            output_config={"effort": "medium"},
        )
    except Exception as e:
        return Draft(text, None, [], f"Automatic rule drafting failed ({type(e).__name__}). "
                                     "You can still change the values on the left.", "live")
    out = resp.parsed_output
    if resp.stop_reason == "refusal" or out is None:
        return Draft(text, None, [], f"{INVALID_PREFIX} no usable rule was returned", "live")
    rules = [(c.label, _from_draft(c.rule, rid)) for c in out.choices]
    return _checked(text, out.question, rules, live, "live")
