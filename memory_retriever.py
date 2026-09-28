"""
Memory Retriever
----------------
Queries Hindsight for historical context relevant to a proposed change.

Hard rules enforced here:
- Never invent evidence.
- Never allow clearly unrelated historical memories to influence the audit.
- If recall returns nothing usable, say so explicitly.
- Never let a malformed Hindsight response, or Hindsight being offline,
  crash the caller.
- Every failure mode returns a structured result with a clear status
  the UI can render honestly.
"""

from dataclasses import dataclass, field
import re

from config import HINDSIGHT_BASE_URL, BANK_ID


NO_MEMORY_FOUND = "No relevant historical memory was found."

HINDSIGHT_UNAVAILABLE = (
    "Hindsight is unavailable. Historical memory cannot be verified."
)


@dataclass
class RetrievalResult:
    status: str  # "ok" | "empty" | "unavailable" | "error"
    evidence: list[str] = field(default_factory=list)
    message: str = ""


def _get_client():
    from hindsight_client import Hindsight

    return Hindsight(base_url=HINDSIGHT_BASE_URL)


def _normalize_recall_response(raw) -> list[str]:
    """
    Normalize hindsight-client RecallResponse into a flat list of
    evidence strings.

    Primary supported shape:
        RecallResponse.results
        result.text

    Defensive support is also included for dict/list/string responses.

    If the response shape is unknown, return [] rather than guessing.
    """
    try:
        if raw is None:
            return []

        # ---------------------------------------------------------
        # Primary path: hindsight-client RecallResponse
        # ---------------------------------------------------------
        if hasattr(raw, "results"):
            out = []

            for r in raw.results:
                text = getattr(r, "text", None)

                if text is None and isinstance(r, dict):
                    text = r.get("text") or r.get("content")

                if text:
                    out.append(str(text))

            return [item for item in out if item.strip()]

        # ---------------------------------------------------------
        # String response
        # ---------------------------------------------------------
        if isinstance(raw, str):
            return [raw] if raw.strip() else []

        # ---------------------------------------------------------
        # Dictionary response
        # ---------------------------------------------------------
        if isinstance(raw, dict):

            if "results" in raw and isinstance(raw["results"], list):
                out = []

                for r in raw["results"]:
                    if isinstance(r, dict):
                        text = r.get("text") or r.get("content")

                        if text:
                            out.append(str(text))
                    else:
                        text = getattr(r, "text", None)

                        if text:
                            out.append(str(text))

                return [item for item in out if item.strip()]

            if "content" in raw:
                content = raw["content"]

                if content:
                    return [str(content)]

            return []

        # ---------------------------------------------------------
        # List response
        # ---------------------------------------------------------
        if isinstance(raw, list):
            out = []

            for r in raw:

                if isinstance(r, dict):
                    text = r.get("text") or r.get("content")

                    if text:
                        out.append(str(text))

                else:
                    text = getattr(r, "text", None)

                    if text:
                        out.append(str(text))

            return [item for item in out if item.strip()]

        return []

    except Exception:
        # Fail safe.
        return []


def extract_subject(proposed_change: str) -> str:
    """
    Pull the main component being changed out of the proposal.

    Example:

        Remove the legacy authentication adapter and replace it
        with the new OAuth flow.

    becomes approximately:

        the legacy authentication adapter
    """

    text = proposed_change.strip().rstrip(".")
    lowered = text.lower()

    for verb in [
        "remove",
        "delete",
        "replace",
        "refactor",
        "migrate",
        "deprecate",
        "rewrite",
        "add",
    ]:

        if lowered.startswith(verb + " "):
            text = text[len(verb):].strip()
            break

    for splitter in [
        " and replace",
        " and migrate",
        " with ",
        " in favor",
        " and switch",
    ]:

        idx = text.lower().find(splitter)

        if idx > 0:
            text = text[:idx].strip()
            break

    return text


def _build_queries(proposed_change: str) -> list[str]:
    """
    Build several recall queries around the same proposed change.

    Different queries target:
    - the proposal itself
    - architecture decisions
    - incidents
    - dependencies
    - migrations
    - lessons learned
    """

    subject = extract_subject(proposed_change) or proposed_change

    queries = [
        proposed_change,
        subject,
        f"{subject} incident failure outage rollback postmortem",
        f"{subject} architecture decision constraint requirement",
        f"{subject} dependency tenants migration status",
        f"{subject} lesson learned validated approach safeguards",
    ]

    seen = set()
    out = []

    for query in queries:

        key = query.lower().strip()

        if key and key not in seen:
            seen.add(key)
            out.append(query)

    return out


