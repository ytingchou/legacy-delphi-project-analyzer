from __future__ import annotations

from collections import defaultdict

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ArtifactManifestEntry,
    DiagnosticRecord,
    FailureTriageArtifact,
    LoadBundleArtifact,
    PromptPackArtifact,
    ResolvedQueryArtifact,
)

WORKSPACE_TROUBLE_CODES = {
    "PROJECT_SEARCH_PATH_MISSING",
    "PROJECT_SEARCH_PATH_UNRESOLVED",
    "WORKSPACE_CONFIG_NOT_FOUND",
    "WORKSPACE_CONFIG_INVALID_JSON",
}
WEAK_MODEL_HINTS = ("qwen", "128k", "limited", "weak")
DEFAULT_CONTEXT_PROFILE = {"max_tokens": 9000, "max_paths": 6}
WEAK_CONTEXT_PROFILE = {"max_tokens": 6000, "max_paths": 4}


def build_prompt_packs(
    output: AnalysisOutput,
    manifest: list[ArtifactManifestEntry],
    load_bundles: list[LoadBundleArtifact],
    target_model: str,
) -> list[PromptPackArtifact]:
    manifest_by_target = _manifest_by_target(manifest)
    path_to_tokens = {entry.path: entry.estimated_tokens for entry in manifest}
    knowledge_paths = _prioritized_support_paths(manifest)
    profile = _context_profile(target_model)
    packs: list[PromptPackArtifact] = []

    for bundle in load_bundles:
        if bundle.category != "module":
            continue
        raw_context = list(dict.fromkeys(bundle.artifact_paths + knowledge_paths[:2]))
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        packs.append(
            PromptPackArtifact(
                name=f"{bundle.name}Transition",
                category="module-transition",
                goal="propose_smallest_transition_slice",
                target_model=target_model,
                objective=f"Extract the next React + Spring Boot migration slice for module {bundle.name}.",
                subject_name=bundle.name,
                issue_summary=f"Module {bundle.name} still needs a smallest safe migration slice with bounded unknowns.",
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                context_budget_tokens=profile["max_tokens"],
                prompt=_module_transition_prompt(bundle.name),
                fallback_prompt=_module_transition_fallback_prompt(bundle.name),
                verification_prompt=_module_transition_verification_prompt(bundle.name),
                expected_response_schema={
                    "module_name": "string",
                    "user_goal": "string",
                    "react_pages": ["string"],
                    "spring_endpoints": ["string"],
                    "unknowns": ["string"],
                    "next_smallest_step": "string",
                    "confidence": "low|medium|high",
                },
                acceptance_checks=[
                    "The response names only one smallest migration slice.",
                    "Every proposed page and endpoint is supported by attached artifacts.",
                    "Unknowns are explicit instead of invented behavior.",
                ],
                notes=bundle.notes,
            )
        )

    for spec in output.transition_specs:
        raw_context = list(
            dict.fromkeys(
                [
                    entry.path
                    for entry in manifest
                    if spec.module_name in entry.recommended_for or "project-overview" in entry.recommended_for
                ]
                + knowledge_paths[:2]
            )
        )
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        packs.append(
            PromptPackArtifact(
                name=f"{spec.module_name}SpecValidate",
                category="transition-spec-validation",
                goal="validate_transition_spec",
                target_model=target_model,
                objective=f"Validate that the generated transition spec for module {spec.module_name} stays inside the available legacy evidence.",
                subject_name=spec.module_name,
                issue_summary=(
                    f"Transition spec for module {spec.module_name} should be checked against recovered flows, SQL, and diagnostics "
                    f"before implementation planning. Readiness is {spec.readiness_level} ({spec.readiness_score}/100)."
                ),
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                context_budget_tokens=profile["max_tokens"],
                prompt=_transition_spec_validation_prompt(spec.module_name),
                fallback_prompt=_transition_spec_validation_fallback_prompt(spec.module_name),
                verification_prompt=_transition_spec_validation_verification_prompt(spec.module_name),
                expected_response_schema={
                    "module_name": "string",
                    "supported_pages": ["string"],
                    "supported_endpoints": ["string"],
                    "unsupported_items": ["string"],
                    "remaining_unknowns": ["string"],
                    "revised_first_slice": "string",
                },
                acceptance_checks=[
                    "Every supported page or endpoint must exist in the attached transition spec.",
                    "Unsupported items are tied to a specific evidence gap.",
                    "The revised first slice stays smaller or equal in scope to the current one.",
                ],
                notes=[
                    f"Readiness: {spec.readiness_level} ({spec.readiness_score}/100)",
                    spec.recommended_first_slice,
                ],
            )
        )

    for query in output.resolved_queries:
        if not (query.unresolved_placeholders or query.warnings):
            continue
        raw_context = _query_context_paths(query, manifest, manifest_by_target, knowledge_paths)
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        packs.append(
            PromptPackArtifact(
                name=f"{query.name}Intent",
                category="query-intent",
                goal="classify_query_intent",
                target_model=target_model,
                objective=f"Classify the business intent and Oracle behavior of query {query.name}.",
                subject_name=query.name,
                issue_summary=f"Query {query.name} needs a narrow business-intent summary before migration planning.",
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                context_budget_tokens=profile["max_tokens"],
                prompt=_query_intent_prompt(query),
                fallback_prompt=_query_intent_fallback_prompt(query),
                verification_prompt=_query_intent_verification_prompt(query),
                expected_response_schema={
                    "query_name": "string",
                    "business_intent": "string",
                    "read_or_write": "read|write|mixed|unknown",
                    "oracle_specifics": ["string"],
                    "likely_ui_trigger": "string",
                    "missing_evidence": ["string"],
                },
                acceptance_checks=[
                    "Business intent is grounded in the SQL and flow artifacts.",
                    "Oracle-specific notes mention only observable SQL behavior.",
                    "Missing evidence is listed when the intent is uncertain.",
                ],
                notes=query.warnings,
            )
        )
        if query.unresolved_placeholders:
            packs.append(
                PromptPackArtifact(
                    name=f"{query.name}Clarify",
                    category="query-clarification",
                    goal="infer_placeholder_meaning",
                    target_model=target_model,
                    objective=f"Clarify the runtime placeholder meaning for query {query.name}.",
                    subject_name=query.name,
                    issue_summary=(
                        f"Query {query.name} still depends on runtime placeholders: "
                        + ", ".join(query.unresolved_placeholders)
                    ),
                    context_paths=context_paths,
                    estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                    context_budget_tokens=profile["max_tokens"],
                    prompt=_query_clarification_prompt(query),
                    fallback_prompt=_query_clarification_fallback_prompt(query),
                    verification_prompt=_query_clarification_verification_prompt(query),
                    expected_response_schema={
                        "query_name": "string",
                        "business_intent": "string",
                        "placeholder_meanings": {"placeholder": "meaning"},
                        "oracle_specifics": ["string"],
                        "missing_assumptions": ["string"],
                        "recommended_next_prompt": "string",
                    },
                    acceptance_checks=[
                        "Every unresolved placeholder is mapped or marked unknown.",
                        "The answer identifies evidence gaps instead of inventing business rules.",
                        "Recommended next prompt asks for one concrete missing assumption.",
                    ],
                    notes=query.warnings
                    + ["Use the business flow artifact before asking the model to infer placeholder semantics."],
                )
            )

    for diagnostic in output.diagnostics:
        if diagnostic.code not in WORKSPACE_TROUBLE_CODES:
            continue
        raw_context = list(dict.fromkeys(knowledge_paths[:2] + _diagnostic_context_paths(diagnostic, manifest)))
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        prompt_name = _workspace_prompt_name(diagnostic)
        packs.append(
            PromptPackArtifact(
                name=prompt_name,
                category="workspace-resolution",
                goal="resolve_search_path",
                target_model=target_model,
                objective="Resolve a Delphi workspace search path or missing external repository reference.",
                subject_name=diagnostic.details.get("raw_path") or diagnostic.code,
                issue_summary=diagnostic.message,
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                context_budget_tokens=profile["max_tokens"],
                prompt=_workspace_resolution_prompt(diagnostic),
                fallback_prompt=_workspace_resolution_fallback_prompt(diagnostic),
                verification_prompt=_workspace_resolution_verification_prompt(diagnostic),
                expected_response_schema={
                    "raw_path": "string",
                    "resolved_path": "string",
                    "path_variables": {"name": "value"},
                    "recommended_workspace_change": "string",
                    "confidence": "low|medium|high",
                },
                acceptance_checks=[
                    "The answer resolves the raw path to one concrete directory or says it is still unknown.",
                    "Suggested variables are explicit and reusable in workspace config.",
                    "The workspace change is minimal and scoped to the failing path only.",
                ],
                notes=[item for item in [diagnostic.suggestion, diagnostic.prompt_hint] if item],
            )
        )

    for flow in output.business_flows:
        for step in flow.steps:
            if not any("no implementation body was recovered" in note.lower() for note in step.notes):
                continue
            raw_context = [
                entry.path
                for entry in manifest
                if flow.module_name in entry.recommended_for or "diagnostics" in entry.tags
            ]
            context_paths = _select_context_paths(
                list(dict.fromkeys(raw_context + knowledge_paths[:1])),
                path_to_tokens,
                max_tokens=profile["max_tokens"],
                max_paths=profile["max_paths"],
            )
            packs.append(
                PromptPackArtifact(
                    name=f"{flow.module_name}{step.handler}Summary",
                    category="flow-summary",
                    goal="summarize_form_behavior",
                    target_model=target_model,
                    objective=f"Summarize the likely behavior and evidence gaps for handler {step.handler}.",
                    subject_name=f"{flow.module_name}:{step.handler}",
                    issue_summary=(
                        f"Form handler {step.handler} is referenced by the UI but its implementation body was not recovered."
                    ),
                    context_paths=context_paths,
                    estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                    context_budget_tokens=profile["max_tokens"],
                    prompt=_flow_summary_prompt(flow.module_name, step.handler),
                    fallback_prompt=_flow_summary_fallback_prompt(flow.module_name, step.handler),
                    verification_prompt=_flow_summary_verification_prompt(flow.module_name, step.handler),
                    expected_response_schema={
                        "module_name": "string",
                        "handler": "string",
                        "likely_behavior": "string",
                        "likely_queries": ["string"],
                        "missing_evidence": ["string"],
                    },
                    acceptance_checks=[
                        "The response separates likely behavior from missing evidence.",
                        "Any query names mentioned are present in attached artifacts.",
                        "The answer avoids reconstructing a full implementation body.",
                    ],
                    notes=step.notes,
                )
            )

    unknowns = _collect_unknowns(output)
    if unknowns:
        raw_context = knowledge_paths[:2] + [
            entry.path for entry in manifest if "project-overview" in entry.recommended_for
        ]
        context_paths = _select_context_paths(
            list(dict.fromkeys(raw_context)),
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        packs.append(
            PromptPackArtifact(
                name="UnknownsLedger",
                category="unknown-resolution",
                goal="prioritize_unknowns",
                target_model=target_model,
                objective="Resolve only the highest-impact unknowns blocking migration progress.",
                subject_name="project",
                issue_summary=f"{len(unknowns)} unresolved items remain across workspace, query, and flow analysis.",
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                context_budget_tokens=profile["max_tokens"],
                prompt=_unknowns_prompt(unknowns),
                fallback_prompt=_unknowns_fallback_prompt(unknowns),
                verification_prompt=_unknowns_verification_prompt(),
                expected_response_schema={
                    "highest_impact_unknowns": ["string"],
                    "recommended_resolution_order": ["string"],
                    "questions_for_humans": ["string"],
                    "questions_for_llm": ["string"],
                },
                acceptance_checks=[
                    "The response reduces the unknown set instead of expanding it.",
                    "Resolution order is explicit and bounded.",
                    "Human questions are separated from LLM questions.",
                ],
                notes=[f"{len(unknowns)} unresolved items captured for low-capability model triage."],
            )
        )
    return packs


def build_failure_triage(
    output: AnalysisOutput,
    manifest: list[ArtifactManifestEntry],
    target_model: str,
) -> list[FailureTriageArtifact]:
    manifest_by_target = _manifest_by_target(manifest)
    knowledge_paths = _prioritized_support_paths(manifest)
    path_to_tokens = {entry.path: entry.estimated_tokens for entry in manifest}
    profile = _context_profile(target_model)
    triage: list[FailureTriageArtifact] = []

    for diagnostic in output.diagnostics:
        if diagnostic.severity not in {"error", "fatal"} and diagnostic.code not in WORKSPACE_TROUBLE_CODES:
            continue
        raw_context = list(dict.fromkeys(knowledge_paths + _diagnostic_context_paths(diagnostic, manifest)))
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        issue_name = diagnostic.code.lower().replace("_", "-")
        triage.append(
            FailureTriageArtifact(
                name=issue_name,
                issue_code=diagnostic.code,
                severity=diagnostic.severity,
                goal="resolve_search_path" if diagnostic.code in WORKSPACE_TROUBLE_CODES else "repair_diagnostic",
                summary=diagnostic.message,
                likely_root_cause=diagnostic.suggestion or "Unsupported legacy structure needs a new rule or override.",
                subject_name=diagnostic.details.get("raw_path") or diagnostic.code,
                context_paths=context_paths,
                context_budget_tokens=profile["max_tokens"],
                suggested_prompt=(
                    _workspace_resolution_prompt(diagnostic)
                    if diagnostic.code in WORKSPACE_TROUBLE_CODES
                    else _diagnostic_repair_prompt(diagnostic.code, diagnostic.message)
                ),
                fallback_prompt=(
                    _workspace_resolution_fallback_prompt(diagnostic)
                    if diagnostic.code in WORKSPACE_TROUBLE_CODES
                    else "Summarize only the minimum parser change or override entry needed. Do not redesign unrelated parts."
                ),
                verification_prompt=(
                    _workspace_resolution_verification_prompt(diagnostic)
                    if diagnostic.code in WORKSPACE_TROUBLE_CODES
                    else _diagnostic_repair_verification_prompt(diagnostic.code)
                ),
                acceptance_checks=(
                    [
                        "The root cause matches the failing workspace path.",
                        "Any proposed override is concrete and minimal.",
                    ]
                    if diagnostic.code in WORKSPACE_TROUBLE_CODES
                    else [
                        "The root cause is specific to the failing diagnostic.",
                        "The change is the smallest viable parser rule or override.",
                    ]
                ),
                notes=[item for item in [diagnostic.prompt_hint] if item],
            )
        )

    for query in output.resolved_queries:
        if not query.unresolved_placeholders:
            continue
        raw_context = _query_context_paths(query, manifest, manifest_by_target, knowledge_paths)
        context_paths = _select_context_paths(
            raw_context,
            path_to_tokens,
            max_tokens=profile["max_tokens"],
            max_paths=profile["max_paths"],
        )
        triage.append(
            FailureTriageArtifact(
                name=f"{query.name.lower()}-unresolved-placeholders",
                issue_code="QUERY_UNRESOLVED_PLACEHOLDERS",
                severity="warning",
                goal="infer_placeholder_meaning",
                summary=f"Query {query.name} still depends on runtime placeholders: {', '.join(query.unresolved_placeholders)}",
                likely_root_cause="Delphi-side string replacement or UI parameter mapping is not yet documented.",
                subject_name=query.name,
                context_paths=context_paths,
                context_budget_tokens=profile["max_tokens"],
                suggested_prompt=_query_clarification_prompt(query),
                fallback_prompt=_query_clarification_fallback_prompt(query),
                verification_prompt=_query_clarification_verification_prompt(query),
                acceptance_checks=[
                    "All unresolved placeholders are addressed or marked unknown.",
                    "The answer identifies missing assumptions explicitly.",
                ],
                notes=["Use the business flow artifact before asking the model to infer placeholder semantics."],
            )
        )

    for flow in output.business_flows:
        for step in flow.steps:
            if any("no implementation body was recovered" in note.lower() for note in step.notes):
                raw_context = [
                    entry.path
                    for entry in manifest
                    if flow.module_name in entry.recommended_for or "diagnostics" in entry.tags
                ]
                context_paths = _select_context_paths(
                    list(dict.fromkeys(raw_context + knowledge_paths[:1])),
                    path_to_tokens,
                    max_tokens=profile["max_tokens"],
                    max_paths=profile["max_paths"],
                )
                triage.append(
                    FailureTriageArtifact(
                        name=f"{flow.module_name.lower()}-{step.handler.lower()}-missing-body",
                        issue_code="FLOW_HANDLER_BODY_MISSING",
                        severity="warning",
                        goal="summarize_form_behavior",
                        summary=f"Handler {step.handler} is referenced by the form but its implementation body was not recovered.",
                        likely_root_cause="The Pascal unit may be incomplete, split across files, or use unsupported syntax.",
                        subject_name=f"{flow.module_name}:{step.handler}",
                        context_paths=context_paths,
                        context_budget_tokens=profile["max_tokens"],
                        suggested_prompt=_flow_summary_prompt(flow.module_name, step.handler),
                        fallback_prompt=_flow_summary_fallback_prompt(flow.module_name, step.handler),
                        verification_prompt=_flow_summary_verification_prompt(flow.module_name, step.handler),
                        acceptance_checks=[
                            "The answer stays at behavior-summary level.",
                            "Missing evidence is clearly separated from inference.",
                        ],
                        notes=step.notes,
                    )
                )
    return triage


def render_prompt_pack_markdown(prompt_pack: PromptPackArtifact) -> str:
    return f"""# Prompt Pack: {prompt_pack.name}

## Objective

- Category: {prompt_pack.category}
- Goal: {prompt_pack.goal}
- Target model: {prompt_pack.target_model}
- Subject: {prompt_pack.subject_name or "None"}
- Estimated context tokens: {prompt_pack.estimated_tokens}
- Context budget: {prompt_pack.context_budget_tokens}
- Objective: {prompt_pack.objective}
- Issue summary: {prompt_pack.issue_summary or "None"}
- Repro bundle: {prompt_pack.repro_bundle_path or "Pending"}

## Context Paths

{_bullet_lines(prompt_pack.context_paths)}

## Primary Prompt

```text
{prompt_pack.prompt or ""}
```

## Fallback Prompt

```text
{prompt_pack.fallback_prompt or ""}
```

## Verification Prompt

```text
{prompt_pack.verification_prompt or ""}
```

## Acceptance Checks

{_bullet_lines(prompt_pack.acceptance_checks)}

## Expected Response Schema

```json
{_json_schema_text(prompt_pack.expected_response_schema)}
```

## Notes

{_bullet_lines(prompt_pack.notes)}
"""


def render_failure_triage_markdown(triage: FailureTriageArtifact) -> str:
    return f"""# Failure Triage: {triage.name}

## Issue

- Code: {triage.issue_code}
- Severity: {triage.severity}
- Goal: {triage.goal}
- Subject: {triage.subject_name or "None"}
- Summary: {triage.summary}
- Likely root cause: {triage.likely_root_cause}
- Context budget: {triage.context_budget_tokens}
- Repro bundle: {triage.repro_bundle_path or "Pending"}

## Context Paths

{_bullet_lines(triage.context_paths)}

## Suggested Prompt

```text
{triage.suggested_prompt or ""}
```

## Fallback Prompt

```text
{triage.fallback_prompt or ""}
```

## Verification Prompt

```text
{triage.verification_prompt or ""}
```

## Acceptance Checks

{_bullet_lines(triage.acceptance_checks)}

## Notes

{_bullet_lines(triage.notes)}
"""


def render_closure_summary(prompt_packs: list[PromptPackArtifact]) -> str:
    lines = ["# Prompt Closure Summary", ""]
    if not prompt_packs:
        lines.append("- No prompt packs were generated.")
        lines.append("")
        return "\n".join(lines)
    grouped: dict[str, list[PromptPackArtifact]] = defaultdict(list)
    for item in prompt_packs:
        grouped[item.goal].append(item)
    for goal, items in sorted(grouped.items()):
        lines.append(f"## {goal}")
        lines.append("")
        for item in sorted(items, key=lambda value: value.name.lower()):
            lines.append(
                f"- {item.name}: {item.issue_summary or item.objective} "
                f"(paths={len(item.context_paths)}, est_tokens={item.estimated_tokens})"
            )
        lines.append("")
    return "\n".join(lines)


def build_unknowns_markdown(output: AnalysisOutput) -> str:
    unknowns = _collect_unknowns(output)
    return "# Unknowns Ledger\n\n" + _bullet_lines(unknowns) + "\n"


def build_repro_bundle_payload(
    *,
    source_kind: str,
    name: str,
    goal: str,
    target_model: str,
    issue_summary: str,
    subject_name: str | None,
    context_paths: list[str],
    context_budget_tokens: int,
    primary_prompt: str | None,
    fallback_prompt: str | None,
    verification_prompt: str | None,
    expected_response_schema: dict | None = None,
    acceptance_checks: list[str] | None = None,
) -> dict:
    return {
        "source_kind": source_kind,
        "name": name,
        "goal": goal,
        "target_model": target_model,
        "issue_summary": issue_summary,
        "subject_name": subject_name,
        "context_paths": context_paths,
        "context_budget_tokens": context_budget_tokens,
        "primary_prompt": primary_prompt,
        "fallback_prompt": fallback_prompt,
        "verification_prompt": verification_prompt,
        "expected_response_schema": expected_response_schema or {},
        "acceptance_checks": acceptance_checks or [],
    }


def _manifest_by_target(
    manifest: list[ArtifactManifestEntry],
) -> dict[str, list[ArtifactManifestEntry]]:
    index: dict[str, list[ArtifactManifestEntry]] = defaultdict(list)
    for entry in manifest:
        for target in entry.recommended_for:
            index[target].append(entry)
    return index


def _query_context_paths(
    query: ResolvedQueryArtifact,
    manifest: list[ArtifactManifestEntry],
    manifest_by_target: dict[str, list[ArtifactManifestEntry]],
    knowledge_paths: list[str],
) -> list[str]:
    context_paths = [
        entry.path
        for entry in manifest_by_target.get(query.name, [])
    ]
    related_module_names = _query_related_module_names(query.name, manifest)
    for module_name in related_module_names:
        context_paths.extend(entry.path for entry in manifest_by_target.get(module_name, []))
    return list(dict.fromkeys(context_paths + knowledge_paths[:2]))


def _sum_tokens(paths: list[str], path_to_tokens: dict[str, int]) -> int:
    return sum(path_to_tokens.get(path, 0) for path in paths)


def _context_profile(target_model: str) -> dict[str, int]:
    lowered = target_model.lower()
    if any(token in lowered for token in WEAK_MODEL_HINTS):
        return WEAK_CONTEXT_PROFILE
    return DEFAULT_CONTEXT_PROFILE


def _select_context_paths(
    paths: list[str],
    path_to_tokens: dict[str, int],
    *,
    max_tokens: int,
    max_paths: int,
) -> list[str]:
    selected: list[str] = []
    total = 0
    for path in paths:
        if path in selected:
            continue
        tokens = path_to_tokens.get(path, 0)
        if selected and (len(selected) >= max_paths or total + tokens > max_tokens):
            continue
        selected.append(path)
        total += tokens
        if len(selected) >= max_paths or total >= max_tokens:
            break
    return selected


def _query_related_module_names(
    query_name: str,
    manifest: list[ArtifactManifestEntry],
) -> list[str]:
    for entry in manifest:
        if entry.kind == "query-artifact" and query_name in entry.recommended_for:
            return [item for item in entry.recommended_for if item != query_name]
    return []


def _diagnostic_context_paths(
    diagnostic: DiagnosticRecord,
    manifest: list[ArtifactManifestEntry],
) -> list[str]:
    location_path = diagnostic.location.file_path if diagnostic.location else None
    matches = []
    for entry in manifest:
        if location_path and entry.path == location_path:
            matches.append(entry.path)
            continue
        if "diagnostics" in entry.tags:
            matches.append(entry.path)
    return list(dict.fromkeys(matches))


def _collect_unknowns(output: AnalysisOutput) -> list[str]:
    unknowns = []
    for query in output.resolved_queries:
        if query.unresolved_placeholders:
            unknowns.append(
                f"Query {query.name} unresolved placeholders: {', '.join(query.unresolved_placeholders)}"
            )
    for diagnostic in output.diagnostics:
        if diagnostic.severity in {"error", "fatal"} or diagnostic.code in WORKSPACE_TROUBLE_CODES:
            unknowns.append(f"{diagnostic.code}: {diagnostic.message}")
    for flow in output.business_flows:
        for step in flow.steps:
            if any("no implementation body was recovered" in note.lower() for note in step.notes):
                unknowns.append(
                    f"Missing implementation body for handler {step.handler} in module {flow.module_name}"
                )
    deduped = []
    seen = set()
    for item in unknowns:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _module_transition_prompt(module_name: str) -> str:
    return (
        f"You are helping migrate Delphi module {module_name} to React + Spring Boot.\n"
        "Use only the attached artifacts.\n"
        "Do not invent database columns or UI behavior not supported by evidence.\n"
        "Return strict JSON with keys: module_name, user_goal, react_pages, spring_endpoints, unknowns, next_smallest_step, confidence."
    )


def _module_transition_fallback_prompt(module_name: str) -> str:
    return (
        f"For module {module_name}, do not design the whole migration.\n"
        "Only identify the smallest web transition slice, the missing legacy assumptions, and the one next artifact that should be loaded."
    )


def _module_transition_verification_prompt(module_name: str) -> str:
    return (
        f"Verify the proposed transition slice for module {module_name}.\n"
        "Reject any page, endpoint, or assumption that is not directly supported by the attached artifacts.\n"
        "Return strict JSON with keys: supported_items, unsupported_items, remaining_unknowns."
    )


def _query_intent_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"Classify Oracle query {query.name}.\n"
        "Use only the attached query, flow, and knowledge artifacts.\n"
        "Summarize business intent, read/write posture, Oracle-specific behavior, likely UI trigger, and missing evidence.\n"
        "Return strict JSON with keys: query_name, business_intent, read_or_write, oracle_specifics, likely_ui_trigger, missing_evidence."
    )


