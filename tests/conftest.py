from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from inspection_agent.config import PROJECT_ROOT, Settings
from inspection_agent.demo import seed_demo
from inspection_agent.services.knowledge import KnowledgeIndexBuilder, KnowledgeRetriever


@pytest.fixture
def demo_settings(tmp_path: Path) -> Settings:
    """Create an isolated data directory containing only source fixtures."""

    data_dir = tmp_path / "data"
    shutil.copytree(PROJECT_ROOT / "data" / "knowledge", data_dir / "knowledge")
    shutil.copytree(
        PROJECT_ROOT / "data" / "demo" / "fixtures" / "images",
        data_dir / "demo" / "fixtures" / "images",
    )
    shutil.copytree(
        PROJECT_ROOT / "data" / "failure_modes",
        data_dir / "failure_modes",
    )
    shutil.copytree(
        PROJECT_ROOT / "data" / "evaluation",
        data_dir / "evaluation",
    )
    shutil.copytree(
        PROJECT_ROOT / "data" / "real" / "metropt3",
        data_dir / "real" / "metropt3",
    )
    return Settings(
        app_env="test",
        log_level="CRITICAL",
        data_dir=data_dir,
        database_path=data_dir / "runtime" / "test.db",
        random_seed=20_260_811,
    )


@pytest.fixture
def seeded_demo(demo_settings: Settings) -> Settings:
    seed_demo(demo_settings)
    return demo_settings


@pytest.fixture
def knowledge_retriever(seeded_demo: Settings) -> KnowledgeRetriever:
    KnowledgeIndexBuilder(seeded_demo).build()
    return KnowledgeRetriever(seeded_demo.knowledge_index_dir)
