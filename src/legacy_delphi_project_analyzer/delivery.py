from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.codegen import generate_transition_code
from legacy_delphi_project_analyzer.oracle_bff import compile_oracle_bff_sql
from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def deliver_slices(
    analysis_dir: Path,
    *,
    module_names: list[str] | None = None,
    target_project_dir: Path | None = None,
    output_dir: Path | None = None,
    allow_unvalidated: bool = False,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    delivery_dir = (output_dir or analysis_dir / "delivery-slices").resolve()
    ensure_directory(delivery_dir)

    compiler_manifest = compile_oracle_bff_sql(analysis_dir)
    target_manifest = (
        build_target_project_integration_pack(analysis_dir, target_project_dir)
        if target_project_dir is not None
        else {"entries": []}
    )
    generated = generate_transition_code(analysis_dir, require_validated=not allow_unvalidated)
    codegen_manifest = _load_json(analysis_dir / "codegen" / "manifest.json", default=[])
    validation_results = _load_json(analysis_dir / "runtime" / "validation-results.json", default=[])
    boss_summary = _safe_read(analysis_dir / "llm-pack" / "boss-summary.md")

    requested = {name for name in module_names or []}
    entries: list[dict[str, Any]] = []
    for spec in output.transition_specs:
        if requested and spec.module_name not in requested:
            continue
        module_slug = slugify(spec.module_name)
        module_dir = delivery_dir / module_slug
        ensure_directory(module_dir)
        bff_entries = [item for item in compiler_manifest.get("entries", []) if item.get("module_name") == spec.module_name]
        target_entries = [item for item in target_manifest.get("entries", []) if item.get("module_name") == spec.module_name]
        code_entries = [item for item in codegen_manifest if isinstance(item, dict) and item.get("module_name") == spec.module_name]
        validation_states = [
            item for item in validation_results if isinstance(item, dict) and item.get("module_name") == spec.module_name
        ]
        remaining_gaps = list(spec.open_questions) + list(spec.risks)
        entry = {
            "module_name": spec.module_name,
            "readiness_score": spec.readiness_score,
            "readiness_level": spec.readiness_level,
            "recommended_first_slice": spec.recommended_first_slice,
            "validation_results": validation_states,
            "bff_entries": bff_entries,
            "target_integration_entries": target_entries,
            "generated_code": code_entries,
            "remaining_gaps": remaining_gaps,
            "boss_summary_excerpt": _boss_summary_excerpt(boss_summary, spec.module_name),
        }
        write_json(module_dir / "slice-manifest.json", entry)
        write_text(module_dir / "slice-summary.md", _render_slice_summary(entry))
        entries.append(entry)

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.resolve().as_posix() if target_project_dir else None,
        "entries": entries,
        "delivery_count": len(entries),
    }
    write_json(delivery_dir / "delivery-manifest.json", manifest)
    write_text(delivery_dir / "delivery-guide.md", _render_delivery_guide(manifest))
    return manifest


def _load_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _boss_summary_excerpt(boss_summary: str, module_name: str) -> str:
    if not boss_summary:
        return ""
    matching_lines = [line for line in boss_summary.splitlines() if module_name.lower() in line.lower()]
    if matching_lines:
        return "\n".join(matching_lines[:6])
    return "\n".join(boss_summary.splitlines()[:8])


def _render_slice_summary(entry: dict[str, Any]) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {item}" for item in values) if values else "- None"

    return f"""# Slice Delivery: {entry['module_name']}

- Readiness: {entry['readiness_score']} ({entry['readiness_level']})
- Recommended first slice: {entry['recommended_first_slice']}
- Validation results: {len(entry['validation_results'])}
- BFF entries: {len(entry['bff_entries'])}
- Target integration entries: {len(entry['target_integration_entries'])}
- Generated code files: {len(entry['generated_code'])}

## Remaining Gaps

{bullets(entry['remaining_gaps'])}

## Boss Summary Excerpt

```text
{entry['boss_summary_excerpt']}
```
"""


def _render_delivery_guide(manifest: dict[str, Any]) -> str:
    lines = [
        "# Slice Delivery Guide",
        "",
        f"- Delivery count: {manifest['delivery_count']}",
        f"- Target project: `{manifest.get('target_project_dir') or 'None'}`",
        "",
    ]
    for entry in manifest.get("entries", []):
        lines.extend(
            [
                f"## {entry['module_name']}",
                f"- Readiness: {entry['readiness_score']} ({entry['readiness_level']})",
                f"- First slice: {entry['recommended_first_slice']}",
                f"- Generated code files: {len(entry['generated_code'])}",
                f"- Remaining gaps: {len(entry['remaining_gaps'])}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