def _query_intent_fallback_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"For query {query.name}, do not infer domain behavior beyond the SQL text.\n"
        "Return only the most defensible business intent and list every unresolved assumption."
    )


def _query_intent_verification_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"Verify the classified intent for query {query.name}.\n"
        "Check that every intent statement can be justified by the SQL body or linked flow artifact.\n"
        "Return strict JSON with keys: supported_claims, unsupported_claims, confidence."
    )


def _query_clarification_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"Clarify Oracle query {query.name}.\n"
        "Use only the attached query, flow, and knowledge artifacts.\n"
        "Explain business intent, each unresolved placeholder meaning, Oracle-specific behavior, and the minimum missing assumptions.\n"
        "Return strict JSON with keys: query_name, business_intent, placeholder_meanings, oracle_specifics, missing_assumptions, recommended_next_prompt."
    )


def _query_clarification_fallback_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"For query {query.name}, do not guess business rules.\n"
        "Only map each unresolved placeholder to the most likely source and list what evidence is still missing."
    )


def _query_clarification_verification_prompt(query: ResolvedQueryArtifact) -> str:
    return (
        f"Verify the placeholder mapping for query {query.name}.\n"
        "Reject any placeholder meaning that lacks direct evidence from the attached flow or knowledge artifacts.\n"
        "Return strict JSON with keys: supported_placeholders, unsupported_placeholders, missing_evidence."
    )


