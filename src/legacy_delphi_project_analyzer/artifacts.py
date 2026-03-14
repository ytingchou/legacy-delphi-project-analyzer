from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ArtifactManifestEntry,
    BusinessModuleArtifact,
    DiagnosticRecord,
    FormSummary,
    PascalUnitSummary,
    ResolvedQueryArtifact,
    TransitionMappingArtifact,
)
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, split_text_chunks, write_json, write_text


def build_transition_mapping(
    pascal_units: list[PascalUnitSummary],
    forms: list[FormSummary],
    resolved_queries: list[ResolvedQueryArtifact],
    diagnostics: list[DiagnosticRecord],
    module_name_resolver,
) -> TransitionMappingArtifact:
    unit_by_name = {item.unit_name.lower(): item for item in pascal_units}
    queries_by_xml = defaultdict(list)
    for query in resolved_queries:
        queries_by_xml[Path(query.file_path).name.lower()].append(query)
        queries_by_xml[Path(query.file_path).stem.lower()].append(query)

    modules: list[BusinessModuleArtifact] = []
    attached_units: set[str] = set()
    for form in forms:
        linked_unit = _resolve_form_unit(form, unit_by_name)
        base_name = _derive_module_name(form.root_name or linked_unit or Path(form.file_path).stem)
        module_name = module_name_resolver(base_name)
        unit_summary = unit_by_name.get(linked_unit.lower()) if linked_unit else None
        form_queries = _queries_for_pascal_unit(unit_summary, queries_by_xml)
        risks = []
        notes = []
        if form.is_binary:
            risks.append("Binary DFM was parsed heuristically; UI structure may be incomplete.")
        if form.parse_mode != "text":
            notes.extend(form.parse_notes)
        if linked_unit and linked_unit.lower() in unit_by_name:
            attached_units.add(linked_unit.lower())
        if not linked_unit:
            risks.append("Could not link DFM to a Pascal unit with high confidence.")
        if any(query.unresolved_placeholders for query in form_queries):
            risks.append("Some attached SQL artifacts still rely on Delphi-side placeholder replacement.")
        if form.datasets:
            notes.append(f"Datasets seen in DFM: {', '.join(form.datasets)}")
        modules.append(
            BusinessModuleArtifact(
                name=module_name,
                confidence="high" if linked_unit else "medium",
                source_units=[linked_unit] if linked_unit else [],
                forms=[form.root_name] if form.root_name else [],
                query_artifacts=sorted({query.name for query in form_queries}),
                react_candidates=_build_react_candidates(module_name, form),
                spring_candidates=_build_spring_candidates(module_name, form_queries),
                risks=risks,
                open_questions=_module_questions(module_name, diagnostics),
                notes=notes,
            )
        )

    for unit in pascal_units:
        if unit.unit_name.lower() in attached_units:
            continue
        module_name = module_name_resolver(_derive_module_name(unit.unit_name))
        unit_queries = _queries_for_pascal_unit(unit, queries_by_xml)
        risks = []
        if unit.sql_hints and not unit_queries:
            risks.append("Unit appears to contain inline SQL that is not backed by SQL XML artifacts.")
        if unit.replace_tokens:
            risks.append("StringReplace tokens suggest runtime SQL mutation that remains heuristic.")
        modules.append(
            BusinessModuleArtifact(
                name=module_name,
                confidence="medium" if unit.sql_hints or unit.xml_references else "low",
                source_units=[unit.unit_name],
                forms=[],
                query_artifacts=sorted({query.name for query in unit_queries}),
                react_candidates=[f"{module_name}Page"],
                spring_candidates=_build_spring_candidates(module_name, unit_queries),
                risks=risks,
                open_questions=_module_questions(module_name, diagnostics),
                notes=_unit_notes(unit),
            )
        )

    shared_services = sorted(
        f"{_derive_module_name(unit.unit_name)}SharedService"
        for unit in pascal_units
        if any(dep.lower().startswith("ucommon") for dep in unit.interface_uses + unit.implementation_uses)
    )
    cross_cutting = sorted(
        {
            "SQL XML composition"
            for query in resolved_queries
            if len(query.source_trace) > 1
        }
        | {
            "Legacy string replacement"
            for unit in pascal_units
            if unit.replace_tokens
        }
    )
    return TransitionMappingArtifact(
        modules=modules,
        shared_services=shared_services,
        cross_cutting_concerns=cross_cutting,
    )


