from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.utils import ensure_directory, write_json


@dataclass(slots=True)
class PhaseState:
    phase: str
    status: str
    completion_score: int
    blockers: list[str] = field(default_factory=list)
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    done_criteria: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BlockingUnknown:
    task_id: str
    task_type: str
    phase: str
    priority: int
    module_name: str | None = None
    subject_name: str | None = None
    reason: str = ""
    source_artifacts: list[str] = field(default_factory=list)
    retry_count: int = 0
    fingerprint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArtifactCompleteness:
    items: dict[str, bool] = field(default_factory=dict)
    completed_count: int = 0
    required_count: int = 0


@dataclass(slots=True)
class LoopMetrics:
    total_iterations: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    validation_rejections: int = 0
    fallback_uses: int = 0
    cline_dispatches: int = 0
    provider_dispatches: int = 0
    manual_dispatches: int = 0
    blockers_resolved: int = 0
    accepted_rules: int = 0
    tokens_requested: int = 0
    tokens_completed: int = 0
    last_task_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunState:
    run_id: str
    project_root: str
    analysis_dir: str
    target_model_profile: str
    dispatch_mode: str
    current_phase: str
    loop_iteration: int
    status: str
    blocking_task_id: str | None = None
    completed_artifacts: int = 0
    required_artifacts: int = 0
    stop_reason: str | None = None
    analysis_config: dict[str, Any] = field(default_factory=dict)
    provider_config: dict[str, Any] = field(default_factory=dict)
    last_updated_at: str | None = None


def load_run_state(runtime_dir: Path) -> RunState | None:
    path = runtime_dir / "run-state.json"
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return None
    return RunState(
        run_id=str(payload.get("run_id") or ""),
        project_root=str(payload.get("project_root") or ""),
        analysis_dir=str(payload.get("analysis_dir") or ""),
        target_model_profile=str(payload.get("target_model_profile") or "qwen3_128k_weak"),
        dispatch_mode=str(payload.get("dispatch_mode") or "manual"),
        current_phase=str(payload.get("current_phase") or "workspace"),
        loop_iteration=int(payload.get("loop_iteration") or 0),
        status=str(payload.get("status") or "idle"),
        blocking_task_id=_optional_str(payload.get("blocking_task_id")),
        completed_artifacts=int(payload.get("completed_artifacts") or 0),
        required_artifacts=int(payload.get("required_artifacts") or 0),
        stop_reason=_optional_str(payload.get("stop_reason")),
        analysis_config=payload.get("analysis_config") if isinstance(payload.get("analysis_config"), dict) else {},
        provider_config=payload.get("provider_config") if isinstance(payload.get("provider_config"), dict) else {},
        last_updated_at=_optional_str(payload.get("last_updated_at")),
    )


def load_loop_metrics(runtime_dir: Path) -> LoopMetrics:
    path = runtime_dir / "metrics.json"
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return LoopMetrics()
    return LoopMetrics(
        total_iterations=int(payload.get("total_iterations") or 0),
        successful_tasks=int(payload.get("successful_tasks") or 0),
        failed_tasks=int(payload.get("failed_tasks") or 0),
        validation_rejections=int(payload.get("validation_rejections") or 0),
        fallback_uses=int(payload.get("fallback_uses") or 0),
        cline_dispatches=int(payload.get("cline_dispatches") or 0),
        provider_dispatches=int(payload.get("provider_dispatches") or 0),
        manual_dispatches=int(payload.get("manual_dispatches") or 0),
        blockers_resolved=int(payload.get("blockers_resolved") or 0),
        accepted_rules=int(payload.get("accepted_rules") or 0),
        tokens_requested=int(payload.get("tokens_requested") or 0),
        tokens_completed=int(payload.get("tokens_completed") or 0),
        last_task_id=_optional_str(payload.get("last_task_id")),
        notes=[item for item in payload.get("notes", []) if isinstance(item, str)]
        if isinstance(payload.get("notes"), list)
        else [],
    )


