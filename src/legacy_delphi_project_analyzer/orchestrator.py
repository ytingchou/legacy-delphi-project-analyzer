from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.cheatsheet import write_runtime_cheat_sheet
from legacy_delphi_project_analyzer.cline_session import build_cline_session_manifest
from legacy_delphi_project_analyzer.failure_replay import build_failure_replay_lab
from legacy_delphi_project_analyzer.golden_tasks import evaluate_golden_tasks
from legacy_delphi_project_analyzer.human_review import build_review_summary
from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.phase_state import (
    ArtifactCompleteness,
    LoopMetrics,
    RunState,
    load_artifact_completeness,
    load_blocking_unknowns,
    load_loop_metrics,
    load_phase_states,
    load_run_state,
    save_artifact_completeness,
    save_blocking_unknowns,
    save_loop_metrics,
    save_phase_states,
    save_run_state,
)
from legacy_delphi_project_analyzer.phases import (
    HANDOFF_MANIFEST_FILE,
    PHASE_SEQUENCE,
    VALIDATION_RESULT_FILE,
    build_blocking_unknowns,
    build_phase_states,
    determine_current_phase,
)
from legacy_delphi_project_analyzer.pipeline import run_analysis
from legacy_delphi_project_analyzer.reporting import build_web_report_html
from legacy_delphi_project_analyzer.runtime_errors import (
    load_provider_health,
    load_review_summary,
    write_runtime_error_summary,
)
from legacy_delphi_project_analyzer.task_studio import build_task_studio
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, write_taskpacks
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


RUNTIME_DIR_NAME = "runtime"


def run_phases(
    *,
    project_root: Path,
    output_dir: Path,
    rules_dir: Path | None = None,
    workspace_config_path: Path | None = None,
    extra_search_paths: list[str] | None = None,
    path_variables: dict[str, str] | None = None,
    phases: list[str] | None = None,
    max_artifact_chars: int = 40000,
    max_artifact_tokens: int = 10000,
    target_model: str = "qwen3-128k",
    target_model_profile: str = "qwen3_128k_weak",
    dispatch_mode: str = "manual",
    provider_config: dict[str, Any] | None = None,
) -> Any:
    output = run_analysis(
        project_root=project_root,
        output_dir=output_dir,
        rules_dir=rules_dir,
        workspace_config_path=workspace_config_path,
        extra_search_paths=extra_search_paths,
        path_variables=path_variables,
        phases=phases,
        max_artifact_chars=max_artifact_chars,
        max_artifact_tokens=max_artifact_tokens,
        target_model=target_model,
    )
    refresh_runtime_artifacts(
        output,
        target_model_profile=target_model_profile,
        dispatch_mode=dispatch_mode,
        analysis_config=build_analysis_config(
            project_root=project_root,
            output_dir=output_dir,
            rules_dir=rules_dir,
            workspace_config_path=workspace_config_path,
            extra_search_paths=extra_search_paths,
            path_variables=path_variables,
            phases=phases,
            max_artifact_chars=max_artifact_chars,
            max_artifact_tokens=max_artifact_tokens,
            target_model=target_model,
        ),
        provider_config=provider_config,
    )
    return output


def build_analysis_config(
    *,
    project_root: Path,
    output_dir: Path,
    rules_dir: Path | None,
    workspace_config_path: Path | None,
    extra_search_paths: list[str] | None,
    path_variables: dict[str, str] | None,
    phases: list[str] | None,
    max_artifact_chars: int,
    max_artifact_tokens: int,
    target_model: str,
) -> dict[str, Any]:
    return {
        "project_root": project_root.resolve().as_posix(),
        "output_dir": output_dir.resolve().as_posix(),
        "rules_dir": rules_dir.resolve().as_posix() if rules_dir else None,
        "workspace_config_path": workspace_config_path.resolve().as_posix() if workspace_config_path else None,
        "extra_search_paths": list(extra_search_paths or []),
        "path_variables": dict(path_variables or {}),
        "phases": list(phases) if phases else None,
        "max_artifact_chars": max_artifact_chars,
        "max_artifact_tokens": max_artifact_tokens,
        "target_model": target_model,
    }


