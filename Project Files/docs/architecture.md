# Architecture

## The shape of the thing

```
                    data/cases.csv
                  33 cases, ground truth
                  in expected_fault (never
                    shown to the model)
                            │
            ┌───────────────┴───────────────┐
            │                               │
            ▼                               ▼
  ┌───────────────────┐          ┌────────────────────────┐
  │  STAGE 1          │          │  STAGE 2               │
  │  rule_checker.py  │          │  ai_diagnose.py        │
  │                   │          │                        │
  │  34 checks        │          │  prompts/*.md           │
  │  pure Python      │          │  recorded | anthropic  │
  │  regex over the   │          │                        │
  │  transcript       │          │  structured JSON out   │
  │                   │          │           │            │
  │  cannot reason    │          │           ▼            │
  │  cannot           │          │  ┌──────────────────┐  │
  │  hallucinate      │          │  │ schema.py        │  │
  └─────────┬─────────┘          │  │ 9 fields typed   │  │
            │                    │  │ evidence must be │  │
            │                    │  │ VERBATIM         │  │
            │                    │  └────────┬─────────┘  │
            │                    └───────────┼────────────┘
            │                                │
            └────────────┬───────────────────┘
                         ▼
             ┌───────────────────────┐
             │  STAGE 3              │
             │  review.py            │
             │                       │
             │  transcript  →        │
             │  checker     →        │   ordering is deliberate
             │  AI          →        │
             │                       │
             │  accept/edit/reject   │
             │  → review_log.csv     │
             └───────────┬───────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
   ┌────────────────────┐  ┌──────────────────┐
   │  STAGE 4a          │  │  STAGE 4b        │
   │  can_apply() GATE  │  │  dashboard.py    │
   │                    │  │                  │
   │  no review → REFUSE│  │  pandas join     │
   │  Rejected → REFUSE │  │  matplotlib      │
   │  Accepted → allow  │  │  Jinja2 → HTML   │
   │  Edited   → allow, │  │  + summary CSV   │
   │   reviewer's cause │  │                  │
   │   supersedes       │  │  catch rate      │
   │                    │  │  recomputed live │
   │  fix steps printed │  │                  │
   │  for a HUMAN to run│  └──────────────────┘
   └────────────────────┘
```

Nothing in this system touches a device. Stage 4a prints commands for a person to run.

---

## Why two analysis stages instead of one

The two stages fail in opposite directions, and that is the entire design rationale.

**The rule checker cannot reason.** It has no idea what the network is *for*. On NS-002 a
switch port is in VLAN 30 when the design says VLAN 20 — every line of the config is
well-formed and internally consistent, and no regex can know the intent was violated. It
misses the case entirely.

**The rule checker also cannot hallucinate.** Every finding it emits carries the verbatim
transcript line that produced it. When it says `route_mask_suspicious`, there is a /26 in
the routing table. It is never confidently wrong, because it is never confident.

**The AI can reason.** It reads a symptom, correlates across four different `show` outputs,
and explains the mechanism in prose a student can learn from. It proposes the next command
to run. It notices secondary faults.

**The AI can also invent.** On NS-021 it quoted a line containing the fault and then
concluded the configuration was fine, inventing powered-off hosts and host firewalls to
explain a reachability boundary that falls exactly at `.63`. Measured across 33 cases: 21
diagnoses accepted unedited, 7 needing correction, 5 simply wrong.

Neither is sufficient. Running both and showing a human where they disagree is more
informative than either alone — and where they disagree, one of them is wrong, which is a
much easier thing to hand a reviewer than a single confident answer.

Measured outcome:

| | Count |
|---|---:|
| Checker fired **and** AI accepted | 21 |
| Checker fired, AI needed edits | 7 |
| Checker fired, AI wrong | 4 |
| Neither caught it (human only) | 1 |

---

## Stage 1 — `rule_checker.py`

Registry pattern. Each check is a generator decorated into a module-level list:

```python
@check("route_mask_suspicious", "Medium")
def check_route_mask_suspicious(case) -> Iterator[Finding]:
    ...
    yield Finding(check_id=..., severity=..., message=..., evidence=s["line"], ...)
```

Adding a check is one function; nothing else changes. `run_checks()` wraps each in
try/except, so a parser bug in one check cannot take down the run — a crashed check reports
nothing rather than crashing the tool.

`Finding.evidence` is required to be the **matched transcript line**, not a description of
it. A finding that cannot quote its own trigger is an assertion, and the test suite asserts
this property across the whole corpus.

Shared parsers do the transcript work: `interface_table`, `routes`, `device_blocks`,
`ping_results`, `host_config`, `_acl_blocks`, `running_config_addresses`. The checks
themselves stay short enough to read.

**Three of these parsers exist in their current form because of a bug:**

- `ping_results` originally matched the success pattern anywhere in a result block. The text
  `Reply from 192.168.10.1: Destination host unreachable.` contains `Reply from`, so a
  *failed* ping was recorded as a success — silently disabling every connectivity check
  downstream. Now failure markers are matched separately and win over success markers, and
  the result window is truncated at the next ping command.
