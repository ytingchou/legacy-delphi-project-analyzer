from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ArtifactManifestEntry,
    BusinessFlowArtifact,
    BusinessFlowStep,
    BusinessModuleArtifact,
    DiagnosticRecord,
    FormSummary,
    LoadBundleArtifact,
    PascalMethodFlow,
    PascalUnitSummary,
    ResolvedQueryArtifact,
    TransitionMappingArtifact,
)
from legacy_delphi_project_analyzer.prompting import (
    build_repro_bundle_payload,
    build_failure_triage,
    build_prompt_packs,
    build_unknowns_markdown,
    render_closure_summary,
    render_failure_triage_markdown,
    render_prompt_pack_markdown,
)
from legacy_delphi_project_analyzer.reporting import (
    build_boss_summary_markdown,
    build_web_report_html,
)
from legacy_delphi_project_analyzer.utils import (
    ensure_directory,
    estimate_tokens,
    slugify,
    split_text_chunks_by_budget,
    write_json,
    write_text,
)


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


def build_business_flows(
    pascal_units: list[PascalUnitSummary],
    forms: list[FormSummary],
    transition_mapping: TransitionMappingArtifact,
    resolved_queries: list[ResolvedQueryArtifact],
) -> list[BusinessFlowArtifact]:
    units_by_name = {item.unit_name.lower(): item for item in pascal_units}
    forms_by_name = {item.root_name: item for item in forms if item.root_name}
    queries_by_name = {item.name.lower(): item for item in resolved_queries}
    flows: list[BusinessFlowArtifact] = []

    for module in transition_mapping.modules:
        unit_summaries = [
            units_by_name[unit_name.lower()]
            for unit_name in module.source_units
            if unit_name and unit_name.lower() in units_by_name
        ]
        module_forms = [
            forms_by_name[form_name]
            for form_name in module.forms
            if form_name and form_name in forms_by_name
        ]
        method_index: dict[str, PascalMethodFlow] = {}
        for unit in unit_summaries:
            for method_flow in unit.method_flows:
                simple_name = method_flow.method_name.split(".")[-1]
                method_index[simple_name.lower()] = method_flow
                method_index[method_flow.method_name.lower()] = method_flow

        steps: list[BusinessFlowStep] = []
        for form in module_forms:
            for trigger, handler in sorted(form.event_bindings.items()):
                method_flow = method_index.get(handler.lower())
                if method_flow:
                    steps.append(
                        BusinessFlowStep(
                            trigger=trigger,
                            handler=handler,
                            queries=method_flow.query_names,
                            xml_references=method_flow.xml_references,
                            replace_tokens=method_flow.replace_tokens,
                            called_methods=method_flow.called_methods,
                            sql_snippets=method_flow.sql_snippets,
                            notes=["Linked from DFM event binding."],
                        )
                    )
                else:
                    steps.append(
                        BusinessFlowStep(
                            trigger=trigger,
                            handler=handler,
                            notes=["Event handler was declared in DFM but no implementation body was recovered."],
                        )
                    )

        if not steps:
            for unit in unit_summaries:
                for method_flow in unit.method_flows:
                    if not (
                        method_flow.query_names
                        or method_flow.replace_tokens
                        or method_flow.xml_references
                        or method_flow.sql_snippets
                    ):
                        continue
                    steps.append(
                        BusinessFlowStep(
                            trigger="pascal-method",
                            handler=method_flow.method_name,
                            queries=method_flow.query_names,
                            xml_references=method_flow.xml_references,
                            replace_tokens=method_flow.replace_tokens,
                            called_methods=method_flow.called_methods,
                            sql_snippets=method_flow.sql_snippets,
                            notes=["Recovered from Pascal method heuristics without a DFM event source."],
                        )
                    )

        linked_queries = {query for step in steps for query in step.queries}
        unresolved_queries = [
            query_name
            for query_name in module.query_artifacts
            if query_name.lower() not in {item.lower() for item in linked_queries}
        ]
        recommendations = []
        for query_name in module.query_artifacts:
            query = queries_by_name.get(query_name.lower())
            if query and query.unresolved_placeholders:
                recommendations.append(
                    f"Clarify Delphi-side replacement for query {query.name}: {', '.join(query.unresolved_placeholders)}"
                )
        if not recommendations and steps:
            recommendations.append("Use the flow artifact first, then load only the linked query artifacts.")
        flows.append(
            BusinessFlowArtifact(
                module_name=module.name,
                steps=steps,
                unlinked_queries=unresolved_queries,
                recommendations=recommendations,
            )
        )
    return flows


