"""Load refund CSVs and normalise them.

Nothing here guesses: a value that can't be parsed becomes None and the row is marked invalid with a
reason, so later rules send it to a person instead of treating it as safe.
"""
from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal

REASONS = ("wrong_size", "changed_mind", "arrived_damaged", "wrong_item", "never_arrived", "late_delivery")
FraudFlag = Literal["yes", "no", "unknown"]
REQUIRED_COLUMNS: tuple[str, ...] = (
    "request_id", "requested_at", "customer_id", "tenure_months", "prior_refunds_12m", "amount_usd",
    "category", "payment_method", "stated_reason", "customer_note", "support_note", "days_since_delivery",
    "fraud_flag", "decision", "decided_by", "outcome_note", "decided_at", "review_minutes",
)

_REASON_ALIASES = {
    "changed mind": "changed_mind", "damaged": "arrived_damaged", "arrived damaged": "arrived_damaged",
    "late": "late_delivery", "wrong item": "wrong_item", "never arrived": "never_arrived",
    "wrong size": "wrong_size", "late delivery": "late_delivery",
}
_FLAG_YES = {"yes", "y", "true"}
_FLAG_NO = {"no", "n", "false"}
_AMOUNT_RE = re.compile(r"\s*(?:USD|\$)?\s*(\d+(?:\.\d+)?)\s*(?:USD)?\s*", re.IGNORECASE)


class InputFileError(Exception):
    """The file can't be processed at all (missing, unreadable, wrong columns)."""


@dataclass(frozen=True)
class Request:
    row_index: int
    request_id: str
    requested_at: date | None
    customer_id: str | None
    tenure_months: float | None
    prior_refunds_12m: int | None
    amount_usd: float | None
    category: str | None
    payment_method: str | None
    stated_reason: str | None
    customer_note: str
    support_note: str
    days_since_delivery: float | None
    fraud_flag: FraudFlag
    decision: str | None = None
    decided_by: str | None = None
    outcome_note: str = ""
    decided_at: datetime | None = None
    review_minutes: float | None = None
    invalid_reasons: tuple[str, ...] = ()
    duplicate_id: bool = False
    cleaning_changes: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.invalid_reasons

    @property
    def notes_present(self) -> bool:
        return bool(self.customer_note.strip() or self.support_note.strip())


@dataclass
class LoadResult:
    requests: list[Request] = field(default_factory=list)
    dropped_exact_duplicates: list[str] = field(default_factory=list)


def parse_amount(raw: str) -> float | None:
    m = _AMOUNT_RE.fullmatch((raw or "").replace(",", ""))
    return float(m.group(1)) if m else None


def parse_date(raw: str) -> date | None:
    s = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def normalise_reason(raw: str) -> str | None:
    s = (raw or "").strip().lower()
    s = _REASON_ALIASES.get(s, s)
    return s if s in REASONS else None


def normalise_flag(raw: str) -> FraudFlag:
    s = (raw or "").strip().lower()
    if s in _FLAG_YES:
        return "yes"
    if s in _FLAG_NO:
        return "no"
    return "unknown"


def _number(raw: str) -> float | None:
    """A finite number, or None. nan/inf are rejected: every comparison with nan is False, so it would slip past
    threshold rules."""
    try:
        v = float((raw or "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _decided_at(raw: str) -> datetime | None:
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise InputFileError(f"file not found: {path}") from None
    except OSError as e:
        raise InputFileError(f"can't read {path}: {e}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _fmt(v: float) -> str:
    return f"{v:g}"


def _clean_row(i: int, raw: dict[str, str]) -> Request:
    invalid: list[str] = []
    changes: list[str] = []

    def change(col: str, clean: str) -> None:
        if (raw[col] or "").strip() != clean or raw[col] != (raw[col] or "").strip():
            changes.append(f"{col}: '{raw[col]}' → '{clean}'")

    amount = parse_amount(raw["amount_usd"])
    if amount is None:
        invalid.append(f"amount_usd unreadable: '{raw['amount_usd']}'")
    else:
        change("amount_usd", _fmt(amount))

    requested = parse_date(raw["requested_at"])
    if requested is not None:
        change("requested_at", requested.isoformat())

    reason = normalise_reason(raw["stated_reason"])
    if reason is None:
        invalid.append(f"unknown stated_reason '{(raw['stated_reason'] or '').strip()}'")
    else:
        change("stated_reason", reason)

    flag = normalise_flag(raw["fraud_flag"])
    change("fraud_flag", flag)

    category = (raw["category"] or "").strip().lower() or None
    if category is not None:
        change("category", category)

    tenure = _number(raw["tenure_months"])
    if tenure is None:
        invalid.append("tenure_months missing or unreadable")
    elif tenure < 0:
        invalid.append(f"tenure_months negative ({_fmt(tenure)})")
    days = _number(raw["days_since_delivery"])
    if days is None:
        invalid.append("days_since_delivery missing or unreadable")
    elif days < 0:
        invalid.append(f"days_since_delivery negative ({_fmt(days)})")
    prior = _number(raw["prior_refunds_12m"])
    if prior is None:
        invalid.append("prior_refunds_12m missing or unreadable")
    elif prior < 0 or not prior.is_integer():
        invalid.append(f"prior_refunds_12m must be a whole number of 0 or more ('{raw['prior_refunds_12m']}')")
        prior = None
    customer = (raw["customer_id"] or "").strip() or None
    if customer is None:
        invalid.append("customer_id missing")
    if not (raw["request_id"] or "").strip():
        invalid.append("request_id missing")

    return Request(
        row_index=i,
        request_id=(raw["request_id"] or "").strip(),
        requested_at=requested,
        customer_id=customer,
        tenure_months=tenure,
        prior_refunds_12m=int(prior) if prior is not None else None,
        amount_usd=amount,
        category=category,
        payment_method=(raw["payment_method"] or "").strip().lower() or None,
        stated_reason=reason,
        customer_note=raw["customer_note"] or "",
        support_note=raw["support_note"] or "",
        days_since_delivery=days,
        fraud_flag=flag,
        decision=(raw["decision"] or "").strip() or None,
        decided_by=(raw["decided_by"] or "").strip() or None,
        outcome_note=(raw["outcome_note"] or "").strip(),
        decided_at=_decided_at(raw["decided_at"]),
        review_minutes=_number(raw["review_minutes"]),
        invalid_reasons=tuple(invalid),
        cleaning_changes=tuple(changes),
    )


def load_requests(path: str | Path) -> LoadResult:
    path = Path(path)
    reader = csv.DictReader(io.StringIO(_read_text(path)))
    if reader.fieldnames is None:
        raise InputFileError(f"{path} is empty (no header row)")
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise InputFileError(f"{path} is missing column(s): {', '.join(missing)}")

    seen: set[tuple[str, ...]] = set()
    dropped: list[str] = []
    rows: list[Request] = []
    for raw in reader:
        key = tuple(raw.get(c) or "" for c in REQUIRED_COLUMNS)
        if key in seen:
            dropped.append((raw["request_id"] or "").strip())
            continue
        seen.add(key)
        rows.append(_clean_row(len(rows), raw))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.request_id] = counts.get(r.request_id, 0) + 1
    rows = [replace(r, duplicate_id=True) if counts[r.request_id] > 1 else r for r in rows]
    return LoadResult(rows, dropped)