def package_analysis(
    output: AnalysisOutput,
    max_artifact_chars: int,
) -> list[ArtifactManifestEntry]:
    if not output.output_dir:
        raise ValueError("output.output_dir must be set before packaging analysis artifacts.")

    output_root = Path(output.output_dir)
    inventory_dir = output_root / "inventory"
    intermediate_dir = output_root / "intermediate"
    llm_pack_dir = output_root / "llm-pack"
    errors_dir = output_root / "errors"

    for directory in (inventory_dir, intermediate_dir, llm_pack_dir, errors_dir):
        ensure_directory(directory)

    manifest: list[ArtifactManifestEntry] = []

    inventory_payload = {
        "inventory": output.inventory,
        "counts": {
            "pascal_units": len(output.pascal_units),
            "forms": len(output.forms),
            "sql_xml_files": len(output.sql_xml_files),
            "resolved_queries": len(output.resolved_queries),
            "diagnostics": len(output.diagnostics),
        },
    }
    write_json(inventory_dir / "project_inventory.json", inventory_payload)
    manifest.append(
        ArtifactManifestEntry(
            kind="inventory",
            path=(inventory_dir / "project_inventory.json").as_posix(),
            chars=len(str(inventory_payload)),
            tags=["inventory", "summary"],
            recommended_for=["project-scan"],
        )
    )

    write_json(intermediate_dir / "pascal_units.json", output.pascal_units)
    write_json(intermediate_dir / "forms.json", output.forms)
    write_json(intermediate_dir / "sql_xml_files.json", output.sql_xml_files)
    write_json(intermediate_dir / "resolved_queries.json", output.resolved_queries)
    write_json(intermediate_dir / "transition_mapping.json", output.transition_mapping)

    manifest.extend(
        [
            _manifest_entry("intermediate", intermediate_dir / "pascal_units.json", ["pascal"]),
            _manifest_entry("intermediate", intermediate_dir / "forms.json", ["dfm"]),
            _manifest_entry("intermediate", intermediate_dir / "sql_xml_files.json", ["sql-xml"]),
            _manifest_entry("intermediate", intermediate_dir / "resolved_queries.json", ["sql"]),
            _manifest_entry(
                "transition",
                intermediate_dir / "transition_mapping.json",
                ["transition", "mapping"],
            ),
        ]
    )

    project_summary = _build_project_summary(output)
    manifest.extend(
        _write_chunked_markdown(
            llm_pack_dir / "project-summary.md",
            project_summary,
            max_artifact_chars,
            kind="llm-summary",
            tags=["summary", "llm"],
            recommended_for=["project-overview"],
        )
    )

    prompt_recipes = _build_prompt_recipes(output)
    manifest.extend(
        _write_chunked_markdown(
            errors_dir / "prompt-recipes.md",
            prompt_recipes,
            max_artifact_chars,
            kind="prompt-recipes",
            tags=["prompts", "diagnostics"],
            recommended_for=["debugging", "llm-follow-up"],
        )
    )

    diagnostics_md = _build_diagnostics_markdown(output.diagnostics)
    manifest.extend(
        _write_chunked_markdown(
            errors_dir / "diagnostics.md",
            diagnostics_md,
            max_artifact_chars,
            kind="diagnostics",
            tags=["diagnostics"],
            recommended_for=["debugging"],
        )
    )
    write_json(errors_dir / "diagnostics.json", output.diagnostics)
    manifest.append(
        _manifest_entry("diagnostics-json", errors_dir / "diagnostics.json", ["diagnostics"])
    )

    for module in output.transition_mapping.modules:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "modules" / f"{slugify(module.name)}.md",
                _build_module_dossier(module),
                max_artifact_chars,
                kind="module-dossier",
                tags=["module", module.name],
                recommended_for=[module.name],
            )
        )

    for query in output.resolved_queries:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "queries" / f"{slugify(query.name)}.md",
                _build_query_artifact(query),
                max_artifact_chars,
                kind="query-artifact",
                tags=["query", query.name, query.xml_key],
                recommended_for=[query.name],
            )
        )

    dependency_graph = _build_dependency_graph(output)
    write_text(llm_pack_dir / "dependency-graph.dot", dependency_graph)
    manifest.append(
        _manifest_entry(
            "dependency-graph",
            llm_pack_dir / "dependency-graph.dot",
            ["graph", "dependencies"],
        )
    )

    write_json(llm_pack_dir / "manifest.json", manifest)
    manifest.append(
        _manifest_entry("manifest", llm_pack_dir / "manifest.json", ["manifest", "llm"])
    )
    return manifest


