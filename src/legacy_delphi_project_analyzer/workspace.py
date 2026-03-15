from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from legacy_delphi_project_analyzer.models import DiagnosticRecord
from legacy_delphi_project_analyzer.utils import make_diagnostic, read_text_file


PROJECT_FILE_PATTERNS = ("*.dproj", "*.groupproj", "*.cfg")
SEARCH_PATH_TAGS = {
    "DCC_UnitSearchPath",
    "DCC_IncludePath",
    "DCC_SearchPath",
    "UnitSearchPath",
    "SearchPath",
    "IncludePath",
}
CFG_PATH_PREFIXES = ("-U", "-I")
STANDARD_MACROS = {
    "BDS",
    "BDSCOMMONDIR",
    "BDSLIB",
    "DELPHI",
    "PLATFORM",
    "CONFIG",
    "CURDIR",
    "PROJECTDIR",
}
MACRO_RE = re.compile(r"\$\(([^)]+)\)")


@dataclass(slots=True)
class WorkspaceResolution:
    scan_roots: list[Path]
    project_files: list[str] = field(default_factory=list)
    configured_search_paths: list[str] = field(default_factory=list)
    missing_search_paths: list[str] = field(default_factory=list)
    unresolved_search_paths: list[str] = field(default_factory=list)
    diagnostics: list[DiagnosticRecord] = field(default_factory=list)


def resolve_workspace(
    project_root: Path,
    extra_search_paths: list[str] | None = None,
    workspace_config_path: Path | None = None,
    path_variables: dict[str, str] | None = None,
) -> WorkspaceResolution:
    project_root = project_root.resolve()
    diagnostics: list[DiagnosticRecord] = []
    project_files = sorted(
        path.resolve().as_posix()
        for pattern in PROJECT_FILE_PATTERNS
        for path in project_root.rglob(pattern)
        if path.is_file()
    )

    config = _load_workspace_config(workspace_config_path, diagnostics)
    merged_variables = _build_path_variables(
        project_root=project_root,
        workspace_config_path=workspace_config_path,
        config_variables=config["path_variables"],
        cli_variables=path_variables or {},
    )

    scan_roots = [project_root]
    configured_search_paths: list[str] = []
    missing_search_paths: list[str] = []
    unresolved_search_paths: list[str] = []

    for path in config["scan_roots"]:
        result = _resolve_search_path(
            raw_path=path,
            base_dir=workspace_config_path.parent if workspace_config_path else project_root,
            variables=merged_variables,
            source_file=workspace_config_path.as_posix() if workspace_config_path else None,
        )
        if result is not None:
            _apply_resolved_path(
                result,
                scan_roots,
                configured_search_paths,
                missing_search_paths,
                unresolved_search_paths,
                diagnostics,
            )

    for raw_path in config["search_paths"]:
        result = _resolve_search_path(
            raw_path=raw_path,
            base_dir=workspace_config_path.parent if workspace_config_path else project_root,
            variables=merged_variables,
            source_file=workspace_config_path.as_posix() if workspace_config_path else None,
        )
        if result is not None:
            _apply_resolved_path(
                result,
                scan_roots,
                configured_search_paths,
                missing_search_paths,
                unresolved_search_paths,
                diagnostics,
            )

    for raw_path in extra_search_paths or []:
        result = _resolve_search_path(
            raw_path=raw_path,
            base_dir=project_root,
            variables=merged_variables,
            source_file=None,
        )
        if result is not None:
            _apply_resolved_path(
                result,
                scan_roots,
                configured_search_paths,
                missing_search_paths,
                unresolved_search_paths,
                diagnostics,
            )

    for project_file in project_files:
        project_path = Path(project_file)
        for raw_path in _extract_project_search_paths(project_path):
            result = _resolve_search_path(
                raw_path=raw_path,
                base_dir=project_path.parent,
                variables=merged_variables,
                source_file=project_path.as_posix(),
            )
            if result is not None:
                _apply_resolved_path(
                    result,
                    scan_roots,
                    configured_search_paths,
                    missing_search_paths,
                    unresolved_search_paths,
                    diagnostics,
                )

    unique_roots = list(dict.fromkeys(path.resolve() for path in scan_roots if path.exists()))
    return WorkspaceResolution(
        scan_roots=unique_roots,
        project_files=project_files,
        configured_search_paths=sorted(dict.fromkeys(configured_search_paths)),
        missing_search_paths=sorted(dict.fromkeys(missing_search_paths)),
        unresolved_search_paths=sorted(dict.fromkeys(unresolved_search_paths)),
        diagnostics=diagnostics,
    )


def workspace_key_for_path(path: Path, scan_roots: list[Path]) -> str:
    normalized = path.resolve()
    for root in scan_roots:
        try:
            relative = normalized.relative_to(root)
        except ValueError:
            continue
        if root == scan_roots[0]:
            return relative.as_posix().lower()
        return f"{root.name}/{relative.as_posix()}".lower()
    return normalized.name.lower()


