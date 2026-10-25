# AI coding tools: usage log

A running record of where AI coding output (Claude Code) was kept, changed or thrown away. It feeds the
README's "how AI coding tools were used" section.

## Analysis and design

- **Thrown away: a keyword red-flag list.** It stood in for the LLM note reader during the first backtest.
  It was tuned after reading these exact rows, so its results overstated what it would catch on new data.
  Replaced by the LLM note reader plus a hand-labelled eval set.
- **Changed: a preview mockup.**
  - It showed the wrong baseline: 103 auto-approvals, the $150 figure, instead of 101 at the $100 cap.
  - It assumed the stolen-card rule only needed to override the fraud-team rule (R1). In fact all 11
    historical cases were never-arrived claims from 1-month-old accounts, so it must also override R3 and
    R4.
  - Both were caught when checking the numbers against the data. This led to the "ordered rules, first
    match wins" design.
- **Thrown away: exploratory analysis scripts.** One-off scripts produced the numbers in
  `docs/findings.md`. They were replaced by the repo's preview engine (`python -m smartrefunds backtest`),
  so slide numbers are reproducible from committed code.

## Build

- **Changed: the note-reader prompt (notes-v1 → notes-v2).**
  - The first live run on `pending.csv` showed Opus 5.5 answering `admits_delivered = yes` for wrong-item,
    wrong-size and late-delivery requests. That's literally true, since the parcel did arrive, but it isn't
    what the fact is for.
  - Through rule R6 it sent RF-2198 (a $52 late delivery) to a person.
  - Fixed in two places:
    - the prompt now scopes the fact to never-arrived claims
    - the rule only uses it for `never_arrived` (new R2b), so a model slip can't misroute other reasons
  - Caught by comparing the live output against the hand labels before running the 248 history rows.
- **Changed: a test that silently spent money.** Once a real key was in `.env`, the app loaded it during a
  manager-screen test and made one live drafting call (about 1¢). Tests now blank the key with an autouse
  fixture, so no test can reach the API.
