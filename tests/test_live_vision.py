from __future__ import annotations

import os
from pathlib import Path

import pytest

from inspection_agent.config import Settings
from inspection_agent.vision_evaluation import run_live_vision_smoke


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to allow paid real Vision calls",
)
def test_three_image_openai_vision_smoke(
    seeded_demo: Settings,
    tmp_path: Path,
) -> None:
    report = run_live_vision_smoke(
        seeded_demo,
        output_path=tmp_path / "live_vision_smoke.json",
    )

    assert report.sample_count == 3
    assert report.schema_success_count == 3
    assert all(item.latency_ms > 0 for item in report.samples)
