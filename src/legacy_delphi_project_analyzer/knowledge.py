from __future__ import annotations

import fnmatch
import json
from collections import Counter
from datetime import UTC, datetime
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import DiagnosticRecord, ResolvedQueryArtifact
from legacy_delphi_project_analyzer.utils import ensure_directory, make_diagnostic, write_json, write_text


ALLOWED_RULE_KEYS = {
    "ignore_globs": list,
    "module_overrides": dict,
    "xml_aliases": dict,
    "placeholder_notes": dict,
    "query_hints": dict,
    "path_variables": dict,
    "search_paths": list,
    "transition_hints": dict,
}
RUNTIME_RULE_FILES = ("overrides.json", "accepted_rules.json")


def load_bootstrap_rules(
    rules_dir: Path | None,
    output_dir: Path,
) -> tuple[dict[str, Any], list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    merged = _default_rules()
    candidate_paths = []
    if rules_dir:
        candidate_paths.extend(rules_dir / file_name for file_name in RUNTIME_RULE_FILES)
    candidate_paths.append(output_dir / "knowledge" / "accepted_rules.json")

    for path in candidate_paths:
        if not path.exists():
            continue
        payload = _read_json(path)
        diagnostics.extend(_validate_rule_payload(payload, path))
        _merge_rules(merged, _sanitize_rule_payload(payload))
    return merged, diagnostics


class KnowledgeStore:
    def __init__(
        self,
        project_root: Path,
        rules_dir: Path | None,
        output_dir: Path,
        scan_roots: list[Path] | None = None,
    ) -> None:
        self.project_root = project_root
        self.scan_roots = [item.resolve() for item in (scan_roots or [project_root])]
        self.rules_dir = rules_dir
        self.output_dir = output_dir
        self.knowledge_dir = output_dir / "knowledge"
        self.diagnostics: list[DiagnosticRecord] = []
        self.overrides = _default_rules()
        self.learned = {
            "ignore_globs": [],
            "diagnostic_counts": {},
            "unresolved_placeholders": {},
            "missing_xml_refs": {},
            "missing_query_refs": {},
        }
        self.feedback_log: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        runtime_rules, runtime_diags = load_bootstrap_rules(self.rules_dir, self.output_dir)
        self.overrides = runtime_rules
        self.diagnostics.extend(runtime_diags)

        learned_path = self.knowledge_dir / "learned_patterns.json"
        if learned_path.exists():
            self.learned.update(_read_json(learned_path))

        feedback_log_path = self.knowledge_dir / "feedback-log.json"
        if feedback_log_path.exists():
            payload = _read_json(feedback_log_path)
            if isinstance(payload, list):
                self.feedback_log = payload
            else:
                self.diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "KNOWLEDGE_FEEDBACK_LOG_INVALID",
                        "feedback-log.json should contain a JSON array.",
                        file_path=feedback_log_path.as_posix(),
                        suggestion="Rewrite feedback-log.json as an array of feedback entries.",
                    )
                )

    def get_diagnostics(self) -> list[DiagnosticRecord]:
        return list(self.diagnostics)

    def should_ignore(self, path: Path) -> bool:
        relative = self._path_key(path)
        patterns = [
            item
            for item in list(self.overrides.get("ignore_globs", []))
            + list(self.learned.get("ignore_globs", []))
            if isinstance(item, str)
        ]
        return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)

    def _path_key(self, path: Path) -> str:
        normalized = path.resolve()
        for root in self.scan_roots:
            try:
                relative = normalized.relative_to(root)
            except ValueError:
                continue
            if root == self.scan_roots[0]:
                return relative.as_posix()
            return f"{root.name}/{relative.as_posix()}"
        return normalized.as_posix()

    def apply_module_override(self, candidate: str) -> str:
        module_overrides = self.overrides.get("module_overrides", {})
        value = module_overrides.get(candidate)
        return value if isinstance(value, str) else candidate

    def get_transition_hint(self, candidate: str) -> str | None:
        hints = self.overrides.get("transition_hints", {})
        value = hints.get(candidate)
        return value if isinstance(value, str) else None

    def get_path_variables(self) -> dict[str, str]:
        value = self.overrides.get("path_variables", {})
        return value if isinstance(value, dict) else {}

    def get_search_paths(self) -> list[str]:
        value = self.overrides.get("search_paths", [])
        return value if isinstance(value, list) else []

    def resolve_xml_alias(self, name: str) -> str:
        aliases = self.get_xml_aliases()
        return aliases.get(name, name)

    def get_xml_aliases(self) -> dict[str, str]:
        aliases = {}
        aliases.update(
            {
                key: value
                for key, value in self.learned.get("xml_aliases", {}).items()
                if isinstance(key, str) and isinstance(value, str)
            }
        )
        aliases.update(
            {
                key: value
                for key, value in self.overrides.get("xml_aliases", {}).items()
                if isinstance(key, str) and isinstance(value, str)
            }
        )
        return aliases

    def get_feedback_log(self) -> list[dict[str, Any]]:
        return list(self.feedback_log)

    def learn(
        self,
        diagnostics: list[DiagnosticRecord],
        resolved_queries: list[ResolvedQueryArtifact],
        available_xml_names: list[str],
    ) -> None:
        diagnostic_counts = Counter(item.code for item in diagnostics)
        unresolved = Counter()
        missing_xml = Counter()
        missing_query = Counter()
        unresolved_queries: dict[str, list[str]] = {}
        for query in resolved_queries:
            unresolved.update(query.unresolved_placeholders)
            if query.unresolved_placeholders:
                unresolved_queries[query.name] = query.unresolved_placeholders
        for item in diagnostics:
            if item.code == "SQL_XML_EXTERNAL_NOT_FOUND":
                missing_xml.update([item.details.get("xml_name", "unknown")])
            if item.code == "SQL_XML_TARGET_NOT_FOUND":
                missing_query.update([item.details.get("target_name", "unknown")])

        self.learned["diagnostic_counts"] = dict(diagnostic_counts)
        self.learned["unresolved_placeholders"] = dict(unresolved)
        self.learned["missing_xml_refs"] = dict(missing_xml)
        self.learned["missing_query_refs"] = dict(missing_query)
        self.learned["updated_at"] = datetime.now(UTC).isoformat()

        suggested_overrides = self._build_suggested_overrides(
            missing_xml_refs=list(missing_xml.keys()),
            unresolved_queries=unresolved_queries,
            available_xml_names=available_xml_names,
        )
        knowledge_insights = self._build_knowledge_insights(
            diagnostics=diagnostics,
            resolved_queries=resolved_queries,
            suggested_overrides=suggested_overrides,
        )

        ensure_directory(self.knowledge_dir)
        write_json(self.knowledge_dir / "learned_patterns.json", self.learned)
        write_json(self.knowledge_dir / "suggested_overrides.json", suggested_overrides)
        write_text(self.knowledge_dir / "knowledge-insights.md", knowledge_insights)

    def _build_suggested_overrides(
        self,
        missing_xml_refs: list[str],
        unresolved_queries: dict[str, list[str]],
        available_xml_names: list[str],
    ) -> dict:
        suggestions = {
            "xml_aliases": {},
            "placeholder_notes": {},
            "query_hints": {},
            "path_variables": {},
            "search_paths": [],
            "transition_hints": {},
        }
        normalized_xml_names = sorted(
            {Path(name).name.lower() for name in available_xml_names}
            | {Path(name).stem.lower() for name in available_xml_names}
        )
        for missing_name in missing_xml_refs:
            if not missing_name:
                continue
            matches = get_close_matches(missing_name.lower(), normalized_xml_names, n=1, cutoff=0.6)
            if matches:
                suggestions["xml_aliases"][missing_name] = matches[0]
        for query_name, placeholders in unresolved_queries.items():
            if placeholders:
                suggestions["placeholder_notes"][query_name] = (
                    "Document Delphi-side replacement rules for: " + ", ".join(placeholders)
                )
                suggestions["query_hints"][query_name] = (
                    "Explain which business rule injects runtime SQL values before execution."
                )
        return suggestions

    def _build_knowledge_insights(
        self,
        diagnostics: list[DiagnosticRecord],
        resolved_queries: list[ResolvedQueryArtifact],
        suggested_overrides: dict,
    ) -> str:
        severe = [item for item in diagnostics if item.severity in {"error", "fatal"}]
        unresolved_queries = [
            f"{query.name}: {', '.join(query.unresolved_placeholders)}"
            for query in resolved_queries
            if query.unresolved_placeholders
        ]
        lines = [
            "# Knowledge Insights",
            "",
            "## Diagnostic Clusters",
            "",
        ]
        if diagnostics:
            counts = Counter(item.code for item in diagnostics)
            lines.extend(f"- {code}: {count}" for code, count in counts.most_common(10))
        else:
            lines.append("- No diagnostics recorded.")
        lines.extend(["", "## Queries Requiring Runtime Knowledge", ""])
        if unresolved_queries:
            lines.extend(f"- {item}" for item in unresolved_queries[:20])
        else:
            lines.append("- No unresolved placeholders were detected.")
        lines.extend(["", "## Suggested Overrides", ""])
        if any(suggested_overrides.values()):
            for key, value in suggested_overrides.items():
                if value:
                    lines.append(f"- {key}: {len(value)} suggestion(s)")
        else:
            lines.append("- No override suggestions were generated.")
        lines.extend(["", "## Feedback Learning", ""])
        if self.feedback_log:
            lines.append(f"- Feedback entries loaded: {len(self.feedback_log)}")
        else:
            lines.append("- No feedback has been ingested yet.")
        lines.extend(["", "## Prompting Advice", ""])
        if severe:
            for item in severe[:5]:
                lines.append(f"- {item.prompt_hint or item.message}")
        else:
            lines.append("- Use business flow artifacts first, then add query artifacts only when needed.")
        lines.append("")
        return "\n".join(lines)


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


