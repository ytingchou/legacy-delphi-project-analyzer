from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def build_developer_handoff_packs(
    analysis_dir: Path,
    *,
    output: Any,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    handoff_dir = (output_dir or analysis_dir / "delivery-handoff").resolve()
    ensure_directory(handoff_dir)
    entries: list[dict[str, Any]] = []

    for spec in output.transition_specs:
        module_dir = handoff_dir / slugify(spec.module_name)
        ensure_directory(module_dir)
        entry = {
            "module_name": spec.module_name,
            "implementation_brief": (module_dir / "implementation-brief.md").as_posix(),
            "patch_checklist": (module_dir / "patch-checklist.md").as_posix(),
            "known_gaps": (module_dir / "known-gaps.md").as_posix(),
            "source_artifacts": [
                f"llm-pack/transition-specs/{slugify(spec.module_name)}-transition-spec.md",
                f"llm-pack/bff-sql/",
                f"llm-pack/ui-pseudo/",
            ],
            "first_slice": spec.recommended_first_slice,
            "readiness_level": spec.readiness_level,
        }
        write_text(module_dir / "implementation-brief.md", _implementation_brief(spec))
        write_text(module_dir / "patch-checklist.md", _patch_checklist(spec))
        write_text(module_dir / "known-gaps.md", _known_gaps(spec))
        write_json(module_dir / "manifest.json", entry)
        entries.append(entry)

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "entry_count": len(entries),
        "entries": entries,
    }
    write_json(handoff_dir / "manifest.json", manifest)
    write_text(handoff_dir / "README.md", _render_summary(manifest))
    return manifest


def _implementation_brief(spec: Any) -> str:
    return f"""# Implementation Brief: {spec.module_name}

- Readiness: {spec.readiness_level} ({spec.readiness_score})
- First slice: {spec.recommended_first_slice}
- Strategy: {spec.migration_strategy}

## Frontend Pages

{chr(10).join(f"- {page.name}: {page.route_path}" for page in spec.frontend_pages) or '- None'}

## Backend Endpoints

{chr(10).join(f"- {endpoint.name}: {endpoint.method} {endpoint.path}" for endpoint in spec.backend_endpoints) or '- None'}
"""


def _patch_checklist(spec: Any) -> str:
    lines = [
        f"# Patch Checklist: {spec.module_name}",
        "",
        "- Keep the first slice bounded.",
        "- Validate each task response before moving to the next one.",
        "- Use code patch packs instead of open-ended prompts.",
        "",
        "## Open Questions",
        "",
    ]
    lines.extend(f"- {item}" for item in spec.open_questions or ["None"])
    return "\n".join(lines).strip() + "\n"


def _known_gaps(spec: Any) -> str:
    lines = [f"# Known Gaps: {spec.module_name}", "", "## Risks", ""]
    lines.extend(f"- {item}" for item in spec.risks or ["None"])
    lines.extend(["", "## Assumptions", ""])
    lines.extend(f"- {item}" for item in spec.key_assumptions or ["None"])
    return "\n".join(lines).strip() + "\n"


def _render_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# Developer Handoff Packs",
        "",
        f"- Entry count: {manifest.get('entry_count', 0)}",
        "",
    ]
    for item in manifest.get("entries", []):
        lines.extend(
            [
                f"## {item.get('module_name')}",
                f"- First slice: {item.get('first_slice')}",
                f"- Brief: `{item.get('implementation_brief')}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