def _workspace_resolution_prompt(diagnostic: DiagnosticRecord) -> str:
    raw_path = diagnostic.details.get("raw_path", diagnostic.message)
    return (
        f"Resolve Delphi workspace path: {raw_path}\n"
        "Use only the attached artifacts.\n"
        "Return strict JSON with keys: raw_path, resolved_path, path_variables, recommended_workspace_change, confidence."
    )


def _workspace_resolution_fallback_prompt(diagnostic: DiagnosticRecord) -> str:
    raw_path = diagnostic.details.get("raw_path", diagnostic.message)
    return (
        f"For workspace path {raw_path}, do not fix unrelated settings.\n"
        "Return only the one concrete path or variable mapping needed to unblock the analyzer."
    )


def _workspace_resolution_verification_prompt(diagnostic: DiagnosticRecord) -> str:
    raw_path = diagnostic.details.get("raw_path", diagnostic.message)
    return (
        f"Verify the proposed workspace resolution for {raw_path}.\n"
        "Check that the suggested path or variable is specific enough to be added to workspace.json or CLI flags.\n"
        "Return strict JSON with keys: verified_path, verified_variables, remaining_gaps."
    )


def _flow_summary_prompt(module_name: str, handler: str) -> str:
    return (
        f"Summarize likely behavior for Delphi handler {handler} in module {module_name}.\n"
        "Use only the attached artifacts.\n"
        "Return strict JSON with keys: module_name, handler, likely_behavior, likely_queries, missing_evidence."
    )