def package_analysis(
    output: AnalysisOutput,
    max_artifact_chars: int,
    max_artifact_tokens: int,
    target_model: str,
) -> tuple[list[ArtifactManifestEntry], list[LoadBundleArtifact]]:
    if not output.output_dir:
        raise ValueError("output.output_dir must be set before packaging analysis artifacts.")

    output_root = Path(output.output_dir)
    inventory_dir = output_root / "inventory"
    intermediate_dir = output_root / "intermediate"
    llm_pack_dir = output_root / "llm-pack"
    errors_dir = output_root / "errors"
    report_dir = output_root / "report"

    for directory in (inventory_dir, intermediate_dir, llm_pack_dir, errors_dir, report_dir):
        ensure_directory(directory)

    manifest: list[ArtifactManifestEntry] = []
    query_to_modules = _query_to_modules(output.transition_mapping)

    inventory_payload = {
        "inventory": output.inventory,
        "counts": {
            "pascal_units": len(output.pascal_units),
            "forms": len(output.forms),
            "sql_xml_files": len(output.sql_xml_files),
            "resolved_queries": len(output.resolved_queries),
            "business_flows": len(output.business_flows),
            "diagnostics": len(output.diagnostics),
        },
    }
    write_json(inventory_dir / "project_inventory.json", inventory_payload)
    manifest.append(
        ArtifactManifestEntry(
            kind="inventory",
            path=(inventory_dir / "project_inventory.json").as_posix(),
            chars=len(str(inventory_payload)),
            estimated_tokens=estimate_tokens(str(inventory_payload)),
            tags=["inventory", "summary"],
            recommended_for=["project-scan"],
        )
    )

    write_json(intermediate_dir / "pascal_units.json", output.pascal_units)
    write_json(intermediate_dir / "forms.json", output.forms)
    write_json(intermediate_dir / "sql_xml_files.json", output.sql_xml_files)
    write_json(intermediate_dir / "resolved_queries.json", output.resolved_queries)
    write_json(intermediate_dir / "transition_mapping.json", output.transition_mapping)
    write_json(intermediate_dir / "business_flows.json", output.business_flows)
    if output.complexity_report is not None:
        write_json(intermediate_dir / "complexity_report.json", output.complexity_report)

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
            _manifest_entry(
                "business-flows",
                intermediate_dir / "business_flows.json",
                ["flow", "transition"],
            ),
        ]
    )
    if output.complexity_report is not None:
        manifest.append(
            _manifest_entry(
                "complexity-report",
                intermediate_dir / "complexity_report.json",
                ["leadership", "complexity"],
            )
        )

    project_summary = _build_project_summary(output)
    manifest.extend(
        _write_chunked_markdown(
            llm_pack_dir / "project-summary.md",
            project_summary,
            max_artifact_chars,
            max_artifact_tokens,
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
            max_artifact_tokens,
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
            max_artifact_tokens,
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
                max_artifact_tokens,
                kind="module-dossier",
                tags=["module", module.name],
                recommended_for=[module.name],
            )
        )

    for flow in output.business_flows:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "flows" / f"{slugify(flow.module_name)}-flow.md",
                _build_business_flow_artifact(flow),
                max_artifact_chars,
                max_artifact_tokens,
                kind="business-flow",
                tags=["flow", flow.module_name],
                recommended_for=[flow.module_name],
            )
        )

    for query in output.resolved_queries:
        recommended_for = [query.name, *sorted(query_to_modules.get(query.name, []))]
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "queries" / f"{slugify(query.name)}.md",
                _build_query_artifact(query),
                max_artifact_chars,
                max_artifact_tokens,
                kind="query-artifact",
                tags=["query", query.name, query.xml_key],
                recommended_for=recommended_for,
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

    knowledge_dir = output_root / "knowledge"
    if (knowledge_dir / "learned_patterns.json").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "learned_patterns.json",
                ["knowledge", "learning"],
            )
        )
    if (knowledge_dir / "suggested_overrides.json").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "suggested_overrides.json",
                ["knowledge", "overrides"],
            )
        )
    if (knowledge_dir / "knowledge-insights.md").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "knowledge-insights.md",
                ["knowledge", "insights"],
            )
        )

    load_bundles = _build_load_bundles(output, manifest)
    output.load_bundles = load_bundles
    for bundle in load_bundles:
        bundle_path = llm_pack_dir / "bundles" / f"{slugify(bundle.name)}.json"
        write_json(bundle_path, bundle)
        manifest.append(
            _manifest_entry(
                "load-bundle",
                bundle_path,
                ["bundle", bundle.category, bundle.name],
            )
        )

    load_plan = _build_load_plan(output, load_bundles)
    write_json(llm_pack_dir / "load-plan.json", load_plan)
    manifest.append(
        _manifest_entry("load-plan", llm_pack_dir / "load-plan.json", ["bundle", "load-plan"])
    )

    prompt_pack_dir = output_root / "prompt-pack"
    prompt_repro_dir = prompt_pack_dir / "repro-bundles"
    ensure_directory(prompt_pack_dir)
    ensure_directory(prompt_repro_dir)
    prompt_packs = build_prompt_packs(output, manifest, load_bundles, target_model=target_model)
    output.prompt_packs = prompt_packs
    closure_summary_path = prompt_pack_dir / "closure-summary.md"
    for prompt_pack in prompt_packs:
        repro_bundle_path = prompt_repro_dir / f"{slugify(prompt_pack.name)}.json"
        prompt_pack.repro_bundle_path = repro_bundle_path.as_posix()
        write_json(
            repro_bundle_path,
            build_repro_bundle_payload(
                source_kind="prompt-pack",
                name=prompt_pack.name,
                goal=prompt_pack.goal,
                target_model=prompt_pack.target_model,
                issue_summary=prompt_pack.issue_summary or prompt_pack.objective,
                subject_name=prompt_pack.subject_name,
                context_paths=prompt_pack.context_paths,
                context_budget_tokens=prompt_pack.context_budget_tokens,
                primary_prompt=prompt_pack.prompt,
                fallback_prompt=prompt_pack.fallback_prompt,
                verification_prompt=prompt_pack.verification_prompt,
                expected_response_schema=prompt_pack.expected_response_schema,
                acceptance_checks=prompt_pack.acceptance_checks,
            ),
        )
        prompt_path = prompt_pack_dir / f"{slugify(prompt_pack.name)}.md"
        prompt_json_path = prompt_pack_dir / f"{slugify(prompt_pack.name)}.json"
        write_text(prompt_path, render_prompt_pack_markdown(prompt_pack))
        write_json(prompt_json_path, prompt_pack)
        manifest.append(
            ArtifactManifestEntry(
                kind="prompt-pack",
                path=prompt_path.as_posix(),
                chars=len(prompt_path.read_text(encoding="utf-8")),
                estimated_tokens=estimate_tokens(prompt_path.read_text(encoding="utf-8")),
                tags=["prompt-pack", prompt_pack.category, prompt_pack.target_model],
                recommended_for=["llm-follow-up", prompt_pack.name],
            )
        )
        manifest.append(
            _manifest_entry(
                "prompt-repro-bundle",
                repro_bundle_path,
                ["prompt-pack", "repro-bundle", prompt_pack.goal],
            )
        )
        manifest.append(
            _manifest_entry(
                "prompt-pack-json",
                prompt_json_path,
                ["prompt-pack", prompt_pack.category, prompt_pack.target_model],
            )
        )

    write_text(closure_summary_path, render_closure_summary(prompt_packs))
    manifest.append(
        _manifest_entry("prompt-closure-summary", closure_summary_path, ["prompt-pack", "summary"])
    )

    unknowns_markdown = build_unknowns_markdown(output)
    write_text(prompt_pack_dir / "unknowns.md", unknowns_markdown)
    manifest.append(
        _manifest_entry("unknowns-ledger", prompt_pack_dir / "unknowns.md", ["prompt-pack", "unknowns"])
    )

    failure_case_dir = output_root / "failure-cases"
    failure_repro_dir = failure_case_dir / "repro-bundles"
    ensure_directory(failure_case_dir)
    ensure_directory(failure_repro_dir)
    failure_triage = build_failure_triage(output, manifest, target_model=target_model)
    output.failure_triage = failure_triage
    for triage in failure_triage:
        repro_bundle_path = failure_repro_dir / f"{slugify(triage.name)}.json"
        triage.repro_bundle_path = repro_bundle_path.as_posix()
        write_json(
            repro_bundle_path,
            build_repro_bundle_payload(
                source_kind="failure-triage",
                name=triage.name,
                goal=triage.goal,
                target_model=target_model,
                issue_summary=triage.summary,
                subject_name=triage.subject_name,
                context_paths=triage.context_paths,
                context_budget_tokens=triage.context_budget_tokens,
                primary_prompt=triage.suggested_prompt,
                fallback_prompt=triage.fallback_prompt,
                verification_prompt=triage.verification_prompt,
                acceptance_checks=triage.acceptance_checks,
            ),
        )
        triage_path = failure_case_dir / f"{slugify(triage.name)}.md"
        triage_json_path = failure_case_dir / f"{slugify(triage.name)}.json"
        write_text(triage_path, render_failure_triage_markdown(triage))
        write_json(triage_json_path, triage)
        manifest.append(
            ArtifactManifestEntry(
                kind="failure-triage",
                path=triage_path.as_posix(),
                chars=len(triage_path.read_text(encoding="utf-8")),
                estimated_tokens=estimate_tokens(triage_path.read_text(encoding="utf-8")),
                tags=["failure-triage", triage.issue_code, triage.severity],
                recommended_for=["debugging", triage.name],
            )
        )
        manifest.append(
            _manifest_entry(
                "failure-repro-bundle",
                repro_bundle_path,
                ["failure-triage", "repro-bundle", triage.issue_code],
            )
        )
        manifest.append(
            _manifest_entry(
                "failure-triage-json",
                triage_json_path,
                ["failure-triage", triage.issue_code, triage.severity],
            )
        )

    if output.complexity_report is not None:
        boss_summary = build_boss_summary_markdown(output)
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "boss-summary.md",
                boss_summary,
                max_artifact_chars,
                max_artifact_tokens,
                kind="boss-summary",
                tags=["leadership", "summary"],
                recommended_for=["leadership"],
            )
        )
        write_json(report_dir / "complexity-report.json", output.complexity_report)
        manifest.append(
            _manifest_entry(
                "report-data",
                report_dir / "complexity-report.json",
                ["leadership", "report"],
            )
        )
        write_text(report_dir / "index.html", build_web_report_html(output))
        manifest.append(
            _manifest_entry("web-report", report_dir / "index.html", ["leadership", "report"])
        )

    write_json(llm_pack_dir / "manifest.json", manifest)
    manifest.append(
        _manifest_entry("manifest", llm_pack_dir / "manifest.json", ["manifest", "llm"])
    )
    return manifest, load_bundles


