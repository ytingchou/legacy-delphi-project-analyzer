from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import AnalysisOutput
from legacy_delphi_project_analyzer.utils import write_json, write_text


def evaluate_golden_tasks(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    output: AnalysisOutput,
) -> dict[str, Any]:
    validation_results = _load_json(runtime_dir / "validation-results.json") or []
    rows: dict[str, dict[str, Any]] = {}

    for taskpack in output.taskpacks:
        row = rows.setdefault(
            taskpack.task_type,
            {
                "task_type": taskpack.task_type,
                "task_count": 0,
                "accepted": 0,
                "rejected": 0,
                "follow_up": 0,
                "avg_context_budget": 0,
                "sample_task_ids": [],
                "target_profiles": set(),
            },
        )
        row["task_count"] += 1
        row["avg_context_budget"] += taskpack.context_budget_tokens
        row["target_profiles"].add(taskpack.target_model_profile)
        if len(row["sample_task_ids"]) < 3:
            row["sample_task_ids"].append(taskpack.task_id)
        for validation in validation_results:
            if not isinstance(validation, dict):
                continue
            if str(validation.get("task_id") or "") != taskpack.task_id:
                continue
            status = str(validation.get("status") or "")
            if status in {"accepted", "accepted_with_warnings"}:
                row["accepted"] += 1
            elif status == "needs_follow_up":
                row["follow_up"] += 1
            elif status:
                row["rejected"] += 1

    leaderboard = []
    for row in rows.values():
        count = max(1, int(row["task_count"]))
        row["avg_context_budget"] = int(row["avg_context_budget"] / count)
        row["success_rate"] = round(float(row["accepted"]) / count, 3)
        row["target_profiles"] = sorted(str(item) for item in row["target_profiles"])
        row["recommendation"] = _recommendation(row)
        leaderboard.append(row)
    leaderboard.sort(key=lambda item: (-float(item["success_rate"]), item["task_type"]))

    report = {
        "analysis_dir": analysis_dir.as_posix(),
        "task_type_count": len(leaderboard),
        "leaderboard": leaderboard,
        "global_recommendations": _global_recommendations(leaderboard),
    }
    golden_dir = runtime_dir / "golden-tasks"
    golden_dir.mkdir(parents=True, exist_ok=True)
    write_json(golden_dir / "golden-task-evaluation.json", report)
    write_text(golden_dir / "golden-task-evaluation.md", _render_markdown(report))
    return report


def _recommendation(row: dict[str, Any]) -> str:
    if row["success_rate"] >= 0.75:
        return "Keep this task shape as a golden example for weak-model execution."
    if row["follow_up"] > row["accepted"]:
        return "Use stricter repair prompts and smaller evidence bundles."
    return "Narrow the task further before sending it to qwen3."


def _global_recommendations(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No task evaluation data yet."]
    notes = []
    if any(item["avg_context_budget"] > 10000 for item in rows):
        notes.append("Some task types still exceed a safe weak-model context budget; shrink them before delivery.")
    if any(item["rejected"] > item["accepted"] for item in rows):
        notes.append("Several task types still fail more often than they pass; prefer retry-plan driven repair.")
    if not notes:
        notes.append("Current golden tasks are stable enough to reuse as reference bounded prompts.")
    return notes


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Golden Task Evaluation",
        "",
        f"- Task types evaluated: {report.get('task_type_count', 0)}",
        "",
        "## Global Recommendations",
        "",
    ]
    for item in report.get("global_recommendations", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Leaderboard", ""])
    for item in report.get("leaderboard", []):
        lines.extend(
            [
                f"### {item['task_type']}",
                f"- Success rate: {item['success_rate']}",
                f"- Accepted: {item['accepted']}",
                f"- Rejected: {item['rejected']}",
                f"- Follow-up: {item['follow_up']}",
                f"- Avg context budget: {item['avg_context_budget']}",
                f"- Recommendation: {item['recommendation']}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
