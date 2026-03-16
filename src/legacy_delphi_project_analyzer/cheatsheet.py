from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.phase_state import ArtifactCompleteness, BlockingUnknown, RunState
from legacy_delphi_project_analyzer.utils import write_json, write_text


def write_analysis_cheat_sheet(output: Any) -> dict[str, str]:
    output_root = Path(str(output.output_dir)).resolve()
    llm_pack_dir = output_root / "llm-pack"
    markdown_path = llm_pack_dir / "cline-cheat-sheet.md"
    json_path = llm_pack_dir / "cline-cheat-sheet.json"
    payload = build_analysis_cheat_sheet_payload(output)
    write_text(markdown_path, render_analysis_cheat_sheet_markdown(payload))
    write_json(json_path, payload)
    return {
        "markdown_path": markdown_path.as_posix(),
        "json_path": json_path.as_posix(),
    }


def write_runtime_cheat_sheet(
    *,
    analysis_dir: Path,
    run_state: RunState,
    blockers: list[BlockingUnknown],
    completeness: ArtifactCompleteness | None,
) -> dict[str, str]:
    analysis_dir = analysis_dir.resolve()
    runtime_dir = analysis_dir / "runtime"
    markdown_path = runtime_dir / "cline-cheat-sheet.md"
    json_path = runtime_dir / "cline-cheat-sheet.json"
    payload = build_runtime_cheat_sheet_payload(
        analysis_dir=analysis_dir,
        run_state=run_state,
        blockers=blockers,
        completeness=completeness,
    )
    write_text(markdown_path, render_runtime_cheat_sheet_markdown(payload))
    write_json(json_path, payload)
    return {
        "markdown_path": markdown_path.as_posix(),
        "json_path": json_path.as_posix(),
    }


def build_analysis_cheat_sheet_payload(output: Any) -> dict[str, Any]:
    analysis_dir = Path(str(output.output_dir)).resolve()
    load_bundles = list(getattr(output, "load_bundles", []) or [])
    prompt_packs = list(getattr(output, "prompt_packs", []) or [])
    transition_specs = list(getattr(output, "transition_specs", []) or [])
    recommended_backend = [
        bundle.name
        for bundle in load_bundles
        if getattr(bundle, "category", "") == "backend-sql"
    ][:3]
    recommended_ui = [
        bundle.name
        for bundle in load_bundles
        if getattr(bundle, "category", "") == "ui"
    ][:3]
    recommended_integration = [
        bundle.name
        for bundle in load_bundles
        if getattr(bundle, "category", "") == "ui-integration"
    ][:3]
    return {
        "analysis_dir": analysis_dir.as_posix(),
        "quick_start": [
            f"legacy-delphi-analyzer run-phases {output.inventory.project_root} --output-dir {analysis_dir.as_posix()}",
            f"legacy-delphi-analyzer build-taskpacks {analysis_dir.as_posix()}",
            f"legacy-delphi-analyzer validate-provider --provider-base-url <url> --model <model> --verbose",
        ],
        "single_task_flow": [
            "Open one runtime/taskpacks/<task-id>/ directory.",
            "Copy only agent-task.md, compiled-context.md, and agent-expected-output-schema.json into Cline.",
            "Force JSON-only output.",
            "Save the answer to runtime/taskpacks/<task-id>/agent-response.json.",
            f"Run legacy-delphi-analyzer validate-response {analysis_dir.as_posix()} <task-id>.",
            f"If validation fails, run legacy-delphi-analyzer retry-plan {analysis_dir.as_posix()} <task-id> and feed retry-plan.md back to Cline.",
            f"For automation, use legacy-delphi-analyzer run-cline-wrapper {analysis_dir.as_posix()} --cline-cmd <your cline command> --watch.",
        ],
        "artifacts": {
            "project_summary": (analysis_dir / "llm-pack" / "project-summary.md").as_posix(),
            "load_plan": (analysis_dir / "llm-pack" / "load-plan.json").as_posix(),
            "taskpacks_root": (analysis_dir / "runtime" / "taskpacks").as_posix(),
            "backend_manifest": (analysis_dir / "llm-pack" / "backend-sql-manifest.json").as_posix(),
            "backend_guide": (analysis_dir / "llm-pack" / "backend-sql-guide.md").as_posix(),
            "ui_manifest": (analysis_dir / "llm-pack" / "ui-handoff-manifest.json").as_posix(),
            "ui_guide": (analysis_dir / "llm-pack" / "ui-handoff-guide.md").as_posix(),
            "target_integration_root": (analysis_dir / "llm-pack" / "target-integration").as_posix(),
        },
        "recommended_bundles": {
            "backend_sql": recommended_backend,
            "ui": recommended_ui,
            "ui_integration": recommended_integration,
        },
        "task_priority": [
            "infer_placeholder_meaning",
            "classify_query_intent",
            "validate_transition_spec",
            "bff-sql",
            "ui-pseudo-generation",
            "ui-integration-generation",
        ],
        "copy_paste_prompt_rules": [
            "Use exactly one task at a time.",
            "Only paste agent-task.md, compiled-context.md, and agent-expected-output-schema.json.",
            "Always tell Cline to output JSON only.",
            "Do not ask qwen3 to design an entire module in one prompt.",
            "Do not mix UI and SQL work in the same prompt.",
        ],
        "prompt_templates": {
            "backend_sql": (
                "請只根據我提供的 artifacts，產出一個 Spring Boot BFF endpoint 的 Oracle 19c SQL 實作邏輯。"
                " 只處理一個 endpoint，回傳 strict JSON。"
            ),
            "ui_pseudo": (
                "請只根據我提供的 artifacts，產出一個 React page 的 pseudo UI。"
                " 只處理一個 page，回傳 strict JSON。"
            ),
            "ui_integration": (
                "請只根據我提供的 artifacts，說明如何把這個 page 整合進既有的 React transition project。"
                " 只處理一個 page，回傳 strict JSON。"
            ),
        },
        "response_template": {
            "task_id": "<task-id>",
            "status": "completed",
            "result": {},
            "supported_claims": [],
            "unsupported_claims": [],
            "remaining_unknowns": [],
            "recommended_next_task": "",
        },
        "module_count": len(transition_specs),
        "prompt_pack_count": len(prompt_packs),
    }


