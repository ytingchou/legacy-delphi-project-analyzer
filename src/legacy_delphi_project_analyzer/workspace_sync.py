from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack, inspect_target_project
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_transition_workspace_sync(
    analysis_dir: Path,
    target_project_dir: Path,
    *,
    output: Any,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    target_project_dir = target_project_dir.resolve()
    sync_dir = (output_dir or analysis_dir / "llm-pack" / "workspace-sync").resolve()
    ensure_directory(sync_dir)

    patch_manifest = build_code_patch_packs(analysis_dir=analysis_dir, output=output)
    integration_manifest = build_target_project_integration_pack(analysis_dir, target_project_dir)
    target_summary = inspect_target_project(target_project_dir)
    known_routes = set(target_summary.get("known_routes", []))

    entries: list[dict[str, Any]] = []
    for item in patch_manifest.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        expected_files = [str(value) for value in item.get("expected_files", []) if isinstance(value, str)]
        existing_files = [
            rel_path for rel_path in expected_files if (target_project_dir / rel_path).exists()
        ]
        missing_files = [rel_path for rel_path in expected_files if rel_path not in existing_files]
        entry = {
            "module_name": item.get("module_name"),
            "slice_name": item.get("slice_name"),
            "target_stack": item.get("target_stack"),
            "patch_kind": item.get("patch_kind"),
            "expected_files": expected_files,
            "existing_files": existing_files,
            "missing_files": missing_files,
            "integration_state": _integration_state(existing_files, expected_files),
            "next_best_action": _next_best_action(existing_files, missing_files, item.get("target_stack")),
            "prompt_file": item.get("prompt_file"),
            "summary_file": item.get("summary_file"),
        }
        entries.append(entry)

    route_entries: list[dict[str, Any]] = []
    for item in integration_manifest.get("entries", []):
        if not isinstance(item, dict):
            continue
        route_path = str(item.get("route_path") or "")
        route_entries.append(
            {
                "module_name": item.get("module_name"),
                "page_name": item.get("page_name"),
                "route_path": route_path,
                "route_exists": route_path in known_routes,
                "target_feature_dir": item.get("target_feature_dir"),
                "route_alignment_score": item.get("route_alignment_score", 0),
                "assistant_prompt": item.get("assistant_prompt"),
            }
        )

    counts_by_state: dict[str, int] = {}
    for item in entries:
        state = str(item.get("integration_state") or "unknown")
        counts_by_state[state] = counts_by_state.get(state, 0) + 1

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.as_posix(),
        "counts_by_state": counts_by_state,
        "entry_count": len(entries),
        "route_entry_count": len(route_entries),
        "entries": entries,
        "route_entries": route_entries,
        "recommended_workflow": [
            "Start with slices that already have some expected files in the target project.",
            "For missing slices, use the patch pack prompt before touching unrelated target files.",
            "Keep one page or endpoint per Cline session.",
        ],
    }
    write_json(sync_dir / "workspace-sync.json", payload)
    write_text(sync_dir / "workspace-sync.md", _render_markdown(payload))
    return payload


def _integration_state(existing_files: list[str], expected_files: list[str]) -> str:
    if not expected_files:
        return "unknown"
    if len(existing_files) == 0:
        return "missing"
    if len(existing_files) == len(expected_files):
        return "complete"
    return "partial"


def _next_best_action(existing_files: list[str], missing_files: list[str], target_stack: Any) -> str:
    if not existing_files:
        return f"Use the {target_stack or 'patch'} pack to create the bounded slice in the target workspace."
    if missing_files:
        return "Merge the remaining expected files only. Do not broaden the slice."
    return "Validate the existing target implementation against the current patch pack."


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Transition Workspace Sync",
        "",
        f"- Entry count: {payload.get('entry_count', 0)}",
        f"- Route entry count: {payload.get('route_entry_count', 0)}",
        "",
        "## Counts By State",
        "",
    ]
    for key, value in sorted((payload.get("counts_by_state") or {}).items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Recommended Workflow", ""])
    for item in payload.get("recommended_workflow", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Slice Entries", ""])
    for item in payload.get("entries", [])[:20]:
        lines.extend(
            [
                f"### {item.get('module_name')} / {item.get('slice_name')}",
                f"- State: {item.get('integration_state')}",
                f"- Existing files: {len(item.get('existing_files', []))}",
                f"- Missing files: {len(item.get('missing_files', []))}",
                f"- Next action: {item.get('next_best_action')}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
