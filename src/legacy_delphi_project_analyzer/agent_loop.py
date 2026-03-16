from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.cline import emit_cline_task, wait_for_cline_response
from legacy_delphi_project_analyzer.context_compiler import compact_runtime_state, compile_task_context
from legacy_delphi_project_analyzer.feedback import ingest_feedback_entries
from legacy_delphi_project_analyzer.llm import run_llm_artifact
from legacy_delphi_project_analyzer.model_profiles import get_model_profile
from legacy_delphi_project_analyzer.models import ValidationRecord, to_jsonable
from legacy_delphi_project_analyzer.orchestrator import (
    load_runtime_bundle,
    refresh_runtime_artifacts,
    rerun_analysis_from_runtime_state,
)
from legacy_delphi_project_analyzer.retry_planner import build_retry_plan, classify_validation_failure, write_retry_plan
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, load_taskpack, write_taskpacks
from legacy_delphi_project_analyzer.validators import validate_evidence, validate_schema
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def run_loop(
    analysis_dir: Path,
    *,
    dispatch_mode: str | None = None,
    max_loops: int = 10,
    max_task_attempts: int = 3,
    wait_seconds: int = 120,
    poll_seconds: float = 1.0,
    provider_base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    timeout_seconds: int = 120,
) -> Any:
    from legacy_delphi_project_analyzer.benchmarking import benchmark_prompts

    analysis_dir = analysis_dir.resolve()
    runtime_dir = analysis_dir / "runtime"
    bundle = load_runtime_bundle(analysis_dir)
    run_state = bundle["run_state"]
    if run_state is None:
        raise ValueError(f"Runtime state does not exist under {runtime_dir}")

    dispatch_mode = dispatch_mode or run_state.dispatch_mode
    if provider_base_url:
        run_state.provider_config["provider_base_url"] = provider_base_url
    if model:
        run_state.provider_config["model"] = model
    if api_key:
        run_state.provider_config["api_key"] = api_key
    if api_key_env:
        run_state.provider_config["api_key_env"] = api_key_env
    run_state.dispatch_mode = dispatch_mode

    attempts = _load_json(runtime_dir / "task-attempts.json", default={})
    history = _load_json(runtime_dir / "task-history.json", default=[])
    if not isinstance(attempts, dict):
        attempts = {}
    if not isinstance(history, list):
        history = []

    for _ in range(max_loops):
        output = rerun_analysis_from_runtime_state(analysis_dir)
        refresh_runtime_artifacts(
            output,
            target_model_profile=run_state.target_model_profile,
            dispatch_mode=dispatch_mode,
            analysis_config=run_state.analysis_config,
            provider_config=run_state.provider_config,
        )
        run_state = output.runtime_state
        assert run_state is not None
        compact_runtime_state(runtime_dir)

        if not output.blocking_unknowns and output.artifact_completeness and (
            output.artifact_completeness.completed_count >= output.artifact_completeness.required_count
        ):
            run_state.status = "completed"
            run_state.stop_reason = "all_required_artifacts_ready"
            _save_loop_files(runtime_dir, run_state, attempts, history)
            benchmark_prompts(analysis_dir)
            return run_state

        next_task = _select_next_task(output, attempts, max_task_attempts)
        if next_task is None:
            run_state.status = "completed"
            run_state.stop_reason = "max_task_attempts_reached"
            _save_loop_files(runtime_dir, run_state, attempts, history)
            benchmark_prompts(analysis_dir)
            return run_state

        taskpacks = build_taskpacks(output, run_state)
        write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
        task_dir = runtime_dir / "taskpacks" / next_task.task_id
        taskpack = load_taskpack(task_dir)
        if taskpack is None:
            raise ValueError(f"Task pack is missing for {next_task.task_id}")
        compiled = compile_task_context(
            analysis_dir=analysis_dir,
            runtime_dir=runtime_dir,
            taskpack=taskpack,
            task_dir=task_dir,
        )
        prompt_mode = _prompt_mode_for_attempts(int(attempts.get(taskpack.task_id, 0)))
        response_payload, response_path = _dispatch_task(
            analysis_dir=analysis_dir,
            runtime_dir=runtime_dir,
            task_dir=task_dir,
            taskpack=taskpack,
            compiled_payload_path=Path(compiled.compiled_payload_path or task_dir / "taskpack-compiled.json"),
            dispatch_mode=dispatch_mode,
            prompt_mode=prompt_mode,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            provider_config=run_state.provider_config,
        )
        if response_payload is None:
            run_state.status = {
                "manual": "waiting_for_manual_response",
                "cline": "waiting_for_cline_response",
                "provider": "provider_failed",
            }.get(dispatch_mode, "waiting")
            run_state.stop_reason = f"no_response_for_{taskpack.task_id}"
            attempts[taskpack.task_id] = int(attempts.get(taskpack.task_id, 0)) + 1
            history.append(
                {
                    "task_id": taskpack.task_id,
                    "dispatch_mode": dispatch_mode,
                    "prompt_mode": prompt_mode,
                    "status": run_state.status,
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
            )
            _save_loop_files(runtime_dir, run_state, attempts, history)
            benchmark_prompts(analysis_dir)
            return run_state

        validation = validate_task_response(
            analysis_dir=analysis_dir,
            task_dir=task_dir,
            response_payload=response_payload,
            response_path=response_path,
            prompt_mode=prompt_mode,
        )
        attempts[taskpack.task_id] = int(attempts.get(taskpack.task_id, 0)) + 1
        if validation.status in {"accepted", "accepted_with_warnings"}:
            ingest_feedback_entries(
                analysis_dir,
                [
                    {
                        "prompt_name": taskpack.source_prompt_name or taskpack.task_id,
                        "status": "accepted",
                        "used_fallback": prompt_mode == "fallback",
                        "target_model": run_state.provider_config.get("model")
                        if isinstance(run_state.provider_config, dict)
                        else None,
                        "response": validation.parsed_response,
                    }
                ],
            )
            run_state.loop_iteration += 1
            run_state.status = "ready_for_loop"
        else:
            run_state.loop_iteration += 1
            run_state.status = "ready_for_loop"
        _update_metrics(run_state, runtime_dir, validation, dispatch_mode, response_payload)
        history.append(
            {
                "task_id": taskpack.task_id,
                "dispatch_mode": dispatch_mode,
                "prompt_mode": prompt_mode,
                "validation_status": validation.status,
                "recorded_at": validation.validated_at,
            }
        )
        _save_loop_files(runtime_dir, run_state, attempts, history)
        benchmark_prompts(analysis_dir)

        if validation.status == "accepted":
            continue
        if attempts[taskpack.task_id] >= max_task_attempts:
            run_state.stop_reason = f"max_attempts:{taskpack.task_id}"
    run_state.status = "stopped"
    run_state.stop_reason = "max_loops_reached"
    _save_loop_files(runtime_dir, run_state, attempts, history)
    benchmark_prompts(analysis_dir)
    return run_state


def validate_task_response(
    *,
    analysis_dir: Path,
    task_dir: Path,
    response_payload: dict[str, Any] | None = None,
    response_path: Path | None = None,
    prompt_mode: str = "primary",
) -> ValidationRecord:
    analysis_dir = analysis_dir.resolve()
    task_dir = task_dir.resolve()
    taskpack = load_taskpack(task_dir)
    if taskpack is None:
        raise ValueError(f"Task pack does not exist or is invalid: {task_dir}")
    if response_payload is None:
        if response_path is None:
            response_path = task_dir / "agent-response.json"
        if not response_path.exists():
            runtime_dir = analysis_dir / "runtime"
            response_path = runtime_dir / "cline-outbox" / taskpack.task_id / "response.json"
        response_payload = _load_json(response_path, default={})
    output = rerun_analysis_from_runtime_state(analysis_dir)
    bundle = load_runtime_bundle(analysis_dir)
    existing_run_state = bundle["run_state"]
    refresh_runtime_artifacts(
        output,
        target_model_profile=existing_run_state.target_model_profile if existing_run_state else "qwen3_128k_weak",
        dispatch_mode=existing_run_state.dispatch_mode if existing_run_state else "manual",
        analysis_config=existing_run_state.analysis_config if existing_run_state else {},
        provider_config=existing_run_state.provider_config if existing_run_state else {},
    )
    parsed_response = _normalize_response_payload(response_payload)
    schema_valid, schema_issues = validate_schema(parsed_response, taskpack.expected_output_schema)
    evidence_valid, supported_claims, unsupported_claims, missing_evidence = validate_evidence(taskpack, parsed_response, output)
    if not schema_valid:
        status = "rejected"
    elif evidence_valid and not missing_evidence:
        status = "accepted"
    elif supported_claims and (unsupported_claims or missing_evidence):
        status = "accepted_with_warnings"
    elif supported_claims:
        status = "accepted"
    else:
        status = "needs_follow_up" if missing_evidence else "rejected"

    rejection_category = classify_validation_failure(
        ValidationRecord(
            task_id=taskpack.task_id,
            task_type=taskpack.task_type,
            prompt_mode=prompt_mode,
            status=status,
            schema_valid=schema_valid,
            evidence_valid=evidence_valid,
            analysis_dir=analysis_dir.as_posix(),
            subject_name=taskpack.subject_name,
            module_name=taskpack.module_name,
            response_path=response_path.as_posix() if response_path else None,
            parsed_response=parsed_response,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            missing_evidence=missing_evidence,
            issues=schema_issues + unsupported_claims,
        )
    )
    record = ValidationRecord(
        task_id=taskpack.task_id,
        task_type=taskpack.task_type,
        prompt_mode=prompt_mode,
        status=status,
        schema_valid=schema_valid,
        evidence_valid=evidence_valid,
        analysis_dir=analysis_dir.as_posix(),
        subject_name=taskpack.subject_name,
        module_name=taskpack.module_name,
        response_path=response_path.as_posix() if response_path else None,
        parsed_response=parsed_response,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        missing_evidence=missing_evidence,
        issues=schema_issues + unsupported_claims,
        rejection_category=rejection_category,
        should_learn=status in {"accepted", "accepted_with_warnings"},
        should_retry=status in {"needs_follow_up", "rejected"},
        validated_at=datetime.now(UTC).isoformat(),
    )
    retry_plan = build_retry_plan(analysis_dir=analysis_dir, taskpack=taskpack, record=record)
    record.validator_feedback = [str(item) for item in retry_plan.get("validator_feedback", [])]
    record.repair_prompt = str(retry_plan.get("repair_prompt") or "") or None
    record.retry_context_paths = [
        str(item)
        for item in retry_plan.get("retry_context_paths", [])
        if isinstance(item, str)
    ]
    _write_validation_record(analysis_dir / "runtime", task_dir, record)
    write_retry_plan(task_dir, retry_plan)
    return record


def load_task_history(runtime_dir: Path) -> list[dict[str, Any]]:
    payload = _load_json(runtime_dir / "task-history.json", default=[])
    return payload if isinstance(payload, list) else []


def load_task_attempts(runtime_dir: Path) -> dict[str, int]:
    payload = _load_json(runtime_dir / "task-attempts.json", default={})
    if not isinstance(payload, dict):
        return {}
    return {str(key): int(value) for key, value in payload.items() if isinstance(key, str)}


def _dispatch_task(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    task_dir: Path,
    taskpack: Any,
    compiled_payload_path: Path,
    dispatch_mode: str,
    prompt_mode: str,
    wait_seconds: int,
    poll_seconds: float,
    timeout_seconds: int,
    provider_config: dict[str, Any],
) -> tuple[dict[str, Any] | None, Path | None]:
    if dispatch_mode == "manual":
        response_path = task_dir / "agent-response.json"
        payload = _load_json(response_path, default=None) if response_path.exists() else None
        return payload if isinstance(payload, dict) else None, response_path if response_path.exists() else None
    if dispatch_mode == "cline":
        emit_cline_task(taskpack, task_dir, runtime_dir)
        payload = wait_for_cline_response(
            taskpack.task_id,
            runtime_dir,
            timeout_seconds=wait_seconds,
            poll_interval_seconds=poll_seconds,
        )
        response_path = runtime_dir / "cline-outbox" / taskpack.task_id / "response.json"
        return payload, response_path if payload is not None else None
    if dispatch_mode != "provider":
        raise ValueError(f"Unsupported dispatch mode: {dispatch_mode}")
    provider_base_url = provider_config.get("provider_base_url")
    model = provider_config.get("model")
    if not isinstance(provider_base_url, str) or not provider_base_url or not isinstance(model, str) or not model:
        raise ValueError("Provider loop requires provider_base_url and model in runtime/provider config.")
    result = run_llm_artifact(
        analysis_dir=analysis_dir,
        artifact_json_path=compiled_payload_path,
        provider_base_url=provider_base_url,
        model=model,
        api_key=provider_config.get("api_key") if isinstance(provider_config.get("api_key"), str) else None,
        api_key_env=str(provider_config.get("api_key_env") or "OPENAI_API_KEY"),
        prompt_mode=prompt_mode,
        token_limit=int(provider_config.get("token_limit") or get_model_profile(taskpack.target_model_profile).max_input_tokens),
        output_token_limit=int(provider_config.get("output_token_limit") or get_model_profile(taskpack.target_model_profile).max_output_tokens),
        temperature=float(provider_config.get("temperature") or get_model_profile(taskpack.target_model_profile).temperature),
        timeout_seconds=timeout_seconds,
    )
    response_wrapper = {
        "task_id": taskpack.task_id,
        "status": "completed",
        "result": result.parsed_response,
        "response_text": result.response_text,
        "llm_run_id": result.run_id,
        "included_context_paths": result.included_context_paths,
        "skipped_context_paths": result.skipped_context_paths,
        "usage": result.usage,
    }
    response_path = task_dir / "agent-response.json"
    write_json(response_path, response_wrapper)
    return response_wrapper, response_path


def _select_next_task(output: Any, attempts: dict[str, int], max_task_attempts: int) -> Any | None:
    blockers = list(output.blocking_unknowns or [])
    profile = get_model_profile(output.runtime_state.target_model_profile if output.runtime_state else "qwen3_128k_weak")
    candidates = []
    for blocker in blockers:
        retry_count = int(attempts.get(blocker.task_id, 0))
        if retry_count >= max_task_attempts:
            continue
        score = blocker.priority
        if profile.preferred_task_types and blocker.task_type in profile.preferred_task_types:
            score += 15
        score -= retry_count * 10
        candidates.append((score, blocker))
    candidates.sort(key=lambda item: (-item[0], item[1].task_id))
    return candidates[0][1] if candidates else None


def _prompt_mode_for_attempts(attempt_count: int) -> str:
    if attempt_count <= 0:
        return "primary"
    if attempt_count == 1:
        return "fallback"
    return "primary"


def _normalize_response_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    response = payload.get("response")
    if isinstance(response, dict):
        return response
    return payload


def _write_validation_record(runtime_dir: Path, task_dir: Path, record: ValidationRecord) -> None:
    ensure_directory(runtime_dir)
    write_json(task_dir / "validation-result.json", record)
    history_path = task_dir / "validation-history.json"
    task_history = _load_json(history_path, default=[])
    if not isinstance(task_history, list):
        task_history = []
    task_history.append(to_jsonable(record))
    write_json(history_path, task_history)
    path = runtime_dir / "validation-results.json"
    payload = _load_json(path, default=[])
    if not isinstance(payload, list):
        payload = []
    payload = [
        item
        for item in payload
        if not (isinstance(item, dict) and str(item.get("task_id") or "") == record.task_id)
    ]
    payload.append(to_jsonable(record))
    write_json(path, payload)
    runtime_history_path = runtime_dir / "validation-history.json"
    runtime_history = _load_json(runtime_history_path, default=[])
    if not isinstance(runtime_history, list):
        runtime_history = []
    runtime_history.append(to_jsonable(record))
    write_json(runtime_history_path, runtime_history)


def _update_metrics(
    run_state: Any,
    runtime_dir: Path,
    validation: ValidationRecord,
    dispatch_mode: str,
    response_payload: dict[str, Any],
) -> None:
    metrics_path = runtime_dir / "metrics.json"
    metrics = _load_json(metrics_path, default={})
    if not isinstance(metrics, dict):
        metrics = {}
    metrics["total_iterations"] = int(metrics.get("total_iterations") or 0) + 1
    if validation.status in {"accepted", "accepted_with_warnings"}:
        metrics["successful_tasks"] = int(metrics.get("successful_tasks") or 0) + 1
    else:
        metrics["failed_tasks"] = int(metrics.get("failed_tasks") or 0) + 1
        if validation.status == "rejected":
            metrics["validation_rejections"] = int(metrics.get("validation_rejections") or 0) + 1
    metrics["last_task_id"] = validation.task_id
    key = {
        "manual": "manual_dispatches",
        "cline": "cline_dispatches",
        "provider": "provider_dispatches",
    }.get(dispatch_mode, "manual_dispatches")
    metrics[key] = int(metrics.get(key) or 0) + 1
    usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
    if isinstance(usage, dict):
        metrics["tokens_requested"] = int(metrics.get("tokens_requested") or 0) + int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        metrics["tokens_completed"] = int(metrics.get("tokens_completed") or 0) + int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
    write_json(metrics_path, metrics)


def _save_loop_files(runtime_dir: Path, run_state: Any, attempts: dict[str, int], history: list[dict[str, Any]]) -> None:
    write_json(runtime_dir / "run-state.json", run_state)
    write_json(runtime_dir / "task-attempts.json", attempts)
    write_json(runtime_dir / "task-history.json", history)
    write_json(
        runtime_dir / "loop-state.json",
        {
            "run_id": run_state.run_id,
            "status": run_state.status,
            "current_phase": run_state.current_phase,
            "loop_iteration": run_state.loop_iteration,
            "blocking_task_id": run_state.blocking_task_id,
            "stop_reason": run_state.stop_reason,
        },
    )
    write_text(
        runtime_dir / "loop-summary.md",
        "\n".join(
            [
                "# Agent Loop Summary",
                "",
                f"- Status: {run_state.status}",
                f"- Current phase: {run_state.current_phase}",
                f"- Loop iteration: {run_state.loop_iteration}",
                f"- Blocking task: {run_state.blocking_task_id or 'None'}",
                f"- Stop reason: {run_state.stop_reason or 'None'}",
                f"- History entries: {len(history)}",
            ]
        )
        + "\n",
    )


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default