def _flow_summary_fallback_prompt(module_name: str, handler: str) -> str:
    return (
        f"For handler {handler} in module {module_name}, do not reconstruct code.\n"
        "Return only likely behavior phrases and the missing evidence needed to confirm them."
    )


def _flow_summary_verification_prompt(module_name: str, handler: str) -> str:
    return (
        f"Verify the behavior summary for handler {handler} in module {module_name}.\n"
        "Check that each claimed behavior is supported by the form structure or linked queries.\n"
        "Return strict JSON with keys: supported_behaviors, unsupported_behaviors, missing_evidence."
    )


def _transition_spec_validation_prompt(module_name: str) -> str:
    return (
        f"Validate the generated React + Spring Boot transition spec for module {module_name}.\n"
        "Use only the attached module dossier, transition spec, business flow artifact, query artifacts, and diagnostics.\n"
        "Return strict JSON with keys: module_name, supported_pages, supported_endpoints, unsupported_items, remaining_unknowns, revised_first_slice."
    )


def _transition_spec_validation_fallback_prompt(module_name: str) -> str:
    return (
        f"For module {module_name}, do not redesign the transition spec.\n"
        "Only list which pages and endpoints are clearly supported, which are not, and the one smallest first slice that is safest to implement next."
    )


def _transition_spec_validation_verification_prompt(module_name: str) -> str:
    return (
        f"Verify the spec validation result for module {module_name}.\n"
        "Check that unsupported items are tied to a concrete evidence gap and that the revised first slice is still bounded.\n"
        "Return strict JSON with keys: verified_supported_items, verified_unsupported_items, verified_first_slice."
    )