def _read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_rule_payload(payload: Any, path: Path) -> list[DiagnosticRecord]:
    diagnostics: list[DiagnosticRecord] = []
    if not isinstance(payload, dict):
        diagnostics.append(
            make_diagnostic(
                "warning",
                "KNOWLEDGE_OVERRIDE_INVALID_ROOT",
                "Rule file must contain a JSON object.",
                file_path=path.as_posix(),
                suggestion="Rewrite the file as a JSON object keyed by rule type.",
            )
        )
        return diagnostics

    for key, value in payload.items():
        expected = ALLOWED_RULE_KEYS.get(key)
        if expected is None:
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "KNOWLEDGE_OVERRIDE_UNKNOWN_KEY",
                    f"Unknown override key '{key}' was loaded.",
                    file_path=path.as_posix(),
                    suggestion="Remove unknown keys or teach the analyzer how to use them.",
                )
            )
            continue
        if not isinstance(value, expected):
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "KNOWLEDGE_OVERRIDE_INVALID_TYPE",
                    f"Override key '{key}' should be {expected.__name__}.",
                    file_path=path.as_posix(),
                    suggestion=f"Change '{key}' to a JSON {expected.__name__}.",
                )
            )
            continue
        if isinstance(value, dict) and not all(
            isinstance(item_key, str) and isinstance(item_value, str)
            for item_key, item_value in value.items()
        ):
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "KNOWLEDGE_OVERRIDE_INVALID_MAPPING",
                    f"Override key '{key}' must map string keys to string values.",
                    file_path=path.as_posix(),
                    suggestion=f"Normalize all keys and values under '{key}' to strings.",
                )
            )
        if isinstance(value, list) and not all(isinstance(item, str) for item in value):
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "KNOWLEDGE_OVERRIDE_INVALID_LIST",
                    f"Override key '{key}' must contain only strings.",
                    file_path=path.as_posix(),
                    suggestion=f"Normalize all entries under '{key}' to strings.",
                )
            )
    return diagnostics


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