def save_run_state(runtime_dir: Path, state: RunState) -> None:
    ensure_directory(runtime_dir)
    write_json(runtime_dir / "run-state.json", state)


def save_loop_metrics(runtime_dir: Path, metrics: LoopMetrics) -> None:
    ensure_directory(runtime_dir)
    write_json(runtime_dir / "metrics.json", metrics)


def save_phase_states(runtime_dir: Path, phase_states: list[PhaseState]) -> None:
    phase_dir = runtime_dir / "phases"
    ensure_directory(phase_dir)
    write_json(phase_dir / "phase-status.json", phase_states)
    for phase_state in phase_states:
        current_dir = phase_dir / phase_state.phase
        ensure_directory(current_dir)
        write_json(current_dir / "phase-status.json", phase_state)


def save_blocking_unknowns(runtime_dir: Path, blockers: list[BlockingUnknown]) -> None:
    ensure_directory(runtime_dir)
    write_json(runtime_dir / "blocking-unknowns.json", blockers)


def save_artifact_completeness(runtime_dir: Path, completeness: ArtifactCompleteness) -> None:
    ensure_directory(runtime_dir)
    write_json(runtime_dir / "artifact-completeness.json", completeness)


def load_phase_states(runtime_dir: Path) -> list[PhaseState]:
    payload = _read_json(runtime_dir / "phases" / "phase-status.json")
    if not isinstance(payload, list):
        return []
    phase_states: list[PhaseState] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        phase_states.append(
            PhaseState(
                phase=str(item.get("phase") or "unknown"),
                status=str(item.get("status") or "pending"),
                completion_score=int(item.get("completion_score") or 0),
                blockers=[value for value in item.get("blockers", []) if isinstance(value, str)]
                if isinstance(item.get("blockers"), list)
                else [],
                input_artifacts=[value for value in item.get("input_artifacts", []) if isinstance(value, str)]
                if isinstance(item.get("input_artifacts"), list)
                else [],
                output_artifacts=[value for value in item.get("output_artifacts", []) if isinstance(value, str)]
                if isinstance(item.get("output_artifacts"), list)
                else [],
                done_criteria=[value for value in item.get("done_criteria", []) if isinstance(value, str)]
                if isinstance(item.get("done_criteria"), list)
                else [],
                notes=[value for value in item.get("notes", []) if isinstance(value, str)]
                if isinstance(item.get("notes"), list)
                else [],
            )
        )
    return phase_states


def load_blocking_unknowns(runtime_dir: Path) -> list[BlockingUnknown]:
    payload = _read_json(runtime_dir / "blocking-unknowns.json")
    if not isinstance(payload, list):
        return []
    blockers: list[BlockingUnknown] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        blockers.append(
            BlockingUnknown(
                task_id=str(item.get("task_id") or ""),
                task_type=str(item.get("task_type") or ""),
                phase=str(item.get("phase") or "clarify"),
                priority=int(item.get("priority") or 0),
                module_name=_optional_str(item.get("module_name")),
                subject_name=_optional_str(item.get("subject_name")),
                reason=str(item.get("reason") or ""),
                source_artifacts=[value for value in item.get("source_artifacts", []) if isinstance(value, str)]
                if isinstance(item.get("source_artifacts"), list)
                else [],
                retry_count=int(item.get("retry_count") or 0),
                fingerprint=_optional_str(item.get("fingerprint")),
                details=item.get("details") if isinstance(item.get("details"), dict) else {},
            )
        )
    return blockers


def load_artifact_completeness(runtime_dir: Path) -> ArtifactCompleteness | None:
    payload = _read_json(runtime_dir / "artifact-completeness.json")
    if not isinstance(payload, dict):
        return None
    items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    return ArtifactCompleteness(
        items={key: bool(value) for key, value in items.items() if isinstance(key, str)},
        completed_count=int(payload.get("completed_count") or 0),
        required_count=int(payload.get("required_count") or 0),
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
