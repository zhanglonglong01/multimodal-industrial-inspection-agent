"""Environment-backed application settings for the local demo."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-backed settings with absolute local persistence paths."""

    app_name: str = "Multimodal Industrial Inspection Agent"
    app_mode: Literal["demo", "live"] = Field(
        default="demo",
        validation_alias=AliasChoices("APP_MODE", "INSPECTION_APP_MODE"),
    )
    vision_provider: Literal["fixture", "openai"] = Field(
        default="fixture",
        validation_alias=AliasChoices("VISION_PROVIDER", "INSPECTION_VISION_PROVIDER"),
    )
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    database_path: Path | None = None
    checkpoint_path: Path | None = None
    random_seed: int = 20_260_811
    openai_api_key: SecretStr | None = None
    openai_diagnosis_model: str = "gpt-5-mini"
    openai_vision_model: str = "gpt-5-mini"
    max_upload_bytes: int = 5 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="INSPECTION_",
        extra="ignore",
        case_sensitive=False,
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return normalized

    @field_validator("random_seed")
    @classmethod
    def validate_seed(cls, value: int) -> int:
        if value < 0:
            raise ValueError("random_seed must be non-negative")
        return value

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def blank_api_key_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def resolve_paths(self) -> "Settings":
        if not self.data_dir.is_absolute():
            self.data_dir = (PROJECT_ROOT / self.data_dir).resolve()
        else:
            self.data_dir = self.data_dir.resolve()

        if self.database_path is None:
            self.database_path = self.data_dir / "runtime" / "inspection_agent.db"
        elif not self.database_path.is_absolute():
            self.database_path = (PROJECT_ROOT / self.database_path).resolve()
        else:
            self.database_path = self.database_path.resolve()

        if self.checkpoint_path is None:
            self.checkpoint_path = self.data_dir / "runtime" / "langgraph_checkpoints.db"
        elif not self.checkpoint_path.is_absolute():
            self.checkpoint_path = (PROJECT_ROOT / self.checkpoint_path).resolve()
        else:
            self.checkpoint_path = self.checkpoint_path.resolve()
        return self

    @property
    def scenarios_dir(self) -> Path:
        return self.data_dir / "demo" / "scenarios"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "demo" / "fixtures"

    @property
    def image_manifest_path(self) -> Path:
        return self.fixtures_dir / "image_manifest.json"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def knowledge_manifest_path(self) -> Path:
        return self.knowledge_dir / "manifest.json"

    @property
    def knowledge_index_dir(self) -> Path:
        return self.data_dir / "runtime" / "knowledge_index"

    @property
    def failure_modes_path(self) -> Path:
        return self.data_dir / "failure_modes" / "failure_modes.json"

    @property
    def retrieval_evaluation_path(self) -> Path:
        return self.data_dir / "evaluation" / "retrieval_queries.json"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "runtime" / "uploads"

    def ensure_directories(self) -> None:
        assert self.database_path is not None
        assert self.checkpoint_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
