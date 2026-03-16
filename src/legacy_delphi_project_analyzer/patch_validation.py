from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def validate_patch_packs(
    analysis_dir: Path,
    *,
    output: Any,
    target_project_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    target_project_dir = target_project_dir.resolve() if target_project_dir else None
    validation_dir = (output_dir or analysis_dir / "llm-pack" / "patch-validation").resolve()
    ensure_directory(validation_dir)

    patch_manifest = build_code_patch_packs(analysis_dir=analysis_dir, output=output)
    entries: list[dict[str, Any]] = []
    for item in patch_manifest.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        expected_files = [str(value) for value in item.get("expected_files", []) if isinstance(value, str)]
        missing_source_artifacts = [
            path for path in item.get("source_artifacts", [])
            if isinstance(path, str) and not (analysis_dir / path).exists()
        ]
        target_existing = []
        if target_project_dir:
            target_existing = [path for path in expected_files if (target_project_dir / path).exists()]
        status, issues = _status_for_entry(expected_files, missing_source_artifacts, target_existing, target_project_dir is not None)
        entry = {
            "module_name": item.get("module_name"),
            "slice_name": item.get("slice_name"),
            "target_stack": item.get("target_stack"),
            "patch_kind": item.get("patch_kind"),
            "status": status,
            "issues": issues,
            "expected_file_count": len(expected_files),
            "existing_target_files": target_existing,
            "missing_source_artifacts": missing_source_artifacts,
            "summary_file": item.get("summary_file"),
            "prompt_file": item.get("prompt_file"),
        }
        entries.append(entry)

    counts_by_status: dict[str, int] = {}
    for item in entries:
        counts_by_status[item["status"]] = counts_by_status.get(item["status"], 0) + 1

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.as_posix() if target_project_dir else None,
        "entry_count": len(entries),
        "counts_by_status": counts_by_status,
        "entries": entries,
        "recommended_workflow": [
            "Fix missing source artifacts before sending the patch pack to qwen3.",
            "If target files already exist, ask for a merge-oriented patch instead of create-from-scratch output.",
            "Keep one page or endpoint per patch validation cycle.",
        ],
    }
    write_json(validation_dir / "patch-validation.json", payload)
    write_text(validation_dir / "patch-validation.md", _render_markdown(payload))
    return payload


def _status_for_entry(
    expected_files: list[str],
    missing_source_artifacts: list[str],
    target_existing: list[str],
    has_target: bool,
) -> tuple[str, list[str]]:
    issues: list[str] = []
    if missing_source_artifacts:
        issues.append("source_artifacts_missing")
    if not expected_files:
        issues.append("expected_files_empty")
    if has_target and target_existing and len(target_existing) < len(expected_files):
        issues.append("target_partial_merge")
    if issues:
        return ("fail" if "source_artifacts_missing" in issues else "warn", issues)
    return "pass", []


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Patch Validation",
        "",
        f"- Entry count: {payload.get('entry_count', 0)}",
        "",
        "## Counts By Status",
        "",
    ]
    for key, value in sorted((payload.get("counts_by_status") or {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Entries", ""])
    for item in payload.get("entries", [])[:20]:
        lines.extend(
            [
                f"### {item.get('module_name')} / {item.get('slice_name')}",
                f"- Status: {item.get('status')}",
                f"- Issues: {', '.join(item.get('issues', [])) or 'None'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
