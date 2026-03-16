from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SourceLocation:
    file_path: str
    line: int | None = None
    column: int | None = None


@dataclass(slots=True)
class DiagnosticRecord:
    severity: str
    code: str
    message: str
    location: SourceLocation | None = None
    context: str | None = None
    suggestion: str | None = None
    prompt_hint: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectInventory:
    project_root: str
    total_files: int
    total_size_bytes: int
    scan_roots: list[str] = field(default_factory=list)
    external_roots: list[str] = field(default_factory=list)
    project_files: list[str] = field(default_factory=list)
    configured_search_paths: list[str] = field(default_factory=list)
    missing_search_paths: list[str] = field(default_factory=list)
    unresolved_search_paths: list[str] = field(default_factory=list)
    pas_files: list[str] = field(default_factory=list)
    dfm_files: list[str] = field(default_factory=list)
    xml_files: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PascalClassSummary:
    name: str
    ancestor: str | None = None


@dataclass(slots=True)
class PascalMethodFlow:
    method_name: str
    query_names: list[str] = field(default_factory=list)
    xml_references: list[str] = field(default_factory=list)
    replace_tokens: list[str] = field(default_factory=list)
    called_methods: list[str] = field(default_factory=list)
    sql_snippets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PascalUnitSummary:
    unit_name: str
    file_path: str
    interface_uses: list[str] = field(default_factory=list)
    implementation_uses: list[str] = field(default_factory=list)
    classes: list[PascalClassSummary] = field(default_factory=list)
    form_classes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    event_handlers: list[str] = field(default_factory=list)
    published_fields: list[str] = field(default_factory=list)
    published_properties: list[str] = field(default_factory=list)
    component_fields: list[str] = field(default_factory=list)
    sql_hints: list[str] = field(default_factory=list)
    xml_references: list[str] = field(default_factory=list)
    replace_tokens: list[str] = field(default_factory=list)
    referenced_query_names: list[str] = field(default_factory=list)
    method_flows: list[PascalMethodFlow] = field(default_factory=list)
    linked_dfm: str | None = None


@dataclass(slots=True)
class ComponentSummary:
    name: str
    component_type: str
    path: str
    properties: dict[str, str] = field(default_factory=dict)
    events: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class FormSummary:
    file_path: str
    root_name: str | None
    root_type: str | None
    linked_unit: str | None = None
    captions: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    components: list[ComponentSummary] = field(default_factory=list)
    event_bindings: dict[str, str] = field(default_factory=dict)
    is_binary: bool = False
    parse_mode: str = "text"
    parse_notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueryParameter:
    name: str
    data_type: str | None = None
    sample: str | None = None
    default: str | None = None


@dataclass(slots=True)
class QueryFragment:
    kind: str
    text: str | None = None
    name: str | None = None
    xml_name: str | None = None
    target_kind: str | None = None


@dataclass(slots=True)
class QueryDefinition:
    file_path: str
    xml_key: str
    kind: str
    name: str
    raw_body: str
    parameters: list[QueryParameter] = field(default_factory=list)
    fragments: list[QueryFragment] = field(default_factory=list)


