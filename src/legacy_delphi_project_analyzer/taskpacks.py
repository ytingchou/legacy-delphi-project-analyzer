from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.model_profiles import ModelProfile, get_model_profile
from legacy_delphi_project_analyzer.phase_state import BlockingUnknown, RunState
from legacy_delphi_project_analyzer.utils import ensure_directory, estimate_tokens, read_text_file, slugify, write_json, write_text


@dataclass(slots=True)
class TaskPack:
    task_id: str
    task_type: str
    phase: str
    priority: int
    target_model_profile: str
    module_name: str | None = None
    subject_name: str | None = None
    issue_summary: str = ""
    context_paths: list[str] = field(default_factory=list)
    context_budget_tokens: int = 0
    primary_prompt: str | None = None
    fallback_prompt: str | None = None
    verification_prompt: str | None = None
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    acceptance_checks: list[str] = field(default_factory=list)
    source_prompt_name: str | None = None
    source_kind: str = "prompt-pack"
    notes: list[str] = field(default_factory=list)


def build_taskpacks(
    output: Any,
    run_state: RunState,
    *,
    max_tasks: int | None = None,
) -> list[TaskPack]:
    profile = get_model_profile(run_state.target_model_profile)
    blockers: list[BlockingUnknown] = list(output.blocking_unknowns or [])
    prompt_index = _build_prompt_index(output)
    taskpacks: list[TaskPack] = []

    for blocker in blockers:
        payload = _match_task_payload(blocker, prompt_index)
        if payload is None:
            continue
        payload_notes = (
            [item for item in payload.get("notes", []) if isinstance(item, str)]
            if isinstance(payload.get("notes"), list)
            else []
        )
        selected_paths = _limit_context_paths(
            [item for item in payload.get("context_paths", []) if isinstance(item, str)],
            profile.max_context_paths,
        )
        taskpacks.append(
            TaskPack(
                task_id=blocker.task_id,
                task_type=blocker.task_type,
                phase=blocker.phase,
                priority=blocker.priority,
                target_model_profile=profile.name,
                module_name=blocker.module_name,
                subject_name=blocker.subject_name,
                issue_summary=str(payload.get("issue_summary") or payload.get("summary") or blocker.reason),
                context_paths=selected_paths,
                context_budget_tokens=min(
                    int(payload.get("context_budget_tokens") or profile.max_input_tokens),
                    profile.max_input_tokens,
                ),
                primary_prompt=_payload_prompt(payload, "prompt"),
                fallback_prompt=_payload_prompt(payload, "fallback_prompt"),
                verification_prompt=_payload_prompt(payload, "verification_prompt"),
                expected_output_schema=payload.get("expected_response_schema")
                if isinstance(payload.get("expected_response_schema"), dict)
                else {},
                acceptance_checks=[
                    item for item in payload.get("acceptance_checks", []) if isinstance(item, str)
                ]
                if isinstance(payload.get("acceptance_checks"), list)
                else [],
                source_prompt_name=str(payload.get("name") or ""),
                source_kind=str(payload.get("_source_kind") or "prompt-pack"),
                notes=[
                    f"priority={blocker.priority}",
                    f"profile={profile.name}",
                    *payload_notes,
                ],
            )
        )
        if max_tasks is not None and len(taskpacks) >= max_tasks:
            break

    output.taskpacks = taskpacks
    return taskpacks


