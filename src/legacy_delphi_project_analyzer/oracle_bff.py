from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, trim_sql_snippet, write_json, write_text


def compile_oracle_bff_sql(analysis_dir: Path, *, output_dir: Path | None = None) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    compiler_dir = (output_dir or analysis_dir / "llm-pack" / "bff-sql-compiler").resolve()
    ensure_directory(compiler_dir)
    query_index = {item.name: item for item in output.resolved_queries}

    entries: list[dict[str, Any]] = []
    for artifact in output.bff_sql_artifacts:
        query = query_index.get(artifact.query_name)
        entry = _build_entry(artifact, query)
        base_name = f"{slugify(artifact.module_name)}-{slugify(artifact.endpoint_name)}-oracle-bff"
        write_json(compiler_dir / f"{base_name}.json", entry)
        write_text(compiler_dir / f"{base_name}.md", _render_entry_markdown(entry))
        entries.append(entry)

    manifest = {
        "analysis_dir": analysis_dir.as_posix(),
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "read_endpoints": sum(1 for item in entries if item["operation_kind"] == "read"),
            "command_endpoints": sum(1 for item in entries if item["operation_kind"] == "command"),
        },
    }
    write_json(compiler_dir / "oracle-bff-manifest.json", manifest)
    write_text(compiler_dir / "oracle-bff-guide.md", _render_manifest_markdown(manifest))
    return manifest


def _build_entry(artifact: Any, query: Any) -> dict[str, Any]:
    sql = query.expanded_sql if query is not None else artifact.compact_sql_summary
    operation_kind = _operation_kind(sql)
    select_fields = _select_fields(sql)
    dto_bindings = [
        {
            "field_name": field.name,
            "data_type": field.data_type,
            "required": field.required,
            "bind_name": field.name,
        }
        for field in artifact.request_fields
    ]
    filter_bindings = [field.name for field in artifact.request_fields]
    pagination_supported = bool(re.search(r"\b(fetch\s+first|offset|rownum)\b", sql, re.I))
    sort_supported = bool(re.search(r"\border\s+by\b", sql, re.I))
    semantic_checks = _semantic_checks(artifact, query, sql, dto_bindings)
    return {
        "module_name": artifact.module_name,
        "endpoint_name": artifact.endpoint_name,
        "http_method": artifact.http_method,
        "route_path": artifact.route_path,
        "query_name": artifact.query_name,
        "operation_kind": operation_kind,
        "sql_preview": trim_sql_snippet(sql, limit=320),
        "controller_contract": f"{artifact.http_method} {artifact.route_path}",
        "request_dto": artifact.request_dto,
        "response_dto": artifact.response_dto,
        "dto_bindings": dto_bindings,
        "filter_bindings": filter_bindings,
        "select_fields": select_fields,
        "pagination_supported": pagination_supported,
        "sort_supported": sort_supported,
        "oracle_19c_notes": list(artifact.oracle_19c_notes),
        "placeholder_strategy": list(artifact.placeholder_strategy),
        "service_logic": list(artifact.service_logic),
        "repository_contract": list(artifact.repository_contract),
        "semantic_checks": semantic_checks,
    }


def _operation_kind(sql: str) -> str:
    compact = sql.lstrip().lower()
    if compact.startswith("select"):
        return "read"
    if compact.startswith(("insert", "update", "delete", "merge")):
        return "command"
    return "unknown"


def _select_fields(sql: str) -> list[str]:
    match = re.search(r"select\s+(.*?)\s+from\s", sql, re.I | re.S)
    if not match:
        return []
    raw = match.group(1)
    fields = []
    for item in raw.split(","):
        clean = re.sub(r"\s+", " ", item).strip()
        if clean:
            fields.append(clean)
    return fields[:20]


def _semantic_checks(artifact: Any, query: Any, sql: str, dto_bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = []
    placeholders = list(query.discovered_placeholders) if query is not None else []
    unresolved = list(query.unresolved_placeholders) if query is not None else []
    bound_fields = {item["bind_name"] for item in dto_bindings}
    missing_binds = [item for item in placeholders if item.lstrip(":") not in bound_fields]
    checks.append(
        {
            "check": "bind_param_completeness",
            "status": "warning" if missing_binds else "pass",
            "details": missing_binds or ["All discovered placeholders map to request DTO fields or legacy runtime steps."],
        }
    )
    dml = _operation_kind(sql) == "command"
    checks.append(
        {
            "check": "dml_terminator",
            "status": "warning" if dml and not sql.rstrip().endswith(";") else "pass",
            "details": ["DML SQL should end with ';' for the legacy XML rule set."] if dml and not sql.rstrip().endswith(";") else ["Terminator rule satisfied or not applicable."],
        }
    )
    checks.append(
        {
            "check": "unresolved_placeholders",
            "status": "warning" if unresolved else "pass",
            "details": unresolved or ["No unresolved placeholders remain after SQL XML expansion."],
        }
    )
    checks.append(
        {
            "check": "service_step_count",
            "status": "warning" if len(artifact.service_logic) > 5 else "pass",
            "details": [f"Service logic steps: {len(artifact.service_logic)}"],
        }
    )
    return checks


def _render_entry_markdown(entry: dict[str, Any]) -> str:
    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {item}" for item in values) if values else "- None"

    checks = "\n".join(
        f"- {item['check']}: {item['status']} ({'; '.join(item['details'])})"
        for item in entry["semantic_checks"]
    )
    return f"""# Oracle BFF Compiler: {entry['module_name']} / {entry['endpoint_name']}

- Controller contract: `{entry['controller_contract']}`
- Query: `{entry['query_name']}`
- Operation kind: {entry['operation_kind']}
- Pagination supported: {str(entry['pagination_supported']).lower()}
- Sort supported: {str(entry['sort_supported']).lower()}

## SQL Preview

```sql
{entry['sql_preview']}
```

## DTO Bindings

{bullets([f"{item['field_name']} -> {item['bind_name']} ({item['data_type']})" for item in entry['dto_bindings']])}

## Semantic Checks

{checks}

## Repository Contract

{bullets(entry['repository_contract'])}

## Service Logic

{bullets(entry['service_logic'])}
"""


def _render_manifest_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Oracle BFF SQL Compiler Guide",
        "",
        f"- Entries: {manifest['summary']['entry_count']}",
        f"- Read endpoints: {manifest['summary']['read_endpoints']}",
        f"- Command endpoints: {manifest['summary']['command_endpoints']}",
        "",
        "## Endpoint Map",
        "",
    ]
    for entry in manifest.get("entries", []):
        lines.extend(
            [
                f"### {entry['module_name']} / {entry['endpoint_name']}",
                f"- Contract: `{entry['controller_contract']}`",
                f"- Query: `{entry['query_name']}`",
                f"- Operation kind: {entry['operation_kind']}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
