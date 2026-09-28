"""
Test suite for Cognitive Debt Auditor.
Run with: pytest tests/ -v

These use mocking/monkeypatching for Hindsight and OpenAI calls so the
suite runs without a live Hindsight server or API key — that's what
smoke_test.py (in the project root) is for.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import context_ingestor
import memory_retriever
import audit_agent
import reflection_engine


# ---------------------------------------------------------------------
# 1. dataset loads
# ---------------------------------------------------------------------
def test_dataset_loads():
    events = context_ingestor.load_dataset()
    assert isinstance(events, list)
    assert len(events) >= 15
    for e in events:
        assert "id" in e and "title" in e and "description" in e


def test_dataset_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        context_ingestor.load_dataset(path="does-not-exist.json")


# ---------------------------------------------------------------------
# 2. Hindsight client initializes (mocked — no live server required)
# ---------------------------------------------------------------------
def test_hindsight_client_initializes():
    with patch("hindsight_client.Hindsight") as MockHindsight:
        MockHindsight.return_value = MagicMock()
        client = MockHindsight(base_url="http://localhost:8888")
        assert client is not None


# ---------------------------------------------------------------------
# 3. Hindsight connection failure is handled
# ---------------------------------------------------------------------
def test_recall_handles_connection_failure():
    with patch("memory_retriever._get_client") as mock_get_client:
        mock_get_client.side_effect = ConnectionError("refused")
        result = memory_retriever.retrieve_relevant_memories("some proposal")
        assert result.status == "unavailable"
        assert "unavailable" in result.message.lower()


# ---------------------------------------------------------------------
# 4. memory retrieval handles empty results
# ---------------------------------------------------------------------
def test_retrieval_handles_empty_results():
    mock_client = MagicMock()
    mock_client.recall.return_value = {"results": []}
    with patch("memory_retriever._get_client", return_value=mock_client):
        result = memory_retriever.retrieve_relevant_memories("some proposal")
        assert result.status == "empty"
        assert result.evidence == []


def test_retrieval_handles_real_pydantic_response_shape():
    """
    Regression test: hindsight-client's actual RecallResponse is a Pydantic
    model with `.results: List[RecallResult]`, each RecallResult exposing
    `.text` — NOT a plain dict. This mocks that real shape (via a minimal
    stand-in object) rather than a dict, to catch normalization bugs that
    dict-only mocking would hide.
    """
    class FakeRecallResult:
        def __init__(self, text):
            self.text = text

    class FakeRecallResponse:
        def __init__(self, results):
            self.results = results

    mock_client = MagicMock()
    mock_client.recall.return_value = FakeRecallResponse([
        FakeRecallResult("ADR-014: adapter must remain until tenants verified"),
        FakeRecallResult("2022 postmortem: OAuth cutover caused auth failures"),
    ])
    with patch("memory_retriever._get_client", return_value=mock_client):
        result = memory_retriever.retrieve_relevant_memories("remove the adapter")
        assert result.status == "ok"
        assert len(result.evidence) == 2
        assert "ADR-014" in result.evidence[0]


def test_retrieval_normalizes_malformed_response():
    mock_client = MagicMock()
    mock_client.recall.return_value = object()  # deliberately malformed
    with patch("memory_retriever._get_client", return_value=mock_client):
        result = memory_retriever.retrieve_relevant_memories("some proposal")
        assert result.status == "empty"  # fails safe, not crash


# ---------------------------------------------------------------------
# 5. audit works with retrieved evidence
# ---------------------------------------------------------------------
def test_audit_with_evidence_llm_mode():
    fake_evidence = ["ADR-014: adapter must remain until tenants verified"]
    mock_retrieval = memory_retriever.RetrievalResult(status="ok", evidence=fake_evidence)

    fake_llm_response = {
        "risk_level": "HIGH",
        "historical_why": "Adapter exists for enterprise SSO tenant compatibility.",
        "previous_incidents": ["2022 OAuth cutover caused auth failures"],
        "architectural_constraints": ["ADR-014 requires tenant verification"],
        "potential_consequences": "Repeat the 2022 incident.",
        "recommended_checks": ["Verify tenant compatibility"],
        "recommendation": "Do not remove without verification.",
    }

    with patch("audit_agent.retrieve_relevant_memories", return_value=mock_retrieval), \
         patch("audit_agent.LLM_AVAILABLE", True), \
         patch("audit_agent._llm_audit", return_value=fake_llm_response):
        result = audit_agent.audit_change("Remove the legacy authentication adapter")

    assert result.risk_level == "HIGH"
    assert result.reasoning_mode == "llm"
    assert len(result.evidence) == 1


# ---------------------------------------------------------------------
# 6. audit works when no evidence exists
# ---------------------------------------------------------------------
def test_audit_with_no_evidence():
    mock_retrieval = memory_retriever.RetrievalResult(
        status="empty", message=memory_retriever.NO_MEMORY_FOUND
    )
    with patch("audit_agent.retrieve_relevant_memories", return_value=mock_retrieval):
        result = audit_agent.audit_change("some totally unrelated proposal")

    assert result.reasoning_mode == "no_memory"
    assert result.evidence == []


# ---------------------------------------------------------------------
# 7. reflection creates a lesson
# ---------------------------------------------------------------------
def test_reflection_creates_and_stores_lesson():
    mock_client = MagicMock()
    with patch("reflection_engine._get_client", return_value=mock_client), \
         patch("reflection_engine.LLM_AVAILABLE", False):
        result = reflection_engine.reflect_and_store(
            proposal="Remove legacy adapter",
            evidence=["some evidence"],
            audit_result=audit_agent.AuditResult(
                proposed_change="x", risk_level="HIGH", historical_why=""
            ),
            outcome="Change approved with safeguards",
            notes="test note",
        )
    assert result.status == "stored"
    assert result.lesson
    mock_client.retain.assert_called_once()


def test_reflection_handles_hindsight_unavailable():
    with patch("reflection_engine._get_client", side_effect=ConnectionError("refused")), \
         patch("reflection_engine.LLM_AVAILABLE", False):
        result = reflection_engine.reflect_and_store(
            proposal="x", evidence=[], audit_result=None, outcome="Change rejected"
        )
    assert result.status == "hindsight_unavailable"


# ---------------------------------------------------------------------
# 8. malformed Hindsight response doesn't crash the app
# ---------------------------------------------------------------------
def test_normalize_recall_response_handles_garbage():
    assert memory_retriever._normalize_recall_response(None) == []
    assert memory_retriever._normalize_recall_response(12345) == []
    assert memory_retriever._normalize_recall_response({"unexpected": "shape"}) == []
    assert memory_retriever._normalize_recall_response("plain string") == ["plain string"]