def build_runtime_cheat_sheet_payload(
    *,
    analysis_dir: Path,
    run_state: RunState,
    blockers: list[BlockingUnknown],
    completeness: ArtifactCompleteness | None,
) -> dict[str, Any]:
    top_blockers = []
    for blocker in blockers[:5]:
        task_id = blocker.task_id
        top_blockers.append(
            {
                "task_id": task_id,
                "task_type": blocker.task_type,
                "module_name": blocker.module_name,
                "subject_name": blocker.subject_name,
                "reason": blocker.reason,
                "taskpack_dir": (analysis_dir / "runtime" / "taskpacks" / task_id).as_posix(),
                "dispatch_command": f"legacy-delphi-analyzer dispatch-task {analysis_dir.as_posix()} {task_id} --mode cline",
                "validate_command": f"legacy-delphi-analyzer validate-response {analysis_dir.as_posix()} {task_id}",
                "retry_command": f"legacy-delphi-analyzer retry-plan {analysis_dir.as_posix()} {task_id}",
            }
        )
    return {
        "analysis_dir": analysis_dir.as_posix(),
        "run_state": {
            "run_id": run_state.run_id,
            "status": run_state.status,
            "current_phase": run_state.current_phase,
            "dispatch_mode": run_state.dispatch_mode,
            "target_model_profile": run_state.target_model_profile,
            "blocking_task_id": run_state.blocking_task_id,
        },
        "artifact_progress": {
            "completed_count": completeness.completed_count if completeness else 0,
            "required_count": completeness.required_count if completeness else 0,
        },
        "commands": {
            "phase_status": f"legacy-delphi-analyzer phase-status {analysis_dir.as_posix()}",
            "build_taskpacks": f"legacy-delphi-analyzer build-taskpacks {analysis_dir.as_posix()}",
            "run_loop": f"legacy-delphi-analyzer run-loop {analysis_dir.as_posix()} --dispatch-mode cline --verbose",
            "resume_loop": f"legacy-delphi-analyzer resume-loop {analysis_dir.as_posix()} --verbose",
            "run_wrapper": f"legacy-delphi-analyzer run-cline-wrapper {analysis_dir.as_posix()} --cline-cmd <your cline command> --watch",
        },
        "top_blockers": top_blockers,
    }