def workspace_display_path(path: Path, scan_roots: list[Path]) -> str:
    normalized = path.resolve()
    for root in scan_roots:
        try:
            relative = normalized.relative_to(root)
        except ValueError:
            continue
        if root == scan_roots[0]:
            return relative.as_posix()
        return f"{root.name}/{relative.as_posix()}"
    return normalized.as_posix()


def _load_workspace_config(
    workspace_config_path: Path | None,
    diagnostics: list[DiagnosticRecord],
) -> dict[str, dict | list]:
    if workspace_config_path is None:
        return {"scan_roots": [], "search_paths": [], "path_variables": {}}

    try:
        payload = json.loads(workspace_config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        diagnostics.append(
            make_diagnostic(
                "error",
                "WORKSPACE_CONFIG_NOT_FOUND",
                f"Workspace config file does not exist: {workspace_config_path}",
                file_path=workspace_config_path.as_posix(),
                suggestion="Create the workspace config file or remove --workspace-config.",
                prompt_hint="List the external Delphi library paths that should be scanned and save them in a workspace config JSON file.",
            )
        )
        return {"scan_roots": [], "search_paths": [], "path_variables": {}}
    except json.JSONDecodeError as exc:
        diagnostics.append(
            make_diagnostic(
                "error",
                "WORKSPACE_CONFIG_INVALID_JSON",
                f"Workspace config JSON could not be parsed: {exc}",
                file_path=workspace_config_path.as_posix(),
                suggestion="Fix the JSON syntax in the workspace config file.",
                prompt_hint="Show the smallest valid JSON structure for scan_roots, search_paths, and path_variables.",
            )
        )
        return {"scan_roots": [], "search_paths": [], "path_variables": {}}

    if not isinstance(payload, dict):
        diagnostics.append(
            make_diagnostic(
                "error",
                "WORKSPACE_CONFIG_INVALID_ROOT",
                "Workspace config must be a JSON object.",
                file_path=workspace_config_path.as_posix(),
                suggestion="Wrap workspace settings in a top-level JSON object.",
            )
        )
        return {"scan_roots": [], "search_paths": [], "path_variables": {}}

    scan_roots = payload.get("scan_roots", [])
    search_paths = payload.get("search_paths", [])
    path_variables = payload.get("path_variables", {})
    if not isinstance(scan_roots, list) or not all(isinstance(item, str) for item in scan_roots):
        diagnostics.append(
            make_diagnostic(
                "warning",
                "WORKSPACE_CONFIG_INVALID_SCAN_ROOTS",
                "workspace_config.scan_roots must be a list of strings.",
                file_path=workspace_config_path.as_posix(),
                suggestion="Change scan_roots to an array of directory strings.",
            )
        )
        scan_roots = []
    if not isinstance(search_paths, list) or not all(isinstance(item, str) for item in search_paths):
        diagnostics.append(
            make_diagnostic(
                "warning",
                "WORKSPACE_CONFIG_INVALID_SEARCH_PATHS",
                "workspace_config.search_paths must be a list of strings.",
                file_path=workspace_config_path.as_posix(),
                suggestion="Change search_paths to an array of Delphi search-path strings.",
            )
        )
        search_paths = []
    if not isinstance(path_variables, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in path_variables.items()
    ):
        diagnostics.append(
            make_diagnostic(
                "warning",
                "WORKSPACE_CONFIG_INVALID_PATH_VARIABLES",
                "workspace_config.path_variables must map string names to string paths.",
                file_path=workspace_config_path.as_posix(),
                suggestion="Normalize path_variables to a JSON object of string keys and string values.",
            )
        )
        path_variables = {}
    return {
        "scan_roots": scan_roots,
        "search_paths": search_paths,
        "path_variables": path_variables,
    }


def _build_path_variables(
    project_root: Path,
    workspace_config_path: Path | None,
    config_variables: dict[str, str],
    cli_variables: dict[str, str],
) -> dict[str, str]:
    variables = {
        "PROJECTDIR": project_root.as_posix(),
        "CURDIR": project_root.as_posix(),
    }
    for key, value in os.environ.items():
        if isinstance(value, str):
            variables[key.upper()] = value
    base_dir = workspace_config_path.parent if workspace_config_path else project_root
    for source in (config_variables, cli_variables):
        for key, value in source.items():
            variables[key.upper()] = _resolve_variable_path(value, base_dir)
    return variables


def _resolve_variable_path(value: str, base_dir: Path) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve().as_posix()
    return (base_dir / candidate).resolve().as_posix()


def _extract_project_search_paths(project_path: Path) -> list[str]:
    if project_path.suffix.lower() == ".cfg":
        return _extract_cfg_search_paths(project_path)
    return _extract_xml_search_paths(project_path)


def _extract_xml_search_paths(project_path: Path) -> list[str]:
    text, _, _ = read_text_file(project_path)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    paths: list[str] = []
    for element in root.iter():
        local_name = element.tag.split("}", 1)[-1]
        if local_name in SEARCH_PATH_TAGS and (element.text or "").strip():
            paths.extend(_split_search_paths(element.text or ""))
    return paths


def _extract_cfg_search_paths(project_path: Path) -> list[str]:
    text, _, _ = read_text_file(project_path)
    paths: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        for prefix in CFG_PATH_PREFIXES:
            if not line.startswith(prefix):
                continue
            payload = line[len(prefix):].strip().strip('"')
            paths.extend(_split_search_paths(payload))
    return paths


def _split_search_paths(raw_value: str) -> list[str]:
    entries = []
    for item in raw_value.split(";"):
        candidate = item.strip().strip('"').strip()
        if candidate:
            entries.append(candidate)
    return entries


@dataclass(slots=True)
class _ResolvedSearchPath:
    raw_path: str
    status: str
    resolved_path: Path | None
    source_file: str | None
    unresolved_macros: list[str] = field(default_factory=list)


def _resolve_search_path(
    raw_path: str,
    base_dir: Path,
    variables: dict[str, str],
    source_file: str | None,
) -> _ResolvedSearchPath | None:
    candidate = raw_path.strip().replace("\\", "/")
    if not candidate or candidate == ".":
        return None
    if candidate.startswith("$(") and candidate.endswith(")") and candidate[2:-1].upper() in {"PLATFORM", "CONFIG"}:
        return None
    expanded, unresolved = _expand_macros(candidate, variables)
    if unresolved:
        return _ResolvedSearchPath(
            raw_path=candidate,
            status="unresolved",
            resolved_path=None,
            source_file=source_file,
            unresolved_macros=unresolved,
        )
    normalized = Path(expanded)
    if not normalized.is_absolute():
        normalized = (base_dir / normalized).resolve()
    else:
        normalized = normalized.resolve()
    if normalized.exists() and normalized.is_dir():
        return _ResolvedSearchPath(
            raw_path=candidate,
            status="ok",
            resolved_path=normalized,
            source_file=source_file,
        )
    return _ResolvedSearchPath(
        raw_path=candidate,
        status="missing",
        resolved_path=normalized,
        source_file=source_file,
    )


def _expand_macros(candidate: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    unresolved: list[str] = []

    def replacer(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = variables.get(name.upper())
        if value is None:
            unresolved.append(name)
            return match.group(0)
        return value

    return MACRO_RE.sub(replacer, candidate), sorted(dict.fromkeys(unresolved))


def _apply_resolved_path(
    result: _ResolvedSearchPath,
    scan_roots: list[Path],
    configured_search_paths: list[str],
    missing_search_paths: list[str],
    unresolved_search_paths: list[str],
    diagnostics: list[DiagnosticRecord],
) -> None:
    if result.status == "ok" and result.resolved_path is not None:
        scan_roots.append(result.resolved_path)
        configured_search_paths.append(result.resolved_path.as_posix())
        return

    if result.status == "missing" and result.resolved_path is not None:
        missing_search_paths.append(result.resolved_path.as_posix())
        diagnostics.append(
            make_diagnostic(
                _path_issue_severity(result.raw_path, result.resolved_path.as_posix(), []),
                "PROJECT_SEARCH_PATH_MISSING",
                f"Configured Delphi search path does not exist: {result.raw_path}",
                file_path=result.source_file,
                context=result.resolved_path.as_posix(),
                suggestion=(
                    "Add the missing external repository to the workspace, or pass --search-path / --workspace-config "
                    "so the analyzer can scan it."
                ),
                prompt_hint=(
                    "Provide the real directory for this Delphi search path and say whether it should be added via "
                    "--search-path, workspace_config.scan_roots, or workspace_config.path_variables."
                ),
                details={
                    "raw_path": result.raw_path,
                    "resolved_path": result.resolved_path.as_posix(),
                },
            )
        )
        return

    unresolved_search_paths.append(result.raw_path)
    diagnostics.append(
        make_diagnostic(
            _path_issue_severity(result.raw_path, None, result.unresolved_macros),
            "PROJECT_SEARCH_PATH_UNRESOLVED",
            f"Configured Delphi search path contains unresolved variables: {result.raw_path}",
            file_path=result.source_file,
            context=", ".join(result.unresolved_macros),
            suggestion=(
                "Define the missing variable with --path-var or workspace_config.path_variables, or replace it with "
                "a concrete path."
            ),
            prompt_hint=(
                "List the concrete directory value for each unresolved Delphi path variable and say which external "
                "repository it points to."
            ),
            details={
                "raw_path": result.raw_path,
                "unresolved_macros": result.unresolved_macros,
            },
        )
    )


def _path_issue_severity(
    raw_path: str,
    resolved_path: str | None,
    unresolved_macros: list[str],
) -> str:
    custom_macros = [item for item in unresolved_macros if item.upper() not in STANDARD_MACROS]
    if custom_macros:
        return "error"
    if ".." in raw_path.replace("\\", "/"):
        return "error"
    if resolved_path and any(token in resolved_path.lower() for token in ("pdss_", "/common", "/sql")):
        return "error"
    return "warning"
