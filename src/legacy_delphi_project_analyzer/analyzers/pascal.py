from __future__ import annotations

import re
from pathlib import Path

from legacy_delphi_project_analyzer.models import DiagnosticRecord, PascalClassSummary, PascalUnitSummary
from legacy_delphi_project_analyzer.utils import (
    PLACEHOLDER_RE,
    make_diagnostic,
    read_text_file,
    trim_sql_snippet,
)


UNIT_RE = re.compile(r"(?im)^\s*unit\s+([A-Za-z0-9_]+)\s*;")
CLASS_RE = re.compile(r"(?im)^\s*([A-Za-z0-9_]+)\s*=\s*class\s*(?:\(([^)]*)\))?")
CLASS_BLOCK_RE = re.compile(
    r"(?is)\b([A-Za-z0-9_]+)\s*=\s*class\s*(?:\(([^)]*)\))?(.*?)\bend\s*;"
)
METHOD_RE = re.compile(
    r"(?im)^\s*(?:class\s+)?(?:procedure|function|constructor|destructor)\s+([A-Za-z0-9_.]+)\s*(\([^)]*\))?"
)
USES_RE = re.compile(r"\buses\s+([^;]+);", re.IGNORECASE | re.DOTALL)
STRING_RE = re.compile(r"'(?:''|[^'])*'")
LOAD_SQL_RE = re.compile(
    r"(?is)\b(?:loadsql|getsql)\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)"
)
XML_LITERAL_RE = re.compile(r"[\w./-]+\.xml", re.IGNORECASE)
FIELD_RE = re.compile(r"^\s*([A-Za-z0-9_,\s]+)\s*:\s*([A-Za-z0-9_.<>]+)\s*;\s*$")
PROPERTY_RE = re.compile(r"^\s*property\s+([A-Za-z0-9_]+)\b", re.IGNORECASE)
SECTION_RE = re.compile(r"^\s*(private|protected|public|published|automated)\b", re.IGNORECASE)
EVENT_NAME_HINT_RE = re.compile(r"(click|change|exit|enter|close|open|keydown|keyup|create|show)$", re.I)


def analyze_pascal_file(path: Path) -> tuple[PascalUnitSummary, list[DiagnosticRecord]]:
    diagnostics: list[DiagnosticRecord] = []
    text, _, decode_failed = read_text_file(path)
    if decode_failed:
        diagnostics.append(
            make_diagnostic(
                "warning",
                "PAS_DECODE_FALLBACK",
                "Decoded Pascal file with replacement characters.",
                file_path=path.as_posix(),
                suggestion="Re-run after confirming the Pascal source encoding.",
            )
        )

    structural_text = _strip_pascal_comments(text)
    unit_match = UNIT_RE.search(structural_text)
    unit_name = unit_match.group(1) if unit_match else path.stem
    interface_text, _, implementation_text = structural_text.partition("implementation")

    interface_uses = _extract_uses(interface_text)
    implementation_uses = _extract_uses(implementation_text)
    classes = [
        PascalClassSummary(name=match.group(1), ancestor=_clean(match.group(2)))
        for match in CLASS_RE.finditer(structural_text)
    ]
    form_classes = [
        item.name
        for item in classes
        if item.ancestor and "form" in item.ancestor.lower()
    ]

    published_fields: set[str] = set()
    published_properties: set[str] = set()
    component_fields: set[str] = set()
    for match in CLASS_BLOCK_RE.finditer(structural_text):
        block_fields, block_properties, block_components = _extract_class_members(match.group(3))
        published_fields.update(block_fields)
        published_properties.update(block_properties)
        component_fields.update(block_components)

    methods = []
    event_handlers = []
    for match in METHOD_RE.finditer(structural_text):
        name = match.group(1)
        methods.append(name)
        signature = match.group(2) or ""
        simple_name = name.split(".")[-1]
        if (
            "Sender:" in signature
            or "TObject" in signature
            or EVENT_NAME_HINT_RE.search(simple_name)
            or simple_name.lower().startswith(("btn", "act", "menu", "mi", "action"))
        ):
            event_handlers.append(name)

    string_literals = [_unquote(item.group(0)) for item in STRING_RE.finditer(text)]
    sql_hints = []
    xml_references = []
    for literal in string_literals:
        normalized = literal.strip()
        if re.search(r"\b(select|insert|update|delete|merge|from|where)\b", normalized, re.I):
            sql_hints.append(trim_sql_snippet(normalized))
        xml_references.extend(XML_LITERAL_RE.findall(normalized))

    replace_tokens = sorted(
        {match.group(1) for match in PLACEHOLDER_RE.finditer(text) if match.group(1)}
    )
    load_sql_calls = list(LOAD_SQL_RE.finditer(text))
    referenced_query_names = sorted({match.group(2) for match in load_sql_calls})
    xml_references.extend(match.group(1) for match in load_sql_calls)

    return (
        PascalUnitSummary(
            unit_name=unit_name,
            file_path=path.as_posix(),
            interface_uses=sorted(set(interface_uses)),
            implementation_uses=sorted(set(implementation_uses)),
            classes=classes,
            form_classes=sorted(set(form_classes)),
            methods=sorted(set(methods)),
            event_handlers=sorted(set(event_handlers)),
            published_fields=sorted(published_fields),
            published_properties=sorted(published_properties),
            component_fields=sorted(component_fields),
            sql_hints=sorted(set(sql_hints)),
            xml_references=sorted(set(item.lower() for item in xml_references)),
            replace_tokens=replace_tokens,
            referenced_query_names=referenced_query_names,
        ),
        diagnostics,
    )


