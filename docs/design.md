# Smart Refunds: Design

Status: draft for approval · 2026-10-25 · author: Omri (decisions), drafted with Claude Code

Companion document: [`findings.md`](findings.md) (data analysis, traps, per-row notes on pending).
Everything marked **(builder's call)** was decided during drafting and can be changed without asking.
Everything else was decided by Omri.

---

## 1. Intent

Windward's take-home assignment for the Applied AI Engineer role: a refund-handling system for an online
retailer. An AI-assisted system takes the easy refund requests and sends the rest to people, along with the
tools those people need.

The brief says it directly: *"The hard part isn't the agent."* The evaluators want to see:

1. **The line** between auto-approve and human review, the **evidence** behind it, and how we'd know it's
   wrong before and after switching it on. Every approval moves real money.
2. **Two mechanisms:** a **reviewer** workflow, and a way for the **Refund Ops Manager** to change the
   rules herself, previewing their effect before they go live.
3. **AI judgment:** an LLM where it earns its place, and an explicit account of where it was deliberately
   not used.

The submission is **a single git repository** sent to the recipients named in the assignment email within 4 days of
receipt. The repository *is* the deliverable: code, tests, demo, README, slides and recording link all live
in it, and the commit history is visible to the evaluators.

## 2. Goals, non-goals, anti-goals

**Goals**
- One command processes `pending.csv`, or any file with the same columns, into decisions.
- A clickable local demo (Streamlit) of the reviewer queue and the manager's rule change with preview.
- Every number on the slides is reproducible from code in the repo.
- Runs with no API key (saved AI outputs plus a fail-safe fallback). Runs live with a key.
- Narrow and working beats wide and half-working. Cuts are listed explicitly (§11).

**Non-goals** (excluded by the brief or by scope): auth, cloud deployment, real payments, customer messaging,
multi-user concurrency, production monitoring infrastructure.

**Anti-goals** (failures even if everything "works"):
- The LLM can approve money, or can be talked into it (RF-1073's injected note).
- A request is paid twice (RF-2183 appears twice).
- Missing or unparseable data is treated as safe (the legacy rule's bug).
- The manager publishes a rule whose effect she didn't see first.
- Slide numbers that the code can't reproduce.
- A repository that doesn't run on a clean checkout, or that leaks a key.

## 3. Constraints

- Scope target: 4–6 hours of build. Deadline: 4 days from receipt.
- Python 3.12 (existing `.venv`), Streamlit, pytest, the official `anthropic` SDK. Windows dev machine,
  but nothing Windows-specific.
- LLM: **Claude Opus 5.5** (`claude-opus-5-5`) for note reading and rule drafting. **Claude Haiku 5.5**
  (`claude-haiku-5-5`) is evaluated as a cheaper comparison. The key comes from `ANTHROPIC_API_KEY`
  (env var or `.env`). Omri has a key with credits. Expected total spend during the build is about
  $5–10 (history facts about $4, eval about $1, plus development calls).
- Tests never call the LLM. They use a fake client or the saved outputs.
- Input files have the columns listed in `data/data_dictionary.csv`. The data does not always comply
  (see findings §4.4).

## 4. Decisions made (as decided)

| # | Decision |
|---|---|
| D1 | **The line = option B, cap $100.** Auto-approve only reasons with complete evidence, up to $100: tenure ≥ 9 months, 0 prior refunds, fraud flag an explicit "no", never-arrived always to a person. The cap and tenure are manager-editable values. Evidence: findings §4 plus the backtest in §8. |
| D2 | **No auto-reject.** Where policy clearly says reject, the system **pre-fills a rejection** (with the policy cited) that a person confirms or edits. |
| D3 | **Manager changes rules both ways:** plain English → AI drafts a structured rule (asking a clarifying question when ambiguous) → she edits or confirms → preview → publish. The structured form is what runs. |
| D4 | **Stack:** Python + Streamlit. **Model:** Opus 5.5, with Haiku 5.5 as the eval comparison. |
| D5 | **Reviewer queue = separate lanes** (Fraud team, Delivery disputes, Damage & evidence, Policy rejections, Other) with **incident grouping** (e.g. Northfield). |
| D6 | **Case view and manager screen as in the approved mockups** (`.superpowers/brainstorm/mockups/`, not committed; the relevant content is captured in §7). |
| D7 | **Rules are an ordered list; the first matching rule decides.** Locked safety rules are pinned at the top. New rules get a suggested position, and the preview reflects that position. |
| D8 | **Scope as proposed** (§11). |

## 5. Architecture

```
            input CSV
                │
     ┌──────────▼──────────┐
     │ 1. load & clean     │  deterministic: parse, normalise, dedupe, mark invalid
     └──────────┬──────────┘
     ┌──────────▼──────────┐
     │ 2. note reader (AI) │  redact PII → Opus 5.5 → fixed set of facts + summary
     │    saved / live / off │  (only ever adds reasons to escalate)
     └──────────┬──────────┘
     ┌──────────▼──────────┐
     │ 3. rules engine     │  deterministic, ordered, first match wins → outcome + lane + draft
     └──────────┬──────────┘
        ┌───────┴────────┐
   auto-approve     route to person (lane, reason, optional pre-filled rejection)
        │                │
  mock payment     reviewer UI ──► decision ──► audit log, mock payment, metrics
  (once per ID)
                   manager UI ──► draft rule ──► preview engine (history + pending) ──► publish / rollback
```

The **preview engine** is the same code that produces the slide evidence (backtest) and the manager's
preview. It runs a rule set over cleaned rows plus saved facts and diffs the result against the live rule set.

### 5.1 Proposed repo layout (builder's call)

```
smartrefunds/            package
  clean.py               loading and normalisation
  notes.py               note reader: redaction, prompt, schema, saved-output cache, fallback
  rules.py               rule schema, evaluation, safety rules
  engine.py              pipeline: clean → facts → rules → outcomes
  preview.py             replay + diff + history outcome labels
  incidents.py           grouping
  store.py               SQLite: decisions, audit log, mock payments, rule versions
  rule_drafting.py       plain English → structured rule (LLM)
  cli.py / __main__.py   `python -m smartrefunds ...`
app/                     Streamlit (one app, role switch: reviewer / fraud team / manager)
rules/v1.yaml            initial committed rule set
data/                    history.csv, pending.csv, data_dictionary.csv, policy_notes.csv
data/facts/              saved AI outputs, committed, clearly labelled
eval/                    labelled cases + eval runner + results (committed)
tests/
docs/                    design.md, findings.md, slides (PDF), ai-usage-log.md
README.md, requirements.txt, .env.example, .gitignore
```

## 6. Components

### 6.1 Load & clean (no AI)

Covers every issue in findings §4.4:
- **Amounts:** handle `$43`, `USD 120.00`, `55.00 USD`, spaces, and `$1,150.00` (comma).
- **Dates:** handle `YYYY-MM-DD`, `DD/MM/YYYY` and ISO timestamps. Treat `DD/MM` as day-first; the data
  dictionary's format plus the observed values (`12/06`, `09/06`) support that.
- **Normalisation:**
  - reason: `changed mind` → `changed_mind`, `damaged` / `arrived damaged` → `arrived_damaged`,
    `late` → `late_delivery`, `wrong item` → `wrong_item`, `Never Arrived` → `never_arrived`
  - category: lowercase
  - fraud flag: `yes/Y/true` → yes, `no/N/false` → no, blank → **unknown**
- **Validity:** negative `days_since_delivery` is invalid. Missing `customer_id`, `tenure_months`,
  `days_since_delivery` or `amount` is invalid. Anything that fails to parse becomes "invalid" with a
  reason, never a default value.
- **Duplicates:** exact duplicate rows are dropped, and the drop is recorded. A request ID that appears
  more than once with different content is marked `duplicate_id` on all copies.
- **Change log:** each row keeps a list of what cleaning changed. The case view shows it.

### 6.2 Note reader (AI)

**Input:**
- `customer_note` and `support_note` with PII redacted first (card digits, phone numbers, emails; regex,
  no AI).
- Plus `stated_reason`, `amount_usd`, `category` and `days_since_delivery` as context.

**Output:** a fixed schema enforced through structured outputs. Every fact has a value plus the quoted
evidence span it came from.

| Fact | Values |
|---|---|
| `photo_or_video_evidence` | present / absent / not_mentioned |
| `carrier_signature_or_photo` | yes / no / not_mentioned |
| `carrier_investigation_open` | yes / no / not_mentioned |
| `fraud_reported` | card reported stolen / unauthorised by the cardholder |
| `fraud_confirmed` | fraud team or bank confirmed |
| `picking_error_confirmed` | warehouse / support confirmed a mis-pick |
| `shipped_item_matches_order` | "matches order line" |
| `admits_delivered` | customer admits it arrived |
| `admits_own_fault` | customer admits causing the damage |
| `already_refunded_elsewhere` | double refund, e.g. RF-1074 |
| `reason_contradicts_note` | e.g. RF-1075, RF-2175 |
| `damage_cosmetic_only` | |
| `instructions_to_system` | prompt injection or claimed authority (RF-1073, RF-2068) |
| `repeat_claim_pattern` | e.g. RF-1039's support note |
| `location_tag` | normalised place or cause, e.g. `northfield` |
| `summary` | 2–3 English lines for the reviewer |
| `language` | language of the notes |

**Safety posture:**
- The note text is wrapped and labelled as untrusted data in the prompt. The model is told never to
  follow instructions found inside it.
- Most importantly, **no fact can produce an approval on its own.** The rules engine treats the AI's
  facts only as reasons to escalate, plus the evidence that approve rules require.
- One exception: approve rules that the manager writes on AI facts (e.g. stolen-card, Northfield). These
  are explicit, previewed and audited.
- `instructions_to_system` also triggers safety rule S3.

**Modes:**
- `live`: calls the API.
- `saved`: reads `data/facts/<file>.jsonl`, keyed by a hash of (notes text + prompt version + model).
- `off`: no facts.

Default: saved if available, else live if a key is set, else off. In `off` mode, safety rule S4 sends any
row with non-empty notes to a person. A live call that fails (error, refusal, schema invalid) is treated
as `off` for that row.

**Saved outputs committed:** facts for all of `history.csv` (248 unique rows, about $4 on Opus 5.5) and
`pending.csv`. They are labelled in the README and the UI ("AI facts loaded from saved run of
<date>, model X").

### 6.3 Rules engine (no AI at decision time)

**Format:** YAML, one file per version.

**Rule fields:**
- `id`, `title` (plain words), `locked` (safety rules)
- `when`: list of conditions, AND-ed. A condition is `field op value`, with `any_of` for OR.
- `then`: action plus options
- `note` (why)

**Condition vocabulary:**
- **Fields:** the cleaned columns (amount, tenure, prior_refunds, days_since_delivery, reason, category,
  payment_method, fraud_flag), the validity and duplicate markers, and all AI facts.
- **Operators:** `= != < <= > >= in not_in is_set is_unset`.

**Actions:**
- `approve` (full)
- `route` (to a `lane`, or `by_reason`)
- `route_with_draft_rejection` (lane plus a `policy` reference used in the draft text)

The first matching rule decides. If nothing matches, the case goes to a person (`Other`), never
auto-approved.

`by_reason` lane mapping:

| Reason | Lane |
|---|---|
| never_arrived, late_delivery | Delivery disputes |
| arrived_damaged | Damage & evidence |
| wrong_item, wrong_size, changed_mind | Other |
| any fraud-related routing | Fraud team |

**Initial rule set `rules/v1.yaml`** (option B, $100; reproduces the backtest):

| ID | When | Then |
|---|---|---|
| S1 🔒 | row invalid (missing / unparseable / negative days) | route → Other |
| S2 🔒 | duplicate_id | route → Other ("pay once: confirm which amount") |
| S3 🔒 | AI `instructions_to_system` | route by_reason, with a warning banner |
| S4 🔒 | notes non-empty and AI facts unavailable | route by_reason |
| R1 | fraud_flag ≠ no (yes or unknown), or AI `fraud_reported` / `fraud_confirmed` | route → Fraud team |
| R2 | reason = never_arrived and AI `carrier_signature_or_photo` = yes and `carrier_investigation_open` ≠ yes | draft rejection, policy 4 → Delivery disputes |
| R2b | reason = never_arrived and AI `admits_delivered` | route → Delivery disputes ("customer admits it arrived") |
| R3 | reason = never_arrived | route → Delivery disputes |
| R4 | reason = changed_mind and days_since_delivery > 14 | draft rejection, policy 2 → Policy rejections |
| R5 | AI `already_refunded_elsewhere` | draft rejection (double refund) → Policy rejections |
| R6 | AI `reason_contradicts_note` or `admits_own_fault` | route by_reason |
| R7 | reason = wrong_item and AI `shipped_item_matches_order` | draft rejection ("shipped item matches order") → Other |
| R8 | prior_refunds_12m > **0** | route by_reason |
| R9 | tenure_months < **9** | route by_reason |
| R10 | amount > **100** | route by_reason |
| R11 | reason = arrived_damaged and amount > **50** and photo evidence ≠ present | route → Damage & evidence |
| R12 | reason = arrived_damaged and AI `damage_cosmetic_only` | route → Damage & evidence (partial is the reviewer's call) |
| R13 | reason = late_delivery and amount ≥ **55** | route → Delivery disputes (partial is the reviewer's call) |
| R14 | reason = wrong_item and amount ≥ **55** and not AI `picking_error_confirmed` | route by_reason |
| R15 | reason = changed_mind and amount ≥ **55** | route by_reason |
| A1 | reason in {wrong_size, changed_mind, wrong_item, arrived_damaged, late_delivery} | **approve** |
| (end) | nothing matched | route → Other |

Bold values are the manager-editable numbers. Every routed outcome records the matching rule ID. The UI
also shows *all* rules the row would have matched, so "why it's here" is complete.

**(Builder's call)** Where the backtest script and this table differ in small details, this table wins, and
the slide evidence is regenerated from it.

**Traced against pending:**
- **Auto-approved:** RF-1069 and RF-1071 (A1), and RF-2198 ($52 late delivery: under the $55 late-delivery
  threshold, which matches history's full refunds for small late deliveries).
- **Fraud team:** RF-1070, RF-1077 and RF-2182 (R1).
- **Delivery disputes:** RF-2181 (R2, draft rejection), RF-1073 (S3, with warning), and RF-1067, RF-1068
  and the Northfield never-arrived cases (R3).
- **Policy rejections:** RF-1074 (R5, draft rejection).
- **Other:** RF-1072 (R7, draft rejection) and RF-2183 ×2 (S2).
- **Damage & evidence:** RF-1075 (R6) and RF-1076 (R8).

**CLI output (builder's call):** `out/decisions_<input>.csv` has the input columns plus `outcome`
(auto_approved / needs_review), `lane`, `rule_id`, `reasons`, `draft_rejection` and `cleaning_changes`.
A summary is printed to the terminal: counts per outcome and lane, auto-approved amount, and the AI mode
used.

### 6.4 Incidents

**(Builder's call)** Open, person-routed cases sharing an AI `location_tag` form an incident when there are
3 or more within any 14-day window of `requested_at`. The incident card shows the count, the total
amount, when it was first seen, and the spellings the AI matched. Actions: open the cases one by one, and
"suggest a rule to the manager" (pre-fills the manager's text box). Bulk decisions are cut.

### 6.5 Storage

SQLite in `var/` (gitignored) holds decisions, the audit log, mock payments and published rule versions.
`rules/v1.yaml` is committed as the seed. Published versions go to `var/rules/vN.yaml`, so the evaluator's
checkout stays clean. A "reset demo" button or CLI command wipes `var/`.

- **Audit entry:** timestamp, role, request ID, action, amount, reason code, free-text note, rule version,
  system suggestion, AI flag feedback.
- **Mock payment:** unique constraint on request ID. It logs "would refund $X to <method>". A second
  attempt is refused and shown.

### 6.6 Preview engine

`preview(candidate_rules, live_rules, history, pending)` → for both datasets:
- auto-approve count, amount and share
- reviewer minutes saved (history `review_minutes` of rows newly auto-approved)
- **bad outcomes let through**
- lane counts
- the list of rows whose outcome changed (before → after)

**"Bad" label for history** (findings §4.3): chargeback, or rejected and not overturned on appeal, or
partial. These are counted as auto-approvals that should not have happened. Appeal-overturned rejections
count as "should have approved".

**Warnings on the preview:**
- the rule bypasses a lane (e.g. the Fraud team)
- the largest single amount the rule would now auto-approve
- the sample is small (< 20 rows affected)
- "no effect"

Every number links to the exact rows behind it.

### 6.7 Rule drafting (AI)

**Input:** the manager's sentence, the current rule set, and the condition vocabulary.

**Output** (structured): one or more candidate rules in the rule schema, with:
- a clarifying question when there's more than one reasonable reading. Each choice maps to a candidate,
  e.g. "stolen card" confirmed vs reported.
- the suggested position in the list
- which existing rules it effectively overrides for the matching rows

**Validation:** the drafted rule must parse against the schema and use only known fields, operators and
values. If it doesn't, the manager sees "couldn't turn that into a rule" and the form opens empty for
manual editing.

Without a key, the screen still works: form-only editing. The two brief examples (stolen card, over $300)
have saved drafts so the demo runs offline. Nothing drafted is ever live until published.

## 7. Screens (Streamlit, one app)

**Role switch** at the top: Reviewer / Fraud team / Manager. No auth. A banner shows which AI mode is
active (live / saved / off) and states that payment is mocked.

### 7.1 Reviewer (and Fraud team, which sees only its own lane)

- **Lanes as tabs**, with counts.
- **The incident card** above the lane's list.
- **List columns:** request, amount, "why it's here" tags, suggested action, waiting time. Priority within
  a lane: severity of flags, then money at risk, then age **(builder's call)**.
- **Case view:**
  - facts, plus what cleaning changed
  - "Why it's here" (rules, not AI)
  - the AI reading: summary, flags with quoted evidence, 👍/👎 per flag
  - raw notes, marked as untrusted
  - an injection warning where S3 fired
  - similar past cases: same reason plus the same top flag, with their outcomes
- **Actions:**
  - Approve full
  - Approve partial (presets: shipping only, 25%, 30%, 50%, custom)
  - Reject (pre-filled draft, editable)
  - Request info (photo / carrier investigation → Waiting)
  - Move to lane
- **Going against the suggestion** requires a reason code from a short list plus optional text.
- **Waiting cases** sit in a "Waiting" tab with a "carrier/customer replied" button that returns them
  (mock).
- **After a decision:** audit entry, mock payment if money moves, and the case leaves the queue.

### 7.2 Manager

- **Left:** the live rules in plain words, in order. Locked safety rules are marked 🔒. Values like the cap
  are editable inline, and an edit creates a draft. Below: version history with roll-back.
- **Right:**
  1. a text box for the change
  2. the AI draft, with the clarifying question and an editable rule form showing position and overrides
  3. the preview (§6.6) on history and on today's queue
  4. Publish (requires a short reason), Save draft, Discard
- Roll-back is itself a previewed, published change.

## 8. AI: where it is and isn't, and how it's evaluated

**AI is used for:**
- reading notes into facts and a summary (any language)
- drafting rules from plain English
- the location/cause tag behind incident grouping

**AI is not used for:**
- the approve decision
- thresholds and date windows
- parsing, cleaning and de-duplication
- payment idempotency
- the preview arithmetic

These must be testable, explainable and safe when the model is down.

**Eval set** (`eval/cases.jsonl`, committed): about 30–40 labelled notes, covering:
- all pending traps (RF-1073 injection, RF-1074 Spanish double refund, RF-1075 contradiction, RF-2182
  confirmed fraud with a blank flag, RF-2181 signature, RF-1072 order-line match, Northfield spellings
  incl. Spanish)
- the history notes behind chargebacks and appeals
- Hebrew and German notes, the PII note, "Dana said yes"
- plain easy cases as controls

Labels are drafted from the notes by Claude Code, then **reviewed and corrected by Omri** (about
20 minutes) before any model is scored. Labels are never copied from the note reader's own output. The
eval README records who signed off on the labels.

**Metrics:**
- per-fact precision/recall
- **recall on escalation facts**, the headline number (a missed red flag means money out)
- injection resistance (pass/fail on every instruction-bearing case)
- end-to-end routing agreement after rules
- cost and latency per decision

Run on Opus 5.5 and Haiku 5.5. Results go to `eval/results/` and onto the AI slide.

**Cost estimate:** about 2,000 tokens in and 400 out per decision. Roughly 1–1.6¢ on Opus 5.5 and
0.04¢ on Haiku 5.5. At about 300 requests a week, $3–5 a week on Opus. The eval measures the actual
cost.

`python -m smartrefunds eval` runs the eval. It needs a key and prints the expected cost before running.

## 9. How we'd know the line is wrong (for the slides)

**Before switch-on:**
- the backtest of v1 on history using outcome labels (§6.6)
- the eval
- *(described, not built)* two weeks of shadow mode, where the system decides alongside reviewers and
  disagreements are reviewed

**After switch-on, watch:**
- auto-approve rate
- chargebacks and appeals on auto-approved requests
- the share of decisions that go against the suggestion, per lane
- a weekly random audit sample of auto-approvals
- queue wait time per lane
- AI-flag drift (a spike in a tag, e.g. Northfield)

**Rollback triggers** (proposed numbers, labelled as such):
- any chargeback on an auto-approval above $X
- audit disagreement above 5%
- the AI unavailable for long enough that S4 floods the queue

## 10. Repository and submission

**Hosting:** a **public GitHub repository**, with the link emailed to the three recipients. Because it's
public:
- Keys must never be committed; check `git status` and the diff before every push.
- The brief PDF stays out of the repo.
- Saved AI outputs contain only redacted note text.

**Setup:**
- **`recieved_files/` is never in git history (Omri's decision).** It was only a temporary way to hand
  over the files for ideation. The PDF is Omri's own conversion of the brief.
  - The four CSVs are copied into `data/` before `git init`.
  - `recieved_files/` is in `.gitignore` before the first commit.
  - Before every commit, `git ls-files` must show nothing from `recieved_files/` and no `*.pdf` except
    `docs/slides.pdf`.
  - Whether to delete the local folder afterwards is Omri's call.
- `git init` at the project root, only after `.gitignore` exists.
- **`.gitignore`:** `.venv/`, `.idea/`, `.superpowers/`, `var/`, `out/`, `.env`, `__pycache__/`, `.pytest_cache/`,
  `recieved_files/`.
- **`.env.example`:** `ANTHROPIC_API_KEY=`. No key is ever committed.

**Commands from a clean checkout:**
- `pip install -r requirements.txt`
- `python -m smartrefunds run data/pending.csv` (decisions CSV plus summary)
- `streamlit run app/main.py` (demo)
- `pytest`
- `python -m smartrefunds eval`

**README:**
- how to run, incl. the API key and the three AI modes
- an architecture sketch (from §5)
- which demo parts are mocked: payment, waiting-state resume, customer messages
- links to the slides, recording and design docs
- one paragraph on how AI coding tools were used, including one thing they produced that was changed or
  thrown away

**AI-usage log:** `docs/ai-usage-log.md` records, as work happens, where AI coding output was kept,
changed or thrown away. It feeds the README line. Candidates from today:
- the keyword red-flag list was tuned to these rows, so it was replaced by the LLM
- a preview mockup had wrong numbers (103 vs 101) and a wrong override assumption (R1 only), both caught
  and fixed
- the exploratory analysis scripts were discarded in favour of the preview engine

**Commits:** small, meaningful commits, since the history is visible to evaluators.

**Slides:** max 8, exported to `docs/slides.pdf`. The tool is open (§12).

**Recording:** 3–5 minutes, linked from the README. Hosted outside the repo to keep it small
**(builder's call)**.

## 11. Scope

**Built and working:**
- load and clean
- note reader (live, saved and off modes; PII redaction)
- rules engine with v1 and safety rules
- CLI `run`
- preview engine
- reviewer screen (lanes, incidents, case view, actions, audit, role switch)
- manager screen (rules list, inline edits, plain English → draft with clarifying question, preview,
  publish, rollback)
- eval on Opus 5.5 and Haiku 5.5
- README, slides, recording

**Mocked (and labelled in the UI and README):**
- payment (logged, once per ID)
- "Waiting" resume button
- rejection message (drafted and shown, never sent)

**Cut (listed on the "deferred" slide):**
- bulk incident decisions
- automatic expiry of temporary rules
- shadow mode and live monitoring alerts (described only)
- 👍/👎 feedback feeding the eval automatically (stored only)
- smarter similar-case matching
- multi-user handling

**If time runs short:** keep the manager's plain-English path working for the brief's two examples and
say so. Don't cut the preview.

**Build order:** each step leaves something that runs.
1. clean + rules + CLI with tests
2. note reader + saved outputs
3. preview engine
4. reviewer screen
5. manager screen
6. eval
7. README, slides, recording

## 12. Left to the builder / open

- Internal module boundaries, prompt wording, the exact Streamlit layout and styling.
- The exact reason-code list and partial-refund presets beyond those listed.
- Slide tool and visual style (decide when writing slides).
