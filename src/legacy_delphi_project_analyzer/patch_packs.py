from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import AnalysisOutput, CodePatchPackArtifact, to_jsonable
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def build_code_patch_packs(
    *,
    analysis_dir: Path,
    output: AnalysisOutput,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    pack_dir = (output_dir or analysis_dir / "llm-pack" / "code-patch-packs").resolve()
    ensure_directory(pack_dir)
    artifacts: list[CodePatchPackArtifact] = []

    for spec in output.transition_specs:
        module_dir = pack_dir / slugify(spec.module_name)
        ensure_directory(module_dir)
        for page in spec.frontend_pages:
            slice_name = f"{spec.module_name}:{page.name}"
            prompt_path = module_dir / f"{slugify(page.name)}-react-patch-prompt.md"
            manifest_path = module_dir / f"{slugify(page.name)}-react-patch-pack.json"
            summary_path = module_dir / f"{slugify(page.name)}-react-patch-pack.md"
            payload = _react_patch_payload(spec, page)
            write_text(prompt_path, _render_patch_prompt(payload))
            write_json(manifest_path, payload)
            write_text(summary_path, _render_patch_summary(payload))
            artifacts.append(
                CodePatchPackArtifact(
                    module_name=spec.module_name,
                    slice_name=slice_name,
                    target_stack="react",
                    patch_kind="page",
                    prompt_file=prompt_path.as_posix(),
                    summary_file=summary_path.as_posix(),
                    manifest_file=manifest_path.as_posix(),
                    expected_files=payload["expected_files"],
                    source_artifacts=payload["source_artifacts"],
                    validation_checks=payload["validation_checks"],
                    notes=payload["notes"],
                )
            )
        for endpoint in spec.backend_endpoints:
            slice_name = f"{spec.module_name}:{endpoint.name}"
            prompt_path = module_dir / f"{slugify(endpoint.name)}-spring-patch-prompt.md"
            manifest_path = module_dir / f"{slugify(endpoint.name)}-spring-patch-pack.json"
            summary_path = module_dir / f"{slugify(endpoint.name)}-spring-patch-pack.md"
            payload = _spring_patch_payload(spec, endpoint)
            write_text(prompt_path, _render_patch_prompt(payload))
            write_json(manifest_path, payload)
            write_text(summary_path, _render_patch_summary(payload))
            artifacts.append(
                CodePatchPackArtifact(
                    module_name=spec.module_name,
                    slice_name=slice_name,
                    target_stack="spring-boot",
                    patch_kind="endpoint",
                    prompt_file=prompt_path.as_posix(),
                    summary_file=summary_path.as_posix(),
                    manifest_file=manifest_path.as_posix(),
                    expected_files=payload["expected_files"],
                    source_artifacts=payload["source_artifacts"],
                    validation_checks=payload["validation_checks"],
                    notes=payload["notes"],
                )
            )

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "patch_count": len(artifacts),
        "by_stack": {
            "react": len([item for item in artifacts if item.target_stack == "react"]),
            "spring-boot": len([item for item in artifacts if item.target_stack == "spring-boot"]),
        },
        "artifacts": [to_jsonable(item) for item in artifacts],
    }
    write_json(pack_dir / "manifest.json", manifest)
    write_text(pack_dir / "README.md", _render_manifest_summary(manifest))
    output.code_patch_packs = artifacts
    return manifest


def _react_patch_payload(spec: Any, page: Any) -> dict[str, Any]:
    module_slug = slugify(spec.module_name)
    return {
        "module_name": spec.module_name,
        "slice_name": page.name,
        "target_stack": "react",
        "patch_kind": "page",
        "purpose": page.purpose,
        "expected_files": [
            f"src/features/{module_slug}/{page.name}.tsx",
            f"src/features/{module_slug}/{page.name}.types.ts",
            f"src/features/{module_slug}/{page.name}.api.ts",
        ],
        "source_artifacts": [
            f"llm-pack/ui-pseudo/{slugify(spec.module_name)}-{slugify(page.name)}-pseudo-ui.md",
            f"llm-pack/ui-reference/{slugify(spec.module_name)}-{slugify(page.name)}-reference-ui.md",
        ],
        "validation_checks": [
            "Stay within one page only.",
            "Use only the listed APIs and fields.",
            "Do not redesign unrelated routes or modules.",
        ],
        "notes": [
            f"route={page.route_path}",
            f"actions={len(page.actions)}",
            f"inputs={len(page.inputs)}",
        ],
        "bounded_prompt": (
            f"Implement only the React page `{page.name}` for module `{spec.module_name}`. "
            "Use the pseudo/reference artifacts and return patch-oriented guidance only."
        ),
    }


def _spring_patch_payload(spec: Any, endpoint: Any) -> dict[str, Any]:
    module_slug = slugify(spec.module_name).replace("-", "")
    return {
        "module_name": spec.module_name,
        "slice_name": endpoint.name,
        "target_stack": "spring-boot",
        "patch_kind": "endpoint",
        "purpose": endpoint.purpose,
        "expected_files": [
            f"src/main/java/com/example/{module_slug}/{spec.module_name}Controller.java",
            f"src/main/java/com/example/{module_slug}/{spec.module_name}Service.java",
            f"src/main/java/com/example/{module_slug}/{spec.module_name}Repository.java",
        ],
        "source_artifacts": [
            f"llm-pack/bff-sql/{slugify(spec.module_name)}-{slugify(endpoint.name)}-bff-sql.md",
            "llm-pack/backend-sql-guide.md",
        ],
        "validation_checks": [
            "Stay within one endpoint only.",
            "Use only the listed query artifacts and DTOs.",
            "Preserve Oracle 19c placeholder handling guidance.",
        ],
        "notes": [
            f"route={endpoint.method} {endpoint.path}",
            f"request_dto={endpoint.request_dto or 'none'}",
            f"response_dto={endpoint.response_dto or 'none'}",
        ],
        "bounded_prompt": (
            f"Implement only the Spring Boot endpoint `{endpoint.name}` for module `{spec.module_name}`. "
            "Use the BFF SQL artifact and return patch-oriented guidance only."
        ),
    }


def _render_patch_prompt(payload: dict[str, Any]) -> str:
    checks = "\n".join(f"- {item}" for item in payload["validation_checks"])
    expected = "\n".join(f"- {item}" for item in payload["expected_files"])
    sources = "\n".join(f"- {item}" for item in payload["source_artifacts"])
    return f"""# Code Patch Pack Prompt

Target stack: {payload['target_stack']}
Patch kind: {payload['patch_kind']}
Module: {payload['module_name']}
Slice: {payload['slice_name']}

## Purpose

{payload['purpose']}

## Expected Files

{expected}

## Source Artifacts

{sources}

## Validation Checks

{checks}

## Bounded Prompt

{payload['bounded_prompt']}
"""


def _render_patch_summary(payload: dict[str, Any]) -> str:
    return _render_patch_prompt(payload)


def _render_manifest_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# Code Patch Packs",
        "",
        f"- Patch count: {manifest.get('patch_count', 0)}",
        f"- React patch packs: {manifest.get('by_stack', {}).get('react', 0)}",
        f"- Spring Boot patch packs: {manifest.get('by_stack', {}).get('spring-boot', 0)}",
        "",
    ]
    for item in manifest.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"## {item.get('module_name')} / {item.get('slice_name')}",
                f"- Stack: {item.get('target_stack')}",
                f"- Summary: `{item.get('summary_file')}`",
                f"- Prompt: `{item.get('prompt_file')}`",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
