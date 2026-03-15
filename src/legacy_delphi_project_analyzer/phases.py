from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from legacy_delphi_project_analyzer.models import AnalysisOutput, DiagnosticRecord
from legacy_delphi_project_analyzer.phase_state import BlockingUnknown, PhaseState
from legacy_delphi_project_analyzer.utils import slugify


WORKSPACE_TROUBLE_CODES = {
    "PROJECT_SEARCH_PATH_MISSING",
    "PROJECT_SEARCH_PATH_UNRESOLVED",
    "WORKSPACE_CONFIG_NOT_FOUND",
    "WORKSPACE_CONFIG_INVALID_JSON",
}
PARSE_FAILURE_CODES = {
    "PAS_PARSE_FAILED",
    "DFM_PARSE_FAILED",
    "SQL_XML_PARSE_FAILED",
}
VALIDATION_RESULT_FILE = "runtime/validation-results.json"
HANDOFF_MANIFEST_FILE = "llm-pack/handoff-manifest.json"
PHASE_SEQUENCE = (
    "workspace",
    "parse",
    "clarify",
    "transition_spec",
    "transition_validate",
    "handoff",
)


@dataclass(slots=True)
class PhaseDefinition:
    name: str
    description: str
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    done_criteria: list[str] = field(default_factory=list)


def get_phase_definitions() -> list[PhaseDefinition]:
    return [
        PhaseDefinition(
            name="workspace",
            description="Resolve external Delphi XE search paths and workspace blockers.",
            input_artifacts=["inventory/project_inventory.json"],
            output_artifacts=["runtime/blocking-unknowns.json"],
            done_criteria=[
                "No missing or unresolved search paths remain.",
                "No workspace diagnostics with blocker severity remain.",
            ],
        ),
        PhaseDefinition(
            name="parse",
            description="Recover Delphi, DFM, and SQL XML structures without fatal parse failures.",
            input_artifacts=[
                "inventory/project_inventory.json",
                "intermediate/pascal_units.json",
                "intermediate/forms.json",
                "intermediate/sql_xml_files.json",
            ],
            output_artifacts=["intermediate/resolved_queries.json", "intermediate/business_flows.json"],
            done_criteria=[
                "No fatal parse diagnostics remain.",
                "Recovered intermediate artifacts exist for Pascal, DFM, and SQL XML analysis.",
            ],
        ),
        PhaseDefinition(
            name="clarify",
            description="Reduce blocking legacy unknowns before transition planning expands.",
            input_artifacts=[
                "intermediate/resolved_queries.json",
                "intermediate/business_flows.json",
                "prompt-pack/unknowns.md",
            ],
            output_artifacts=["runtime/blocking-unknowns.json", "runtime/taskpacks"],
            done_criteria=[
                "No high-priority blocker remains for workspace, placeholder, or handler recovery tasks.",
                "Top blockers have task packs ready for LLM or Cline execution.",
            ],
        ),
        PhaseDefinition(
            name="transition_spec",
            description="Generate module transition specs for React and Spring Boot handoff.",
            input_artifacts=["intermediate/transition_mapping.json", "intermediate/business_flows.json"],
            output_artifacts=[
                "intermediate/transition_specs.json",
                "intermediate/bff_sql_artifacts.json",
                "intermediate/ui_pseudo_artifacts.json",
                "intermediate/ui_reference_artifacts.json",
                "intermediate/ui_integration_artifacts.json",
                "llm-pack/transition-specs",
                "llm-pack/bff-sql",
                "llm-pack/ui-pseudo",
                "llm-pack/ui-reference",
                "llm-pack/ui-integration",
            ],
            done_criteria=[
                "Every inferred module has a transition spec.",
                "Every transition module has backend SQL and UI handoff artifacts sized for weak-model prompts.",
                "The load plan and boss summary are available for leadership review.",
            ],
        ),
        PhaseDefinition(
            name="transition_validate",
            description="Validate generated transition specs with bounded LLM tasks.",
            input_artifacts=["intermediate/transition_specs.json", "prompt-pack"],
            output_artifacts=[VALIDATION_RESULT_FILE],
            done_criteria=[
                "Each transition spec has a validation record.",
                "No unsupported first slice remains unreviewed.",
            ],
        ),
        PhaseDefinition(
            name="handoff",
            description="Emit a final handoff manifest with remaining gaps and ready artifacts.",
            input_artifacts=["llm-pack/load-plan.json", "intermediate/transition_specs.json", VALIDATION_RESULT_FILE],
            output_artifacts=[HANDOFF_MANIFEST_FILE],
            done_criteria=[
                "Handoff manifest exists.",
                "Backend SQL and UI handoff artifacts are explicitly listed for downstream LLM use.",
                "Ready modules and remaining gaps are explicitly listed.",
            ],
        ),
    ]


