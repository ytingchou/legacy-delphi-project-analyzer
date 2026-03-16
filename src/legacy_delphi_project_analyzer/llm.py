from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import LlmRunArtifact
from legacy_delphi_project_analyzer.utils import estimate_tokens, read_text_file, slugify, write_json, write_text


def validate_openai_compatible_provider(
    *,
    provider_base_url: str,
    model: str | None = None,
    api_key: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    timeout_seconds: int = 30,
    perform_completion: bool = True,
) -> dict[str, Any]:
    resolved_api_key = api_key or os.environ.get(api_key_env)
    models_endpoint = _normalize_models_url(provider_base_url)
    chat_endpoint = _normalize_chat_completion_url(provider_base_url)
    result: dict[str, Any] = {
        "provider_base_url": provider_base_url,
        "models_endpoint": models_endpoint,
        "chat_endpoint": chat_endpoint,
        "auth_configured": bool(resolved_api_key),
        "requested_model": model,
        "listed_models": [],
        "models_ok": False,
        "completion_ok": False,
        "selected_model": None,
        "response_preview": None,
        "response_format": None,
        "response_content_type": None,
        "debug": [],
        "ok": False,
    }
    try:
        models_payload = _request_provider_json(
            endpoint=models_endpoint,
            api_key=resolved_api_key,
            payload=None,
            timeout_seconds=timeout_seconds,
        )
        model_ids = _extract_model_ids(models_payload)
        result["listed_models"] = model_ids
        result["models_ok"] = True
        result["debug"].append(f"Models endpoint reachable: {models_endpoint}")
    except ValueError as exc:
        result["debug"].append(str(exc))
        if not perform_completion:
            return result
        model_ids = []

    selected_model = model or (model_ids[0] if model_ids else None)
    result["selected_model"] = selected_model
    if model and result["listed_models"] and model not in result["listed_models"]:
        result["debug"].append(f"Requested model `{model}` was not listed by the provider.")
    if perform_completion and selected_model:
        sample_payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only."},
                {"role": "user", "content": 'Return {"ok": true, "provider": "validated"}.'},
            ],
            "temperature": 0.0,
            "max_tokens": 64,
        }
        try:
            completion_payload = _request_provider_json(
                endpoint=chat_endpoint,
                api_key=resolved_api_key,
                payload=sample_payload,
                timeout_seconds=timeout_seconds,
            )
            response_text = _extract_response_text(completion_payload)
            result["completion_ok"] = True
            result["response_preview"] = response_text[:240]
            result["response_format"] = completion_payload.get("_response_format")
            result["response_content_type"] = completion_payload.get("_response_content_type")
            result["debug"].append(f"Chat completion endpoint reachable: {chat_endpoint}")
            if result["response_content_type"]:
                result["debug"].append(f"Response content type: {result['response_content_type']}")
        except ValueError as exc:
            result["debug"].append(str(exc))
    elif perform_completion and not selected_model:
        result["debug"].append("Could not determine a model for the sample completion probe.")

    result["ok"] = bool(result["models_ok"] and (result["completion_ok"] or not perform_completion))
    return result


