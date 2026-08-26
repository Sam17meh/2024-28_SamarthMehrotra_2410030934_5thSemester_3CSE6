"""
ai_diagnose.py -- the AI layer, behind a provider adapter.

Two providers implement the same interface:

  recorded    Replays outputs/ai_responses.json. This is the default, and it is the
              default deliberately: the demo video must not depend on network access,
              an API key, or a model that might phrase things differently on the day.
  anthropic   Live call to the Messages API using `requests`. No SDK install required.

PROVENANCE OF THE RECORDED RESPONSES
------------------------------------
They are genuine model output, produced by reading each case's symptom, topology note
and show-command transcript against prompts/diagnose_prompt.md. The `expected_fault`
column of cases.csv was NOT supplied to the model - that column is the answer key and
feeding it in would make the whole evaluation circular.

Consequently some recorded responses are wrong. That is the point. The wrong ones are
what docs/responsible_ai_log.md documents, and a pipeline reporting 100% agreement
would be evidence of a leaked answer key rather than a good model.

Every response, recorded or live, is validated by schema.py before it is returned. A
response that fails validation is retried once on the live provider and raised as an
error on the recorded one - it is never silently patched into shape, because a pipeline
that quietly repairs bad AI output cannot tell you when the AI is degrading.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from schema import validate_diagnosis, validate_evidence

ROOT = Path(__file__).resolve().parent.parent
RECORDED_PATH = ROOT / "outputs" / "ai_responses.json"
PROMPT_PATH = ROOT / "prompts" / "diagnose_prompt.md"

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-5"
MAX_TOKENS = 2000


class DiagnosisError(Exception):
    """Raised when a usable diagnosis could not be obtained for a case."""


# ---------------------------------------------------------------------------
# prompt assembly
# ---------------------------------------------------------------------------

def load_system_prompt(path: Path = PROMPT_PATH) -> str:
    """
    Extract the system prompt from the first fenced block of diagnose_prompt.md.

    The prompt file is written for humans to read and audit - it has the schema, three
    worked examples and a version history in it. The live provider only needs the system
    prompt itself, so it is pulled from the file rather than duplicated in this module.
    Keeping one copy means a prompt revision cannot silently fail to reach the model.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(r"## System prompt\s*\n+```\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise DiagnosisError(
            f"could not locate the system prompt block in {path.name} - the file's "
            "'## System prompt' fenced block is required by load_system_prompt()"
        )
    return match.group(1).strip()


def build_user_message(case: dict) -> str:
    """Fill the user message template from the prompt file. Never includes expected_fault."""
    return (
        f"CASE ID: {case['case_id']}\n\n"
        f"SYMPTOM:\n{case['symptom']}\n\n"
        f"TOPOLOGY NOTE:\n{case['topology_note']}\n\n"
        f"SHOW COMMAND OUTPUT:\n{case['show_outputs']}\n\n"
        "Return your diagnosis as a single JSON object matching the schema. Remember "
        "that every entry in `evidence` must be copied verbatim from the SHOW COMMAND "
        "OUTPUT above."
    )


def _extract_json(raw: str) -> dict:
    """
    Parse a model response into an object.

    Rule 1 of the prompt forbids markdown fences, and in practice the model complies -
    but a parser that dies on a stray fence would make the live provider needlessly
    brittle, so fences are stripped before parsing rather than treated as fatal.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DiagnosisError(f"model returned unparseable JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

class RecordedProvider:
    """Replays committed responses. Offline, deterministic, demo-safe."""

    name = "recorded"

    def __init__(self, path: Path = RECORDED_PATH):
        if not path.exists():
            raise DiagnosisError(
                f"{path} not found. Recorded responses are committed to the repo; if "
                "the file is missing, run with --provider anthropic to regenerate."
            )
        self.responses = json.loads(path.read_text(encoding="utf-8"))

    def diagnose(self, case: dict) -> dict:
        case_id = case["case_id"]
        if case_id not in self.responses:
            raise DiagnosisError(f"no recorded response for {case_id}")
        return self.responses[case_id]


class AnthropicProvider:
    """Live Messages API call. Reads ANTHROPIC_API_KEY from the environment."""

    name = "anthropic"

    def __init__(self, model: str = MODEL):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise DiagnosisError(
                "ANTHROPIC_API_KEY is not set. Either export it or use the default "
                "--provider recorded."
            )
        try:
            import requests  # noqa: F401  (imported here so the recorded path needs no deps)
        except ImportError as exc:  # pragma: no cover
            raise DiagnosisError("the anthropic provider needs `requests` installed") from exc
        self.model = model
        self.system_prompt = load_system_prompt()

    def diagnose(self, case: dict) -> dict:
        import requests

        payload = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": self.system_prompt,
            "messages": [{"role": "user", "content": build_user_message(case)}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
        if response.status_code != 200:
            raise DiagnosisError(
                f"API returned {response.status_code}: {response.text[:300]}"
            )

        body = response.json()
        blocks = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
        if not blocks:
            raise DiagnosisError("API response contained no text block")
        return _extract_json("".join(blocks))


def get_provider(name: str = "recorded"):
    if name == "recorded":
        return RecordedProvider()
    if name == "anthropic":
        return AnthropicProvider()
    raise DiagnosisError(f"unknown provider '{name}' (expected 'recorded' or 'anthropic')")


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def diagnose_case(case: dict, provider=None, retries: int = 1) -> dict:
    """
    Return a schema-valid diagnosis for one case.

    The returned object carries two extra keys the model did not produce:

      _provider              which provider answered
      _unverified_evidence   evidence lines that could not be found in the transcript

    They are prefixed with an underscore so they are visibly ours rather than the
    model's. _unverified_evidence is deliberately surfaced rather than raised on: a
    fabricated evidence line is exactly the failure the reviewer needs to see, so it is
    passed through to the review stage and shown, not hidden behind an exception.
    """
    provider = provider or get_provider()
    last_errors: list[str] = []

    for attempt in range(retries + 1):
        try:
            diagnosis = provider.diagnose(case)
        except DiagnosisError:
            if attempt >= retries or provider.name == "recorded":
                raise
            time.sleep(1)
            continue

        errors = validate_diagnosis(diagnosis)
        if not errors:
            diagnosis = dict(diagnosis)
            diagnosis["_provider"] = provider.name
            diagnosis["_unverified_evidence"] = validate_evidence(
                diagnosis, case["show_outputs"]
            )
            return diagnosis

        last_errors = errors
        # Recorded responses are committed data. If one fails validation the fix is to
        # correct the file, not to retry an identical replay.
        if provider.name == "recorded" or attempt >= retries:
            break
        time.sleep(1)

    raise DiagnosisError(
        f"{case['case_id']}: diagnosis failed schema validation - "
        + "; ".join(last_errors)
    )


def diagnose_all(cases: list[dict], provider=None) -> tuple[dict, dict]:
    """
    Diagnose every case. Returns (diagnoses_by_id, failures_by_id).

    One bad case does not abort the batch - the failure is recorded and the sweep
    continues, so `netsage.py diagnose --all` always produces a complete picture.
    """
    provider = provider or get_provider()
    diagnoses: dict[str, dict] = {}
    failures: dict[str, str] = {}

    for case in cases:
        try:
            diagnoses[case["case_id"]] = diagnose_case(case, provider=provider)
        except DiagnosisError as exc:
            failures[case["case_id"]] = str(exc)

    return diagnoses, failures