def build_blocking_unknowns(
    output: AnalysisOutput,
    validation_results: list[dict],
) -> list[BlockingUnknown]:
    query_to_modules = {
        query_name: module.name
        for module in output.transition_mapping.modules
        for query_name in module.query_artifacts
    }
    blockers: list[BlockingUnknown] = []
    seen_task_ids: set[str] = set()

    for diagnostic in output.diagnostics:
        if diagnostic.code in WORKSPACE_TROUBLE_CODES:
            raw_path = str(diagnostic.details.get("raw_path") or diagnostic.message)
            task_id = f"task-workspace-{slugify(raw_path)}"
            _append_blocker(
                blockers,
                seen_task_ids,
                BlockingUnknown(
                    task_id=task_id,
                    task_type="resolve_search_path",
                    phase="workspace",
                    priority=95,
                    subject_name=raw_path,
                    reason=diagnostic.message,
                    source_artifacts=[diagnostic.location.file_path] if diagnostic.location else [],
                    fingerprint=f"resolve_search_path:{slugify(raw_path)}",
                    details={"diagnostic_code": diagnostic.code, "raw_path": raw_path},
                ),
            )
        elif diagnostic.code in PARSE_FAILURE_CODES:
            file_path = diagnostic.location.file_path if diagnostic.location else None
            subject = file_path or diagnostic.code
            _append_blocker(
                blockers,
                seen_task_ids,
                BlockingUnknown(
                    task_id=f"task-parse-{slugify(subject)}",
                    task_type="repair_diagnostic",
                    phase="parse",
                    priority=90,
                    subject_name=subject,
                    reason=diagnostic.message,
                    source_artifacts=[file_path] if file_path else [],
                    fingerprint=f"repair_diagnostic:{slugify(subject)}",
                    details={"diagnostic_code": diagnostic.code},
                ),
            )

    for query in output.resolved_queries:
        if query.unresolved_placeholders:
            query_module = query_to_modules.get(query.name)
            _append_blocker(
                blockers,
                seen_task_ids,
                BlockingUnknown(
                    task_id=f"task-query-{slugify(query.name)}-placeholders",
                    task_type="infer_placeholder_meaning",
                    phase="clarify",
                    priority=85,
                    module_name=query_module,
                    subject_name=query.name,
                    reason=f"Blocks DTO and API contract finalization for placeholders: {', '.join(query.unresolved_placeholders)}",
                    source_artifacts=[query.file_path],
                    fingerprint=f"infer_placeholder_meaning:{slugify(query.name)}",
                    details={"placeholders": query.unresolved_placeholders},
                ),
            )
        if query.warnings:
            query_module = query_to_modules.get(query.name)
            _append_blocker(
                blockers,
                seen_task_ids,
                BlockingUnknown(
                    task_id=f"task-query-{slugify(query.name)}-intent",
                    task_type="classify_query_intent",
                    phase="clarify",
                    priority=55,
                    module_name=query_module,
                    subject_name=query.name,
                    reason="Query still has warnings that weaken transition planning confidence.",
                    source_artifacts=[query.file_path],
                    fingerprint=f"classify_query_intent:{slugify(query.name)}",
                    details={"warnings": query.warnings},
                ),
            )

    for flow in output.business_flows:
        for step in flow.steps:
            if not any("no implementation body was recovered" in note.lower() for note in step.notes):
                continue
            _append_blocker(
                blockers,
                seen_task_ids,
                BlockingUnknown(
                    task_id=f"task-flow-{slugify(flow.module_name)}-{slugify(step.handler)}",
                    task_type="summarize_form_behavior",
                    phase="clarify",
                    priority=60,
                    module_name=flow.module_name,
                    subject_name=step.handler,
                    reason=f"UI handler {step.handler} still has no recovered implementation body.",
                    source_artifacts=[],
                    fingerprint=f"summarize_form_behavior:{slugify(flow.module_name)}:{slugify(step.handler)}",
                    details={"handler": step.handler},
                ),
            )

    validated_subjects = {
        str(item.get("subject_name") or item.get("module_name") or "")
        for item in validation_results
        if str(item.get("status") or "").startswith("accepted")
    }
    for spec in output.transition_specs:
        if spec.module_name in validated_subjects and spec.readiness_level == "ready":
            continue
        priority = 80 if spec.readiness_level == "blocked" else 72 if spec.readiness_level == "needs-clarification" else 65
        _append_blocker(
            blockers,
            seen_task_ids,
            BlockingUnknown(
                task_id=f"task-transition-{slugify(spec.module_name)}-validate",
                task_type="validate_transition_spec",
                phase="transition_validate",
                priority=priority,
                module_name=spec.module_name,
                subject_name=spec.module_name,
                reason=f"Transition spec for {spec.module_name} still needs validation at readiness {spec.readiness_level}.",
                source_artifacts=[],
                fingerprint=f"validate_transition_spec:{slugify(spec.module_name)}",
                details={"readiness_level": spec.readiness_level, "readiness_score": spec.readiness_score},
            ),
        )

    blockers.sort(key=lambda item: (-item.priority, item.task_id))
    return blockers