def render_analysis_cheat_sheet_markdown(payload: dict[str, Any]) -> str:
    quick_start = "\n".join(f"```bash\n{item}\n```" for item in payload["quick_start"])
    single_task_flow = "\n".join(f"{index}. {item}" for index, item in enumerate(payload["single_task_flow"], start=1))
    task_priority = "\n".join(f"- `{item}`" for item in payload["task_priority"])
    artifact_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in payload["artifacts"].items())
    backend = "\n".join(f"- `{item}`" for item in payload["recommended_bundles"]["backend_sql"]) or "- None"
    ui = "\n".join(f"- `{item}`" for item in payload["recommended_bundles"]["ui"]) or "- None"
    integration = "\n".join(f"- `{item}`" for item in payload["recommended_bundles"]["ui_integration"]) or "- None"
    prompt_rules = "\n".join(f"- {item}" for item in payload["copy_paste_prompt_rules"])
    response_template = json_dump(payload["response_template"])
    return f"""# Cline Cheat Sheet

This sheet is optimized for **Cline CLI + weak qwen3-class models + 128k context limits**.

## Quick Start

{quick_start}

## Single-Task SOP

{single_task_flow}

## Priority Order

{task_priority}

## Key Artifact Paths

{artifact_lines}

## Recommended Bundle Order

### Backend SQL

{backend}

### UI

{ui}

### UI Integration

{integration}

## Copy/Paste Rules

{prompt_rules}

## Prompt Starters

- Backend SQL: {payload["prompt_templates"]["backend_sql"]}
- UI Pseudo: {payload["prompt_templates"]["ui_pseudo"]}
- UI Integration: {payload["prompt_templates"]["ui_integration"]}

## Response JSON Template

```json
{response_template}
```
"""


def render_runtime_cheat_sheet_markdown(payload: dict[str, Any]) -> str:
    blockers = payload["top_blockers"]
    if blockers:
        blocker_lines = []
        for item in blockers:
            blocker_lines.extend(
                [
                    f"## {item['task_id']}",
                    f"- Type: `{item['task_type']}`",
                    f"- Module: `{item.get('module_name') or 'Unknown'}`",
                    f"- Subject: `{item.get('subject_name') or 'Unknown'}`",
                    f"- Reason: {item['reason'] or 'None'}",
                    f"- Task pack: `{item['taskpack_dir']}`",
                    f"- Dispatch: `{item['dispatch_command']}`",
                    f"- Validate: `{item['validate_command']}`",
                    f"- Retry: `{item['retry_command']}`",
                    "",
                ]
            )
        blocker_text = "\n".join(blocker_lines).rstrip()
    else:
        blocker_text = "- No active blockers. Run `deliver-slice` or `generate-code` next.\n"
    return f"""# Runtime Cline Cheat Sheet

- Analysis dir: `{payload['analysis_dir']}`
- Run status: `{payload['run_state']['status']}`
- Current phase: `{payload['run_state']['current_phase']}`
- Dispatch mode: `{payload['run_state']['dispatch_mode']}`
- Model profile: `{payload['run_state']['target_model_profile']}`
- Artifact progress: `{payload['artifact_progress']['completed_count']}/{payload['artifact_progress']['required_count']}`

## Core Commands

```bash
{payload['commands']['phase_status']}
{payload['commands']['build_taskpacks']}
{payload['commands']['run_loop']}
{payload['commands']['resume_loop']}
{payload['commands']['run_wrapper']}
```

## Current Top Blockers

{blocker_text}
"""


def json_dump(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)
