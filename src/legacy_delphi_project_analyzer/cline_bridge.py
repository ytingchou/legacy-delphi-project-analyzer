from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.agent_loop import validate_task_response
from legacy_delphi_project_analyzer.runtime_errors import write_runtime_error_summary
from legacy_delphi_project_analyzer.taskpacks import load_taskpack
from legacy_delphi_project_analyzer.utils import ensure_directory, write_json, write_text


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run_cline_wrapper(
    *,
    analysis_dir: Path,
    cline_cmd: list[str],
    watch: bool = False,
    once: bool = False,
    resume: bool = False,
    skip_accepted: bool = True,
    streaming: bool = False,
    sanitize_output: bool = True,
    validate_after_run: bool = True,
    retry_on_fail: bool = True,
    timeout_seconds: int = 180,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    analysis_dir = analysis_dir.resolve()
    runtime_dir = analysis_dir / "runtime"
    inbox_root = runtime_dir / "cline-inbox"
    ensure_directory(inbox_root)
    processed = 0
    repaired = 0
    skipped = 0
    last_task_id: str | None = None

    def process_available() -> int:
        local_processed = 0
        for request_path in sorted(inbox_root.glob("*/request.json")):
            task_id = request_path.parent.name
            if skip_accepted and _is_task_accepted(runtime_dir, task_id):
                continue
            response_path = runtime_dir / "cline-outbox" / task_id / "response.json"
            if response_path.exists() and not resume:
                continue
            result = _process_request(
                request_path=request_path,
                analysis_dir=analysis_dir,
                cline_cmd=cline_cmd,
                streaming=streaming,
                sanitize_output_flag=sanitize_output,
                validate_after_run=validate_after_run,
                retry_on_fail=retry_on_fail,
                timeout_seconds=timeout_seconds,
            )
            nonlocal repaired, last_task_id
            repaired += 1 if result.get("used_retry") else 0
            last_task_id = task_id
            local_processed += 1
        return local_processed

    if once:
        processed += process_available()
    elif watch:
        while True:
            current = process_available()
            processed += current
            if current == 0 and not watch:
                break
            time.sleep(max(0.2, poll_seconds))
    else:
        processed += process_available()
        skipped = len(list(inbox_root.glob("*/request.json"))) - processed

    write_runtime_error_summary(analysis_dir=analysis_dir, runtime_dir=runtime_dir)
    summary = {
        "analysis_dir": analysis_dir.as_posix(),
        "processed": processed,
        "repaired": repaired,
        "skipped": max(skipped, 0),
        "last_task_id": last_task_id,
        "watch": watch,
        "once": once,
        "resume": resume,
    }
    write_json(runtime_dir / "cline-wrapper-summary.json", summary)
    return summary


def build_vscode_cline_taskpack_files(*, analysis_dir: Path, task_id: str) -> dict[str, str]:
    analysis_dir = analysis_dir.resolve()
    task_dir = analysis_dir / "runtime" / "taskpacks" / task_id
    taskpack = load_taskpack(task_dir)
    if taskpack is None:
        raise ValueError(f"Task pack does not exist or is invalid: {task_dir}")
    prompt = _assemble_prompt(task_dir, taskpack, prompt_mode="primary", retry_prompt=None)
    quick_open = _render_vscode_quick_open(taskpack)
    response_template = {
        "task_id": taskpack.task_id,
        "status": "completed",
        "result": {},
        "supported_claims": [],
        "unsupported_claims": [],
        "remaining_unknowns": [],
        "recommended_next_task": "",
    }
    write_text(task_dir / "vscode-cline-quick-open.md", quick_open)
    write_text(task_dir / "vscode-cline-copy-prompt.txt", prompt)
    write_json(task_dir / "vscode-cline-response-template.json", response_template)
    return {
        "quick_open": (task_dir / "vscode-cline-quick-open.md").as_posix(),
        "copy_prompt": (task_dir / "vscode-cline-copy-prompt.txt").as_posix(),
        "response_template": (task_dir / "vscode-cline-response-template.json").as_posix(),
    }


def _process_request(
    *,
    request_path: Path,
    analysis_dir: Path,
    cline_cmd: list[str],
    streaming: bool,
    sanitize_output_flag: bool,
    validate_after_run: bool,
    retry_on_fail: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    request_payload = _load_json(request_path)
    if not isinstance(request_payload, dict):
        return {"used_retry": False}
    task_id = str(request_payload.get("task_id") or request_path.parent.name)
    task_dir = Path(str(request_payload.get("context_dir") or "")).resolve()
    taskpack = load_taskpack(task_dir)
    if taskpack is None:
        raise ValueError(f"Task pack does not exist or is invalid: {task_dir}")
    runtime_dir = analysis_dir / "runtime"
    prompt = _assemble_prompt(task_dir, taskpack, prompt_mode="primary", retry_prompt=None)
    raw_output, stderr_output, exit_code = _invoke_cline(
        cline_cmd=cline_cmd,
        prompt=prompt,
        timeout_seconds=timeout_seconds,
        streaming=streaming,
    )
    if sanitize_output_flag:
        raw_output = _sanitize_output(raw_output)
    parsed = _extract_json(raw_output)
    used_retry = False
    if parsed is None:
        repair_prompt = _build_json_repair_prompt(raw_output)
        repair_output, repair_stderr, repair_code = _invoke_cline(
            cline_cmd=cline_cmd,
            prompt=repair_prompt,
            timeout_seconds=timeout_seconds,
            streaming=streaming,
        )
        if sanitize_output_flag:
            repair_output = _sanitize_output(repair_output)
        repair_parsed = _extract_json(repair_output)
        if repair_parsed is not None:
            parsed = repair_parsed
            raw_output = repair_output
            stderr_output = f"{stderr_output}\n{repair_stderr}".strip()
            exit_code = repair_code
            used_retry = True
    response_payload = _wrap_response(
        task_id=task_id,
        parsed=parsed,
        raw_output=raw_output,
        stderr_output=stderr_output,
        exit_code=exit_code,
    )
    response_path = runtime_dir / "cline-outbox" / task_id / "response.json"
    write_json(response_path, response_payload)
    _write_wrapper_log(task_id=task_id, runtime_dir=runtime_dir, prompt=prompt, raw_output=raw_output, stderr_output=stderr_output)

    if validate_after_run:
        validation = validate_task_response(
            analysis_dir=analysis_dir,
            task_dir=task_dir,
            response_payload=response_payload,
            response_path=response_path,
            prompt_mode="primary",
        )
        if retry_on_fail and validation.status not in {"accepted", "accepted_with_warnings"}:
            retry_plan = _load_json(task_dir / "retry-plan.json")
            if isinstance(retry_plan, dict):
                retry_prompt = str(retry_plan.get("repair_prompt") or "").strip()
                if retry_prompt:
                    second_prompt = _assemble_prompt(task_dir, taskpack, prompt_mode="fallback", retry_prompt=retry_prompt)
                    second_output, second_stderr, second_code = _invoke_cline(
                        cline_cmd=cline_cmd,
                        prompt=second_prompt,
                        timeout_seconds=timeout_seconds,
                        streaming=streaming,
                    )
                    if sanitize_output_flag:
                        second_output = _sanitize_output(second_output)
                    second_parsed = _extract_json(second_output)
                    second_response = _wrap_response(
                        task_id=task_id,
                        parsed=second_parsed,
                        raw_output=second_output,
                        stderr_output=second_stderr,
                        exit_code=second_code,
                    )
                    write_json(response_path, second_response)
                    _write_wrapper_log(
                        task_id=f"{task_id}-retry",
                        runtime_dir=runtime_dir,
                        prompt=second_prompt,
                        raw_output=second_output,
                        stderr_output=second_stderr,
                    )
                    validate_task_response(
                        analysis_dir=analysis_dir,
                        task_dir=task_dir,
                        response_payload=second_response,
                        response_path=response_path,
                        prompt_mode=str(retry_plan.get("next_prompt_mode") or "fallback"),
                    )
                    used_retry = True
    return {"used_retry": used_retry}


def _invoke_cline(
    *,
    cline_cmd: list[str],
    prompt: str,
    timeout_seconds: int,
    streaming: bool,
) -> tuple[str, str, int]:
    if any("{prompt_file}" in part for part in cline_cmd):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            handle.write(prompt)
            prompt_path = handle.name
        cmd = [part.replace("{prompt_file}", prompt_path) for part in cline_cmd]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_seconds)
        return proc.stdout, proc.stderr, proc.returncode
    if not streaming:
        proc = subprocess.run(cline_cmd, input=prompt, text=True, capture_output=True, timeout=timeout_seconds)
        return proc.stdout, proc.stderr, proc.returncode

    proc = subprocess.Popen(
        cline_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    proc.stdin.write(prompt)
    proc.stdin.close()
    started = time.time()
    stdout_lines: list[str] = []
    while True:
        line = proc.stdout.readline()
        if line:
            stdout_lines.append(line)
        elif proc.poll() is not None:
            break
        elif time.time() - started > timeout_seconds:
            proc.kill()
            raise TimeoutError("Cline CLI timed out while streaming output.")
        else:
            time.sleep(0.05)
    stderr_output = proc.stderr.read()
    exit_code = proc.wait()
    return "".join(stdout_lines), stderr_output, exit_code


def _assemble_prompt(task_dir: Path, taskpack: Any, prompt_mode: str, retry_prompt: str | None) -> str:
    task_md = _safe_read(task_dir / "agent-task.md")
    compiled_md = _safe_read(task_dir / "compiled-context.md")
    schema_json = _safe_read(task_dir / "agent-expected-output-schema.json")
    checks_json = _safe_read(task_dir / "agent-acceptance-checks.json")
    selected_prompt = {
        "primary": taskpack.primary_prompt,
        "fallback": taskpack.fallback_prompt or taskpack.primary_prompt,
        "verification": taskpack.verification_prompt or taskpack.primary_prompt,
    }.get(prompt_mode, taskpack.primary_prompt)
    instructions = retry_prompt or selected_prompt or ""
    return (
        "You are handling one bounded migration task.\n\n"
        f"Task metadata:\n{task_md}\n\n"
        f"Compact context:\n{compiled_md or '[compiled-context.md missing]'}\n\n"
        f"Expected output schema:\n{schema_json}\n\n"
        f"Acceptance checks:\n{checks_json}\n\n"
        f"Primary task:\n{instructions}\n\n"
        "Hard constraints:\n"
        "- Output JSON only.\n"
        "- Do not add markdown fences.\n"
        "- Use only the supplied evidence.\n"
        "- If uncertain, put unresolved points into remaining_unknowns or missing_assumptions.\n"
    )


def _sanitize_output(raw_output: str) -> str:
    text = ANSI_RE.sub("", raw_output)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(("thinking", "tokens", "provider:", "model:", "cost:")):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
            if line == "[DONE]":
                continue
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_json(raw_output: str) -> dict[str, Any] | None:
    stripped = raw_output.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"```json\s*(\{.*?\})\s*```", stripped, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
    match = re.search(r"(\{.*\})", stripped, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _build_json_repair_prompt(raw_output: str) -> str:
    return (
        "Convert the following output into valid JSON only.\n"
        "Do not add explanation.\n"
        "Do not add markdown fences.\n"
        "Preserve the original meaning and move uncertainty into remaining_unknowns.\n\n"
        f"Raw output:\n{raw_output[:16000]}\n"
    )


def _wrap_response(
    *,
    task_id: str,
    parsed: dict[str, Any] | None,
    raw_output: str,
    stderr_output: str,
    exit_code: int,
) -> dict[str, Any]:
    if parsed is not None:
        return {
            "task_id": task_id,
            "status": "completed",
            "result": parsed,
            "supported_claims": [],
            "unsupported_claims": [],
            "remaining_unknowns": [],
            "recommended_next_task": "",
            "wrapper_meta": {
                "exit_code": exit_code,
                "stderr_preview": stderr_output[:1000],
            },
        }
    return {
        "task_id": task_id,
        "status": "needs_follow_up",
        "result": {"raw_response": raw_output[:20000]},
        "supported_claims": [],
        "unsupported_claims": ["wrapper_parse_failed"],
        "remaining_unknowns": ["response_not_valid_json"],
        "recommended_next_task": "",
        "wrapper_meta": {
            "exit_code": exit_code,
            "stderr_preview": stderr_output[:1000],
        },
    }


def _write_wrapper_log(*, task_id: str, runtime_dir: Path, prompt: str, raw_output: str, stderr_output: str) -> None:
    log_dir = runtime_dir / "cline-logs"
    ensure_directory(log_dir)
    write_text(
        log_dir / f"{task_id}.log",
        f"PROMPT\n{prompt}\n\nSTDOUT\n{raw_output}\n\nSTDERR\n{stderr_output}\n",
    )


def _is_task_accepted(runtime_dir: Path, task_id: str) -> bool:
    path = runtime_dir / "taskpacks" / task_id / "validation-result.json"
    payload = _load_json(path)
    if not isinstance(payload, dict):
        return False
    return str(payload.get("status") or "") in {"accepted", "accepted_with_warnings"}


def _render_vscode_quick_open(taskpack: Any) -> str:
    return f"""# VSCode Cline Quick Open

Open these files first:

1. `agent-task.md`
2. `compiled-context.md`
3. `agent-expected-output-schema.json`

Task:

- Task ID: `{taskpack.task_id}`
- Task type: `{taskpack.task_type}`
- Module: `{taskpack.module_name or 'None'}`
- Subject: `{taskpack.subject_name or 'None'}`

Rules:

- Start a fresh chat for this task only.
- Paste only the three files above.
- Force JSON-only output.
- Save the answer back to `agent-response.json`.
"""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
