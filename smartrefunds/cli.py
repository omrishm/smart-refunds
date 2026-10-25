"""Command line: python -m smartrefunds <command> ..."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .clean import InputFileError, load_requests
from .engine import FactsProvider, OffProvider, decide_all, summarize, write_decisions_csv
from .rules import load_rules

DEFAULT_RULES = Path("rules/v1.yaml")


def get_provider(mode: str) -> FactsProvider:
    return OffProvider()


def cmd_run(args: argparse.Namespace) -> int:
    requests = load_requests(args.csv).requests
    provider = get_provider(args.ai)
    decisions = decide_all(requests, load_rules(args.rules), provider)
    out = Path(args.out) / f"decisions_{Path(args.csv).stem}.csv"
    write_decisions_csv(decisions, out)
    print(summarize(decisions, provider.mode))
    print(f"wrote {out}")
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except InputFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
