from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.feedback import ingest_feedback_entries
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def record_task_review(
    *,
    analysis_dir: Path,
    task_id: str,
    decision: str,
    notes: str | None = None,
    reviewer: str | None = None,
    response_file: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    runtime_dir = analysis_dir / "runtime"
    task_dir = runtime_dir / "taskpacks" / task_id
    if not task_dir.exists():
        raise ValueError(f"Task pack does not exist: {task_dir}")

    validation = _load_json(task_dir / "validation-result.json") or {}
    response_path = response_file.resolve() if response_file else task_dir / "agent-response.json"
    response_payload = _load_json(response_path) or {}

    record = {
        "task_id": task_id,
        "task_type": validation.get("task_type"),
        "module_name": validation.get("module_name"),
        "subject_name": validation.get("subject_name"),
        "decision": decision,
        "notes": notes or "",
        "reviewer": reviewer or "",
        "response_path": response_path.as_posix(),
        "validated_status": validation.get("status"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    reviews_dir = runtime_dir / "reviews"
    ensure_directory(reviews_dir)
    history_path = reviews_dir / "review-history.json"
    history = _load_json(history_path) or []
    if not isinstance(history, list):
        history = []
    history.append(record)
    write_json(history_path, history)
    write_json(reviews_dir / f"{task_id}.json", record)

    if decision in {"accept", "trim"}:
        run_state = _load_json(runtime_dir / "run-state.json") or {}
        ingest_feedback_entries(
            analysis_dir,
            [
                {
                    "prompt_name": task_id,
                    "status": "accepted",
                    "used_fallback": False,
                    "target_model": (
                        (run_state.get("provider_config") or {}).get("model")
                        if isinstance(run_state, dict)
                        else None
                    ),
                    "notes": notes or "",
                    "response": response_payload.get("result") if isinstance(response_payload, dict) else {},
                }
            ],
        )

    summary = build_review_summary(history)
    write_json(reviews_dir / "review-summary.json", summary)
    write_text(reviews_dir / "review-summary.md", render_review_summary_markdown(summary))
    return record


def build_review_summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in history:
        decision = str(item.get("decision") or "unknown")
        counts[decision] = counts.get(decision, 0) + 1
    return {
        "total_reviews": len(history),
        "counts_by_decision": counts,
        "recent_reviews": history[-10:],
    }


def render_review_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Human Review Summary",
        "",
        f"- Total reviews: {summary.get('total_reviews', 0)}",
        "",
        "## Counts By Decision",
        "",
    ]
    counts = summary.get("counts_by_decision", {})
    if isinstance(counts, dict) and counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- None")
    lines.extend(["", "## Recent Reviews", ""])
    recent = summary.get("recent_reviews", [])
    if isinstance(recent, list) and recent:
        for item in recent:
            lines.append(
                f"- task=`{item.get('task_id')}` decision=`{item.get('decision')}` reviewer=`{item.get('reviewer') or 'n/a'}`"
            )
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
