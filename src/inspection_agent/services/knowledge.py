"""Local maintenance-document ingestion, FAISS indexing, and retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

import faiss
import numpy as np

from ..analysis_schemas import (
    KnowledgeChunk,
    KnowledgeIndexMetadata,
    KnowledgeSection,
    LoadedKnowledgeDocument,
    RetrievedKnowledgeChunk,
)
from ..config import Settings
from ..schemas import AssetType, KnowledgeManifest


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")
    if not slug:
        raise ValueError(f"cannot create an identifier from {value!r}")
    return slug


class MaintenanceDocumentLoader:
    """Load versioned Markdown documents and retain their section provenance."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def load(self) -> list[LoadedKnowledgeDocument]:
        manifest_path = self.settings.knowledge_manifest_path
        if not manifest_path.is_file():
            raise FileNotFoundError(f"knowledge manifest not found: {manifest_path}")
        manifest = KnowledgeManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
        documents: list[LoadedKnowledgeDocument] = []
        for declared in manifest.documents:
            source_path = self.settings.data_dir / declared.path
            if not source_path.is_file():
                raise FileNotFoundError(f"knowledge document not found: {source_path}")
            sections = self._parse_sections(source_path.read_text(encoding="utf-8"))
            documents.append(
                LoadedKnowledgeDocument(
                    doc_id=declared.document_id,
                    title=declared.title,
                    version=declared.version,
                    source=declared.path,
                    document_type=declared.document_type,
                    asset_types=declared.asset_types,
                    sections=sections,
                )
            )
        return documents

    @staticmethod
    def _parse_sections(markdown: str) -> list[KnowledgeSection]:
        sections: list[KnowledgeSection] = []
        current_heading: str | None = "Document notice"
        current_lines: list[str] = []

        def flush() -> None:
            if current_heading is None:
                return
            text = "\n".join(current_lines).strip()
            if text:
                sections.append(KnowledgeSection(section=current_heading, text=text))

        for line in markdown.splitlines():
            if line.startswith("# "):
                continue
            if line.startswith("## "):
                flush()
                current_heading = line[3:].strip()
                current_lines = []
            elif current_heading is not None:
                current_lines.append(line)
        flush()
        if not sections:
            raise ValueError("knowledge document contains no level-two Markdown sections")
        return sections


class MaintenanceDocumentChunker:
    """Split sections into bounded word chunks without losing source metadata."""

    def __init__(
        self,
        *,
        index_version: str = "maintenance-knowledge-v1",
        max_characters: int = 700,
        overlap_words: int = 20,
    ) -> None:
        if max_characters < 200:
            raise ValueError("max_characters must be at least 200")
        if overlap_words < 0:
            raise ValueError("overlap_words must be non-negative")
        self.index_version = index_version
        self.max_characters = max_characters
        self.overlap_words = overlap_words

    def chunk(self, documents: list[LoadedKnowledgeDocument]) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        for document in documents:
            for section in document.sections:
                for part_number, part in enumerate(
                    self._split_text(section.text), start=1
                ):
                    chunk_id = (
                        f"CHUNK-{document.doc_id}-{_slug(section.section)}-"
                        f"{part_number:03d}"
                    )
                    chunks.append(
                        KnowledgeChunk(
                            chunk_id=chunk_id,
                            doc_id=document.doc_id,
                            title=document.title,
                            section=section.section,
                            source=document.source,
                            text=part,
                            asset_types=document.asset_types,
                            index_version=self.index_version,
                            evidence_id=f"EVIDENCE-KNOWLEDGE-{chunk_id}",
                        )
                    )
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("chunker produced duplicate chunk IDs")
        return chunks

    def _split_text(self, text: str) -> list[str]:
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= self.max_characters:
            return [compact]
        words = compact.split()
        parts: list[str] = []
        cursor = 0
        while cursor < len(words):
            end = cursor
            length = 0
            while end < len(words):
                addition = len(words[end]) + (1 if length else 0)
                if length + addition > self.max_characters and end > cursor:
                    break
                length += addition
                end += 1
            parts.append(" ".join(words[cursor:end]))
            if end >= len(words):
                break
            next_cursor = max(cursor + 1, end - self.overlap_words)
            cursor = next_cursor
        return parts


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    version: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        """Embed document strings as normalized float32 rows."""

    def embed_query(self, query: str) -> np.ndarray:
        """Embed one query as a normalized float32 row."""


