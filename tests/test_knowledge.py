from __future__ import annotations

import json

import numpy as np
import pytest

from inspection_agent.config import Settings
from inspection_agent.services.knowledge import (
    DeterministicHashEmbedding,
    EmbeddingProvider,
    KnowledgeIndexBuilder,
    KnowledgeRetriever,
    MaintenanceDocumentChunker,
    MaintenanceDocumentLoader,
)


def test_document_loader_preserves_sections_and_sources(seeded_demo: Settings) -> None:
    documents = MaintenanceDocumentLoader(seeded_demo).load()

    assert len(documents) == 3
    assert sum(len(document.sections) for document in documents) == 12
    pump = next(document for document in documents if document.doc_id == "KNOW-PUMP-MANUAL")
    assert pump.source == "knowledge/pump_maintenance_manual.md"
    assert {section.section for section in pump.sections} >= {
        "Normal operating observations",
        "Seal leakage indicators",
    }


def test_chunker_creates_stable_citation_ids(seeded_demo: Settings) -> None:
    documents = MaintenanceDocumentLoader(seeded_demo).load()
    chunks = MaintenanceDocumentChunker().chunk(documents)

    assert len(chunks) == 12
    seal_chunk = next(
        chunk for chunk in chunks if chunk.section == "Seal leakage indicators"
    )
    assert (
        seal_chunk.chunk_id
        == "CHUNK-KNOW-PUMP-MANUAL-SEAL-LEAKAGE-INDICATORS-001"
    )
    assert seal_chunk.doc_id == "KNOW-PUMP-MANUAL"
    assert seal_chunk.source == "knowledge/pump_maintenance_manual.md"
    assert seal_chunk.text


def test_hash_embedding_is_local_deterministic_and_normalized() -> None:
    embedding = DeterministicHashEmbedding(dimension=128)

    first = embedding.embed_documents(["pump seal leakage", "motor bearing"])
    second = embedding.embed_documents(["pump seal leakage", "motor bearing"])

    assert isinstance(embedding, EmbeddingProvider)
    np.testing.assert_array_equal(first, second)
    np.testing.assert_allclose(np.linalg.norm(first, axis=1), [1.0, 1.0])


def test_faiss_index_keeps_independent_full_metadata(
    seeded_demo: Settings,
) -> None:
    metadata = KnowledgeIndexBuilder(seeded_demo).build()
    metadata_path = seeded_demo.knowledge_index_dir / "metadata.json"
    index_path = seeded_demo.knowledge_index_dir / "index.faiss"

    assert index_path.is_file()
    assert metadata_path.is_file()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["chunk_count"] == metadata.chunk_count == 12
    assert payload["embedding_provider"] == "deterministic_hash_embedding"
    assert all(
        {"chunk_id", "doc_id", "section", "source", "text", "index_version"}
        <= set(chunk)
        for chunk in payload["chunks"]
    )


def test_retrieval_returns_expected_chunk_with_citation_metadata(
    knowledge_retriever: KnowledgeRetriever,
) -> None:
    results = knowledge_retriever.search(
        "motor bearing vibration temperature",
        top_k=3,
        asset_type="motor",
    )

    assert results[0].chunk_id == (
        "CHUNK-KNOW-MOTOR-MANUAL-BEARING-FAULT-INDICATORS-001"
    )
    assert results[0].doc_id == "KNOW-MOTOR-MANUAL"
    assert results[0].section == "Bearing fault indicators"
    assert results[0].source == "knowledge/motor_maintenance_manual.md"
    assert results[0].index_version == "maintenance-knowledge-v1"
    assert results[0].excerpt


def test_retrieval_metadata_resolves_to_original_document(
    seeded_demo: Settings,
    knowledge_retriever: KnowledgeRetriever,
) -> None:
    result = knowledge_retriever.search("pump leakage", top_k=1, asset_type="pump")[0]
    chunk = knowledge_retriever.get_chunk(result.chunk_id)

    assert chunk is not None
    assert chunk.evidence_id == result.evidence_id
    assert (seeded_demo.data_dir / chunk.source).is_file()
    assert chunk.text in (seeded_demo.data_dir / chunk.source).read_text(encoding="utf-8")


def test_retriever_rejects_missing_metadata(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="metadata not found"):
        KnowledgeRetriever(tmp_path)
