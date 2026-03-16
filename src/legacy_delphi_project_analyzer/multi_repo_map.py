from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def build_multi_repo_transition_map(
    analysis_dir: Path,
    *,
    output: Any,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    map_dir = (output_dir or analysis_dir / "llm-pack" / "multi-repo-transition-map").resolve()
    ensure_directory(map_dir)

    root_usage: dict[str, dict[str, Any]] = {}
    for root in output.inventory.scan_roots + output.inventory.external_roots:
        root_usage[root] = {"root": root, "units": 0, "forms": 0, "queries": 0}

    for unit in output.pascal_units:
        root = _best_root_for_path(unit.file_path, root_usage)
        if root:
            root_usage[root]["units"] += 1
    for form in output.forms:
        root = _best_root_for_path(form.file_path, root_usage)
        if root:
            root_usage[root]["forms"] += 1
    for query in output.resolved_queries:
        root = _best_root_for_path(query.file_path, root_usage)
        if root:
            root_usage[root]["queries"] += 1

    shared_query_names: dict[str, list[str]] = defaultdict(list)
    for query in output.resolved_queries:
        shared_query_names[query.name].append(query.file_path)
    reusable_query_families = [
        {"query_name": name, "file_paths": paths}
        for name, paths in shared_query_names.items()
        if len(paths) > 1
    ]

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "root_count": len(root_usage),
        "roots": list(root_usage.values()),
        "reusable_query_families": reusable_query_families,
        "recommended_reuse_notes": [
            "Prefer shared SQL XML families from common roots before duplicating them in target projects.",
            "Track external roots separately when planning reusable transition tasks.",
        ],
    }
    write_json(map_dir / "multi-repo-transition-map.json", payload)
    write_text(map_dir / "multi-repo-transition-map.md", _render_markdown(payload))
    return payload


def _best_root_for_path(file_path: str, root_usage: dict[str, dict[str, Any]]) -> str | None:
    matching = [root for root in root_usage if file_path.startswith(root)]
    if not matching:
        return None
    return max(matching, key=len)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Multi-Repo Transition Map",
        "",
        f"- Root count: {payload.get('root_count', 0)}",
        f"- Reusable query families: {len(payload.get('reusable_query_families', []))}",
        "",
        "## Roots",
        "",
    ]
    for item in payload.get("roots", []):
        lines.extend(
            [
                f"### {item.get('root')}",
                f"- Units: {item.get('units')}",
                f"- Forms: {item.get('forms')}",
                f"- Queries: {item.get('queries')}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