def _interleave_dedupe(
    result_lists: list[list[str]],
    limit: int,
) -> list[str]:
    """
    Merge multiple recall result lists fairly.

    Results are taken round-robin from each query and duplicates
    are removed.
    """

    merged = []
    seen = set()

    depth = max(
        (len(result_list) for result_list in result_lists),
        default=0,
    )

    for i in range(depth):

        for result_list in result_lists:

            if i >= len(result_list):
                continue

            item = result_list[i]

            key = " ".join(item.lower().split())

            if key in seen:
                continue

            seen.add(key)
            merged.append(item)

            if len(merged) >= limit:
                return merged

    return merged


def _meaningful_tokens(text: str) -> set[str]:
    """
    Extract meaningful engineering-related words.

    Generic action words such as 'remove', 'change', 'system', etc.
    are ignored because they don't identify the actual component.
    """

    stop_words = {
        "the",
        "and",
        "or",
        "to",
        "of",
        "a",
        "an",
        "with",
        "for",
        "from",
        "in",
        "on",
        "all",
        "this",
        "that",
        "these",
        "those",
        "new",
        "old",
        "legacy",
        "remove",
        "delete",
        "replace",
        "migrate",
        "migration",
        "refactor",
        "rewrite",
        "change",
        "changes",
        "add",
        "added",
        "update",
        "updated",
        "use",
        "using",
        "into",
        "system",
        "service",
        "integration",
        "implementation",
        "proposed",
        "proposal",
    }

    tokens = re.findall(
        r"[a-zA-Z][a-zA-Z0-9_-]{2,}",
        text.lower(),
    )

    return {
        token
        for token in tokens
        if token not in stop_words
    }


def _filter_relevant_memories(
    proposed_change: str,
    memories: list[str],
) -> list[str]:
    """
    Safety gate between Hindsight retrieval and the audit agent.

    Hindsight uses semantic retrieval, so it may return the closest
    memories even when the proposal belongs to a different domain.

    Example:

        Proposal:
            Remove payment gateway

        Retrieved memory:
            Legacy authentication adapter

    These should NOT be treated as evidence for each other.

    This function therefore requires meaningful lexical overlap
    between the proposal and retrieved historical evidence.

    If no meaningful overlap exists, return an empty list.
    """

    if not memories:
        return []

    proposal_tokens = _meaningful_tokens(proposed_change)

    if not proposal_tokens:
        return []

    relevant = []

    for memory in memories:

        memory_tokens = _meaningful_tokens(memory)

        overlap = proposal_tokens.intersection(memory_tokens)

        if overlap:
            relevant.append(memory)

    return relevant


def retrieve_relevant_memories(
    proposed_change: str,
    top_k: int = 12,
) -> RetrievalResult:
    """
    Retrieve historical engineering memory relevant to a proposed change.

    Pipeline:

        Proposal
           ↓
        Hindsight recall
           ↓
        Deduplicate
           ↓
        Relevance gate
           ↓
        Relevant evidence only

    No evidence is fabricated.
    """

    if not proposed_change or not proposed_change.strip():

        return RetrievalResult(
            status="error",
            message="No proposed change was provided.",
        )

    try:

        client = _get_client()

    except Exception as e:

        return RetrievalResult(
            status="unavailable",
            message=f"{HINDSIGHT_UNAVAILABLE} ({e})",
        )

    per_query = []
    errors = []

    # -------------------------------------------------------------
    # Query Hindsight from multiple angles
    # -------------------------------------------------------------

    for query in _build_queries(proposed_change):

        try:

            raw = client.recall(
                bank_id=BANK_ID,
                query=query,
                max_tokens=4096,
                budget="high",
            )

        except Exception as e:

            errors.append(e)
            continue

        normalized = _normalize_recall_response(raw)

        if normalized:
            per_query.append(normalized)

    # -------------------------------------------------------------
    # If every Hindsight query failed
    # -------------------------------------------------------------

    if not per_query and errors:

        return RetrievalResult(
            status="unavailable",
            message=f"{HINDSIGHT_UNAVAILABLE} ({errors[-1]})",
        )

    # -------------------------------------------------------------
    # Merge and deduplicate Hindsight results
    # -------------------------------------------------------------

    merged = _interleave_dedupe(
        per_query,
        top_k,
    )

    if not merged:

        return RetrievalResult(
            status="empty",
            message=NO_MEMORY_FOUND,
        )

    # -------------------------------------------------------------
    # IMPORTANT:
    # Filter out unrelated historical memories.
    # -------------------------------------------------------------

    relevant = _filter_relevant_memories(
        proposed_change,
        merged,
    )

    if not relevant:

        return RetrievalResult(
            status="empty",
            message=NO_MEMORY_FOUND,
        )

    # -------------------------------------------------------------
    # Return only relevant historical evidence.
    # -------------------------------------------------------------

    return RetrievalResult(
        status="ok",
        evidence=relevant,
    )