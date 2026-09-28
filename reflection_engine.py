"""
Reflection Engine
-------------------
Closes the loop: developer records what actually happened after an audit,
the engine synthesizes a concise lesson from (proposal + evidence + audit +
outcome), and retains that lesson into Hindsight tagged as procedural
memory. Future audits can then recall this lesson.
"""

from dataclasses import dataclass

from config import HINDSIGHT_BASE_URL, BANK_ID, LLM_AVAILABLE, OPENAI_MODEL


@dataclass
class ReflectionResult:
    status: str  # "stored" | "hindsight_unavailable" | "error"
    lesson: str = ""
    message: str = ""


def _get_client():
    from hindsight_client import Hindsight
    return Hindsight(base_url=HINDSIGHT_BASE_URL)


def _generate_lesson_llm(proposal: str, evidence: list[str], audit_result, outcome: str, notes: str) -> str:
    from openai import OpenAI
    client = OpenAI()

    prompt = f"""Summarize a single, concrete, reusable engineering lesson from this
audit-and-outcome cycle. One or two sentences. Write it as a general
principle future engineers should follow, not as a narrative recap.

Proposed change: {proposal}
Audit risk level: {getattr(audit_result, 'risk_level', 'UNKNOWN')}
Audit recommendation: {getattr(audit_result, 'recommendation', '')}
Developer-recorded outcome: {outcome}
Notes: {notes or '(none)'}
"""
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _generate_lesson_fallback(proposal: str, outcome: str, notes: str) -> str:
    return (
        f"Regarding '{proposal}': recorded outcome was '{outcome}'."
        + (f" Notes: {notes}" if notes else "")
        + " (Fallback mode — no LLM summarization applied.)"
    )


def reflect_and_store(proposal: str, evidence: list[str], audit_result, outcome: str, notes: str = "") -> ReflectionResult:
    try:
        if LLM_AVAILABLE:
            lesson = _generate_lesson_llm(proposal, evidence, audit_result, outcome, notes)
        else:
            lesson = _generate_lesson_fallback(proposal, outcome, notes)
    except Exception as e:
        lesson = _generate_lesson_fallback(proposal, outcome, notes)
        lesson += f" (LLM summarization failed: {e})"

    content = f"[procedural][lesson_learned] {lesson}"

    try:
        client = _get_client()
        client.retain(bank_id=BANK_ID, content=content)
    except Exception as e:
        return ReflectionResult(
            status="hindsight_unavailable",
            lesson=lesson,
            message=f"Lesson generated but could not be stored — Hindsight unavailable ({e}).",
        )

    return ReflectionResult(status="stored", lesson=lesson, message="Stored as procedural memory.")


def synthesize_patterns(topic_hint: str = "recurring architectural risks and lessons in this codebase") -> str:
    """Higher-level reflect() synthesis across everything retained so far."""
    try:
        client = _get_client()
        result = client.reflect(bank_id=BANK_ID, query=topic_hint)
    except Exception as e:
        return f"Hindsight is unavailable. Historical memory cannot be verified. ({e})"

    # hindsight-client's real ReflectResponse is a Pydantic model with `.text`.
    text = getattr(result, "text", None)
    if text:
        return text
    if isinstance(result, dict):
        return result.get("text") or result.get("content") or str(result)
    return str(result)
