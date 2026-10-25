"""Ordered, deterministic refund rules. The first matching rule decides; nothing matching means a person.

Rules are plain data (YAML) so the Refund Ops Manager can change them without an engineer.
Locked safety rules always sit at the top and can't be edited from the manager screen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .clean import Request
from .facts import Facts

LANES = ("Fraud team", "Delivery disputes", "Damage & evidence", "Policy rejections", "Other")
OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not_in", "is_set", "is_unset")
ACTIONS = ("approve", "route", "route_with_draft_rejection")
_FACT_FIELDS = tuple(f"facts.{n}" for n in Facts.model_fields if n not in ("summary", "language"))
FIELDS: tuple[str, ...] = (
    "request_id", "amount_usd", "tenure_months", "prior_refunds_12m", "days_since_delivery", "stated_reason",
    "category", "payment_method", "fraud_flag", "is_valid", "duplicate_id", "notes_present", "facts_available",
) + _FACT_FIELDS
_NON_NEGATIVE = ("amount_usd", "tenure_months", "prior_refunds_12m", "days_since_delivery")

POLICY_TEXTS: dict[str, str] = {
    "policy_2": (
        "Thanks for getting in touch. We accept change-of-mind returns within 14 days of delivery, and this "
        "request came {days} days after delivery, so we're unable to refund it."
    ),
    "policy_4": (
        "Our carrier confirms delivery with a signature or delivery photo at your address. We're unable to "
        "refund a confirmed delivery unless the carrier opens an investigation. If you believe the delivery "
        "record isn't genuine, reply and we'll ask the carrier to investigate."
    ),
    "double_refund": (
        "Our records show this purchase has already been refunded, so we're unable to issue a second refund. "
        "If that's not right, reply with the details and we'll take another look."
    ),
    "order_matches": (
        "We checked your order and the item we sent matches what was ordered. If something is still wrong "
        "with it, reply with a photo and we'll take another look."
    ),
}
INJECTION_WARNING = (
    "Possible manipulation: the customer note contains instructions addressed to the refund system "
    "(for example, a claim that the refund was pre-approved). They were not acted on. Verify this request "
    "independently."
)


@dataclass
class Condition:
    field: str
    op: str
    value: Any = None


@dataclass
class AnyOf:
    conditions: list[Condition]


@dataclass
class Action:
    kind: Literal["approve", "route", "route_with_draft_rejection"]
    lane: str | None = None  # a LANES value or "by_reason"
    policy: str | None = None


@dataclass
class Rule:
    id: str
    title: str
    when: list[Condition | AnyOf]
    then: Action
    locked: bool = False
    note: str = ""


@dataclass
class RuleSet:
    version: int
    rules: list[Rule]
    reason: str = ""
    published_at: str = ""


@dataclass
class Outcome:
    outcome: Literal["auto_approved", "needs_review"]
    lane: str | None
    rule_id: str
    matched_rule_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    draft_rejection: str | None = None
    warning: str | None = None


# ---------- YAML I/O ----------

def _cond_from(d: dict) -> Condition | AnyOf:
    if "any_of" in d:
        return AnyOf([Condition(**c) for c in d["any_of"]])
    return Condition(d["field"], d["op"], d.get("value"))


def _cond_to(c: Condition | AnyOf) -> dict:
    if isinstance(c, AnyOf):
        return {"any_of": [_cond_to(x) for x in c.conditions]}
    d = {"field": c.field, "op": c.op}
    if c.op not in ("is_set", "is_unset"):
        d["value"] = c.value
    return d


def rule_from_dict(d: dict) -> Rule:
    then = d["then"]
    return Rule(
        id=d["id"], title=d["title"], when=[_cond_from(c) for c in d.get("when", [])],
        then=Action(then["kind"], then.get("lane"), then.get("policy")),
        locked=bool(d.get("locked", False)), note=d.get("note", ""),
    )


def rule_to_dict(r: Rule) -> dict:
    then = {"kind": r.then.kind}
    if r.then.lane is not None:
        then["lane"] = r.then.lane
    if r.then.policy is not None:
        then["policy"] = r.then.policy
    d: dict[str, Any] = {"id": r.id, "title": r.title}
    if r.locked:
        d["locked"] = True
    d["when"] = [_cond_to(c) for c in r.when]
    d["then"] = then
    if r.note:
        d["note"] = r.note
    return d


def load_rules(path: str | Path) -> RuleSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RuleSet(
        version=int(data["version"]), rules=[rule_from_dict(r) for r in data["rules"]],
        reason=data.get("reason", ""), published_at=str(data.get("published_at", "")),
    )


def dump_rules(rs: RuleSet, path: str | Path) -> None:
    data = {"version": rs.version, "reason": rs.reason, "published_at": rs.published_at,
            "rules": [rule_to_dict(r) for r in rs.rules]}
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---------- evaluation ----------

def case_fields(r: Request, facts: Facts | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "request_id": r.request_id, "amount_usd": r.amount_usd, "tenure_months": r.tenure_months,
        "prior_refunds_12m": r.prior_refunds_12m, "days_since_delivery": r.days_since_delivery,
        "stated_reason": r.stated_reason, "category": r.category, "payment_method": r.payment_method,
        "fraud_flag": r.fraud_flag, "is_valid": r.is_valid, "duplicate_id": r.duplicate_id,
        "notes_present": r.notes_present, "facts_available": facts is not None,
    }
    for f in _FACT_FIELDS:
        name = f.split(".", 1)[1]
        fields[f] = getattr(facts, name).value if facts is not None else None
    return fields


def matches(c: Condition | AnyOf, fields: dict[str, Any]) -> bool:
    """None semantics: a missing value never satisfies a positive test (=, in, <, >...) and always
    satisfies a negative one (!=, not_in, is_unset), so missing evidence routes to a person."""
    if isinstance(c, AnyOf):
        return any(matches(x, fields) for x in c.conditions)
    v = fields.get(c.field)
    op = c.op
    if op == "is_set":
        return v is not None
    if op == "is_unset":
        return v is None
    if v is None:
        return op in ("!=", "not_in")
    try:
        if op == "=":
            return v == c.value
        if op == "!=":
            return v != c.value
        if op == "in":
            return v in c.value
        if op == "not_in":
            return v not in c.value
        if op == "<":
            return v < c.value
        if op == "<=":
            return v <= c.value
        if op == ">":
            return v > c.value
        if op == ">=":
            return v >= c.value
    except TypeError:
        return False
    raise ValueError(f"unknown operator {op!r}")


def lane_by_reason(fields: dict[str, Any]) -> str:
    if (fields.get("fraud_flag") != "no" or fields.get("facts.fraud_reported") == "yes"
            or fields.get("facts.fraud_confirmed") == "yes"):
        return "Fraud team"
    reason = fields.get("stated_reason")
    if reason in ("never_arrived", "late_delivery"):
        return "Delivery disputes"
    if reason == "arrived_damaged":
        return "Damage & evidence"
    return "Other"


def _draft(policy: str | None, r: Request) -> str | None:
    if policy is None:
        return None
    days = f"{r.days_since_delivery:g}" if r.days_since_delivery is not None else "more than 14"
    return POLICY_TEXTS[policy].format(days=days)


def evaluate(rs: RuleSet, r: Request, facts: Facts | None) -> Outcome:
    fields = case_fields(r, facts)
    matched = [rule for rule in rs.rules if all(matches(c, fields) for c in rule.when)]
    warning = INJECTION_WARNING if any(m.id == "S3" for m in matched) else None
    reasons = [m.title for m in matched if m.then.kind != "approve"]
    ids = [m.id for m in matched]
    if not matched:
        return Outcome("needs_review", "Other", "(none)", ids, ["No rule matched, so a person decides"], None, warning)
    first = matched[0]
    if first.then.kind == "approve":
        return Outcome("auto_approved", None, first.id, ids, [first.title], None, warning)
    lane = lane_by_reason(fields) if first.then.lane == "by_reason" else first.then.lane
    draft = _draft(first.then.policy, r) if first.then.kind == "route_with_draft_rejection" else None
    return Outcome("needs_review", lane, first.id, ids, reasons, draft, warning)


# ---------- validation ----------

def _check_condition(c: Condition | AnyOf, rid: str, errors: list[str]) -> None:
    if isinstance(c, AnyOf):
        if not c.conditions:
            errors.append(f"{rid}: 'any of' needs at least one condition")
        for x in c.conditions:
            _check_condition(x, rid, errors)
        return
    if c.field not in FIELDS:
        errors.append(f"{rid}: unknown field '{c.field}'")
    if c.op not in OPS:
        errors.append(f"{rid}: unknown operator '{c.op}'")
        return
    if c.op in ("<", "<=", ">", ">=") and not isinstance(c.value, (int, float)):
        errors.append(f"{rid}: '{c.field} {c.op}' needs a number")
    if c.op in ("in", "not_in") and not isinstance(c.value, list):
        errors.append(f"{rid}: '{c.field} {c.op}' needs a list")
    if c.field in _NON_NEGATIVE and isinstance(c.value, (int, float)) and c.value < 0:
        errors.append(f"{rid}: '{c.field}' can't be compared with a negative number")


def validate(rs: RuleSet, base: RuleSet | None = None) -> list[str]:
    errors: list[str] = []
    ids = [r.id for r in rs.rules]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"rule id '{dup}' is used more than once")
    seen_unlocked = False
    for r in rs.rules:
        if r.locked and seen_unlocked:
            errors.append(f"{r.id}: safety rules must stay at the top of the list")
        if not r.locked:
            seen_unlocked = True
        if r.then.kind not in ACTIONS:
            errors.append(f"{r.id}: unknown action '{r.then.kind}'")
        if r.then.kind == "approve" and not r.when:
            errors.append(f"{r.id}: an approve rule needs at least one condition")
        if r.then.kind != "approve" and r.then.lane not in LANES + ("by_reason",):
            errors.append(f"{r.id}: unknown lane '{r.then.lane}'")
        if r.then.kind == "route_with_draft_rejection" and r.then.policy not in POLICY_TEXTS:
            errors.append(f"{r.id}: unknown rejection policy '{r.then.policy}'")
        for c in r.when:
            _check_condition(c, r.id, errors)
    if base is not None:
        locked_base = [rule_to_dict(r) for r in base.rules if r.locked]
        top = [rule_to_dict(r) for r in rs.rules[: len(locked_base)]]
        if top != locked_base:
            errors.append("locked safety rules can't be edited, removed or moved")
    return errors