def write_taskpacks(
    taskpacks: list[TaskPack],
    runtime_dir: Path,
    *,
    include_compiled_context: bool = False,
) -> list[Path]:
    taskpack_root = runtime_dir / "taskpacks"
    ensure_directory(taskpack_root)
    written_paths: list[Path] = []
    index_payload = []

    for taskpack in taskpacks:
        task_dir = taskpack_root / taskpack.task_id
        ensure_directory(task_dir)
        context_manifest = []
        for raw_path in taskpack.context_paths:
            path = Path(raw_path)
            context_manifest.append(
                {
                    "path": raw_path,
                    "exists": path.exists(),
                    "estimated_tokens": estimate_tokens(read_text_file(path)[0]) if path.exists() else 0,
                }
            )
        write_text(task_dir / "agent-task.md", render_taskpack_markdown(taskpack))
        write_json(task_dir / "agent-context.json", {"context_paths": taskpack.context_paths})
        write_json(task_dir / "agent-context-manifest.json", context_manifest)
        write_json(
            task_dir / "agent-run-config.json",
            {
                "task_id": taskpack.task_id,
                "task_type": taskpack.task_type,
                "target_model_profile": taskpack.target_model_profile,
                "max_input_tokens": taskpack.context_budget_tokens,
                "max_output_tokens": get_model_profile(taskpack.target_model_profile).max_output_tokens,
                "strict_json": get_model_profile(taskpack.target_model_profile).strict_json,
                "max_retries": 3,
                "fallback_enabled": True,
                "auto_compact": include_compiled_context,
            },
        )
        write_json(task_dir / "agent-expected-output-schema.json", taskpack.expected_output_schema)
        write_json(task_dir / "agent-acceptance-checks.json", {"checks": taskpack.acceptance_checks})
        write_json(
            task_dir / "agent-handoff-template.json",
            {
                "task_id": taskpack.task_id,
                "task_type": taskpack.task_type,
                "status": "completed|blocked|needs_follow_up",
                "result": {},
                "supported_claims": [],
                "unsupported_claims": [],
                "remaining_unknowns": [],
                "recommended_next_task": "",
            },
        )
        write_json(task_dir / "taskpack.json", taskpack_to_llm_payload(taskpack))
        index_payload.append(
            {
                "task_id": taskpack.task_id,
                "task_type": taskpack.task_type,
                "phase": taskpack.phase,
                "priority": taskpack.priority,
                "module_name": taskpack.module_name,
                "subject_name": taskpack.subject_name,
                "path": task_dir.as_posix(),
            }
        )
        written_paths.append(task_dir)

    write_json(taskpack_root / "taskpack-index.json", index_payload)
    return written_paths


def render_taskpack_markdown(taskpack: TaskPack) -> str:
    return f"""# Task Pack: {taskpack.task_id}

- Task type: {taskpack.task_type}
- Phase: {taskpack.phase}
- Priority: {taskpack.priority}
- Target model profile: {taskpack.target_model_profile}
- Module: {taskpack.module_name or "None"}
- Subject: {taskpack.subject_name or "None"}
- Source prompt: {taskpack.source_prompt_name or "None"}
- Issue summary: {taskpack.issue_summary}
- Context budget: {taskpack.context_budget_tokens}

## Context Paths

{_bullet_lines(taskpack.context_paths)}

## Primary Prompt

```text
{taskpack.primary_prompt or ""}
```

## Fallback Prompt

```text
{taskpack.fallback_prompt or ""}
```

## Verification Prompt

```text
{taskpack.verification_prompt or ""}
```

## Acceptance Checks

{_bullet_lines(taskpack.acceptance_checks)}

## Notes

{_bullet_lines(taskpack.notes)}
"""


def taskpack_to_llm_payload(taskpack: TaskPack) -> dict[str, Any]:
    return {
        "name": taskpack.task_id,
        "goal": taskpack.task_type,
        "subject_name": taskpack.subject_name,
        "target_model": taskpack.target_model_profile,
        "issue_summary": taskpack.issue_summary,
        "context_paths": taskpack.context_paths,
        "context_budget_tokens": taskpack.context_budget_tokens,
        "prompt": taskpack.primary_prompt,
        "fallback_prompt": taskpack.fallback_prompt,
        "verification_prompt": taskpack.verification_prompt,
        "expected_response_schema": taskpack.expected_output_schema,
        "acceptance_checks": taskpack.acceptance_checks,
        "notes": taskpack.notes,
        "task_id": taskpack.task_id,
        "task_type": taskpack.task_type,
        "module_name": taskpack.module_name,
        "phase": taskpack.phase,
    }


