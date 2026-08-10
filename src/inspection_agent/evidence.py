"""Unified evidence validation for independent analysis outputs."""

from __future__ import annotations

from .analysis_schemas import EvidenceRef


def ensure_unique_evidence(references: list[EvidenceRef]) -> list[EvidenceRef]:
    """Validate that an evidence bundle has unique IDs and source references."""

    evidence_ids = [reference.evidence_id for reference in references]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence IDs must be unique within a bundle")
    source_keys = [
        (reference.kind.value, reference.source_id) for reference in references
    ]
    if len(source_keys) != len(set(source_keys)):
        raise ValueError("evidence sources must be unique within a kind")
    return references
