from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.phase_state import BlockingUnknown
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


ERROR_GUIDANCE = {
    "schema_error": {
        "code": "TASK_SCHEMA_INVALID",
        "title": "Task output did not match the expected JSON schema",
        "next_best_action": "Use the retry plan and require JSON only.",
        "suggested_prompt": "Use retry-plan.md and ask the model to return strict JSON only.",
        "severity": "high",
    },
    "missing_evidence": {
        "code": "TASK_EVIDENCE_MISSING",
        "title": "Task response did not have enough grounded evidence",
        "next_best_action": "Keep the task small and add only one more directly related artifact if needed.",
        "suggested_prompt": "Use retry-plan.md and tell the model to keep unsupported items in remaining_unknowns.",
        "severity": "medium",
    },
    "unsupported_claims": {
        "code": "TASK_UNSUPPORTED_CLAIMS",
        "title": "Task response included claims not supported by the supplied artifacts",
        "next_best_action": "Use the retry plan and remove unsupported claims instead of adding more speculation.",
        "suggested_prompt": "Rewrite the answer using only supported evidence. Remove guessed behavior.",
        "severity": "high",
    },
    "follow_up_required": {
        "code": "TASK_FOLLOW_UP_REQUIRED",
        "title": "The model produced partial progress but still needs a bounded follow-up",
        "next_best_action": "Run the retry plan and keep the same task scope.",
        "suggested_prompt": "Use the retry-plan repair prompt without broadening task scope.",
        "severity": "medium",
    },
    "validation_failed": {
        "code": "TASK_VALIDATION_FAILED",
        "title": "The task failed validation",
        "next_best_action": "Inspect validation-result.json and retry-plan.md before asking a larger question.",
        "suggested_prompt": "Repair the answer to fit the current schema and evidence.",
        "severity": "high",
    },
    "no_response": {
        "code": "TASK_NO_RESPONSE",
        "title": "No model response was collected for the task",
        "next_best_action": "Check the Cline wrapper or provider logs, then rerun the same task.",
        "suggested_prompt": "No prompt recommendation. Fix the execution path first.",
        "severity": "high",
    },
    "provider_failed": {
        "code": "PROVIDER_CHAT_FAILED",
        "title": "The provider could not complete the chat request",
        "next_best_action": "Run validate-provider with --verbose and inspect provider-health.json.",
        "suggested_prompt": "Not applicable until the provider probe succeeds.",
        "severity": "critical",
    },
    "provider_non_json": {
        "code": "PROVIDER_NON_JSON",
        "title": "The provider returned a non-JSON response format",
        "next_best_action": "Check whether the provider is returning SSE, HTML, or proxy pages.",
        "suggested_prompt": "Not applicable until provider output is normalized.",
        "severity": "high",
    },
    "provider_sse_warning": {
        "code": "PROVIDER_SSE_WARNING",
        "title": "The provider is using SSE streaming responses",
        "next_best_action": "Use a streaming-safe wrapper or the built-in provider parsing path.",
        "suggested_prompt": "Not applicable. This is an integration warning.",
        "severity": "medium",
    },
    "task_timeout": {
        "code": "TASK_TIMEOUT",
        "title": "The task timed out before a valid response was collected",
        "next_best_action": "Retry the same task with the same scope. Do not add more context.",
        "suggested_prompt": "Repeat the same bounded task. Return JSON only.",
        "severity": "medium",
    },
    "context_too_large": {
        "code": "TASK_CONTEXT_TOO_LARGE",
        "title": "The task context likely exceeded the practical weak-model budget",
        "next_best_action": "Regenerate the task pack and keep only one page, endpoint, or query family.",
        "suggested_prompt": "Repeat with only the smallest evidence bundle.",
        "severity": "high",
    },
}


def load_provider_health(runtime_dir: Path) -> dict[str, Any] | None:
    path = runtime_dir / "provider-health.json"
    return _load_json(path)


def save_provider_health(runtime_dir: Path, payload: dict[str, Any]) -> Path:
    path = runtime_dir / "provider-health.json"
    write_json(path, payload)
    return path


def load_review_summary(runtime_dir: Path) -> dict[str, Any] | None:
    return _load_json(runtime_dir / "reviews" / "review-summary.json")


