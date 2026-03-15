from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from legacy_delphi_project_analyzer.models import (
    DiagnosticRecord,
    QueryDefinition,
    QueryFragment,
    QueryParameter,
    ResolvedQueryArtifact,
    SqlXmlFileSummary,
)
from legacy_delphi_project_analyzer.utils import PLACEHOLDER_RE, make_diagnostic, read_text_file
from legacy_delphi_project_analyzer.workspace import workspace_key_for_path


COMMENT_RE = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)
DML_RE = re.compile(r"^\s*(insert|update|delete|merge)\b", re.IGNORECASE)
DUAL_SELECT_RE = re.compile(r"(?is)\bselect\s+:(\w+)\b.*?\bfrom\s+dual\b")
VALID_DATA_TYPES = {"Int", "Double", "String", "DateTime", "IntArray", "StringArray", "SQL"}


def parse_sql_xml_file(
    path: Path,
    workspace_roots: Path | list[Path],
) -> tuple[SqlXmlFileSummary | None, list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    scan_roots = _normalize_workspace_roots(workspace_roots)
    text, _, decode_failed = read_text_file(path)
    if decode_failed:
        diagnostics.append(
            make_diagnostic(
                "warning",
                "SQL_XML_DECODE_FALLBACK",
                "Decoded XML file with replacement characters.",
                file_path=path.as_posix(),
                suggestion="Re-run after confirming the XML file encoding.",
            )
        )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        diagnostics.append(
            make_diagnostic(
                "warning",
                "XML_PARSE_SKIPPED",
                f"Skipped XML file that could not be parsed: {exc}",
                file_path=path.as_posix(),
                suggestion="If this file is a SQL XML mapping, fix malformed XML before the next run.",
            )
        )
        return None, diagnostics

    if root.tag != "sql-mapping":
        return None, diagnostics

    relative_key = workspace_key_for_path(path, scan_roots).lower()
    xml_keys = list(
        OrderedDict.fromkeys([relative_key, path.name.lower(), path.stem.lower(), path.resolve().as_posix().lower()])
    )
    summary = SqlXmlFileSummary(file_path=path.as_posix(), xml_keys=xml_keys)

    unnamed_counter = 1
    seen_names: set[tuple[str, str]] = set()
    for element in root:
        if element.tag not in {"main-query", "sub-query"}:
            continue
        name = (element.attrib.get("name") or "").strip()
        if not name:
            name = f"__anonymous_{unnamed_counter}"
            unnamed_counter += 1
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "SQL_XML_MISSING_NAME",
                    "Query is missing a name attribute; generated a synthetic name.",
                    file_path=path.as_posix(),
                    suggestion="Add an explicit query name to stabilize references.",
                )
            )
        key = (element.tag, name.lower())
        if key in seen_names:
            diagnostics.append(
                make_diagnostic(
                    "error",
                    "SQL_XML_DUPLICATE_QUERY",
                    f"Duplicate {element.tag} named '{name}' in the same XML file.",
                    file_path=path.as_posix(),
                    suggestion="Rename or consolidate duplicate query definitions to keep reference resolution deterministic.",
                    prompt_hint=f"Explain which duplicate SQL XML query named {name} should remain authoritative.",
                    details={"kind": element.tag, "query_name": name},
                )
            )
        else:
            seen_names.add(key)
        query, query_diagnostics = _parse_query_element(path, xml_keys[0], element, name)
        diagnostics.extend(query_diagnostics)
        if element.tag == "main-query":
            summary.main_queries.append(query)
        else:
            summary.sub_queries.append(query)

    return summary, diagnostics


def _normalize_workspace_roots(workspace_roots: Path | list[Path]) -> list[Path]:
    if isinstance(workspace_roots, Path):
        return [workspace_roots.resolve()]
    return [item.resolve() for item in workspace_roots]


