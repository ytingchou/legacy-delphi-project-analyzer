from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.delivery import deliver_slices
from legacy_delphi_project_analyzer.developer_handoff import build_developer_handoff_packs
from legacy_delphi_project_analyzer.failure_replay import build_failure_replay_lab
from legacy_delphi_project_analyzer.patch_apply import build_patch_apply_assistant
from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.patch_validation import validate_patch_packs
from legacy_delphi_project_analyzer.progress_layer import update_progress_report
from legacy_delphi_project_analyzer.repair_tasks import build_repair_tasks
from legacy_delphi_project_analyzer.repo_validation import build_repo_validation_gate
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack
from legacy_delphi_project_analyzer.workspace_sync import build_transition_workspace_sync
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def run_controlled_delivery(
    analysis_dir: Path,
    *,
    output: Any,
    target_project_dir: Path | None = None,
    output_dir: Path | None = None,
    allow_unvalidated: bool = False,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    control_dir = (output_dir or analysis_dir / "delivery-control").resolve()
    ensure_directory(control_dir)
    runtime_dir = analysis_dir / "runtime"

    steps: list[dict[str, Any]] = []

    patch_manifest = build_code_patch_packs(analysis_dir=analysis_dir, output=output)
    steps.append({"step": "build_patch_packs", "status": "completed", "path": (analysis_dir / "llm-pack" / "code-patch-packs" / "manifest.json").as_posix()})
    patch_apply_manifest = build_patch_apply_assistant(
        analysis_dir,
        output=output,
        target_project_dir=target_project_dir,
    )
    steps.append({"step": "build_patch_apply", "status": "completed", "path": (analysis_dir / "llm-pack" / "patch-apply-assistant" / "manifest.json").as_posix()})

    handoff_manifest = build_developer_handoff_packs(analysis_dir, output=output)
    steps.append({"step": "build_handoff_packs", "status": "completed", "path": (analysis_dir / "delivery-handoff" / "manifest.json").as_posix()})

    progress_report = update_progress_report(analysis_dir, runtime_dir=runtime_dir, output=output)
    steps.append({"step": "update_progress", "status": "completed", "path": (runtime_dir / "progress" / "progress-report.json").as_posix()})

    replay_manifest = build_failure_replay_lab(analysis_dir=analysis_dir, runtime_dir=runtime_dir, output=output)
    steps.append({"step": "build_failure_replay", "status": "completed", "path": (runtime_dir / "failure-replay" / "manifest.json").as_posix()})

    patch_validation_report = None
    repo_validation_report = build_repo_validation_gate(
        analysis_dir,
        output=output,
        target_project_dir=target_project_dir,
    )
    steps.append({"step": "build_repo_validation", "status": "completed", "path": (analysis_dir / "llm-pack" / "repo-validation-gate" / "repo-validation.json").as_posix()})
    workspace_sync_report = None
    target_manifest = None
    if target_project_dir is not None:
        target_manifest = build_target_project_integration_pack(analysis_dir, target_project_dir)
        steps.append({"step": "build_target_pack", "status": "completed", "path": (analysis_dir / "llm-pack" / "target-integration" / "target-integration-manifest.json").as_posix()})
        workspace_sync_report = build_transition_workspace_sync(analysis_dir, target_project_dir, output=output)
        steps.append({"step": "sync_workspace", "status": "completed", "path": (analysis_dir / "llm-pack" / "workspace-sync" / "workspace-sync.json").as_posix()})
        patch_validation_report = validate_patch_packs(analysis_dir, output=output, target_project_dir=target_project_dir)
        steps.append({"step": "validate_patch_packs", "status": "completed", "path": (analysis_dir / "llm-pack" / "patch-validation" / "patch-validation.json").as_posix()})

    repair_manifest = build_repair_tasks(
        analysis_dir,
        runtime_dir=runtime_dir,
        runtime_error_summary=output.runtime_error_summary,
        patch_validation_report=patch_validation_report,
        repo_validation_report=repo_validation_report,
    )
    steps.append({"step": "build_repair_tasks", "status": "completed", "path": (runtime_dir / "repair-tasks" / "repair-tasks.json").as_posix()})

    delivery_manifest = deliver_slices(
        analysis_dir,
        target_project_dir=target_project_dir,
        allow_unvalidated=allow_unvalidated,
    )
    steps.append({"step": "deliver_slices", "status": "completed", "path": (analysis_dir / "delivery-slices" / "delivery-manifest.json").as_posix()})

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.resolve().as_posix() if target_project_dir else None,
        "step_count": len(steps),
        "steps": steps,
        "summary": {
            "patch_count": patch_manifest.get("patch_count", 0),
            "patch_apply_count": patch_apply_manifest.get("entry_count", 0),
            "handoff_count": handoff_manifest.get("entry_count", 0),
            "replay_count": replay_manifest.get("entry_count", 0),
            "repair_count": repair_manifest.get("entry_count", 0),
            "delivery_count": delivery_manifest.get("delivery_count", 0),
            "workspace_sync_count": workspace_sync_report.get("entry_count", 0) if workspace_sync_report else 0,
            "patch_validation_count": patch_validation_report.get("entry_count", 0) if patch_validation_report else 0,
            "repo_validation_count": repo_validation_report.get("entry_count", 0) if repo_validation_report else 0,
        },
    }
    write_json(control_dir / "controlled-delivery-manifest.json", payload)
    write_text(control_dir / "controlled-delivery-guide.md", _render_markdown(payload))
    return payload


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Controlled Delivery Pipeline",
        "",
        f"- Step count: {payload.get('step_count', 0)}",
        "",
        "## Summary",
        "",
    ]
    for key, value in sorted((payload.get("summary") or {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Steps", ""])
    for item in payload.get("steps", []):
        lines.append(f"- {item.get('step')}: {item.get('status')} -> `{item.get('path')}`")
    return "\n".join(lines).strip() + "\n"
