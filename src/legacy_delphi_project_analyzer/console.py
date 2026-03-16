from __future__ import annotations

import traceback
from dataclasses import dataclass


@dataclass(slots=True)
class CliReporter:
    verbose: bool = False
    progress_enabled: bool = True

    def progress(self, message: str) -> None:
        if self.progress_enabled:
            print(f"[progress] {message}")

    def info(self, message: str) -> None:
        print(message)

    def detail(self, message: str) -> None:
        if self.verbose:
            print(f"[debug] {message}")

    def warning(self, message: str) -> None:
        print(f"[warn] {message}")

    def error(self, message: str) -> None:
        print(message)


def render_cli_exception(exc: BaseException, *, command: str | None = None, verbose: bool = False) -> str:
    header = f"Error while running `{command}`" if command else "Error"
    message = str(exc).strip() or exc.__class__.__name__
    lines = [
        f"{header}:",
        f"  {message}",
    ]
    hint = _suggest_hint(message)
    if hint:
        lines.extend(["", f"Hint: {hint}"])
    if verbose:
        lines.extend(["", "Traceback:", traceback.format_exc().rstrip()])
    return "\n".join(lines)


def _suggest_hint(message: str) -> str | None:
    lowered = message.lower()
    if "provider" in lowered or "chat/completions" in lowered or "models" in lowered:
        return "Run `validate-provider` first and check --provider-base-url, --model, auth, and timeout."
    if "runtime state does not exist" in lowered:
        return "Run `run-phases` or `analyze` first so runtime artifacts exist."
    if "task pack does not exist" in lowered or "retry plan does not exist" in lowered:
        return "Regenerate task packs with `build-taskpacks`, then retry the command."
    if "artifact directory does not exist" in lowered or "could not find artifact" in lowered:
        return "Check the artifact name/path and confirm the expected prompt-pack or failure-case exists."
    if "invalid --path-var" in lowered:
        return "Use NAME=VALUE form, for example --path-var PDSS_SQL=../PDSS_SQL."
    return None
