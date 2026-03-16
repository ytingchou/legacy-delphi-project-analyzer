from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def build_target_project_integration_pack(
    analysis_dir: Path,
    target_project_dir: Path,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    target_project_dir = target_project_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    target_summary = inspect_target_project(target_project_dir)
    pack_dir = (output_dir or analysis_dir / "llm-pack" / "target-integration").resolve()
    ensure_directory(pack_dir)

    entries: list[dict[str, Any]] = []
    for artifact in output.ui_integration_artifacts:
        entry = _build_entry(artifact, target_summary)
        base_name = f"{slugify(artifact.module_name)}-{slugify(artifact.page_name)}-target-integration"
        write_json(pack_dir / f"{base_name}.json", entry)
        write_text(pack_dir / f"{base_name}.md", _render_entry_markdown(entry))
        entries.append(entry)

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.as_posix(),
        "target_summary": target_summary,
        "entries": entries,
    }
    write_json(pack_dir / "target-project-summary.json", target_summary)
    write_json(pack_dir / "target-integration-manifest.json", manifest)
    write_text(pack_dir / "target-integration-guide.md", _render_manifest_markdown(manifest))
    return manifest


def inspect_target_project(target_project_dir: Path) -> dict[str, Any]:
    files = [path for path in target_project_dir.rglob("*") if path.is_file()]
    feature_dirs = sorted(
        path.as_posix()
        for path in (target_project_dir / "src" / "features").glob("*")
        if path.is_dir()
    ) if (target_project_dir / "src" / "features").exists() else []
    route_files = [path.as_posix() for path in files if re.search(r"(routes?|router|app)\.(t|j)sx?$", path.name, re.I)]
    api_files = [path.as_posix() for path in files if re.search(r"api", path.as_posix(), re.I)]
    state_files = [path.as_posix() for path in files if re.search(r"(store|state)", path.as_posix(), re.I)]
    known_routes = sorted(
        {
            match.group(1)
            for path in files
            for match in re.finditer(r'path\s*[:=]\s*[\'"]([^\'"]+)[\'"]', _safe_read(path))
        }
        | {
            match.group(1)
            for path in files
            for match in re.finditer(r'<Route[^>]+path=[\'"]([^\'"]+)[\'"]', _safe_read(path))
        }
    )
    return {
        "target_project_dir": target_project_dir.as_posix(),
        "file_count": len(files),
        "feature_dirs": feature_dirs,
        "route_files": route_files,
        "api_files": api_files,
        "state_files": state_files,
        "known_routes": known_routes,
    }


def _build_entry(artifact: Any, target_summary: dict[str, Any]) -> dict[str, Any]:
    feature_dirs = [str(item) for item in target_summary.get("feature_dirs", []) if isinstance(item, str)]
    route_files = [str(item) for item in target_summary.get("route_files", []) if isinstance(item, str)]
    api_files = [str(item) for item in target_summary.get("api_files", []) if isinstance(item, str)]
    known_routes = {str(item) for item in target_summary.get("known_routes", []) if isinstance(item, str)}
    normalized_feature_dir = _normalize_path_key(artifact.target_feature_dir)
    feature_exists = any(_normalize_path_key(path).endswith(normalized_feature_dir) for path in feature_dirs)
    route_exists = artifact.route_path in known_routes
    route_registration = "update_existing_route" if route_exists else "register_new_route"
    files_to_create = [
        f"{artifact.target_feature_dir}/{artifact.page_name}.tsx",
        *[path for path in artifact.suggested_files if path not in feature_dirs],
    ]
    files_to_update = []
    if route_files:
        files_to_update.append(route_files[0])
    if api_files:
        files_to_update.append(api_files[0])
    merge_risks = []
    if feature_exists:
        merge_risks.append("Target feature directory already exists; merge with local conventions instead of overwriting.")
    if route_exists:
        merge_risks.append("Route already exists in target project; verify whether the legacy transition should extend it or replace it.")
    if not api_files:
        merge_risks.append("No API client file was detected; generate a new API adapter for this page.")
    return {
        "module_name": artifact.module_name,
        "page_name": artifact.page_name,
        "route_path": artifact.route_path,
        "target_feature_dir": artifact.target_feature_dir,
        "feature_exists": feature_exists,
        "route_exists": route_exists,
        "route_registration": route_registration,
        "route_file_candidates": route_files[:3],
        "api_client_candidates": api_files[:3],
        "state_file_candidates": [str(item) for item in target_summary.get("state_files", [])[:3]],
        "files_to_create": files_to_create,
        "files_to_update": files_to_update,
        "integration_steps": list(artifact.integration_steps),
        "acceptance_checks": list(artifact.acceptance_checks),
        "handoff_artifacts": list(artifact.handoff_artifacts),
        "merge_risks": merge_risks,
    }


def _render_entry_markdown(entry: dict[str, Any]) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {item}" for item in values) if values else "- None"

    return f"""# Target Integration: {entry['module_name']} / {entry['page_name']}

- Route path: `{entry['route_path']}`
- Target feature dir: `{entry['target_feature_dir']}`
- Route registration: {entry['route_registration']}
- Feature exists: {str(entry['feature_exists']).lower()}
- Route exists: {str(entry['route_exists']).lower()}

## Files To Create

{bullets(entry['files_to_create'])}

## Files To Update

{bullets(entry['files_to_update'])}

## Route File Candidates

{bullets(entry['route_file_candidates'])}

## API Client Candidates

{bullets(entry['api_client_candidates'])}

## Merge Risks

{bullets(entry['merge_risks'])}

## Integration Steps

{bullets(entry['integration_steps'])}
"""


def _render_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Target Project Integration Guide",
        "",
        f"- Target project: `{manifest['target_project_dir']}`",
        f"- Integration entries: {len(manifest.get('entries', []))}",
        "",
        "## Existing Target Features",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest.get("target_summary", {}).get("feature_dirs", []))
    lines.extend(["", "## Planned Integrations", ""])
    for entry in manifest.get("entries", []):
        lines.extend(
            [
                f"### {entry['module_name']} / {entry['page_name']}",
                f"- Route: `{entry['route_path']}`",
                f"- Feature dir: `{entry['target_feature_dir']}`",
                f"- Route registration: {entry['route_registration']}",
                f"- Files to update: {', '.join(entry['files_to_update']) or 'None'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _normalize_path_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