@dataclass(slots=True)
class SqlXmlFileSummary:
    file_path: str
    xml_keys: list[str]
    main_queries: list[QueryDefinition] = field(default_factory=list)
    sub_queries: list[QueryDefinition] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedQueryArtifact:
    file_path: str
    xml_key: str
    kind: str
    name: str
    raw_body: str
    expanded_sql: str
    parameter_definitions: list[QueryParameter] = field(default_factory=list)
    discovered_placeholders: list[str] = field(default_factory=list)
    unresolved_placeholders: list[str] = field(default_factory=list)
    source_trace: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BusinessModuleArtifact:
    name: str
    confidence: str
    source_units: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    query_artifacts: list[str] = field(default_factory=list)
    react_candidates: list[str] = field(default_factory=list)
    spring_candidates: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BusinessFlowStep:
    trigger: str
    handler: str
    queries: list[str] = field(default_factory=list)
    xml_references: list[str] = field(default_factory=list)
    replace_tokens: list[str] = field(default_factory=list)
    called_methods: list[str] = field(default_factory=list)
    sql_snippets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BusinessFlowArtifact:
    module_name: str
    steps: list[BusinessFlowStep] = field(default_factory=list)
    unlinked_queries: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LoadBundleArtifact:
    name: str
    category: str
    artifact_paths: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    recommended_prompt: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromptPackArtifact:
    name: str
    category: str
    goal: str
    target_model: str
    objective: str
    subject_name: str | None = None
    issue_summary: str | None = None
    context_paths: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    context_budget_tokens: int = 0
    prompt: str | None = None
    fallback_prompt: str | None = None
    verification_prompt: str | None = None
    expected_response_schema: dict[str, Any] = field(default_factory=dict)
    acceptance_checks: list[str] = field(default_factory=list)
    repro_bundle_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FailureTriageArtifact:
    name: str
    issue_code: str
    severity: str
    goal: str
    summary: str
    likely_root_cause: str
    subject_name: str | None = None
    context_paths: list[str] = field(default_factory=list)
    context_budget_tokens: int = 0
    suggested_prompt: str | None = None
    fallback_prompt: str | None = None
    verification_prompt: str | None = None
    acceptance_checks: list[str] = field(default_factory=list)
    repro_bundle_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromptEffectivenessItem:
    prompt_name: str
    goal: str
    subject_name: str | None = None
    target_model: str | None = None
    attempts: int = 0
    accepted: int = 0
    rejected: int = 0
    needs_follow_up: int = 0
    fallback_uses: int = 0
    success_rate: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromptEffectivenessReport:
    total_feedback_entries: int
    accepted_entries: int
    rejected_entries: int
    follow_up_entries: int
    fallback_entries: int
    top_successful_prompts: list[PromptEffectivenessItem] = field(default_factory=list)
    top_failing_prompts: list[PromptEffectivenessItem] = field(default_factory=list)
    goal_summary: dict[str, dict[str, int | float]] = field(default_factory=dict)
    management_summary: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransitionFieldSpec:
    name: str
    data_type: str = "string"
    required: bool = False
    source_evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReactPageSpec:
    name: str
    route_path: str
    purpose: str
    components: list[str] = field(default_factory=list)
    inputs: list[TransitionFieldSpec] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    data_dependencies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SpringEndpointSpec:
    name: str
    method: str
    path: str
    purpose: str
    query_artifacts: list[str] = field(default_factory=list)
    request_dto: str | None = None
    response_dto: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DtoSpec:
    name: str
    kind: str
    fields: list[TransitionFieldSpec] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransitionSpecArtifact:
    module_name: str
    readiness_score: int
    readiness_level: str
    user_goal: str
    migration_strategy: str
    recommended_first_slice: str
    frontend_pages: list[ReactPageSpec] = field(default_factory=list)
    backend_endpoints: list[SpringEndpointSpec] = field(default_factory=list)
    dtos: list[DtoSpec] = field(default_factory=list)
    supporting_queries: list[str] = field(default_factory=list)
    cross_cutting_concerns: list[str] = field(default_factory=list)
    key_assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BffSqlLogicArtifact:
    module_name: str
    endpoint_name: str
    http_method: str
    route_path: str
    query_name: str
    purpose: str
    request_dto: str | None = None
    response_dto: str | None = None
    request_fields: list[TransitionFieldSpec] = field(default_factory=list)
    response_fields: list[TransitionFieldSpec] = field(default_factory=list)
    compact_sql_summary: str = ""
    oracle_19c_notes: list[str] = field(default_factory=list)
    placeholder_strategy: list[str] = field(default_factory=list)
    implementation_steps: list[str] = field(default_factory=list)
    repository_contract: list[str] = field(default_factory=list)
    service_logic: list[str] = field(default_factory=list)
    evidence_queries: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UiPseudoArtifact:
    module_name: str
    page_name: str
    route_path: str
    purpose: str
    layout_sections: list[str] = field(default_factory=list)
    component_tree: list[str] = field(default_factory=list)
    inputs: list[TransitionFieldSpec] = field(default_factory=list)
    display_fields: list[TransitionFieldSpec] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    api_dependencies: list[str] = field(default_factory=list)
    interaction_steps: list[str] = field(default_factory=list)
    state_model: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UiReferenceArtifact:
    module_name: str
    page_name: str
    route_path: str
    title: str
    summary: str
    layout_sections: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    api_dependencies: list[str] = field(default_factory=list)
    html_file_path: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UiIntegrationArtifact:
    module_name: str
    page_name: str
    route_path: str
    target_feature_dir: str
    suggested_files: list[str] = field(default_factory=list)
    api_dependencies: list[str] = field(default_factory=list)
    dto_dependencies: list[str] = field(default_factory=list)
    integration_steps: list[str] = field(default_factory=list)
    acceptance_checks: list[str] = field(default_factory=list)
    handoff_artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationRecord:
    task_id: str
    task_type: str
    prompt_mode: str
    status: str
    schema_valid: bool
    evidence_valid: bool
    analysis_dir: str
    subject_name: str | None = None
    module_name: str | None = None
    response_path: str | None = None
    parsed_response: dict[str, Any] = field(default_factory=dict)
    supported_claims: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    rejection_category: str | None = None
    validator_feedback: list[str] = field(default_factory=list)
    repair_prompt: str | None = None
    retry_context_paths: list[str] = field(default_factory=list)
    should_learn: bool = False
    should_retry: bool = False
    validated_at: str | None = None