def _unknowns_prompt(unknowns: list[str]) -> str:
    joined = "\n".join(f"- {item}" for item in unknowns[:12])
    return (
        "You are assisting a legacy migration with a limited-context model.\n"
        "Prioritize only the highest-impact unknowns below.\n"
        "Do not solve everything at once. Return strict JSON with keys: highest_impact_unknowns, recommended_resolution_order, questions_for_humans, questions_for_llm.\n"
        f"Unknowns:\n{joined}"
    )


def _unknowns_fallback_prompt(unknowns: list[str]) -> str:
    joined = "\n".join(f"- {item}" for item in unknowns[:8])
    return (
        "Reduce the unknown list to at most three items that block progress the most.\n"
        "For each, say whether it should be solved by a human or by another LLM prompt.\n"
        f"Unknowns:\n{joined}"
    )


def _unknowns_verification_prompt() -> str:
    return (
        "Verify the unknown prioritization.\n"
        "Check that each chosen unknown actually blocks migration progress and that the order is justified.\n"
        "Return strict JSON with keys: supported_priorities, unsupported_priorities, revised_order."
    )


def _diagnostic_repair_prompt(code: str, message: str) -> str:
    return (
        f"Diagnostic {code} occurred: {message}\n"
        "Using only the attached artifacts, explain the root cause and propose the smallest parser rule, override entry, or artifact change needed.\n"
        "Return strict JSON with keys: root_cause, smallest_change, override_example, confidence."
    )


