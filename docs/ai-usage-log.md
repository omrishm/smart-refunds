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

## Backtest v1 (saved Opus 5.5 facts, prompt notes-v2)

`python -m smartrefunds backtest --ai saved` on 2026-10-25:

```
Backtest on 248 historical requests (AI mode: saved)
  Rule set v1: auto-approves 103 (42%), $3,302.75; bad outcomes let through: 1 ['RF-2074']
    reviewer minutes saved vs today: 35 of 1119
    to people by lane: Damage & evidence 38, Delivery disputes 43, Fraud team 20, Other 35, Policy rejections 9
  Legacy auto-approve rule (actual): 111 (45%), $3,377.13; bad outcomes: 1 ['RF-2074']
```

- Every chargeback, partial refund and policy rejection in history is routed to a person, except RF-2074 (a $49
  "damaged" claim with empty notes, which nothing on the page could catch).
- **14 of the old rule's approvals go to people instead:**
  - 7 small never-arrived depot claims
  - 3 damage claims over $50 with no photo
  - 3 rows with missing or invalid data
  - RF-2124 ("honestly it's fine")
- **6 cases humans handled are now automated:** RF-1002, RF-1007, RF-2022, RF-2041, RF-2165 and RF-2174.
- Facts extraction cost: $3.75 for 205 history rows, plus $0.67 for pending (two runs, notes-v1 and notes-v2).

## Model switch (notes-v3)

- **Changed: the note-reader model, Opus 5.5 → Haiku 5.5.**
  - The eval (`eval/results/summary-notes-v2.md`) showed the two models identical on everything that moves money:
    93% escalation recall, injection cases flagged, 100% routing agreement, 0 unsafe auto-approvals.
  - Haiku is about 1/37th of the cost.
  - Haiku's one weakness was normalising place names ("nfd", "north field"), which would split the Northfield
    incident.
  - Rather than prompt harder, the prompt now asks for the name as written, and code normalises it
    (`smartrefunds/places.py`, `rules/place_aliases.yaml`).
  - The notes-v3 re-run on Haiku kept every safety number and reached 100% location recall.

## Three-model comparison (notes-v4)

- **Changed: the eval set, after the backtest exposed a gap.**
  - On the full history, Haiku with prompt notes-v3 let RF-1021 through: "dented box", "contents fine per photos", a
    rejection that stood.
  - None of the 41 labelled cases covered "only the packaging was damaged".
  - The prompt now defines that as contradicting the claim (notes-v4).
  - Two labelled cases were added (RF-1021, RF-2059).
- **Also added Sonnet 5.5,** at Omri's suggestion. Result on the same 43 cases:
  - Haiku: 100% escalation recall
  - Sonnet and Opus: 94% each
  - All three: 0 unsafe approvals and every injection flagged
- **Haiku re-read all history and pending.** The backtest is identical to Opus: 103 auto-approved, $3,302.75, only
  RF-2074 let through.
- Spend for this round: about $1.13 for the eval and $0.11 for the re-read.
