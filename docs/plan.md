# Smart Refunds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A runnable refund-triage system: a CLI that decides `pending.csv` (auto-approve vs route to a person), a Streamlit demo of the reviewer queue and of the manager's rule change with preview, an AI eval, and the submission docs. All of it lives in one public git repo.

**Architecture:**
- Deterministic cleaning, then an AI note reader that only extracts facts, then a deterministic ordered rules engine (first match wins).
- One preview engine replays any rule set over history and pending. It powers both the manager's preview and the slide evidence.
- Streamlit reads the same engine. State lives in SQLite under a git-ignored `var/`.

**Tech Stack:**
- Python 3.12.6 (existing `.venv`)
- `anthropic` SDK (Claude Opus 5.5 / Haiku 5.5), `pydantic`, `pyyaml`, `python-dotenv`
- `streamlit` (with `streamlit.testing.v1.AppTest` for UI tests), `pytest`

**Spec:** [`docs/design.md`](design.md). Data analysis: [`docs/findings.md`](findings.md). Executors read both. Section references below (§N) point into `design.md`.

## Global Constraints

- **`recieved_files/` is never tracked.** It must be in `.gitignore` before `git init`. Before every commit, `git ls-files` must show nothing under `recieved_files/` and no `*.pdf` other than `docs/slides.pdf`. `tests/test_repo_hygiene.py` enforces this.
- Never commit `.env`, keys, `var/`, `out/`, `.venv/`, `.idea/` or `.superpowers/`. The repo is **public**.
- Git identity is Omri's. Never set `user.name` / `user.email` yourself. Never push unless Omri says to.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Model IDs exactly `claude-opus-5-5` (default) and `claude-haiku-5-5` (eval comparison). Never send `temperature` / `top_p` / `top_k` (400 since 2026-10-21). Never set `thinking: disabled` on Opus 5.5 (400). Control depth with `output_config.effort`.
- Before writing any code that calls the API, read the claude-api skill's `python/claude-api/README.md` (structured outputs, refusal handling, fallbacks). Use only documented SDK calls.
- **Tests never call the API.** They use fakes or saved files. Any live call that costs money requires Omri's go-ahead, and the command prints the estimated cost first.
- **The AI never approves.** AI facts only add escalation reasons, or supply evidence that a rule explicitly requires.
- **No auto-reject.** Rejections are drafts a person confirms (D2).
- **At most one mock payment per `request_id`** (unique constraint).
- **Default rule values (v1):**

  | Setting | Value |
  |---|---|
  | Cap | $100 |
  | Tenure | ≥ 9 months |
  | Prior refunds allowed | 0 |
  | Change-of-mind window | 14 days |
  | Damage photo threshold | > $50 |
  | Small-amount threshold for late_delivery / wrong_item / changed_mind | $55 |

- **Lane names exactly:** `Fraud team`, `Delivery disputes`, `Damage & evidence`, `Policy rejections`, `Other`.
- All commands run from the repo root with `.venv/Scripts/python` on Windows (`.venv/bin/python` elsewhere). Plain `python` below means that interpreter.

## Review Focus

1. **A new file with unseen values** (reason `defective`, category `Toys`, fraud flag `TRUE`/`maybe`) must never auto-approve. Unknown reason → invalid (S1). Unknown flag → `unknown` (R1). Pinned in Task 1 (`test_unseen_values`) and Task 2 (`test_unknown_reason_never_approves`).
2. **Malformed input** (missing column, header-only file, UTF-8 BOM, cp1252 bytes, nonexistent path) must give a one-line error and exit code 2, or an empty run, never a traceback. Pinned in Task 1 (`test_missing_column_raises`, `test_header_only`, `test_encodings`) and Task 3 (`test_cli_bad_input`).
3. **AI output that is schema-valid but unsupported** must not create approval evidence. If an approval-supporting fact has an evidence quote that isn't in the redacted notes, it's downgraded to `not_mentioned`. A refusal or error means facts are unavailable (S4). Pinned in Task 4 (`test_unquoted_approval_evidence_downgraded`, `test_refusal_and_errors_mean_unavailable`).
4. **A dangerously broad manager rule** must be blocked by `validate()` with a plain message:
   - approve with no conditions
   - a negative threshold
   - a rule placed above, or editing, a locked rule

   The preview always shows the largest single new auto-approval. Pinned in Task 2 (`test_validate_errors`) and Task 6 (`test_preview_warnings`).
5. **The same request decided twice** (double click, two copies of RF-2183, re-running the CLI) must pay at most once and give deterministic output. Pinned in Task 7 (`test_pay_once`), Task 9 (`test_duplicate_copy_payment_refused`) and Task 3 (`test_run_twice_identical`).

---

## File structure

| Path | Responsibility |
|---|---|
| `smartrefunds/clean.py` | CSV loading, normalisation, validity, duplicate marking |
| `smartrefunds/facts.py` | `Facts` pydantic schema, fact name lists |
| `smartrefunds/notes.py` | redaction, prompt, cache key, Saved/Live/Off providers |
| `smartrefunds/rules.py` | rule schema, YAML I/O, validation, evaluation, draft-rejection texts |
| `smartrefunds/engine.py` | pipeline: requests + provider + rules → decisions, CSV out, summary |
| `smartrefunds/incidents.py` | incident grouping |
| `smartrefunds/preview.py` | history labels, replay, diff, warnings |
| `smartrefunds/store.py` | SQLite state + published rule versions under `var/` |
| `smartrefunds/drafting.py` | plain English → candidate rules (LLM + saved drafts), position, overrides |
| `smartrefunds/evaluation.py` | eval scoring + runner |
| `smartrefunds/cli.py`, `smartrefunds/__main__.py` | `run`, `extract-facts`, `backtest`, `eval`, `reset-demo` |
| `rules/v1.yaml` | committed seed rule set |
| `data/*.csv` | the four assignment CSVs |
| `data/facts/*.jsonl` | saved AI facts (committed) |
| `data/drafts/*.json` | saved drafts for the two brief examples (hand-written) |
| `app/main.py`, `app/reviewer.py`, `app/manager.py`, `app/state.py` | Streamlit |
| `eval/cases.jsonl`, `eval/README.md`, `eval/results/` | eval set, sign-off, results |
| `tests/…` | one test file per module, plus `tests/fixtures/` |

---

### Task 0: Repository bootstrap

**Files:**
- Create: `.gitignore`, `requirements.txt`, `.env.example`, `pytest.ini`
- Create: `smartrefunds/__init__.py`, `tests/test_repo_hygiene.py`, `docs/ai-usage-log.md`
- Copy: `recieved_files/{history,pending,data_dictionary,policy_notes}.csv` → `data/` (byte-identical copies)

