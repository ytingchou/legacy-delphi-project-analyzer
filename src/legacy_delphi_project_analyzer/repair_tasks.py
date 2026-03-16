from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_repair_tasks(
    analysis_dir: Path,
    *,
    runtime_dir: Path,
    runtime_error_summary: dict[str, Any] | None,
    patch_validation_report: dict[str, Any] | None = None,
    repo_validation_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repair_dir = runtime_dir / "repair-tasks"
    ensure_directory(repair_dir)
    entries: list[dict[str, Any]] = []

    for item in (runtime_error_summary or {}).get("items", []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or item.get("code") or "repair")
        entry = {
            "repair_id": f"repair-{task_id}",
            "source": "runtime_error",
            "task_id": item.get("task_id"),
            "category": item.get("category") or item.get("code"),
            "title": item.get("title"),
            "repair_prompt": (
                f"Repair only task {item.get('task_id') or 'unknown'}. "
                f"{item.get('suggested_prompt') or 'Use only supported evidence and output strict JSON.'}"
            ),
            "next_command": item.get("suggested_command") or "",
            "notes": [item.get("next_best_action") or ""],
        }
        entries.append(entry)

    for item in (patch_validation_report or {}).get("entries", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "pass") == "pass":
            continue
        slice_name = str(item.get("slice_name") or "slice")
        entry = {
            "repair_id": f"repair-patch-{slice_name}",
            "source": "patch_validation",
            "task_id": None,
            "category": "patch_validation",
            "title": f"Repair patch pack for {slice_name}",
            "repair_prompt": (
                f"Repair the bounded patch pack for {slice_name}. "
                "Fix only the listed validation issues and do not redesign the slice."
            ),
            "next_command": "",
            "notes": list(item.get("issues", [])),
        }
        entries.append(entry)

    for item in (repo_validation_report or {}).get("entries", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "pass") == "pass":
            continue
        slice_name = str(item.get("slice_name") or "slice")
        entry = {
            "repair_id": f"repair-repo-{slice_name}",
            "source": "repo_validation",
            "task_id": None,
            "category": "repo_validation",
            "title": f"Repair repo placement for {slice_name}",
            "repair_prompt": str(item.get("repair_prompt") or ""),
            "next_command": item.get("next_command") or "",
            "notes": list(item.get("issues", [])),
        }
        entries.append(entry)

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "entry_count": len(entries),
        "entries": entries,
        "recommended_workflow": [
            "Run one repair task at a time.",
            "Prefer schema/evidence repair before expanding the original task scope.",
            "After the repair response, run validate-response again.",
        ],
    }
    write_json(repair_dir / "repair-tasks.json", payload)
    write_text(repair_dir / "repair-tasks.md", _render_markdown(payload))
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repair Tasks",
        "",
        f"- Entry count: {payload.get('entry_count', 0)}",
        "",
        "## Recommended Workflow",
        "",
    ]
    for item in payload.get("recommended_workflow", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Entries", ""])
    for item in payload.get("entries", [])[:20]:
        lines.extend(
            [
                f"### {item.get('repair_id')}",
                f"- Source: {item.get('source')}",
                f"- Title: {item.get('title')}",
                f"- Next command: `{item.get('next_command') or 'n/a'}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
