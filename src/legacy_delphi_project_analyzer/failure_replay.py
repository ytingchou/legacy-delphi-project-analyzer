from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import AnalysisOutput
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_failure_replay_lab(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    output: AnalysisOutput,
) -> dict[str, Any]:
    replay_dir = runtime_dir / "failure-replay"
    ensure_directory(replay_dir)
    validation_results = _load_json(runtime_dir / "validation-results.json") or []
    runtime_errors = (output.runtime_error_summary or {}).get("items", [])
    task_dirs = sorted((runtime_dir / "taskpacks").glob("*"))
    entries: list[dict[str, Any]] = []

    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        validation = next(
            (
                item for item in validation_results
                if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
            ),
            {},
        )
        status = str(validation.get("status") or "")
        if status in {"accepted", "accepted_with_warnings"}:
            continue
        error_item = next(
            (
                item for item in runtime_errors
                if isinstance(item, dict) and str(item.get("task_id") or "") == task_id
            ),
            {},
        )
        entry_dir = replay_dir / task_id
        ensure_directory(entry_dir)
        payload = {
            "task_id": task_id,
            "task_type": validation.get("task_type"),
            "module_name": validation.get("module_name"),
            "subject_name": validation.get("subject_name"),
            "status": status or "unvalidated",
            "rejection_category": validation.get("rejection_category") or error_item.get("category") or "unknown",
            "response_file": _existing_file(task_dir / "agent-response.json", runtime_dir / "cline-outbox" / task_id / "response.json"),
            "compiled_context_file": (task_dir / "compiled-context.md").as_posix(),
            "prompt_file": (task_dir / "primary-prompt.txt").as_posix(),
            "validation_file": (task_dir / "validation-result.json").as_posix(),
            "retry_plan_file": (task_dir / "retry-plan.md").as_posix(),
            "repair_strategy": _repair_strategy(validation, error_item),
            "suggested_next_command": f"legacy-delphi-analyzer retry-plan {analysis_dir} {task_id}",
        }
        write_json(entry_dir / "manifest.json", payload)
        write_text(entry_dir / "replay.md", _render_replay_markdown(payload))
        entries.append(payload)

    counts_by_category: dict[str, int] = {}
    for item in entries:
        category = str(item.get("rejection_category") or "unknown")
        counts_by_category[category] = counts_by_category.get(category, 0) + 1

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "entry_count": len(entries),
        "counts_by_category": counts_by_category,
        "entries": entries,
    }
    write_json(replay_dir / "manifest.json", manifest)
    write_text(replay_dir / "README.md", _render_replay_summary(manifest))
    return manifest


def _repair_strategy(validation: dict[str, Any], error_item: dict[str, Any]) -> list[str]:
    category = str(validation.get("rejection_category") or error_item.get("category") or "")
    if category == "schema_error":
        return ["Keep the exact same task scope.", "Return strict JSON only.", "Do not add explanatory prose."]
    if category in {"unsupported_claims", "missing_evidence"}:
        return ["Remove unsupported claims.", "Keep uncertain points in remaining_unknowns.", "Do not broaden the context."]
    if category == "follow_up_required":
        return ["Use the retry-plan repair prompt.", "Ask for one missing fact only."]
    return ["Retry the same bounded task.", "Do not add unrelated artifacts."]


def _render_replay_markdown(payload: dict[str, Any]) -> str:
    strategy = "\n".join(f"- {item}" for item in payload["repair_strategy"])
    return f"""# Failure Replay: {payload['task_id']}

- Type: {payload.get('task_type') or 'unknown'}
- Status: {payload.get('status') or 'unknown'}
- Module: {payload.get('module_name') or 'None'}
- Subject: {payload.get('subject_name') or 'None'}
- Category: {payload.get('rejection_category') or 'unknown'}

## Files

- Prompt: `{payload.get('prompt_file')}`
- Compiled context: `{payload.get('compiled_context_file')}`
- Response: `{payload.get('response_file')}`
- Validation: `{payload.get('validation_file')}`
- Retry plan: `{payload.get('retry_plan_file')}`

## Repair Strategy

{strategy}

## Suggested Next Command

`{payload.get('suggested_next_command')}`
"""


def _render_replay_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# Failure Replay Lab",
        "",
        f"- Replay entries: {manifest.get('entry_count', 0)}",
        "",
        "## Counts By Category",
        "",
    ]
    counts = manifest.get("counts_by_category", {})
    if isinstance(counts, dict) and counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- None")
    return "\n".join(lines).strip() + "\n"


def _existing_file(primary: Path, fallback: Path) -> str:
    if primary.exists():
        return primary.as_posix()
    if fallback.exists():
        return fallback.as_posix()
    return primary.as_posix()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
