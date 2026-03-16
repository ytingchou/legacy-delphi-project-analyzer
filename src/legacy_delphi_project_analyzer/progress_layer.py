from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def update_progress_report(
    analysis_dir: Path,
    *,
    runtime_dir: Path,
    output: Any,
) -> dict[str, Any]:
    progress_dir = runtime_dir / "progress"
    ensure_directory(progress_dir)
    history_path = progress_dir / "progress-history.json"
    history = _load_json(history_path)
    if not isinstance(history, list):
        history = []

    snapshot = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "readiness_ready": len([item for item in output.transition_specs if item.readiness_level == "ready"]),
        "readiness_blocked": len([item for item in output.transition_specs if item.readiness_level == "blocked"]),
        "blocker_count": len(output.blocking_unknowns or []),
        "runtime_error_count": int((output.runtime_error_summary or {}).get("item_count", 0)),
        "accepted_validations": len(
            [
                item for item in _load_json(runtime_dir / "validation-results.json") or []
                if isinstance(item, dict) and str(item.get("status") or "").startswith("accepted")
            ]
        ),
        "task_studio_count": int((output.task_studio or {}).get("task_count", 0)),
    }
    if not history or history[-1] != snapshot:
        history.append(snapshot)
    write_json(history_path, history)

    report = {
        "analysis_dir": analysis_dir.as_posix(),
        "snapshot_count": len(history),
        "latest": history[-1] if history else snapshot,
        "trend_summary": _trend_summary(history),
        "management_notes": _management_notes(history),
    }
    write_json(progress_dir / "progress-report.json", report)
    write_text(progress_dir / "progress-report.md", _render_markdown(report))
    return report


def _trend_summary(history: list[dict[str, Any]]) -> dict[str, str]:
    if len(history) < 2:
        return {"status": "baseline_only"}
    previous = history[-2]
    latest = history[-1]
    return {
        "blockers": _delta_string(int(previous.get("blocker_count", 0)), int(latest.get("blocker_count", 0))),
        "ready_modules": _delta_string(int(previous.get("readiness_ready", 0)), int(latest.get("readiness_ready", 0))),
        "accepted_validations": _delta_string(int(previous.get("accepted_validations", 0)), int(latest.get("accepted_validations", 0))),
    }


def _management_notes(history: list[dict[str, Any]]) -> list[str]:
    if not history:
        return ["No progress snapshots yet."]
    latest = history[-1]
    notes = [
        f"Ready modules: {latest.get('readiness_ready', 0)}",
        f"Blocked modules: {latest.get('readiness_blocked', 0)}",
        f"Current blocker count: {latest.get('blocker_count', 0)}",
    ]
    if len(history) >= 2:
        notes.append(f"Blocker trend since last snapshot: {_trend_summary(history).get('blockers')}")
    return notes


def _delta_string(previous: int, current: int) -> str:
    delta = current - previous
    if delta == 0:
        return "unchanged"
    return f"{delta:+d}"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Progress Report",
        "",
        f"- Snapshot count: {report.get('snapshot_count', 0)}",
        "",
        "## Latest",
        "",
    ]
    for key, value in sorted((report.get("latest") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Management Notes", ""])
    for item in report.get("management_notes", []):
        lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