def _parse_query_element(
    path: Path,
    xml_key: str,
    element: ET.Element,
    name: str,
) -> tuple[QueryDefinition, list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    parameters: list[QueryParameter] = []
    fragments: list[QueryFragment] = []
    raw_body_parts: list[str] = []

    for child in list(element):
        if child.tag == "parameter":
            data_type = child.attrib.get("data_type")
            if data_type and data_type not in VALID_DATA_TYPES:
                diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "SQL_XML_UNKNOWN_DATA_TYPE",
                        f"Unsupported parameter data_type '{data_type}' on '{name}'.",
                        file_path=path.as_posix(),
                        suggestion="Use one of the documented SQL XML data types or teach the analyzer how to map this custom type.",
                    )
                )
            parameters.append(
                QueryParameter(
                    name=(child.attrib.get("name") or "").lstrip(":"),
                    data_type=data_type,
                    sample=child.attrib.get("sample"),
                    default=child.attrib.get("default"),
                )
            )
            continue

        if child.tag == "sql-body":
            body_text = (child.text or "").strip("\n")
            fragments.append(QueryFragment(kind="sql-body", text=body_text))
            raw_body_parts.append(body_text)
            continue

        if child.tag not in {
            "sql-refer-to",
            "ext-sql-refer-to",
            "sql-copy",
            "ext-sql-copy",
        }:
            diagnostics.append(
                make_diagnostic(
                    "warning",
                    "SQL_XML_UNKNOWN_TAG",
                    f"Unsupported SQL XML tag '{child.tag}' was ignored.",
                    file_path=path.as_posix(),
                    suggestion="If this tag affects query composition, extend the parser rule set.",
                )
            )
            continue

        fragment, fragment_diagnostics = _parse_reference_fragment(path, child)
        diagnostics.extend(fragment_diagnostics)
        fragments.append(fragment)

    return (
        QueryDefinition(
            file_path=path.as_posix(),
            xml_key=xml_key,
            kind=element.tag,
            name=name,
            raw_body="\n".join(part for part in raw_body_parts if part).strip(),
            parameters=parameters,
            fragments=fragments,
        ),
        diagnostics,
    )