def run_llm_artifact(
    *,
    analysis_dir: Path,
    prompt_name: str | None = None,
    failure_name: str | None = None,
    artifact_json_path: Path | None = None,
    provider_base_url: str,
    model: str,
    api_key: str | None = None,
    api_key_env: str = "OPENAI_API_KEY",
    prompt_mode: str = "primary",
    token_limit: int | None = None,
    output_token_limit: int = 1200,
    temperature: float = 0.1,
    timeout_seconds: int = 120,
) -> LlmRunArtifact:
    analysis_dir = analysis_dir.resolve()
    artifact_payload, artifact_kind = _load_artifact_payload(
        analysis_dir=analysis_dir,
        prompt_name=prompt_name,
        failure_name=failure_name,
        artifact_json_path=artifact_json_path,
    )
    artifact_name = str(artifact_payload.get("name") or "artifact")
    selected_prompt = _select_prompt(artifact_payload, prompt_mode)
    target_model = artifact_payload.get("target_model")
    goal = artifact_payload.get("goal")
    expected_response_schema = artifact_payload.get("expected_response_schema", {})
    acceptance_checks = artifact_payload.get("acceptance_checks", [])
    input_limit = token_limit or int(artifact_payload.get("context_budget_tokens") or 6000)
    context_bundle = _build_context_bundle(
        context_paths=[str(item) for item in artifact_payload.get("context_paths", []) if isinstance(item, str)],
        token_limit=input_limit,
    )
    user_message = _build_user_message(
        artifact_payload=artifact_payload,
        prompt_mode=prompt_mode,
        selected_prompt=selected_prompt,
        context_sections=context_bundle["sections"],
        expected_response_schema=expected_response_schema if isinstance(expected_response_schema, dict) else {},
        acceptance_checks=acceptance_checks if isinstance(acceptance_checks, list) else [],
    )
    request_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise legacy modernization assistant. "
                    "Use only the provided context. If evidence is missing, say so explicitly. "
                    "Follow the requested schema strictly."
                ),
            },
            {
                "role": "user",
                "content": user_message,
            },
        ],
        "temperature": temperature,
        "max_tokens": output_token_limit,
    }
    request_tokens_estimate = estimate_tokens(json.dumps(request_payload, ensure_ascii=False))
    response_payload = _call_openai_compatible_provider(
        provider_base_url=provider_base_url,
        api_key=api_key or os.environ.get(api_key_env),
        payload=request_payload,
        timeout_seconds=timeout_seconds,
    )
    response_text = _extract_response_text(response_payload)
    run_id = f"{slugify(artifact_name)}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = analysis_dir / "llm-runs"
    run_json_path = run_dir / f"{run_id}.json"
    run_md_path = run_dir / f"{run_id}.md"
    feedback_template_path = run_dir / f"{run_id}-feedback-template.json"

    run_artifact = LlmRunArtifact(
        run_id=run_id,
        artifact_kind=artifact_kind,
        artifact_name=artifact_name,
        prompt_mode=prompt_mode,
        provider_base_url=provider_base_url,
        model=model,
        target_model=str(target_model) if isinstance(target_model, str) else None,
        goal=str(goal) if isinstance(goal, str) else None,
        input_token_limit=input_limit,
        output_token_limit=output_token_limit,
        temperature=temperature,
        request_tokens_estimate=request_tokens_estimate,
        included_context_paths=context_bundle["included_paths"],
        skipped_context_paths=context_bundle["skipped_paths"],
        response_text=response_text,
        parsed_response=_parse_response_json(response_text),
        usage=response_payload.get("usage", {}) if isinstance(response_payload.get("usage"), dict) else {},
        raw_response=response_payload,
        request_payload=request_payload,
        feedback_template_path=feedback_template_path.as_posix(),
    )
    write_json(run_json_path, run_artifact)
    write_text(run_md_path, _render_run_markdown(run_artifact))
    write_json(
        feedback_template_path,
        {
            "entries": [
                {
                    "prompt_name": artifact_name,
                    "status": "needs_follow_up",
                    "used_fallback": prompt_mode == "fallback",
                    "target_model": model,
                    "notes": "Review the response, then change status to accepted/rejected and edit response if needed.",
                    "response": run_artifact.parsed_response or {},
                }
            ]
        },
    )
    return run_artifact


def _load_artifact_payload(
    *,
    analysis_dir: Path,
    prompt_name: str | None,
    failure_name: str | None,
    artifact_json_path: Path | None,
) -> tuple[dict[str, Any], str]:
    chosen = [item for item in (prompt_name, failure_name, artifact_json_path) if item is not None]
    if len(chosen) != 1:
        raise ValueError("Specify exactly one of prompt_name, failure_name, or artifact_json_path.")

    if artifact_json_path is not None:
        path = artifact_json_path if artifact_json_path.is_absolute() else analysis_dir / artifact_json_path
        payload = _read_json_file(path.resolve())
        repro_bundle_path = payload.get("repro_bundle_path")
        if isinstance(repro_bundle_path, str) and repro_bundle_path:
            payload = _read_json_file(Path(repro_bundle_path))
        return payload, "artifact-json"

    if prompt_name is not None:
        payload = _find_named_payload(
            base_dir=analysis_dir / "prompt-pack",
            name=prompt_name,
        )
        return payload, "prompt-pack"

    payload = _find_named_payload(
        base_dir=analysis_dir / "failure-cases",
        name=failure_name or "",
    )
    return payload, "failure-triage"