def _query_to_modules(
    transition_mapping: TransitionMappingArtifact,
) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = defaultdict(set)
    for module in transition_mapping.modules:
        for query_name in module.query_artifacts:
            mapping[query_name].add(module.name)
    return mapping


def _manifest_entry(kind: str, path: Path, tags: list[str]) -> ArtifactManifestEntry:
    size = len(path.read_text(encoding="utf-8")) if path.exists() else 0
    return ArtifactManifestEntry(
        kind=kind,
        path=path.as_posix(),
        chars=size,
        estimated_tokens=estimate_tokens(path.read_text(encoding="utf-8")) if path.exists() else 0,
        tags=tags,
    )


def _write_chunked_markdown(
    path: Path,
    content: str,
    max_artifact_chars: int,
    max_artifact_tokens: int,
    kind: str,
    tags: list[str],
    recommended_for: list[str],
) -> list[ArtifactManifestEntry]:
    chunks = split_text_chunks_by_budget(content, max_artifact_chars, max_artifact_tokens)
    entries: list[ArtifactManifestEntry] = []
    if len(chunks) == 1:
        write_text(path, chunks[0])
        entries.append(
            ArtifactManifestEntry(
                kind=kind,
                path=path.as_posix(),
                chars=len(chunks[0]),
                estimated_tokens=estimate_tokens(chunks[0]),
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
                estimated_tokens=estimate_tokens(chunk),
                tags=tags + [f"part-{index:02d}"],
                recommended_for=recommended_for,
            )
        )
    return entries


