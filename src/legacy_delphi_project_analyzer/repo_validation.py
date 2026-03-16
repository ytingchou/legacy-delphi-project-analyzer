from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.patch_apply import build_patch_apply_assistant
from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_repo_validation_gate(
    analysis_dir: Path,
    *,
    output: Any,
    target_project_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    target_project_dir = target_project_dir.resolve() if target_project_dir else None
    gate_dir = (output_dir or analysis_dir / "llm-pack" / "repo-validation-gate").resolve()
    ensure_directory(gate_dir)

    patch_manifest = build_code_patch_packs(analysis_dir=analysis_dir, output=output)
    apply_manifest = build_patch_apply_assistant(
        analysis_dir,
        output=output,
        target_project_dir=target_project_dir,
    )
    apply_entries = {
        (str(item.get("module_name") or ""), str(item.get("slice_name") or "")): item
        for item in apply_manifest.get("entries", [])
        if isinstance(item, dict)
    }
    target_summary = {}
    if target_project_dir:
        from legacy_delphi_project_analyzer.target_integration import inspect_target_project

        target_summary = inspect_target_project(target_project_dir)

    entries: list[dict[str, Any]] = []
    for artifact in patch_manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        module_name = str(artifact.get("module_name") or "")
        slice_name = str(artifact.get("slice_name") or "")
        apply_entry = apply_entries.get((module_name, slice_name), {})
        expected_files = [str(item) for item in artifact.get("expected_files", []) if isinstance(item, str)]
        existing_files = [
            rel_path for rel_path in expected_files
            if target_project_dir is not None and (target_project_dir / rel_path).exists()
        ]
        missing_files = [item for item in expected_files if item not in existing_files]
        missing_source_artifacts = [
            path for path in artifact.get("source_artifacts", [])
            if isinstance(path, str) and not (analysis_dir / path).exists()
        ]
        repo_checks = _repo_checks(
            artifact=artifact,
            existing_files=existing_files,
            target_project_dir=target_project_dir,
            target_summary=target_summary,
        )
        status, issues = _status_and_issues(
            artifact=artifact,
            missing_source_artifacts=missing_source_artifacts,
            missing_files=missing_files,
            repo_checks=repo_checks,
            has_target=target_project_dir is not None,
        )
        repair_prompt = _repair_prompt(artifact, issues)
        entry = {
            "module_name": module_name,
            "slice_name": slice_name,
            "target_stack": artifact.get("target_stack"),
            "patch_kind": artifact.get("patch_kind"),
            "status": status,
            "issues": issues,
            "expected_files": expected_files,
            "existing_files": existing_files,
            "missing_files": missing_files,
            "missing_source_artifacts": missing_source_artifacts,
            "repo_checks": repo_checks,
            "patch_apply_prompt_file": apply_entry.get("patch_apply_prompt_file"),
            "allowed_files_file": apply_entry.get("allowed_files_file"),
            "repair_prompt": repair_prompt,
            "next_command": f"legacy-delphi-analyzer build-repair-tasks {analysis_dir}",
        }
        entries.append(entry)

    counts_by_status: dict[str, int] = {}
    for item in entries:
        state = str(item.get("status") or "unknown")
        counts_by_status[state] = counts_by_status.get(state, 0) + 1

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.as_posix() if target_project_dir else None,
        "entry_count": len(entries),
        "counts_by_status": counts_by_status,
        "entries": entries,
        "recommended_workflow": [
            "Run one patch-apply slice only.",
            "If repo validation fails, repair that slice instead of broadening scope.",
            "Keep React work to one page and Spring work to one endpoint per cycle.",
        ],
    }
    write_json(gate_dir / "repo-validation.json", payload)
    write_text(gate_dir / "repo-validation.md", _render_markdown(payload))
    return payload


def _repo_checks(
    *,
    artifact: dict[str, Any],
    existing_files: list[str],
    target_project_dir: Path | None,
    target_summary: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "existing_file_count": len(existing_files),
        "expected_file_count": len([item for item in artifact.get("expected_files", []) if isinstance(item, str)]),
    }
    if target_project_dir is None:
        checks["target_project_present"] = False
        return checks

    checks["target_project_present"] = True
    target_stack = str(artifact.get("target_stack") or "")
    if target_stack == "react":
        feature_dirs = [str(item) for item in target_summary.get("feature_dirs", []) if isinstance(item, str)]
        route_files = [str(item) for item in target_summary.get("route_files", []) if isinstance(item, str)]
        api_files = [str(item) for item in target_summary.get("api_files", []) if isinstance(item, str)]
        first_expected = next((item for item in artifact.get("expected_files", []) if isinstance(item, str)), "")
        parent_dir = str(Path(first_expected).parent)
        checks["feature_dir_present"] = any(path.endswith(parent_dir) for path in feature_dirs)
        checks["route_files_present"] = bool(route_files)
        checks["api_files_present"] = bool(api_files)
    elif target_stack == "spring-boot":
        java_root = target_project_dir / "src" / "main" / "java"
        checks["java_root_present"] = java_root.exists()
        checks["package_dirs_present"] = any(
            (target_project_dir / Path(item).parent).exists()
            for item in artifact.get("expected_files", [])
            if isinstance(item, str)
        )
    return checks


def _status_and_issues(
    *,
    artifact: dict[str, Any],
    missing_source_artifacts: list[str],
    missing_files: list[str],
    repo_checks: dict[str, Any],
    has_target: bool,
) -> tuple[str, list[str]]:
    issues: list[str] = []
    if missing_source_artifacts:
        issues.append("source_artifacts_missing")

    target_stack = str(artifact.get("target_stack") or "")
    if has_target:
        if missing_files:
            issues.append("target_files_missing")
        if target_stack == "react":
            if not repo_checks.get("route_files_present", False):
                issues.append("route_files_missing")
            if not repo_checks.get("api_files_present", False):
                issues.append("api_files_missing")
        elif target_stack == "spring-boot":
            if not repo_checks.get("java_root_present", False):
                issues.append("java_root_missing")
            if not repo_checks.get("package_dirs_present", False):
                issues.append("package_dirs_missing")

    if "source_artifacts_missing" in issues:
        return "fail", issues
    if any(item.endswith("_missing") for item in issues):
        return "warn", issues
    return "pass", issues


def _repair_prompt(artifact: dict[str, Any], issues: list[str]) -> str:
    issue_text = ", ".join(issues) if issues else "no explicit issues"
    return (
        f"Repair only the bounded {artifact.get('target_stack')} slice `{artifact.get('slice_name')}`. "
        f"Fix these repo-validation issues only: {issue_text}. "
        "Do not redesign unrelated files."
    )


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Repo Validation Gate",
        "",
        f"- Entry count: {payload.get('entry_count', 0)}",
        "",
        "## Counts By Status",
        "",
    ]
    for key, value in sorted((payload.get("counts_by_status") or {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Recommended Workflow", ""])
    for item in payload.get("recommended_workflow", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Entries", ""])
    for item in payload.get("entries", [])[:20]:
        lines.extend(
            [
                f"### {item.get('module_name')} / {item.get('slice_name')}",
                f"- Status: {item.get('status')}",
                f"- Issues: {', '.join(item.get('issues', [])) or 'None'}",
                f"- Patch apply prompt: `{item.get('patch_apply_prompt_file') or 'n/a'}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
