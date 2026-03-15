from __future__ import annotations

import fnmatch
from collections import Counter
from datetime import UTC, datetime
from difflib import get_close_matches
from pathlib import Path

from legacy_delphi_project_analyzer.models import DiagnosticRecord, ResolvedQueryArtifact
from legacy_delphi_project_analyzer.utils import ensure_directory, make_diagnostic, write_json, write_text


ALLOWED_OVERRIDE_KEYS = {
    "ignore_globs": list,
    "module_overrides": dict,
    "xml_aliases": dict,
    "placeholder_notes": dict,
    "query_hints": dict,
}


class KnowledgeStore:
    def __init__(self, project_root: Path, rules_dir: Path | None, output_dir: Path) -> None:
        self.project_root = project_root
        self.rules_dir = rules_dir
        self.output_dir = output_dir
        self.knowledge_dir = output_dir / "knowledge"
        self.diagnostics: list[DiagnosticRecord] = []
        self.overrides = {
            "ignore_globs": [],
            "module_overrides": {},
            "xml_aliases": {},
            "placeholder_notes": {},
            "query_hints": {},
        }
        self.learned = {
            "ignore_globs": [],
            "diagnostic_counts": {},
            "unresolved_placeholders": {},
            "missing_xml_refs": {},
            "missing_query_refs": {},
        }
        self._load()

    def _load(self) -> None:
        if self.rules_dir:
            overrides_path = self.rules_dir / "overrides.json"
            if overrides_path.exists():
                loaded = self._read_json(overrides_path)
                self._validate_overrides(loaded, overrides_path)
                self.overrides.update(loaded)
        learned_path = self.knowledge_dir / "learned_patterns.json"
        if learned_path.exists():
            self.learned.update(self._read_json(learned_path))

    @staticmethod
    def _read_json(path: Path) -> dict:
        import json

        return json.loads(path.read_text(encoding="utf-8"))

    def _validate_overrides(self, loaded: dict, path: Path) -> None:
        for key, value in loaded.items():
            expected = ALLOWED_OVERRIDE_KEYS.get(key)
            if expected is None:
                self.diagnostics.append(
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
                self.diagnostics.append(
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
                self.diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "KNOWLEDGE_OVERRIDE_INVALID_MAPPING",
                        f"Override key '{key}' must map string keys to string values.",
                        file_path=path.as_posix(),
                        suggestion=f"Normalize all keys and values under '{key}' to strings.",
                    )
                )
            if isinstance(value, list) and not all(isinstance(item, str) for item in value):
                self.diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "KNOWLEDGE_OVERRIDE_INVALID_LIST",
                        f"Override key '{key}' must contain only strings.",
                        file_path=path.as_posix(),
                        suggestion=f"Normalize all entries under '{key}' to strings.",
                    )
                )

    def get_diagnostics(self) -> list[DiagnosticRecord]:
        return list(self.diagnostics)

    def should_ignore(self, path: Path) -> bool:
        relative = path.relative_to(self.project_root).as_posix()
        patterns = [
            item
            for item in list(self.overrides.get("ignore_globs", []))
            + list(self.learned.get("ignore_globs", []))
            if isinstance(item, str)
        ]
        return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)

    def apply_module_override(self, candidate: str) -> str:
        module_overrides = self.overrides.get("module_overrides", {})
        value = module_overrides.get(candidate)
        return value if isinstance(value, str) else candidate

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
        }
        normalized_xml_names = sorted(
            {Path(name).name.lower() for name in available_xml_names} | {Path(name).stem.lower() for name in available_xml_names}
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
        lines.extend(["", "## Prompting Advice", ""])
        if severe:
            for item in severe[:5]:
                lines.append(f"- {item.prompt_hint or item.message}")
        else:
            lines.append("- Use business flow artifacts first, then add query artifacts only when needed.")
        lines.append("")
        return "\n".join(lines)