def load_taskpack(task_dir: Path) -> TaskPack | None:
    taskpack_path = task_dir / "taskpack.json"
    if not taskpack_path.exists():
        return None
    try:
        payload = json.loads(taskpack_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return TaskPack(
        task_id=str(payload.get("task_id") or payload.get("name") or ""),
        task_type=str(payload.get("task_type") or payload.get("goal") or ""),
        phase=str(payload.get("phase") or "clarify"),
        priority=int(payload.get("priority") or 0),
        target_model_profile=str(payload.get("target_model") or "qwen3_128k_weak"),
        module_name=payload.get("module_name") if isinstance(payload.get("module_name"), str) else None,
        subject_name=payload.get("subject_name") if isinstance(payload.get("subject_name"), str) else None,
        issue_summary=str(payload.get("issue_summary") or ""),
        context_paths=[item for item in payload.get("context_paths", []) if isinstance(item, str)]
        if isinstance(payload.get("context_paths"), list)
        else [],
        context_budget_tokens=int(payload.get("context_budget_tokens") or 0),
        primary_prompt=payload.get("prompt") if isinstance(payload.get("prompt"), str) else None,
        fallback_prompt=payload.get("fallback_prompt") if isinstance(payload.get("fallback_prompt"), str) else None,
        verification_prompt=payload.get("verification_prompt") if isinstance(payload.get("verification_prompt"), str) else None,
        expected_output_schema=payload.get("expected_response_schema")
        if isinstance(payload.get("expected_response_schema"), dict)
        else {},
        acceptance_checks=[item for item in payload.get("acceptance_checks", []) if isinstance(item, str)]
        if isinstance(payload.get("acceptance_checks"), list)
        else [],
        source_prompt_name=payload.get("source_prompt_name") if isinstance(payload.get("source_prompt_name"), str) else None,
        source_kind=payload.get("source_kind") if isinstance(payload.get("source_kind"), str) else "prompt-pack",
        notes=[item for item in payload.get("notes", []) if isinstance(item, str)]
        if isinstance(payload.get("notes"), list)
        else [],
    )


def _build_prompt_index(output: Any) -> list[dict[str, Any]]:
    prompt_entries: list[dict[str, Any]] = []
    for prompt in list(output.prompt_packs or []):
        payload = {
            "name": getattr(prompt, "name", None),
            "goal": getattr(prompt, "goal", None),
            "subject_name": getattr(prompt, "subject_name", None),
            "issue_summary": getattr(prompt, "issue_summary", None),
            "context_paths": list(getattr(prompt, "context_paths", [])),
            "context_budget_tokens": getattr(prompt, "context_budget_tokens", None),
            "prompt": getattr(prompt, "prompt", None),
            "fallback_prompt": getattr(prompt, "fallback_prompt", None),
            "verification_prompt": getattr(prompt, "verification_prompt", None),
            "expected_response_schema": getattr(prompt, "expected_response_schema", {}),
            "acceptance_checks": list(getattr(prompt, "acceptance_checks", [])),
            "notes": list(getattr(prompt, "notes", [])),
            "_source_kind": "prompt-pack",
        }
        prompt_entries.append(payload)
    for item in list(output.failure_triage or []):
        payload = {
            "name": getattr(item, "name", None),
            "goal": getattr(item, "goal", None),
            "subject_name": getattr(item, "subject_name", None),
            "issue_summary": getattr(item, "summary", None),
            "context_paths": list(getattr(item, "context_paths", [])),
            "context_budget_tokens": getattr(item, "context_budget_tokens", None),
            "prompt": getattr(item, "suggested_prompt", None),
            "fallback_prompt": getattr(item, "fallback_prompt", None),
            "verification_prompt": getattr(item, "verification_prompt", None),
            "expected_response_schema": {},
            "acceptance_checks": list(getattr(item, "acceptance_checks", [])),
            "notes": list(getattr(item, "notes", [])),
            "_source_kind": "failure-triage",
        }
        prompt_entries.append(payload)
    return prompt_entries


def _match_task_payload(blocker: BlockingUnknown, prompt_index: list[dict[str, Any]]) -> dict[str, Any] | None:
    preferred = []
    fallback = []
    for payload in prompt_index:
        if payload.get("goal") != blocker.task_type:
            continue
        subject = str(payload.get("subject_name") or "")
        source_name = str(payload.get("name") or "")
        if blocker.subject_name and (
            subject.lower() == blocker.subject_name.lower()
            or blocker.subject_name.lower() in source_name.lower()
        ):
            preferred.append(payload)
        else:
            fallback.append(payload)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return None


def _limit_context_paths(paths: list[str], max_paths: int) -> list[str]:
    deduped = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
        if len(deduped) >= max_paths:
            break
    return deduped


def _payload_prompt(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {item}" for item in values)
