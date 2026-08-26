# NetSage AI

**AI-assisted network fault diagnosis with enforced human review.**

Cisco–AICTE Virtual Internship Program 2026 · Problem Statement 2, *Applied AI + Network
Troubleshooting*
Samarth Mehrotra · Enrollment 2410030934 · B.Tech CSE (2024–28), Semester 5
IILM University, Greater Noida

---

## What it does

Give it a network fault report and the `show`-command output from the affected devices. It
returns a diagnosis: root cause, OSI layer, the verbatim evidence it relied on, the next
command to run, and the fix steps.

Then it stops. **No fix it suggests can be applied until a human has reviewed it** — and that
is enforced in code, not written in a policy document:

```bash
python src/netsage.py apply --case NS-031
```

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
REFUSED -- no fix steps will be emitted for NS-031.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
NS-031 was REJECTED on review by Samarth Mehrotra. The AI diagnosis was found
incorrect, so its fix steps must not be applied.
Reviewer note: Refuted by a line the diagnosis itself quoted: 'State Assoc' means
the WPA2 four-way handshake already completed...
```

There is no override flag, and no code path from a diagnosis to a fix that avoids
`review.can_apply()`.

---

## Quickstart

```bash
pip install -r requirements.txt
```

```bash
python src/netsage.py all
```

That runs the deterministic checks over all 33 cases, replays the recorded AI diagnoses,
prints the review summary, and builds the dashboard. Then open
`outputs/dashboard.html`.

Individual commands:

```bash
python src/netsage.py check --all              # 34 deterministic checks, 33 cases
python src/netsage.py check --case NS-021      # one case, verbose
python src/netsage.py checks                   # list every check id
python src/netsage.py diagnose --case NS-014   # AI diagnosis + schema + evidence report
python src/netsage.py review --case NS-031     # interactive human review
python src/netsage.py review --summary         # decision counts and failure modes
python src/netsage.py apply --case NS-007      # gated fix output
python src/netsage.py dashboard                # charts, HTML, summary CSV
python -m pytest tests/ -q                     # 65 tests
```

No network access is needed. The default AI provider replays committed responses.

---

## Measured results

Every number here is produced by code in this repository and reproducible with the commands
above. None of them is rounded up.

| Metric | Value | Where it comes from |
|---|---|---|
| Cases | **33** | `data/cases.csv` — brief requires 30 |
| Fault families | **9** | VLAN, Gateway, DHCP, DNS, Routing, ACL, NAT, Wireless, Physical |
| OSI layers covered | **1, 2, 3, 4, 7** | |
| Deterministic checks | **34** | `src/rule_checker.py` |
| Rule-checker catch rate | **32 / 33 = 97.0%** | `netsage.py check --all` |
| AI diagnoses, schema-valid | **33 / 33** | `netsage.py diagnose --all` |
| Evidence integrity | **97.0%** (1 case flagged) | `schema.validate_evidence` |
| Human agreement rate | **63.6%** (21 accepted) | `netsage.py review --summary` |
| Corrections logged | **12** | brief requires 5 |
| Tests | **65 passing** | `pytest tests/ -q` |

### On the 97% catch rate

It means a deterministic check fired on a genuine fault in 32 of 33 cases. It does **not**
mean the check named the fault perfectly.

The one miss is **NS-002**, and it is uncovered on purpose. A switch port is in VLAN 30 when
the design calls for VLAN 20. Every line of the configuration is well-formed and internally
consistent — only the *intent* is violated, and no regex can know a port's intended VLAN. The
AI got it wrong too, blaming the DHCP server because `DHCP Server` was the most prominent
line in the output. Both automated layers failed and the human caught it.

`tests/test_rule_checker.py` asserts that NS-002 stays uncovered, so if a future check starts
firing on it, the test fails and this README gets corrected rather than quietly going stale.

### On the 63.6% agreement rate

This is the number a reader should be most suspicious of, so here is how it was produced.

The 33 recorded diagnoses in `outputs/ai_responses.json` are genuine output from Claude Opus
5 reading each case's symptom, topology note and transcript against
`prompts/diagnose_prompt.md`. The `expected_fault` column was never in the prompt. **Twelve
of them are wrong, and they are committed as-is.** Deleting a wrong answer and regenerating
until the model agreed with me would have produced a 100% agreement rate and destroyed the
only interesting result in the project.

**The honest caveat:** these responses were generated in the same working session that
authored the case library. No answer key was passed to the model and it did not grade itself,
but this is a *demonstration corpus, not a blind benchmark*, and it should not be cited as
one. Anyone wanting a genuinely blind measurement can run `--provider anthropic` against a
fresh case set; that path exists and is used by nothing else.

All 12 corrections are documented case by case, with the failure mode and the change each one
drove, in **[docs/responsible_ai_log.md](docs/responsible_ai_log.md)**.

---

## How it is built

Four stages. Full detail in **[docs/architecture.md](docs/architecture.md)**.

```
cases.csv ──┬─→ rule_checker.py  (34 checks, pure Python, no model)
            └─→ ai_diagnose.py   (prompt → JSON → schema + verbatim-evidence check)
                        │
                        └─→ review.py   (transcript → checker → AI, in that order)
                                  │
                        ┌─────────┴─────────┐
                        ▼                   ▼
                  can_apply() GATE    dashboard.py
