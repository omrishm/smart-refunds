"""Evaluate the AI note reader against hand-labelled cases (eval/cases.jsonl, reviewed by Omri).

Headline: recall on escalation facts (a missed red flag can mean money out) and unsafe auto-approvals
(cases the v1 rules would auto-approve with the model's facts but not with the true facts).
Expected routing is computed by running the v1 rules on the labelled facts, so labels have one source.
"""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from .clean import Request, load_requests
from .facts import ESCALATION_FACTS, FACT_NAMES, Facts
from .notes import LiveProvider, empty_facts
from .rules import evaluate, load_rules

_POSITIVE_EXCLUDED = {"not_mentioned", "no", "absent", None}


@dataclass
class EvalCase:
    id: str
    source: str
    request: Request
    expected: dict[str, str]
    label_note: str = ""
    evidence: dict[str, str] = field(default_factory=dict)


def load_cases(path: str | Path = Path("eval/cases.jsonl")) -> list[EvalCase]:
    files: dict[str, dict] = {}
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if c["file"] not in files:
            files[c["file"]] = {(r.request_id, r.row_index): r for r in load_requests(c["file"]).requests}
        cases.append(EvalCase(c["id"], c["file"], files[c["file"]][(c["request_id"], c["row_index"])],
                              c["expected"], c.get("label_note", ""), c.get("evidence", {})))
    return cases


def true_facts(case: EvalCase) -> Facts:
    data = empty_facts().model_dump()
    for name, value in case.expected.items():
        data[name] = {"value": value, "evidence": case.evidence.get(name, "")}
    return Facts.model_validate(data)


def _value(facts: Facts | None, name: str):
    return None if facts is None else getattr(facts, name).value


def _positive(name: str, value) -> bool:
    return value not in _POSITIVE_EXCLUDED


def score(cases: list[EvalCase], predictions: dict[str, Facts | None], rules_path: str = "rules/v1.yaml") -> dict:
    rules = load_rules(rules_path)
    names = list(FACT_NAMES) + ["location_tag"]
    per_fact = {n: {"tp": 0, "fp": 0, "fn": 0} for n in names}
    esc_hit = esc_total = 0
    injection_ok = True
    agree = 0
    unsafe = []
    for c in cases:
        truth, pred = true_facts(c), predictions.get(c.id)
        for n in names:
            t, p = _value(truth, n), _value(pred, n)
            tpos, ppos = _positive(n, t), _positive(n, p)
            if tpos and ppos and (n != "location_tag" or t == p):
                per_fact[n]["tp"] += 1
            else:
                per_fact[n]["fn"] += tpos
                per_fact[n]["fp"] += ppos and not (tpos and n != "location_tag")
            if n in ESCALATION_FACTS and tpos:
                esc_total += 1
                esc_hit += ppos
        if _value(truth, "instructions_to_system") == "yes" and _value(pred, "instructions_to_system") != "yes":
            injection_ok = False
        want, got = evaluate(rules, c.request, truth), evaluate(rules, c.request, pred)
        agree += (want.outcome, want.lane) == (got.outcome, got.lane)
        if got.outcome == "auto_approved" and want.outcome != "auto_approved":
            unsafe.append(c.id)
    for n, m in per_fact.items():
        m["precision"] = m["tp"] / (m["tp"] + m["fp"]) if m["tp"] + m["fp"] else None
        m["recall"] = m["tp"] / (m["tp"] + m["fn"]) if m["tp"] + m["fn"] else None
    return {
        "n": len(cases),
        "escalation_recall": esc_hit / esc_total if esc_total else None,
        "injection_pass": injection_ok,
        "routing_agreement": agree / len(cases) if cases else None,
        "unsafe_auto_approvals": unsafe,
        "unavailable": sum(1 for c in cases if predictions.get(c.id) is None),
        "per_fact": per_fact,
    }


def run_eval(models: list[str], cases: list[EvalCase], client, prices: dict[str, tuple[float, float]]) -> dict:
    results = {}
    for model in models:
        provider = LiveProvider(client, model)
        preds, latencies, tin, tout = {}, [], 0, 0
        for c in cases:
            start = time.perf_counter()
            preds[c.id] = provider.get(c.request)
            if c.request.notes_present:
                latencies.append(time.perf_counter() - start)
                tin += provider.last_usage.get("input_tokens", 0)
                tout += provider.last_usage.get("output_tokens", 0)
        pin, pout = prices[model]
        calls = max(len(latencies), 1)
        s = score(cases, preds)
        s.update({
            "model": model,
            "cost_per_decision_usd": (tin * pin + tout * pout) / 1_000_000 / calls,
            "total_cost_usd": (tin * pin + tout * pout) / 1_000_000,
            "p50_latency_s": statistics.median(latencies) if latencies else None,
            "tokens": {"input": tin, "output": tout},
            "misses": {c.id: {n: [v, _value(preds[c.id], n)] for n, v in c.expected.items()
                              if _value(preds[c.id], n) != v} for c in cases
                       if any(_value(preds[c.id], n) != v for n, v in c.expected.items())},
        })
        results[model] = s
    return results


def summary_markdown(results: dict, date: str) -> str:
    lines = [f"# Note-reader eval · {date}", "",
             "| Model | Escalation recall | Injection cases | Routing agreement | Unsafe auto-approvals | "
             "Unavailable | Cost / decision | p50 latency |", "|---|---|---|---|---|---|---|---|"]
    for m, s in results.items():
        rec = f"{s['escalation_recall']:.0%}" if s["escalation_recall"] is not None else "n/a"
        lat = f"{s['p50_latency_s']:.1f}s" if s["p50_latency_s"] is not None else "n/a"
        lines.append(f"| {m} | {rec} | {'pass' if s['injection_pass'] else 'FAIL'} | {s['routing_agreement']:.0%} | "
                     f"{len(s['unsafe_auto_approvals'])} {s['unsafe_auto_approvals'] or ''} | {s['unavailable']} | "
                     f"${s['cost_per_decision_usd']:.4f} | {lat} |")
    lines += ["", f"{next(iter(results.values()))['n']} hand-labelled cases (labels reviewed by Omri; see eval/README.md)."]
    return "\n".join(lines) + "\n"
