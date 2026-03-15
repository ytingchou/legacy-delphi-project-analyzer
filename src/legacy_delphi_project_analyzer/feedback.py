from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.knowledge import ALLOWED_RULE_KEYS
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


def ingest_feedback(analysis_dir: Path, feedback_path: Path) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    if not analysis_dir.exists():
        raise ValueError(f"Analysis directory does not exist: {analysis_dir}")
    if not feedback_path.exists():
        raise ValueError(f"Feedback file does not exist: {feedback_path}")
    knowledge_dir = analysis_dir / "knowledge"
    ensure_directory(knowledge_dir)

    feedback_entries = _load_feedback_entries(feedback_path)
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
            "recorded_at": datetime.now(UTC).isoformat(),
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
    elif goal == "summarize_form_behavior":
        behavior = response.get("likely_behavior")
        if isinstance(behavior, str) and behavior and isinstance(subject, str) and subject:
            rules["transition_hints"][subject] = behavior
    return rules


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