def rerun_analysis_from_runtime_state(analysis_dir: Path) -> Any:
    runtime_dir = analysis_dir / RUNTIME_DIR_NAME
    state = load_run_state(runtime_dir)
    if state is None:
        raise ValueError(f"Runtime state does not exist under {runtime_dir}")
    config = state.analysis_config
    return run_analysis(
        project_root=Path(str(config.get("project_root"))),
        output_dir=Path(str(config.get("output_dir"))),
        rules_dir=Path(str(config["rules_dir"])) if isinstance(config.get("rules_dir"), str) and config.get("rules_dir") else None,
        workspace_config_path=Path(str(config["workspace_config_path"]))
        if isinstance(config.get("workspace_config_path"), str) and config.get("workspace_config_path")
        else None,
        extra_search_paths=[
            item for item in config.get("extra_search_paths", []) if isinstance(item, str)
        ]
        if isinstance(config.get("extra_search_paths"), list)
        else None,
        path_variables=config.get("path_variables") if isinstance(config.get("path_variables"), dict) else None,
        phases=config.get("phases") if isinstance(config.get("phases"), list) else None,
        max_artifact_chars=int(config.get("max_artifact_chars") or 40000),
        max_artifact_tokens=int(config.get("max_artifact_tokens") or 10000),
        target_model=str(config.get("target_model") or "qwen3-128k"),
    )