**Interfaces:**
- Produces: `data/` paths used by every later task, and the hygiene test run on every commit.

- [ ] **Step 1: Copy the CSVs and write `.gitignore` before anything touches git**

`.gitignore` lines:
```
.venv/
.idea/
.superpowers/
var/
out/
.env
__pycache__/
.pytest_cache/
recieved_files/
```
Verify the copies: `python -c "import filecmp,sys; sys.exit(0 if all(filecmp.cmp(f'recieved_files/{n}.csv', f'data/{n}.csv', shallow=False) for n in ['history','pending','data_dictionary','policy_notes']) else 1)"` exits 0.

- [ ] **Step 2: Write `requirements.txt` and `.env.example`, then install**

`requirements.txt`: `anthropic`, `pydantic`, `pyyaml`, `python-dotenv`, `streamlit`, `pytest`. Install with `python -m pip install -r requirements.txt`, then pin the installed versions with `==` (from `pip freeze`, these six only).

`.env.example`:
```
ANTHROPIC_API_KEY=
# SMARTREFUNDS_AI=auto   # auto | live | saved | off
```

- [ ] **Step 3: Write `tests/test_repo_hygiene.py`**

```python
def test_recieved_files_never_tracked():
    tracked = git_ls_files()          # [] when not a git repo yet
    assert not [p for p in tracked if p.startswith("recieved_files/")]
    assert [p for p in tracked if p.lower().endswith(".pdf")] in ([], ["docs/slides.pdf"])

def test_no_secrets_tracked():
    assert ".env" not in git_ls_files()

def test_gitignore_lists_private_paths():
    text = Path(".gitignore").read_text()
    for entry in ["recieved_files/", ".env", "var/", ".superpowers/", ".venv/"]:
        assert entry in text.splitlines()
```
`git_ls_files()` runs `git ls-files` via `subprocess` and returns `[]` if git fails.

- [ ] **Step 4: Run `python -m pytest tests/test_repo_hygiene.py -v`**. Expected: 3 passed.

- [ ] **Step 5: Git identity check (Omri).** Run `git init`, then `git config user.email`. If it's empty, **stop and ask Omri** to run `git config user.name "…"` and `git config user.email "…"`. Don't invent them.

- [ ] **Step 6: Stage, verify, commit**

```bash
git add .gitignore requirements.txt .env.example pytest.ini smartrefunds/__init__.py tests/test_repo_hygiene.py data/ docs/design.md docs/findings.md docs/plan.md docs/ai-usage-log.md
git status --short          # must not list recieved_files/, .env, .idea/, .superpowers/
python -m pytest tests/test_repo_hygiene.py -q
git commit -m "chore: bootstrap repo with data, design and plan"
```

`docs/ai-usage-log.md` starts with the three entries from design §10.

---

### Task 1: Load & clean (`smartrefunds/clean.py`)

**Files:** Create `smartrefunds/clean.py`, `tests/test_clean.py`

**Interfaces:**
- Produces:
```python
REASONS = ("wrong_size","changed_mind","arrived_damaged","wrong_item","never_arrived","late_delivery")
FraudFlag = Literal["yes","no","unknown"]
REQUIRED_COLUMNS: tuple[str, ...]   # the 18 columns in data/data_dictionary.csv, in file order

class InputFileError(Exception): ...

@dataclass(frozen=True)
class Request:
    row_index: int; request_id: str; requested_at: date | None; customer_id: str | None
    tenure_months: float | None; prior_refunds_12m: int | None; amount_usd: float | None
    category: str | None; payment_method: str | None; stated_reason: str | None
    customer_note: str; support_note: str; days_since_delivery: float | None; fraud_flag: FraudFlag
    decision: str | None; decided_by: str | None; outcome_note: str
    decided_at: datetime | None; review_minutes: float | None
    invalid_reasons: tuple[str, ...]; duplicate_id: bool; cleaning_changes: tuple[str, ...]
    @property is_valid -> bool            # not invalid_reasons
    @property notes_present -> bool       # either note non-blank

@dataclass
class LoadResult:
    requests: list[Request]; dropped_exact_duplicates: list[str]   # request_ids of dropped copies

def parse_amount(raw: str) -> float | None
def parse_date(raw: str) -> date | None          # YYYY-MM-DD, DD/MM/YYYY (day-first), ISO with time
def normalise_reason(raw: str) -> str | None     # None if unknown
def normalise_flag(raw: str) -> FraudFlag
def load_requests(path: str | Path) -> LoadResult
```

- [ ] **Step 1: Write the failing tests**

| Test | Assertions |
|---|---|
| `test_parse_amount` | `"$1,150.00"→1150.0`, `"USD 120.00"→120.0`, `"55.00 USD"→55.0`, `" 42 "→42.0`, `"$43"→43.0`, `"abc"→None`, `""→None` |
| `test_parse_date` | `"03/09/2026"→date(2026,9,3)`, `"12/06/2026"→date(2026,6,12)`, `"2026-06-15T07:11:00Z"→date(2026,6,15)`, `"2026-08-19"→date(2026,8,19)`, `"soon"→None` |
| `test_normalise_reason` | `"Never Arrived"→"never_arrived"`, `"changed mind"→"changed_mind"`, `"damaged"` and `"arrived damaged"→"arrived_damaged"`, `"late"→"late_delivery"`, `"wrong item"→"wrong_item"`, `"defective"→None` |
| `test_normalise_flag` | `yes/Y/TRUE/true→"yes"`, `no/N/false→"no"`, `""` and `"maybe"→"unknown"` |
| `test_history_load` | 248 requests. `dropped_exact_duplicates == ["RF-2040","RF-1019","RF-2111"]` |
| `test_history_invalid_rows` | RF-2139 invalid (negative days). RF-2048 and RF-2167 invalid (tenure). RF-2065 invalid (customer_id). RF-2050 invalid (days missing) |
| `test_pending_load` | 21 requests. Both RF-2183 have `duplicate_id`. RF-2181: `amount_usd==1150.0`, `stated_reason=="never_arrived"`, `requested_at==date(2026,9,3)`, `cleaning_changes` mentions amount, date and reason. RF-2182 `fraud_flag=="unknown"`. RF-1077 `fraud_flag=="yes"` |
| `test_unseen_values` | tmp CSV row with reason `defective`, category `Toys`, flag `maybe` → `stated_reason is None`, `"unknown stated_reason 'defective'"` in `invalid_reasons`, category `"toys"`, flag `"unknown"` |
| `test_missing_column_raises` | CSV without `amount_usd` → `InputFileError` whose message contains `amount_usd` |
| `test_header_only` | header-only CSV → `LoadResult([], [])` |
| `test_encodings` | the same row saved as UTF-8 with BOM and as cp1252 (`"café"` in note) both load, with the note intact |

