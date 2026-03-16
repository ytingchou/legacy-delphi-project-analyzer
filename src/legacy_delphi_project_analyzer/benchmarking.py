from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.taskpacks import load_taskpack
from legacy_delphi_project_analyzer.utils import estimate_tokens, write_json, write_text


def benchmark_prompts(analysis_dir: Path) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    runtime_dir = analysis_dir / "runtime"
    validation_results = _load_json(runtime_dir / "validation-history.json", default=[])
    if not validation_results:
        validation_results = _load_json(runtime_dir / "validation-results.json", default=[])
    feedback_log = _load_json(analysis_dir / "knowledge" / "feedback-log.json", default=[])
    prompt_index = {item.name: item for item in output.prompt_packs}
    taskpack_root = runtime_dir / "taskpacks"

    prompt_rows: dict[str, dict[str, Any]] = {}
    for task_dir in sorted(taskpack_root.iterdir()) if taskpack_root.exists() else []:
        if not task_dir.is_dir():
            continue
        taskpack = load_taskpack(task_dir)
        if taskpack is None:
            continue
        prompt_name = taskpack.source_prompt_name or taskpack.task_id
        row = prompt_rows.setdefault(
            prompt_name,
            {
                "prompt_name": prompt_name,
                "goal": taskpack.task_type,
                "module_name": taskpack.module_name,
                "subject_name": taskpack.subject_name,
                "attempts": 0,
                "accepted": 0,
                "accepted_with_warnings": 0,
                "needs_follow_up": 0,
                "rejected": 0,
                "fallback_uses": 0,
                "verification_uses": 0,
                "avg_context_budget": taskpack.context_budget_tokens,
                "prompt_tokens": estimate_tokens(taskpack.primary_prompt or ""),
                "schema_keys": len(taskpack.expected_output_schema),
                "recommended_template": "primary",
                "tuning_actions": [],
            },
        )
        records = [
            item
            for item in validation_results
            if isinstance(item, dict) and str(item.get("task_id") or "") == taskpack.task_id
        ]
        if not records:
            continue
        row["attempts"] += len(records)
        row["fallback_uses"] += sum(1 for item in records if item.get("prompt_mode") == "fallback")
        row["verification_uses"] += sum(1 for item in records if item.get("prompt_mode") == "verification")
        for item in records:
            status = str(item.get("status") or "")
            if status == "accepted":
                row["accepted"] += 1
            elif status == "accepted_with_warnings":
                row["accepted_with_warnings"] += 1
            elif status == "needs_follow_up":
                row["needs_follow_up"] += 1
            else:
                row["rejected"] += 1

    feedback_summary = _summarize_feedback(feedback_log)
    goal_rows = defaultdict(lambda: {"attempts": 0, "accepted": 0, "rejected": 0, "follow_up": 0})
    leaderboard = []
    for row in prompt_rows.values():
        success = row["accepted"] + row["accepted_with_warnings"]
        attempts = row["attempts"] or 1
        row["success_rate"] = round(success / attempts, 3)
        row["recommended_template"] = _recommended_template(row)
        row["tuning_actions"] = _tuning_actions(row)
        goal = str(row["goal"] or "unknown")
        goal_rows[goal]["attempts"] += row["attempts"]
        goal_rows[goal]["accepted"] += success
        goal_rows[goal]["rejected"] += row["rejected"]
        goal_rows[goal]["follow_up"] += row["needs_follow_up"]
        leaderboard.append(row)
    leaderboard.sort(key=lambda item: (-float(item["success_rate"]), -int(item["attempts"]), str(item["prompt_name"])))

    tuning = {
        "best_by_goal": {},
        "global_recommendations": _global_recommendations(leaderboard),
    }
    for goal, values in goal_rows.items():
        goal_leaderboard = [row for row in leaderboard if row["goal"] == goal]
        tuning["best_by_goal"][goal] = goal_leaderboard[0]["prompt_name"] if goal_leaderboard else None
        values["success_rate"] = round(values["accepted"] / values["attempts"], 3) if values["attempts"] else 0.0

    report = {
        "analysis_dir": analysis_dir.as_posix(),
        "prompt_benchmark": leaderboard,
        "goal_summary": dict(goal_rows),
        "feedback_summary": feedback_summary,
        "template_tuning": tuning,
    }
    write_json(runtime_dir / "prompt-benchmark.json", report)
    write_text(runtime_dir / "prompt-benchmark.md", _render_prompt_benchmark_markdown(report))
    write_json(runtime_dir / "prompt-template-tuning.json", tuning)
    return report


def _summarize_feedback(feedback_log: Any) -> dict[str, Any]:
    if not isinstance(feedback_log, list):
        return {"entries": 0, "accepted": 0, "rejected": 0, "needs_follow_up": 0}
    return {
        "entries": len(feedback_log),
        "accepted": sum(1 for item in feedback_log if isinstance(item, dict) and item.get("status") == "accepted"),
        "rejected": sum(1 for item in feedback_log if isinstance(item, dict) and item.get("status") == "rejected"),
        "needs_follow_up": sum(
            1 for item in feedback_log if isinstance(item, dict) and item.get("status") == "needs_follow_up"
        ),
    }


def _recommended_template(row: dict[str, Any]) -> str:
    if int(row["fallback_uses"]) and row["accepted"] == 0 and row["accepted_with_warnings"] > 0:
        return "fallback"
    if int(row["rejected"]) > int(row["accepted"]) + int(row["accepted_with_warnings"]):
        return "verification"
    return "primary"


def _tuning_actions(row: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if int(row["prompt_tokens"]) > 1200:
        actions.append("shorten_prompt_template")
    if int(row["avg_context_budget"]) > 10000:
        actions.append("shrink_context_budget")
    if int(row["rejected"]) > 0:
        actions.append("tighten_evidence_constraints")
    if int(row["needs_follow_up"]) > 0:
        actions.append("ask_for_one_missing_fact")
    if not actions:
        actions.append("keep_current_template")
    return actions


def _global_recommendations(leaderboard: list[dict[str, Any]]) -> list[str]:
    if not leaderboard:
        return ["No prompt benchmark data yet."]
    recommendations = []
    if any("shrink_context_budget" in row["tuning_actions"] for row in leaderboard):
        recommendations.append("Several prompts still carry too much context for qwen3; prefer smaller bundles.")
    if any(row["recommended_template"] == "verification" for row in leaderboard):
        recommendations.append("Some tasks benefit from verification-first retries after weak initial answers.")
    if any("tighten_evidence_constraints" in row["tuning_actions"] for row in leaderboard):
        recommendations.append("Unsupported claims remain a top failure mode; keep JSON schemas and evidence checks strict.")
    return recommendations or ["Current prompt templates are stable enough to keep."]


def _render_prompt_benchmark_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Prompt Benchmark",
        "",
        "## Global Recommendations",
        "",
    ]
    lines.extend(f"- {item}" for item in report.get("template_tuning", {}).get("global_recommendations", []))
    lines.extend(["", "## Prompt Leaderboard", ""])
    for row in report.get("prompt_benchmark", []):
        lines.extend(
            [
                f"### {row['prompt_name']}",
                f"- Goal: {row['goal']}",
                f"- Success rate: {row['success_rate']}",
                f"- Attempts: {row['attempts']}",
                f"- Recommended template: {row['recommended_template']}",
                f"- Tuning actions: {', '.join(row['tuning_actions'])}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _load_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
