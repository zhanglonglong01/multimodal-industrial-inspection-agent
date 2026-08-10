from __future__ import annotations

import pytest

from inspection_agent.analysis_schemas import EvidenceKind, EvidenceRef
from inspection_agent.config import Settings
from inspection_agent.evidence import ensure_unique_evidence
from inspection_agent.phase2 import run_scenario_analysis
from inspection_agent.services.knowledge import KnowledgeRetriever


def test_all_analysis_outputs_convert_to_unique_evidence(
    seeded_demo: Settings,
    knowledge_retriever: KnowledgeRetriever,
) -> None:
    analysis = run_scenario_analysis(
        seeded_demo,
        "SCENARIO-001",
        knowledge_retriever,
    )

    assert {reference.kind for reference in analysis.evidence} == {
        EvidenceKind.VISION,
        EvidenceKind.SENSOR,
        EvidenceKind.KNOWLEDGE,
    }
    assert len({item.evidence_id for item in analysis.evidence}) == len(
        analysis.evidence
    )
    assert ensure_unique_evidence(analysis.evidence) == analysis.evidence


def test_normal_scenario_retrieves_normal_guidance(
    seeded_demo: Settings,
    knowledge_retriever: KnowledgeRetriever,
) -> None:
    analysis = run_scenario_analysis(
        seeded_demo,
        "SCENARIO-003",
        knowledge_retriever,
    )

    assert analysis.sensor.segments == []
    assert analysis.failure_modes == []
    assert (
        analysis.knowledge[0].chunk_id
        == "CHUNK-KNOW-INSPECTION-SOP-NORMAL-SCENARIO-001"
    )


def test_evidence_id_kind_must_match() -> None:
    with pytest.raises(ValueError, match="prefix must match"):
        EvidenceRef(
            evidence_id="EVIDENCE-SENSOR-SOURCE-001",
            kind=EvidenceKind.VISION,
            source_id="SOURCE-001",
            summary="Mismatch",
            observed_at=None,
        )


def test_duplicate_evidence_is_rejected() -> None:
    reference = EvidenceRef(
        evidence_id="EVIDENCE-KNOWLEDGE-SOURCE-001",
        kind=EvidenceKind.KNOWLEDGE,
        source_id="SOURCE-001",
        summary="Knowledge evidence",
        observed_at=None,
    )

    with pytest.raises(ValueError, match="must be unique"):
        ensure_unique_evidence([reference, reference])