def refresh_runtime_artifacts(
    output: Any,
    *,
    target_model_profile: str,
    dispatch_mode: str,
    analysis_config: dict[str, Any] | None = None,
    provider_config: dict[str, Any] | None = None,
) -> RunState:
    if not output.output_dir:
        raise ValueError("Analysis output must have output_dir before runtime artifacts can be generated.")
    output_root = Path(output.output_dir)
    runtime_dir = output_root / RUNTIME_DIR_NAME
    ensure_directory(runtime_dir)

    validation_results = _load_json(runtime_dir / "validation-results.json")
    if not isinstance(validation_results, list):
        validation_results = []
    metrics = load_loop_metrics(runtime_dir)
    blockers = build_blocking_unknowns(output, validation_results)
    phase_states = build_phase_states(output, blockers, validation_results, runtime_dir)
    completeness = compute_artifact_completeness(output, validation_results, output_root)
    handoff_manifest = _build_handoff_manifest(output, blockers, validation_results)
    write_json(output_root / HANDOFF_MANIFEST_FILE, handoff_manifest)

    previous_state = load_run_state(runtime_dir)
    current_phase = determine_current_phase(phase_states)
    run_state = previous_state or RunState(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        project_root=output.inventory.project_root,
        analysis_dir=output_root.as_posix(),
        target_model_profile=target_model_profile,
        dispatch_mode=dispatch_mode,
        current_phase=current_phase,
        loop_iteration=0,
        status="prepared",
        analysis_config=analysis_config or {},
        provider_config=provider_config or {},
    )
    run_state.project_root = output.inventory.project_root
    run_state.analysis_dir = output_root.as_posix()
    run_state.target_model_profile = target_model_profile or run_state.target_model_profile
    run_state.dispatch_mode = dispatch_mode or run_state.dispatch_mode
    if analysis_config:
        run_state.analysis_config = analysis_config
    if provider_config:
        run_state.provider_config.update(provider_config)
    run_state.current_phase = current_phase
    run_state.blocking_task_id = blockers[0].task_id if blockers else None
    run_state.completed_artifacts = completeness.completed_count
    run_state.required_artifacts = completeness.required_count
    run_state.status = _derive_runtime_status(current_phase, blockers, completeness)
    run_state.stop_reason = None if run_state.status != "completed" else "all_required_artifacts_ready"
    run_state.last_updated_at = datetime.now(UTC).isoformat()

    save_run_state(runtime_dir, run_state)
    save_loop_metrics(runtime_dir, metrics)
    save_blocking_unknowns(runtime_dir, blockers)
    save_artifact_completeness(runtime_dir, completeness)
    save_phase_states(runtime_dir, phase_states)
    _write_phase_summaries(runtime_dir, phase_states)
    _write_runtime_summary(runtime_dir, run_state, blockers, completeness, metrics)
    write_runtime_cheat_sheet(
        analysis_dir=output_root,
        run_state=run_state,
        blockers=blockers,
        completeness=completeness,
    )
    runtime_error_summary = write_runtime_error_summary(
        analysis_dir=output_root,
        runtime_dir=runtime_dir,
        blockers=blockers,
    )
    provider_health = load_provider_health(runtime_dir)
    review_summary = load_review_summary(runtime_dir)
    if review_summary is None:
        review_history = _load_json(runtime_dir / "reviews" / "review-history.json")
        if isinstance(review_history, list):
            review_summary = build_review_summary(review_history)
            write_json(runtime_dir / "reviews" / "review-summary.json", review_summary)

    output.runtime_state = run_state
    output.phase_states = phase_states
    output.blocking_unknowns = blockers
    output.artifact_completeness = completeness
    output.loop_metrics = metrics
    output.runtime_error_summary = runtime_error_summary
    output.provider_health = provider_health
    output.review_summary = review_summary
    taskpacks = build_taskpacks(output, run_state)
    write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
    output.taskpacks = taskpacks
    output.task_studio = build_task_studio(
        analysis_dir=output_root,
        runtime_dir=runtime_dir,
        output=output,
    )
    output.cline_session_manifest = build_cline_session_manifest(
        analysis_dir=output_root,
        runtime_dir=runtime_dir,
        output=output,
    )
    build_code_patch_packs(
        analysis_dir=output_root,
        output=output,
    )
    output.failure_replay_lab = build_failure_replay_lab(
        analysis_dir=output_root,
        runtime_dir=runtime_dir,
        output=output,
    )
    output.golden_task_evaluation = evaluate_golden_tasks(
        analysis_dir=output_root,
        runtime_dir=runtime_dir,
        output=output,
    )
    output.target_integration_assistant = _load_json(
        output_root / "llm-pack" / "target-integration" / "target-integration-assistant-manifest.json"
    )
    report_dir = output_root / "report"
    if report_dir.exists():
        write_text(report_dir / "index.html", build_web_report_html(output))
    return run_state


def compute_artifact_completeness(
    output: Any,
    validation_results: list[dict[str, Any]],
    output_root: Path,
) -> ArtifactCompleteness:
    accepted_validations = sum(
        1 for item in validation_results if isinstance(item, dict) and str(item.get("status") or "").startswith("accepted")
    )
    items = {
        "project_summary": (output_root / "llm-pack" / "project-summary.md").exists(),
        "load_plan": (output_root / "llm-pack" / "load-plan.json").exists(),
        "boss_summary": (output_root / "llm-pack" / "boss-summary.md").exists(),
        "business_flows": bool(output.business_flows),
        "transition_specs": bool(output.transition_specs) and len(output.transition_specs) >= len(output.transition_mapping.modules),
        "bff_sql_artifacts": bool(output.bff_sql_artifacts),
        "ui_pseudo_artifacts": bool(output.ui_pseudo_artifacts),
        "ui_reference_artifacts": bool(output.ui_reference_artifacts),
        "ui_integration_artifacts": bool(output.ui_integration_artifacts),
        "prompt_packs": bool(output.prompt_packs),
        "failure_triage": bool(output.failure_triage),
        "task_studio": (output_root / "runtime" / "task-studio.json").exists(),
        "cline_session": (output_root / "runtime" / "cline-session" / "session-manifest.json").exists(),
        "code_patch_packs": (output_root / "llm-pack" / "code-patch-packs" / "manifest.json").exists(),
        "failure_replay_lab": (output_root / "runtime" / "failure-replay" / "manifest.json").exists(),
        "golden_task_evaluation": (output_root / "runtime" / "golden-tasks" / "golden-task-evaluation.json").exists(),
        "validation_results": accepted_validations >= len(output.transition_specs) if output.transition_specs else False,
        "handoff_manifest": (output_root / HANDOFF_MANIFEST_FILE).exists(),
    }
    completed = sum(1 for value in items.values() if value)
    return ArtifactCompleteness(items=items, completed_count=completed, required_count=len(items))