def _find_named_payload(base_dir: Path, name: str) -> dict[str, Any]:
    if not base_dir.exists():
        raise ValueError(f"Artifact directory does not exist: {base_dir}")
    normalized = name.strip().lower()
    for path in sorted(base_dir.glob("*.json")):
        payload = _read_json_file(path)
        payload_name = str(payload.get("name") or "").strip().lower()
        if payload_name == normalized:
            repro_bundle_path = payload.get("repro_bundle_path")
            if isinstance(repro_bundle_path, str) and repro_bundle_path:
                return _read_json_file(Path(repro_bundle_path))
            return payload
    raise ValueError(f"Could not find artifact named '{name}' under {base_dir}")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Artifact JSON does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Artifact JSON is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Artifact JSON must contain an object: {path}")
    return payload


def _select_prompt(payload: dict[str, Any], prompt_mode: str) -> str:
    if prompt_mode == "primary":
        value = payload.get("primary_prompt") or payload.get("prompt")
    elif prompt_mode == "fallback":
        value = payload.get("fallback_prompt")
    elif prompt_mode == "verification":
        value = payload.get("verification_prompt")
    else:
        raise ValueError(f"Unsupported prompt mode: {prompt_mode}")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Artifact does not contain a usable {prompt_mode} prompt.")
    return value.strip()


def _build_context_bundle(
    *,
    context_paths: list[str],
    token_limit: int,
) -> dict[str, list[str]]:
    sections: list[str] = []
    included_paths: list[str] = []
    skipped_paths: list[str] = []
    current_tokens = 0

    for raw_path in context_paths:
        path = Path(raw_path)
        if not path.exists():
            skipped_paths.append(raw_path)
            continue
        content, _, _ = read_text_file(path)
        rendered, rendered_tokens = _render_context_section(path, content, max(256, token_limit - current_tokens))
        if sections and current_tokens + rendered_tokens > token_limit:
            skipped_paths.append(raw_path)
            continue
        sections.append(rendered)
        included_paths.append(raw_path)
        current_tokens += rendered_tokens
        if current_tokens >= token_limit:
            break

    remaining = [item for item in context_paths if item not in included_paths and item not in skipped_paths]
    skipped_paths.extend(remaining)
    return {
        "sections": sections,
        "included_paths": included_paths,
        "skipped_paths": skipped_paths,
    }


def _render_context_section(path: Path, content: str, token_budget: int) -> tuple[str, int]:
    header = f"### Context File: {path.as_posix()}\n"
    header_tokens = estimate_tokens(header)
    if header_tokens >= token_budget:
        section = header + "\n[content omitted due to token budget]\n"
        return section, estimate_tokens(section)

    lines: list[str] = []
    current_tokens = header_tokens
    for line in content.splitlines():
        line_text = line + "\n"
        line_tokens = estimate_tokens(line_text)
        if lines and current_tokens + line_tokens > token_budget:
            lines.append("[truncated due to token budget]\n")
            break
        if not lines and current_tokens + line_tokens > token_budget:
            trimmed = line[: max(0, (token_budget - current_tokens) * 4 - 32)] + "..."
            lines.append(trimmed + "\n")
            lines.append("[truncated due to token budget]\n")
            break
        lines.append(line_text)
        current_tokens += line_tokens
    section = header + "```text\n" + "".join(lines) + "```\n"
    return section, estimate_tokens(section)


def _build_user_message(
    *,
    artifact_payload: dict[str, Any],
    prompt_mode: str,
    selected_prompt: str,
    context_sections: list[str],
    expected_response_schema: dict[str, Any],
    acceptance_checks: list[str],
) -> str:
    lines = [
        f"Artifact name: {artifact_payload.get('name', 'unknown')}",
        f"Goal: {artifact_payload.get('goal', 'unknown')}",
        f"Prompt mode: {prompt_mode}",
        f"Issue summary: {artifact_payload.get('issue_summary') or artifact_payload.get('summary') or artifact_payload.get('objective') or 'None'}",
        "",
        "Primary task:",
        selected_prompt,
        "",
    ]
    if acceptance_checks:
        lines.append("Acceptance checks:")
        lines.extend(f"- {item}" for item in acceptance_checks if isinstance(item, str))
        lines.append("")
    if expected_response_schema:
        lines.append("Expected response schema:")
        lines.append(json.dumps(expected_response_schema, indent=2, ensure_ascii=False))
        lines.append("")
    if context_sections:
        lines.append("Context artifacts:")
        lines.append("")
        lines.extend(context_sections)
    else:
        lines.append("Context artifacts: none")
    return "\n".join(lines)


