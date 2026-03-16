from __future__ import annotations

from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def build_workspace_graph(analysis_dir: Path, *, output_dir: Path | None = None) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    graph_dir = (output_dir or analysis_dir / "llm-pack" / "workspace-graph").resolve()
    ensure_directory(graph_dir)

    roots = _workspace_roots(output)
    nodes = list(roots)
    edges: list[dict[str, Any]] = []

    for unit in output.pascal_units:
        node_id = f"unit:{unit.unit_name}"
        root_id = _root_id_for_path(unit.file_path, roots)
        nodes.append({"id": node_id, "type": "pascal-unit", "label": unit.unit_name, "root_id": root_id})
        edges.append({"source": root_id, "target": node_id, "relation": "contains"})
        for dependency in unit.interface_uses + unit.implementation_uses:
            edges.append({"source": node_id, "target": f"unit:{dependency}", "relation": "uses"})
        for query_name in unit.referenced_query_names:
            edges.append({"source": node_id, "target": f"query:{query_name}", "relation": "references-query"})

    for form in output.forms:
        form_name = form.root_name or Path(form.file_path).stem
        node_id = f"form:{form_name}"
        root_id = _root_id_for_path(form.file_path, roots)
        nodes.append({"id": node_id, "type": "form", "label": form_name, "root_id": root_id})
        edges.append({"source": root_id, "target": node_id, "relation": "contains"})
        if form.linked_unit:
            edges.append({"source": node_id, "target": f"unit:{form.linked_unit}", "relation": "linked-unit"})

    for xml_file in output.sql_xml_files:
        xml_name = Path(xml_file.file_path).name
        node_id = f"xml:{xml_name}"
        root_id = _root_id_for_path(xml_file.file_path, roots)
        nodes.append({"id": node_id, "type": "sql-xml", "label": xml_name, "root_id": root_id})
        edges.append({"source": root_id, "target": node_id, "relation": "contains"})

    for query in output.resolved_queries:
        node_id = f"query:{query.name}"
        nodes.append({"id": node_id, "type": "query", "label": query.name, "root_id": _root_id_for_path(query.file_path, roots)})
        edges.append({"source": f"xml:{Path(query.file_path).name}", "target": node_id, "relation": "defines-query"})

    for module in output.transition_mapping.modules:
        node_id = f"module:{module.name}"
        nodes.append({"id": node_id, "type": "module", "label": module.name})
        for unit_name in module.source_units:
            edges.append({"source": node_id, "target": f"unit:{unit_name}", "relation": "contains-unit"})
        for form_name in module.forms:
            edges.append({"source": node_id, "target": f"form:{form_name}", "relation": "contains-form"})
        for query_name in module.query_artifacts:
            edges.append({"source": node_id, "target": f"query:{query_name}", "relation": "contains-query"})

    summary = {
        "root_count": len(roots),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "cross_root_edges": _count_cross_root_edges(nodes, edges),
    }
    payload = {"analysis_dir": analysis_dir.as_posix(), "summary": summary, "nodes": nodes, "edges": edges}
    write_json(graph_dir / "workspace-graph.json", payload)
    write_text(graph_dir / "workspace-graph.dot", _render_dot(nodes, edges))
    write_text(graph_dir / "workspace-graph.md", _render_markdown(payload))
    return payload


def _workspace_roots(output: Any) -> list[dict[str, Any]]:
    roots = []
    for path in [output.inventory.project_root, *output.inventory.external_roots, *output.inventory.scan_roots]:
        if not isinstance(path, str):
            continue
        root_id = _root_id(path)
        if any(item["id"] == root_id for item in roots):
            continue
        roots.append({"id": root_id, "type": "root", "label": Path(path).name or path, "path": path})
    return roots


def _root_id(path: str) -> str:
    return f"root:{slugify(path)}"


def _root_id_for_path(path: str, roots: list[dict[str, Any]]) -> str:
    normalized = Path(path).resolve().as_posix()
    ordered_roots = sorted(
        [item for item in roots if isinstance(item.get("path"), str)],
        key=lambda item: len(str(item["path"])),
        reverse=True,
    )
    for root in ordered_roots:
        root_path = Path(str(root["path"])).resolve().as_posix()
        if normalized.startswith(root_path):
            return str(root["id"])
    return f"root:{slugify(Path(path).anchor or 'workspace')}"


def _count_cross_root_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    root_by_node = {item["id"]: item.get("root_id") for item in nodes}
    count = 0
    for edge in edges:
        source_root = root_by_node.get(edge["source"], edge["source"] if str(edge["source"]).startswith("root:") else None)
        target_root = root_by_node.get(edge["target"], edge["target"] if str(edge["target"]).startswith("root:") else None)
        if source_root and target_root and source_root != target_root:
            count += 1
    return count


def _render_dot(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    lines = ["digraph workspace {"]
    for node in nodes:
        lines.append(f'  "{node["id"]}" [label="{node["label"]}"];')
    for edge in edges:
        lines.append(f'  "{edge["source"]}" -> "{edge["target"]}" [label="{edge["relation"]}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Workspace Knowledge Graph",
        "",
        f"- Roots: {payload['summary']['root_count']}",
        f"- Nodes: {payload['summary']['node_count']}",
        f"- Edges: {payload['summary']['edge_count']}",
        f"- Cross-root edges: {payload['summary']['cross_root_edges']}",
        "",
        "## Roots",
        "",
    ]
    for node in payload["nodes"]:
        if node["type"] != "root":
            continue
        lines.append(f"- {node['label']}: `{node.get('path', '')}`")
    lines.extend(["", "## Modules", ""])
    for node in payload["nodes"]:
        if node["type"] != "module":
            continue
        lines.append(f"- {node['label']}")
    return "\n".join(lines).strip() + "\n"
