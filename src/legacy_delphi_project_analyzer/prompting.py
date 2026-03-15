from __future__ import annotations

from collections import defaultdict

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ArtifactManifestEntry,
    FailureTriageArtifact,
    LoadBundleArtifact,
    PromptPackArtifact,
    ResolvedQueryArtifact,
)


def build_prompt_packs(
    output: AnalysisOutput,
    manifest: list[ArtifactManifestEntry],
    load_bundles: list[LoadBundleArtifact],
    target_model: str,
) -> list[PromptPackArtifact]:
    manifest_by_target = _manifest_by_target(manifest)
    path_to_tokens = {entry.path: entry.estimated_tokens for entry in manifest}
    knowledge_paths = _prioritized_support_paths(manifest)
    packs: list[PromptPackArtifact] = []

    for bundle in load_bundles:
        if bundle.category != "module":
            continue
        context_paths = list(dict.fromkeys(bundle.artifact_paths + knowledge_paths[:2]))
        packs.append(
            PromptPackArtifact(
                name=f"{bundle.name}Transition",
                category="module-transition",
                target_model=target_model,
                objective=f"Extract the next React + Spring Boot migration slice for module {bundle.name}.",
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                prompt=_module_transition_prompt(bundle.name),
                fallback_prompt=_module_transition_fallback_prompt(bundle.name),
                expected_response_schema={
                    "module_name": "string",
                    "user_goal": "string",
                    "react_pages": ["string"],
                    "spring_endpoints": ["string"],
                    "unknowns": ["string"],
                    "next_smallest_step": "string",
                    "confidence": "low|medium|high",
                },
                notes=bundle.notes,
            )
        )

    for query in output.resolved_queries:
        if not (query.unresolved_placeholders or query.warnings):
            continue
        context_paths = _query_context_paths(query, manifest, manifest_by_target, knowledge_paths)
        packs.append(
            PromptPackArtifact(
                name=f"{query.name}Clarify",
                category="query-clarification",
                target_model=target_model,
                objective=f"Clarify the intent and runtime placeholder meaning for query {query.name}.",
                context_paths=context_paths,
                estimated_tokens=_sum_tokens(context_paths, path_to_tokens),
                prompt=_query_clarification_prompt(query),
                fallback_prompt=_query_clarification_fallback_prompt(query),
                expected_response_schema={
                    "query_name": "string",
                    "business_intent": "string",
                    "placeholder_meanings": {"placeholder": "meaning"},
                    "oracle_specifics": ["string"],
                    "missing_assumptions": ["string"],
                    "recommended_next_prompt": "string",
                },
                notes=query.warnings,
            )
        )

    unknowns = _collect_unknowns(output)
    if unknowns:
        unknown_context = list(dict.fromkeys(knowledge_paths[:2] + [
            entry.path for entry in manifest if "project-overview" in entry.recommended_for
        ]))
        packs.append(
            PromptPackArtifact(
                name="UnknownsLedger",
                category="unknown-resolution",
                target_model=target_model,
                objective="Resolve only the highest-impact unknowns blocking migration progress.",
                context_paths=unknown_context,
                estimated_tokens=_sum_tokens(unknown_context, path_to_tokens),
                prompt=_unknowns_prompt(unknowns),
                fallback_prompt=_unknowns_fallback_prompt(unknowns),
                expected_response_schema={
                    "highest_impact_unknowns": ["string"],
                    "recommended_resolution_order": ["string"],
                    "questions_for_humans": ["string"],
                    "questions_for_llm": ["string"],
                },
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
    triage: list[FailureTriageArtifact] = []

    for diagnostic in output.diagnostics:
        if diagnostic.severity not in {"error", "fatal"}:
            continue
        context_paths = list(dict.fromkeys(knowledge_paths))
        issue_name = diagnostic.code.lower().replace("_", "-")
        triage.append(
            FailureTriageArtifact(
                name=issue_name,
                issue_code=diagnostic.code,
                severity=diagnostic.severity,
                summary=diagnostic.message,
                likely_root_cause=diagnostic.suggestion or "Unsupported legacy structure needs a new rule or override.",
                context_paths=context_paths,
                suggested_prompt=_diagnostic_repair_prompt(diagnostic.code, diagnostic.message),
                fallback_prompt=(
                    "Summarize only the minimum parser change or override entry needed. "
                    "Do not redesign unrelated parts."
                ),
                notes=[diagnostic.prompt_hint] if diagnostic.prompt_hint else [],
            )
        )

    for query in output.resolved_queries:
        if not query.unresolved_placeholders:
            continue
        context_paths = _query_context_paths(query, manifest, manifest_by_target, knowledge_paths)
        triage.append(
            FailureTriageArtifact(
                name=f"{query.name.lower()}-unresolved-placeholders",
                issue_code="QUERY_UNRESOLVED_PLACEHOLDERS",
                severity="warning",
                summary=f"Query {query.name} still depends on runtime placeholders: {', '.join(query.unresolved_placeholders)}",
                likely_root_cause="Delphi-side string replacement or UI parameter mapping is not yet documented.",
                context_paths=context_paths,
                suggested_prompt=_query_clarification_prompt(query),
                fallback_prompt=_query_clarification_fallback_prompt(query),
                notes=["Use the business flow artifact before asking the model to infer placeholder semantics."],
            )
        )

    for flow in output.business_flows:
        for step in flow.steps:
            if any("no implementation body was recovered" in note.lower() for note in step.notes):
                context_paths = [
                    entry.path
                    for entry in manifest
                    if flow.module_name in entry.recommended_for or "diagnostics" in entry.tags
                ]
                triage.append(
                    FailureTriageArtifact(
                        name=f"{flow.module_name.lower()}-{step.handler.lower()}-missing-body",
                        issue_code="FLOW_HANDLER_BODY_MISSING",
                        severity="warning",
                        summary=f"Handler {step.handler} is referenced by the form but its implementation body was not recovered.",
                        likely_root_cause="The Pascal unit may be incomplete, split across files, or use unsupported syntax.",
                        context_paths=list(dict.fromkeys(context_paths)),
                        suggested_prompt=(
                            f"Using only the attached artifacts, infer what handler {step.handler} most likely does "
                            "and list the missing evidence needed to confirm it."
                        ),
                        fallback_prompt=(
                            f"Do not guess the full implementation of {step.handler}. Return only missing evidence and likely affected queries."
                        ),
                        notes=step.notes,
                    )
                )
    return triage


def render_prompt_pack_markdown(prompt_pack: PromptPackArtifact) -> str:
    return f"""# Prompt Pack: {prompt_pack.name}

## Objective

- Category: {prompt_pack.category}
- Target model: {prompt_pack.target_model}
- Estimated context tokens: {prompt_pack.estimated_tokens}
- Objective: {prompt_pack.objective}

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
- Summary: {triage.summary}
- Likely root cause: {triage.likely_root_cause}

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

## Notes

{_bullet_lines(triage.notes)}
"""


def build_unknowns_markdown(output: AnalysisOutput) -> str:
    unknowns = _collect_unknowns(output)
    return "# Unknowns Ledger\n\n" + _bullet_lines(unknowns) + "\n"


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


def _query_related_module_names(
    query_name: str,
    manifest: list[ArtifactManifestEntry],
) -> list[str]:
    for entry in manifest:
        if entry.kind == "query-artifact" and query_name in entry.recommended_for:
            return [item for item in entry.recommended_for if item != query_name]
    return []


def _collect_unknowns(output: AnalysisOutput) -> list[str]:
    unknowns = []
    for query in output.resolved_queries:
        if query.unresolved_placeholders:
            unknowns.append(
                f"Query {query.name} unresolved placeholders: {', '.join(query.unresolved_placeholders)}"
            )
    for diagnostic in output.diagnostics:
        if diagnostic.severity in {"error", "fatal"}:
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


def _diagnostic_repair_prompt(code: str, message: str) -> str:
    return (
        f"Diagnostic {code} occurred: {message}\n"
        "Using only the attached artifacts, explain the root cause and propose the smallest parser rule, override entry, or artifact change needed.\n"
        "Return strict JSON with keys: root_cause, smallest_change, override_example, confidence."
    )


def _json_schema_text(schema: dict) -> str:
    import json

    return json.dumps(schema, indent=2)


def _prioritized_support_paths(manifest: list[ArtifactManifestEntry]) -> list[str]:
    priority = []
    fallback = []
    for entry in manifest:
        if "knowledge" in entry.tags and "insights" in entry.tags:
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
