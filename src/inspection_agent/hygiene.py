"""Commit-candidate secret and runtime-artifact hygiene checks for CI."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FORBIDDEN_TRACKED_NAMES = {".env", ".pypirc"}
FORBIDDEN_TRACKED_PARTS = {"__pycache__", ".pytest_cache", "uploads"}
FORBIDDEN_TRACKED_SUFFIXES = {".db", ".db-wal", ".db-shm", ".pyc"}
SECRET_PATTERNS = {
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def candidate_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [root / item.decode("utf-8") for item in output.split(b"\0") if item]


def scan_repository(root: Path) -> list[str]:
    findings: list[str] = []
    for path in candidate_files(root):
        relative = path.relative_to(root)
        parts = set(relative.parts)
        normalized = relative.as_posix()
        if (
            relative.name in FORBIDDEN_TRACKED_NAMES
            or parts & FORBIDDEN_TRACKED_PARTS
            or any(normalized.startswith(prefix) for prefix in ("data/runtime/",))
            or any(normalized.endswith(suffix) for suffix in FORBIDDEN_TRACKED_SUFFIXES)
        ):
            findings.append(f"forbidden commit-candidate runtime/secret path: {normalized}")
            continue
        if path.stat().st_size > 2_000_000:
            continue
        content = path.read_bytes()
        if b"\0" in content:
            continue
        text = content.decode("utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"possible {name} in commit-candidate file: {normalized}")
        if normalized == ".env.example":
            for line in text.splitlines():
                if line.startswith("INSPECTION_OPENAI_API_KEY=") and line.split("=", 1)[1].strip():
                    findings.append(".env.example contains a non-empty OpenAI API key")
    return findings


def assert_repository_hygiene(root: Path) -> None:
    findings = scan_repository(root)
    if findings:
        raise RuntimeError("repository hygiene check failed:\n- " + "\n- ".join(findings))