```

The two analysis stages fail in opposite directions, and that is the design rationale:

- The **rule checker** cannot reason about intent, so it misses NS-002. It also cannot
  hallucinate — every finding carries the verbatim transcript line that triggered it.
- The **AI** can reason across four different `show` outputs and explain the mechanism. It
  can also invent one: on NS-021 it quoted the line containing the fault, declared the
  configuration fine, and invented powered-off hosts to explain a reachability boundary
  falling exactly at `.63`.

Where they disagree, the reviewer is told so explicitly. "One of these is wrong" is a much
more useful thing to hand a person than a single confident answer.

### Human oversight, mechanically

1. `schema.validate_evidence()` runs **before** a human sees anything. Every evidence line is
   whitespace-normalised and checked for containment in the transcript. Failures are marked
   `<-- NOT FOUND IN TRANSCRIPT` with a `!!!` warning block.
2. `review.render_case()` shows raw transcript → deterministic findings → AI conclusion, in
   that order. Reading a confident summary first makes you read the evidence looking for
   confirmation.
3. `review.can_apply()` gates `apply`. No review or a Rejected review means no fix output.

---

## Layout

```
NetSage-AI/
├── data/
│   ├── cases.csv                 33 cases with CLI transcripts + ground truth
│   ├── build_cases.py            regenerates cases.csv, validates it
│   └── seed_review_log.py        regenerates the 33 reviewer decisions
├── prompts/
│   ├── diagnose_prompt.md        main prompt, v1.2, 6 hard rules, 3 worked examples
│   ├── triage_prompt.md          fast OSI-layer + urgency triage (does not diagnose)
│   └── reviewer_assist_prompt.md adversarial pre-review, prompted to refute
├── src/
│   ├── netsage.py                CLI — every entry point
│   ├── rule_checker.py           34 deterministic checks
│   ├── ai_diagnose.py            provider adapter: recorded | anthropic
│   ├── schema.py                 output validation + verbatim-evidence enforcement
│   ├── review.py                 human review + the apply gate
│   └── dashboard.py              pandas → matplotlib → Jinja2
├── outputs/
│   ├── ai_responses.json         33 recorded diagnoses (committed, wrong ones included)
│   ├── review_log.csv            33 reviewer decisions
│   ├── rule_report.txt           generated
│   ├── dashboard.html            generated
│   ├── dashboard_summary.csv     generated
│   └── charts/*.png              generated, 5 charts
├── tests/test_rule_checker.py    65 tests, incl. named regression tests
├── docs/
│   ├── responsible_ai_log.md     12 corrections, failure modes, prompt changes
│   ├── architecture.md           the four stages, and why two analysis layers
│   └── demo_script.md            timed 7-minute shot list for the video
├── requirements.txt
└── README.md
```

---

## Notes for a reviewer

**Why no `.pkt` file.** The Cisco brief requires Packet Tracer files only from
Networking-domain students. This is an AI-domain submission — all three completed
certificates are AI-track (*Introduction to Modern AI*, *Apply AI: Analyze Customer Reviews*,
*Data Analytics Essentials*) and the problem statement's `MAIN COURSE` is Modern AI. Lab
transcripts ship inside `cases.csv`, which the brief's "Packet Tracer **or lab scenarios**"
wording permits.

**The bugs are documented, not hidden.** Several parsers exist in their current form because
an earlier version was wrong, and each carries a named regression test. The most instructive:
`ping_results` matched the success pattern anywhere in a result block, and
`Reply from 192.168.10.1: Destination host unreachable.` contains `Reply from` — so a *failed*
ping was recorded as a success, silently disabling every connectivity check downstream. The
test is called `test_destination_host_unreachable_is_a_failure_not_a_success`.

**Nothing here touches a device.** `apply` prints commands for a human to run.

---

## Reproducing every claim in this README

```bash
python -m pytest tests/ -q
python src/netsage.py check --all | tail -4
python src/netsage.py diagnose --all
python src/netsage.py review --summary
python src/netsage.py apply --case NS-031
python src/netsage.py apply --case NS-007
python src/netsage.py dashboard
```
