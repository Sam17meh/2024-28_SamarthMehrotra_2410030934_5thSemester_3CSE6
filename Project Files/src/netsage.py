"""
netsage.py -- the CLI. Everything in this project is reachable from here.

    python src/netsage.py check     --all | --case NS-007
    python src/netsage.py diagnose  --case NS-007 [--provider anthropic]
    python src/netsage.py review    --case NS-014 | --all | --summary
    python src/netsage.py apply     --case NS-007
    python src/netsage.py dashboard
    python src/netsage.py all

`apply` is the one that matters: it asks review.can_apply() before printing a single fix
step, so there is no path from an AI diagnosis to an applied change that skips a recorded
human decision.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review as review_mod
from ai_diagnose import DiagnosisError, diagnose_all, diagnose_case, get_provider
from rule_checker import check_count, check_ids, run_checks
from schema import validate_diagnosis, validate_evidence

ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = ROOT / "data" / "cases.csv"
REPORT_PATH = ROOT / "outputs" / "rule_report.txt"

REVIEWER = "Samarth Mehrotra"


def load_cases() -> list[dict]:
    csv.field_size_limit(10 ** 7)
    with open(CASES_PATH, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def pick(cases: list[dict], case_id: str) -> dict:
    for case in cases:
        if case["case_id"].upper() == case_id.upper():
            return case
    sys.exit(f"No such case: {case_id}. Known ids run NS-001 .. NS-{len(cases):03d}.")


def rule(char: str = "-", width: int = 78) -> str:
    return char * width


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def cmd_check(args, cases: list[dict]) -> int:
    targets = cases if args.all else [pick(cases, args.case)]
    lines, hits = [], 0

    for case in targets:
        findings = run_checks(case)
        hits += bool(findings)
        lines.append(rule("="))
        lines.append(f"{case['case_id']}  {case['concept_tag']}  L{case['osi_layer']}  {case['severity']}")
        lines.append(rule("="))
        lines.append(f"  symptom: {case['symptom']}")
        if findings:
            for finding in findings:
                lines.append(f"  [{finding.severity.upper():8}] {finding.check_id}")
                lines.append(f"             {finding.message}")
                if finding.evidence:
                    lines.append(f"             evidence : {finding.evidence}")
                if finding.suggested_command:
                    lines.append(f"             verify   : {finding.suggested_command}")
        else:
            lines.append("  no deterministic check fired -- outside the rule set")
        lines.append("")

    report = "\n".join(lines)
    print(report)

    if args.all:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(rule("="))
        print(f"{check_count()} checks implemented")
        print(f"{hits}/{len(targets)} cases produced at least one finding ({hits/len(targets):.1%})")
        print(f"report written to {REPORT_PATH}")
    return 0


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------

def cmd_diagnose(args, cases: list[dict]) -> int:
    try:
        provider = get_provider(args.provider)
    except DiagnosisError as exc:
        sys.exit(str(exc))

    if args.all:
        diagnoses, failures = diagnose_all(cases, provider)
        print(f"provider   : {provider.name}")
        print(f"diagnosed  : {len(diagnoses)}/{len(cases)}")
        unverified = [cid for cid, d in diagnoses.items() if d.get("_unverified_evidence")]
        print(f"schema ok  : {len(diagnoses)} (0 rejected)" if not failures
              else f"failures   : {len(failures)}")
        for cid, err in failures:
            print(f"  {cid}: {err}")
        print(f"evidence   : {len(unverified)} case(s) cite a line absent from the transcript"
              f"{' -> ' + ', '.join(unverified) if unverified else ''}")
        return 1 if failures else 0

    case = pick(cases, args.case)
    try:
        diagnosis = diagnose_case(case, provider)
    except DiagnosisError as exc:
        sys.exit(f"{case['case_id']}: {exc}")

    printable = {k: v for k, v in diagnosis.items() if not k.startswith("_")}
    print(json.dumps(printable, indent=2))

    print()
    print(rule())
    errors = validate_diagnosis(diagnosis)
    print(f"schema        : {'valid' if not errors else 'INVALID -- ' + '; '.join(errors)}")
    unverified = validate_evidence(diagnosis, case["show_outputs"])
    if unverified:
        print(f"evidence      : {len(unverified)} line(s) NOT found verbatim in the transcript")
        for line in unverified:
            print(f"                ! {line}")
    else:
        print(f"evidence      : all {len(diagnosis['evidence'])} line(s) found verbatim")
    print(f"provider      : {diagnosis.get('_provider')}")
    print(f"human review  : {review_mod.decision_for(case['case_id']) or 'not yet reviewed'}")
    return 0


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

def cmd_review(args, cases: list[dict]) -> int:
    if args.summary:
        s = review_mod.summarise()
        print(f"reviewed        : {s['total_reviewed']}")
        print(f"  accepted      : {s['accepted']}")
        print(f"  edited        : {s['edited']}")
        print(f"  rejected      : {s['rejected']}")
        print(f"corrections     : {s['corrections']}  (requirement is 5)")
        print(f"agreement rate  : {s['agreement_rate']:.1%}")
        if s["failure_modes"]:
            print("failure modes   :")
            for mode, count in sorted(s["failure_modes"].items(), key=lambda kv: -kv[1]):
                print(f"  {count:>2}  {mode}")
        return 0

    try:
        provider = get_provider(args.provider)
    except DiagnosisError as exc:
        sys.exit(str(exc))

    targets = cases if args.all else [pick(cases, args.case)]
    for case in targets:
        try:
            diagnosis = diagnose_case(case, provider)
        except DiagnosisError as exc:
            print(f"{case['case_id']}: no usable diagnosis ({exc}) -- skipped", file=sys.stderr)
            continue
        review_mod.review_case(case, diagnosis, run_checks(case), REVIEWER)
    return 0


# ---------------------------------------------------------------------------
# apply -- the gate
# ---------------------------------------------------------------------------

def cmd_apply(args, cases: list[dict]) -> int:
    case = pick(cases, args.case)
    allowed, reason = review_mod.can_apply(case["case_id"])

    if not allowed:
        print(rule("!"))
        print(f"REFUSED -- no fix steps will be emitted for {case['case_id']}.")
        print(rule("!"))
        print(reason)
        print()
        print("Human review is a precondition, not a formality. Fix steps stay unprinted.")
        return 2

    try:
        diagnosis = diagnose_case(case, get_provider(args.provider))
    except DiagnosisError as exc:
        sys.exit(f"{case['case_id']}: {exc}")

    row = review_mod.load_log().get(case["case_id"], {})
    corrected = (row.get("corrected_root_cause") or "").strip()

    print(rule("="))
    print(f"APPROVED -- {case['case_id']}")
    print(rule("="))
    print(reason)
    print()
    print(f"root cause (AI)       : {diagnosis['root_cause']}")
    if corrected:
        print(f"root cause (reviewer) : {corrected}")
        print("                        the reviewer's version supersedes the AI's.")
    print()
    print("fix steps, for a human to run -- this tool does not touch any device:")
    for i, step in enumerate(diagnosis["fix_steps"], 1):
        print(f"  {i}. {step}")
    print()
    print(f"verify with: {diagnosis['next_command']}")
    return 0


# ---------------------------------------------------------------------------
# dashboard / all
# ---------------------------------------------------------------------------

def cmd_dashboard(args, cases: list[dict]) -> int:
    from dashboard import build
    build()
    return 0


def cmd_all(args, cases: list[dict]) -> int:
    print(rule("="))
    print("1/3  deterministic checks")
    print(rule("="))
    cmd_check(argparse.Namespace(all=True, case=None), cases)

    print()
    print(rule("="))
    print("2/3  recorded AI diagnoses")
    print(rule("="))
    cmd_diagnose(argparse.Namespace(all=True, case=None, provider="recorded"), cases)

    print()
    print(rule("="))
    print("3/3  review summary + dashboard")
    print(rule("="))
    cmd_review(argparse.Namespace(summary=True, all=False, case=None, provider="recorded"), cases)
    print()
    return cmd_dashboard(args, cases)


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="netsage",
        description="NetSage AI -- AI-assisted network fault triage with enforced human review.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p = subs.add_parser("check", help="run the deterministic rule checker")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--case")
    p.set_defaults(func=cmd_check)

    p = subs.add_parser("diagnose", help="get an AI diagnosis")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--case")
    p.add_argument("--provider", default="recorded", choices=["recorded", "anthropic"])
    p.set_defaults(func=cmd_diagnose)

    p = subs.add_parser("review", help="human review of AI diagnoses")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--case")
    g.add_argument("--summary", action="store_true")
    p.add_argument("--provider", default="recorded", choices=["recorded", "anthropic"])
    p.set_defaults(func=cmd_review)

    p = subs.add_parser("apply", help="emit fix steps -- gated on human review")
    p.add_argument("--case", required=True)
    p.add_argument("--provider", default="recorded", choices=["recorded", "anthropic"])
    p.set_defaults(func=cmd_apply)

    p = subs.add_parser("dashboard", help="build charts, HTML and summary CSV")
    p.set_defaults(func=cmd_dashboard)

    p = subs.add_parser("all", help="check + diagnose + review summary + dashboard")
    p.set_defaults(func=cmd_all)

    p = subs.add_parser("checks", help="list every implemented check id")
    p.set_defaults(func=lambda a, c: (print("\n".join(check_ids())), print(f"\n{check_count()} checks"), 0)[-1])

    args = parser.parse_args(argv)
    return args.func(args, load_cases())


if __name__ == "__main__":
    sys.exit(main())