def build_phase_states(
    output: AnalysisOutput,
    blockers: list[BlockingUnknown],
    validation_results: list[dict],
    runtime_dir: Path,
) -> list[PhaseState]:
    definitions = {item.name: item for item in get_phase_definitions()}
    blockers_by_phase: dict[str, list[BlockingUnknown]] = {}
    for blocker in blockers:
        blockers_by_phase.setdefault(blocker.phase, []).append(blocker)

    validation_by_subject = {
        str(item.get("subject_name") or item.get("module_name") or ""): item
        for item in validation_results
        if isinstance(item, dict)
    }
    expected_specs = len(output.transition_specs)
    accepted_validations = sum(
        1
        for item in validation_results
        if isinstance(item, dict) and str(item.get("status") or "").startswith("accepted")
    )

    phase_states: list[PhaseState] = []
    for phase_name in PHASE_SEQUENCE:
        definition = definitions[phase_name]
        current_blockers = blockers_by_phase.get(phase_name, [])
        done = False
        completion_score = 0
        notes: list[str] = []
        if phase_name == "workspace":
            unresolved = len(output.inventory.missing_search_paths) + len(output.inventory.unresolved_search_paths)
            done = unresolved == 0 and not current_blockers
            completion_score = 100 if done else max(10, 100 - unresolved * 35 - len(current_blockers) * 10)
            if unresolved:
                notes.append(f"Workspace gaps remaining: {unresolved}")
        elif phase_name == "parse":
            parse_failures = [
                item for item in output.diagnostics if item.code in PARSE_FAILURE_CODES and item.severity in {"error", "fatal"}
            ]
            done = not parse_failures
            completion_score = 100 if done else max(20, 100 - len(parse_failures) * 30)
            if parse_failures:
                notes.append(f"Parse failures remaining: {len(parse_failures)}")
        elif phase_name == "clarify":
            high_priority = [item for item in current_blockers if item.priority >= 70]
            done = not high_priority
            completion_score = 100 if done else max(15, 100 - len(high_priority) * 20 - len(current_blockers) * 5)
            if current_blockers:
                notes.append(f"Clarification tasks queued: {len(current_blockers)}")
        elif phase_name == "transition_spec":
            expected = max(1, len(output.transition_mapping.modules))
            produced = len(output.transition_specs)
            done = produced >= expected
            completion_score = min(100, int((produced / expected) * 100))
            notes.append(f"Transition specs: {produced}/{expected}")
        elif phase_name == "transition_validate":
            done = expected_specs > 0 and accepted_validations >= expected_specs and not current_blockers
            completion_score = min(100, int((accepted_validations / max(1, expected_specs)) * 100))
            notes.append(f"Accepted validations: {accepted_validations}/{expected_specs}")
            if validation_by_subject:
                notes.append(f"Validation records loaded: {len(validation_by_subject)}")
        elif phase_name == "handoff":
            handoff_path = runtime_dir.parent / HANDOFF_MANIFEST_FILE
            ready_specs = [item for item in output.transition_specs if item.readiness_level == "ready"]
            done = handoff_path.exists() and accepted_validations >= len(ready_specs)
            completion_score = 100 if done else 60 if handoff_path.exists() else 20
            notes.append(f"Handoff manifest: {'present' if handoff_path.exists() else 'missing'}")
        phase_states.append(
            PhaseState(
                phase=phase_name,
                status="done" if done else "in_progress" if current_blockers or completion_score > 0 else "pending",
                completion_score=max(0, min(100, completion_score)),
                blockers=[item.task_id for item in current_blockers[:10]],
                input_artifacts=definition.input_artifacts,
                output_artifacts=definition.output_artifacts,
                done_criteria=definition.done_criteria,
                notes=notes,
            )
        )
    return phase_states


def determine_current_phase(phase_states: list[PhaseState]) -> str:
    for phase_state in phase_states:
        if phase_state.status != "done":
            return phase_state.phase
    return PHASE_SEQUENCE[-1]


def affected_phases_for_task(task_type: str) -> list[str]:
    mapping = {
        "resolve_search_path": ["workspace", "parse", "clarify", "transition_spec", "transition_validate", "handoff"],
        "repair_diagnostic": ["parse", "clarify", "transition_spec", "transition_validate", "handoff"],
        "infer_placeholder_meaning": ["clarify", "transition_spec", "transition_validate", "handoff"],
        "classify_query_intent": ["clarify", "transition_spec", "handoff"],
        "summarize_form_behavior": ["clarify", "transition_spec", "transition_validate", "handoff"],
        "validate_transition_spec": ["transition_validate", "handoff"],
    }
    return mapping.get(task_type, ["clarify", "transition_spec", "transition_validate", "handoff"])


def _append_blocker(
    blockers: list[BlockingUnknown],
    seen_task_ids: set[str],
    blocker: BlockingUnknown,
) -> None:
    if not blocker.task_id or blocker.task_id in seen_task_ids:
        return
    seen_task_ids.add(blocker.task_id)
    blockers.append(blocker)
