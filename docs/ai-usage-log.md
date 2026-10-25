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
