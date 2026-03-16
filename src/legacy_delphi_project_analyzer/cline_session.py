from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import AnalysisOutput
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_cline_session_manifest(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    output: AnalysisOutput,
    cline_cmd: list[str] | None = None,
) -> dict[str, Any]:
    session_dir = runtime_dir / "cline-session"
    tasks_dir = session_dir / "tasks"
    ensure_directory(tasks_dir)
    cline_cmd = cline_cmd or ["cline", "chat"]
    entries: list[dict[str, Any]] = []

    for taskpack in output.taskpacks:
        task_dir = runtime_dir / "taskpacks" / taskpack.task_id
        session_task_dir = tasks_dir / taskpack.task_id
        ensure_directory(session_task_dir)
        prompt_text = _build_prompt_text(task_dir)
        prompt_path = session_task_dir / "prompt.txt"
        fallback_path = session_task_dir / "fallback-prompt.txt"
        verification_path = session_task_dir / "verification-prompt.txt"
        write_text(prompt_path, prompt_text)
        write_text(fallback_path, _build_prompt_text(task_dir, mode="fallback"))
        write_text(verification_path, _build_prompt_text(task_dir, mode="verification"))
        response_template_path = session_task_dir / "response-template.json"
        write_json(
            response_template_path,
            {
                "task_id": taskpack.task_id,
                "status": "completed",
                "result": {},
                "supported_claims": [],
                "unsupported_claims": [],
                "remaining_unknowns": [],
                "recommended_next_task": "",
            },
        )
        command_text = _suggested_command(cline_cmd, prompt_path)
        write_text(session_task_dir / "run-command.txt", command_text)
        write_text(session_task_dir / "vscode-open.md", _render_vscode_open(taskpack.task_id, task_dir))
        entry = {
            "task_id": taskpack.task_id,
            "task_type": taskpack.task_type,
            "module_name": taskpack.module_name,
            "subject_name": taskpack.subject_name,
            "prompt_file": prompt_path.as_posix(),
            "fallback_prompt_file": fallback_path.as_posix(),
            "verification_prompt_file": verification_path.as_posix(),
            "response_template_file": response_template_path.as_posix(),
            "run_command_file": (session_task_dir / "run-command.txt").as_posix(),
            "inbox_request_file": (runtime_dir / "cline-inbox" / taskpack.task_id / "request.json").as_posix(),
            "outbox_response_file": (runtime_dir / "cline-outbox" / taskpack.task_id / "response.json").as_posix(),
            "validate_command": f"legacy-delphi-analyzer validate-response {analysis_dir} {taskpack.task_id}",
            "retry_command": f"legacy-delphi-analyzer retry-plan {analysis_dir} {taskpack.task_id}",
            "notes": [
                "Use only one task at a time with qwen3.",
                "Do not paste the entire module dossier.",
            ],
        }
        entries.append(entry)
        write_json(session_task_dir / "manifest.json", entry)

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "cline_cmd": cline_cmd,
        "task_count": len(entries),
        "entries": entries,
    }
    write_json(session_dir / "session-manifest.json", payload)
    write_text(session_dir / "quick-start.md", _render_quick_start(payload))
    return payload


def _build_prompt_text(task_dir: Path, *, mode: str = "primary") -> str:
    prompt_name = {
        "primary": "primary-prompt.txt",
        "fallback": "fallback-prompt.txt",
        "verification": "verification-prompt.txt",
    }[mode]
    prompt_text = _safe_read(task_dir / prompt_name)
    agent_task = _safe_read(task_dir / "agent-task.md")
    compiled_context = _safe_read(task_dir / "compiled-context.md")
    schema = _safe_read(task_dir / "agent-expected-output-schema.json")
    return (
        "You are handling one bounded migration task.\n\n"
        f"Task:\n{agent_task}\n\n"
        f"Compiled Context:\n{compiled_context}\n\n"
        f"Expected Output Schema:\n{schema}\n\n"
        "Hard Constraints:\n"
        "- Output JSON only\n"
        "- Use only the supplied evidence\n"
        "- Do not invent behavior\n"
        "- If uncertain, put it in remaining_unknowns\n"
        "- Do not include markdown fences\n\n"
        f"Prompt Variant:\n{prompt_text}\n"
    )


def _suggested_command(cline_cmd: list[str], prompt_path: Path) -> str:
    rendered = " ".join(cline_cmd)
    if "{prompt_file}" in rendered:
        return rendered.replace("{prompt_file}", prompt_path.as_posix())
    return f"{rendered} < {prompt_path.as_posix()}"


def _render_vscode_open(task_id: str, task_dir: Path) -> str:
    return f"""# VSCode Cline Quick Open

Open these files for `{task_id}`:

- `{task_dir / 'agent-task.md'}`
- `{task_dir / 'compiled-context.md'}`
- `{task_dir / 'agent-expected-output-schema.json'}`

Then paste `prompt.txt` into the extension or use the copy-prompt helper.
"""


def _render_quick_start(payload: dict[str, Any]) -> str:
    lines = [
        "# Cline Session Quick Start",
        "",
        f"- Tasks prepared: {payload.get('task_count', 0)}",
        "",
        "## Workflow",
        "",
        "- Open one task only.",
        "- Run the generated prompt.txt through Cline CLI or paste it into VSCode Cline.",
        "- Save JSON into the expected response file.",
        "- Run validate-response before moving to the next task.",
        "",
        "## Task Entries",
        "",
    ]
    for item in payload.get("entries", []):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {item.get('task_id')}",
                f"- Prompt file: `{item.get('prompt_file')}`",
                f"- Validate: `{item.get('validate_command')}`",
                f"- Retry: `{item.get('retry_command')}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""