- [ ] **Step 2: Run `python -m pytest tests/test_clean.py -v`**. Expected: FAIL (module missing).

- [ ] **Step 3: Implement `clean.py`**
- Read with `csv.DictReader`. Encoding: try `utf-8-sig`, fall back to `cp1252`.
- Invalid when `amount_usd`, `tenure_months`, `days_since_delivery` or `customer_id` is missing or unparseable, when days is negative, or when the reason is unknown.
- Exact-duplicate detection compares all 18 raw values.
- `duplicate_id` is set on every remaining row whose `request_id` appears more than once.
- Each normalisation that changed a value appends `"<column>: '<raw>' → '<clean>'"` to `cleaning_changes`.

- [ ] **Step 4: Run the tests**. Expected: all pass.

- [ ] **Step 5: Commit** `feat: load and clean refund CSVs`.

---

### Task 2: Facts schema + rules engine (`facts.py`, `rules.py`, `rules/v1.yaml`)

**Files:** Create `smartrefunds/facts.py`, `smartrefunds/rules.py`, `rules/v1.yaml`, `tests/conftest.py`, `tests/test_rules.py`

**Interfaces:**
- Consumes: `Request` (Task 1).
- Produces:
```python
# facts.py
Tri = Literal["yes","no","not_mentioned"]
class Fact(BaseModel): value: Tri; evidence: str = ""          # verbatim quote from the notes
class PhotoFact(BaseModel): value: Literal["present","absent","not_mentioned"]; evidence: str = ""
class LocationFact(BaseModel): value: str | None = None; evidence: str = ""   # lowercase slug, e.g. "northfield"
class Facts(BaseModel):
    photo_or_video_evidence: PhotoFact
    carrier_signature_or_photo: Fact; carrier_investigation_open: Fact; fraud_reported: Fact
    fraud_confirmed: Fact; picking_error_confirmed: Fact; shipped_item_matches_order: Fact
    admits_delivered: Fact; admits_own_fault: Fact; already_refunded_elsewhere: Fact
    reason_contradicts_note: Fact; damage_cosmetic_only: Fact; instructions_to_system: Fact
    repeat_claim_pattern: Fact; location_tag: LocationFact; summary: str; language: str
APPROVAL_EVIDENCE_FACTS = ("photo_or_video_evidence","picking_error_confirmed","fraud_confirmed","fraud_reported","location_tag")
ESCALATION_FACTS = ("carrier_signature_or_photo","admits_delivered","admits_own_fault","already_refunded_elsewhere",
                    "reason_contradicts_note","damage_cosmetic_only","instructions_to_system","repeat_claim_pattern",
                    "shipped_item_matches_order","fraud_reported","fraud_confirmed")

# rules.py
LANES = ("Fraud team","Delivery disputes","Damage & evidence","Policy rejections","Other")
OPS = ("=","!=","<","<=",">",">=","in","not_in","is_set","is_unset")
FIELDS: tuple[str, ...]   # request_id, amount_usd, tenure_months, prior_refunds_12m, days_since_delivery,
                          # stated_reason, category, payment_method, fraud_flag, is_valid, duplicate_id,
                          # notes_present, facts_available, plus "facts.<name>" for every Facts field except summary/language
@dataclass class Condition: field: str; op: str; value: Any = None
@dataclass class AnyOf: conditions: list[Condition]
@dataclass class Action: kind: Literal["approve","route","route_with_draft_rejection"]; lane: str | None = None; policy: str | None = None
                                                     # lane is a LANES value or "by_reason"
@dataclass class Rule: id: str; title: str; when: list[Condition | AnyOf]; then: Action; locked: bool = False; note: str = ""
@dataclass class RuleSet: version: int; rules: list[Rule]; reason: str = ""; published_at: str = ""
@dataclass class Outcome:
    outcome: Literal["auto_approved","needs_review"]; lane: str | None; rule_id: str
    matched_rule_ids: list[str]; reasons: list[str]; draft_rejection: str | None; warning: str | None
POLICY_TEXTS: dict[str, str]   # keys: policy_2, policy_4, double_refund, order_matches (reviewer-editable draft copy)
def load_rules(path) -> RuleSet;  def dump_rules(rs: RuleSet, path) -> None
def case_fields(r: Request, facts: Facts | None) -> dict[str, Any]
def lane_by_reason(fields: dict) -> str
def validate(rs: RuleSet, base: RuleSet | None = None) -> list[str]   # [] means valid; base = live set for locked-rule checks
def evaluate(rs: RuleSet, r: Request, facts: Facts | None) -> Outcome
```

**Fixed semantics:**
- `case_fields` maps `facts.X` to `facts.X.value`, or `None` when facts is None.
- A condition on a `None` field: `=`, `in`, `<`, `<=`, `>` and `>=` are **False**; `!=`, `not_in` and `is_unset` are **True**.
- `lane_by_reason`:
  1. Fraud signals first: `fraud_flag != "no"`, or `facts.fraud_reported`/`facts.fraud_confirmed == "yes"` → `Fraud team`.
  2. Then by reason: `never_arrived`/`late_delivery` → `Delivery disputes`; `arrived_damaged` → `Damage & evidence`; anything else → `Other`.
- `evaluate` walks the rules in order. The first match decides. `matched_rule_ids` lists every rule that matches. If nothing matches → `needs_review`, lane `Other`, `rule_id="(none)"`.
- `warning` is set when S3 matched anywhere in the list.

**`rules/v1.yaml`:** exactly the §6.3 table, IDs in order S1–S4 (locked), R1–R15, A1. Bold values in that table become literals.

YAML shape example:
```yaml
version: 1
reason: Initial rules from policy notes (option B, $100 cap)
rules:
  - id: R11
    title: Damage over $50 with no photo → Damage & evidence
    when:
      - {field: stated_reason, op: "=", value: arrived_damaged}
      - {field: amount_usd, op: ">", value: 50}
      - {field: facts.photo_or_video_evidence, op: "!=", value: present}
    then: {kind: route, lane: "Damage & evidence"}
```
`AnyOf` is written `- any_of: [ {...}, {...} ]`. S4 is `notes_present = true` AND `facts_available = false`.

