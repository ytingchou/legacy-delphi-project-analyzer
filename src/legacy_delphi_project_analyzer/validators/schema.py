from __future__ import annotations

from typing import Any


def validate_schema(payload: Any, schema: dict[str, Any]) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict):
        return False, ["Response is not a JSON object."]
    if not isinstance(schema, dict) or not schema:
        return True, []

    issues: list[str] = []
    for key, expected in schema.items():
        if key not in payload:
            issues.append(f"Missing required key: {key}")
            continue
        issues.extend(_validate_value(payload[key], expected, key))
    return not issues, issues


def _validate_value(value: Any, expected: Any, path: str) -> list[str]:
    issues: list[str] = []
    if isinstance(expected, str):
        issues.extend(_validate_scalar(value, expected, path))
    elif isinstance(expected, list):
        if not isinstance(value, list):
            issues.append(f"{path} should be an array.")
            return issues
        if expected:
            inner_expected = expected[0]
            for index, item in enumerate(value):
                issues.extend(_validate_value(item, inner_expected, f"{path}[{index}]"))
    elif isinstance(expected, dict):
        if not isinstance(value, dict):
            issues.append(f"{path} should be an object.")
            return issues
        if expected:
            if all(key in value for key in expected.keys()):
                for key, inner_expected in expected.items():
                    issues.extend(_validate_value(value.get(key), inner_expected, f"{path}.{key}"))
            else:
                sample_expected = next(iter(expected.values()))
                for key, item in value.items():
                    if not isinstance(key, str):
                        issues.append(f"{path} contains a non-string key.")
                        continue
                    issues.extend(_validate_value(item, sample_expected, f"{path}.{key}"))
    return issues


def _validate_scalar(value: Any, expected: str, path: str) -> list[str]:
    expected = expected.strip()
    if "|" in expected:
        if not isinstance(value, str) or value not in expected.split("|"):
            return [f"{path} should be one of: {expected}."]
        return []

    normalized = expected.lower()
    if normalized == "string":
        return [] if isinstance(value, str) else [f"{path} should be a string."]
    if normalized in {"number", "float"}:
        return [] if isinstance(value, (int, float)) and not isinstance(value, bool) else [f"{path} should be a number."]
    if normalized in {"integer", "int"}:
        return [] if isinstance(value, int) and not isinstance(value, bool) else [f"{path} should be an integer."]
    if normalized in {"boolean", "bool"}:
        return [] if isinstance(value, bool) else [f"{path} should be a boolean."]
    if normalized in {"object", "dict"}:
        return [] if isinstance(value, dict) else [f"{path} should be an object."]
    return [] if isinstance(value, str) else [f"{path} should be a string."]
