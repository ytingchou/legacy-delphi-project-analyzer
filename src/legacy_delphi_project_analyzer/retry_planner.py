from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import ValidationRecord
from legacy_delphi_project_analyzer.utils import write_json, write_text


def classify_validation_failure(record: ValidationRecord) -> str | None:
    if record.status in {"accepted", "accepted_with_warnings"}:
        return None
    if not record.schema_valid:
        return "schema_error"
    if record.missing_evidence and not record.supported_claims:
        return "missing_evidence"
    if record.unsupported_claims:
        return "unsupported_claims"
    if record.status == "needs_follow_up":
        return "follow_up_required"
    return "validation_failed"


def build_retry_plan(
    *,
    analysis_dir: Path,
    taskpack: Any,
    record: ValidationRecord,
) -> dict[str, Any]:
    category = classify_validation_failure(record)
    feedback = _build_feedback_lines(record)
    repair_prompt = _build_repair_prompt(taskpack, feedback)
    next_prompt_mode = "fallback" if record.prompt_mode == "primary" else "verification"
    retry_context_paths = _limit_retry_context_paths(taskpack, category)
    return {
        "task_id": taskpack.task_id,
        "task_type": taskpack.task_type,
        "module_name": taskpack.module_name,
        "subject_name": taskpack.subject_name,
        "status": record.status,
        "rejection_category": category,
        "validator_feedback": feedback,
        "repair_prompt": repair_prompt,
        "next_prompt_mode": next_prompt_mode,
        "retry_context_paths": retry_context_paths,
        "analysis_dir": analysis_dir.as_posix(),
    }


def write_retry_plan(task_dir: Path, retry_plan: dict[str, Any]) -> None:
    write_json(task_dir / "retry-plan.json", retry_plan)
    write_text(task_dir / "retry-plan.md", _render_retry_plan_markdown(retry_plan))


def _build_feedback_lines(record: ValidationRecord) -> list[str]:
    feedback: list[str] = []
    if record.issues:
        feedback.extend(record.issues[:5])
    if record.missing_evidence:
        feedback.extend(f"Missing evidence: {item}" for item in record.missing_evidence[:5])
    if not feedback:
        feedback.append("Validator did not receive enough grounded evidence to accept the response.")
    return feedback


def _build_repair_prompt(taskpack: Any, feedback: list[str]) -> str:
    feedback_lines = "\n".join(f"- {item}" for item in feedback)
    return (
        f"Task:\nRepair the previous {taskpack.task_type} answer for {taskpack.subject_name or taskpack.task_id}.\n\n"
        "Validator Feedback:\n"
        f"{feedback_lines}\n\n"
        "Instructions:\n"
        "- Keep the answer inside the existing JSON schema.\n"
        "- Use only the attached evidence and trusted facts.\n"
        "- Remove unsupported claims instead of guessing.\n"
        "- Put unresolved items into missing_assumptions or remaining_unknowns.\n"
        "Output JSON only."
    )


def _limit_retry_context_paths(taskpack: Any, category: str | None) -> list[str]:
    context_paths = list(getattr(taskpack, "context_paths", []) or [])
    if category == "schema_error":
        return context_paths[:1]
    if category in {"missing_evidence", "follow_up_required"}:
        return context_paths[:2]
    return context_paths[:3]


def _render_retry_plan_markdown(retry_plan: dict[str, Any]) -> str:
    feedback_lines = "\n".join(f"- {item}" for item in retry_plan.get("validator_feedback", [])) or "- None"
    retry_paths = "\n".join(f"- {item}" for item in retry_plan.get("retry_context_paths", [])) or "- None"
    return f"""# Retry Plan: {retry_plan.get('task_id')}

- Task type: {retry_plan.get('task_type')}
- Status: {retry_plan.get('status')}
- Category: {retry_plan.get('rejection_category') or 'accepted'}
- Next prompt mode: {retry_plan.get('next_prompt_mode') or 'n/a'}

## Validator Feedback

{feedback_lines}

## Retry Context Paths

{retry_paths}

## Repair Prompt

```text
{retry_plan.get('repair_prompt') or ''}
```
"""
