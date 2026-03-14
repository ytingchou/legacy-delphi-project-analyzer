from __future__ import annotations

import fnmatch
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from legacy_delphi_project_analyzer.models import DiagnosticRecord, ResolvedQueryArtifact
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json


class KnowledgeStore:
    def __init__(self, project_root: Path, rules_dir: Path | None, output_dir: Path) -> None:
        self.project_root = project_root
        self.rules_dir = rules_dir
        self.output_dir = output_dir
        self.knowledge_dir = output_dir / "knowledge"
        self.overrides = {
            "ignore_globs": [],
            "module_overrides": {},
            "xml_aliases": {},
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
                self.overrides.update(self._read_json(overrides_path))
        learned_path = self.knowledge_dir / "learned_patterns.json"
        if learned_path.exists():
            self.learned.update(self._read_json(learned_path))

    @staticmethod
    def _read_json(path: Path) -> dict:
        import json

        return json.loads(path.read_text(encoding="utf-8"))

    def should_ignore(self, path: Path) -> bool:
        relative = path.relative_to(self.project_root).as_posix()
        patterns = list(self.overrides.get("ignore_globs", [])) + list(
            self.learned.get("ignore_globs", [])
        )
        return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)

    def apply_module_override(self, candidate: str) -> str:
        return self.overrides.get("module_overrides", {}).get(candidate, candidate)

    def resolve_xml_alias(self, name: str) -> str:
        aliases = {}
        aliases.update(self.learned.get("xml_aliases", {}))
        aliases.update(self.overrides.get("xml_aliases", {}))
        return aliases.get(name, name)

    def learn(
        self,
        diagnostics: list[DiagnosticRecord],
        resolved_queries: list[ResolvedQueryArtifact],
    ) -> None:
        diagnostic_counts = Counter(item.code for item in diagnostics)
        unresolved = Counter()
        missing_xml = Counter()
        missing_query = Counter()
        for query in resolved_queries:
            unresolved.update(query.unresolved_placeholders)
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

        ensure_directory(self.knowledge_dir)
        write_json(self.knowledge_dir / "learned_patterns.json", self.learned)
