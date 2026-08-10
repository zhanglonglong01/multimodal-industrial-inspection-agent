from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from inspection_agent.config import PROJECT_ROOT, Settings


def test_settings_resolve_relative_paths_inside_project() -> None:
    settings = Settings(
        app_env="test",
        data_dir=Path("tmp/demo-data"),
        database_path=Path("tmp/demo.db"),
    )

    assert settings.data_dir == (PROJECT_ROOT / "tmp/demo-data").resolve()
    assert settings.database_path == (PROJECT_ROOT / "tmp/demo.db").resolve()


def test_settings_default_database_is_below_data_dir(tmp_path: Path) -> None:
    settings = Settings(app_env="test", data_dir=tmp_path)

    assert settings.database_path == tmp_path / "runtime" / "inspection_agent.db"


def test_settings_reject_invalid_log_level() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        Settings(log_level="verbose")