- `running_config_addresses` + `_canonical_intf` exist because an interface appearing in both
  `show ip interface brief` and `show running-config` was counted as two devices sharing an
  address (false `duplicate_ip` on NS-009).
- `_nat_role_listed` exists because `show ip nat statistics` prints interface roles two
  different ways — inline and on a following indented line — and the guard has been wrong in
  both directions. Its docstring records both failures.

Each has a named regression test.

---

## Stage 2 — `ai_diagnose.py` + `schema.py`

**Provider adapter, two implementations behind one interface:**

- `RecordedProvider` replays `outputs/ai_responses.json`. Default. Works offline, so a demo
  cannot fail from a connectivity problem, and the committed responses are reproducible by
  anyone.
- `AnthropicProvider` calls `claude-opus-5` over `https://api.anthropic.com/v1/messages`
  using `requests` and `ANTHROPIC_API_KEY`. No SDK dependency.

`load_system_prompt()` extracts the prompt from the fenced block in
`prompts/diagnose_prompt.md`, so the prompt exists in exactly one place — the documentation
and the executed prompt cannot drift apart.

`build_user_message()` assembles symptom + topology + transcript. **It never includes
`expected_fault`.**

**Two-part validation, both in `schema.py`:**

1. `validate_diagnosis()` — nine required fields with correct types, `osi_layer ∈ {1,2,3,4,7}`,
   `confidence ∈ {high,medium,low}`, non-empty `evidence` and `fix_steps`, and a refusal to
   accept `requires_human_review: false`. It handles the `bool`-is-a-subclass-of-`int` trap,
   so `osi_layer: true` is rejected rather than read as layer 1.
2. `validate_evidence()` — every evidence string is whitespace-normalised and checked for
   containment in the case transcript. Column alignment is not meaning, so a reflowed quote
   still passes; a *paraphrase* does not.

Malformed output is retried once and then surfaced as a failure. It is never silently
patched into shape.

Current state: 33/33 schema-valid, and exactly one case (NS-014) flagged for unverifiable
evidence. Evidence integrity rate **97.0%**.

---

## Stage 3 — `review.py`

`render_case()` presents the case in a deliberate order: **raw transcript, then deterministic
findings, then the AI's conclusion.** A reviewer who reads a confident summary first reads
the evidence looking for confirmation of it.

It also actively surfaces trouble:
- evidence lines that failed the verbatim check are marked `<-- NOT FOUND IN TRANSCRIPT`
- a `!!!` block appears when any evidence is unverifiable
- when the checker's findings don't align with the AI's concept tag, a `NOTE` says so —
  "one of them is wrong"

Decisions append to `outputs/review_log.csv`. Append-only: a re-review supersedes without
erasing what was decided the first time.

---

## Stage 4a — the gate

```python
allowed, reason = review.can_apply(case_id)
if not allowed:
    print("REFUSED -- no fix steps will be emitted")
    print(reason)
    return 2
```

`can_apply()` returns `(False, reason)` for a case with no log entry and for a case reviewed
as Rejected, and `(True, reason)` for Accepted or Edited. The reason is printed either way —
a refusal that doesn't explain itself teaches the operator nothing.

Where the review was `Edited`, `apply` prints the reviewer's corrected root cause above the
AI's and states which supersedes.

**This is the difference between documenting human oversight and enforcing it.** There is no
flag to override it and no code path from a diagnosis to a fix that avoids it.

---

## Stage 4b — `dashboard.py`

pandas joins `cases.csv`, `review_log.csv`, and a **live** `run_checks()` call — not a cached
report. The catch rate on the dashboard is recomputed on every build, so it cannot drift
away from what the code does.

Five matplotlib PNGs plus a Jinja2 HTML report and `dashboard_summary.csv`.

Chart decisions follow the `dataviz` procedure rather than matplotlib defaults, and the
palette was validated with its checker rather than eyeballed:

- Nominal categories (concept tag, failure mode) use **one colour for the whole series**. A
  value-ramp on unordered categories double-encodes bar length as hue.
- Ordered categories (OSI layer, severity, degree of human intervention) use a **one-hue
  ordinal blue ramp**, validated for monotone lightness, adjacent lightness gaps ≥ 0.06, and
  a light end that clears the surface.
- The status palette is deliberately *not* used for severity: green↔red measure ΔE 4.1 under
  deuteranopia, which fails as a categorical set.
- Every value is direct-labelled and every chart has a table twin, so colour never carries
  meaning alone.

---

## What is deliberately absent

- **No auto-remediation.** Not a missing feature — the point.
- **No confidence threshold that skips review.** A high-confidence wrong answer is the most
  dangerous output in the corpus (NS-014 rated itself `high` on reconstructed evidence).
- **No fine-tuning.** The corpus is 33 cases. Fine-tuning on 33 examples would produce a
  model that memorised them.
- **No `.pkt` file.** The Cisco brief requires one only for Networking-domain students; this
  is an AI-domain submission (all three certificates are AI-track, and the problem
  statement's MAIN COURSE is Modern AI). Lab transcripts ship inside `cases.csv`, which is
  what the brief's "Packet Tracer **or lab scenarios**" wording permits.
