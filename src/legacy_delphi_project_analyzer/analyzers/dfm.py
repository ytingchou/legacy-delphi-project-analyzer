from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from legacy_delphi_project_analyzer.models import ComponentSummary, DiagnosticRecord, FormSummary
from legacy_delphi_project_analyzer.utils import is_binary_dfm, make_diagnostic, read_text_file


OBJECT_RE = re.compile(r"^\s*(object|inherited|inline)\s+([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_.]+)")
PROPERTY_RE = re.compile(r"^\s*([A-Za-z0-9_.]+)\s*=\s*(.+)$")
INTERESTING_PROPERTIES = {
    "Caption",
    "Hint",
    "DataSource",
    "DataField",
    "FieldName",
    "SQL",
    "Text",
    "StoredProcName",
}
COMPONENT_NAME_PREFIXES = (
    "frm",
    "btn",
    "lbl",
    "edt",
    "txt",
    "grd",
    "grid",
    "qry",
    "tbl",
    "ds",
    "db",
    "pnl",
    "tab",
    "cmb",
    "chk",
    "rad",
    "act",
    "img",
    "mem",
)


def analyze_dfm_file(path: Path) -> tuple[FormSummary, list[DiagnosticRecord]]:
    if is_binary_dfm(path):
        return _analyze_binary_dfm_file(path)
    return _analyze_text_dfm_file(path)