def build_runtime_error_summary(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    blockers: list[BlockingUnknown] | None = None,
) -> dict[str, Any]:
    validation_results = _load_json(runtime_dir / "validation-results.json") or []
    task_history = _load_json(runtime_dir / "task-history.json") or []
    provider_health = load_provider_health(runtime_dir)
    reviews = _load_json(runtime_dir / "reviews" / "review-history.json") or []

    items: list[dict[str, Any]] = []
    blocker_ids = {item.task_id for item in blockers or [] if item.task_id}

    if isinstance(validation_results, list):
        for item in validation_results:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status in {"accepted", "accepted_with_warnings"}:
                continue
            category = str(item.get("rejection_category") or "validation_failed")
            guidance = ERROR_GUIDANCE.get(category, ERROR_GUIDANCE["validation_failed"])
            task_id = str(item.get("task_id") or "")
            items.append(
                {
                    "code": guidance["code"],
                    "title": guidance["title"],
                    "severity": guidance["severity"],
                    "task_id": task_id,
                    "task_type": item.get("task_type"),
                    "module_name": item.get("module_name"),
                    "subject_name": item.get("subject_name"),
                    "status": status,
                    "category": category,
                    "what_happened": _what_happened_for_validation(item),
                    "next_best_action": guidance["next_best_action"],
                    "suggested_prompt": guidance["suggested_prompt"],
                    "suggested_command": _retry_command(analysis_dir, task_id) if task_id else "",
                    "is_blocking": task_id in blocker_ids,
                }
            )

    if isinstance(task_history, list):
        for item in task_history[-20:]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            task_id = str(item.get("task_id") or "")
            if not status or status in {"ready_for_loop"}:
                continue
            if "waiting_for" in status or "no_response" in status:
                guidance = ERROR_GUIDANCE["no_response"]
                items.append(
                    {
                        "code": guidance["code"],
                        "title": guidance["title"],
                        "severity": guidance["severity"],
                        "task_id": task_id,
                        "task_type": item.get("task_type"),
                        "module_name": item.get("module_name"),
                        "subject_name": item.get("subject_name"),
                        "status": status,
                        "category": "no_response",
                        "what_happened": f"The loop did not receive a response for task {task_id}.",
                        "next_best_action": guidance["next_best_action"],
                        "suggested_prompt": guidance["suggested_prompt"],
                        "suggested_command": _dispatch_command(analysis_dir, task_id) if task_id else "",
                        "is_blocking": task_id in blocker_ids,
                    }
                )

    if isinstance(provider_health, dict):
        items.extend(_provider_health_items(provider_health, analysis_dir))

    if isinstance(reviews, list):
        for item in reviews[-10:]:
            if not isinstance(item, dict):
                continue
            decision = str(item.get("decision") or "")
            if decision == "escalate":
                items.append(
                    {
                        "code": "TASK_ESCALATED_TO_HUMAN",
                        "title": "Task was escalated for human review",
                        "severity": "medium",
                        "task_id": item.get("task_id"),
                        "task_type": item.get("task_type"),
                        "module_name": item.get("module_name"),
                        "subject_name": item.get("subject_name"),
                        "status": "escalated",
                        "category": "human_review",
                        "what_happened": item.get("notes") or "A reviewer marked this task for manual follow-up.",
                        "next_best_action": "Review the attached task pack and retry with a smaller question if possible.",
                        "suggested_prompt": "Use the current task pack and ask one narrower question.",
                        "suggested_command": _retry_command(analysis_dir, str(item.get("task_id") or "")),
                        "is_blocking": str(item.get("task_id") or "") in blocker_ids,
                    }
                )

    counts_by_code: dict[str, int] = {}
    for item in items:
        code = str(item.get("code") or "UNKNOWN")
        counts_by_code[code] = counts_by_code.get(code, 0) + 1

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "item_count": len(items),
        "counts_by_code": counts_by_code,
        "items": items,
    }
    return payload


def write_runtime_error_summary(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    blockers: list[BlockingUnknown] | None = None,
) -> dict[str, Any]:
    errors_dir = runtime_dir / "errors"
    ensure_directory(errors_dir)
    payload = build_runtime_error_summary(
        analysis_dir=analysis_dir,
        runtime_dir=runtime_dir,
        blockers=blockers,
    )
    write_json(errors_dir / "error-summary.json", payload)
    write_text(errors_dir / "error-summary.md", render_runtime_error_summary_markdown(payload))
    for item in payload["items"]:
        task_id = str(item.get("task_id") or item.get("code") or "error")
        safe_name = task_id.replace("/", "-")
        write_text(errors_dir / f"{safe_name}.md", render_runtime_error_item_markdown(item))
    return payload