def _manifest_entry(kind: str, path: Path, tags: list[str]) -> ArtifactManifestEntry:
    size = len(path.read_text(encoding="utf-8")) if path.exists() else 0
    return ArtifactManifestEntry(kind=kind, path=path.as_posix(), chars=size, tags=tags)


def _write_chunked_markdown(
    path: Path,
    content: str,
    max_artifact_chars: int,
    kind: str,
    tags: list[str],
    recommended_for: list[str],
) -> list[ArtifactManifestEntry]:
    chunks = split_text_chunks(content, max_artifact_chars)
    entries: list[ArtifactManifestEntry] = []
    if len(chunks) == 1:
        write_text(path, chunks[0])
        entries.append(
            ArtifactManifestEntry(
                kind=kind,
                path=path.as_posix(),
                chars=len(chunks[0]),
                tags=tags,
                recommended_for=recommended_for,
            )
        )
        return entries

    for index, chunk in enumerate(chunks, start=1):
        chunk_path = path.with_name(f"{path.stem}.part-{index:02d}{path.suffix}")
        write_text(chunk_path, chunk)
        entries.append(
            ArtifactManifestEntry(
                kind=kind,
                path=chunk_path.as_posix(),
                chars=len(chunk),
                tags=tags + [f"part-{index:02d}"],
                recommended_for=recommended_for,
            )
        )
    return entries


def _build_project_summary(output: AnalysisOutput) -> str:
    severe = [item for item in output.diagnostics if item.severity in {"error", "fatal"}]
    module_names = ", ".join(module.name for module in output.transition_mapping.modules[:10]) or "None"
    migration_order = ", ".join(
        module.name for module in sorted(output.transition_mapping.modules, key=lambda item: item.confidence)
    )
    return f"""# Project Summary

## Overview

- Project root: `{output.inventory.project_root}`
- Pascal units: {len(output.pascal_units)}
- Forms: {len(output.forms)}
- SQL XML files: {len(output.sql_xml_files)}
- Resolved queries: {len(output.resolved_queries)}
- Diagnostics: {len(output.diagnostics)} total, {len(severe)} severe

## Recommended LLM Load Order

1. `project-summary.md`
2. Module dossiers for the module you want to migrate first
3. Query artifacts for queries attached to that module
4. `diagnostics.md` and `prompt-recipes.md` for unresolved context

## Candidate Migration Modules

{module_names}

## Suggested Migration Order

{migration_order or "No modules inferred"}

## Top Risks

{_format_risks(output.transition_mapping.modules, output.diagnostics)}
"""


def _format_risks(modules: list[BusinessModuleArtifact], diagnostics: list[DiagnosticRecord]) -> str:
    risks = []
    for module in modules:
        risks.extend(module.risks)
    if not risks and not diagnostics:
        return "- No major risks detected."
    counts = Counter(risks)
    lines = [f"- {risk} ({count})" for risk, count in counts.most_common(6)]
    severe = [item for item in diagnostics if item.severity in {"error", "fatal"}]
    lines.extend(f"- {item.code}: {item.message}" for item in severe[:4])
    return "\n".join(lines)


def _build_module_dossier(module: BusinessModuleArtifact) -> str:
    return f"""# Module: {module.name}

## Purpose

- Confidence: {module.confidence}
- Source units: {", ".join(module.source_units) or "None"}
- Forms: {", ".join(filter(None, module.forms)) or "None"}
- Query artifacts: {", ".join(module.query_artifacts) or "None"}

## React Mapping

{_bullet_lines(module.react_candidates)}

## Spring Boot Mapping

{_bullet_lines(module.spring_candidates)}

## Risks

{_bullet_lines(module.risks)}

## Open Questions

{_bullet_lines(module.open_questions)}

## Notes

{_bullet_lines(module.notes)}
"""


