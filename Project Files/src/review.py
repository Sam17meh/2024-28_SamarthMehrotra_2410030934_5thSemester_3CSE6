"""
review.py -- the human-in-the-loop stage, and the gate that gives it teeth.

The problem statement asks for "human review of AI suggestions (accepted / edited /
rejected)". A log alone would satisfy the letter of that: write a CSV, tick the box. This
module implements it as an actual control instead.

Two things make it real:

  1. review_case() shows the reviewer the AI diagnosis, the deterministic checker's
     findings, AND any evidence line that failed the verbatim check - side by side. The
     reviewer decides with the disagreements in front of them, not after the fact.

  2. can_apply() is consulted by `netsage.py apply` before any fix steps are emitted. A
     case with no review, or a Rejected review, cannot produce fix output. The AI cannot
     route around this: there is no code path from a diagnosis to an applied fix that
     does not pass through a recorded human decision.

That second point is the difference between a system that documents oversight and a
system that enforces it.
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "outputs" / "review_log.csv"

FIELDS = [
    "case_id",
    "reviewer",
    "reviewed_at",
    "decision",
    "failure_mode",
    "corrected_root_cause",
    "reviewer_note",
    "checker_agreement",
]

ACCEPTED = "Accepted"
EDITED = "Edited"
REJECTED = "Rejected"
VALID_DECISIONS = {ACCEPTED, EDITED, REJECTED}

# A fix may only be emitted for a diagnosis a human signed off on. Edited counts: the
# reviewer corrected the diagnosis and stands behind the corrected version.
APPLICABLE = {ACCEPTED, EDITED}

FAILURE_MODES = {
    "surface-signal bias": "Named the most visible symptom instead of the underlying cause.",
    "plausible-but-wrong mechanism": "Invented a failure mechanism that the supplied evidence contradicts.",
    "incorrect protocol reasoning": "Got the protocol's own behaviour wrong.",
    "evidence not verbatim": "Paraphrased or reformatted a cited line instead of copying it.",
    "unsafe fix proposed": "Cause identified correctly, but the fix would cause collateral damage.",
    "platform-inappropriate command": "Proposed a command that is not valid on the device in question.",
    "fix violates design intent": "Technically resolves the fault but against the documented design.",
    "wrong device blamed": "Right fault class, wrong end of the link.",
    "missed second fault": "Primary diagnosis correct but an independent second problem went unreported.",
}


# ---------------------------------------------------------------------------
# log access
# ---------------------------------------------------------------------------

def load_log(path: Path = LOG_PATH) -> dict[str, dict]:
    """Return {case_id: row}. Later entries win, so a re-review supersedes the first."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["case_id"]: row for row in csv.DictReader(fh)}


def append_review(
    case_id: str,
    decision: str,
    reviewer: str,
    note: str = "",
    failure_mode: str = "",
    corrected_root_cause: str = "",
    checker_agreement: str = "",
    path: Path = LOG_PATH,
) -> None:
    """Append one decision. The log is append-only so the review history stays auditable."""
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(VALID_DECISIONS)}, got {decision!r}")

    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()

    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "case_id": case_id,
                "reviewer": reviewer,
                "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "decision": decision,
                "failure_mode": failure_mode,
                "corrected_root_cause": corrected_root_cause,
                "reviewer_note": note,
                "checker_agreement": checker_agreement,
            }
        )


