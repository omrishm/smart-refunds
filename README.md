# Smart Refunds

A refund-triage system for an online retailer. Clear, evidence-backed requests are refunded automatically; the
rest go to the right person with the reasons attached. An LLM reads the free-text notes, but **only rules
decide**, and the Refund Ops Manager can change those rules herself, previewing their effect on history and
today's queue before anything goes live.

Built for the Windward Applied AI Engineer home assignment. Design: [`docs/design.md`](docs/design.md) ·
data analysis: [`docs/findings.md`](docs/findings.md) · slides: [`docs/slides.pdf`](docs/slides.pdf) ·
recording: _link added on submission_.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

python -m smartrefunds run data/pending.csv    # decide a file → out/decisions_pending.csv + summary
streamlit run app/main.py                      # the clickable demo (reviewer queue + manager rules)
pytest                                         # tests (never call the API)
```

`run` takes any CSV with the same columns as `data/pending.csv`.

## API key and AI modes

Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Without a key everything still runs.

| Mode (`--ai`, or `SMARTREFUNDS_AI`) | What happens |
|---|---|
| `auto` (default) | Saved AI facts for known notes (`data/facts/`); unseen notes go to the API if a key is set, otherwise as `off` |
| `saved` | Saved facts only; unseen notes are treated as AI-unavailable |
| `live` | Every note goes to the API (Claude Haiku 5.5) |
| `off` | No AI. Safety rule S4 sends every request that has notes to a person |

Commands that cost money print an estimate and ask first:
- `extract-facts`: re-read notes and save the facts.
- `eval`: score the note reader.

## How it works

```
input CSV → 1. clean (deterministic) → 2. note reader (AI: facts only) → 3. rules (ordered, first match wins)
                                                                          ├─ auto-approve → mock payment (once per ID)
                                                                          └─ person: lane + reasons (+ pre-filled rejection)
reviewer screen → decision → audit log, mock payment
manager screen  → plain English → AI draft → structured rule → preview (history + today) → publish / roll back
```

- **Cleaning (`smartrefunds/clean.py`).** Handles messy amounts, dates, reasons and flags, plus duplicates.
  Anything unreadable is marked invalid and goes to a person; it's never treated as safe.
- **Note reader (`smartrefunds/notes.py`).**
  - Removes card numbers, phone numbers and emails, then asks Claude Haiku 5.5 for a fixed set of facts with
    verbatim quotes.
  - Notes are passed as untrusted data, so planted instructions are flagged, not followed.
  - Approval evidence that isn't actually in the notes is discarded.
  - Any failure means "no facts", and the request goes to a person.
- **Rules (`rules/v1.yaml`, `smartrefunds/rules.py`).** Four locked safety rules, then the policy rules, then
  one approve rule.
  - The auto-approve line: up to $100, account at least 9 months old, no refunds in the last 12 months, fraud
    flag explicitly "no", evidence required for each reason.
  - Never-arrived claims and fraud always go to a person.
  - Rejections are only ever drafted for a person to confirm.
- **Preview (`smartrefunds/preview.py`).** Replays any rule set over history, judged by what happened
  afterwards (chargebacks, appeals, partials), and over today's queue. The same code produces the slide
  numbers: `python -m smartrefunds backtest`.
- **Demo (`app/`).** Streamlit with a role switch (no logins).
  - The reviewer queue has lanes, incident grouping (e.g. the Northfield depot backlog), the case view and
    the decisions.
  - The manager screen covers rules, inline edits, plain-English drafts with a clarifying question, preview,
    publish and roll-back.
  - Demo state lives in `var/`; reset it from the sidebar or with `python -m smartrefunds reset-demo`.

## What's mocked

- **Payments:** logged as "would refund $X", at most once per request ID. Nothing is paid.
- **"Waiting" cases:** come back via a *carrier/customer replied (simulated)* button.
- **Rejection messages:** drafted and editable, never sent.
- **No logins:** the role switch stands in for auth.

## Commands

| Command | Purpose |
|---|---|
| `python -m smartrefunds run FILE [--ai MODE] [--rules PATH] [--out DIR]` | Decide every request in a CSV. Without `--rules` it uses the live version: the newest one published in the demo, else `rules/v1.yaml` |
| `python -m smartrefunds backtest [--rules PATH] [--ai saved]` | Replay a rule set over history (line-slide evidence) |
| `python -m smartrefunds extract-facts FILE --out data/facts/NAME.jsonl` | Read notes with the LLM and save facts (paid) |
| `python -m smartrefunds eval [--models a,b]` | Score the note reader on `eval/cases.jsonl` (paid) |
| `python -m smartrefunds reset-demo` | Clear demo decisions, payments and published rules |

## AI: where it is and isn't

**The LLM is used for three things:**
- reading notes into facts and a reviewer summary, in any language (Claude Haiku 5.5)
- drafting rules from the manager's sentences, which she always confirms (Claude Opus 5.5: rare, harder
  reasoning, so cost doesn't matter there)
- the place tag behind incident grouping

**It is deliberately not used for:**
- the approve decision, thresholds and date windows
- parsing, de-duplication and payment idempotency
- the preview arithmetic

Those must be testable, explainable and safe when the model is down. Place-name spellings ("NFD",
"North field") are normalised in code (`rules/place_aliases.yaml`), not by the model.

**Model choice came from the eval** ([`eval/results/summary-notes-v4.md`](eval/results/summary-notes-v4.md)), on the same 43
hand-labelled cases:

| Model | Red flags caught | Unsafe approvals | Cost per decision |
|---|---|---|---|
| Haiku 5.5 | 100% | 0 | 0.05¢ |
| Sonnet 5.5 | 94% | 0 | 0.95¢ |
| Opus 5.5 | 94% | 0 | 1.9¢ |

All three flagged every planted instruction and matched the expected routing on every case. The cheapest won, and the
backtest over all 248 past decisions confirmed it: at the $100 cap Haiku gives the same outcome as Opus on every case.
Above $200 they differ on one case (RF-2082, liquid damage that Opus flags and Haiku misses), so the cap also acts as
a second safety layer behind the note reader.

## How AI coding tools were used

Built with Claude Code: analysis of the data, design discussion, then test-first implementation from a written
plan ([`docs/plan.md`](docs/plan.md)). Every decision about the line, the mechanisms and the scope was made by me.
The running log is in [`docs/ai-usage-log.md`](docs/ai-usage-log.md).

**One thing I threw away:** the first backtest used a keyword red-flag list that the AI wrote after reading the
very rows it was then scored on. It looked accurate, but only because it was tuned to the answers. I replaced it
with the LLM note reader, measured on a hand-labelled set that I reviewed.
