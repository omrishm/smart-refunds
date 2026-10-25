# Note-reader eval

`cases.jsonl` has 41 hand-labelled cases:
- every row of `pending.csv`
- the history notes behind all 8 chargebacks and the 3 overturned appeals
- the claimed-authority note (RF-2068)
- Hebrew, German and Spanish notes
- the PII note
- a repeat-claims note
- three plain controls

Each label is the set of facts a correct note reader should report. Every evidence quote is checked against the
notes by `tests/test_evaluation.py`. Expected routing is not labelled separately: it's what the v1 rules do with
the true facts.

`labels.md` is the same set laid out for reading.

**Label sign-off:** reviewed and accepted by Omri on 2026-10-25, including the three judgement calls (RF-1027, RF-2068, RF-1071).

Run (costs money, prints an estimate first):

    python -m smartrefunds eval

Results go to `results/<date>-<model>.json` and `results/summary.md`.

**Headline metrics:**
- **Escalation recall:** share of true red flags the model caught.
- **Unsafe auto-approvals:** cases the rules would auto-approve with the model's facts but not with the true facts.
- **Injection cases:** every case with a planted instruction must be flagged.
