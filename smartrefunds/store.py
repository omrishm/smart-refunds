"""Demo state in SQLite under var/ (git-ignored): decisions, audit log, mock payments, case status, and the
manager's published rule versions. The committed rules/v1.yaml is the seed; a fresh checkout starts there.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .rules import RuleSet, dump_rules, load_rules, validate

FINAL_ACTIONS = {"approve_full", "approve_partial", "reject"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT, request_id TEXT, row_index INTEGER, role TEXT, action TEXT, amount REAL,
  reason_code TEXT, note TEXT, rule_version INTEGER, suggestion TEXT, flag_feedback TEXT);
CREATE TABLE IF NOT EXISTS payments (
  request_id TEXT PRIMARY KEY, amount REAL, method TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS case_status (
  request_id TEXT, row_index INTEGER, status TEXT, waiting_for TEXT, lane TEXT,
  PRIMARY KEY (request_id, row_index));
"""


@dataclass
class PaymentResult:
    ok: bool
    message: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, var_dir: str | Path = Path("var"), seed_rules: str | Path = Path("rules/v1.yaml")):
        self.var_dir = Path(var_dir)
        self.seed_rules = Path(seed_rules)
        self.rules_dir = self.var_dir / "rules"
        self.db_path = self.var_dir / "smartrefunds.db"
        self._init()

    def _init(self) -> None:
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.rules_dir.mkdir(exist_ok=True)
        with closing(self._conn()) as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- decisions and audit ----------

    def record_decision(self, request_id: str, row_index: int, role: str, action: str, amount: float | None,
                        reason_code: str | None, note: str, rule_version: int, suggestion: str,
                        flag_feedback: dict[str, bool]) -> int:
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO audit (at, request_id, row_index, role, action, amount, reason_code, note, rule_version,"
                " suggestion, flag_feedback) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), request_id, row_index, role, action, amount, reason_code, note, rule_version, suggestion,
                 json.dumps(flag_feedback)),
            )
            if action in FINAL_ACTIONS:
                self._set_status(c, request_id, row_index, "decided", None)
            return cur.lastrowid

    def audit(self, request_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM audit", ()
        if request_id is not None:
            sql, args = sql + " WHERE request_id = ?", (request_id,)
        with closing(self._conn()) as c:
            rows = [dict(r) for r in c.execute(sql + " ORDER BY id", args)]
        for r in rows:
            r["flag_feedback"] = json.loads(r["flag_feedback"] or "{}")
        return rows

    # ---------- mock payments: at most one per request_id ----------

    def pay(self, request_id: str, amount: float, method: str) -> PaymentResult:
        with closing(self._conn()) as c, c:
            existing = c.execute("SELECT amount FROM payments WHERE request_id = ?", (request_id,)).fetchone()
            if existing:
                return PaymentResult(False, f"{request_id} was already refunded (${existing['amount']:g}); "
                                            "not paying again")
            c.execute("INSERT INTO payments VALUES (?,?,?,?)", (request_id, amount, method, _now()))
        return PaymentResult(True, f"Simulated: would refund ${amount:,.2f} to {method} for {request_id}")

    def payments(self) -> list[dict]:
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM payments ORDER BY at")]

    # ---------- case status ----------

    @staticmethod
    def _set_status(c: sqlite3.Connection, request_id: str, row_index: int, status: str, what: str | None) -> None:
        c.execute("INSERT INTO case_status (request_id, row_index, status, waiting_for) VALUES (?,?,?,?) "
                  "ON CONFLICT(request_id, row_index) DO UPDATE SET "
                  "status = excluded.status, waiting_for = excluded.waiting_for",
                  (request_id, row_index, status, what))

    def status(self, request_id: str, row_index: int) -> Literal["open", "waiting", "decided"]:
        with closing(self._conn()) as c:
            row = c.execute("SELECT status FROM case_status WHERE request_id = ? AND row_index = ?",
                            (request_id, row_index)).fetchone()
        return row["status"] if row else "open"

    def waiting_for(self, request_id: str, row_index: int) -> str | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT waiting_for FROM case_status WHERE request_id = ? AND row_index = ?",
                            (request_id, row_index)).fetchone()
        return row["waiting_for"] if row else None

    def set_waiting(self, request_id: str, row_index: int, what: str) -> None:
        with closing(self._conn()) as c, c:
            self._set_status(c, request_id, row_index, "waiting", what)

    def resume(self, request_id: str, row_index: int) -> None:
        with closing(self._conn()) as c, c:
            self._set_status(c, request_id, row_index, "open", None)

    def set_lane(self, request_id: str, row_index: int, lane: str) -> None:
        with closing(self._conn()) as c, c:
            c.execute("INSERT INTO case_status (request_id, row_index, status, lane) VALUES (?,?, 'open', ?) "
                      "ON CONFLICT(request_id, row_index) DO UPDATE SET lane = excluded.lane",
                      (request_id, row_index, lane))

    def lane_override(self, request_id: str, row_index: int) -> str | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT lane FROM case_status WHERE request_id = ? AND row_index = ?",
                            (request_id, row_index)).fetchone()
        return row["lane"] if row else None

    # ---------- rule versions ----------

    def _published(self) -> list[tuple[int, Path]]:
        files = [(int(p.stem[1:]), p) for p in self.rules_dir.glob("v*.yaml") if p.stem[1:].isdigit()]
        return sorted(files)

    def live_rules(self) -> RuleSet:
        published = self._published()
        return load_rules(published[-1][1] if published else self.seed_rules)

    def versions(self) -> list[tuple[int, str, str]]:
        seed = load_rules(self.seed_rules)
        out = [(seed.version, seed.published_at, seed.reason)]
        for _, path in self._published():
            rs = load_rules(path)
            out.append((rs.version, rs.published_at, rs.reason))
        return out

    def publish(self, rs: RuleSet, reason: str, author: str = "Refund Ops Manager") -> int:
        if not reason.strip():
            raise ValueError("a short reason is required to publish")
        live = self.live_rules()
        errors = validate(rs, base=live)
        if errors:
            raise ValueError("; ".join(errors))
        new = copy.deepcopy(rs)
        new.version = live.version + 1
        new.reason = reason.strip()
        new.published_at = f"{_now()} by {author}"
        dump_rules(new, self.rules_dir / f"v{new.version}.yaml")
        return new.version

    def rollback(self, to_version: int, reason: str) -> int:
        if to_version == load_rules(self.seed_rules).version:
            target = load_rules(self.seed_rules)
        else:
            target = load_rules(self.rules_dir / f"v{to_version}.yaml")
        return self.publish(target, reason=f"Roll back to v{to_version}: {reason}")

    def reset(self) -> None:
        shutil.rmtree(self.var_dir, ignore_errors=True)
        self._init()
