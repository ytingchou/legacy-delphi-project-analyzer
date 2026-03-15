from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from legacy_delphi_project_analyzer.feedback import ingest_feedback
from legacy_delphi_project_analyzer.llm import run_llm_artifact
from legacy_delphi_project_analyzer.cline import emit_cline_task
from legacy_delphi_project_analyzer.agent_loop import (
    load_task_attempts,
    load_task_history,
    run_loop,
    validate_task_response,
)
from legacy_delphi_project_analyzer.codegen import generate_transition_code
from legacy_delphi_project_analyzer.orchestrator import (
    build_analysis_config,
    load_runtime_bundle,
    refresh_runtime_artifacts,
    rerun_analysis_from_runtime_state,
    run_phases,
)
from legacy_delphi_project_analyzer.pipeline import PHASE_ORDER, run_analysis
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, load_taskpack, write_taskpacks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy-delphi-analyzer",
        description="Analyze a legacy Delphi project and emit LLM-friendly migration artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Run the analyzer on a project root.")
    _add_analysis_arguments(analyze_parser)
    analyze_parser.add_argument(
        "--model-profile",
        default="qwen3_128k_weak",
        help="Runtime LLM profile label stored for later loop/task-pack generation.",
    )

    phase_runner_parser = subparsers.add_parser(
        "run-phases",
        help="Run analysis and emit runtime phase orchestration artifacts.",
    )
    _add_analysis_arguments(phase_runner_parser)
    phase_runner_parser.add_argument(
        "--model-profile",
        default="qwen3_128k_weak",
        help="Runtime LLM profile label stored for orchestration and later loop commands.",
    )
    phase_runner_parser.add_argument(
        "--dispatch-mode",
        choices=["manual", "provider", "cline"],
        default="manual",
        help="Dispatch mode recorded in runtime state for later loop execution.",
    )
    _add_provider_arguments(phase_runner_parser)

    phase_status_parser = subparsers.add_parser(
        "phase-status",
        help="Read runtime phase orchestration files from an analysis output directory.",
    )
    phase_status_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    build_taskpacks_parser = subparsers.add_parser(
        "build-taskpacks",
        help="Regenerate task packs for the current runtime blockers.",
    )
    build_taskpacks_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    build_taskpacks_parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Optional cap on the number of task packs to emit.",
    )
    build_taskpacks_parser.add_argument(
        "--model-profile",
        default=None,
        help="Optional override for the runtime model profile when building task packs.",
    )

    validate_response_parser = subparsers.add_parser(
        "validate-response",
        help="Validate one task response against schema and recovered legacy evidence.",
    )
    validate_response_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    validate_response_parser.add_argument("task_id", help="Task ID under runtime/taskpacks/ to validate.")
    validate_response_parser.add_argument(
        "--response-file",
        default=None,
        help="Optional response JSON path. Defaults to runtime/taskpacks/<task-id>/agent-response.json or the Cline outbox response.",
    )
    validate_response_parser.add_argument(
        "--prompt-mode",
        choices=["primary", "fallback", "verification"],
        default="primary",
        help="Prompt mode associated with the response being validated.",
    )

    loop_parser = subparsers.add_parser(
        "run-loop",
        help="Run the bounded orchestration loop until blockers are reduced or stop conditions are hit.",
    )
    loop_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    loop_parser.add_argument(
        "--dispatch-mode",
        choices=["manual", "provider", "cline"],
        default=None,
        help="Override the runtime dispatch mode for this loop run.",
    )
    loop_parser.add_argument("--max-loops", type=int, default=10, help="Maximum loop iterations for this run.")
    loop_parser.add_argument("--max-task-attempts", type=int, default=3, help="Maximum attempts per task.")
    loop_parser.add_argument("--wait-seconds", type=int, default=120, help="Wait time for provider/Cline responses.")
    loop_parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval for Cline responses.")
    loop_parser.add_argument("--timeout-seconds", type=int, default=120, help="Provider HTTP timeout.")
    _add_provider_arguments(loop_parser)

    resume_loop_parser = subparsers.add_parser(
        "resume-loop",
        help="Resume a previously prepared orchestration loop using the saved runtime state.",
    )
    resume_loop_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    resume_loop_parser.add_argument("--max-loops", type=int, default=10, help="Maximum loop iterations for this run.")
    resume_loop_parser.add_argument("--max-task-attempts", type=int, default=3, help="Maximum attempts per task.")
    resume_loop_parser.add_argument("--wait-seconds", type=int, default=120, help="Wait time for provider/Cline responses.")
    resume_loop_parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval for Cline responses.")
    resume_loop_parser.add_argument("--timeout-seconds", type=int, default=120, help="Provider HTTP timeout.")
    _add_provider_arguments(resume_loop_parser)

    loop_status_parser = subparsers.add_parser(
        "loop-status",
        help="Read current loop state, task attempts, and task history from runtime files.",
    )
    loop_status_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    dispatch_task_parser = subparsers.add_parser(
        "dispatch-task",
        help="Dispatch one generated task pack to the file-based Cline inbox.",
    )
    dispatch_task_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    dispatch_task_parser.add_argument("task_id", help="Task ID under runtime/taskpacks/ to dispatch.")
    dispatch_task_parser.add_argument(
        "--mode",
        choices=["cline", "manual"],
        default="cline",
        help="Dispatch target. 'manual' only validates that the task pack exists.",
    )

    report_parser = subparsers.add_parser(
        "serve-report", help="Serve a generated HTML report directory locally."
    )
    report_parser.add_argument("report_dir", help="Path to the generated report directory.")
    report_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    report_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")

    feedback_parser = subparsers.add_parser(
        "ingest-feedback",
        help="Import accepted/rejected prompt feedback and turn it into reusable analyzer rules.",
    )
    feedback_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    feedback_parser.add_argument("feedback_file", help="Path to a JSON feedback file.")

    llm_parser = subparsers.add_parser(
        "run-llm",
        help="Run a prompt pack or failure triage artifact against an OpenAI-compatible provider.",
    )
    llm_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    llm_target = llm_parser.add_mutually_exclusive_group(required=True)
    llm_target.add_argument("--prompt-name", help="Prompt pack name to execute, for example OrderLookupClarify.")
    llm_target.add_argument("--failure-name", help="Failure triage name to execute.")
    llm_target.add_argument("--artifact-json", help="Direct path to a prompt/failure JSON or repro-bundle JSON.")
    llm_parser.add_argument("--provider-base-url", required=True, help="OpenAI-compatible provider base URL.")
    llm_parser.add_argument("--model", required=True, help="Provider model name.")
    llm_parser.add_argument("--api-key", default=None, help="Bearer token for the provider. Optional for local providers.")
    llm_parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable to read the provider API key from when --api-key is omitted.",
    )
    llm_parser.add_argument(
        "--prompt-mode",
        choices=["primary", "fallback", "verification"],
        default="primary",
        help="Which prompt variant from the artifact to execute.",
    )
    llm_parser.add_argument(
        "--token-limit",
        type=int,
        default=None,
        help="Maximum estimated input/context tokens to include in the request. Defaults to the artifact budget.",
    )
    llm_parser.add_argument(
        "--output-token-limit",
        type=int,
        default=1200,
        help="Maximum completion tokens requested from the provider.",
    )
    llm_parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="Sampling temperature sent to the provider.",
    )
    llm_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="HTTP timeout for the provider request.",
    )

    codegen_parser = subparsers.add_parser(
        "generate-code",
        help="Generate validated React and Spring Boot skeletons from transition specs.",
    )
    codegen_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    codegen_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for generated skeletons. Defaults to <analysis_dir>/codegen.",
    )
    codegen_parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Generate skeletons even when validation results are missing.",
    )
    return parser


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", help="Path to the Delphi project root.")
    parser.add_argument(
        "--phase",
        dest="phases",
        action="append",
        choices=[*PHASE_ORDER, "all"],
        help="Limit execution to one or more phases. Defaults to all phases.",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts",
        help="Directory where analysis outputs will be written.",
    )
    parser.add_argument(
        "--rules-dir",
        default=None,
        help="Optional directory containing overrides.json.",
    )
    parser.add_argument(
        "--workspace-config",
        default=None,
        help="Optional JSON file defining external scan roots, search paths, and path variables.",
    )
    parser.add_argument(
        "--search-path",
        dest="search_paths",
        action="append",
        default=[],
        help="Additional Delphi search path directory to scan. Can be repeated.",
    )
    parser.add_argument(
        "--path-var",
        dest="path_vars",
        action="append",
        default=[],
        help="Delphi path variable mapping in NAME=VALUE form. Can be repeated.",
    )
    parser.add_argument(
        "--max-artifact-chars",
        type=int,
        default=40000,
        help="Split Markdown artifacts once they exceed this size.",
    )
    parser.add_argument(
        "--max-artifact-tokens",
        type=int,
        default=10000,
        help="Split Markdown artifacts once they exceed this approximate token budget.",
    )
    parser.add_argument(
        "--target-model",
        default="qwen3-128k",
        help="Target LLM profile used when generating prompt packs.",
    )
    parser.add_argument(
        "--fail-on-fatal",
        action="store_true",
        help="Exit with a non-zero status if fatal diagnostics are present.",
    )


