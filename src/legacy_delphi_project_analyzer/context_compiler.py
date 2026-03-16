from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.model_profiles import get_model_profile
from legacy_delphi_project_analyzer.utils import ensure_directory, estimate_tokens, read_text_file, trim_sql_snippet, write_json, write_text


@dataclass(slots=True)
class CompiledContext:
    task_id: str
    target_model_profile: str
    estimated_tokens: int
    included_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    trusted_facts: list[str] = field(default_factory=list)
    evidence_snippets: list[str] = field(default_factory=list)
    markdown_path: str | None = None
    json_path: str | None = None
    compiled_payload_path: str | None = None


def compile_task_context(
    *,
    analysis_dir: Path,
    runtime_dir: Path,
    taskpack: Any,
    task_dir: Path,
) -> CompiledContext:
    profile = get_model_profile(taskpack.target_model_profile)
    trusted_facts = _trusted_facts(analysis_dir, taskpack)
    validator_feedback = _last_validator_feedback(task_dir)
    evidence_snippets, included_paths, skipped_paths = _collect_evidence(
        taskpack.context_paths,
        max_paths=profile.max_context_paths,
        max_tokens=profile.max_input_tokens,
    )
    markdown = _render_compiled_markdown(taskpack, trusted_facts, validator_feedback, evidence_snippets)
    payload = {
        "name": taskpack.task_id,
        "goal": taskpack.task_type,
        "subject_name": taskpack.subject_name,
        "target_model": taskpack.target_model_profile,
        "issue_summary": taskpack.issue_summary,
        "context_paths": [],
        "context_budget_tokens": profile.max_input_tokens,
        "prompt": taskpack.primary_prompt,
        "fallback_prompt": taskpack.fallback_prompt,
        "verification_prompt": taskpack.verification_prompt,
        "expected_response_schema": taskpack.expected_output_schema,
        "acceptance_checks": taskpack.acceptance_checks,
        "notes": list(taskpack.notes) + [f"compiled_from={len(included_paths)} file(s)"],
    }

    compiled_md_path = task_dir / "compiled-context.md"
    compiled_json_path = task_dir / "compiled-context.json"
    compiled_payload_path = task_dir / "taskpack-compiled.json"
    write_text(compiled_md_path, markdown)
    write_json(
        compiled_json_path,
        {
            "task_id": taskpack.task_id,
            "target_model_profile": taskpack.target_model_profile,
            "estimated_tokens": estimate_tokens(markdown),
            "included_paths": included_paths,
            "skipped_paths": skipped_paths,
            "trusted_facts": trusted_facts,
            "validator_feedback": validator_feedback,
            "evidence_snippets": evidence_snippets,
        },
    )
    payload["context_paths"] = [compiled_md_path.as_posix()]
    write_json(compiled_payload_path, payload)
    return CompiledContext(
        task_id=taskpack.task_id,
        target_model_profile=taskpack.target_model_profile,
        estimated_tokens=estimate_tokens(markdown),
        included_paths=included_paths,
        skipped_paths=skipped_paths,
        trusted_facts=trusted_facts,
        evidence_snippets=evidence_snippets,
        markdown_path=compiled_md_path.as_posix(),
        json_path=compiled_json_path.as_posix(),
        compiled_payload_path=compiled_payload_path.as_posix(),
    )


def compact_runtime_state(runtime_dir: Path) -> dict[str, Any]:
    state_summary_path = runtime_dir / "state-summary.md"
    phase_delta_path = runtime_dir / "phase-delta.md"
    trusted_facts_path = runtime_dir / "trusted-facts.json"
    current_blockers_path = runtime_dir / "blocking-unknowns.json"

    facts = {
        "state_summary": state_summary_path.read_text(encoding="utf-8") if state_summary_path.exists() else "",
        "phase_delta": phase_delta_path.read_text(encoding="utf-8") if phase_delta_path.exists() else "",
        "blocking_unknowns": json.loads(current_blockers_path.read_text(encoding="utf-8")) if current_blockers_path.exists() else [],
    }
    accepted_rules_path = runtime_dir.parent / "knowledge" / "accepted_rules.json"
    if accepted_rules_path.exists():
        facts["accepted_rules"] = json.loads(accepted_rules_path.read_text(encoding="utf-8"))
    write_json(trusted_facts_path, facts)
    return facts


