from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
from pathlib import Path
import re

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ArtifactManifestEntry,
    BffSqlLogicArtifact,
    BusinessFlowArtifact,
    BusinessFlowStep,
    BusinessModuleArtifact,
    DiagnosticRecord,
    DtoSpec,
    FormSummary,
    LoadBundleArtifact,
    PascalMethodFlow,
    PascalUnitSummary,
    ReactPageSpec,
    ResolvedQueryArtifact,
    SpringEndpointSpec,
    TransitionFieldSpec,
    TransitionMappingArtifact,
    TransitionSpecArtifact,
    UiIntegrationArtifact,
    UiPseudoArtifact,
    UiReferenceArtifact,
)
from legacy_delphi_project_analyzer.feedback import (
    build_prompt_effectiveness_report,
    render_prompt_effectiveness_markdown,
)
from legacy_delphi_project_analyzer.cheatsheet import write_analysis_cheat_sheet
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
    trim_sql_snippet,
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
    transition_hint_resolver,
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
        transition_hint = transition_hint_resolver(module_name)
        if transition_hint:
            notes.append(f"Learned transition hint: {transition_hint}")
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
        transition_hint = transition_hint_resolver(module_name)
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
                notes=_unit_notes(unit)
                + ([f"Learned transition hint: {transition_hint}"] if transition_hint else []),
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


def build_transition_specs(
    transition_mapping: TransitionMappingArtifact,
    business_flows: list[BusinessFlowArtifact],
    forms: list[FormSummary],
    resolved_queries: list[ResolvedQueryArtifact],
) -> list[TransitionSpecArtifact]:
    flow_by_module = {item.module_name: item for item in business_flows}
    form_by_name = {item.root_name: item for item in forms if item.root_name}
    query_by_name = {item.name: item for item in resolved_queries}
    specs: list[TransitionSpecArtifact] = []

    for module in transition_mapping.modules:
        flow = flow_by_module.get(module.name)
        module_forms = [
            form_by_name[form_name]
            for form_name in module.forms
            if form_name and form_name in form_by_name
        ]
        module_queries = [
            query_by_name[query_name]
            for query_name in module.query_artifacts
            if query_name in query_by_name
        ]
        user_goal = _infer_module_user_goal(module, module_forms, flow, module_queries)
        frontend_pages = _build_frontend_specs(module, module_forms, flow, module_queries, user_goal)
        backend_endpoints, dtos = _build_backend_specs(module, module_queries, frontend_pages)
        readiness_score = _compute_transition_readiness(module, flow, module_queries)
        readiness_level = _transition_readiness_level(readiness_score)
        cross_cutting = _module_cross_cutting_concerns(module, flow, module_queries, transition_mapping)
        specs.append(
            TransitionSpecArtifact(
                module_name=module.name,
                readiness_score=readiness_score,
                readiness_level=readiness_level,
                user_goal=user_goal,
                migration_strategy=_build_migration_strategy(module, flow, module_queries, readiness_level),
                recommended_first_slice=_build_first_slice(
                    module,
                    frontend_pages,
                    backend_endpoints,
                    module_queries,
                    readiness_level,
                ),
                frontend_pages=frontend_pages,
                backend_endpoints=backend_endpoints,
                dtos=dtos,
                supporting_queries=[query.name for query in module_queries],
                cross_cutting_concerns=cross_cutting,
                key_assumptions=_build_key_assumptions(module, flow, module_queries),
                open_questions=module.open_questions,
                risks=module.risks,
                notes=list(
                    dict.fromkeys(
                        module.notes
                        + (flow.recommendations[:3] if flow else [])
                        + [
                            f"React candidates: {', '.join(module.react_candidates) or 'None'}",
                            f"Spring candidates: {', '.join(module.spring_candidates) or 'None'}",
                        ]
                    )
                ),
            )
        )
    return specs


def build_bff_sql_logic_artifacts(
    transition_specs: list[TransitionSpecArtifact],
    resolved_queries: list[ResolvedQueryArtifact],
) -> list[BffSqlLogicArtifact]:
    query_by_name = {item.name: item for item in resolved_queries}
    artifacts: list[BffSqlLogicArtifact] = []

    for spec in transition_specs:
        dto_by_name = {item.name: item for item in spec.dtos}
        for endpoint in spec.backend_endpoints:
            if not endpoint.query_artifacts:
                continue
            primary_query_name = endpoint.query_artifacts[0]
            query = query_by_name.get(primary_query_name)
            if query is None:
                continue
            request_dto = dto_by_name.get(endpoint.request_dto) if endpoint.request_dto else None
            response_dto = dto_by_name.get(endpoint.response_dto) if endpoint.response_dto else None
            artifacts.append(
                BffSqlLogicArtifact(
                    module_name=spec.module_name,
                    endpoint_name=endpoint.name,
                    http_method=endpoint.method,
                    route_path=endpoint.path,
                    query_name=query.name,
                    purpose=endpoint.purpose,
                    request_dto=endpoint.request_dto,
                    response_dto=endpoint.response_dto,
                    request_fields=list(request_dto.fields) if request_dto else [],
                    response_fields=list(response_dto.fields) if response_dto else [],
                    compact_sql_summary=trim_sql_snippet(query.expanded_sql, limit=320),
                    oracle_19c_notes=_oracle_19c_notes(query),
                    placeholder_strategy=_placeholder_strategy(query),
                    implementation_steps=_bff_implementation_steps(spec, endpoint, query),
                    repository_contract=_repository_contract(spec, endpoint, query),
                    service_logic=_service_logic(spec, endpoint, query),
                    evidence_queries=list(endpoint.query_artifacts),
                    notes=list(dict.fromkeys(endpoint.notes + _bff_notes(query))),
                )
            )
    return artifacts


