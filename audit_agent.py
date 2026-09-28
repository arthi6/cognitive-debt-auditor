"""
Audit Agent
------------
Recalls historical engineering context from Hindsight and analyzes a
proposed change using the local Ollama LLM.

The agent also applies a deterministic safety rule:
if retrieved evidence explicitly says the change violates an ADR,
the final risk is forced to HIGH.
"""

import json
import re
from dataclasses import dataclass, field

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    LLM_AVAILABLE,
)
from memory_retriever import retrieve_relevant_memories, RetrievalResult


# Explicit architectural violation detector.
# Example:
# "Bulk removal of legacy adapter would violate ADR-014"
_ADR_VIOLATION_PATTERN = re.compile(
    r"\bviolat\w*\b[^.]{0,80}\bADR[-\s]?\d+\b"
    r"|\bADR[-\s]?\d+\b[^.]{0,80}\bviolat\w*\b",
    re.IGNORECASE,
)


def _detect_explicit_architectural_violation(
    evidence: list[str],
) -> str | None:
    """Return evidence explicitly stating an ADR violation."""
    for item in evidence:
        if _ADR_VIOLATION_PATTERN.search(item):
            return item
    return None


@dataclass
class AuditResult:
    proposed_change: str
    risk_level: str
    historical_why: str
    previous_incidents: list[str] = field(default_factory=list)
    architectural_constraints: list[str] = field(default_factory=list)
    potential_consequences: str = ""
    recommended_checks: list[str] = field(default_factory=list)
    recommendation: str = ""
    evidence: list[str] = field(default_factory=list)

    reasoning_mode: str = "llm"
    memory_status_message: str = ""


def _llm_audit(
    proposed_change: str,
    evidence: list[str],
) -> dict:

    from openai import OpenAI

    client = OpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key="ollama",
        timeout=180.0,
        max_retries=0,
    )

    evidence_text = "\n".join(
        f"- {item}" for item in evidence[:6]
    )

    prompt = f"""
You are the Cognitive Debt Auditor for a software engineering team.

Your job is to analyze a proposed change using ONLY the historical
engineering evidence retrieved from Hindsight.

Do NOT invent facts.

Proposed change:
"{proposed_change}"

Historical engineering evidence:
{evidence_text}

Important rules:

1. If the evidence explicitly says the proposed action violates an
   architectural decision, ADR, backward-compatibility requirement,
   migration requirement, or other binding constraint, treat the
   change as HIGH risk.

2. One successfully migrated tenant does NOT prove that all tenants
   have migrated.

3. If a previous incident, failed migration, rollback, compatibility
   issue, or unresolved dependency exists, mention it.

4. Explain WHY the existing code or architecture exists.

5. Recommendations must be grounded in the evidence.

Return ONLY valid JSON with exactly these fields:

{{
  "risk_level": "HIGH" | "MEDIUM" | "LOW",
  "historical_why": "why this code or architecture exists",
  "previous_incidents": [],
  "architectural_constraints": [],
  "potential_consequences": "what could go wrong",
  "recommended_checks": [],
  "recommendation": "one clear recommendation sentence"
}}
"""

    response = client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a conservative software architecture "
                    "review agent. Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0,
        max_tokens=700,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("Ollama returned an empty response.")

    return json.loads(content)


def _fallback_audit(
    proposed_change: str,
    evidence: list[str],
) -> dict:

    incident_kw = [
        "incident",
        "failure",
        "failed",
        "rollback",
        "postmortem",
    ]

    constraint_kw = [
        "constraint",
        "adr",
        "decision",
        "require",
        "must",
    ]

    incidents = [
        e for e in evidence
        if any(k in e.lower() for k in incident_kw)
    ]

    constraints = [
        e for e in evidence
        if any(k in e.lower() for k in constraint_kw)
    ]

    risk = (
        "HIGH"
        if incidents or constraints
        else "MEDIUM"
        if evidence
        else "UNKNOWN"
    )

    return {
        "risk_level": risk,
        "historical_why": (
            "Retrieved engineering history indicates that this "
            "component exists for previously documented reasons."
        ),
        "previous_incidents": incidents,
        "architectural_constraints": constraints,
        "potential_consequences": (
            "Potential consequences are based on the retrieved "
            "historical evidence."
        ),
        "recommended_checks": [
            "Review the retrieved historical evidence.",
            "Verify all documented constraints before proceeding.",
        ],
        "recommendation": (
            "Review the historical constraints and validate them "
            "before making the proposed change."
        ),
    }