def _trusted_facts(analysis_dir: Path, taskpack: Any) -> list[str]:
    facts = [
        f"Task type: {taskpack.task_type}",
        f"Phase: {taskpack.phase}",
    ]
    if taskpack.module_name:
        facts.append(f"Module: {taskpack.module_name}")
    if taskpack.subject_name:
        facts.append(f"Subject: {taskpack.subject_name}")
    load_plan_path = analysis_dir / "llm-pack" / "load-plan.json"
    if load_plan_path.exists():
        load_plan = json.loads(load_plan_path.read_text(encoding="utf-8"))
        notes = load_plan.get("notes", [])
        if isinstance(notes, list):
            facts.extend(str(item) for item in notes[:2] if isinstance(item, str))
    accepted_rules_path = analysis_dir / "knowledge" / "accepted_rules.json"
    if accepted_rules_path.exists():
        accepted_rules = json.loads(accepted_rules_path.read_text(encoding="utf-8"))
        transition_hints = accepted_rules.get("transition_hints")
        if taskpack.module_name and isinstance(transition_hints, dict):
            hint = transition_hints.get(taskpack.module_name)
            if isinstance(hint, str) and hint:
                facts.append(f"Learned hint: {hint}")
    return facts[:8]


def _last_validator_feedback(task_dir: Path) -> list[str]:
    retry_plan_path = task_dir / "retry-plan.json"
    if not retry_plan_path.exists():
        return []
    try:
        payload = json.loads(retry_plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    feedback = payload.get("validator_feedback")
    if not isinstance(feedback, list):
        return []
    return [str(item) for item in feedback if isinstance(item, str)][:6]


def _collect_evidence(
    context_paths: list[str],
    *,
    max_paths: int,
    max_tokens: int,
) -> tuple[list[str], list[str], list[str]]:
    snippets: list[str] = []
    included_paths: list[str] = []
    skipped_paths: list[str] = []
    current_tokens = 0
    for raw_path in context_paths:
        if len(included_paths) >= max_paths:
            skipped_paths.append(raw_path)
            continue
        path = Path(raw_path)
        if not path.exists():
            skipped_paths.append(raw_path)
            continue
        content, _, _ = read_text_file(path)
        snippet = _summarize_content(path, content)
        snippet_tokens = estimate_tokens(snippet)
        if included_paths and current_tokens + snippet_tokens > max_tokens:
            skipped_paths.append(raw_path)
            continue
        included_paths.append(raw_path)
        snippets.append(snippet)
        current_tokens += snippet_tokens
    return snippets, included_paths, skipped_paths


def _summarize_content(path: Path, content: str) -> str:
    trimmed_lines = [line.rstrip() for line in content.splitlines()[:40]]
    joined = "\n".join(trimmed_lines)
    if path.suffix.lower() in {".md", ".txt", ".java", ".tsx", ".ts", ".json"}:
        return f"### {path.as_posix()}\n```text\n{joined}\n```\n"
    return f"### {path.as_posix()}\n```text\n{trim_sql_snippet(joined, limit=900)}\n```\n"


def _render_compiled_markdown(
    taskpack: Any,
    trusted_facts: list[str],
    validator_feedback: list[str],
    evidence_snippets: list[str],
) -> str:
    lines = [
        f"# Compiled Context: {taskpack.task_id}",
        "",
        "## Task",
        "",
        f"- Type: {taskpack.task_type}",
        f"- Phase: {taskpack.phase}",
        f"- Module: {taskpack.module_name or 'None'}",
        f"- Subject: {taskpack.subject_name or 'None'}",
        f"- Issue summary: {taskpack.issue_summary}",
        "",
        "## Trusted Facts",
        "",
    ]
    lines.extend(f"- {item}" for item in trusted_facts)
    lines.extend(["", "## Validator Feedback", ""])
    lines.extend(f"- {item}" for item in (validator_feedback or ["No prior validator feedback."]))
    lines.extend(["", "## Evidence Snippets", ""])
    lines.extend(evidence_snippets or ["No evidence snippets available."])
    lines.extend(["", "## Prompt", "", "```text", taskpack.primary_prompt or "", "```", ""])
    return "\n".join(lines)