def decision_for(case_id: str, path: Path = LOG_PATH) -> str | None:
    row = load_log(path).get(case_id)
    return row["decision"] if row else None


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def can_apply(case_id: str, path: Path = LOG_PATH) -> tuple[bool, str]:
    """
    Decide whether fix steps may be emitted for a case.

    Returns (allowed, reason). The reason is shown to the user either way - a refusal
    that does not explain itself teaches the operator nothing.
    """
    row = load_log(path).get(case_id)

    if row is None:
        return False, (
            f"{case_id} has no entry in review_log.csv. Fix steps are gated on human "
            f"review - run `netsage.py review --case {case_id}` first."
        )

    decision = row["decision"]

    if decision == REJECTED:
        note = row.get("reviewer_note", "").strip()
        return False, (
            f"{case_id} was REJECTED on review by {row.get('reviewer', 'unknown')}. "
            f"The AI diagnosis was found incorrect, so its fix steps must not be applied."
            + (f"\nReviewer note: {note}" if note else "")
        )

    if decision not in APPLICABLE:
        return False, f"{case_id} has an unrecognised decision {decision!r} in the review log."

    return True, f"{case_id} was {decision.upper()} on review by {row.get('reviewer', 'unknown')}."


# ---------------------------------------------------------------------------
# interactive review
# ---------------------------------------------------------------------------

def _rule(char: str = "-", width: int = 78) -> str:
    return char * width


def render_case(case: dict, diagnosis: dict, findings: list) -> str:
    """
    Build the reviewer's view of one case.

    Ordering is deliberate: the raw transcript comes before the AI's interpretation of
    it, and the deterministic findings come before the AI's conclusion. A reviewer who
    reads the AI's confident summary first tends to read the evidence looking for
    confirmation of it.
    """
    out = [
        _rule("="),
        f"CASE {case['case_id']}   {case['concept_tag']}  |  OSI layer {case['osi_layer']}"
        f"  |  severity {case['severity']}",
        _rule("="),
        "",
        "SYMPTOM AS REPORTED",
        f"  {case['symptom']}",
        "",
        "TOPOLOGY",
        f"  {case['topology_note']}",
        "",
        _rule(),
        "SHOW COMMAND OUTPUT",
        _rule(),
    ]
    out += ["  " + line for line in case["show_outputs"].splitlines()]

    out += ["", _rule(), f"DETERMINISTIC CHECKER  ({len(findings)} finding(s))", _rule()]
    if findings:
        for finding in findings:
            out.append(f"  [{finding.severity.upper():8}] {finding.check_id}")
            out.append(f"             {finding.message}")
            if finding.evidence:
                out.append(f"             evidence: {finding.evidence}")
    else:
        out.append("  No deterministic check fired. This case is outside the rule set -")
        out.append("  the AI layer is the only analysis available, so scrutinise it harder.")

    out += ["", _rule(), "AI DIAGNOSIS", _rule()]
    out += [
        f"  root cause   : {diagnosis['root_cause']}",
        f"  layer / tag  : {diagnosis['osi_layer']} / {diagnosis['concept_tag']}",
        f"  confidence   : {diagnosis['confidence']}",
        f"  next command : {diagnosis['next_command']}",
        "",
        "  evidence cited:",
    ]
    unverified = set(diagnosis.get("_unverified_evidence") or [])
    for line in diagnosis["evidence"]:
        flag = "  <-- NOT FOUND IN TRANSCRIPT" if line in unverified else ""
        out.append(f"    | {line}{flag}")

    out += ["", "  proposed fix steps (NOT applied):"]
    out += [f"    {i}. {step}" for i, step in enumerate(diagnosis["fix_steps"], 1)]

    if diagnosis.get("secondary_findings"):
        out += ["", "  secondary findings:"]
        out += [f"    - {s}" for s in diagnosis["secondary_findings"]]

    if unverified:
        out += [
            "",
            _rule("!"),
            f"WARNING: {len(unverified)} evidence line(s) could not be located in the",
            "transcript. Rule 2 of the diagnosis prompt requires verbatim quotation.",
            "Treat the conclusion as unsupported until you verify it yourself.",
            _rule("!"),
        ]

    checker_ids = {f.check_id for f in findings}
    tag = diagnosis["concept_tag"].lower()
    if findings and not any(tag in cid for cid in checker_ids):
        out += [
            "",
            f"NOTE: the checker's findings ({', '.join(sorted(checker_ids))}) do not "
            f"obviously",
            f"      align with the AI's '{diagnosis['concept_tag']}' conclusion. One of "
            f"them is wrong.",
        ]

    return "\n".join(out)