def render_runtime_error_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Runtime Error Summary",
        "",
        f"- Total items: {payload.get('item_count', 0)}",
        "",
        "## Counts By Code",
        "",
    ]
    counts = payload.get("counts_by_code", {})
    if isinstance(counts, dict) and counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- None")
    lines.extend(["", "## Items", ""])
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        lines.append("- No blocking or recent runtime errors.")
    else:
        for item in items:
            lines.append(
                f"- `{item.get('code')}` task=`{item.get('task_id') or 'n/a'}` severity=`{item.get('severity')}` "
                f"next=`{item.get('suggested_command') or item.get('next_best_action')}`"
            )
    lines.append("")
    return "\n".join(lines)


def render_runtime_error_item_markdown(item: dict[str, Any]) -> str:
    return f"""# {item.get('code')}

- Title: {item.get('title')}
- Severity: {item.get('severity')}
- Task ID: {item.get('task_id') or 'n/a'}
- Task Type: {item.get('task_type') or 'n/a'}
- Module: {item.get('module_name') or 'n/a'}
- Subject: {item.get('subject_name') or 'n/a'}
- Blocking: {str(bool(item.get('is_blocking'))).lower()}

## What Happened

{item.get('what_happened') or 'Unknown'}

## Next Best Action

{item.get('next_best_action') or 'None'}

## Suggested Prompt

```text
{item.get('suggested_prompt') or ''}
```

## Suggested Command

```bash
{item.get('suggested_command') or ''}
```
"""


def _provider_health_items(provider_health: dict[str, Any], analysis_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not bool(provider_health.get("ok")):
        code = "provider_non_json" if str(provider_health.get("response_format") or "") == "non-json" else "provider_failed"
        guidance = ERROR_GUIDANCE[code]
        items.append(
            {
                "code": guidance["code"],
                "title": guidance["title"],
                "severity": guidance["severity"],
                "task_id": "",
                "task_type": "provider_validation",
                "module_name": None,
                "subject_name": provider_health.get("selected_model"),
                "status": "provider_error",
                "category": code,
                "what_happened": "; ".join(str(item) for item in provider_health.get("debug", [])[:5]) or "Provider probe failed.",
                "next_best_action": guidance["next_best_action"],
                "suggested_prompt": guidance["suggested_prompt"],
                "suggested_command": (
                    f"legacy-delphi-analyzer validate-provider --provider-base-url {provider_health.get('provider_base_url')} "
                    f"--model {provider_health.get('selected_model') or provider_health.get('requested_model') or '<model>'} --verbose"
                ),
                "is_blocking": True,
            }
        )
    if str(provider_health.get("response_format") or "") == "sse":
        guidance = ERROR_GUIDANCE["provider_sse_warning"]
        items.append(
            {
                "code": guidance["code"],
                "title": guidance["title"],
                "severity": guidance["severity"],
                "task_id": "",
                "task_type": "provider_validation",
                "module_name": None,
                "subject_name": provider_health.get("selected_model"),
                "status": "warning",
                "category": "provider_sse_warning",
                "what_happened": "The provider reported SSE / text/event-stream responses on chat completions.",
                "next_best_action": guidance["next_best_action"],
                "suggested_prompt": guidance["suggested_prompt"],
                "suggested_command": "Use run-cline-wrapper --streaming or rely on the built-in provider SSE support.",
                "is_blocking": False,
            }
        )
    return items


def _what_happened_for_validation(item: dict[str, Any]) -> str:
    issues = item.get("issues")
    missing = item.get("missing_evidence")
    unsupported = item.get("unsupported_claims")
    parts: list[str] = []
    if isinstance(issues, list) and issues:
        parts.append("; ".join(str(value) for value in issues[:3]))
    if isinstance(missing, list) and missing:
        parts.append("Missing evidence: " + "; ".join(str(value) for value in missing[:3]))
    if isinstance(unsupported, list) and unsupported:
        parts.append("Unsupported claims: " + "; ".join(str(value) for value in unsupported[:3]))
    return " ".join(parts) or "The response did not satisfy schema or evidence validation."


def _retry_command(analysis_dir: Path, task_id: str) -> str:
    return f"legacy-delphi-analyzer retry-plan {analysis_dir.as_posix()} {task_id}"


def _dispatch_command(analysis_dir: Path, task_id: str) -> str:
    return f"legacy-delphi-analyzer dispatch-task {analysis_dir.as_posix()} {task_id} --mode cline"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