def _diagnostic_repair_verification_prompt(code: str) -> str:
    return (
        f"Verify the proposed fix for diagnostic {code}.\n"
        "Check that the fix is minimal, local to the issue, and does not depend on unrelated architecture changes.\n"
        "Return strict JSON with keys: supported_fix, unsupported_fix, follow_up_needed."
    )


def _workspace_prompt_name(diagnostic: DiagnosticRecord) -> str:
    code = diagnostic.code.title().replace("_", "")
    raw_path = diagnostic.details.get("raw_path")
    if not isinstance(raw_path, str) or not raw_path:
        return code
    compact = "".join(ch for ch in raw_path.title() if ch.isalnum())
    return f"{code}{compact[:24]}"


def _json_schema_text(schema: dict) -> str:
    import json

    return json.dumps(schema, indent=2)


def _prioritized_support_paths(manifest: list[ArtifactManifestEntry]) -> list[str]:
    priority = []
    fallback = []
    for entry in manifest:
        if "knowledge" in entry.tags and "insights" in entry.tags:
            priority.append(entry.path)
        elif "knowledge" in entry.tags and "feedback-insights" in entry.tags:
            priority.append(entry.path)
        elif "knowledge" in entry.tags and "accepted-rules" in entry.tags:
            priority.append(entry.path)
        elif "knowledge" in entry.tags and "overrides" in entry.tags:
            priority.append(entry.path)
        elif "diagnostics" in entry.tags or "knowledge" in entry.tags:
            fallback.append(entry.path)
    return list(dict.fromkeys(priority + fallback))


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {item}" for item in values)
