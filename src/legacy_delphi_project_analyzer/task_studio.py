from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import AnalysisOutput, TaskStudioTask, to_jsonable
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_task_studio(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    output: AnalysisOutput,
) -> dict[str, Any]:
    studio_dir = runtime_dir / "task-studio"
    ensure_directory(studio_dir)
    tasks: list[TaskStudioTask] = []

    for taskpack in output.taskpacks:
        task_dir = runtime_dir / "taskpacks" / taskpack.task_id
        validation = _load_json(task_dir / "validation-result.json") or {}
        response_file = _resolve_response_file(runtime_dir, task_dir, taskpack.task_id)
        status = _task_status(task_dir, response_file, validation)
        task = TaskStudioTask(
            task_id=taskpack.task_id,
            task_type=taskpack.task_type,
            module_name=taskpack.module_name,
            subject_name=taskpack.subject_name,
            priority=taskpack.priority,
            status=status,
            prompt_file=(task_dir / "primary-prompt.txt").as_posix(),
            compiled_context_file=(task_dir / "compiled-context.md").as_posix(),
            expected_schema_file=(task_dir / "agent-expected-output-schema.json").as_posix(),
            response_file=response_file.as_posix() if response_file else None,
            validation_file=(task_dir / "validation-result.json").as_posix(),
            retry_plan_file=(task_dir / "retry-plan.md").as_posix(),
            copy_prompt_command=f"cat {task_dir / 'vscode-cline-copy-prompt.txt'}",
            validate_command=f"legacy-delphi-analyzer validate-response {analysis_dir} {taskpack.task_id}",
            retry_command=f"legacy-delphi-analyzer retry-plan {analysis_dir} {taskpack.task_id}",
            review_command=(
                f"legacy-delphi-analyzer review-task {analysis_dir} {taskpack.task_id} "
                f"--decision accept --reviewer <name>"
            ),
            notes=[
                f"profile={taskpack.target_model_profile}",
                f"context_budget_tokens={taskpack.context_budget_tokens}",
                f"source_prompt={taskpack.source_prompt_name or 'none'}",
            ],
        )
        tasks.append(task)
        write_json(studio_dir / f"{task.task_id}.json", task)
        write_text(studio_dir / f"{task.task_id}.md", _render_task_detail(task))

    counts_by_status: dict[str, int] = {}
    for task in tasks:
        counts_by_status[task.status] = counts_by_status.get(task.status, 0) + 1

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "task_count": len(tasks),
        "counts_by_status": counts_by_status,
        "recommended_workflow": [
            "Open one task only.",
            "Use agent-task.md, compiled-context.md, and agent-expected-output-schema.json only.",
            "Force JSON-only output from Cline/qwen3.",
            "Run validate-response before moving to the next task.",
            "Use retry-plan if validation does not pass.",
        ],
        "tasks": [to_jsonable(item) for item in tasks],
    }
    write_json(runtime_dir / "task-studio.json", payload)
    write_text(runtime_dir / "task-studio.md", render_task_studio_markdown(payload))
    return payload


def render_task_studio_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Task Studio",
        "",
        f"- Task count: {payload.get('task_count', 0)}",
        "",
        "## Counts By Status",
        "",
    ]
    counts = payload.get("counts_by_status", {})
    if isinstance(counts, dict) and counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- None")
    lines.extend(["", "## Recommended Workflow", ""])
    for item in payload.get("recommended_workflow", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Tasks", ""])
    for item in payload.get("tasks", []):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {item.get('task_id')}",
                f"- Type: {item.get('task_type')}",
                f"- Status: {item.get('status')}",
                f"- Module: {item.get('module_name') or 'None'}",
                f"- Subject: {item.get('subject_name') or 'None'}",
                f"- Validate: `{item.get('validate_command')}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _render_task_detail(task: TaskStudioTask) -> str:
    notes = "\n".join(f"- {item}" for item in task.notes) if task.notes else "- None"
    return f"""# Task Studio Detail: {task.task_id}

- Type: {task.task_type}
- Status: {task.status}
- Module: {task.module_name or 'None'}
- Subject: {task.subject_name or 'None'}
- Priority: {task.priority}

## Files

- Prompt: `{task.prompt_file or 'None'}`
- Compiled context: `{task.compiled_context_file or 'None'}`
- Expected schema: `{task.expected_schema_file or 'None'}`
- Response: `{task.response_file or 'None'}`
- Validation: `{task.validation_file or 'None'}`
- Retry plan: `{task.retry_plan_file or 'None'}`

## Commands

- Copy prompt: `{task.copy_prompt_command or 'None'}`
- Validate: `{task.validate_command or 'None'}`
- Retry: `{task.retry_command or 'None'}`
- Review: `{task.review_command or 'None'}`

## Notes

{notes}
"""


def _resolve_response_file(runtime_dir: Path, task_dir: Path, task_id: str) -> Path | None:
    local_response = task_dir / "agent-response.json"
    if local_response.exists():
        return local_response
    cline_response = runtime_dir / "cline-outbox" / task_id / "response.json"
    if cline_response.exists():
        return cline_response
    return local_response


def _task_status(task_dir: Path, response_file: Path | None, validation: dict[str, Any]) -> str:
    status = str(validation.get("status") or "")
    if status in {"accepted", "accepted_with_warnings"}:
        return status
    if status:
        return status
    if response_file is not None and response_file.exists():
        return "response_received"
    if (task_dir / "compiled-context.md").exists():
        return "ready"
    return "prepared"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