def _build_project_summary(output: AnalysisOutput) -> str:
    severe = [item for item in output.diagnostics if item.severity in {"error", "fatal"}]
    module_names = ", ".join(module.name for module in output.transition_mapping.modules[:10]) or "None"
    migration_order = ", ".join(
        module.name
        for module in sorted(output.transition_mapping.modules, key=lambda item: item.confidence)
    )
    return f"""# Project Summary

## Overview

- Project root: `{output.inventory.project_root}`
- Pascal units: {len(output.pascal_units)}
- Forms: {len(output.forms)}
- SQL XML files: {len(output.sql_xml_files)}
- Resolved queries: {len(output.resolved_queries)}
- Business flows: {len(output.business_flows)}
- Diagnostics: {len(output.diagnostics)} total, {len(severe)} severe

## Workspace Coverage

- Scan roots: {len(output.inventory.scan_roots)}
- External roots: {len(output.inventory.external_roots)}
- Project files parsed for search paths: {len(output.inventory.project_files)}
- Configured search paths found: {len(output.inventory.configured_search_paths)}
- Missing search paths: {len(output.inventory.missing_search_paths)}
- Unresolved search paths: {len(output.inventory.unresolved_search_paths)}
- External root list: {", ".join(output.inventory.external_roots) or "None"}

## Recommended LLM Load Order

1. `project-summary.md`
2. `load-plan.json`
3. The target module dossier and business flow artifact
4. Only the query artifacts listed by that module bundle
5. `diagnostics.md` and `prompt-recipes.md` for unresolved context

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


def _build_business_flow_artifact(flow: BusinessFlowArtifact) -> str:
    parts = [f"# Business Flow: {flow.module_name}", ""]
    if not flow.steps:
        parts.extend(["- No business flow steps were recovered.", ""])
    for index, step in enumerate(flow.steps, start=1):
        parts.append(f"## Step {index}")
        parts.append(f"- Trigger: {step.trigger}")
        parts.append(f"- Handler: {step.handler}")
        parts.append(f"- Queries: {', '.join(step.queries) or 'None'}")
        parts.append(f"- XML references: {', '.join(step.xml_references) or 'None'}")
        parts.append(f"- Replace tokens: {', '.join(step.replace_tokens) or 'None'}")
        parts.append(f"- Called methods: {', '.join(step.called_methods) or 'None'}")
        if step.sql_snippets:
            parts.append(f"- SQL hints: {' | '.join(step.sql_snippets[:3])}")
        parts.append(f"- Notes: {'; '.join(step.notes) or 'None'}")
        parts.append("")
    parts.append("## Unlinked Queries")
    parts.append("")
    parts.append(_bullet_lines(flow.unlinked_queries))
    parts.append("")
    parts.append("## Recommendations")
    parts.append("")
    parts.append(_bullet_lines(flow.recommendations))
    parts.append("")
    return "\n".join(parts)


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
        "1. Feed `project-summary.md` and `load-plan.json` first.",
        "2. Add the relevant module dossier and business flow artifact.",
        "3. Add only the linked query artifacts if the problem involves SQL XML expansion.",
        "4. Include the exact diagnostic block and ask the LLM to propose either a rule override or parser extension.",
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
        "```text\nYou are continuing a Delphi-to-web transition. Use the attached project summary, load plan, module dossier, business flow artifact, query artifact, and diagnostics. Explain the root cause, missing legacy assumptions, and the smallest rule or migration design change needed next.\n```"
    )
    return "\n".join(recipes)


def _build_dependency_graph(output: AnalysisOutput) -> str:
    lines = ["digraph legacy_delphi_project {", "  rankdir=LR;"]
    for unit in output.pascal_units:
        unit_node = slugify(unit.unit_name)
        lines.append(f'  "{unit_node}" [label="{unit.unit_name}\\nunit"];')
        for dep in unit.interface_uses + unit.implementation_uses:
            lines.append(f'  "{unit_node}" -> "{slugify(dep)}";')
        for method_flow in unit.method_flows:
            for query_name in method_flow.query_names:
                lines.append(
                    f'  "{unit_node}" -> "{slugify(query_name)}" [label="{method_flow.method_name.split(".")[-1]}"];'
                )
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
            lines.append(f'  "{query_node}" -> "{slugify(trace)}" [label="depends-on"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _build_load_bundles(
    output: AnalysisOutput,
    manifest: list[ArtifactManifestEntry],
) -> list[LoadBundleArtifact]:
    bundles: list[LoadBundleArtifact] = []
    summary_paths = [entry.path for entry in manifest if "project-overview" in entry.recommended_for]
    overview_bundle = LoadBundleArtifact(
        name="ProjectOverview",
        category="overview",
        artifact_paths=summary_paths,
        estimated_tokens=sum(
            entry.estimated_tokens for entry in manifest if entry.path in summary_paths
        ),
        recommended_prompt=(
            "Load the project summary first, then inspect the module bundle for the migration target."
        ),
        notes=["Start every LLM session from this bundle."],
    )
    bundles.append(overview_bundle)

    for module in output.transition_mapping.modules:
        relevant = [
            entry
            for entry in manifest
            if module.name in entry.recommended_for or "project-overview" in entry.recommended_for
        ]
        artifact_paths = [entry.path for entry in relevant]
        estimated = sum(entry.estimated_tokens for entry in relevant)
        bundles.append(
            LoadBundleArtifact(
                name=module.name,
                category="module",
                artifact_paths=artifact_paths,
                estimated_tokens=estimated,
                recommended_prompt=(
                    f"Load the project summary, then the {module.name} module dossier, its business flow artifact, "
                    "and only the listed query artifacts before proposing React/Spring migration steps."
                ),
                notes=[
                    f"Confidence: {module.confidence}",
                    f"Risk count: {len(module.risks)}",
                    f"Query artifacts: {len(module.query_artifacts)}",
                ],
            )
        )
    return bundles


def _build_load_plan(
    output: AnalysisOutput,
    bundles: list[LoadBundleArtifact],
) -> dict:
    module_priority = [
        bundle
        for bundle in bundles
        if bundle.category == "module"
    ]
    module_priority.sort(key=lambda item: item.estimated_tokens)
    return {
        "overview_bundle": next(
            (bundle.name for bundle in bundles if bundle.category == "overview"),
            None,
        ),
        "recommended_module_order": [bundle.name for bundle in module_priority],
        "bundles": bundles,
        "cross_cutting_concerns": output.transition_mapping.cross_cutting_concerns,
        "notes": [
            "Prefer the smallest module bundle that still covers the target business flow.",
            "Only add diagnostics when the current artifacts leave a concrete unanswered question.",
        ],
    }


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
    if any(
        query.expanded_sql.strip().lower().startswith(("insert", "update", "delete", "merge"))
        for query in queries
    ):
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
    if unit.method_flows:
        notes.append(f"Recovered method flows: {len(unit.method_flows)}")
    return notes


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {value}" for value in values)