@dataclass(slots=True)
class GeneratedCodeArtifact:
    module_name: str
    language: str
    relative_path: str
    artifact_kind: str
    source_spec: str
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LlmRunArtifact:
    run_id: str
    artifact_kind: str
    artifact_name: str
    prompt_mode: str
    provider_base_url: str
    model: str
    target_model: str | None = None
    goal: str | None = None
    input_token_limit: int = 0
    output_token_limit: int = 0
    temperature: float = 0.0
    request_tokens_estimate: int = 0
    included_context_paths: list[str] = field(default_factory=list)
    skipped_context_paths: list[str] = field(default_factory=list)
    response_text: str = ""
    parsed_response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)
    request_payload: dict[str, Any] = field(default_factory=dict)
    feedback_template_path: str | None = None


@dataclass(slots=True)
class ModuleComplexityScore:
    module_name: str
    score: int
    level: str
    forms: int = 0
    queries: int = 0
    event_steps: int = 0
    unresolved_placeholders: int = 0
    risks: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ComplexityReport:
    project_score: int
    level: str
    total_forms: int
    total_units: int
    total_queries: int
    total_business_flows: int
    total_diagnostics: int
    total_unresolved_placeholders: int
    module_scores: list[ModuleComplexityScore] = field(default_factory=list)
    executive_summary: list[str] = field(default_factory=list)
    migration_recommendations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TransitionMappingArtifact:
    modules: list[BusinessModuleArtifact] = field(default_factory=list)
    shared_services: list[str] = field(default_factory=list)
    cross_cutting_concerns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArtifactManifestEntry:
    kind: str
    path: str
    chars: int
    estimated_tokens: int = 0
    tags: list[str] = field(default_factory=list)
    recommended_for: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisOutput:
    inventory: ProjectInventory
    pascal_units: list[PascalUnitSummary] = field(default_factory=list)
    forms: list[FormSummary] = field(default_factory=list)
    sql_xml_files: list[SqlXmlFileSummary] = field(default_factory=list)
    resolved_queries: list[ResolvedQueryArtifact] = field(default_factory=list)
    transition_mapping: TransitionMappingArtifact = field(
        default_factory=TransitionMappingArtifact
    )
    business_flows: list[BusinessFlowArtifact] = field(default_factory=list)
    transition_specs: list[TransitionSpecArtifact] = field(default_factory=list)
    bff_sql_artifacts: list[BffSqlLogicArtifact] = field(default_factory=list)
    ui_pseudo_artifacts: list[UiPseudoArtifact] = field(default_factory=list)
    ui_reference_artifacts: list[UiReferenceArtifact] = field(default_factory=list)
    ui_integration_artifacts: list[UiIntegrationArtifact] = field(default_factory=list)
    load_bundles: list[LoadBundleArtifact] = field(default_factory=list)
    prompt_packs: list[PromptPackArtifact] = field(default_factory=list)
    failure_triage: list[FailureTriageArtifact] = field(default_factory=list)
    complexity_report: ComplexityReport | None = None
    prompt_effectiveness_report: PromptEffectivenessReport | None = None
    feedback_log: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = field(default_factory=list)
    manifest: list[ArtifactManifestEntry] = field(default_factory=list)
    runtime_state: Any | None = None
    phase_states: list[Any] = field(default_factory=list)
    blocking_unknowns: list[Any] = field(default_factory=list)
    artifact_completeness: Any | None = None
    loop_metrics: Any | None = None
    taskpacks: list[Any] = field(default_factory=list)
    output_dir: str | None = None


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value