def _build_query_artifact(query: ResolvedQueryArtifact) -> str:
    parameter_lines = [
        f"- `{item.name}` ({item.data_type or 'Unknown'}) sample={item.sample or '-'} default={item.default or '-'}"
        for item in query.parameter_definitions
    ]
    return f"""# Query: {query.name}

## Source

- XML key: `{query.xml_key}`
- Kind: `{query.kind}`
- File: `{query.file_path}`

## Parameters

{chr(10).join(parameter_lines) if parameter_lines else "- None declared"}

## Placeholders

- Discovered: {", ".join(query.discovered_placeholders) or "None"}
- Unresolved: {", ".join(query.unresolved_placeholders) or "None"}

## Source Trace

{_bullet_lines(query.source_trace)}

## Warnings

{_bullet_lines(query.warnings)}

## Raw SQL Body

```sql
{query.raw_body}
```

## Expanded SQL

```sql
{query.expanded_sql}
```
"""


def _build_diagnostics_markdown(diagnostics: list[DiagnosticRecord]) -> str:
    if not diagnostics:
        return "# Diagnostics\n\n- No diagnostics recorded.\n"
    parts = ["# Diagnostics", ""]
    for item in diagnostics:
        location = item.location.file_path if item.location else "unknown"
        if item.location and item.location.line:
            location = f"{location}:{item.location.line}"
        parts.append(f"## {item.severity.upper()} {item.code}")
        parts.append(f"- Location: {location}")
        parts.append(f"- Message: {item.message}")
        parts.append(f"- Suggestion: {item.suggestion or 'None'}")
        parts.append(f"- Prompt hint: {item.prompt_hint or 'None'}")
        if item.context:
            parts.append(f"- Context: `{item.context}`")
        parts.append("")
    return "\n".join(parts)


def _build_prompt_recipes(output: AnalysisOutput) -> str:
    severe = [item for item in output.diagnostics if item.severity in {"error", "fatal"}]
    recipes = [
        "# Prompt Recipes",
        "",
        "## Use When The Analyzer Fails",
        "",
        "1. Feed the relevant module dossier first.",
        "2. Add the query artifact if the problem involves SQL XML expansion.",
        "3. Include the exact diagnostic block and ask the LLM to propose either a rule override or parser extension.",
        "",
        "## Suggested Follow-up Prompts",
        "",
    ]
    for item in severe[:10]:
        recipes.append(f"- `{item.code}`: {item.prompt_hint or item.message}")
    if not severe:
        recipes.append("- No severe diagnostics were recorded in this run.")
    recipes.append("")
    recipes.append("## Recommended Prompt Skeleton")
    recipes.append("")
    recipes.append(
        "```text\nYou are continuing a Delphi-to-web transition. Use the attached project summary, module dossier, query artifact, and diagnostics. Explain the root cause, missing legacy assumptions, and the smallest rule or code change needed next.\n```"
    )
    return "\n".join(recipes)


def _build_dependency_graph(output: AnalysisOutput) -> str:
    lines = ["digraph legacy_delphi_project {", "  rankdir=LR;"]
    for unit in output.pascal_units:
        unit_node = slugify(unit.unit_name)
        lines.append(f'  "{unit_node}" [label="{unit.unit_name}\\nunit"];')
        for dep in unit.interface_uses + unit.implementation_uses:
            lines.append(f'  "{unit_node}" -> "{slugify(dep)}";')
    for form in output.forms:
        if form.root_name:
            lines.append(f'  "{slugify(form.root_name)}" [label="{form.root_name}\\nform"];')
            if form.linked_unit:
                lines.append(
                    f'  "{slugify(form.root_name)}" -> "{slugify(form.linked_unit)}" [label="unit"];'
                )
    for query in output.resolved_queries:
        query_node = slugify(f"{query.xml_key}-{query.name}")
        lines.append(f'  "{query_node}" [label="{query.name}\\nquery"];')
        for trace in query.source_trace[1:]:
            lines.append(
                f'  "{query_node}" -> "{slugify(trace)}" [label="depends-on"];'
            )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _resolve_form_unit(form: FormSummary, unit_by_name: dict[str, PascalUnitSummary]) -> str | None:
    candidates = [
        form.linked_unit,
        Path(form.file_path).stem,
        form.root_type[1:] if form.root_type and form.root_type.startswith("T") else form.root_type,
    ]
    for candidate in candidates:
        if candidate and candidate.lower() in unit_by_name:
            return unit_by_name[candidate.lower()].unit_name
    return None