def _add_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider-base-url", default=None, help="OpenAI-compatible provider base URL.")
    parser.add_argument("--model", default=None, help="Provider model name.")
    parser.add_argument("--api-key", default=None, help="Bearer token for the provider.")
    parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable for the provider API key when --api-key is omitted.",
    )
    parser.add_argument(
        "--token-limit",
        type=int,
        default=None,
        help="Optional input/context token budget override for provider loop execution.",
    )
    parser.add_argument(
        "--output-token-limit",
        type=int,
        default=None,
        help="Optional output token budget override for provider loop execution.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional provider temperature override for loop execution.",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve-report":
        return serve_report(Path(args.report_dir), host=args.host, port=args.port)
    if args.command == "ingest-feedback":
        try:
            result = ingest_feedback(Path(args.analysis_dir), Path(args.feedback_file))
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"Feedback imported into: {result['analysis_dir']}")
        print(
            "Feedback summary: "
            f"{result['feedback_entries']} entries, "
            f"{result['accepted']} accepted, "
            f"{result['rejected']} rejected, "
            f"{result['needs_follow_up']} follow-up, "
            f"{result['fallback_uses']} fallback uses"
        )
        return 0
    if args.command == "run-llm":
        try:
            result = run_llm_artifact(
                analysis_dir=Path(args.analysis_dir),
                prompt_name=args.prompt_name,
                failure_name=args.failure_name,
                artifact_json_path=Path(args.artifact_json) if args.artifact_json else None,
                provider_base_url=args.provider_base_url,
                model=args.model,
                api_key=args.api_key,
                api_key_env=args.api_key_env,
                prompt_mode=args.prompt_mode,
                token_limit=args.token_limit,
                output_token_limit=args.output_token_limit,
                temperature=args.temperature,
                timeout_seconds=args.timeout_seconds,
            )
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"LLM run complete: {result.run_id}")
        print(
            "LLM summary: "
            f"{result.artifact_kind} {result.artifact_name}, "
            f"model={result.model}, "
            f"input_tokens~{result.request_tokens_estimate}, "
            f"context={len(result.included_context_paths)} file(s)"
        )
        print(f"Feedback template: {result.feedback_template_path}")
        return 0
    if args.command == "validate-response":
        analysis_dir = Path(args.analysis_dir).resolve()
        task_dir = analysis_dir / "runtime" / "taskpacks" / args.task_id
        response_path = Path(args.response_file).resolve() if args.response_file else None
        result = validate_task_response(
            analysis_dir=analysis_dir,
            task_dir=task_dir,
            response_path=response_path,
            prompt_mode=args.prompt_mode,
        )
        print(f"Validation status: {result.status}")
        print(
            f"Schema valid: {str(result.schema_valid).lower()}, "
            f"evidence valid: {str(result.evidence_valid).lower()}, "
            f"supported={len(result.supported_claims)}, "
            f"unsupported={len(result.unsupported_claims)}, "
            f"missing={len(result.missing_evidence)}"
        )
        return 0
    if args.command in {"run-loop", "resume-loop"}:
        provider_config = _provider_config_from_args(args)
        result = run_loop(
            Path(args.analysis_dir),
            dispatch_mode=getattr(args, "dispatch_mode", None),
            max_loops=args.max_loops,
            max_task_attempts=args.max_task_attempts,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
            provider_base_url=provider_config.get("provider_base_url"),
            model=provider_config.get("model"),
            api_key=provider_config.get("api_key"),
            api_key_env=provider_config.get("api_key_env", "OPENAI_API_KEY"),
            timeout_seconds=args.timeout_seconds,
        )
        print(
            f"Loop complete: status={result.status}, phase={result.current_phase}, "
            f"iteration={result.loop_iteration}, stop_reason={result.stop_reason or 'None'}"
        )
        return 0
    if args.command == "loop-status":
        analysis_dir = Path(args.analysis_dir).resolve()
        runtime_dir = analysis_dir / "runtime"
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise SystemExit(f"Runtime state does not exist under {runtime_dir}")
        history = load_task_history(runtime_dir)
        attempts = load_task_attempts(runtime_dir)
        print(
            f"Loop: status={run_state.status}, phase={run_state.current_phase}, "
            f"iteration={run_state.loop_iteration}, blocking={run_state.blocking_task_id or 'None'}"
        )
        print(f"Task attempts tracked: {len(attempts)}")
        print(f"Task history entries: {len(history)}")
        for item in history[-5:]:
            print(
                f"- {item.get('task_id')}: "
                f"{item.get('validation_status') or item.get('status') or 'unknown'} "
                f"({item.get('prompt_mode') or 'n/a'})"
            )
        return 0
    if args.command == "generate-code":
        generated = generate_transition_code(
            Path(args.analysis_dir),
            output_dir=Path(args.output_dir) if args.output_dir else None,
            require_validated=not args.allow_unvalidated,
        )
        base_dir = Path(args.output_dir).resolve() if args.output_dir else (Path(args.analysis_dir).resolve() / "codegen")
        print(f"Generated code skeletons: {len(generated)}")
        print(f"Output directory: {base_dir}")
        return 0
    if args.command == "phase-status":
        bundle = load_runtime_bundle(Path(args.analysis_dir))
        run_state = bundle["run_state"]
        phase_states = bundle["phase_states"]
        blockers = bundle["blocking_unknowns"]
        completeness = bundle["artifact_completeness"]
        if run_state is None:
            raise SystemExit(f"Runtime state does not exist under {Path(args.analysis_dir) / 'runtime'}")
        print(f"Run ID: {run_state.run_id}")
        print(
            f"Runtime: status={run_state.status}, phase={run_state.current_phase}, "
            f"loop={run_state.loop_iteration}, model_profile={run_state.target_model_profile}"
        )
        if completeness is not None:
            print(
                f"Artifacts: {completeness.completed_count}/{completeness.required_count} required artifacts complete"
            )
        print(f"Blockers: {len(blockers)}")
        for phase_state in phase_states:
            print(
                f"- {phase_state.phase}: {phase_state.status}, completion={phase_state.completion_score}/100, "
                f"blockers={len(phase_state.blockers)}"
            )
        return 0
    if args.command == "build-taskpacks":
        analysis_dir = Path(args.analysis_dir).resolve()
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise SystemExit(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
        output = rerun_analysis_from_runtime_state(analysis_dir)
        refresh_runtime_artifacts(
            output,
            target_model_profile=args.model_profile or run_state.target_model_profile,
            dispatch_mode=run_state.dispatch_mode,
            analysis_config=run_state.analysis_config,
            provider_config=run_state.provider_config,
        )
        assert output.runtime_state is not None
        taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=args.max_tasks)
        written = write_taskpacks(taskpacks, analysis_dir / "runtime")
        print(f"Task packs generated: {len(written)}")
        for path in written[:10]:
            print(f"- {path}")
        return 0
    if args.command == "dispatch-task":
        analysis_dir = Path(args.analysis_dir).resolve()
        task_dir = analysis_dir / "runtime" / "taskpacks" / args.task_id
        taskpack = load_taskpack(task_dir)
        if taskpack is None:
            raise SystemExit(f"Task pack does not exist or is invalid: {task_dir}")
        if args.mode == "manual":
            print(f"Task pack ready for manual execution: {task_dir}")
            return 0
        request_path = emit_cline_task(taskpack, task_dir, analysis_dir / "runtime")
        print(f"Cline request emitted: {request_path}")
        return 0
    if args.command not in {"analyze", "run-phases"}:
        parser.error("Unsupported command")

    path_variables = _parse_path_variables(args.path_vars)
    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    workspace_config_path = Path(args.workspace_config) if args.workspace_config else None
    analysis_config = build_analysis_config(
        project_root=Path(args.project_root),
        output_dir=Path(args.output_dir),
        rules_dir=rules_dir,
        workspace_config_path=workspace_config_path,
        extra_search_paths=args.search_paths,
        path_variables=path_variables,
        phases=args.phases,
        max_artifact_chars=args.max_artifact_chars,
        max_artifact_tokens=args.max_artifact_tokens,
        target_model=args.target_model,
    )

    if args.command == "run-phases":
        output = run_phases(
            project_root=Path(args.project_root),
            output_dir=Path(args.output_dir),
            rules_dir=rules_dir,
            workspace_config_path=workspace_config_path,
            extra_search_paths=args.search_paths,
            path_variables=path_variables,
            phases=args.phases,
            max_artifact_chars=args.max_artifact_chars,
            max_artifact_tokens=args.max_artifact_tokens,
            target_model=args.target_model,
            target_model_profile=args.model_profile,
            dispatch_mode=args.dispatch_mode,
            provider_config=_provider_config_from_args(args),
        )
    else:
        output = run_analysis(
            project_root=Path(args.project_root),
            output_dir=Path(args.output_dir),
            rules_dir=rules_dir,
            workspace_config_path=workspace_config_path,
            extra_search_paths=args.search_paths,
            path_variables=path_variables,
            phases=args.phases,
            max_artifact_chars=args.max_artifact_chars,
            max_artifact_tokens=args.max_artifact_tokens,
            target_model=args.target_model,
        )
        refresh_runtime_artifacts(
            output,
            target_model_profile=args.model_profile,
            dispatch_mode="manual",
            analysis_config=analysis_config,
        )

    fatal_count = len([item for item in output.diagnostics if item.severity == "fatal"])
    error_count = len([item for item in output.diagnostics if item.severity == "error"])
    print(f"Analysis complete: {output.output_dir}")
    print(
        "Artifacts: "
        f"{len(output.manifest)} files, "
        f"{len(output.pascal_units)} Pascal units, "
        f"{len(output.forms)} forms, "
        f"{len(output.resolved_queries)} resolved queries"
    )
    print(
        "Workspace: "
        f"{len(output.inventory.scan_roots)} scan roots, "
        f"{len(output.inventory.external_roots)} external roots, "
        f"{len(output.inventory.missing_search_paths)} missing paths, "
        f"{len(output.inventory.unresolved_search_paths)} unresolved paths"
    )
    print(f"Diagnostics: {len(output.diagnostics)} total, {error_count} error, {fatal_count} fatal")
    report_path = Path(output.output_dir) / "report" / "index.html"
    if report_path.exists():
        print(f"Web report: {report_path}")
    runtime_path = Path(output.output_dir) / "runtime" / "run-state.json"
    if runtime_path.exists():
        print(f"Runtime state: {runtime_path}")
    return 1 if args.fail_on_fatal and fatal_count else 0


def serve_report(report_dir: Path, host: str, port: int) -> int:
    report_dir = report_dir.resolve()
    if not report_dir.exists():
        raise SystemExit(f"Report directory does not exist: {report_dir}")
    handler = partial(SimpleHTTPRequestHandler, directory=report_dir.as_posix())
    server = ThreadingHTTPServer((host, port), handler)
    try:
        print(f"Serving report at http://{host}:{port}/")
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping report server.")
    finally:
        server.server_close()
    return 0


def _parse_path_variables(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"Invalid --path-var value '{item}'. Expected NAME=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise SystemExit(f"Invalid --path-var value '{item}'. Expected NAME=VALUE.")
        parsed[key] = value
    return parsed


def _provider_config_from_args(args) -> dict[str, object]:
    config: dict[str, object] = {}
    for key in (
        "provider_base_url",
        "model",
        "api_key",
        "api_key_env",
        "token_limit",
        "output_token_limit",
        "temperature",
    ):
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value
    return config
