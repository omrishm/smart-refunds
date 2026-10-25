"""Command line: python -m smartrefunds <command> ..."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .clean import InputFileError, load_requests
from .engine import FactsProvider, decide_all, summarize, write_decisions_csv
from .rules import load_rules

DEFAULT_RULES = Path("rules/v1.yaml")
FACTS_DIR = Path("data/facts")
# $ per 1M tokens (input, output) and a rough per-request size, for cost estimates printed before paid runs.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50)}
EST_TOKENS = (2000, 400)


def estimate_cost(n: int, model: str) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-opus-5-5"])
    return n * (EST_TOKENS[0] * pin + EST_TOKENS[1] * pout) / 1_000_000


def confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def get_provider(mode: str) -> FactsProvider:
    from .notes import make_provider

    return make_provider(mode, FACTS_DIR)


def cmd_run(args: argparse.Namespace) -> int:
    requests = load_requests(args.csv).requests
    provider = get_provider(args.ai)
    decisions = decide_all(requests, load_rules(args.rules), provider)
    out = Path(args.out) / f"decisions_{Path(args.csv).stem}.csv"
    write_decisions_csv(decisions, out)
    print(summarize(decisions, provider.mode))
    print(f"wrote {out}")
    return 0


def cmd_extract_facts(args: argparse.Namespace) -> int:
    from . import notes

    requests = [r for r in load_requests(args.csv).requests if r.notes_present]
    out = Path(args.out)
    done: set[str] = set()
    if out.exists():
        done = {json.loads(l)["key"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    todo = [r for r in requests if notes.cache_key(r, args.model) not in done]
    print(f"{len(todo)} rows with notes to read ({len(requests) - len(todo)} already saved), "
          f"estimated cost ${estimate_cost(len(todo), args.model):.2f} on {args.model}")
    if not todo:
        return 0
    if not confirm("Proceed?", args.yes):
        print("cancelled")
        return 1
    provider = notes.LiveProvider(notes._client_from_env(), args.model, args.effort)
    out.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with open(out, "a", encoding="utf-8") as f:
        for i, r in enumerate(todo, 1):
            facts = provider.get(r)
            failures += facts is None
            rec = {
                "key": notes.cache_key(r, args.model), "request_id": r.request_id, "row_index": r.row_index,
                "model_requested": args.model, "model_answered": provider.last_model,
                "prompt_version": notes.PROMPT_VERSION,
                "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "facts": facts.model_dump() if facts else None, "error": provider.last_error,
                "warnings": provider.last_warnings, "usage": provider.last_usage,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(todo)}] {r.request_id}: {'ok' if facts else 'FAILED ' + str(provider.last_error)}")
    print(f"wrote {out} ({failures} failed)")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .preview import backtest_summary

    history = load_requests(args.history).requests
    print(backtest_summary(load_rules(args.rules), history, get_provider(args.ai)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smartrefunds", description="Smart Refunds triage")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="decide every request in a CSV")
    run.add_argument("csv")
    run.add_argument("--rules", default=str(DEFAULT_RULES))
    run.add_argument("--ai", choices=["auto", "live", "saved", "off"], default="auto")
    run.add_argument("--out", default="out")
    run.set_defaults(func=cmd_run)

    ex = sub.add_parser("extract-facts", help="read notes with the LLM and save facts (costs money)")
    ex.add_argument("csv")
    ex.add_argument("--out", required=True)
    ex.add_argument("--model", default="claude-opus-5-5")
    ex.add_argument("--effort", default="low")
    ex.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    ex.set_defaults(func=cmd_extract_facts)

    bt = sub.add_parser("backtest", help="replay a rule set over history (the line-slide evidence)")
    bt.add_argument("--rules", default=str(DEFAULT_RULES))
    bt.add_argument("--history", default="data/history.csv")
    bt.add_argument("--ai", choices=["auto", "live", "saved", "off"], default="saved")
    bt.set_defaults(func=cmd_backtest)
    return p


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    from .notes import ConfigError

    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (InputFileError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
