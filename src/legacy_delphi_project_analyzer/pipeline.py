from __future__ import annotations

from pathlib import Path

from legacy_delphi_project_analyzer.analyzers.dfm import analyze_dfm_file
from legacy_delphi_project_analyzer.analyzers.pascal import analyze_pascal_file
from legacy_delphi_project_analyzer.analyzers.sql_xml import SqlXmlResolver, parse_sql_xml_file
from legacy_delphi_project_analyzer.artifacts import (
    build_business_flows,
    build_transition_mapping,
    package_analysis,
)
from legacy_delphi_project_analyzer.knowledge import KnowledgeStore, load_bootstrap_rules
from legacy_delphi_project_analyzer.models import AnalysisOutput, ProjectInventory
from legacy_delphi_project_analyzer.reporting import build_complexity_report
from legacy_delphi_project_analyzer.utils import make_diagnostic
from legacy_delphi_project_analyzer.workspace import resolve_workspace


PHASE_ORDER = ["discover", "parse", "analyze", "package", "learn"]


def run_analysis(
    project_root: Path,
    output_dir: Path,
    rules_dir: Path | None = None,
    workspace_config_path: Path | None = None,
    extra_search_paths: list[str] | None = None,
    path_variables: dict[str, str] | None = None,
    phases: list[str] | None = None,
    max_artifact_chars: int = 40000,
    max_artifact_tokens: int = 10000,
    target_model: str = "qwen3-128k",
) -> AnalysisOutput:
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    phases = _normalize_phases(phases)
    runtime_rules, bootstrap_diagnostics = load_bootstrap_rules(rules_dir, output_dir)
    merged_search_paths = list(runtime_rules.get("search_paths", []))
    for item in extra_search_paths or []:
        if item not in merged_search_paths:
            merged_search_paths.append(item)
    merged_path_variables = dict(runtime_rules.get("path_variables", {}))
    merged_path_variables.update(path_variables or {})
    workspace = resolve_workspace(
        project_root=project_root,
        extra_search_paths=merged_search_paths,
        workspace_config_path=workspace_config_path.resolve() if workspace_config_path else None,
        path_variables=merged_path_variables,
    )
    knowledge = KnowledgeStore(
        project_root=project_root,
        rules_dir=rules_dir,
        output_dir=output_dir,
        scan_roots=workspace.scan_roots,
    )
    knowledge_diagnostics = knowledge.get_diagnostics()

    inventory = discover_project_files(project_root, knowledge, workspace)
    output = AnalysisOutput(inventory=inventory, output_dir=output_dir.as_posix())
    output.diagnostics.extend(bootstrap_diagnostics)
    output.diagnostics.extend(workspace.diagnostics)
    output.diagnostics.extend(knowledge_diagnostics)

    if "parse" in phases or "analyze" in phases or "package" in phases or "learn" in phases:
        for file_path in inventory.pas_files:
            try:
                summary, diagnostics = analyze_pascal_file(Path(file_path))
                output.pascal_units.append(summary)
                output.diagnostics.extend(diagnostics)
            except Exception as exc:  # pragma: no cover - defensive path
                output.diagnostics.append(
                    make_diagnostic(
                        "error",
                        "PAS_PARSE_FAILED",
                        f"Failed to parse Pascal file: {exc}",
                        file_path=file_path,
                        suggestion="Inspect the file for unsupported legacy syntax and rerun after adding an override or parser rule.",
                        prompt_hint=(
                            "Explain which Delphi syntax pattern in this Pascal file the analyzer could not parse "
                            "and what heuristic should be added."
                        ),
                    )
                )
        for file_path in inventory.dfm_files:
            try:
                summary, diagnostics = analyze_dfm_file(Path(file_path))
                output.forms.append(summary)
                output.diagnostics.extend(diagnostics)
            except Exception as exc:  # pragma: no cover - defensive path
                output.diagnostics.append(
                    make_diagnostic(
                        "error",
                        "DFM_PARSE_FAILED",
                        f"Failed to parse DFM file: {exc}",
                        file_path=file_path,
                        suggestion="If this is a binary DFM, export it to text or add a parser rule for the missing structure.",
                        prompt_hint=(
                            "Describe what is unusual about this DFM file and which components or properties the analyzer "
                            "should learn to parse."
                        ),
                    )
                )
        for file_path in inventory.xml_files:
            try:
                summary, diagnostics = parse_sql_xml_file(Path(file_path), workspace.scan_roots)
                output.diagnostics.extend(diagnostics)
                if summary is not None:
                    output.sql_xml_files.append(summary)
            except Exception as exc:  # pragma: no cover - defensive path
                output.diagnostics.append(
                    make_diagnostic(
                        "error",
                        "SQL_XML_PARSE_FAILED",
                        f"Failed to parse SQL XML file: {exc}",
                        file_path=file_path,
                        suggestion="Validate the XML structure and add a parser extension if the file uses a custom tag.",
                        prompt_hint=(
                            "Explain the unsupported SQL XML structure in this file and propose the smallest parser change "
                            "or override needed."
                        ),
                    )
                )
        _link_forms_to_units(output)

    if "analyze" in phases or "package" in phases or "learn" in phases:
        resolver = SqlXmlResolver(
            output.sql_xml_files,
            diagnostics=output.diagnostics,
            xml_aliases=knowledge.get_xml_aliases(),
        )
        output.resolved_queries = resolver.resolve_all()
        output.transition_mapping = build_transition_mapping(
            output.pascal_units,
            output.forms,
            output.resolved_queries,
            output.diagnostics,
            knowledge.apply_module_override,
            knowledge.get_transition_hint,
        )
        output.business_flows = build_business_flows(
            output.pascal_units,
            output.forms,
            output.transition_mapping,
            output.resolved_queries,
        )
        output.complexity_report = build_complexity_report(output)

    if "learn" in phases or "package" in phases:
        knowledge.learn(
            output.diagnostics,
            output.resolved_queries,
            available_xml_names=inventory.xml_files,
        )

    if "package" in phases:
        output.manifest, output.load_bundles = package_analysis(
            output,
            max_artifact_chars=max_artifact_chars,
            max_artifact_tokens=max_artifact_tokens,
            target_model=target_model,
        )

    return output