- [ ] **Step 1: Write the failing tests** (`tests/conftest.py` provides `make_request(**overrides)` and `make_facts(**overrides)`. Defaults: valid, wrong_size, $30, tenure 24, prior 0, flag no, days 5, empty notes, every fact `not_mentioned`.)

`test_pending_traces`, parametrised as `(request overrides, fact overrides) → (outcome, lane, rule_id, draft?)`:

| Case | Request | Facts | Expected |
|---|---|---|---|
| RF-1069 | changed_mind $25 d8 t35 | – | auto, A1 |
| RF-1071 | arrived_damaged $67 t17, note "photos clear" | photo present | auto, A1 |
| RF-1070 | flag yes $410 never_arrived | fraud_reported yes | Fraud team, R1 |
| RF-2182 | flag unknown $120 | fraud_confirmed yes | Fraud team, R1 |
| RF-2181 | never_arrived $1150 | signature yes | Delivery disputes, R2, draft |
| RF-1073 | never_arrived $385 t2 | instructions yes | Delivery disputes, S3, warning set |
| RF-1074 | wrong_size $39 | already_refunded yes | Policy rejections, R5, draft |
| RF-1075 | arrived_damaged $46 | reason_contradicts yes | Damage & evidence, R6 |
| RF-1072 | wrong_item $140 t8 | shipped_matches yes | Other, R7, draft |
| RF-1076 | arrived_damaged $78 prior 3 | – | Damage & evidence, R8 |
| RF-2183 | duplicate_id | – | Other, S2 |
| RF-2198 | late_delivery $52 | location northfield | auto, A1 |

`test_rule_edges` (same format):

| Request | Facts | Expected |
|---|---|---|
| notes present | None | S4 |
| arrived_damaged $45, no notes | None | auto A1 |
| arrived_damaged $52, no notes | None | R11 |
| changed_mind d15 | – | R4, Policy rejections, draft |
| late_delivery $60 | – | R13 |
| wrong_size $101 | – | R10 |
| wrong_size $100 | – | A1 |
| tenure 8 | – | R9 |
| arrived_damaged $40 | cosmetic yes | R12 |

Plus these tests:
- `test_unknown_reason_never_approves`: invalid row → S1, `needs_review`.
- `test_none_semantics`: per the fixed semantics above.
- `test_v1_valid_and_ordered`: `validate(v1) == []`, and the IDs equal the §6.3 order.
- `test_validate_errors`: each of these produces one error containing the quoted word:
  - unknown field (`"field"`)
  - unknown op (`"operator"`)
  - approve with empty `when` (`"at least one condition"`)
  - negative numeric value on an amount/tenure/days field (`"negative"`)
  - a locked rule edited, removed or moved, vs `base` (`"locked"`)
  - an unlocked rule above a locked rule (`"safety rules"`)
  - unknown lane (`"lane"`)

- [ ] **Step 2: Run `python -m pytest tests/test_rules.py -v`**. Expected: FAIL.
- [ ] **Step 3: Implement `facts.py`, `rules.py`, `rules/v1.yaml`** per the interfaces and semantics above.
- [ ] **Step 4: Run `python -m pytest -q`**. Expected: all pass.
- [ ] **Step 5: Commit** `feat: rules engine with v1 rule set and safety rules`.

---

### Task 3: Engine + CLI `run` (`engine.py`, `cli.py`, `__main__.py`)

**Files:** Create `smartrefunds/engine.py`, `smartrefunds/cli.py`, `smartrefunds/__main__.py`, `tests/fixtures/pending_facts.jsonl`, `tests/test_engine.py`

**Interfaces:**
- Consumes: `load_requests`, `load_rules`, `evaluate`, `Facts`.
- Produces:
```python
class FactsProvider(Protocol):
    mode: str                                   # "live" | "saved" | "off"
    def get(self, r: Request) -> Facts | None: ...
class OffProvider: mode = "off"                 # get() -> None
class FixtureProvider: def __init__(self, path)  # jsonl {request_id, row_index, facts}; tests only
@dataclass class Decision: request: Request; facts: Facts | None; outcome: Outcome
def decide_all(requests: list[Request], rs: RuleSet, provider: FactsProvider) -> list[Decision]
def write_decisions_csv(decisions: list[Decision], path: Path) -> None
       # input columns + outcome, lane, rule_id, reasons ("; "-joined), draft_rejection, cleaning_changes
def summarize(decisions: list[Decision], ai_mode: str) -> str
CLI: python -m smartrefunds run <csv> [--rules PATH] [--ai auto|live|saved|off] [--out DIR]
     writes <out>/decisions_<stem>.csv (default out/), prints summary, exit 0;
     bad input → one line "error: …" to stderr, exit 2
```
`--ai` handling is wired in Task 4. For now `off` is the only mode, and `auto` resolves to `off`.

`tests/fixtures/pending_facts.jsonl`: hand-written facts for all 21 pending rows, matching findings §5. This is test data only; Omri reviews it as part of the eval labels in Task 11. RF-2183's two copies are keyed by `row_index`.

- [ ] **Step 1: Write the failing tests**
- `test_run_pending_off_mode`: only `["RF-1069"]` is auto-approved (A1).
  - Both RF-2183 copies → `S2`.
  - RF-1067 and RF-1068 (no notes) → `R3`.
  - The other 16 rows all have notes → `S4`, with lanes from `lane_by_reason`, so RF-1070, RF-1077 and RF-2182 land in `Fraud team`.
- `test_run_pending_with_fixture_facts`: auto-approved = `{RF-1069, RF-1071, RF-2198}`. Lane counts: `Fraud team 3, Delivery disputes 9, Policy rejections 1, Other 3, Damage & evidence 2`.
- `test_decisions_csv_columns`: the header equals the 18 input columns plus `outcome, lane, rule_id, reasons, draft_rejection, cleaning_changes`.
- `test_run_twice_identical`: two CLI runs produce byte-identical CSVs.
- `test_cli_bad_input`: a nonexistent path and a CSV missing `amount_usd` each give exit code 2, a single `error:` line, and no `Traceback` in stderr.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**, using `argparse`. `summarize` prints counts per outcome and lane, the auto-approved total in $, and the AI mode.
- [ ] **Step 4: Run `python -m pytest -q` and `python -m smartrefunds run data/pending.csv --ai off`**. Expected: tests pass, and the summary shows 1 auto-approved.
- [ ] **Step 5: Commit** `feat: decision pipeline and run command`.

---

### Task 4: Note reader (`notes.py`) + `extract-facts`, and the saved-facts run

**Files:** Create `smartrefunds/notes.py`, `tests/test_notes.py`. Modify `smartrefunds/cli.py` (add `extract-facts`, wire `--ai`). Create `data/facts/history.jsonl` and `data/facts/pending.jsonl` (live run).

