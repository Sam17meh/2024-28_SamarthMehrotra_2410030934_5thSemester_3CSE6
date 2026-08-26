"""
schema.py -- validation for AI diagnosis output.

The diagnosis prompt asks the model for strict JSON. Models mostly comply, but "mostly"
is not a property you want in a graded pipeline, so every response is validated here
before anything downstream touches it.

Deliberately hand-rolled rather than using jsonschema: this keeps the project dependency
free beyond pandas/matplotlib/Jinja2, and the error messages are more useful for the
demo than a generic schema traceback.

The strictest rule is verbatim evidence (Rule 2 of the prompt). validate_evidence()
checks each cited line actually appears in the transcript. That check is what makes the
"AI responses quote or reference actual show-command evidence" requirement provable
rather than merely claimed.
"""

from __future__ import annotations

VALID_LAYERS = {1, 2, 3, 4, 7}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_CONCEPTS = {
    "VLAN",
    "Gateway",
    "DHCP",
    "DNS",
    "Routing",
    "ACL",
    "NAT",
    "Wireless",
    "Physical",
}

REQUIRED_FIELDS = {
    "root_cause": str,
    "osi_layer": int,
    "concept_tag": str,
    "confidence": str,
    "evidence": list,
    "next_command": str,
    "fix_steps": list,
    "secondary_findings": list,
    "requires_human_review": bool,
}


class SchemaError(Exception):
    """Raised when a diagnosis cannot be used at all."""


def validate_diagnosis(obj) -> list[str]:
    """Return a list of problems with a diagnosis object. Empty list means valid."""
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"expected a JSON object, got {type(obj).__name__}"]

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in obj:
            errors.append(f"missing required field: {field}")
            continue
        value = obj[field]
        # bool is a subclass of int in Python, so osi_layer=True would sneak past a
        # naive isinstance check.
        if expected_type is int and isinstance(value, bool):
            errors.append("osi_layer must be an integer, not a boolean")
            continue
        if not isinstance(value, expected_type):
            errors.append(
                f"{field} must be {expected_type.__name__}, got {type(value).__name__}"
            )

    if isinstance(obj.get("osi_layer"), int) and not isinstance(obj.get("osi_layer"), bool):
        if obj["osi_layer"] not in VALID_LAYERS:
            errors.append(
                f"osi_layer {obj['osi_layer']} not in {sorted(VALID_LAYERS)}"
            )

    if isinstance(obj.get("confidence"), str):
        if obj["confidence"].lower() not in VALID_CONFIDENCE:
            errors.append(
                f"confidence '{obj['confidence']}' not in {sorted(VALID_CONFIDENCE)}"
            )

    if isinstance(obj.get("concept_tag"), str):
        if obj["concept_tag"] not in VALID_CONCEPTS:
            errors.append(
                f"concept_tag '{obj['concept_tag']}' not in {sorted(VALID_CONCEPTS)}"
            )

    # An empty evidence array defeats the point of the whole exercise.
    if isinstance(obj.get("evidence"), list):
        if not obj["evidence"]:
            errors.append("evidence must contain at least one quoted line")
        for i, line in enumerate(obj["evidence"]):
            if not isinstance(line, str):
                errors.append(f"evidence[{i}] must be a string")
            elif not line.strip():
                errors.append(f"evidence[{i}] is blank")

    if isinstance(obj.get("fix_steps"), list) and not obj["fix_steps"]:
        errors.append("fix_steps must contain at least one step")

    if obj.get("requires_human_review") is False:
        errors.append(
            "requires_human_review must be true - this pipeline does not support "
            "auto-applied fixes"
        )

    return errors


def validate_evidence(diagnosis: dict, show_outputs: str) -> list[str]:
    """
    Check that every cited evidence line appears verbatim in the transcript.

    Whitespace is normalised on both sides before comparison, because a model that
    reproduces a line correctly but collapses the column padding in `show vlan brief`
    has still quoted real evidence. Anything beyond whitespace difference is treated
    as a fabrication.

    Returns a list of evidence lines that could not be located.
    """
    if not isinstance(diagnosis.get("evidence"), list):
        return []

    def normalise(text: str) -> str:
        return " ".join(text.split())

    haystack = normalise(show_outputs)
    unverified = []

    for line in diagnosis["evidence"]:
        if not isinstance(line, str):
            continue
        needle = normalise(line)
        if needle and needle not in haystack:
            unverified.append(line)

    return unverified


def evidence_integrity_rate(diagnoses: dict, cases_by_id: dict) -> float:
    """
    Fraction of diagnoses whose every evidence line is verifiable.

    Reported on the dashboard. If this is not 1.0 the pipeline is citing lines that do
    not exist, which is the most serious failure mode the system can have.
    """
    if not diagnoses:
        return 0.0

    clean = 0
    for case_id, diagnosis in diagnoses.items():
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        if not validate_evidence(diagnosis, case["show_outputs"]):
            clean += 1

    return clean / len(diagnoses)
