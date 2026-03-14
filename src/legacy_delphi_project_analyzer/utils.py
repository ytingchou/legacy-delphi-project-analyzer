from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import DiagnosticRecord, SourceLocation, to_jsonable


TEXT_ENCODINGS = ["utf-8", "utf-8-sig", "cp950", "big5", "cp1252", "latin-1"]
PLACEHOLDER_RE = re.compile(r":([A-Za-z][A-Za-z0-9_]*)")


def read_text_file(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8", True


def is_binary_dfm(path: Path) -> bool:
    raw = path.read_bytes()[:256]
    return raw.startswith(b"TPF0") or b"\x00" in raw


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_directory(path.parent)
    path.write_text(
        json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, content: str) -> None:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "artifact"


def trim_sql_snippet(sql: str, limit: int = 160) -> str:
    compact = " ".join(sql.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def make_diagnostic(
    severity: str,
    code: str,
    message: str,
    file_path: str | None = None,
    line: int | None = None,
    context: str | None = None,
    suggestion: str | None = None,
    prompt_hint: str | None = None,
    details: dict[str, Any] | None = None,
) -> DiagnosticRecord:
    location = SourceLocation(file_path=file_path, line=line) if file_path else None
    return DiagnosticRecord(
        severity=severity,
        code=code,
        message=message,
        location=location,
        context=context,
        suggestion=suggestion,
        prompt_hint=prompt_hint,
        details=details or {},
    )


def split_text_chunks(content: str, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in content.splitlines(keepends=True):
        if current and current_len + len(line) > max_chars:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def estimate_tokens(content: str) -> int:
    if not content:
        return 0
    # Fast heuristic for GPT/Qwen-class tokenization across mixed code and prose.
    return max(1, math.ceil(len(content) / 4))


def split_text_chunks_by_budget(
    content: str,
    max_chars: int,
    max_tokens: int,
) -> list[str]:
    if len(content) <= max_chars and estimate_tokens(content) <= max_tokens:
        return [content]
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    current_tokens = 0
    for line in content.splitlines(keepends=True):
        line_tokens = estimate_tokens(line)
        if current and (current_chars + len(line) > max_chars or current_tokens + line_tokens > max_tokens):
            chunks.append("".join(current))
            current = []
            current_chars = 0
            current_tokens = 0
        current.append(line)
        current_chars += len(line)
        current_tokens += line_tokens
    if current:
        chunks.append("".join(current))
    return chunks