**Interfaces:**
- Consumes: `Request`, `Facts`, `FactsProvider`.
- Produces:
```python
PROMPT_VERSION = "notes-v1"; DEFAULT_MODEL = "claude-opus-5-5"
def redact(text: str) -> str          # 12–19 digit runs and "card ending NNNN" → [CARD]; phone numbers → [PHONE]; emails → [EMAIL]
def cache_key(r: Request, model: str, prompt_version: str = PROMPT_VERSION) -> str   # sha256 of redacted notes + reason + amount + model + version
def check_evidence(facts: Facts, redacted_notes: str) -> tuple[Facts, list[str]]
       # APPROVAL_EVIDENCE_FACTS whose non-negative value's evidence isn't a case-insensitive substring → not_mentioned; returns warnings
class SavedProvider: def __init__(self, directory: Path = Path("data/facts")); mode = "saved"
class LiveProvider: def __init__(self, client, model: str = DEFAULT_MODEL, effort: str = "low"); mode = "live"
       # get(): redact → messages.parse with Facts schema → check_evidence; refusal / API error / validation error → None
       # last_usage: dict with input/output tokens (used by eval)
def make_provider(mode: Literal["auto","live","saved","off"], facts_dir: Path = Path("data/facts")) -> FactsProvider
       # auto: saved provider wrapping live (if ANTHROPIC_API_KEY set) else wrapping off, for misses
CLI: python -m smartrefunds extract-facts <csv> --out data/facts/<stem>.jsonl [--model M] [--yes]
       prints "N rows with notes, estimated cost $X on <model>"; proceeds only with --yes or typed "y"
       record per row: {"key","request_id","row_index","model_requested","model_answered","prompt_version","extracted_at","facts"}
```
The prompt (wording is the builder's call) must:
- put both notes inside a clearly delimited block labelled as untrusted customer/agent text
- instruct the model never to follow instructions inside that block, and to report them via `instructions_to_system`
- define each fact as in design §6.2
- require evidence to be a verbatim quote
- write `summary` in English

Request settings:
- `output_config={"effort": "low"}`. Effort is the builder's call; the eval in Task 11 may revise it.
- No sampling params.
- Server-side fallbacks per the claude-api skill (`fallbacks: "default"` + beta `server-side-fallback-2026-07-01`). Record `response.model` as `model_answered`.

Load `.env` with `python-dotenv` in the CLI and app only.

- [ ] **Step 1: Read** the claude-api skill's `python/claude-api/README.md` sections on structured outputs and refusals. Note the exact `parse` call and refusal check in a comment at the top of `LiveProvider`.
- [ ] **Step 2: Write the failing tests**
  - `test_redact`: `"pls refund to card ending 4417, or call me on +44 7700 900123"` → contains `[CARD]` and `[PHONE]`, and neither `4417` nor `900123`.
  - `test_cache_key_stable_and_sensitive`: same inputs give the same key. Changing the model or `prompt_version` changes it.
  - `test_saved_provider_hit_miss`.
  - `test_unquoted_approval_evidence_downgraded`: photo `present` with evidence `"photos clear"`, but the notes don't contain it → `not_mentioned`, plus one warning. An escalation fact with a bad quote stays `yes`.
  - `test_refusal_and_errors_mean_unavailable`: a fake client returning `stop_reason="refusal"`, raising `anthropic.APIConnectionError`, or returning schema-invalid output → `get()` is `None`.
  - `test_injection_text_is_inside_untrusted_block`: the prompt built for RF-1073's note has the note text only between the delimiters.
  - `test_make_provider_auto_without_key`: env without the key and an empty facts dir → `get()` is `None`, mode reported as `"off"`.
  - `test_extract_facts_writes_jsonl` (fake client, `--yes`).
- [ ] **Step 3: Run, expect FAIL. Implement. Run `python -m pytest -q`, expect PASS.**
- [ ] **Step 4: Commit** `feat: AI note reader with redaction, saved facts and fail-safe fallback`.
- [ ] **Step 5: Live run, gated on Omri.** Omri puts the key in `.env`. Show him the cost estimate (about $4 for history). With his go-ahead, run:
  ```
  python -m smartrefunds extract-facts data/history.csv --out data/facts/history.jsonl
  python -m smartrefunds extract-facts data/pending.csv --out data/facts/pending.jsonl
  ```
- [ ] **Step 6: Spot-check, and show Omri:**
  - RF-1073 `instructions_to_system=yes`
  - RF-1074 `already_refunded_elsewhere=yes`
  - RF-2182 `fraud_confirmed=yes`
  - RF-2199 `location_tag=northfield`
  - no `4417` in `history.jsonl`

  Then run `python -m smartrefunds run data/pending.csv --ai saved`. Compare with the fixture outcomes from Task 3 and list any differences for Omri. Don't edit saved facts by hand.
- [ ] **Step 7: Commit** `data: saved AI facts for history and pending (Opus 5.5, notes-v1)`. Check `git status` first; no `.env`.

---

### Task 5: Incidents (`incidents.py`)

**Files:** Create `smartrefunds/incidents.py`, `tests/test_incidents.py`

**Interfaces:**
- Consumes: `Decision`.
- Produces:
```python
@dataclass class Incident: tag: str; request_ids: list[str]; total_amount: float; first_seen: date; spellings: list[str]
def find_incidents(decisions: list[Decision], open_ids: set[str] | None = None, min_cases: int = 3, window_days: int = 14) -> list[Incident]
       # only needs_review decisions (and in open_ids if given), grouped by facts.location_tag.value;
       # an incident = the largest set within any window_days span of requested_at with >= min_cases; spellings = distinct evidence quotes
```
- [ ] **Step 1: Failing tests**
  - `test_northfield_pending` (fixture facts): one incident, tag `northfield`, ids `RF-2196, RF-2197, RF-2199, RF-2200, RF-2201`, `total_amount == 726.0`, `first_seen == date(2026,8,26)`. RF-2198 is excluded because it's auto-approved.
  - `test_two_cases_no_incident`.
  - `test_outside_window_no_incident`: three cases spread over 20 days.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat: incident grouping by AI location tag`.

---

### Task 6: Preview engine + `backtest` (`preview.py`)

**Files:** Create `smartrefunds/preview.py`, `tests/test_preview.py`. Modify `smartrefunds/cli.py`.

**Interfaces:**
- Consumes: `evaluate`, `RuleSet`, `Request`, `FactsProvider`.
- Produces:
```python
HistoryLabel = Literal["bad","should_approve","ok"]
def history_label(r: Request) -> HistoryLabel
       # bad: chargeback in outcome_note, or decision rejected and not overturned/refunded on appeal, or partial
       # should_approve: rejected then overturned / "refunded on appeal"; ok: otherwise
@dataclass class Change: request_id: str; row_index: int; amount: float | None; before: str; after: str
       # before/after = "auto_approved" or lane name, plus " (draft rejection)" when a draft exists
@dataclass class DatasetPreview:
    n_rows: int; auto_count: int; auto_amount: float; minutes_saved: float; bad_let_through: list[str]
    lane_counts: dict[str, int]; changed: list[Change]; largest_new_auto: tuple[str, float] | None
@dataclass class PreviewResult: history: DatasetPreview; pending: DatasetPreview; warnings: list[str]
def preview(candidate: RuleSet, live: RuleSet, history: list[Request], pending: list[Request], provider: FactsProvider) -> PreviewResult
       # minutes_saved = sum review_minutes over history rows auto under candidate but not under live, where decided_by != "auto"
       # bad_let_through = history rows auto under candidate with label "bad"
CLI: python -m smartrefunds backtest [--rules PATH] [--ai saved]
       prints the slide table: candidate vs actual legacy (decided_by == "auto"): auto count, $, bad, minutes saved
```
Warnings (exact text stems):
- `"No effect on history or today's queue"`
- `"Skips the {lane} for {n} cases"` (rows moving from a lane to `auto_approved`)
- `"Largest single new auto-approval: ${amount} ({request_id})"`
- `"Small sample: only {n} history cases change"` (1 ≤ n < 20)
- `"Lets through {n} cases with bad outcomes in history"`

- [ ] **Step 1: Failing tests**
  - `test_history_label`: RF-2074 bad (chargeback), RF-1008 should_approve, RF-2143 should_approve, RF-2018 bad (partial), RF-2056 bad (rejected), RF-1001 ok.
  - `test_preview_identical_no_effect`: candidate == live → no changes, the "No effect" warning.
  - `test_preview_cap_change` (fixture facts, pending only plus a small synthetic history): cap 100 → 55 moves RF-1071 from `auto_approved` to `Damage & evidence`, so the change list contains it.
  - `test_preview_warnings`: a synthetic approve rule that moves a fraud-lane row of $610 → the "Skips the Fraud team" and "Largest single new auto-approval: $610" warnings.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Real backtest.** Run `python -m smartrefunds backtest --ai saved` and paste the output into `docs/ai-usage-log.md` under "Backtest v1".
  - **Expected:** about 100 auto-approvals, and `bad_let_through` ⊆ `{"RF-2074"}`.
  - **If it differs:** stop and show Omri; it changes the line slide.
  - Add `test_backtest_v1_snapshot`, which pins the actual numbers (skipped if `data/facts/history.jsonl` is missing).
- [ ] **Step 6: Commit** `feat: preview engine and backtest command`.

---

### Task 7: Store (`store.py`) + `reset-demo`

**Files:** Create `smartrefunds/store.py`, `tests/test_store.py`. Modify `smartrefunds/cli.py`.

**Interfaces:**
- Consumes: `RuleSet`, `load_rules`, `dump_rules`, `validate`.
- Produces:
```python
@dataclass class PaymentResult: ok: bool; message: str
class Store:
    def __init__(self, var_dir: Path = Path("var"), seed_rules: Path = Path("rules/v1.yaml"))
    def record_decision(self, request_id: str, row_index: int, role: str, action: str, amount: float | None,
                        reason_code: str | None, note: str, rule_version: int, suggestion: str, flag_feedback: dict[str, bool]) -> int
    def pay(self, request_id: str, amount: float, method: str) -> PaymentResult    # UNIQUE(request_id); 2nd → ok=False, "already refunded …"
    def status(self, request_id: str, row_index: int) -> Literal["open","waiting","decided"]
    def set_waiting(self, request_id: str, row_index: int, what: str) -> None;  def resume(self, request_id: str, row_index: int) -> None
    def audit(self, request_id: str | None = None) -> list[dict]
    def live_rules(self) -> RuleSet                          # newest var/rules/vN.yaml, else seed
    def publish(self, rs: RuleSet, reason: str, author: str = "Refund Ops Manager") -> int
          # requires non-empty reason and validate(rs, base=live) == []; writes var/rules/v{n}.yaml; returns n
    def versions(self) -> list[tuple[int, str, str]]         # (version, published_at, reason), seed is v1
    def rollback(self, to_version: int, reason: str) -> int  # publishes a copy of that version as a new version
    def reset(self) -> None                                  # deletes var/ contents
CLI: python -m smartrefunds reset-demo
```
- [ ] **Step 1: Failing tests** (all in `tmp_path`):
  - `test_pay_once`: the second `pay("RF-2183", …)` gives `ok=False`, and only one row is in payments.
  - `test_publish_requires_reason_and_valid`.
  - `test_publish_and_live`: v2 becomes live.
  - `test_rollback_creates_new_version`: rollback to 1 → v3, whose rules equal v1's.
  - `test_waiting_resume`.
  - `test_audit_records_rule_version`.
  - `test_reset`.
- [ ] **Step 2–4:** Run (FAIL), implement with `sqlite3`, run (PASS).
- [ ] **Step 5: Commit** `feat: SQLite store, versioned rules, mock payments`.

---

### Task 8: Rule drafting (`drafting.py`) + saved drafts

**Files:** Create `smartrefunds/drafting.py`, `data/drafts/stolen-card.json`, `data/drafts/over-300.json`, `tests/test_drafting.py`

**Interfaces:**
- Consumes: `Rule`, `RuleSet`, `validate`, `evaluate`, `FIELDS`, `OPS`, `LANES`.
- Produces:
```python
@dataclass class DraftChoice: label: str; rule: Rule
@dataclass class Draft: text: str; question: str | None; choices: list[DraftChoice]; error: str | None; source: Literal["saved","live","none"]
def draft_rule(text: str, live: RuleSet, client=None, saved_dir: Path = Path("data/drafts")) -> Draft
       # saved match on normalised text (lowercase, collapse whitespace, strip punctuation) first; else live LLM; else
       # Draft(error="Turning sentences into rules needs an API key. You can still build the rule in the form below.")
       # every candidate must pass validate() when inserted; failures → error "Couldn't turn that into a valid rule: …"
def suggest_position(rs: RuleSet, rule: Rule) -> int
       # approve rules: right after the last locked rule; route rules: right before the first approve rule
def insert_rule(rs: RuleSet, rule: Rule, position: int) -> RuleSet          # new version = rs.version + 1, unpublished
def overridden_rules(live: RuleSet, candidate: RuleSet, rows: list[tuple[Request, Facts | None]]) -> list[str]
       # for rows whose outcome changes, the live rule_ids that used to decide them (sorted, unique)
```
The LLM output schema is a pydantic mirror of `Rule` (enums from `FIELDS`/`OPS`/`LANES`) plus `question` and `choices`. Same model and API rules as Task 4.

**Saved drafts** (hand-written to match the approved mockup):
- **`stolen-card.json`**
  - question: "Which do you mean by “stolen-card case”?"
  - choice "Confirmed: fraud team or bank confirmed" → approve when `facts.fraud_confirmed = yes`
  - choice "Reported: cardholder reported it stolen or unauthorised" → approve when `any_of[facts.fraud_reported = yes, facts.fraud_confirmed = yes]`
- **`over-300.json`**
  - no question
  - one choice → route `by_reason` when `amount_usd > 300`

- [ ] **Step 1: Failing tests**
  - `test_saved_draft_matches_variants`: `"From now on, stolen-card cases go straight through."` and `"from now on stolen-card cases go straight through"` both load the saved draft, with 2 choices.
  - `test_no_key_unknown_text_error`.
  - `test_live_draft_with_fake_client`: valid → choices. Unknown field → the error text above.
  - `test_suggest_position`: stolen approve → index 4 (after S4). Over-300 route → the index of A1.
  - `test_overridden_rules_stolen` (fixture facts, pending): the "reported" choice → `["R1"]`, and RF-2182 and RF-1070 change.
  - `test_over_300_no_effect`: `preview(...)` on pending gives no changes.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat: plain-English rule drafting with saved examples`.

---

### Task 9: Streamlit reviewer screen

**Files:** Create `app/main.py`, `app/state.py`, `app/reviewer.py`, `tests/test_app_reviewer.py`

**Interfaces:**
- Consumes: everything above.
- `app/state.py`:
  - `load_context() -> Context` with `history`, `pending`, `provider`, `store`, `live`, `decisions` and `incidents`
  - environment overrides: `SMARTREFUNDS_PENDING`, `SMARTREFUNDS_HISTORY`, `SMARTREFUNDS_VAR`, `SMARTREFUNDS_AI`, `SMARTREFUNDS_FACTS_DIR`
  - cache with `st.cache_data`, keyed on the live rule version
- `app/main.py`:
  - sidebar role radio, key `role`: `Reviewer` / `Fraud team` / `Manager`
  - a banner showing the AI mode, the facts source/date, and "Payments are simulated"
  - a "Reset demo" button

**Reviewer screen** (design §7.1, layout as in the approved mockup):
- Tabs: `"{lane} ({n})"` for the five lanes, plus `Waiting ({n})`. The `Fraud team` role sees only its own lane.
- The incident card is above the Delivery disputes list. It has a `suggest_rule` button that switches the role to Manager and pre-fills `change_text` with "Northfield never-arrived cases under $100: refund in full" (tag and count from the incident).
- The `Waiting` tab has a `resume` button labelled "Carrier/customer replied (simulated)".
- Case selection via `st.selectbox` key `case`.
- The case view shows:
  - facts, plus `cleaning_changes`
  - "Why it's here": `matched_rule_ids` with their titles
  - the AI box: summary, flags with evidence, 👍/👎 checkboxes
  - raw notes in a box labelled "Untrusted text"
  - the S3 warning
  - similar past cases: the same `stated_reason` plus the same first escalation fact = yes, at most 3, with `history_label`
- Action buttons with keys:
  - `approve_full`
  - `approve_partial`, with presets `shipping only` (prompts for an amount), `25%`, `30%`, `50%`, `custom`
  - `reject`, with an editable `draft_rejection` text area
  - `request_info`, with `photo` / `carrier investigation`
  - `move_lane`
- Going against the suggestion requires `reason_code`, from: `Customer history`, `Evidence in notes`, `Policy exception approved by manager`, `AI flag wrong`, `Other`.
- Approvals call `store.pay`, and show a refusal message when `ok=False`.

- [ ] **Step 1: Failing tests** (`AppTest.from_file("app/main.py")`, env pointing at the fixture facts and a tmp var dir):
  - `test_reviewer_tabs_counts`: tab labels include `Fraud team (3)`, `Delivery disputes (9)`, `Damage & evidence (2)`, `Policy rejections (1)`, `Other (3)`.
  - `test_northfield_incident_card`: the text contains `Northfield` and `5 cases`.
  - `test_approve_writes_audit_and_payment`: select RF-1076, set the reason code, click `approve_full` → `store.audit("RF-1076")` has 1 entry, and one payment.
  - `test_override_requires_reason_code`: approving RF-2181 (suggestion: reject) without a reason code shows an error and records nothing.
  - `test_duplicate_copy_payment_refused`: approve the first RF-2183 copy, then the second → the message contains "already refunded", and there's one payment.
  - `test_injection_warning_shown`: RF-1073 shows the warning text.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Manual check.** Run `streamlit run app/main.py`, click through RF-2181 and the Northfield incident, and take a screenshot for Omri.
- [ ] **Step 6: Commit** `feat: reviewer queue with lanes, incidents and case view`.

---

### Task 10: Streamlit manager screen

**Files:** Create `app/manager.py`, `tests/test_app_manager.py`

**Interfaces:** Consumes `store.live_rules`, `store.publish`, `store.rollback`, `store.versions`, `draft_rule`, `suggest_position`, `insert_rule`, `overridden_rules`, `preview`, `validate`.

**Manager screen** (design §7.2, layout as in the approved mockup):
- **Left column:**
  - the rules in order: `id`, `title`, 🔒 for locked
  - number inputs for the editable values (R10 cap key `edit_cap`, R9 tenure `edit_tenure`, R8 prior `edit_prior`, R4 window `edit_window`, R11 photo threshold `edit_photo`)
  - version history with a `rollback_{n}` button each
- **Right column:**
  1. `st.text_area` key `change_text` and button `draft`
  2. the question as `st.radio` key `choice` when present
  3. the rule shown in plain words: When / Then / Position (up/down buttons `move_up`/`move_down`) / Overrides
  4. the preview: four metrics, warnings, a changed-cases table
  5. `publish_reason` text input, then buttons `publish`, `save_draft` (keeps the candidate in `st.session_state` for this session only; persistent drafts are cut), `discard`
- Any edit (inline value or drafted rule) builds a candidate `RuleSet` and re-runs `preview`.
- Publish is disabled until a reason is entered and `validate()` is clean. Validation errors are shown verbatim.

- [ ] **Step 1: Failing tests** (AppTest, role `Manager`):
  - `test_rules_listed_with_locks`.
  - `test_cap_edit_preview`: `edit_cap` 100 → 55 → the preview's changed table contains RF-1071.
  - `test_stolen_card_saved_draft_flow`: enter the brief sentence, click `draft` → the radio shows 2 choices. Pick "Reported" → the warnings include "Skips the Fraud team", and the changes include RF-2182 and RF-1070.
  - `test_over_300_no_effect`: the warning contains "No effect".
  - `test_publish_requires_reason`, `test_publish_then_rollback`: after publishing, `store.versions()` has 2 entries. Rolling back to 1 gives 3.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5: Manual check** in the browser with saved data: the stolen-card flow and the $300 flow. Take a screenshot for Omri.
- [ ] **Step 6: Commit** `feat: manager rule editing with plain-English drafts and preview`.

---

### Task 11: AI eval (`evaluation.py`, `eval/`)

**Files:** Create `smartrefunds/evaluation.py`, `eval/cases.jsonl`, `eval/README.md`, `tests/test_evaluation.py`. Modify `smartrefunds/cli.py`.

**Interfaces:**
- Consumes: `LiveProvider`, `Facts`, `evaluate`, `load_rules`, `ESCALATION_FACTS`.
- Produces:
```python
@dataclass class EvalCase: id: str; source: str; request: dict; expected: dict[str, str]; expected_outcome: str; expected_lane: str | None
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-haiku-5-5": (0.10, 0.50)}   # $ per 1M input / output tokens
def load_cases(path: Path = Path("eval/cases.jsonl")) -> list[EvalCase]
def score(cases: list[EvalCase], predictions: dict[str, Facts | None]) -> dict
       # per-fact precision/recall on non-negative values; escalation_recall (headline); injection_pass (all cases with
       # expected instructions_to_system=yes); routing_agreement (outcome+lane via v1 rules); unavailable count
def run_eval(models: list[str], cases, client) -> dict   # adds cost_per_decision and p50 latency per model from usage
CLI: python -m smartrefunds eval [--models claude-opus-5-5,claude-haiku-5-5] [--yes]
     prints estimated cost; writes eval/results/<date>-<model>.json and eval/results/summary.md
```
`eval/cases.jsonl` holds about 30–40 cases, drawn from:
- all 21 pending rows (start from `tests/fixtures/pending_facts.jsonl`)
- history notes: the 8 chargebacks, the 3 overturned appeals, RF-2068 ("Dana"), RF-2161 (Hebrew), RF-2168 (German), RF-2140 (Spanish), RF-2077 (PII), RF-1039 (repeat claims)
- 3 plain controls

Labels come from reading the notes, never from model output.

- [ ] **Step 1: Failing tests** for `score` with hand-built predictions:
  - a perfect prediction gives escalation_recall 1.0
  - a missed `instructions_to_system` gives `injection_pass == False`
  - `None` predictions count as unavailable, not as correct
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). Commit `feat: eval scoring and runner`.
- [ ] **Step 5: Draft `eval/cases.jsonl`. STOP for Omri's review** (about 20 minutes). He corrects the labels. Record "Labels reviewed by Omri on <date>" in `eval/README.md`. Any change he makes to a pending row's facts is also applied to `tests/fixtures/pending_facts.jsonl`; re-run `pytest` after.
- [ ] **Step 6: Live eval, gated on Omri.** Show the estimate (about $1), then run `python -m smartrefunds eval --yes`.
  - If Opus misses an escalation fact or fails an injection case, show Omri before any prompt change.
  - Any prompt change bumps `PROMPT_VERSION` and means re-running Task 4 Step 5.