def discover_project_files(
    project_root: Path,
    knowledge: KnowledgeStore,
    workspace,
) -> ProjectInventory:
    pas_files: list[str] = []
    dfm_files: list[str] = []
    xml_files: list[str] = []
    other_files: list[str] = []
    total_size_bytes = 0

    for root in workspace.scan_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if knowledge.should_ignore(path):
                continue
            total_size_bytes += path.stat().st_size
            normalized = path.resolve().as_posix()
            suffix = path.suffix.lower()
            if suffix == ".pas":
                pas_files.append(normalized)
            elif suffix == ".dfm":
                dfm_files.append(normalized)
            elif suffix == ".xml":
                xml_files.append(normalized)
            else:
                other_files.append(normalized)

    return ProjectInventory(
        project_root=project_root.as_posix(),
        total_files=len(set(pas_files)) + len(set(dfm_files)) + len(set(xml_files)) + len(set(other_files)),
        total_size_bytes=total_size_bytes,
        scan_roots=[item.as_posix() for item in workspace.scan_roots],
        external_roots=[item.as_posix() for item in workspace.scan_roots if item != project_root],
        project_files=workspace.project_files,
        configured_search_paths=workspace.configured_search_paths,
        missing_search_paths=workspace.missing_search_paths,
        unresolved_search_paths=workspace.unresolved_search_paths,
        pas_files=sorted(set(pas_files)),
        dfm_files=sorted(set(dfm_files)),
        xml_files=sorted(set(xml_files)),
        other_files=sorted(set(other_files)),
    )


def _link_forms_to_units(output: AnalysisOutput) -> None:
    units_by_stem = {Path(item.file_path).stem.lower(): item for item in output.pascal_units}
    units_by_form_class = {}
    for unit in output.pascal_units:
        for form_class in unit.form_classes:
            units_by_form_class[form_class.lower()] = unit

    for form in output.forms:
        stem = Path(form.file_path).stem.lower()
        if stem in units_by_stem:
            unit = units_by_stem[stem]
            form.linked_unit = unit.unit_name
            unit.linked_dfm = form.file_path
            continue
        if form.root_type and form.root_type.lower() in units_by_form_class:
            unit = units_by_form_class[form.root_type.lower()]
            form.linked_unit = unit.unit_name
            unit.linked_dfm = form.file_path


def _normalize_phases(phases: list[str] | None) -> list[str]:
    if not phases:
        return PHASE_ORDER
    requested = []
    for phase in phases:
        if phase == "all":
            return PHASE_ORDER
        if phase not in PHASE_ORDER:
            raise ValueError(f"Unsupported phase '{phase}'. Expected one of: {', '.join(PHASE_ORDER)}")
        requested.append(phase)
    expanded = []
    for phase in PHASE_ORDER:
        if phase in requested:
            expanded.append(phase)
    return expanded