def build_ui_delivery_artifacts(
    transition_specs: list[TransitionSpecArtifact],
    business_flows: list[BusinessFlowArtifact],
) -> tuple[list[UiPseudoArtifact], list[UiReferenceArtifact], list[UiIntegrationArtifact]]:
    flows_by_module = {item.module_name: item for item in business_flows}
    pseudo_artifacts: list[UiPseudoArtifact] = []
    reference_artifacts: list[UiReferenceArtifact] = []
    integration_artifacts: list[UiIntegrationArtifact] = []

    for spec in transition_specs:
        flow = flows_by_module.get(spec.module_name)
        response_fields = [
            field
            for dto in spec.dtos
            if dto.kind == "response"
            for field in dto.fields
        ]
        for page in spec.frontend_pages:
            interaction_steps = _ui_interaction_steps(page, flow, spec)
            pseudo = UiPseudoArtifact(
                module_name=spec.module_name,
                page_name=page.name,
                route_path=page.route_path,
                purpose=page.purpose,
                layout_sections=_ui_layout_sections(page, response_fields),
                component_tree=_ui_component_tree(page, response_fields),
                inputs=list(page.inputs),
                display_fields=_ui_display_fields(page, response_fields),
                actions=list(page.actions),
                api_dependencies=_ui_api_dependencies(page, spec),
                interaction_steps=interaction_steps,
                state_model=_ui_state_model(page, response_fields),
                notes=list(page.notes),
            )
            pseudo_artifacts.append(pseudo)

            reference_artifacts.append(
                UiReferenceArtifact(
                    module_name=spec.module_name,
                    page_name=page.name,
                    route_path=page.route_path,
                    title=f"{spec.module_name} {page.name} Reference UI",
                    summary=f"Static HTML reference for page {page.name}, intended as a React visual baseline.",
                    layout_sections=list(pseudo.layout_sections),
                    actions=list(pseudo.actions),
                    api_dependencies=list(pseudo.api_dependencies),
                    notes=[
                        "Use this HTML as a reference only; keep final React components aligned with the pseudo UI artifact.",
                        "Preserve the route and API dependency names when integrating into another transition project.",
                    ],
                )
            )

            integration_artifacts.append(
                UiIntegrationArtifact(
                    module_name=spec.module_name,
                    page_name=page.name,
                    route_path=page.route_path,
                    target_feature_dir=f"src/features/{_kebab_case(spec.module_name)}",
                    suggested_files=_ui_suggested_files(spec.module_name, page),
                    api_dependencies=_ui_api_dependencies(page, spec),
                    dto_dependencies=_ui_dto_dependencies(spec),
                    integration_steps=_ui_integration_steps(spec, page),
                    acceptance_checks=[
                        "Register the route before wiring business logic.",
                        "Create API client types from the listed DTOs before implementing page state.",
                        "Keep the first slice limited to the listed actions and data dependencies.",
                    ],
                    handoff_artifacts=[
                        f"llm-pack/ui-pseudo/{slugify(spec.module_name)}-{slugify(page.name)}-pseudo-ui.md",
                        f"llm-pack/ui-reference/{slugify(spec.module_name)}-{slugify(page.name)}-reference-ui.html",
                        f"llm-pack/bff-sql/{slugify(spec.module_name)}-*.md",
                    ],
                    notes=[
                        f"Recommended first slice: {spec.recommended_first_slice}",
                        f"Migration strategy: {spec.migration_strategy}",
                    ],
                )
            )
    return pseudo_artifacts, reference_artifacts, integration_artifacts


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
    write_json(intermediate_dir / "transition_specs.json", output.transition_specs)
    write_json(intermediate_dir / "bff_sql_artifacts.json", output.bff_sql_artifacts)
    write_json(intermediate_dir / "ui_pseudo_artifacts.json", output.ui_pseudo_artifacts)
    write_json(intermediate_dir / "ui_reference_artifacts.json", output.ui_reference_artifacts)
    write_json(intermediate_dir / "ui_integration_artifacts.json", output.ui_integration_artifacts)
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
            _manifest_entry(
                "transition-specs",
                intermediate_dir / "transition_specs.json",
                ["transition", "spec"],
            ),
            _manifest_entry(
                "bff-sql-json",
                intermediate_dir / "bff_sql_artifacts.json",
                ["backend-sql", "oracle", "spring"],
            ),
            _manifest_entry(
                "ui-pseudo-json",
                intermediate_dir / "ui_pseudo_artifacts.json",
                ["ui", "react", "pseudo"],
            ),
            _manifest_entry(
                "ui-reference-json",
                intermediate_dir / "ui_reference_artifacts.json",
                ["ui", "react", "reference"],
            ),
            _manifest_entry(
                "ui-integration-json",
                intermediate_dir / "ui_integration_artifacts.json",
                ["ui", "react", "integration"],
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

    for spec in output.transition_specs:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "transition-specs" / f"{slugify(spec.module_name)}-transition-spec.md",
                _build_transition_spec_markdown(spec),
                max_artifact_chars,
                max_artifact_tokens,
                kind="transition-spec",
                tags=["transition-spec", spec.module_name, spec.readiness_level],
                recommended_for=[spec.module_name, "transition-spec"],
            )
        )

    for artifact in output.bff_sql_artifacts:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "bff-sql" / f"{slugify(artifact.module_name)}-{slugify(artifact.query_name)}-bff-sql.md",
                _build_bff_sql_logic_markdown(artifact),
                max_artifact_chars,
                max_artifact_tokens,
                kind="bff-sql",
                tags=["backend-sql", artifact.module_name, artifact.query_name],
                recommended_for=[artifact.module_name, artifact.query_name, artifact.endpoint_name, "backend-sql"],
            )
        )

    for artifact in output.ui_pseudo_artifacts:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "ui-pseudo" / f"{slugify(artifact.module_name)}-{slugify(artifact.page_name)}-pseudo-ui.md",
                _build_ui_pseudo_markdown(artifact),
                max_artifact_chars,
                max_artifact_tokens,
                kind="ui-pseudo",
                tags=["ui", "react", "pseudo", artifact.module_name, artifact.page_name],
                recommended_for=[artifact.module_name, artifact.page_name, "ui-pseudo"],
            )
        )

    for artifact in output.ui_reference_artifacts:
        html_path = llm_pack_dir / "ui-reference" / f"{slugify(artifact.module_name)}-{slugify(artifact.page_name)}-reference-ui.html"
        artifact.html_file_path = html_path.as_posix()
        write_text(html_path, _build_ui_reference_html(artifact))
        manifest.append(
            ArtifactManifestEntry(
                kind="ui-reference-html",
                path=html_path.as_posix(),
                chars=len(html_path.read_text(encoding="utf-8")),
                estimated_tokens=estimate_tokens(html_path.read_text(encoding="utf-8")),
                tags=["ui", "react", "reference", artifact.module_name, artifact.page_name],
                recommended_for=[artifact.module_name, artifact.page_name, "ui-reference"],
            )
        )
        summary_path = llm_pack_dir / "ui-reference" / f"{slugify(artifact.module_name)}-{slugify(artifact.page_name)}-reference-ui.md"
        manifest.extend(
            _write_chunked_markdown(
                summary_path,
                _build_ui_reference_markdown(artifact),
                max_artifact_chars,
                max_artifact_tokens,
                kind="ui-reference",
                tags=["ui", "react", "reference", artifact.module_name, artifact.page_name],
                recommended_for=[artifact.module_name, artifact.page_name, "ui-reference"],
            )
        )

    for artifact in output.ui_integration_artifacts:
        manifest.extend(
            _write_chunked_markdown(
                llm_pack_dir / "ui-integration" / f"{slugify(artifact.module_name)}-{slugify(artifact.page_name)}-ui-integration.md",
                _build_ui_integration_markdown(artifact),
                max_artifact_chars,
                max_artifact_tokens,
                kind="ui-integration",
                tags=["ui", "react", "integration", artifact.module_name, artifact.page_name],
                recommended_for=[artifact.module_name, artifact.page_name, "ui-integration"],
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
    if (knowledge_dir / "accepted_rules.json").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "accepted_rules.json",
                ["knowledge", "accepted-rules"],
            )
        )
    if (knowledge_dir / "feedback-log.json").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "feedback-log.json",
                ["knowledge", "feedback-log"],
            )
        )
    if (knowledge_dir / "feedback-insights.md").exists():
        manifest.append(
            _manifest_entry(
                "knowledge",
                knowledge_dir / "feedback-insights.md",
                ["knowledge", "feedback-insights"],
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

    cheat_sheet_paths = write_analysis_cheat_sheet(output)
    manifest.append(
        _manifest_entry(
            "cline-cheat-sheet",
            Path(cheat_sheet_paths["markdown_path"]),
            ["cline", "cheat-sheet", "qwen"],
        )
    )
    manifest.append(
        _manifest_entry(
            "cline-cheat-sheet-json",
            Path(cheat_sheet_paths["json_path"]),
            ["cline", "cheat-sheet", "json"],
        )
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

    backend_manifest_payload = _build_backend_sql_compact_manifest(output, load_bundles, manifest)
    write_json(llm_pack_dir / "backend-sql-manifest.json", backend_manifest_payload)
    write_text(
        llm_pack_dir / "backend-sql-guide.md",
        _render_backend_sql_compact_manifest(backend_manifest_payload),
    )
    manifest.append(
        _manifest_entry(
            "backend-sql-manifest",
            llm_pack_dir / "backend-sql-manifest.json",
            ["backend-sql", "manifest", "qwen"],
        )
    )
    manifest.append(
        _manifest_entry(
            "backend-sql-guide",
            llm_pack_dir / "backend-sql-guide.md",
            ["backend-sql", "guide", "qwen"],
        )
    )

    ui_manifest_payload = _build_ui_compact_manifest(output, load_bundles, manifest)
    write_json(llm_pack_dir / "ui-handoff-manifest.json", ui_manifest_payload)
    write_text(
        llm_pack_dir / "ui-handoff-guide.md",
        _render_ui_compact_manifest(ui_manifest_payload),
    )
    manifest.append(
        _manifest_entry(
            "ui-handoff-manifest",
            llm_pack_dir / "ui-handoff-manifest.json",
            ["ui", "manifest", "qwen"],
        )
    )
    manifest.append(
        _manifest_entry(
            "ui-handoff-guide",
            llm_pack_dir / "ui-handoff-guide.md",
            ["ui", "guide", "qwen"],
        )
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

    output.prompt_effectiveness_report = build_prompt_effectiveness_report(
        output.prompt_packs,
        output.feedback_log,
    )
    write_json(knowledge_dir / "prompt-effectiveness.json", output.prompt_effectiveness_report)
    write_text(
        knowledge_dir / "prompt-effectiveness.md",
        render_prompt_effectiveness_markdown(output.prompt_effectiveness_report),
    )
    manifest.append(
        _manifest_entry(
            "knowledge",
            knowledge_dir / "prompt-effectiveness.json",
            ["knowledge", "prompt-effectiveness"],
        )
    )
    manifest.append(
        _manifest_entry(
            "knowledge",
            knowledge_dir / "prompt-effectiveness.md",
            ["knowledge", "prompt-effectiveness", "insights"],
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


def _oracle_19c_notes(query: ResolvedQueryArtifact) -> list[str]:
    sql = query.expanded_sql.lower()
    notes = [
        "Use named parameter binding so Delphi-style placeholders become explicit Spring repository inputs.",
    ]
    if any(token in sql for token in ("nvl(", "decode(", "to_char(", "to_date(", "rownum", "sysdate", "dual")):
        notes.append("Preserve Oracle-specific scalar functions and row-shaping behavior when moving into the BFF repository.")
    if "merge " in sql:
        notes.append("Treat MERGE as a command path and keep the statement terminator when generating Oracle DML.")
    if query.expanded_sql.strip().lower().startswith("select"):
        notes.append("Prefer a read-only QueryFacade or repository method that returns DTO rows rather than generic maps.")
    else:
        notes.append("Return an execution result contract with affected row counts or status metadata for command SQL.")
    if len(query.source_trace) > 1:
        notes.append("The final SQL came from composed SQL XML fragments; keep that trace visible when explaining implementation logic.")
    return notes


def _placeholder_strategy(query: ResolvedQueryArtifact) -> list[str]:
    if query.unresolved_placeholders:
        return [
            f"Treat unresolved placeholder `{item}` as an explicit service-side assumption until the legacy replacement rule is confirmed."
            for item in query.unresolved_placeholders
        ] + [
            "Do not inline guessed values into the Oracle SQL; surface them as missing assumptions in the generated BFF logic."
        ]
    if query.discovered_placeholders:
        return [
            "Bind every discovered placeholder through a request DTO or an internal service policy object.",
            "Keep Oracle SQL text stable and move conditional behavior into named parameter assembly.",
        ]
    return ["No placeholder expansion remains; the query can be implemented directly as a bounded repository method."]


def _bff_implementation_steps(
    spec: TransitionSpecArtifact,
    endpoint: SpringEndpointSpec,
    query: ResolvedQueryArtifact,
) -> list[str]:
    steps = [
        f"Expose `{endpoint.method} {endpoint.path}` from the Spring Boot BFF as the thin web contract for module {spec.module_name}.",
        f"Validate request fields before calling query `{query.name}` so Oracle parameter binding stays deterministic.",
        "Translate the request DTO into a named-parameter map rather than rebuilding SQL with ad hoc string concatenation.",
    ]
    if query.unresolved_placeholders:
        steps.append("Pause final SQL assembly until unresolved placeholders are mapped to business rules or service policies.")
    steps.append(
        "Execute the expanded SQL through a repository layer, then map the result into the response DTO used by the first migration slice."
    )
    return steps


def _repository_contract(
    spec: TransitionSpecArtifact,
    endpoint: SpringEndpointSpec,
    query: ResolvedQueryArtifact,
) -> list[str]:
    method_name = f"execute{query.name}"
    if endpoint.method == "GET":
        return [
            f"Repository interface: `{query.name}Repository.{method_name}({endpoint.request_dto or 'request'}) -> list[{endpoint.response_dto or 'row'}]`",
            "Repository implementation should keep Oracle SQL close to the expanded artifact and isolate row mapping logic.",
        ]
    return [
        f"Repository interface: `{query.name}Repository.{method_name}({endpoint.request_dto or 'request'}) -> {endpoint.response_dto or 'result'}`",
        "Repository implementation should return command metadata instead of leaking JDBC update counts into the controller layer.",
    ]


def _service_logic(
    spec: TransitionSpecArtifact,
    endpoint: SpringEndpointSpec,
    query: ResolvedQueryArtifact,
) -> list[str]:
    logic = [
        f"Service method should align to the recommended first slice: {spec.recommended_first_slice}",
        "Keep one orchestration method per endpoint so the weak LLM can generate bounded Spring service code from this artifact.",
    ]
    if endpoint.method == "GET":
        logic.append("Normalize filter inputs and call the repository once; keep pagination and enrichment out of the first slice unless explicitly required.")
    else:
        logic.append("Wrap the repository call in one application service method and return a result DTO with status plus operator-facing notes.")
    return logic


def _bff_notes(query: ResolvedQueryArtifact) -> list[str]:
    notes = []
    if query.parameter_definitions:
        notes.append(f"Declared parameters: {', '.join(item.name for item in query.parameter_definitions)}")
    if query.warnings:
        notes.extend(query.warnings[:3])
    return notes


def _ui_layout_sections(
    page: ReactPageSpec,
    response_fields: list[TransitionFieldSpec],
) -> list[str]:
    sections = ["PageHeader"]
    if page.inputs:
        sections.append("FilterPanel")
    if page.actions:
        sections.append("ActionBar")
    if response_fields or any("grid" in item.lower() for item in page.components):
        sections.append("ResultGrid")
    sections.append("FeedbackStrip")
    return sections


def _ui_component_tree(
    page: ReactPageSpec,
    response_fields: list[TransitionFieldSpec],
) -> list[str]:
    base_name = page.name[:-4] if page.name.endswith("Page") else page.name
    tree = [f"{base_name}PageShell"]
    if page.inputs:
        tree.append(f"{base_name}FilterForm")
    if page.actions:
        tree.append(f"{base_name}ActionBar")
    if response_fields:
        tree.append(f"{base_name}ResultTable")
    tree.extend(page.components)
    return list(dict.fromkeys(tree))


def _ui_display_fields(
    page: ReactPageSpec,
    response_fields: list[TransitionFieldSpec],
) -> list[TransitionFieldSpec]:
    if response_fields:
        return _dedupe_fields(response_fields[:8])
    return [
        TransitionFieldSpec(
            name="summaryText",
            data_type="string",
            required=False,
            source_evidence=[page.name],
            notes=["Fallback field because no response DTO columns were inferred."],
        )
    ]


def _ui_api_dependencies(
    page: ReactPageSpec,
    spec: TransitionSpecArtifact,
) -> list[str]:
    related = []
    for endpoint in spec.backend_endpoints:
        if endpoint.path in page.data_dependencies or endpoint.name in page.data_dependencies:
            related.append(f"{endpoint.method} {endpoint.path}")
    if related:
        return related
    return [f"{item.method} {item.path}" for item in spec.backend_endpoints[:2]]


def _ui_interaction_steps(
    page: ReactPageSpec,
    flow: BusinessFlowArtifact | None,
    spec: TransitionSpecArtifact,
) -> list[str]:
    steps = []
    if flow and flow.steps:
        for item in flow.steps[:4]:
            steps.append(f"{item.trigger} -> {item.handler} -> {', '.join(item.queries) or 'no-query-linked'}")
    if not steps:
        steps.append(f"Render {page.name}, collect inputs, call the first listed backend dependency, then display the response state.")
    steps.append(f"Keep the page aligned to the first migration slice: {spec.recommended_first_slice}")
    return steps


def _ui_state_model(
    page: ReactPageSpec,
    response_fields: list[TransitionFieldSpec],
) -> list[str]:
    state = ["formState", "loading", "errorState"]
    if response_fields:
        state.extend(["resultRows", "selectedRow"])
    if page.actions:
        state.append("actionStatus")
    return state


def _ui_suggested_files(module_name: str, page: ReactPageSpec) -> list[str]:
    feature_dir = f"src/features/{_kebab_case(module_name)}"
    base_name = page.name[:-4] if page.name.endswith("Page") else page.name
    return [
        f"{feature_dir}/pages/{page.name}.tsx",
        f"{feature_dir}/components/{base_name}FilterForm.tsx",
        f"{feature_dir}/components/{base_name}ResultTable.tsx",
        f"{feature_dir}/api/{_camel_case(module_name)}Api.ts",
        f"{feature_dir}/types/{_camel_case(module_name)}Types.ts",
    ]


def _ui_dto_dependencies(spec: TransitionSpecArtifact) -> list[str]:
    return [item.name for item in spec.dtos[:6]]


def _ui_integration_steps(spec: TransitionSpecArtifact, page: ReactPageSpec) -> list[str]:
    return [
        f"Register route `{page.route_path}` inside the target React transition project before adding page state.",
        f"Create typed API helpers for module {spec.module_name} from the listed Spring endpoints and DTOs.",
        f"Build `{page.name}` from the pseudo UI artifact first, then compare against the HTML reference before wiring interactions.",
        "Keep the first commit limited to one route, one primary API path, and the minimum filter/result state needed for the first slice.",
    ]


def _build_ui_reference_html(artifact: UiReferenceArtifact) -> str:
    action_buttons = "".join(
        f'<button class="action-btn" type="button">{escape(item)}</button>'
        for item in (artifact.actions or ["Search"])
    )
    api_items = "".join(f"<li>{escape(item)}</li>" for item in artifact.api_dependencies)
    section_cards = "".join(
        f'<section class="panel"><h2>{escape(item)}</h2><div class="placeholder">Reference block for {escape(item)}</div></section>'
        for item in artifact.layout_sections
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(artifact.title)}</title>
    <style>
      :root {{
        --bg: #f5efe6;
        --ink: #1f2a30;
        --accent: #1e6d72;
        --accent-soft: #d9ecec;
        --panel: rgba(255, 255, 255, 0.88);
        --line: rgba(31, 42, 48, 0.12);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: "Trebuchet MS", "Segoe UI", sans-serif;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, #fff8ef 0, transparent 38%),
          linear-gradient(160deg, var(--bg), #e3eceb);
      }}
      .shell {{
        max-width: 1200px;
        margin: 0 auto;
        padding: 28px 18px 44px;
      }}
      .hero {{
        background: linear-gradient(135deg, var(--accent), #27465a);
        color: #fff;
        padding: 24px;
        border-radius: 24px;
        box-shadow: 0 18px 40px rgba(31, 42, 48, 0.18);
      }}
      .hero p {{ max-width: 60ch; line-height: 1.45; }}
      .meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin-top: 14px;
      }}
      .chip {{
        padding: 8px 12px;
        border-radius: 999px;
        background: rgba(255,255,255,0.14);
      }}
      .actions {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin: 22px 0;
      }}
      .action-btn {{
        border: 0;
        border-radius: 999px;
        padding: 12px 18px;
        background: var(--accent-soft);
        color: var(--ink);
        font-weight: 700;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 16px;
      }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 18px;
        min-height: 180px;
        box-shadow: 0 10px 20px rgba(31, 42, 48, 0.06);
      }}
      .placeholder {{
        margin-top: 12px;
        border: 1px dashed var(--line);
        border-radius: 14px;
        padding: 18px;
        background: rgba(217, 236, 236, 0.35);
      }}
      ul {{
        margin: 10px 0 0;
        padding-left: 18px;
      }}
    </style>
  </head>
  <body>
    <main class="shell">
      <header class="hero">
        <h1>{escape(artifact.title)}</h1>
        <p>{escape(artifact.summary)}</p>
        <div class="meta">
          <span class="chip">Route {escape(artifact.route_path)}</span>
          <span class="chip">Module {escape(artifact.module_name)}</span>
          <span class="chip">Page {escape(artifact.page_name)}</span>
        </div>
      </header>
      <div class="actions">{action_buttons}</div>
      <section class="grid">
        {section_cards}
      </section>
      <section class="panel" style="margin-top: 16px;">
        <h2>API Dependencies</h2>
        <ul>{api_items or '<li>None</li>'}</ul>
      </section>
    </main>
  </body>
</html>
"""


def _build_backend_sql_compact_manifest(
    output: AnalysisOutput,
    bundles: list[LoadBundleArtifact],
    manifest: list[ArtifactManifestEntry],
) -> dict:
    bundle_by_name = {item.name: item for item in bundles}
    entries = []
    for artifact in output.bff_sql_artifacts:
        bff_path = _first_manifest_path(
            manifest,
            kind="bff-sql",
            targets=[artifact.module_name, artifact.query_name],
        )
        query_path = _first_manifest_path(
            manifest,
            kind="query-artifact",
            targets=[artifact.query_name],
        )
        prompt_name = f"{artifact.module_name}{artifact.query_name}BffSql"
        bundle_name = f"{artifact.module_name}BffSql"
        bundle = bundle_by_name.get(bundle_name)
        entries.append(
            {
                "module_name": artifact.module_name,
                "endpoint_name": artifact.endpoint_name,
                "query_name": artifact.query_name,
                "bundle_name": bundle_name,
                "bundle_estimated_tokens": bundle.estimated_tokens if bundle else 0,
                "prompt_pack_name": prompt_name,
                "artifact_paths": [item for item in [bff_path, query_path] if item],
                "recommended_load_order": [
                    item
                    for item in [
                        "llm-pack/project-summary.md",
                        f"llm-pack/bundles/{slugify(bundle_name)}.json",
                        bff_path,
                        query_path,
                    ]
                    if item
                ],
                "notes": [
                    "Keep the weak model scoped to one endpoint and one query at a time.",
                    "Use the BFF SQL artifact before loading the full query artifact.",
                ],
            }
        )
    return {
        "target_model": "qwen3-128k",
        "max_recommended_context_tokens": 6000,
        "entries": entries,
    }


def _render_backend_sql_compact_manifest(payload: dict) -> str:
    lines = [
        "# Backend SQL Compact Guide",
        "",
        f"- Target model: {payload.get('target_model', 'qwen3-128k')}",
        f"- Max recommended context tokens: {payload.get('max_recommended_context_tokens', 6000)}",
        "",
    ]
    for entry in payload.get("entries", []):
        lines.extend(
            [
                f"## {entry.get('module_name')} / {entry.get('endpoint_name')}",
                "",
                f"- Query: {entry.get('query_name')}",
                f"- Bundle: {entry.get('bundle_name')} ({entry.get('bundle_estimated_tokens')} est tokens)",
                f"- Prompt pack: {entry.get('prompt_pack_name')}",
                "- Recommended load order:",
            ]
        )
        lines.extend(f"  - {item}" for item in entry.get("recommended_load_order", []))
        lines.append("- Notes:")
        lines.extend(f"  - {item}" for item in entry.get("notes", []))
        lines.append("")
    return "\n".join(lines)


def _build_ui_compact_manifest(
    output: AnalysisOutput,
    bundles: list[LoadBundleArtifact],
    manifest: list[ArtifactManifestEntry],
) -> dict:
    bundle_by_name = {item.name: item for item in bundles}
    reference_by_page = {
        (item.module_name, item.page_name): item
        for item in output.ui_reference_artifacts
    }
    integration_by_page = {
        (item.module_name, item.page_name): item
        for item in output.ui_integration_artifacts
    }
    entries = []
    for artifact in output.ui_pseudo_artifacts:
        module_name = artifact.module_name
        page_name = artifact.page_name
        reference = reference_by_page.get((module_name, page_name))
        integration = integration_by_page.get((module_name, page_name))
        ui_bundle_name = f"{module_name}Ui"
        ui_integration_bundle_name = f"{module_name}UiIntegration"
        ui_bundle = bundle_by_name.get(ui_bundle_name)
        integration_bundle = bundle_by_name.get(ui_integration_bundle_name)
        pseudo_path = _first_manifest_path(
            manifest,
            kind="ui-pseudo",
            targets=[module_name, page_name],
        )
        reference_path = _first_manifest_path(
            manifest,
            kind="ui-reference",
            targets=[module_name, page_name],
        )
        html_path = reference.html_file_path if reference else None
        integration_path = _first_manifest_path(
            manifest,
            kind="ui-integration",
            targets=[module_name, page_name],
        )
        entries.append(
            {
                "module_name": module_name,
                "page_name": page_name,
                "route_path": artifact.route_path,
                "ui_bundle_name": ui_bundle_name,
                "ui_bundle_estimated_tokens": ui_bundle.estimated_tokens if ui_bundle else 0,
                "ui_integration_bundle_name": ui_integration_bundle_name,
                "ui_integration_bundle_estimated_tokens": integration_bundle.estimated_tokens if integration_bundle else 0,
                "prompt_pack_names": {
                    "pseudo_ui": f"{module_name}{page_name}PseudoUi",
                    "reference_ui": f"{module_name}{page_name}ReferenceUi",
                    "integration": f"{module_name}{page_name}UiIntegration",
                },
                "artifact_paths": [item for item in [pseudo_path, reference_path, html_path, integration_path] if item],
                "recommended_load_order": [
                    item
                    for item in [
                        "llm-pack/project-summary.md",
                        f"llm-pack/bundles/{slugify(ui_bundle_name)}.json",
                        pseudo_path,
                        reference_path,
                        html_path,
                        f"llm-pack/bundles/{slugify(ui_integration_bundle_name)}.json",
                        integration_path,
                    ]
                    if item
                ],
                "target_feature_dir": integration.target_feature_dir if integration else None,
                "notes": [
                    "Generate pseudo UI first, then derive the React/HTML reference UI, then plan project integration.",
                    "Keep the weak model scoped to one page at a time.",
                ],
            }
        )
    return {
        "target_model": "qwen3-128k",
        "max_recommended_context_tokens": 6000,
        "entries": entries,
    }


def _render_ui_compact_manifest(payload: dict) -> str:
    lines = [
        "# UI Compact Guide",
        "",
        f"- Target model: {payload.get('target_model', 'qwen3-128k')}",
        f"- Max recommended context tokens: {payload.get('max_recommended_context_tokens', 6000)}",
        "",
    ]
    for entry in payload.get("entries", []):
        lines.extend(
            [
                f"## {entry.get('module_name')} / {entry.get('page_name')}",
                "",
                f"- Route: {entry.get('route_path')}",
                f"- UI bundle: {entry.get('ui_bundle_name')} ({entry.get('ui_bundle_estimated_tokens')} est tokens)",
                f"- Integration bundle: {entry.get('ui_integration_bundle_name')} ({entry.get('ui_integration_bundle_estimated_tokens')} est tokens)",
                f"- Target feature dir: {entry.get('target_feature_dir') or 'Unknown'}",
                "- Prompt packs:",
            ]
        )
        prompt_pack_names = entry.get("prompt_pack_names", {})
        if isinstance(prompt_pack_names, dict):
            for key, value in prompt_pack_names.items():
                lines.append(f"  - {key}: {value}")
        lines.append("- Recommended load order:")
        lines.extend(f"  - {item}" for item in entry.get("recommended_load_order", []))
        lines.append("- Notes:")
        lines.extend(f"  - {item}" for item in entry.get("notes", []))
        lines.append("")
    return "\n".join(lines)


def _first_manifest_path(
    manifest: list[ArtifactManifestEntry],
    *,
    kind: str,
    targets: list[str],
) -> str | None:
    normalized_targets = {item.lower() for item in targets}
    for entry in manifest:
        if entry.kind != kind:
            continue
        if normalized_targets.intersection(item.lower() for item in entry.recommended_for):
            return entry.path
    return None


def _build_project_summary(output: AnalysisOutput) -> str:
    severe = [item for item in output.diagnostics if item.severity in {"error", "fatal"}]
    module_names = ", ".join(module.name for module in output.transition_mapping.modules[:10]) or "None"
    migration_order = ", ".join(
        module.name
        for module in sorted(output.transition_mapping.modules, key=lambda item: item.confidence)
    )
    readiness_summary = ", ".join(
        f"{item.module_name}:{item.readiness_level}"
        for item in sorted(output.transition_specs, key=lambda item: item.readiness_score, reverse=True)[:6]
    ) or "No transition specs generated"
    return f"""# Project Summary

## Overview

- Project root: `{output.inventory.project_root}`
- Pascal units: {len(output.pascal_units)}
- Forms: {len(output.forms)}
- SQL XML files: {len(output.sql_xml_files)}
- Resolved queries: {len(output.resolved_queries)}
- Business flows: {len(output.business_flows)}
- Transition specs: {len(output.transition_specs)}
- BFF SQL artifacts: {len(output.bff_sql_artifacts)}
- UI pseudo artifacts: {len(output.ui_pseudo_artifacts)}
- UI reference artifacts: {len(output.ui_reference_artifacts)}
- UI integration artifacts: {len(output.ui_integration_artifacts)}
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
3. The target module dossier, transition spec, and business flow artifact
4. For backend work, load the module BFF SQL bundle and only the linked query artifacts
5. For frontend work, load the UI pseudo bundle, then the HTML reference and UI integration bundle
6. `diagnostics.md` and `prompt-recipes.md` for unresolved context

## Candidate Migration Modules

{module_names}

## Suggested Migration Order

{migration_order or "No modules inferred"}

## Transition Readiness

- {readiness_summary}

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


def _build_transition_spec_markdown(spec: TransitionSpecArtifact) -> str:
    parts = [
        f"# Transition Spec: {spec.module_name}",
        "",
        "## Readiness",
        "",
        f"- Readiness: {spec.readiness_level.upper()} ({spec.readiness_score}/100)",
        f"- User goal: {spec.user_goal}",
        f"- Migration strategy: {spec.migration_strategy}",
        f"- Recommended first slice: {spec.recommended_first_slice}",
        "",
        "## Frontend Pages",
        "",
    ]
    if not spec.frontend_pages:
        parts.append("- None")
    for page in spec.frontend_pages:
        parts.extend(
            [
                f"### {page.name}",
                f"- Route: `{page.route_path}`",
                f"- Purpose: {page.purpose}",
                f"- Components: {', '.join(page.components) or 'None'}",
                f"- Actions: {', '.join(page.actions) or 'None'}",
                f"- Data dependencies: {', '.join(page.data_dependencies) or 'None'}",
                "- Inputs:",
                _field_bullets(page.inputs),
                f"- Notes: {'; '.join(page.notes) or 'None'}",
                "",
            ]
        )
    parts.extend(["## Backend Endpoints", ""])
    if not spec.backend_endpoints:
        parts.append("- None")
    for endpoint in spec.backend_endpoints:
        parts.extend(
            [
                f"### {endpoint.name}",
                f"- Method: `{endpoint.method}`",
                f"- Path: `{endpoint.path}`",
                f"- Purpose: {endpoint.purpose}",
                f"- Query artifacts: {', '.join(endpoint.query_artifacts) or 'None'}",
                f"- Request DTO: {endpoint.request_dto or 'None'}",
                f"- Response DTO: {endpoint.response_dto or 'None'}",
                f"- Notes: {'; '.join(endpoint.notes) or 'None'}",
                "",
            ]
        )
    parts.extend(["## DTOs", ""])
    if not spec.dtos:
        parts.append("- None")
    for dto in spec.dtos:
        parts.extend(
            [
                f"### {dto.name}",
                f"- Kind: {dto.kind}",
                _field_bullets(dto.fields),
                f"- Notes: {'; '.join(dto.notes) or 'None'}",
                "",
            ]
        )
    parts.extend(
        [
            "## Assumptions",
            "",
            _bullet_lines(spec.key_assumptions),
            "",
            "## Cross-Cutting Concerns",
            "",
            _bullet_lines(spec.cross_cutting_concerns),
            "",
            "## Open Questions",
            "",
            _bullet_lines(spec.open_questions),
            "",
            "## Risks",
            "",
            _bullet_lines(spec.risks),
            "",
            "## Notes",
            "",
            _bullet_lines(spec.notes),
            "",
        ]
    )
    return "\n".join(parts)


def _build_bff_sql_logic_markdown(artifact: BffSqlLogicArtifact) -> str:
    return f"""# BFF SQL Logic: {artifact.module_name} / {artifact.endpoint_name}

## API Contract

- Method: `{artifact.http_method}`
- Route: `{artifact.route_path}`
- Query: `{artifact.query_name}`
- Purpose: {artifact.purpose}
- Request DTO: {artifact.request_dto or "None"}
- Response DTO: {artifact.response_dto or "None"}

## Compact SQL Summary

```sql
{artifact.compact_sql_summary}
```

## Oracle 19c Notes

{_bullet_lines(artifact.oracle_19c_notes)}

## Request Fields

{_field_bullets(artifact.request_fields)}

## Response Fields

{_field_bullets(artifact.response_fields)}

## Placeholder Strategy

{_bullet_lines(artifact.placeholder_strategy)}

## Implementation Steps

{_bullet_lines(artifact.implementation_steps)}

## Repository Contract

{_bullet_lines(artifact.repository_contract)}

## Service Logic

{_bullet_lines(artifact.service_logic)}

## Evidence Queries

{_bullet_lines(artifact.evidence_queries)}

## Notes

{_bullet_lines(artifact.notes)}
"""


def _build_ui_pseudo_markdown(artifact: UiPseudoArtifact) -> str:
    return f"""# UI Pseudo: {artifact.module_name} / {artifact.page_name}

## Page Contract

- Route: `{artifact.route_path}`
- Purpose: {artifact.purpose}

## Layout Sections

{_bullet_lines(artifact.layout_sections)}

## Component Tree

{_bullet_lines(artifact.component_tree)}

## Inputs

{_field_bullets(artifact.inputs)}

## Display Fields

{_field_bullets(artifact.display_fields)}

## Actions

{_bullet_lines(artifact.actions)}

## API Dependencies

{_bullet_lines(artifact.api_dependencies)}

## Interaction Steps

{_bullet_lines(artifact.interaction_steps)}

## State Model

{_bullet_lines(artifact.state_model)}

## Notes

{_bullet_lines(artifact.notes)}
"""


def _build_ui_reference_markdown(artifact: UiReferenceArtifact) -> str:
    return f"""# UI Reference: {artifact.module_name} / {artifact.page_name}

- Route: `{artifact.route_path}`
- Title: {artifact.title}
- HTML file: `{artifact.html_file_path or 'Pending'}`
- Summary: {artifact.summary}

## Layout Sections

{_bullet_lines(artifact.layout_sections)}

## Actions

{_bullet_lines(artifact.actions)}

## API Dependencies

{_bullet_lines(artifact.api_dependencies)}

## Notes

{_bullet_lines(artifact.notes)}
"""


def _build_ui_integration_markdown(artifact: UiIntegrationArtifact) -> str:
    return f"""# UI Integration: {artifact.module_name} / {artifact.page_name}

## Target Placement

- Route: `{artifact.route_path}`
- Feature directory: `{artifact.target_feature_dir}`

## Suggested Files

{_bullet_lines(artifact.suggested_files)}

## API Dependencies

{_bullet_lines(artifact.api_dependencies)}

## DTO Dependencies

{_bullet_lines(artifact.dto_dependencies)}

## Integration Steps

{_bullet_lines(artifact.integration_steps)}

## Acceptance Checks

{_bullet_lines(artifact.acceptance_checks)}

## Handoff Artifacts

{_bullet_lines(artifact.handoff_artifacts)}

## Notes

{_bullet_lines(artifact.notes)}
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
        "2. Add the relevant module dossier, transition spec, and business flow artifact.",
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
            if (
                module.name in entry.recommended_for
                and entry.kind in {"module-dossier", "business-flow", "transition-spec"}
            )
            or "project-overview" in entry.recommended_for
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
                    f"Load the project summary, then the {module.name} module dossier, transition spec, business flow artifact, "
                    "and only the listed query artifacts before proposing React/Spring migration steps."
                ),
                notes=[
                    f"Confidence: {module.confidence}",
                    f"Risk count: {len(module.risks)}",
                    f"Query artifacts: {len(module.query_artifacts)}",
                ],
            )
        )
        backend_relevant = [
            entry
            for entry in manifest
            if module.name in entry.recommended_for
            and entry.kind in {"transition-spec", "business-flow", "bff-sql", "query-artifact"}
        ]
        bundles.append(
            LoadBundleArtifact(
                name=f"{module.name}BffSql",
                category="backend-sql",
                artifact_paths=[entry.path for entry in backend_relevant],
                estimated_tokens=sum(entry.estimated_tokens for entry in backend_relevant),
                recommended_prompt=(
                    f"Load the {module.name} backend SQL bundle, then generate one Spring Boot BFF endpoint or repository implementation at a time."
                ),
                notes=[
                    "Designed for weak models: keep one endpoint per prompt.",
                    "Use the compact BFF SQL artifact before opening the full query artifact.",
                ],
            )
        )
        ui_relevant = [
            entry
            for entry in manifest
            if module.name in entry.recommended_for
            and entry.kind in {"transition-spec", "business-flow", "ui-pseudo", "ui-reference", "ui-reference-html"}
        ]
        bundles.append(
            LoadBundleArtifact(
                name=f"{module.name}Ui",
                category="ui",
                artifact_paths=[entry.path for entry in ui_relevant],
                estimated_tokens=sum(entry.estimated_tokens for entry in ui_relevant),
                recommended_prompt=(
                    f"Load the {module.name} UI bundle, generate the React pseudo UI first, then derive the HTML or React reference UI."
                ),
                notes=[
                    "Designed for weak models: keep one page per prompt.",
                    "Use the HTML reference only after reading the pseudo UI artifact.",
                ],
            )
        )
        integration_relevant = [
            entry
            for entry in manifest
            if module.name in entry.recommended_for
            and entry.kind in {"transition-spec", "ui-pseudo", "ui-reference", "ui-reference-html", "ui-integration", "bff-sql"}
        ]
        bundles.append(
            LoadBundleArtifact(
                name=f"{module.name}UiIntegration",
                category="ui-integration",
                artifact_paths=[entry.path for entry in integration_relevant],
                estimated_tokens=sum(entry.estimated_tokens for entry in integration_relevant),
                recommended_prompt=(
                    f"Load the {module.name} UI integration bundle when merging the generated UI slice into another React transition project."
                ),
                notes=[
                    "Keeps route, API, and file-structure guidance together.",
                    "Load only the target page artifacts plus the linked backend SQL artifact.",
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
    backend_priority = [
        bundle
        for bundle in bundles
        if bundle.category == "backend-sql"
    ]
    backend_priority.sort(key=lambda item: item.estimated_tokens)
    ui_priority = [
        bundle
        for bundle in bundles
        if bundle.category == "ui"
    ]
    ui_priority.sort(key=lambda item: item.estimated_tokens)
    ui_integration_priority = [
        bundle
        for bundle in bundles
        if bundle.category == "ui-integration"
    ]
    ui_integration_priority.sort(key=lambda item: item.estimated_tokens)
    return {
        "overview_bundle": next(
            (bundle.name for bundle in bundles if bundle.category == "overview"),
            None,
        ),
        "recommended_module_order": [bundle.name for bundle in module_priority],
        "recommended_backend_sql_order": [bundle.name for bundle in backend_priority],
        "recommended_ui_order": [bundle.name for bundle in ui_priority],
        "recommended_ui_integration_order": [bundle.name for bundle in ui_integration_priority],
        "bundles": bundles,
        "cross_cutting_concerns": output.transition_mapping.cross_cutting_concerns,
        "notes": [
            "Prefer the smallest module bundle that still covers the target business flow.",
            "Use the backend-sql bundles to generate one Spring Boot BFF Oracle 19c implementation unit at a time.",
            "Use the UI bundles to generate one pseudo UI or reference UI page at a time.",
            "Use the ui-integration bundles only when moving the generated UI into another React transition project.",
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


def _field_bullets(fields: list[TransitionFieldSpec]) -> str:
    if not fields:
        return "- None"
    lines = []
    for field in fields:
        evidence = ", ".join(field.source_evidence) or "None"
        lines.append(
            f"- `{field.name}` ({field.data_type}, required={str(field.required).lower()}): evidence={evidence}"
        )
    return "\n".join(lines)


def _infer_module_user_goal(
    module: BusinessModuleArtifact,
    forms: list[FormSummary],
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
) -> str:
    captions = [caption for form in forms for caption in form.captions if caption]
    if captions:
        base = captions[0]
    else:
        base = re.sub(r"(?<!^)(?=[A-Z])", " ", module.name).strip()
    if queries and all(query.expanded_sql.strip().lower().startswith("select") for query in queries):
        return f"Search and review {base.lower()} data."
    if any(query.expanded_sql.strip().lower().startswith(("insert", "update", "delete", "merge")) for query in queries):
        return f"Maintain and update {base.lower()} records."
    if flow and flow.steps:
        return f"Execute the {base.lower()} workflow from the legacy form in a web surface."
    return f"Expose the {base.lower()} capability through React and Spring Boot."


def _build_frontend_specs(
    module: BusinessModuleArtifact,
    forms: list[FormSummary],
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
    user_goal: str,
) -> list[ReactPageSpec]:
    if not forms:
        return [
            ReactPageSpec(
                name=module.react_candidates[0] if module.react_candidates else f"{module.name}Page",
                route_path=f"/{_kebab_case(module.name)}",
                purpose=user_goal,
                components=module.react_candidates[1:],
                actions=_fallback_actions(flow),
                data_dependencies=[query.name for query in queries],
                notes=["No DFM form was linked directly; page shape is derived from Pascal and SQL heuristics."],
            )
        ]

    pages: list[ReactPageSpec] = []
    for index, form in enumerate(forms):
        candidate_name = (
            module.react_candidates[min(index, len(module.react_candidates) - 1)]
            if module.react_candidates
            else f"{module.name}Page"
        )
        route_path = f"/{_kebab_case(module.name)}"
        if len(forms) > 1:
            route_path = f"{route_path}/{_kebab_case(form.root_name or str(index + 1))}"
        pages.append(
            ReactPageSpec(
                name=candidate_name,
                route_path=route_path,
                purpose=user_goal,
                components=_react_component_candidates(module, form),
                inputs=_extract_form_inputs(form, queries),
                actions=_extract_form_actions(form, flow),
                data_dependencies=_page_data_dependencies(form, flow, queries),
                notes=_page_notes(form, flow),
            )
        )
    return pages


def _build_backend_specs(
    module: BusinessModuleArtifact,
    queries: list[ResolvedQueryArtifact],
    pages: list[ReactPageSpec],
) -> tuple[list[SpringEndpointSpec], list[DtoSpec]]:
    endpoints: list[SpringEndpointSpec] = []
    dto_map: dict[str, DtoSpec] = {}
    page_input_fields = [field for page in pages for field in page.inputs]

    if not queries:
        fallback_request = f"{module.name}ActionRequest"
        dto_map[fallback_request] = DtoSpec(
            name=fallback_request,
            kind="request",
            fields=_dedupe_fields(page_input_fields),
            notes=["Built from form inputs because no SQL XML query was attached to this module."],
        )
        endpoints.append(
            SpringEndpointSpec(
                name=f"{module.name}Controller",
                method="POST",
                path=f"/api/{_kebab_case(module.name)}/actions",
                purpose=f"Handle the first web slice for module {module.name}.",
                request_dto=fallback_request,
                response_dto=None,
                notes=["Replace this endpoint once the concrete legacy operation is confirmed."],
            )
        )
        return endpoints, list(dto_map.values())

    for query in queries:
        method = _http_method_for_query(query)
        request_name = f"{module.name}{query.name}Request"
        response_name = (
            f"{module.name}{query.name}Row"
            if method == "GET"
            else f"{module.name}{query.name}Result"
        )
        request_fields = _request_fields_for_query(query, page_input_fields)
        response_fields = _response_fields_for_query(query)
        dto_map[request_name] = DtoSpec(
            name=request_name,
            kind="request",
            fields=request_fields,
            notes=[f"Derived from query {query.name} parameters and linked form inputs."],
        )
        if response_fields:
            dto_map[response_name] = DtoSpec(
                name=response_name,
                kind="response",
                fields=response_fields,
                notes=[f"Derived from the SELECT list or SQL result shape of query {query.name}."],
            )
        endpoint_path = f"/api/{_kebab_case(module.name)}/{_kebab_case(query.name)}"
        endpoints.append(
            SpringEndpointSpec(
                name=f"{module.name}{query.name}Endpoint",
                method=method,
                path=endpoint_path,
                purpose=_endpoint_purpose(module, query),
                query_artifacts=[query.name],
                request_dto=request_name if request_fields else None,
                response_dto=response_name if response_fields else None,
                notes=_endpoint_notes(query),
            )
        )
    return endpoints, list(dto_map.values())


def _compute_transition_readiness(
    module: BusinessModuleArtifact,
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
) -> int:
    score = 82 if module.confidence == "high" else 68 if module.confidence == "medium" else 54
    if not flow or not flow.steps:
        score -= 12
    score -= len(module.risks) * 7
    score -= len(module.open_questions) * 4
    score -= sum(len(query.unresolved_placeholders) for query in queries) * 5
    if not queries:
        score -= 8
    if any("Binary DFM" in risk for risk in module.risks):
        score -= 8
    return max(5, min(95, score))


def _transition_readiness_level(score: int) -> str:
    if score >= 75:
        return "ready"
    if score >= 50:
        return "needs-clarification"
    return "blocked"


def _module_cross_cutting_concerns(
    module: BusinessModuleArtifact,
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
    transition_mapping: TransitionMappingArtifact,
) -> list[str]:
    concerns: list[str] = []
    if any(len(query.source_trace) > 1 for query in queries):
        concerns.append("SQL XML composition")
    if any(query.unresolved_placeholders for query in queries):
        concerns.append("Legacy string replacement")
    if flow and any(step.called_methods for step in flow.steps):
        concerns.append("Legacy multi-step handler flow")
    concerns.extend(
        concern
        for concern in transition_mapping.cross_cutting_concerns
        if concern not in concerns
        and (
            concern == "SQL XML composition"
            or concern == "Legacy string replacement"
        )
    )
    return concerns


def _build_migration_strategy(
    module: BusinessModuleArtifact,
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
    readiness_level: str,
) -> str:
    if not queries:
        return "Stabilize the UI behavior first, then define one thin Spring endpoint for the first confirmed action."
    has_reads = any(query.expanded_sql.strip().lower().startswith("select") for query in queries)
    has_writes = any(
        query.expanded_sql.strip().lower().startswith(("insert", "update", "delete", "merge"))
        for query in queries
    )
    if has_reads and has_writes:
        strategy = "Split the module into read APIs backed by a QueryFacade and write APIs backed by a CommandService."
    elif has_reads:
        strategy = "Start with a read-only React page and a Spring QueryFacade, then layer mutations only after the search flow is stable."
    else:
        strategy = "Start from the command path, expose one bounded Spring write endpoint, and add a React confirmation flow around it."
    if readiness_level != "ready":
        strategy += " Keep the first slice narrow until the unresolved placeholders and missing handlers are clarified."
    if flow and not flow.steps:
        strategy += " Recover at least one business flow before broadening the scope."
    return strategy


def _build_first_slice(
    module: BusinessModuleArtifact,
    pages: list[ReactPageSpec],
    endpoints: list[SpringEndpointSpec],
    queries: list[ResolvedQueryArtifact],
    readiness_level: str,
) -> str:
    page_name = pages[0].name if pages else f"{module.name}Page"
    endpoint = endpoints[0] if endpoints else None
    if endpoint and queries:
        sentence = (
            f"Implement `{page_name}` with `{endpoint.method} {endpoint.path}` backed by query `{queries[0].name}` "
            "before expanding to other handlers."
        )
    elif endpoint:
        sentence = f"Implement `{page_name}` and a single endpoint `{endpoint.method} {endpoint.path}` as the first migration slice."
    else:
        sentence = f"Implement `{page_name}` as a shell page and confirm the first backend contract from the legacy flow."
    if readiness_level != "ready":
        sentence += " Do not expand the slice until the listed open questions are answered."
    return sentence


def _build_key_assumptions(
    module: BusinessModuleArtifact,
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
) -> list[str]:
    assumptions: list[str] = []
    if queries:
        assumptions.append("Each SQL XML artifact can be mapped to one Spring endpoint before deeper refactoring.")
    if flow and flow.steps:
        assumptions.append("Recovered DFM events represent the primary user path for the first web slice.")
    if any(query.unresolved_placeholders for query in queries):
        assumptions.append("Placeholder semantics must be confirmed before finalizing DTO validation rules.")
    if not module.forms:
        assumptions.append("The module may behave as a service/helper rather than a user-facing page.")
    return assumptions


def _react_component_candidates(module: BusinessModuleArtifact, form: FormSummary) -> list[str]:
    component_names = []
    if form.datasets:
        component_names.append(f"{module.name}DataGrid")
    if any(component.events for component in form.components):
        component_names.append(f"{module.name}ActionBar")
    if any(
        component.component_type.lower().endswith(("edit", "combobox", "lookupcombobox"))
        for component in form.components
    ):
        component_names.append(f"{module.name}FilterForm")
    component_names.extend(module.react_candidates[1:])
    return list(dict.fromkeys(component_names))


def _extract_form_inputs(
    form: FormSummary,
    queries: list[ResolvedQueryArtifact],
) -> list[TransitionFieldSpec]:
    fields: list[TransitionFieldSpec] = []
    for component in form.components:
        if not _is_input_component(component.component_type):
            continue
        field_name = component.properties.get("DataField") or component.name
        fields.append(
            TransitionFieldSpec(
                name=_camel_case(field_name),
                data_type="string",
                required=False,
                source_evidence=[f"{form.root_name or Path(form.file_path).stem}:{component.name}"],
                notes=[component.component_type],
            )
        )
    for query in queries:
        for parameter in query.parameter_definitions:
            fields.append(
                TransitionFieldSpec(
                    name=_camel_case(parameter.name),
                    data_type=_map_data_type(parameter.data_type),
                    required=True,
                    source_evidence=[query.name],
                )
            )
    return _dedupe_fields(fields)


def _extract_form_actions(
    form: FormSummary,
    flow: BusinessFlowArtifact | None,
) -> list[str]:
    actions = []
    for component in form.components:
        if "button" in component.component_type.lower():
            caption = component.properties.get("Caption")
            handler = component.events.get("OnClick")
            actions.append(caption or handler or component.name)
    if flow:
        actions.extend(step.trigger for step in flow.steps if step.trigger != "pascal-method")
    return list(dict.fromkeys(item for item in actions if item))


def _page_data_dependencies(
    form: FormSummary,
    flow: BusinessFlowArtifact | None,
    queries: list[ResolvedQueryArtifact],
) -> list[str]:
    dependencies = [query.name for query in queries]
    if flow:
        dependencies.extend(query_name for step in flow.steps for query_name in step.queries)
    if form.datasets:
        dependencies.extend(form.datasets)
    return list(dict.fromkeys(item for item in dependencies if item))


def _page_notes(form: FormSummary, flow: BusinessFlowArtifact | None) -> list[str]:
    notes = []
    if form.captions:
        notes.append(f"Legacy caption: {', '.join(form.captions[:2])}")
    if form.datasets:
        notes.append(f"Datasets: {', '.join(form.datasets)}")
    if flow and flow.unlinked_queries:
        notes.append(f"Unlinked queries still need mapping: {', '.join(flow.unlinked_queries)}")
    return notes


def _fallback_actions(flow: BusinessFlowArtifact | None) -> list[str]:
    if not flow:
        return []
    return [step.trigger for step in flow.steps if step.trigger]


def _http_method_for_query(query: ResolvedQueryArtifact) -> str:
    sql = query.expanded_sql.strip().lower()
    if sql.startswith("select"):
        return "GET"
    if sql.startswith("insert"):
        return "POST"
    if sql.startswith("update"):
        return "PUT"
    if sql.startswith("delete"):
        return "DELETE"
    if sql.startswith("merge"):
        return "POST"
    return "POST"


def _request_fields_for_query(
    query: ResolvedQueryArtifact,
    page_input_fields: list[TransitionFieldSpec],
) -> list[TransitionFieldSpec]:
    fields: list[TransitionFieldSpec] = []
    input_by_name = {field.name.lower(): field for field in page_input_fields}
    declared = {parameter.name for parameter in query.parameter_definitions}
    placeholder_names = list(declared) + [item for item in query.discovered_placeholders if item not in declared]
    for placeholder in placeholder_names:
        field_name = _camel_case(placeholder)
        source_field = input_by_name.get(field_name.lower())
        fields.append(
            TransitionFieldSpec(
                name=field_name,
                data_type=_parameter_type_for_query(query, placeholder),
                required=True,
                source_evidence=list(
                    dict.fromkeys(
                        [query.name] + (source_field.source_evidence if source_field else [])
                    )
                ),
                notes=(source_field.notes if source_field else []),
            )
        )
    return _dedupe_fields(fields)


def _response_fields_for_query(query: ResolvedQueryArtifact) -> list[TransitionFieldSpec]:
    sql = query.expanded_sql.strip()
    if not sql.lower().startswith("select"):
        return [
            TransitionFieldSpec(
                name="status",
                data_type="string",
                required=True,
                source_evidence=[query.name],
            ),
            TransitionFieldSpec(
                name="affectedRows",
                data_type="number",
                required=False,
                source_evidence=[query.name],
            ),
        ]
    columns = _extract_select_columns(sql)
    return [
        TransitionFieldSpec(
            name=_camel_case(column),
            data_type="string",
            required=False,
            source_evidence=[query.name],
        )
        for column in columns
    ]


def _endpoint_purpose(module: BusinessModuleArtifact, query: ResolvedQueryArtifact) -> str:
    sql = query.expanded_sql.strip().lower()
    if sql.startswith("select"):
        return f"Return data for the {module.name} web slice using query {query.name}."
    if sql.startswith(("insert", "update", "delete", "merge")):
        return f"Execute the {module.name} command path using query {query.name}."
    return f"Bridge query {query.name} into the {module.name} Spring service layer."


def _endpoint_notes(query: ResolvedQueryArtifact) -> list[str]:
    notes = []
    if query.unresolved_placeholders:
        notes.append(
            "Validate unresolved placeholders before finalizing controller validation: "
            + ", ".join(query.unresolved_placeholders)
        )
    if len(query.source_trace) > 1:
        notes.append("Query SQL is composed from multiple SQL XML fragments.")
    return notes


def _parameter_type_for_query(query: ResolvedQueryArtifact, placeholder: str) -> str:
    for parameter in query.parameter_definitions:
        if parameter.name.lower() == placeholder.lower():
            return _map_data_type(parameter.data_type)
    return "string"


def _map_data_type(value: str | None) -> str:
    mapping = {
        "int": "number",
        "double": "number",
        "string": "string",
        "datetime": "datetime",
        "intarray": "number[]",
        "stringarray": "string[]",
        "sql": "string",
    }
    if not value:
        return "string"
    return mapping.get(value.lower(), value)


def _extract_select_columns(sql: str) -> list[str]:
    match = re.search(r"\bselect\b(?P<select>.*?)\bfrom\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    select_clause = match.group("select")
    columns: list[str] = []
    for item in _split_sql_list(select_clause):
        candidate = item.strip()
        if not candidate or candidate == "*":
            continue
        alias_match = re.search(r"\bas\s+([A-Za-z0-9_]+)$", candidate, re.IGNORECASE)
        if alias_match:
            columns.append(alias_match.group(1))
            continue
        trailing = re.search(r"([A-Za-z0-9_]+)$", candidate)
        if trailing:
            columns.append(trailing.group(1))
    return list(dict.fromkeys(columns))


def _split_sql_list(value: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        items.append("".join(current))
    return items


def _dedupe_fields(fields: list[TransitionFieldSpec]) -> list[TransitionFieldSpec]:
    deduped: dict[str, TransitionFieldSpec] = {}
    for field in fields:
        key = field.name.lower()
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = field
            continue
        existing.required = existing.required or field.required
        existing.source_evidence = list(dict.fromkeys(existing.source_evidence + field.source_evidence))
        existing.notes = list(dict.fromkeys(existing.notes + field.notes))
    return list(deduped.values())


def _is_input_component(component_type: str) -> bool:
    lowered = component_type.lower()
    return any(token in lowered for token in ("edit", "combo", "lookup", "date", "mask"))


def _camel_case(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value.replace(":", " ")).strip()
    if not cleaned:
        return "value"
    parts = [item for item in cleaned.replace("_", " ").split() if item]
    if len(parts) == 1:
        part = parts[0]
        return part[0].lower() + part[1:] if part else "value"
    head, *tail = parts
    return head.lower() + "".join(item[:1].upper() + item[1:].lower() for item in tail)


def _kebab_case(value: str) -> str:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "-", value).replace("_", "-")
    normalized = re.sub(r"[^A-Za-z0-9-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized.strip("-").lower() or "module"
