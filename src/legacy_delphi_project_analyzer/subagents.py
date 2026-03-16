from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.agent_loop import validate_task_response
from legacy_delphi_project_analyzer.benchmarking import benchmark_prompts
from legacy_delphi_project_analyzer.cline import emit_cline_task, wait_for_cline_response
from legacy_delphi_project_analyzer.context_compiler import compile_task_context
from legacy_delphi_project_analyzer.orchestrator import (
    load_runtime_bundle,
    refresh_runtime_artifacts,
    rerun_analysis_from_runtime_state,
)
from legacy_delphi_project_analyzer.taskpacks import TaskPack, load_taskpack, write_taskpacks
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def plan_subagent_batches(
    analysis_dir: Path,
    *,
    max_tasks: int = 4,
    batch_size: int = 2,
    goal_filters: list[str] | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    bundle = load_runtime_bundle(analysis_dir)
    run_state = bundle["run_state"]
    if run_state is None:
        raise ValueError(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
    refresh_runtime_artifacts(
        output,
        target_model_profile=run_state.target_model_profile,
        dispatch_mode=run_state.dispatch_mode,
        analysis_config=run_state.analysis_config,
        provider_config=run_state.provider_config,
    )
    filtered = [
        item
        for item in output.prompt_packs
        if not goal_filters or item.goal in goal_filters
    ][:max_tasks]
    taskpacks = [_prompt_pack_to_taskpack(item, run_state.target_model_profile) for item in filtered]
    batches = []
    for index in range(math.ceil(len(taskpacks) / batch_size)) if batch_size > 0 else []:
        slice_start = index * batch_size
        slice_end = slice_start + batch_size
        current = taskpacks[slice_start:slice_end]
        batches.append(
            {
                "batch_id": f"batch-{index + 1}",
                "task_ids": [item.task_id for item in current],
                "goals": [item.task_type for item in current],
            }
        )
    return {
        "analysis_dir": analysis_dir.as_posix(),
        "taskpacks": taskpacks,
        "batches": batches,
    }


def run_subagent_batches(
    analysis_dir: Path,
    *,
    dispatch_mode: str = "manual",
    max_tasks: int = 4,
    batch_size: int = 2,
    goal_filters: list[str] | None = None,
    wait_seconds: int = 120,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    runtime_dir = analysis_dir / "runtime"
    subagent_dir = runtime_dir / "subagents"
    ensure_directory(subagent_dir)

    plan = plan_subagent_batches(
        analysis_dir,
        max_tasks=max_tasks,
        batch_size=batch_size,
        goal_filters=goal_filters,
    )
    taskpacks = plan["taskpacks"]
    write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
    for taskpack in taskpacks:
        task_dir = runtime_dir / "taskpacks" / taskpack.task_id
        compile_task_context(
            analysis_dir=analysis_dir,
            runtime_dir=runtime_dir,
            taskpack=taskpack,
            task_dir=task_dir,
        )

    results = []
    for batch in plan["batches"]:
        batch_results = []
        for task_id in batch["task_ids"]:
            task_dir = runtime_dir / "taskpacks" / task_id
            taskpack = load_taskpack(task_dir)
            if taskpack is None:
                continue
            if dispatch_mode == "cline":
                emit_cline_task(taskpack, task_dir, runtime_dir)
                response = wait_for_cline_response(
                    task_id,
                    runtime_dir,
                    timeout_seconds=wait_seconds,
                    poll_interval_seconds=poll_seconds,
                )
                if response is not None:
                    record = validate_task_response(
                        analysis_dir=analysis_dir,
                        task_dir=task_dir,
                        response_payload=response,
                    )
                    batch_results.append({"task_id": task_id, "status": record.status, "goal": taskpack.task_type})
                else:
                    batch_results.append({"task_id": task_id, "status": "waiting", "goal": taskpack.task_type})
            else:
                batch_results.append({"task_id": task_id, "status": "prepared", "goal": taskpack.task_type})
        results.append({"batch_id": batch["batch_id"], "results": batch_results})

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "dispatch_mode": dispatch_mode,
        "batch_count": len(plan["batches"]),
        "results": results,
    }
    write_json(subagent_dir / "batch-plan.json", {"batches": plan["batches"]})
    write_json(subagent_dir / "batch-results.json", payload)
    write_text(subagent_dir / "batch-summary.md", _render_batch_summary(payload))
    benchmark_prompts(analysis_dir)
    return payload


def _prompt_pack_to_taskpack(prompt_pack: Any, target_model_profile: str) -> TaskPack:
    return TaskPack(
        task_id=f"subagent-{slugify(prompt_pack.name)}",
        task_type=prompt_pack.goal,
        phase=prompt_pack.category,
        priority=50,
        target_model_profile=target_model_profile,
        module_name=next(iter([part for part in [getattr(prompt_pack, "subject_name", None)] if part]), None),
        subject_name=prompt_pack.subject_name,
        issue_summary=prompt_pack.issue_summary or prompt_pack.objective,
        context_paths=list(prompt_pack.context_paths),
        context_budget_tokens=prompt_pack.context_budget_tokens,
        primary_prompt=prompt_pack.prompt,
        fallback_prompt=prompt_pack.fallback_prompt,
        verification_prompt=prompt_pack.verification_prompt,
        expected_output_schema=dict(prompt_pack.expected_response_schema),
        acceptance_checks=list(prompt_pack.acceptance_checks),
        source_prompt_name=prompt_pack.name,
        source_kind="prompt-pack",
        notes=list(prompt_pack.notes),
    )


def _render_batch_summary(payload: dict[str, Any]) -> str:
    lines = [
        "# Multi-Subagent Batch Summary",
        "",
        f"- Dispatch mode: {payload['dispatch_mode']}",
        f"- Batch count: {payload['batch_count']}",
        "",
    ]
    for batch in payload.get("results", []):
        lines.append(f"## {batch['batch_id']}")
        lines.append("")
        for item in batch.get("results", []):
            lines.append(f"- {item['task_id']}: {item['status']} ({item['goal']})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