def _queries_for_unit(
    unit_name: str,
    queries_by_xml: dict[str, list[ResolvedQueryArtifact]],
) -> list[ResolvedQueryArtifact]:
    candidates = {unit_name.lower(), f"{unit_name.lower()}.xml"}
    stem = unit_name.lower().removeprefix("u")
    candidates.update({stem, f"{stem}.xml"})
    queries: list[ResolvedQueryArtifact] = []
    for candidate in candidates:
        queries.extend(queries_by_xml.get(candidate, []))
    return queries


def _queries_for_pascal_unit(
    unit: PascalUnitSummary | None,
    queries_by_xml: dict[str, list[ResolvedQueryArtifact]],
) -> list[ResolvedQueryArtifact]:
    if unit is None:
        return []

    queries = _queries_for_unit(unit.unit_name, queries_by_xml)
    for xml_reference in unit.xml_references:
        xml_path = Path(xml_reference.lower())
        for candidate in {xml_reference.lower(), xml_path.name, xml_path.stem}:
            queries.extend(queries_by_xml.get(candidate, []))

    if unit.referenced_query_names:
        referenced = {name.lower() for name in unit.referenced_query_names}
        queries = [query for query in queries if query.name.lower() in referenced]

    deduped: dict[tuple[str, str], ResolvedQueryArtifact] = {}
    for query in queries:
        deduped[(query.file_path, query.name)] = query
    return list(deduped.values())


def _derive_module_name(value: str | None) -> str:
    if not value:
        return "UnknownModule"
    result = value
    for prefix in ("Tfrm", "frm", "Tf", "T"):
        if result.startswith(prefix) and len(result) > len(prefix):
            result = result[len(prefix) :]
            break
    return result or value


def _build_react_candidates(module_name: str, form: FormSummary) -> list[str]:
    candidates = [f"{module_name}Page"]
    component_types = {component.component_type for component in form.components}
    if any("grid" in item.lower() for item in component_types):
        candidates.append(f"{module_name}Grid")
    if any("edit" in item.lower() or "combo" in item.lower() for item in component_types):
        candidates.append(f"{module_name}Filters")
    return candidates


def _build_spring_candidates(module_name: str, queries: list[ResolvedQueryArtifact]) -> list[str]:
    candidates = [f"{module_name}Controller", f"{module_name}Service"]
    if any(query.expanded_sql.strip().lower().startswith("select") for query in queries):
        candidates.append(f"{module_name}QueryFacade")
    if any(query.expanded_sql.strip().lower().startswith(("insert", "update", "delete", "merge")) for query in queries):
        candidates.append(f"{module_name}CommandService")
    return sorted(dict.fromkeys(candidates))


def _module_questions(module_name: str, diagnostics: list[DiagnosticRecord]) -> list[str]:
    questions = []
    for item in diagnostics:
        if item.prompt_hint and module_name.lower() in item.prompt_hint.lower():
            questions.append(item.prompt_hint)
    return questions[:5]


def _unit_notes(unit: PascalUnitSummary) -> list[str]:
    notes = []
    if unit.xml_references:
        notes.append(f"XML references: {', '.join(unit.xml_references)}")
    if unit.sql_hints:
        notes.append(f"Inline SQL samples: {', '.join(unit.sql_hints[:3])}")
    if unit.event_handlers:
        notes.append(f"Event handlers: {', '.join(unit.event_handlers[:6])}")
    if unit.component_fields:
        notes.append(f"Published component fields: {', '.join(unit.component_fields[:8])}")
    if unit.published_properties:
        notes.append(f"Published properties: {', '.join(unit.published_properties[:8])}")
    return notes


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {value}" for value in values)
