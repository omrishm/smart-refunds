from datetime import date

import pytest

from smartrefunds.clean import (
    REQUIRED_COLUMNS,
    InputFileError,
    LoadResult,
    load_requests,
    normalise_flag,
    normalise_reason,
    parse_amount,
    parse_date,
)

HISTORY = "data/history.csv"
PENDING = "data/pending.csv"


def write_csv(path, rows, columns=REQUIRED_COLUMNS, encoding="utf-8"):
    import csv

    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.DictWriter(f, fieldnames=list(columns))
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in columns})
    return path


GOOD_ROW = {
    "request_id": "RF-9001", "requested_at": "2026-09-10", "customer_id": "CU-9", "tenure_months": "20",
    "prior_refunds_12m": "0", "amount_usd": "30", "category": "apparel", "payment_method": "card",
    "stated_reason": "wrong_size", "days_since_delivery": "5", "fraud_flag": "no", "decision": "pending",
}


def by_id(requests, request_id):
    return [r for r in requests if r.request_id == request_id]


def test_parse_amount():
    assert parse_amount("$1,150.00") == 1150.0
    assert parse_amount("USD 120.00") == 120.0
    assert parse_amount("55.00 USD") == 55.0
    assert parse_amount(" 42 ") == 42.0
    assert parse_amount("$43") == 43.0
    assert parse_amount("abc") is None
    assert parse_amount("") is None


def test_parse_date():
    assert parse_date("03/09/2026") == date(2026, 9, 3)
    assert parse_date("12/06/2026") == date(2026, 6, 12)
    assert parse_date("2026-06-15T07:11:00Z") == date(2026, 6, 15)
    assert parse_date("2026-08-19") == date(2026, 8, 19)
    assert parse_date("soon") is None


def test_normalise_reason():
    assert normalise_reason("Never Arrived") == "never_arrived"
    assert normalise_reason("changed mind") == "changed_mind"
    assert normalise_reason("damaged") == "arrived_damaged"
    assert normalise_reason("arrived damaged") == "arrived_damaged"
    assert normalise_reason("late") == "late_delivery"
    assert normalise_reason("wrong item") == "wrong_item"
    assert normalise_reason("defective") is None


def test_normalise_flag():
    for raw in ["yes", "Y", "TRUE", "true"]:
        assert normalise_flag(raw) == "yes"
    for raw in ["no", "N", "false"]:
        assert normalise_flag(raw) == "no"
    for raw in ["", "maybe"]:
        assert normalise_flag(raw) == "unknown"


def test_history_load():
    result = load_requests(HISTORY)
    assert len(result.requests) == 248
    assert result.dropped_exact_duplicates == ["RF-2040", "RF-1019", "RF-2111"]


def test_history_invalid_rows():
    reqs = load_requests(HISTORY).requests
    for rid in ["RF-2139", "RF-2048", "RF-2167", "RF-2065", "RF-2050"]:
        (r,) = by_id(reqs, rid)
        assert not r.is_valid, rid
    (ok,) = by_id(reqs, "RF-2003")
    assert ok.is_valid


def test_pending_load():
    reqs = load_requests(PENDING).requests
    assert len(reqs) == 21
    copies = by_id(reqs, "RF-2183")
    assert len(copies) == 2 and all(r.duplicate_id for r in copies)
    (r,) = by_id(reqs, "RF-2181")
    assert r.amount_usd == 1150.0
    assert r.stated_reason == "never_arrived"
    assert r.requested_at == date(2026, 9, 3)
    changes = " ".join(r.cleaning_changes)
    assert "amount_usd" in changes and "requested_at" in changes and "stated_reason" in changes
    assert by_id(reqs, "RF-2182")[0].fraud_flag == "unknown"
    assert by_id(reqs, "RF-1077")[0].fraud_flag == "yes"


def test_unseen_values(tmp_path):
    row = dict(GOOD_ROW, stated_reason="defective", category="Toys", fraud_flag="maybe")
    (r,) = load_requests(write_csv(tmp_path / "x.csv", [row])).requests
    assert r.stated_reason is None
    assert "unknown stated_reason 'defective'" in r.invalid_reasons
    assert r.category == "toys"
    assert r.fraud_flag == "unknown"


def test_missing_column_raises(tmp_path):
    cols = [c for c in REQUIRED_COLUMNS if c != "amount_usd"]
    path = write_csv(tmp_path / "x.csv", [GOOD_ROW], columns=cols)
    with pytest.raises(InputFileError, match="amount_usd"):
        load_requests(path)


def test_header_only(tmp_path):
    assert load_requests(write_csv(tmp_path / "x.csv", [])) == LoadResult([], [])


@pytest.mark.parametrize("encoding", ["utf-8-sig", "cp1252"])
def test_encodings(tmp_path, encoding):
    row = dict(GOOD_ROW, customer_note="café was cold")
    (r,) = load_requests(write_csv(tmp_path / "x.csv", [row], encoding=encoding)).requests
    assert r.request_id == "RF-9001"
    assert r.customer_note == "café was cold"
