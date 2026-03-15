from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.taskpacks import TaskPack
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json


def emit_cline_task(taskpack: TaskPack, task_dir: Path, runtime_dir: Path) -> Path:
    inbox_dir = runtime_dir / "cline-inbox" / taskpack.task_id
    ensure_directory(inbox_dir)
    request_path = inbox_dir / "request.json"
    write_json(
        request_path,
        {
            "task_id": taskpack.task_id,
            "task_type": taskpack.task_type,
            "context_dir": task_dir.as_posix(),
            "target_model_profile": taskpack.target_model_profile,
            "module_name": taskpack.module_name,
            "subject_name": taskpack.subject_name,
        },
    )
    return request_path


def collect_cline_response(task_id: str, runtime_dir: Path) -> dict[str, Any] | None:
    response_path = runtime_dir / "cline-outbox" / task_id / "response.json"
    if not response_path.exists():
        return None
    try:
        payload = json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def write_cline_response(task_id: str, runtime_dir: Path, payload: dict[str, Any]) -> Path:
    response_dir = runtime_dir / "cline-outbox" / task_id
    ensure_directory(response_dir)
    response_path = response_dir / "response.json"
    write_json(response_path, payload)
    return response_path
