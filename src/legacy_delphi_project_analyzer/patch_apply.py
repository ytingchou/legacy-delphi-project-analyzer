from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def build_patch_apply_assistant(
    analysis_dir: Path,
    *,
    output: Any,
    target_project_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    assistant_dir = (output_dir or analysis_dir / "llm-pack" / "patch-apply-assistant").resolve()
    ensure_directory(assistant_dir)

    patch_manifest = build_code_patch_packs(analysis_dir=analysis_dir, output=output)
    target_manifest = {"entries": []}
    if target_project_dir is not None:
        from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack

        target_manifest = build_target_project_integration_pack(analysis_dir, target_project_dir)
    target_entries = {
        (str(item.get("module_name") or ""), str(item.get("page_name") or "")): item
        for item in target_manifest.get("entries", [])
        if isinstance(item, dict)
    }

    entries: list[dict[str, Any]] = []
    for artifact in patch_manifest.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        module_name = str(artifact.get("module_name") or "module")
        slice_name = str(artifact.get("slice_name") or "slice")
        module_dir = assistant_dir / slugify(module_name)
        slice_dir = module_dir / slugify(slice_name)
        ensure_directory(slice_dir)

        target_entry = target_entries.get((module_name, slice_name), {})
        expected_files = [str(item) for item in artifact.get("expected_files", []) if isinstance(item, str)]
        files_to_update = [str(item) for item in target_entry.get("files_to_update", []) if isinstance(item, str)]
        allowed_files = _ordered_unique(expected_files + files_to_update)
        blocked_patterns = _blocked_patterns(artifact.get("target_stack"), allowed_files)
        apply_order = _apply_order(artifact, target_entry)
        post_change = _post_change_checklist(artifact, target_entry)
        prompt_text = _render_apply_prompt(artifact, allowed_files, blocked_patterns, apply_order, post_change)

        allowed_files_path = slice_dir / "allowed-files.json"
        blocked_files_path = slice_dir / "blocked-files.json"
        apply_order_path = slice_dir / "apply-order.md"
        checklist_path = slice_dir / "post-change-checklist.md"
        prompt_path = slice_dir / "patch-apply-prompt.md"

        write_json(allowed_files_path, {"allowed_files": allowed_files})
        write_json(blocked_files_path, {"blocked_patterns": blocked_patterns})
        write_text(apply_order_path, _render_markdown_list("Apply Order", apply_order))
        write_text(checklist_path, _render_markdown_list("Post-Change Checklist", post_change))
        write_text(prompt_path, prompt_text)

        entry = {
            "module_name": module_name,
            "slice_name": slice_name,
            "target_stack": artifact.get("target_stack"),
            "patch_kind": artifact.get("patch_kind"),
            "target_project_dir": target_project_dir.resolve().as_posix() if target_project_dir else None,
            "allowed_files": allowed_files,
            "blocked_patterns": blocked_patterns,
            "apply_order": apply_order,
            "post_change_checklist": post_change,
            "allowed_files_file": allowed_files_path.as_posix(),
            "blocked_files_file": blocked_files_path.as_posix(),
            "apply_order_file": apply_order_path.as_posix(),
            "post_change_checklist_file": checklist_path.as_posix(),
            "patch_apply_prompt_file": prompt_path.as_posix(),
            "source_artifacts": list(artifact.get("source_artifacts", [])),
            "summary_file": artifact.get("summary_file"),
            "prompt_file": artifact.get("prompt_file"),
            "notes": _ordered_unique(
                [str(item) for item in artifact.get("notes", []) if isinstance(item, str)]
                + [str(item) for item in target_entry.get("merge_risks", []) if isinstance(item, str)]
            ),
        }
        write_json(slice_dir / "manifest.json", entry)
        entries.append(entry)

    payload = {
        "analysis_dir": analysis_dir.as_posix(),
        "target_project_dir": target_project_dir.resolve().as_posix() if target_project_dir else None,
        "entry_count": len(entries),
        "counts_by_stack": {
            "react": len([item for item in entries if item.get("target_stack") == "react"]),
            "spring-boot": len([item for item in entries if item.get("target_stack") == "spring-boot"]),
        },
        "entries": entries,
        "recommended_workflow": [
            "Open one patch-apply entry only.",
            "Modify only the listed allowed files.",
            "Do not broaden the slice beyond one page or endpoint.",
            "Run repo validation after the bounded patch is applied.",
        ],
    }
    write_json(assistant_dir / "manifest.json", payload)
    write_text(assistant_dir / "README.md", _render_manifest_markdown(payload))
    return payload


def _blocked_patterns(target_stack: Any, allowed_files: list[str]) -> list[str]:
    roots = sorted({Path(item).parts[0] for item in allowed_files if item})
    if target_stack == "react":
        return [f"Do not modify files outside {', '.join(roots or ['src'])}.", "Do not redesign unrelated routes or features."]
    if target_stack == "spring-boot":
        return [f"Do not modify files outside {', '.join(roots or ['src'])}.", "Do not redesign unrelated controllers, services, or repositories."]
    return ["Do not modify files outside the allowed list."]


def _apply_order(artifact: dict[str, Any], target_entry: dict[str, Any]) -> list[str]:
    steps = ["Read the patch pack summary and bounded prompt first."]
    if target_entry:
        if target_entry.get("files_to_update"):
            steps.append("Update shared route or API client files before creating new slice-local files.")
        if target_entry.get("files_to_create"):
            steps.append("Create only the listed new slice files after shared integration points are updated.")
    else:
        steps.append("Create or update only the expected files listed in the patch pack.")
    if artifact.get("target_stack") == "react":
        steps.append("Keep changes scoped to one page, one API adapter, and one types file.")
    elif artifact.get("target_stack") == "spring-boot":
        steps.append("Keep changes scoped to one controller/service/repository slice plus required DTOs only.")
    steps.append("Run repo validation and patch validation before moving to the next slice.")
    return steps


def _post_change_checklist(artifact: dict[str, Any], target_entry: dict[str, Any]) -> list[str]:
    checks = [str(item) for item in artifact.get("validation_checks", []) if isinstance(item, str)]
    if target_entry:
        checks.extend(str(item) for item in target_entry.get("merge_checklist", []) if isinstance(item, str))
    checks.append("Validate imports, file placement, and route/endpoint alignment in the target repo.")
    return _ordered_unique(checks)


def _render_apply_prompt(
    artifact: dict[str, Any],
    allowed_files: list[str],
    blocked_patterns: list[str],
    apply_order: list[str],
    post_change: list[str],
) -> str:
    allowed = "\n".join(f"- {item}" for item in allowed_files) or "- None"
    blocked = "\n".join(f"- {item}" for item in blocked_patterns) or "- None"
    order = "\n".join(f"- {item}" for item in apply_order) or "- None"
    checks = "\n".join(f"- {item}" for item in post_change) or "- None"
    return f"""# Patch Apply Assistant

Module: {artifact.get('module_name')}
Slice: {artifact.get('slice_name')}
Target stack: {artifact.get('target_stack')}
Patch kind: {artifact.get('patch_kind')}

## Allowed Files

{allowed}

## Do Not Do

{blocked}

## Apply Order

{order}

## Post-Change Checklist

{checks}

## Bounded Prompt

Apply only the bounded slice `{artifact.get('slice_name')}` for module `{artifact.get('module_name')}`.
Modify only the allowed files. Do not redesign unrelated code. After the patch, run repo validation.
"""


def _render_markdown_list(title: str, items: list[str]) -> str:
    body = "\n".join(f"- {item}" for item in items) or "- None"
    return f"# {title}\n\n{body}\n"


def _render_manifest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Patch Apply Assistant",
        "",
        f"- Entry count: {payload.get('entry_count', 0)}",
        f"- React slices: {payload.get('counts_by_stack', {}).get('react', 0)}",
        f"- Spring Boot slices: {payload.get('counts_by_stack', {}).get('spring-boot', 0)}",
        "",
        "## Recommended Workflow",
        "",
    ]
    for item in payload.get("recommended_workflow", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Entries", ""])
    for item in payload.get("entries", [])[:20]:
        lines.extend(
            [
                f"### {item.get('module_name')} / {item.get('slice_name')}",
                f"- Stack: {item.get('target_stack')}",
                f"- Allowed files: {len(item.get('allowed_files', []))}",
                f"- Prompt: `{item.get('patch_apply_prompt_file')}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