def prompt_decision(case_id: str, reviewer: str) -> dict:
    """Ask the reviewer for a decision. Loops until a valid one is given."""
    print()
    print(_rule("="))
    print("  [a]ccept    the diagnosis is correct and the fix is safe as written")
    print("  [e]dit      the diagnosis is broadly right but needs correction")
    print("  [r]eject    the diagnosis is wrong")
    print("  [s]kip      decide later - nothing is written to the log")
    print(_rule("="))

    mapping = {"a": ACCEPTED, "e": EDITED, "r": REJECTED}

    while True:
        raw = input(f"{case_id} decision [a/e/r/s]: ").strip().lower()
        if raw in ("s", "skip"):
            return {"decision": None}
        if raw in mapping:
            decision = mapping[raw]
            break
        print("  Please answer a, e, r or s.")

    result = {"decision": decision, "reviewer": reviewer}

    if decision in (EDITED, REJECTED):
        print()
        print("  Failure modes:")
        modes = sorted(FAILURE_MODES)
        for i, mode in enumerate(modes, 1):
            print(f"    {i}. {mode} - {FAILURE_MODES[mode]}")
        raw = input("  Failure mode number (blank to skip): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(modes):
            result["failure_mode"] = modes[int(raw) - 1]

        result["corrected_root_cause"] = input("  Corrected root cause: ").strip()

    result["note"] = input("  Reviewer note: ").strip()

    agreement = input("  Checker agreement [agree/disagree/silent]: ").strip().lower()
    result["checker_agreement"] = {
        "agree": "agree",
        "disagree": "disagree",
        "silent": "checker_silent",
    }.get(agreement, agreement)

    return result


def review_case(case: dict, diagnosis: dict, findings: list, reviewer: str) -> str | None:
    """Show one case and record the decision. Returns the decision, or None if skipped."""
    print(render_case(case, diagnosis, findings))

    existing = decision_for(case["case_id"])
    if existing:
        print()
        print(f"NOTE: {case['case_id']} already has a recorded decision of {existing}.")
        print("      A new entry will be appended; the log is append-only and the")
        print("      latest entry takes precedence.")

    try:
        result = prompt_decision(case["case_id"], reviewer)
    except (EOFError, KeyboardInterrupt):
        print("\nReview aborted. Nothing written.", file=sys.stderr)
        return None

    if result["decision"] is None:
        print(f"{case['case_id']} skipped - no entry written.")
        return None

    append_review(
        case_id=case["case_id"],
        decision=result["decision"],
        reviewer=result.get("reviewer", reviewer),
        note=result.get("note", ""),
        failure_mode=result.get("failure_mode", ""),
        corrected_root_cause=result.get("corrected_root_cause", ""),
        checker_agreement=result.get("checker_agreement", ""),
    )
    print(f"Recorded: {case['case_id']} -> {result['decision']}")
    return result["decision"]


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

def summarise(path: Path = LOG_PATH) -> dict:
    """Counts used by the dashboard and by `netsage.py review --summary`."""
    log = load_log(path)
    counts = {ACCEPTED: 0, EDITED: 0, REJECTED: 0}
    modes: dict[str, int] = {}

    for row in log.values():
        if row["decision"] in counts:
            counts[row["decision"]] += 1
        mode = (row.get("failure_mode") or "").strip()
        if mode:
            modes[mode] = modes.get(mode, 0) + 1

    total = sum(counts.values())
    corrections = counts[EDITED] + counts[REJECTED]

    return {
        "total_reviewed": total,
        "accepted": counts[ACCEPTED],
        "edited": counts[EDITED],
        "rejected": counts[REJECTED],
        "corrections": corrections,
        "agreement_rate": counts[ACCEPTED] / total if total else 0.0,
        "failure_modes": modes,
    }