class DeterministicHashEmbedding:
    """Small API-key-free hashing embedding for reproducible local retrieval.

    It is a lexical feature-hashing baseline, not a learned semantic model. Unigrams
    and adjacent bigrams are SHA-256 mapped into a fixed vector and L2 normalized.
    """

    name = "deterministic_hash_embedding"
    version = "1.0"

    def __init__(self, dimension: int = 512) -> None:
        if dimension < 64:
            raise ValueError("embedding dimension must be at least 64")
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts)

    def embed_query(self, query: str) -> np.ndarray:
        return self._embed([query])

    def _embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row_index, text in enumerate(texts):
            tokens = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
            features = [*tokens, *(f"{a}::{b}" for a, b in zip(tokens, tokens[1:]))]
            for feature in features:
                digest = hashlib.sha256(feature.encode("utf-8")).digest()
                feature_index = int.from_bytes(digest[:8], "big") % self.dimension
                matrix[row_index, feature_index] += 1.0
            norm = math.sqrt(float(np.dot(matrix[row_index], matrix[row_index])))
            if norm > 0:
                matrix[row_index] /= norm
        return matrix


class KnowledgeIndexBuilder:
    index_filename = "index.faiss"
    metadata_filename = "metadata.json"

    def __init__(
        self,
        settings: Settings,
        embedding: EmbeddingProvider | None = None,
        chunker: MaintenanceDocumentChunker | None = None,
    ) -> None:
        self.settings = settings
        self.embedding = embedding or DeterministicHashEmbedding()
        self.chunker = chunker or MaintenanceDocumentChunker()

    def build(self, output_dir: Path | None = None) -> KnowledgeIndexMetadata:
        destination = (output_dir or self.settings.knowledge_index_dir).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        documents = MaintenanceDocumentLoader(self.settings).load()
        chunks = self.chunker.chunk(documents)
        vectors = self.embedding.embed_documents([chunk.text for chunk in chunks])
        index = faiss.IndexFlatIP(self.embedding.dimension)
        index.add(np.ascontiguousarray(vectors, dtype=np.float32))
        faiss.write_index(index, str(destination / self.index_filename))

        metadata = KnowledgeIndexMetadata(
            index_version=self.chunker.index_version,
            embedding_provider=self.embedding.name,
            embedding_version=self.embedding.version,
            embedding_dimension=self.embedding.dimension,
            chunk_count=len(chunks),
            chunks=chunks,
        )
        (destination / self.metadata_filename).write_text(
            metadata.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return metadata


class KnowledgeRetriever:
    """Search a FAISS index while resolving every hit through JSON metadata."""

    def __init__(
        self,
        index_dir: Path,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        self.index_dir = Path(index_dir).resolve()
        self.embedding = embedding or DeterministicHashEmbedding()
        metadata_path = self.index_dir / KnowledgeIndexBuilder.metadata_filename
        index_path = self.index_dir / KnowledgeIndexBuilder.index_filename
        if not metadata_path.is_file():
            raise FileNotFoundError(f"knowledge metadata not found: {metadata_path}")
        if not index_path.is_file():
            raise FileNotFoundError(f"FAISS index not found: {index_path}")
        self.metadata = KnowledgeIndexMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        if self.metadata.embedding_provider != self.embedding.name:
            raise ValueError("embedding provider does not match index metadata")
        if self.metadata.embedding_version != self.embedding.version:
            raise ValueError("embedding version does not match index metadata")
        if self.metadata.embedding_dimension != self.embedding.dimension:
            raise ValueError("embedding dimension does not match index metadata")
        self.index = faiss.read_index(str(index_path))
        if self.index.ntotal != self.metadata.chunk_count:
            raise ValueError("FAISS vector count does not match chunk metadata")

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        asset_type: AssetType | str | None = None,
        minimum_score: float = 0.0,
    ) -> list[RetrievedKnowledgeChunk]:
        if not query.strip():
            raise ValueError("retrieval query cannot be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        normalized_asset_type = AssetType(asset_type) if asset_type else None
        query_vector = np.ascontiguousarray(
            self.embedding.embed_query(query), dtype=np.float32
        )
        scores, indices = self.index.search(query_vector, self.metadata.chunk_count)
        results: list[RetrievedKnowledgeChunk] = []
        for score, index_position in zip(scores[0], indices[0], strict=True):
            if index_position < 0:
                continue
            chunk = self.metadata.chunks[int(index_position)]
            if (
                normalized_asset_type is not None
                and normalized_asset_type not in chunk.asset_types
            ):
                continue
            numeric_score = float(score)
            if numeric_score < minimum_score:
                continue
            results.append(
                RetrievedKnowledgeChunk(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    title=chunk.title,
                    section=chunk.section,
                    score=numeric_score,
                    excerpt=chunk.text[:300],
                    source=chunk.source,
                    index_version=chunk.index_version,
                    evidence_id=chunk.evidence_id,
                )
            )
            if len(results) == top_k:
                break
        return results

    def get_chunk(self, chunk_id: str) -> KnowledgeChunk | None:
        return next(
            (chunk for chunk in self.metadata.chunks if chunk.chunk_id == chunk_id),
            None,
        )
