from __future__ import annotations

from typing import Any


def validate_evidence(taskpack: Any, payload: dict[str, Any], analysis_output: Any) -> tuple[bool, list[str], list[str], list[str]]:
    issues: list[str] = []
    supported: list[str] = []
    missing: list[str] = []

    known_modules = {item.module_name for item in analysis_output.transition_specs}
    known_queries = {item.name for item in analysis_output.resolved_queries}
    known_pages = {
        (spec.module_name, page.name): page.route_path
        for spec in analysis_output.transition_specs
        for page in spec.frontend_pages
    }
    known_page_names = {page_name for _, page_name in known_pages}
    known_endpoints = {
        (spec.module_name, endpoint.name): f"{endpoint.method} {endpoint.path}"
        for spec in analysis_output.transition_specs
        for endpoint in spec.backend_endpoints
    }
    known_endpoint_names = {endpoint_name for _, endpoint_name in known_endpoints}
    known_endpoint_contracts = set(known_endpoints.values())
    known_dtos = {
        dto.name
        for spec in analysis_output.transition_specs
        for dto in spec.dtos
    }
    known_feature_dirs = {
        item.target_feature_dir
        for item in getattr(analysis_output, "ui_integration_artifacts", [])
    }

    task_type = str(getattr(taskpack, "task_type", "") or "")
    module_name = payload.get("module_name")
    module_required = task_type in {
        "validate_transition_spec",
        "propose_smallest_transition_slice",
        "generate_bff_oracle_sql_logic",
        "generate_react_pseudo_ui",
        "generate_react_reference_ui",
        "integrate_react_transition_ui",
        "summarize_form_behavior",
    }
    if isinstance(module_name, str):
        if module_name in known_modules:
            supported.append(f"module:{module_name}")
        else:
            issues.append(f"Unknown module_name: {module_name}")
    elif module_required:
        missing.append("module_name")

    if task_type in {"classify_query_intent", "infer_placeholder_meaning"}:
        _check_known_string(payload.get("query_name"), known_queries, "query_name", supported, issues, missing)
    elif task_type == "validate_transition_spec":
        _check_known_items(payload.get("supported_pages"), known_page_names, "supported_pages", supported, issues)
        _check_known_items(payload.get("supported_endpoints"), known_endpoint_names | known_endpoint_contracts, "supported_endpoints", supported, issues)
    elif task_type == "summarize_form_behavior":
        _check_known_items(payload.get("likely_queries"), known_queries, "likely_queries", supported, issues)
    elif task_type == "propose_smallest_transition_slice":
        _check_known_items(payload.get("react_pages"), known_page_names, "react_pages", supported, issues)
        _check_known_items(payload.get("spring_endpoints"), known_endpoint_names | known_endpoint_contracts, "spring_endpoints", supported, issues)
    elif task_type == "generate_bff_oracle_sql_logic":
        _check_known_string(payload.get("endpoint_name"), known_endpoint_names, "endpoint_name", supported, issues, missing)
        controller_contract = payload.get("controller_contract")
        if isinstance(controller_contract, str):
            if any(contract in controller_contract for contract in known_endpoint_contracts):
                supported.append(f"controller_contract:{controller_contract}")
            else:
                issues.append("controller_contract does not reference a known endpoint contract.")
        _check_known_items(payload.get("dto_mapping"), known_dtos, "dto_mapping", supported, issues, allow_substring=True)
    elif task_type in {"generate_react_pseudo_ui", "generate_react_reference_ui", "integrate_react_transition_ui"}:
        _check_known_string(payload.get("page_name"), known_page_names, "page_name", supported, issues, missing)
        _check_known_items(payload.get("data_dependencies"), known_endpoint_names | known_endpoint_contracts | known_queries, "data_dependencies", supported, issues)
        _check_known_items(payload.get("api_bindings"), known_endpoint_names | known_endpoint_contracts, "api_bindings", supported, issues)
        _check_known_items(payload.get("api_client_contracts"), known_endpoint_names | known_endpoint_contracts, "api_client_contracts", supported, issues)
        feature_dir = payload.get("target_feature_dir")
        if isinstance(feature_dir, str) and feature_dir:
            if feature_dir in known_feature_dirs:
                supported.append(f"feature_dir:{feature_dir}")
            else:
                issues.append(f"Unknown target_feature_dir: {feature_dir}")
    return not issues, supported, issues, missing


def _check_known_string(
    value: Any,
    known: set[str],
    label: str,
    supported: list[str],
    issues: list[str],
    missing: list[str],
) -> None:
    if isinstance(value, str) and value:
        if value in known:
            supported.append(f"{label}:{value}")
        else:
            issues.append(f"Unknown {label}: {value}")
    else:
        missing.append(label)


def _check_known_items(
    values: Any,
    known: set[str],
    label: str,
    supported: list[str],
    issues: list[str],
    *,
    allow_substring: bool = False,
) -> None:
    if values is None:
        return
    if not isinstance(values, list):
        issues.append(f"{label} should be an array.")
        return
    for item in values:
        if not isinstance(item, str) or not item:
            issues.append(f"{label} contains a non-string item.")
            continue
        if item in known or (allow_substring and any(candidate in item for candidate in known)):
            supported.append(f"{label}:{item}")
        else:
            issues.append(f"{label} references unknown item: {item}")
