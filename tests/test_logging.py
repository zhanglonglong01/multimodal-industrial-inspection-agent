from __future__ import annotations

import json
import logging

from inspection_agent.logging_config import JsonFormatter


def test_json_formatter_emits_structured_event_context() -> None:
    record = logging.LogRecord(
        name="inspection_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="seed complete",
        args=(),
        exc_info=None,
    )
    record.event = "demo_seed_completed"
    record.context = {"assets": 2, "scenarios": 3}

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["event"] == "demo_seed_completed"
    assert payload["assets"] == 2
    assert payload["scenarios"] == 3
    assert payload["timestamp"].endswith("+00:00")
