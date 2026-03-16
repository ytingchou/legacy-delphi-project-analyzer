from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.knowledge import ALLOWED_RULE_KEYS
from legacy_delphi_project_analyzer.models import PromptEffectivenessItem, PromptEffectivenessReport
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def ingest_feedback(analysis_dir: Path, feedback_path: Path) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    if not analysis_dir.exists():
        raise ValueError(f"Analysis directory does not exist: {analysis_dir}")
    if not feedback_path.exists():
        raise ValueError(f"Feedback file does not exist: {feedback_path}")
    feedback_entries = _load_feedback_entries(feedback_path)
    return ingest_feedback_entries(analysis_dir, feedback_entries)


def ingest_feedback_entries(
    analysis_dir: Path,
    feedback_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    if not analysis_dir.exists():
        raise ValueError(f"Analysis directory does not exist: {analysis_dir}")
    knowledge_dir = analysis_dir / "knowledge"
    ensure_directory(knowledge_dir)
    prompt_index = _load_prompt_index(analysis_dir)
    accepted_rules = _load_json(knowledge_dir / "accepted_rules.json", default=_default_rules())
    feedback_log = _load_json(knowledge_dir / "feedback-log.json", default=[])
    rejected_log = _load_json(knowledge_dir / "rejected_rules.json", default={"entries": []})
    if not isinstance(feedback_log, list):
        feedback_log = []
    if not isinstance(rejected_log, dict) or not isinstance(rejected_log.get("entries"), list):
        rejected_log = {"entries": []}

    accepted_count = 0
    rejected_count = 0
    follow_up_count = 0
    fallback_count = 0

    for entry in feedback_entries:
        prompt_name = entry["prompt_name"]
        prompt_meta = prompt_index.get(prompt_name, {})
        status = _normalize_status(entry.get("status", "needs_follow_up"))
        used_fallback = bool(entry.get("used_fallback", False))
        if used_fallback:
            fallback_count += 1
        response = entry.get("response", {})
        explicit_rules = _sanitize_rule_payload(entry.get("learned_rules", {}))
        inferred_rules = _infer_rules(prompt_meta, response)
        merged_rules = _default_rules()
        _merge_rules(merged_rules, inferred_rules)
        _merge_rules(merged_rules, explicit_rules)

        normalized_entry = {
            "prompt_name": prompt_name,
            "goal": prompt_meta.get("goal") or entry.get("goal"),
            "subject_name": prompt_meta.get("subject_name") or entry.get("subject_name"),
            "target_model": prompt_meta.get("target_model") or entry.get("target_model"),
            "status": status,
            "used_fallback": used_fallback,
            "notes": entry.get("notes"),
            "response": response,
            "learned_rules": merged_rules,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        feedback_log.append(normalized_entry)

        if status == "accepted":
            _merge_rules(accepted_rules, merged_rules)
            accepted_count += 1
        elif status == "rejected":
            rejected_log["entries"].append(normalized_entry)
            rejected_count += 1
        else:
            follow_up_count += 1

    write_json(knowledge_dir / "accepted_rules.json", accepted_rules)
    write_json(knowledge_dir / "feedback-log.json", feedback_log)
    write_json(knowledge_dir / "rejected_rules.json", rejected_log)
    prompt_effectiveness = build_prompt_effectiveness_report(prompt_index.values(), feedback_log)
    write_json(knowledge_dir / "prompt-effectiveness.json", prompt_effectiveness)
    write_text(
        knowledge_dir / "feedback-insights.md",
        _build_feedback_insights(
            feedback_log=feedback_log,
            accepted_rules=accepted_rules,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            follow_up_count=follow_up_count,
            fallback_count=fallback_count,
        ),
    )
    write_text(
        knowledge_dir / "prompt-effectiveness.md",
        render_prompt_effectiveness_markdown(prompt_effectiveness),
    )
    return {
        "analysis_dir": analysis_dir.as_posix(),
        "feedback_entries": len(feedback_entries),
        "accepted": accepted_count,
        "rejected": rejected_count,
        "needs_follow_up": follow_up_count,
        "fallback_uses": fallback_count,
    }


def _load_feedback_entries(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("Feedback file must contain a JSON array or an object with an 'entries' array.")
    normalized = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        prompt_name = item.get("prompt_name")
        if not isinstance(prompt_name, str) or not prompt_name:
            continue
        normalized.append(item)
    return normalized


def _load_prompt_index(analysis_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for directory in (analysis_dir / "prompt-pack", analysis_dir / "failure-cases"):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            name = payload.get("name")
            if isinstance(name, str):
                index[name] = payload
    return index


def _normalize_status(status: str) -> str:
    lowered = status.strip().lower()
    if lowered in {"accepted", "ok", "resolved"}:
        return "accepted"
    if lowered in {"rejected", "wrong"}:
        return "rejected"
    return "needs_follow_up"


def _infer_rules(prompt_meta: dict[str, Any], response: Any) -> dict[str, Any]:
    rules = _default_rules()
    if not isinstance(response, dict):
        return rules

    goal = prompt_meta.get("goal")
    subject = prompt_meta.get("subject_name")
    if goal == "resolve_search_path":
        path_variables = response.get("path_variables")
        if isinstance(path_variables, dict):
            rules["path_variables"] = {
                key: value
                for key, value in path_variables.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        resolved_path = response.get("resolved_path")
        if isinstance(resolved_path, str) and resolved_path:
            rules["search_paths"] = [resolved_path]
    elif goal == "infer_placeholder_meaning":
        placeholder_meanings = response.get("placeholder_meanings")
        if isinstance(placeholder_meanings, dict) and subject:
            pairs = [
                f"{key}={value}"
                for key, value in placeholder_meanings.items()
                if isinstance(key, str) and isinstance(value, str)
            ]
            if pairs:
                rules["placeholder_notes"][subject] = "; ".join(pairs)
        business_intent = response.get("business_intent")
        if isinstance(business_intent, str) and business_intent and subject:
            rules["query_hints"][subject] = business_intent
    elif goal == "classify_query_intent":
        business_intent = response.get("business_intent")
        if isinstance(business_intent, str) and business_intent and subject:
            rules["query_hints"][subject] = business_intent
    elif goal == "propose_smallest_transition_slice":
        module_name = response.get("module_name") if isinstance(response.get("module_name"), str) else subject
        next_step = response.get("next_smallest_step")
        if isinstance(module_name, str) and module_name and isinstance(next_step, str) and next_step:
            rules["transition_hints"][module_name] = next_step
    elif goal == "validate_transition_spec":
        module_name = response.get("module_name") if isinstance(response.get("module_name"), str) else subject
        revised_slice = response.get("revised_first_slice")
        if isinstance(module_name, str) and module_name and isinstance(revised_slice, str) and revised_slice:
            rules["transition_hints"][module_name] = revised_slice
    elif goal == "summarize_form_behavior":
        behavior = response.get("likely_behavior")
        if isinstance(behavior, str) and behavior and isinstance(subject, str) and subject:
            rules["transition_hints"][subject] = behavior
    elif goal == "generate_bff_oracle_sql_logic":
        module_name = response.get("module_name") if isinstance(response.get("module_name"), str) else None
        controller_contract = response.get("controller_contract")
        repository_method = response.get("repository_method")
        if isinstance(module_name, str) and module_name and isinstance(controller_contract, str) and controller_contract:
            hint = controller_contract
            if isinstance(repository_method, str) and repository_method:
                hint += f" | Repository: {repository_method}"
            rules["transition_hints"][module_name] = hint
    elif goal == "generate_react_pseudo_ui":
        module_name = response.get("module_name") if isinstance(response.get("module_name"), str) else None
        page_name = response.get("page_name")
        components = response.get("components")
        if isinstance(module_name, str) and module_name and isinstance(page_name, str) and page_name:
            component_list = [item for item in components if isinstance(item, str)] if isinstance(components, list) else []
            if component_list:
                rules["transition_hints"][module_name] = (
                    f"Pseudo UI confirmed for {page_name}: {', '.join(component_list[:3])}"
                )
    elif goal == "integrate_react_transition_ui":
        module_name = response.get("module_name") if isinstance(response.get("module_name"), str) else None
        page_name = response.get("page_name")
        feature_dir = response.get("target_feature_dir")
        if (
            isinstance(module_name, str)
            and module_name
            and isinstance(page_name, str)
            and page_name
            and isinstance(feature_dir, str)
            and feature_dir
        ):
            rules["transition_hints"][module_name] = f"Integrate {page_name} under {feature_dir}"
    return rules


def build_prompt_effectiveness_report(
    prompt_sources: Any,
    feedback_log: list[dict[str, Any]],
) -> PromptEffectivenessReport:
    metadata = _build_prompt_metadata(prompt_sources)
    items_by_name: dict[str, PromptEffectivenessItem] = {}

    for name, item in metadata.items():
        items_by_name[name] = PromptEffectivenessItem(
            prompt_name=name,
            goal=item.get("goal") or "unknown",
            subject_name=item.get("subject_name"),
            target_model=item.get("target_model"),
            notes=["No feedback recorded yet."],
        )

    for entry in feedback_log:
        if not isinstance(entry, dict):
            continue
        prompt_name = entry.get("prompt_name")
        if not isinstance(prompt_name, str) or not prompt_name:
            continue
        item = items_by_name.get(prompt_name)
        if item is None:
            item = PromptEffectivenessItem(
                prompt_name=prompt_name,
                goal=str(entry.get("goal") or "unknown"),
                subject_name=entry.get("subject_name") if isinstance(entry.get("subject_name"), str) else None,
                target_model=entry.get("target_model") if isinstance(entry.get("target_model"), str) else None,
            )
            items_by_name[prompt_name] = item
        item.attempts += 1
        status = str(entry.get("status") or "needs_follow_up")
        if status == "accepted":
            item.accepted += 1
        elif status == "rejected":
            item.rejected += 1
        else:
            item.needs_follow_up += 1
        if entry.get("used_fallback"):
            item.fallback_uses += 1
        item.success_rate = round(item.accepted / item.attempts, 3) if item.attempts else 0.0
        item.notes = []

    prompt_items = list(items_by_name.values())
    goal_summary: dict[str, dict[str, int | float]] = {}
    for item in prompt_items:
        goal_metrics = goal_summary.setdefault(
            item.goal,
            {
                "attempts": 0,
                "accepted": 0,
                "rejected": 0,
                "needs_follow_up": 0,
                "fallback_uses": 0,
                "success_rate": 0.0,
            },
        )
        goal_metrics["attempts"] += item.attempts
        goal_metrics["accepted"] += item.accepted
        goal_metrics["rejected"] += item.rejected
        goal_metrics["needs_follow_up"] += item.needs_follow_up
        goal_metrics["fallback_uses"] += item.fallback_uses
    for goal, metrics in goal_summary.items():
        attempts = int(metrics["attempts"])
        metrics["success_rate"] = round(int(metrics["accepted"]) / attempts, 3) if attempts else 0.0

    attempted_items = [item for item in prompt_items if item.attempts]
    top_successful = sorted(
        attempted_items,
        key=lambda item: (item.success_rate, item.accepted, -item.rejected, item.prompt_name.lower()),
        reverse=True,
    )[:5]
    top_failing = sorted(
        attempted_items,
        key=lambda item: (-item.rejected, -item.needs_follow_up, item.success_rate, item.prompt_name.lower()),
    )[:5]
    management_summary = _build_management_summary(prompt_items, goal_summary, feedback_log)
    accepted_entries = sum(1 for item in feedback_log if item.get("status") == "accepted")
    rejected_entries = sum(1 for item in feedback_log if item.get("status") == "rejected")
    follow_up_entries = sum(1 for item in feedback_log if item.get("status") == "needs_follow_up")
    fallback_entries = sum(1 for item in feedback_log if item.get("used_fallback"))
    return PromptEffectivenessReport(
        total_feedback_entries=len(feedback_log),
        accepted_entries=accepted_entries,
        rejected_entries=rejected_entries,
        follow_up_entries=follow_up_entries,
        fallback_entries=fallback_entries,
        top_successful_prompts=top_successful,
        top_failing_prompts=top_failing,
        goal_summary=goal_summary,
        management_summary=management_summary,
    )


def render_prompt_effectiveness_markdown(report: PromptEffectivenessReport) -> str:
    lines = [
        "# Prompt Effectiveness",
        "",
        "## Summary",
        "",
        f"- Total feedback entries: {report.total_feedback_entries}",
        f"- Accepted: {report.accepted_entries}",
        f"- Rejected: {report.rejected_entries}",
        f"- Needs follow-up: {report.follow_up_entries}",
        f"- Fallback uses: {report.fallback_entries}",
        "",
        "## Management Summary",
        "",
    ]
    if report.management_summary:
        lines.extend(f"- {item}" for item in report.management_summary)
    else:
        lines.append("- No prompt feedback has been recorded yet.")
    lines.extend(["", "## Top Successful Prompts", ""])
    if report.top_successful_prompts:
        lines.extend(
            f"- {item.prompt_name}: success_rate={item.success_rate:.3f}, accepted={item.accepted}, attempts={item.attempts}"
            for item in report.top_successful_prompts
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Top Failing Prompts", ""])
    if report.top_failing_prompts:
        lines.extend(
            f"- {item.prompt_name}: rejected={item.rejected}, follow_up={item.needs_follow_up}, success_rate={item.success_rate:.3f}"
            for item in report.top_failing_prompts
        )
    else:
        lines.append("- None")
    lines.extend(["", "## Goal Summary", ""])
    if report.goal_summary:
        for goal, metrics in sorted(report.goal_summary.items()):
            lines.append(
                f"- {goal}: attempts={metrics['attempts']}, accepted={metrics['accepted']}, "
                f"rejected={metrics['rejected']}, follow_up={metrics['needs_follow_up']}, "
                f"fallback={metrics['fallback_uses']}, success_rate={metrics['success_rate']}"
            )
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _build_feedback_insights(
    *,
    feedback_log: list[dict[str, Any]],
    accepted_rules: dict[str, Any],
    accepted_count: int,
    rejected_count: int,
    follow_up_count: int,
    fallback_count: int,
) -> str:
    lines = [
        "# Feedback Insights",
        "",
        "## Summary",
        "",
        f"- Feedback entries imported this run: {accepted_count + rejected_count + follow_up_count}",
        f"- Accepted: {accepted_count}",
        f"- Rejected: {rejected_count}",
        f"- Needs follow-up: {follow_up_count}",
        f"- Fallback used: {fallback_count}",
        "",
        "## Learned Rule Counts",
        "",
    ]
    for key in ("path_variables", "search_paths", "xml_aliases", "placeholder_notes", "query_hints", "transition_hints"):
        value = accepted_rules.get(key, {})
        count = len(value) if isinstance(value, dict) else len(value) if isinstance(value, list) else 0
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Recent Feedback Entries", ""])
    if feedback_log:
        for entry in feedback_log[-10:]:
            lines.append(
                f"- {entry.get('prompt_name')}: {entry.get('status')} "
                f"(goal={entry.get('goal') or 'unknown'}, fallback={entry.get('used_fallback')})"
            )
    else:
        lines.append("- No feedback entries have been recorded.")
    lines.append("")
    return "\n".join(lines)


def _build_prompt_metadata(prompt_sources: Any) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    if isinstance(prompt_sources, dict):
        iterable = prompt_sources.values()
    else:
        iterable = prompt_sources
    for item in iterable:
        if isinstance(item, dict):
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            metadata[name] = {
                "goal": item.get("goal"),
                "subject_name": item.get("subject_name"),
                "target_model": item.get("target_model"),
            }
            continue
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name:
            continue
        metadata[name] = {
            "goal": getattr(item, "goal", None),
            "subject_name": getattr(item, "subject_name", None),
            "target_model": getattr(item, "target_model", None),
        }
    return metadata


def _build_management_summary(
    prompt_items: list[PromptEffectivenessItem],
    goal_summary: dict[str, dict[str, int | float]],
    feedback_log: list[dict[str, Any]],
) -> list[str]:
    summary = []
    if not feedback_log:
        return ["No feedback has been ingested yet, so prompt effectiveness is still unmeasured."]
    accepted = sum(1 for item in feedback_log if item.get("status") == "accepted")
    summary.append(f"Accepted prompt outcomes currently total {accepted} out of {len(feedback_log)} recorded attempts.")
    ranked_goals = [
        (goal, metrics)
        for goal, metrics in goal_summary.items()
        if int(metrics["attempts"]) > 0
    ]
    if ranked_goals:
        best_goal, best_metrics = max(ranked_goals, key=lambda pair: float(pair[1]["success_rate"]))
        worst_goal, worst_metrics = min(ranked_goals, key=lambda pair: float(pair[1]["success_rate"]))
        summary.append(
            f"Best-performing prompt goal is {best_goal} with success rate {float(best_metrics['success_rate']):.3f}."
        )
        summary.append(
            f"Lowest-performing prompt goal is {worst_goal} with success rate {float(worst_metrics['success_rate']):.3f}."
        )
    untested = sum(1 for item in prompt_items if item.attempts == 0)
    if untested:
        summary.append(f"{untested} prompt pack(s) still have no recorded feedback and remain unverified.")
    fallback_uses = sum(1 for item in feedback_log if item.get("used_fallback"))
    if fallback_uses:
        summary.append(f"Fallback prompts were needed {fallback_uses} time(s), which indicates prompts that should be tightened.")
    return summary


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _default_rules() -> dict[str, Any]:
    return {
        "ignore_globs": [],
        "module_overrides": {},
        "xml_aliases": {},
        "placeholder_notes": {},
        "query_hints": {},
        "path_variables": {},
        "search_paths": [],
        "transition_hints": {},
    }


def _sanitize_rule_payload(payload: Any) -> dict[str, Any]:
    sanitized = _default_rules()
    if not isinstance(payload, dict):
        return sanitized
    for key, expected in ALLOWED_RULE_KEYS.items():
        value = payload.get(key)
        if expected is dict and isinstance(value, dict):
            sanitized[key] = {
                item_key: item_value
                for item_key, item_value in value.items()
                if isinstance(item_key, str) and isinstance(item_value, str)
            }
        elif expected is list and isinstance(value, list):
            sanitized[key] = [item for item in value if isinstance(item, str)]
    return sanitized


def _merge_rules(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, expected in ALLOWED_RULE_KEYS.items():
        value = incoming.get(key)
        if expected is dict and isinstance(value, dict):
            current = base.setdefault(key, {})
            if isinstance(current, dict):
                current.update(value)
        elif expected is list and isinstance(value, list):
            current = base.setdefault(key, [])
            if isinstance(current, list):
                current.extend(item for item in value if item not in current)