def load_runtime_bundle(analysis_dir: Path) -> dict[str, Any]:
    runtime_dir = analysis_dir / RUNTIME_DIR_NAME
    return {
        "run_state": load_run_state(runtime_dir),
        "metrics": load_loop_metrics(runtime_dir),
        "phase_states": load_phase_states(runtime_dir),
        "blocking_unknowns": load_blocking_unknowns(runtime_dir),
        "artifact_completeness": load_artifact_completeness(runtime_dir),
    }


def _write_phase_summaries(runtime_dir: Path, phase_states: list[Any]) -> None:
    for phase_state in phase_states:
        current_dir = runtime_dir / "phases" / phase_state.phase
        ensure_directory(current_dir)
        write_text(current_dir / "phase-summary.md", _render_phase_summary(phase_state))
        write_json(
            current_dir / "phase-next-actions.json",
            {
                "phase": phase_state.phase,
                "status": phase_state.status,
                "blockers": phase_state.blockers,
                "next_actions": [
                    f"Resolve blocker {blocker}" for blocker in phase_state.blockers
                ]
                or [f"Review {phase_state.phase} outputs and advance to the next phase."],
            },
        )
        write_json(
            current_dir / "phase-done-criteria.json",
            {
                "phase": phase_state.phase,
                "done_criteria": phase_state.done_criteria,
                "completion_score": phase_state.completion_score,
            },
        )


def _write_runtime_summary(
    runtime_dir: Path,
    run_state: RunState,
    blockers: list[Any],
    completeness: ArtifactCompleteness,
    metrics: LoopMetrics,
) -> None:
    write_text(runtime_dir / "state-summary.md", _render_runtime_summary(run_state, blockers, completeness, metrics))
    write_json(
        runtime_dir / "state-summary.json",
        {
            "run_state": run_state,
            "top_blockers": blockers[:10],
            "artifact_completeness": completeness,
            "metrics": metrics,
        },
    )
    write_text(
        runtime_dir / "phase-delta.md",
        _render_phase_delta(run_state, blockers, completeness),
    )


def _build_handoff_manifest(
    output: Any,
    blockers: list[Any],
    validation_results: list[dict[str, Any]],
) -> dict[str, Any]:
    ready_specs = [
        {
            "module_name": item.module_name,
            "readiness_level": item.readiness_level,
            "readiness_score": item.readiness_score,
            "recommended_first_slice": item.recommended_first_slice,
        }
        for item in output.transition_specs
        if item.readiness_level in {"ready", "needs-clarification"}
    ]
    validation_by_subject = {
        str(item.get("subject_name") or item.get("module_name") or ""): item
        for item in validation_results
        if isinstance(item, dict)
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project_root": output.inventory.project_root,
        "module_count": len(output.transition_mapping.modules),
        "transition_spec_count": len(output.transition_specs),
        "bff_sql_artifact_count": len(output.bff_sql_artifacts),
        "ui_pseudo_artifact_count": len(output.ui_pseudo_artifacts),
        "ui_reference_artifact_count": len(output.ui_reference_artifacts),
        "ui_integration_artifact_count": len(output.ui_integration_artifacts),
        "validated_modules": sorted(validation_by_subject.keys()),
        "ready_modules": ready_specs,
        "remaining_blockers": [
            {
                "task_id": item.task_id,
                "task_type": item.task_type,
                "phase": item.phase,
                "priority": item.priority,
                "module_name": item.module_name,
                "subject_name": item.subject_name,
                "reason": item.reason,
            }
            for item in blockers[:20]
        ],
        "prompt_pack_goals": sorted({item.goal for item in output.prompt_packs}),
        "compact_guides": [
            "llm-pack/backend-sql-guide.md",
            "llm-pack/backend-sql-manifest.json",
            "llm-pack/ui-handoff-guide.md",
            "llm-pack/ui-handoff-manifest.json",
        ],
        "recommended_artifacts": [
            "llm-pack/project-summary.md",
            "llm-pack/load-plan.json",
            "llm-pack/transition-specs/",
            "llm-pack/bff-sql/",
            "llm-pack/ui-pseudo/",
            "llm-pack/ui-reference/",
            "llm-pack/ui-integration/",
            "llm-pack/backend-sql-guide.md",
            "llm-pack/ui-handoff-guide.md",
            "prompt-pack/",
            "runtime/state-summary.md",
        ],
    }