def _parse_reference_fragment(
    path: Path, element: ET.Element
) -> tuple[QueryFragment, list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    target_sub = element.attrib.get("sub-query")
    target_main = element.attrib.get("main-query")
    name = element.attrib.get("name") or target_sub or target_main
    xml_name = element.attrib.get("xml")

    if target_sub and target_main:
        diagnostics.append(
            make_diagnostic(
                "error",
                "SQL_XML_INVALID_TARGET",
                "sub-query and main-query cannot be specified at the same time.",
                file_path=path.as_posix(),
                suggestion="Keep only one of sub-query or main-query on this element.",
            )
        )

    target_kind: str | None = None
    if element.tag in {"sql-copy", "ext-sql-copy"}:
        target_kind = "main-query"
        if target_sub:
            diagnostics.append(
                make_diagnostic(
                    "error",
                    "SQL_XML_COPY_SUBQUERY_UNSUPPORTED",
                    f"{element.tag} only supports main-query targets.",
                    file_path=path.as_posix(),
                    suggestion="Replace sub-query=\"...\" with main-query=\"...\" or use sql-refer-to/ext-sql-refer-to instead.",
                )
            )
        if not (target_main or name):
            diagnostics.append(
                make_diagnostic(
                    "error",
                    "SQL_XML_COPY_MISSING_TARGET",
                    "sql-copy/ext-sql-copy requires a main-query target.",
                    file_path=path.as_posix(),
                    suggestion="Add main-query=\"...\" or name=\"...\" to the copy element.",
                )
            )
    elif target_sub:
        target_kind = "sub-query"
    elif target_main:
        target_kind = "main-query"
    elif element.tag == "sql-refer-to":
        target_kind = "same-name"
    else:
        diagnostics.append(
            make_diagnostic(
                "error",
                "SQL_XML_EXTERNAL_TARGET_REQUIRED",
                "ext-sql-refer-to requires either sub-query or main-query.",
                file_path=path.as_posix(),
                suggestion="Specify sub-query or main-query on the external reference.",
            )
        )

    return (
        QueryFragment(
            kind=element.tag,
            name=name,
            xml_name=(xml_name or "").lower() or None,
            target_kind=target_kind,
        ),
        diagnostics,
    )


class SqlXmlResolver:
    def __init__(
        self,
        summaries: list[SqlXmlFileSummary],
        diagnostics: list[DiagnosticRecord] | None = None,
        xml_aliases: dict[str, str] | None = None,
    ) -> None:
        self.summaries = summaries
        self.diagnostics = diagnostics if diagnostics is not None else []
        self.xml_aliases = {key.lower(): value.lower() for key, value in (xml_aliases or {}).items()}
        self.file_index: dict[str, SqlXmlFileSummary] = {}
        self.query_index: dict[tuple[str, str, str], QueryDefinition] = {}
        self.cache: dict[tuple[str, str, str], ResolvedQueryArtifact] = {}
        for summary in summaries:
            for key in summary.xml_keys:
                self.file_index[key.lower()] = summary
            for query in summary.main_queries + summary.sub_queries:
                primary_key = (summary.xml_keys[0], query.kind, query.name.lower())
                if primary_key in self.query_index:
                    self.diagnostics.append(
                        make_diagnostic(
                            "error",
                            "SQL_XML_DUPLICATE_QUERY",
                            f"Duplicate {query.kind} named '{query.name}' will keep the first definition.",
                            file_path=summary.file_path,
                            suggestion="Rename duplicate query definitions to make reference resolution deterministic.",
                            details={"kind": query.kind, "query_name": query.name},
                        )
                    )
                    continue
                for key in summary.xml_keys:
                    self.query_index[(key.lower(), query.kind, query.name.lower())] = query

    def resolve_all(self) -> list[ResolvedQueryArtifact]:
        artifacts: list[ResolvedQueryArtifact] = []
        for summary in self.summaries:
            for query in summary.main_queries + summary.sub_queries:
                artifacts.append(
                    self.resolve_query(summary.xml_keys[0], query.kind, query.name.lower(), stack=[])
                )
        return artifacts

    def resolve_query(
        self,
        xml_key: str,
        kind: str,
        name: str,
        stack: list[tuple[str, str, str]],
    ) -> ResolvedQueryArtifact:
        cache_key = (xml_key, kind, name)
        if cache_key in self.cache:
            return self.cache[cache_key]
        if cache_key in stack:
            cycle_chain = [f"{xml}:{query_kind}:{query_name}" for xml, query_kind, query_name in [*stack, cache_key]]
            diagnostic = make_diagnostic(
                "error",
                "SQL_XML_CYCLE",
                f"Detected a cyclic SQL reference while resolving {kind}:{name}.",
                file_path=self.file_index.get(xml_key, self.summaries[0]).file_path if self.summaries else None,
                suggestion="Break the reference loop or change one node to sql-copy.",
                prompt_hint=f"Show the full reference chain that creates the cycle for {kind}:{name}.",
                details={"xml_key": xml_key, "kind": kind, "name": name, "cycle_chain": cycle_chain},
            )
            self.diagnostics.append(diagnostic)
            artifact = ResolvedQueryArtifact(
                file_path=self.file_index.get(xml_key, self.summaries[0]).file_path if self.summaries else "",
                xml_key=xml_key,
                kind=kind,
                name=name,
                raw_body="",
                expanded_sql=f"/* cyclic reference: {' -> '.join(cycle_chain)} */",
                unresolved_placeholders=[],
                source_trace=cycle_chain,
                warnings=["cyclic reference"],
            )
            self.cache[cache_key] = artifact
            return artifact

        query = self.query_index.get(cache_key)
        if not query:
            diagnostic = make_diagnostic(
                "error",
                "SQL_XML_TARGET_NOT_FOUND",
                f"Could not resolve referenced query {kind}:{name}.",
                suggestion="Check the query name and whether the referenced XML file was included in the project scan.",
                prompt_hint=f"Locate where query {name} should be defined and describe the expected XML mapping.",
                details={"xml_key": xml_key, "kind": kind, "target_name": name},
            )
            self.diagnostics.append(diagnostic)
            artifact = ResolvedQueryArtifact(
                file_path=self.file_index.get(xml_key, self.summaries[0]).file_path if self.summaries else "",
                xml_key=xml_key,
                kind=kind,
                name=name,
                raw_body="",
                expanded_sql=f"/* unresolved reference: {kind}:{name} */",
                unresolved_placeholders=[],
                source_trace=[f"{xml_key}:{kind}:{name}"],
                warnings=["unresolved reference"],
            )
            self.cache[cache_key] = artifact
            return artifact

        expanded_parts: list[str] = []
        parameters = {item.name: item for item in query.parameters if item.name}
        source_trace = [f"{xml_key}:{kind}:{query.name}"]
        warnings: list[str] = []
        next_stack = [*stack, cache_key]

        for fragment in query.fragments:
            if fragment.kind == "sql-body":
                expanded_parts.append(fragment.text or "")
                continue
            fragment_sql, fragment_parameters, fragment_trace, fragment_warnings = self._resolve_fragment(
                owner_xml_key=xml_key,
                fragment=fragment,
                stack=next_stack,
            )
            expanded_parts.append(fragment_sql)
            parameters.update({item.name: item for item in fragment_parameters if item.name})
            source_trace.extend(fragment_trace)
            warnings.extend(fragment_warnings)

        expanded_sql = "\n".join(part for part in expanded_parts if part).strip()
        discovered = sorted(set(PLACEHOLDER_RE.findall(expanded_sql)))
        unresolved = sorted(set(name for name in discovered if name not in parameters))
        artifact = ResolvedQueryArtifact(
            file_path=query.file_path,
            xml_key=xml_key,
            kind=kind,
            name=query.name,
            raw_body=query.raw_body,
            expanded_sql=expanded_sql,
            parameter_definitions=list(parameters.values()),
            discovered_placeholders=discovered,
            unresolved_placeholders=unresolved,
            source_trace=source_trace,
            warnings=warnings,
        )
        self._apply_sql_rules(artifact)
        self.cache[cache_key] = artifact
        return artifact

    def _resolve_fragment(
        self,
        owner_xml_key: str,
        fragment: QueryFragment,
        stack: list[tuple[str, str, str]],
    ) -> tuple[str, list[QueryParameter], list[str], list[str]]:
        target_xml_key = owner_xml_key
        if fragment.kind.startswith("ext-"):
            target_xml_key = self._resolve_external_xml_key(fragment.xml_name)
            if not target_xml_key:
                self.diagnostics.append(
                    make_diagnostic(
                        "error",
                        "SQL_XML_EXTERNAL_NOT_FOUND",
                        f"Could not find external SQL XML file '{fragment.xml_name}'.",
                        suggestion="Make sure the file exists under the scanned project root or add an xml_aliases override.",
                        prompt_hint=f"List the correct XML file name for external reference '{fragment.xml_name}'.",
                        details={"xml_name": fragment.xml_name},
                    )
                )
                return (
                    f"/* unresolved external xml: {fragment.xml_name} */",
                    [],
                    [],
                    ["missing external xml"],
                )

        target_kind, target_name = self._resolve_target(owner_xml_key, target_xml_key, fragment)
        if not target_kind or not target_name:
            return ("", [], [], ["invalid reference"])

        if fragment.kind in {"sql-copy", "ext-sql-copy"} or target_kind == "sub-query":
            target_query = self.query_index.get((target_xml_key, target_kind, target_name.lower()))
            if not target_query:
                self.diagnostics.append(
                    make_diagnostic(
                        "error",
                        "SQL_XML_TARGET_NOT_FOUND",
                        f"Could not resolve referenced query {target_kind}:{target_name}.",
                        suggestion="Check the referenced name and update overrides if the external XML uses an alias.",
                        prompt_hint=f"Show the query definitions in the XML that should contain {target_name}.",
                        details={
                            "xml_key": target_xml_key,
                            "kind": target_kind,
                            "target_name": target_name,
                        },
                    )
                )
                return (
                    f"/* unresolved reference: {target_kind}:{target_name} */",
                    [],
                    [],
                    ["unresolved reference"],
                )
            return (
                target_query.raw_body,
                target_query.parameters,
                [f"{target_xml_key}:{target_kind}:{target_query.name}"],
                [],
            )

        resolved = self.resolve_query(target_xml_key, target_kind, target_name.lower(), stack)
        return (
            resolved.expanded_sql,
            resolved.parameter_definitions,
            resolved.source_trace,
            resolved.warnings,
        )

    def _resolve_external_xml_key(self, xml_name: str | None) -> str | None:
        if not xml_name:
            return None
        candidates = [xml_name.lower()]
        alias_target = self.xml_aliases.get(xml_name.lower())
        if alias_target:
            candidates.append(alias_target.lower())
        if not xml_name.lower().endswith(".xml"):
            candidates.append(f"{xml_name.lower()}.xml")
        for candidate in candidates:
            if candidate in self.file_index:
                return candidate
            for key in self.file_index:
                if key.endswith(candidate) or Path(key).name == candidate or Path(key).stem == Path(candidate).stem:
                    return key
        return None

    def _resolve_target(
        self,
        owner_xml_key: str,
        target_xml_key: str,
        fragment: QueryFragment,
    ) -> tuple[str | None, str | None]:
        if fragment.target_kind == "same-name":
            if not fragment.name:
                return None, None
            lower_name = fragment.name.lower()
            sub_exists = (target_xml_key, "sub-query", lower_name) in self.query_index
            main_exists = (target_xml_key, "main-query", lower_name) in self.query_index
            if sub_exists and main_exists:
                self.diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "SQL_XML_SAME_NAME_AMBIGUOUS",
                        f"Both sub-query and main-query named '{fragment.name}' exist; defaulting to sub-query.",
                        file_path=self.file_index.get(owner_xml_key, self.summaries[0]).file_path if self.summaries else None,
                        suggestion="Set sub-query or main-query explicitly to avoid ambiguity.",
                    )
                )
            if sub_exists:
                return "sub-query", fragment.name
            if main_exists:
                return "main-query", fragment.name
            self.diagnostics.append(
                make_diagnostic(
                    "warning",
                    "SQL_XML_SAME_NAME_AMBIGUOUS",
                    f"Fell back to same-name resolution for '{fragment.name}' and found no exact match.",
                    file_path=self.file_index.get(owner_xml_key, self.summaries[0]).file_path if self.summaries else None,
                    suggestion="Set sub-query or main-query explicitly to avoid ambiguity.",
                )
            )
            return None, None
        return fragment.target_kind, fragment.name

    def _apply_sql_rules(self, artifact: ResolvedQueryArtifact) -> None:
        sql = artifact.expanded_sql.strip()
        if not sql:
            return
        if DML_RE.search(sql) and not sql.endswith(";"):
            artifact.warnings.append("DML statement does not end with ';'")
            self.diagnostics.append(
                make_diagnostic(
                    "warning",
                    "SQL_DML_MISSING_SEMICOLON",
                    f"DML query {artifact.name} should end with ';'.",
                    file_path=artifact.file_path,
                    suggestion="Append ';' to DML SQL to match the XML authoring rule.",
                    details={"query_name": artifact.name},
                )
            )
        dual_match = DUAL_SELECT_RE.search(sql)
        if dual_match and f"cast(:{dual_match.group(1)}" not in sql.lower():
            artifact.warnings.append("SELECT :param FROM dual should cast the parameter")
            self.diagnostics.append(
                make_diagnostic(
                    "warning",
                    "SQL_DUAL_CAST_MISSING",
                    f"SELECT :{dual_match.group(1)} FROM dual should cast the parameter value.",
                    file_path=artifact.file_path,
                    suggestion="Wrap the parameter with CAST(...) to help downstream schema inference.",
                    details={"query_name": artifact.name, "parameter": dual_match.group(1)},
                )
            )
        for comment in COMMENT_RE.findall(sql):
            if ":" in comment or "'" in comment:
                artifact.warnings.append("SQL comment includes ':' or '''")
                self.diagnostics.append(
                    make_diagnostic(
                        "warning",
                        "SQL_COMMENT_FORBIDDEN_CHAR",
                        f"SQL comment in {artifact.name} contains ':' or a single quote.",
                        file_path=artifact.file_path,
                        suggestion="Move parameter-like markers and quoted text out of SQL comments.",
                        details={"query_name": artifact.name},
                    )
                )