def _call_openai_compatible_provider(
    *,
    provider_base_url: str,
    api_key: str | None,
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    endpoint = _normalize_chat_completion_url(provider_base_url)
    return _request_provider_json(
        endpoint=endpoint,
        api_key=api_key,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )


def _request_provider_json(
    *,
    endpoint: str,
    api_key: str | None,
    payload: dict[str, Any] | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    method = "POST" if payload is not None else "GET"
    request = urllib.request.Request(endpoint, data=body if payload is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            headers = getattr(response, "headers", None)
            content_type = headers.get("Content-Type") if headers is not None and hasattr(headers, "get") else None
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(
            f"Provider {method} {endpoint} returned HTTP {exc.code}. "
            f"Response body: {error_body[:400]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ValueError(
            f"Could not reach provider endpoint {endpoint}. "
            f"Network error: {exc.reason}"
        ) from exc

    try:
        response_payload = _coerce_provider_response_payload(raw_body)
    except ValueError as exc:
        raise ValueError(
            f"Provider {method} {endpoint} returned non-JSON content that could not be normalized. "
            f"{exc} Body preview: {raw_body[:240]}"
        ) from exc
    if not isinstance(response_payload, dict):
        raise ValueError(f"Provider {method} {endpoint} response must be a JSON object.")
    if content_type:
        response_payload["_response_content_type"] = content_type
        if "text/event-stream" in content_type.lower():
            response_payload["_response_format"] = response_payload.get("_response_format") or "sse"
    return response_payload


def _normalize_chat_completion_url(provider_base_url: str) -> str:
    value = provider_base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return f"{value}/chat/completions"
    return f"{value}/v1/chat/completions"


def _normalize_models_url(provider_base_url: str) -> str:
    value = provider_base_url.rstrip("/")
    if value.endswith("/models"):
        return value
    if value.endswith("/v1"):
        return f"{value}/models"
    return f"{value}/v1/models"


def _extract_model_ids(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    models = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id:
            models.append(model_id)
    return models


def _coerce_provider_response_payload(raw_body: str) -> dict[str, Any]:
    stripped = raw_body.strip()
    if not stripped:
        raise ValueError("Response body was empty.")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = _parse_sse_payload(stripped)
        if payload is not None:
            return payload
        if _looks_like_html(stripped):
            raise ValueError("Response body looked like HTML, which usually means a proxy/login/error page.")
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": stripped},
                    "finish_reason": "stop",
                }
            ],
            "_response_format": "plain-text",
        }
    if not isinstance(payload, dict):
        raise ValueError("JSON response root was not an object.")
    return payload


def _parse_sse_payload(raw_body: str) -> dict[str, Any] | None:
    if "data:" not in raw_body:
        return None
    content_parts: list[str] = []
    usage: dict[str, Any] = {}
    saw_event = False
    for line in raw_body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        saw_event = True
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("usage"), dict):
            usage = payload["usage"]
        choices = payload.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                content_parts.append(message["content"])
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                content_parts.append(delta["content"])
    if not saw_event:
        return None
    return {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(content_parts).strip()},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
        "_response_format": "sse",
    }


def _looks_like_html(value: str) -> bool:
    return bool(re.match(r"\s*<(?:!doctype|html|head|body)\b", value, re.I))


def _extract_response_text(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )
    if "choices" not in response_payload:
        return json.dumps(response_payload, ensure_ascii=False)
    raise ValueError("Provider response did not contain choices[0].message.content.")


def _parse_response_json(response_text: str) -> dict[str, Any]:
    stripped = response_text.strip()
    candidates = [stripped]
    if "```json" in stripped:
        start = stripped.find("```json")
        end = stripped.find("```", start + 7)
        if end != -1:
            candidates.append(stripped[start + 7:end].strip())
    elif "```" in stripped:
        start = stripped.find("```")
        end = stripped.find("```", start + 3)
        if end != -1:
            candidates.append(stripped[start + 3:end].strip())
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _render_run_markdown(run: LlmRunArtifact) -> str:
    return f"""# LLM Run: {run.run_id}

## Request

- Artifact kind: {run.artifact_kind}
- Artifact name: {run.artifact_name}
- Prompt mode: {run.prompt_mode}
- Goal: {run.goal or "unknown"}
- Provider: {run.provider_base_url}
- Model: {run.model}
- Input token limit: {run.input_token_limit}
- Output token limit: {run.output_token_limit}
- Estimated request tokens: {run.request_tokens_estimate}

## Included Context Paths

{_bullet_lines(run.included_context_paths)}

## Skipped Context Paths

{_bullet_lines(run.skipped_context_paths)}

## Response

```text
{run.response_text}
```

## Feedback Template

- {run.feedback_template_path or "None"}
"""


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {item}" for item in values)