def _extract_uses(section: str) -> list[str]:
    matches = USES_RE.findall(section)
    units: list[str] = []
    for match in matches:
        cleaned = re.sub(r"\bin\s+'[^']+'", "", match, flags=re.IGNORECASE)
        for item in cleaned.split(","):
            candidate = item.strip()
            if candidate:
                units.append(candidate)
    return units


def _extract_class_members(block: str) -> tuple[set[str], set[str], set[str]]:
    published_fields: set[str] = set()
    published_properties: set[str] = set()
    component_fields: set[str] = set()
    current_section = "private"
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section_match = SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1).lower()
            continue
        property_match = PROPERTY_RE.match(line)
        if property_match:
            if current_section in {"published", "public"}:
                published_properties.add(property_match.group(1))
            continue
        if line.lower().startswith(
            ("procedure ", "function ", "constructor ", "destructor ", "class ")
        ):
            continue
        field_match = FIELD_RE.match(line)
        if not field_match or current_section not in {"published", "public"}:
            continue
        field_names = [
            item.strip()
            for item in field_match.group(1).split(",")
            if item.strip()
        ]
        field_type = field_match.group(2)
        for field_name in field_names:
            published_fields.add(field_name)
            if field_type.startswith("T"):
                component_fields.add(field_name)
    return published_fields, published_properties, component_fields


def _strip_pascal_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    state = "default"
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "default":
            if char == "'" and state == "default":
                result.append(char)
                state = "string"
            elif char == "{" and nxt != "$":
                result.append(" ")
                state = "brace"
            elif char == "(" and nxt == "*":
                result.append(" ")
                result.append(" ")
                index += 1
                state = "paren"
            elif char == "/" and nxt == "/":
                result.append(" ")
                result.append(" ")
                index += 1
                state = "line"
            else:
                result.append(char)
        elif state == "string":
            result.append(char)
            if char == "'" and nxt == "'":
                result.append(nxt)
                index += 1
            elif char == "'":
                state = "default"
        elif state == "brace":
            result.append("\n" if char == "\n" else " ")
            if char == "}":
                state = "default"
        elif state == "paren":
            result.append("\n" if char == "\n" else " ")
            if char == "*" and nxt == ")":
                result.append(" ")
                index += 1
                state = "default"
        elif state == "line":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "default"
        index += 1
    return "".join(result)


def _unquote(value: str) -> str:
    return value[1:-1].replace("''", "'")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip()
    return result or None