def _analyze_text_dfm_file(path: Path) -> tuple[FormSummary, list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    text, _, decode_failed = read_text_file(path)
    if decode_failed:
        diagnostics.append(
            make_diagnostic(
                "warning",
                "DFM_DECODE_FALLBACK",
                "Decoded text DFM with replacement characters.",
                file_path=path.as_posix(),
                suggestion="Re-run after confirming the DFM file encoding.",
            )
        )

    stack: list[dict] = []
    components: list[ComponentSummary] = []
    captions: list[str] = []
    datasets: set[str] = set()
    event_bindings: dict[str, str] = {}

    for line in text.splitlines():
        object_match = OBJECT_RE.match(line)
        if object_match:
            name = object_match.group(2)
            component_type = object_match.group(3)
            parent_path = "/".join(item["name"] for item in stack)
            component_path = "/".join(filter(None, [parent_path, name]))
            current = {
                "name": name,
                "component_type": component_type,
                "path": component_path,
                "properties": {},
                "events": {},
            }
            stack.append(current)
            continue

        if line.strip() == "end":
            if stack:
                current = stack.pop()
                components.append(
                    ComponentSummary(
                        name=current["name"],
                        component_type=current["component_type"],
                        path=current["path"],
                        properties=current["properties"],
                        events=current["events"],
                    )
                )
            continue

        property_match = PROPERTY_RE.match(line)
        if stack and property_match:
            key = property_match.group(1)
            value = _clean_value(property_match.group(2))
            current = stack[-1]
            if key.startswith("On"):
                current["events"][key] = value
                event_bindings[f"{current['path']}.{key}"] = value
            elif key in INTERESTING_PROPERTIES:
                current["properties"][key] = value
                if key == "Caption":
                    captions.append(value)
                if key in {"DataSource", "DataField"}:
                    datasets.add(value)

    root_name = components[-1].name if components else None
    root_type = components[-1].component_type if components else None
    return (
        FormSummary(
            file_path=path.as_posix(),
            root_name=root_name,
            root_type=root_type,
            captions=sorted(set(item for item in captions if item)),
            datasets=sorted(datasets),
            components=list(reversed(components)),
            event_bindings=event_bindings,
            is_binary=False,
            parse_mode="text",
        ),
        diagnostics,
    )


def _analyze_binary_dfm_file(path: Path) -> tuple[FormSummary, list[DiagnosticRecord]]:
    raw = path.read_bytes()
    tokens = _extract_binary_tokens(raw)
    captions: list[str] = []
    datasets: set[str] = set()
    event_bindings: dict[str, str] = {}
    components: list[dict] = []
    root_name: str | None = None
    root_type: str | None = None
    current_component: dict | None = None

    index = 0
    while index < len(tokens):
        token = tokens[index]
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        pair = _match_component_pair(token, next_token)
        if pair:
            name, component_type = pair
            path_value = name if not root_name else "/".join([root_name, name]) if name != root_name else name
            current_component = {
                "name": name,
                "component_type": component_type,
                "path": path_value,
                "properties": {},
                "events": {},
            }
            components.append(current_component)
            if root_name is None:
                root_name = name
                root_type = component_type
                current_component["path"] = name
            index += 2
            continue

        if current_component and (
            token.startswith("On") or token in INTERESTING_PROPERTIES
        ) and next_token:
            if token.startswith("On"):
                current_component["events"][token] = next_token
                event_bindings[f"{current_component['path']}.{token}"] = next_token
            else:
                current_component["properties"][token] = next_token
                if token == "Caption":
                    captions.append(next_token)
                if token in {"DataSource", "DataField"}:
                    datasets.add(next_token)
            index += 2
            continue

        index += 1

    if root_name is None or root_type is None:
        inferred_type = next((item for item in tokens if _is_likely_component_type(item)), None)
        inferred_name = next((item for item in tokens if _is_likely_component_name(item)), None)
        root_name = inferred_name
        root_type = inferred_type

    component_summaries = [
        ComponentSummary(
            name=item["name"],
            component_type=item["component_type"],
            path=item["path"],
            properties=item["properties"],
            events=item["events"],
        )
        for item in components
    ]

    diagnostics = [
        make_diagnostic(
            "warning",
            "DFM_BINARY_HEURISTIC",
            "Binary DFM was parsed with a heuristic token scanner.",
            file_path=path.as_posix(),
            suggestion="If component hierarchy looks incomplete, export the DFM as text for a higher-fidelity rerun.",
            prompt_hint=(
                "Explain which missing form components or events from this binary DFM should be added "
                "as analyzer overrides."
            ),
            details={"token_count": len(tokens)},
        )
    ]

    return (
        FormSummary(
            file_path=path.as_posix(),
            root_name=root_name,
            root_type=root_type,
            captions=sorted(set(item for item in captions if item)),
            datasets=sorted(datasets),
            components=component_summaries,
            event_bindings=event_bindings,
            is_binary=True,
            parse_mode="binary-heuristic",
            parse_notes=[
                f"Recovered {len(component_summaries)} component candidates from {len(tokens)} binary tokens."
            ],
        ),
        diagnostics,
    )


def _extract_binary_tokens(raw: bytes) -> list[str]:
    tokens_with_offsets: list[tuple[int, str]] = []
    tokens_with_offsets.extend(_extract_ascii_tokens(raw))
    tokens_with_offsets.extend(_extract_utf16le_tokens(raw))
    ordered: OrderedDict[str, int] = OrderedDict()
    for offset, token in sorted(tokens_with_offsets, key=lambda item: item[0]):
        normalized = token.strip()
        if len(normalized) < 3 or normalized == "TPF0":
            continue
        ordered.setdefault(normalized, offset)
    return list(ordered.keys())


def _extract_ascii_tokens(raw: bytes) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    start: int | None = None
    for index, byte in enumerate(raw):
        if 32 <= byte <= 126:
            if start is None:
                start = index
            continue
        if start is not None and index - start >= 3:
            tokens.append((start, raw[start:index].decode("latin-1")))
        start = None
    if start is not None and len(raw) - start >= 3:
        tokens.append((start, raw[start:].decode("latin-1")))
    return tokens


def _extract_utf16le_tokens(raw: bytes) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    index = 0
    while index + 1 < len(raw):
        if 32 <= raw[index] <= 126 and raw[index + 1] == 0:
            start = index
            chars = bytearray()
            while index + 1 < len(raw) and 32 <= raw[index] <= 126 and raw[index + 1] == 0:
                chars.append(raw[index])
                index += 2
            if len(chars) >= 3:
                tokens.append((start, chars.decode("latin-1")))
            continue
        index += 1
    return tokens


def _match_component_pair(token: str, next_token: str | None) -> tuple[str, str] | None:
    if not next_token:
        return None
    if _is_likely_component_name(token) and _is_likely_component_type(next_token):
        return token, next_token
    if _is_likely_component_type(token) and _is_likely_component_name(next_token):
        return next_token, token
    return None


def _is_likely_component_name(token: str) -> bool:
    if token.startswith("On") or token in INTERESTING_PROPERTIES:
        return False
    lower = token.lower()
    if any(lower.startswith(prefix) for prefix in COMPONENT_NAME_PREFIXES):
        return True
    return token[:1].islower() and token.replace("_", "").isalnum()


def _is_likely_component_type(token: str) -> bool:
    return (
        token.startswith("T")
        and len(token) > 1
        and token[1].isalpha()
        and token not in INTERESTING_PROPERTIES
    )


def _clean_value(value: str) -> str:
    result = value.strip()
    if result.startswith("'") and result.endswith("'"):
        result = result[1:-1].replace("''", "'")
    return result