def _derive_runtime_status(
    current_phase: str,
    blockers: list[Any],
    completeness: ArtifactCompleteness,
) -> str:
    if completeness.completed_count >= completeness.required_count and not blockers:
        return "completed"
    if current_phase == "transition_validate":
        return "waiting_for_validation"
    if blockers:
        return "ready_for_loop"
    return "prepared"


def _render_phase_summary(phase_state: Any) -> str:
    blockers = "\n".join(f"- {item}" for item in phase_state.blockers) or "- None"
    inputs = "\n".join(f"- {item}" for item in phase_state.input_artifacts) or "- None"
    outputs = "\n".join(f"- {item}" for item in phase_state.output_artifacts) or "- None"
    criteria = "\n".join(f"- {item}" for item in phase_state.done_criteria) or "- None"
    notes = "\n".join(f"- {item}" for item in phase_state.notes) or "- None"
    return f"""# Phase Summary: {phase_state.phase}

- Status: {phase_state.status}
- Completion score: {phase_state.completion_score}/100

## Blockers

{blockers}

## Inputs

{inputs}

## Outputs

{outputs}

## Done Criteria

{criteria}

## Notes

{notes}
"""


def _render_runtime_summary(
    run_state: RunState,
    blockers: list[Any],
    completeness: ArtifactCompleteness,
    metrics: LoopMetrics,
) -> str:
    top_blockers = "\n".join(
        f"- {item.task_id}: {item.task_type} ({item.priority}) {item.reason}" for item in blockers[:10]
    ) or "- None"
    return f"""# Runtime Summary

- Run ID: {run_state.run_id}
- Status: {run_state.status}
- Current phase: {run_state.current_phase}
- Loop iteration: {run_state.loop_iteration}
- Dispatch mode: {run_state.dispatch_mode}
- Target model profile: {run_state.target_model_profile}
- Artifact completeness: {completeness.completed_count}/{completeness.required_count}
- Blocking task: {run_state.blocking_task_id or 'None'}
- Backend/UI compact guides: {sum(1 for key, value in completeness.items.items() if key.endswith('_artifacts') and value)}

## Top Blockers

{top_blockers}

## Loop Metrics

- Successful tasks: {metrics.successful_tasks}
- Failed tasks: {metrics.failed_tasks}
- Validation rejections: {metrics.validation_rejections}
- Fallback uses: {metrics.fallback_uses}
"""


def _render_phase_delta(
    run_state: RunState,
    blockers: list[Any],
    completeness: ArtifactCompleteness,
) -> str:
    lines = [
        "# Phase Delta",
        "",
        f"- Current phase: {run_state.current_phase}",
        f"- Required artifacts complete: {completeness.completed_count}/{completeness.required_count}",
        f"- Remaining blocker count: {len(blockers)}",
    ]
    if blockers:
        lines.append(f"- Highest-priority blocker: {blockers[0].task_id}")
    else:
        lines.append("- No blocking unknowns remain.")
    lines.append("")
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