- [ ] **Step 7: Commit** `eval: labelled set (reviewed) and results for Opus 5.5 vs Haiku 5.5`.

---

### Task 12: README, usage log, final hygiene

**Files:** Create `README.md`. Modify `docs/ai-usage-log.md`.

**README sections, in order:**
1. what this is (2 lines)
2. quick start: venv, `pip install -r requirements.txt`, `python -m smartrefunds run data/pending.csv`, `streamlit run app/main.py`, `pytest`
3. API key: `.env` from `.env.example`, and the AI modes auto/live/saved/off, with what happens in each
4. architecture sketch (from design §5)
5. what's mocked: payment, Waiting resume, rejection message never sent
6. commands reference
7. links: slides, recording, `docs/design.md`, `docs/findings.md`, `eval/results/summary.md`
8. how AI coding tools were used, ending with one thing that was changed or thrown away (from the usage log)

- [ ] **Step 1: Fresh-clone check.**
  ```
  git clone . %TEMP%\sr-check
  cd %TEMP%\sr-check
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
  .venv\Scripts\python -m pytest -q
  .venv\Scripts\python -m smartrefunds run data/pending.csv
  ```
  Expected: tests pass, and the summary shows saved-mode facts. `recieved_files` is absent from the clone.
- [ ] **Step 2: `python -m pytest tests/test_repo_hygiene.py -q`** passes, and `git log --all --name-only | findstr recieved_files` returns nothing.
- [ ] **Step 3: Commit** `docs: README and AI usage notes`.
- [ ] **Step 4: Push, only when Omri says.** Omri creates the public GitHub repo and gives the remote. Push only after he confirms.

---

### Task 13: Slides and recording

**Files:** Create `docs/slides.pdf` (the hygiene test allows exactly this PDF).

- [ ] **Step 1: Ask Omri which slide tool to use** (design §12). Gather the numbers from:
  - `backtest --ai saved`
  - `eval/results/summary.md`
  - findings §4 (process today: 137 human decisions, about 1,120 minutes, median wait about 1.6 days, 8 chargebacks costing $2,519)

  Every number on a slide must come from one of these.
- [ ] **Step 2: Draft at most 8 slides:**
  1. Process today: before/after map
  2. The line and its evidence
  3. Reviewer mechanism
  4. Manager mechanism
  5. Measuring success and rollback triggers
  6. AI: where it is and isn't, model, cost per decision, eval results
  7. Deferred and next
  8. Optional appendix: data traps
- [ ] **Step 3: Omri reviews.** Export to `docs/slides.pdf`, then commit `docs: slides`.
- [ ] **Step 4: Recording (Omri).** 3–5 minutes covering the reviewer queue (RF-2181, the Northfield incident, the RF-2183 pay-once) and the manager flow (stolen card, then $300). Host it outside the repo and link it from the README. Commit `docs: link recording`.