def audit_change(
    proposed_change: str,
) -> AuditResult:

    print("Retrieving historical engineering memory...")

    retrieval: RetrievalResult = retrieve_relevant_memories(
        proposed_change
    )

    if retrieval.status == "unavailable":
        return AuditResult(
            proposed_change=proposed_change,
            risk_level="UNKNOWN",
            historical_why="",
            reasoning_mode="hindsight_unavailable",
            memory_status_message=retrieval.message,
        )

    if retrieval.status == "empty":
        return AuditResult(
            proposed_change=proposed_change,
            risk_level="UNKNOWN",
            historical_why="",
            recommendation=(
                "No historical memory was found for this proposal. "
                "Proceed with standard engineering review."
            ),
            reasoning_mode="no_memory",
            memory_status_message=retrieval.message,
        )

    evidence = retrieval.evidence

    try:
        if LLM_AVAILABLE:

            print(
                f"Analyzing with local Ollama model: {OLLAMA_MODEL}"
            )

            parsed = _llm_audit(
                proposed_change,
                evidence,
            )

            mode = "llm"

            print("Local LLM audit completed successfully.")

        else:

            parsed = _fallback_audit(
                proposed_change,
                evidence,
            )

            mode = "fallback_no_llm"

    except Exception as exc:

        print(
            f"Local LLM failed: {exc}"
        )

        print(
            "Falling back to evidence-based audit."
        )

        parsed = _fallback_audit(
            proposed_change,
            evidence,
        )

        mode = "fallback_no_llm"

        parsed["potential_consequences"] = (
            parsed.get("potential_consequences", "")
            + f" LLM call failed: {exc}"
        )

    # ---------------------------------------------------------
    # DETERMINISTIC SAFETY OVERRIDE
    # ---------------------------------------------------------

    violation_evidence = (
        _detect_explicit_architectural_violation(
            evidence
        )
    )

    if (
        violation_evidence
        and parsed.get("risk_level") != "HIGH"
    ):

        parsed["risk_level"] = "HIGH"

        existing = parsed.get(
            "potential_consequences",
            "",
        ).strip()

        override_message = (
            "Risk escalated to HIGH because historical evidence "
            "explicitly states that the proposed action would "
            f"violate a recorded architectural decision: "
            f"{violation_evidence}"
        )

        parsed["potential_consequences"] = (
            f"{existing} {override_message}"
        ).strip()

    return AuditResult(
        proposed_change=proposed_change,
        risk_level=parsed.get(
            "risk_level",
            "UNKNOWN",
        ),
        historical_why=parsed.get(
            "historical_why",
            "",
        ),
        previous_incidents=parsed.get(
            "previous_incidents",
            [],
        ),
        architectural_constraints=parsed.get(
            "architectural_constraints",
            [],
        ),
        potential_consequences=parsed.get(
            "potential_consequences",
            "",
        ),
        recommended_checks=parsed.get(
            "recommended_checks",
            [],
        ),
        recommendation=parsed.get(
            "recommendation",
            "",
        ),
        evidence=evidence,
        reasoning_mode=mode,
    )


if __name__ == "__main__":

    import sys
    from config import DEFAULT_DEMO_PROPOSAL

    change = (
        sys.argv[1]
        if len(sys.argv) > 1
        else DEFAULT_DEMO_PROPOSAL
    )

    result = audit_change(change)

    print(
        json.dumps(
            result.__dict__,
            indent=2,
        )
    )