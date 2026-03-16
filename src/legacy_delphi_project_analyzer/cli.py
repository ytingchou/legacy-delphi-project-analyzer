from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from legacy_delphi_project_analyzer.benchmarking import benchmark_prompts
from legacy_delphi_project_analyzer.cheatsheet import write_analysis_cheat_sheet, write_runtime_cheat_sheet
from legacy_delphi_project_analyzer.cline_bridge import run_cline_wrapper
from legacy_delphi_project_analyzer.cline_session import build_cline_session_manifest
from legacy_delphi_project_analyzer.console import CliReporter, render_cli_exception
from legacy_delphi_project_analyzer.controlled_delivery import run_controlled_delivery
from legacy_delphi_project_analyzer.developer_handoff import build_developer_handoff_packs
from legacy_delphi_project_analyzer.failure_replay import build_failure_replay_lab
from legacy_delphi_project_analyzer.feedback import ingest_feedback
from legacy_delphi_project_analyzer.golden_tasks import evaluate_golden_tasks
from legacy_delphi_project_analyzer.human_review import record_task_review
from legacy_delphi_project_analyzer.llm import run_llm_artifact, validate_openai_compatible_provider
from legacy_delphi_project_analyzer.multi_repo_map import build_multi_repo_transition_map
from legacy_delphi_project_analyzer.patch_apply import build_patch_apply_assistant
from legacy_delphi_project_analyzer.patch_validation import validate_patch_packs
from legacy_delphi_project_analyzer.cline import emit_cline_task
from legacy_delphi_project_analyzer.delivery import deliver_slices
from legacy_delphi_project_analyzer.agent_loop import (
    load_task_attempts,
    load_task_history,
    run_loop,
    validate_task_response,
)
from legacy_delphi_project_analyzer.codegen import generate_transition_code
from legacy_delphi_project_analyzer.oracle_bff import compile_oracle_bff_sql
from legacy_delphi_project_analyzer.orchestrator import (
    build_analysis_config,
    load_runtime_bundle,
    refresh_runtime_artifacts,
    rerun_analysis_from_runtime_state,
    run_phases,
)
from legacy_delphi_project_analyzer.patch_packs import build_code_patch_packs
from legacy_delphi_project_analyzer.pipeline import PHASE_ORDER, run_analysis
from legacy_delphi_project_analyzer.progress_layer import update_progress_report
from legacy_delphi_project_analyzer.repo_validation import build_repo_validation_gate
from legacy_delphi_project_analyzer.runtime_errors import save_provider_health
from legacy_delphi_project_analyzer.repair_tasks import build_repair_tasks
from legacy_delphi_project_analyzer.subagents import run_subagent_batches
from legacy_delphi_project_analyzer.task_studio import build_task_studio
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, load_taskpack, write_taskpacks
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack
from legacy_delphi_project_analyzer.workspace_sync import build_transition_workspace_sync
from legacy_delphi_project_analyzer.workspace_graph import build_workspace_graph


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

    cheat_sheet_parser = subparsers.add_parser(
        "build-cheatsheet",
        help="Regenerate the Cline quick-start cheat sheets under llm-pack/ and runtime/.",
    )
    cheat_sheet_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    task_studio_parser = subparsers.add_parser(
        "build-task-studio",
        help="Regenerate runtime task-studio artifacts and per-task quick commands.",
    )
    task_studio_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    cline_session_parser = subparsers.add_parser(
        "build-cline-session",
        help="Regenerate session-ready prompt bundles for Cline CLI and the VSCode extension.",
    )
    cline_session_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    cline_session_parser.add_argument(
        "--cline-cmd",
        nargs="+",
        default=["cline", "chat"],
        help="Command template used when emitting run-command.txt files.",
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

    retry_plan_parser = subparsers.add_parser(
        "retry-plan",
        help="Read the validator-driven retry plan for one generated task pack.",
    )
    retry_plan_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    retry_plan_parser.add_argument("task_id", help="Task ID under runtime/taskpacks/ to inspect.")

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

    benchmark_parser = subparsers.add_parser(
        "benchmark-prompts",
        help="Score prompt/taskpack templates from validation and feedback history.",
    )
    benchmark_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    wrapper_parser = subparsers.add_parser(
        "run-cline-wrapper",
        help="Watch the file-based Cline inbox, run an external Cline CLI command, and auto-validate responses.",
    )
    wrapper_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    wrapper_parser.add_argument(
        "--cline-cmd",
        nargs="+",
        required=True,
        help="External Cline command. Use {prompt_file} in one argument if your CLI needs a prompt file path instead of stdin.",
    )
    wrapper_parser.add_argument("--watch", action="store_true", help="Continuously watch the inbox for new tasks.")
    wrapper_parser.add_argument("--once", action="store_true", help="Process currently available tasks once and exit.")
    wrapper_parser.add_argument("--resume", action="store_true", help="Revisit tasks that are not already accepted.")
    wrapper_parser.add_argument(
        "--no-skip-accepted",
        action="store_true",
        help="Do not skip tasks that already have accepted validation results.",
    )
    wrapper_parser.add_argument("--streaming", action="store_true", help="Read the external Cline command as a streaming stdout process.")
    wrapper_parser.add_argument("--timeout-seconds", type=int, default=180, help="Execution timeout for each Cline command.")
    wrapper_parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval used by watch mode.")
    wrapper_parser.add_argument("--no-sanitize-output", action="store_true", help="Disable stdout cleanup before JSON extraction.")
    wrapper_parser.add_argument("--no-validate-after-run", action="store_true", help="Do not run validate-response automatically.")
    wrapper_parser.add_argument("--no-retry-on-fail", action="store_true", help="Do not use retry-plan repair prompts after validation failure.")

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

    provider_probe_parser = subparsers.add_parser(
        "validate-provider",
        help="Probe an OpenAI-compatible provider and print actionable diagnostics.",
    )
    provider_probe_parser.add_argument("--provider-base-url", required=True, help="OpenAI-compatible provider base URL.")
    provider_probe_parser.add_argument("--model", default=None, help="Optional model name to verify and probe.")
    provider_probe_parser.add_argument("--api-key", default=None, help="Bearer token for the provider.")
    provider_probe_parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable to read the provider API key from when --api-key is omitted.",
    )
    provider_probe_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="HTTP timeout for the provider probe.",
    )
    provider_probe_parser.add_argument(
        "--skip-completion",
        action="store_true",
        help="Only check the models endpoint and skip the sample chat completion probe.",
    )
    provider_probe_parser.add_argument(
        "--analysis-dir",
        default=None,
        help="Optional analysis directory. When supplied, the provider probe is persisted under runtime/provider-health.json.",
    )

    review_parser = subparsers.add_parser(
        "review-task",
        help="Record a human decision for a task response and optionally fold accepted output back into feedback learning.",
    )
    review_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    review_parser.add_argument("task_id", help="Task ID under runtime/taskpacks/ to review.")
    review_parser.add_argument(
        "--decision",
        required=True,
        choices=["accept", "reject", "escalate", "trim"],
        help="Human review decision to record.",
    )
    review_parser.add_argument("--notes", default=None, help="Optional reviewer notes.")
    review_parser.add_argument("--reviewer", default=None, help="Optional reviewer name or handle.")
    review_parser.add_argument(
        "--response-file",
        default=None,
        help="Optional response JSON path. Defaults to runtime/taskpacks/<task-id>/agent-response.json.",
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

    patch_pack_parser = subparsers.add_parser(
        "build-patch-packs",
        help="Generate bounded React and Spring Boot code patch packs from transition artifacts.",
    )
    patch_pack_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    patch_pack_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/code-patch-packs.",
    )

    patch_apply_parser = subparsers.add_parser(
        "build-patch-apply",
        help="Generate bounded patch-apply bundles that constrain file edits for one React page or Spring endpoint slice.",
    )
    patch_apply_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    patch_apply_parser.add_argument(
        "--target-project-dir",
        default=None,
        help="Optional target transition workspace root for merge-aware apply guidance.",
    )
    patch_apply_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/patch-apply-assistant.",
    )

    target_pack_parser = subparsers.add_parser(
        "build-target-pack",
        help="Compile target React project integration packs for generated UI artifacts.",
    )
    target_pack_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    target_pack_parser.add_argument("target_project_dir", help="Path to the target React project root.")
    target_pack_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/target-integration.",
    )

    target_assistant_parser = subparsers.add_parser(
        "build-target-assistant",
        help="Alias for build-target-pack with target-integration assistant outputs emphasized.",
    )
    target_assistant_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    target_assistant_parser.add_argument("target_project_dir", help="Path to the target React project root.")
    target_assistant_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/target-integration.",
    )

    bff_compiler_parser = subparsers.add_parser(
        "compile-bff-sql",
        help="Compile Oracle 19c BFF SQL endpoint packs from generated BFF artifacts.",
    )
    bff_compiler_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    bff_compiler_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/bff-sql-compiler.",
    )

    workspace_graph_parser = subparsers.add_parser(
        "build-workspace-graph",
        help="Build a multi-root workspace knowledge graph from the current analysis artifacts.",
    )
    workspace_graph_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    workspace_graph_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/workspace-graph.",
    )

    failure_replay_parser = subparsers.add_parser(
        "build-failure-replay",
        help="Regenerate failure replay lab bundles from runtime validation and error history.",
    )
    failure_replay_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    golden_tasks_parser = subparsers.add_parser(
        "evaluate-golden-tasks",
        help="Evaluate bounded task types and generate weak-model golden-task scorecards.",
    )
    golden_tasks_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    workspace_sync_parser = subparsers.add_parser(
        "build-workspace-sync",
        help="Compare patch packs with a target transition workspace and summarize sync state.",
    )
    workspace_sync_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    workspace_sync_parser.add_argument("target_project_dir", help="Path to the target React or web transition project root.")
    workspace_sync_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/workspace-sync.",
    )

    patch_validation_parser = subparsers.add_parser(
        "validate-patch-packs",
        help="Validate bounded React and Spring Boot patch packs before handing them to a weak model.",
    )
    patch_validation_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    patch_validation_parser.add_argument(
        "--target-project-dir",
        default=None,
        help="Optional target project root for merge-aware validation.",
    )
    patch_validation_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/patch-validation.",
    )

    repo_validation_parser = subparsers.add_parser(
        "build-repo-validation",
        help="Validate bounded patch slices against a target repo layout before asking Cline to apply them.",
    )
    repo_validation_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    repo_validation_parser.add_argument(
        "--target-project-dir",
        default=None,
        help="Optional target project root for repo-aware validation.",
    )
    repo_validation_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/repo-validation-gate.",
    )

    repair_tasks_parser = subparsers.add_parser(
        "build-repair-tasks",
        help="Build interactive repair tasks from runtime errors and patch validation failures.",
    )
    repair_tasks_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    progress_parser = subparsers.add_parser(
        "build-progress-report",
        help="Persist a management-facing progress snapshot and trend report.",
    )
    progress_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")

    handoff_parser = subparsers.add_parser(
        "build-handoff-packs",
        help="Generate developer handoff packs with implementation briefs and patch checklists.",
    )
    handoff_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    handoff_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/delivery-handoff.",
    )

    transition_map_parser = subparsers.add_parser(
        "build-transition-map",
        help="Generate a multi-repo transition map showing reusable roots and shared query families.",
    )
    transition_map_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    transition_map_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/llm-pack/multi-repo-transition-map.",
    )

    controlled_delivery_parser = subparsers.add_parser(
        "run-controlled-delivery",
        help="Run the controlled delivery pipeline: patch packs, sync, validation, repair, handoff, and slice delivery.",
    )
    controlled_delivery_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    controlled_delivery_parser.add_argument(
        "--target-project-dir",
        default=None,
        help="Optional target React or web transition project root.",
    )
    controlled_delivery_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/delivery-control.",
    )
    controlled_delivery_parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow the final delivery slice stage even when validation is incomplete.",
    )

    subagents_parser = subparsers.add_parser(
        "run-subagents",
        help="Plan and dispatch a bounded batch of prompt-pack subagent tasks.",
    )
    subagents_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    subagents_parser.add_argument(
        "--dispatch-mode",
        choices=["manual", "cline"],
        default="manual",
        help="Dispatch mode for the subagent batch.",
    )
    subagents_parser.add_argument("--max-tasks", type=int, default=4, help="Maximum task count to include.")
    subagents_parser.add_argument("--batch-size", type=int, default=2, help="Tasks per batch.")
    subagents_parser.add_argument(
        "--goal",
        dest="goals",
        action="append",
        default=[],
        help="Optional prompt goal filter. Can be repeated.",
    )
    subagents_parser.add_argument("--wait-seconds", type=int, default=120, help="Wait time for Cline responses.")
    subagents_parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval for Cline responses.")

    delivery_parser = subparsers.add_parser(
        "deliver-slice",
        help="Assemble a per-module delivery package from validated specs, BFF packs, integration packs, and generated code.",
    )
    delivery_parser.add_argument("analysis_dir", help="Path to a generated analysis artifact root.")
    delivery_parser.add_argument(
        "--module",
        dest="modules",
        action="append",
        default=[],
        help="Optional module name filter. Can be repeated.",
    )
    delivery_parser.add_argument(
        "--target-project-dir",
        default=None,
        help="Optional target React project root for integration-pack enrichment.",
    )
    delivery_parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <analysis_dir>/delivery-slices.",
    )
    delivery_parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow code generation and slice packaging even when validation results are incomplete.",
    )
    for current_parser in (
        analyze_parser,
        phase_runner_parser,
        phase_status_parser,
        build_taskpacks_parser,
        cheat_sheet_parser,
        task_studio_parser,
        cline_session_parser,
        validate_response_parser,
        retry_plan_parser,
        loop_parser,
        resume_loop_parser,
        loop_status_parser,
        benchmark_parser,
        wrapper_parser,
        dispatch_task_parser,
        report_parser,
        feedback_parser,
        llm_parser,
        provider_probe_parser,
        review_parser,
        codegen_parser,
        patch_pack_parser,
        patch_apply_parser,
        target_pack_parser,
        target_assistant_parser,
        bff_compiler_parser,
        workspace_graph_parser,
        failure_replay_parser,
        golden_tasks_parser,
        workspace_sync_parser,
        patch_validation_parser,
        repo_validation_parser,
        repair_tasks_parser,
        progress_parser,
        handoff_parser,
        transition_map_parser,
        controlled_delivery_parser,
        subagents_parser,
        delivery_parser,
    ):
        _add_cli_runtime_arguments(current_parser)
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


def _add_cli_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print extra debug details and tracebacks when something fails.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress progress lines for long-running commands.",
    )


def _run_command(args, reporter: CliReporter) -> int:
    if args.command == "serve-report":
        reporter.progress("Starting local report server")
        return serve_report(Path(args.report_dir), host=args.host, port=args.port)
    if args.command == "ingest-feedback":
        reporter.progress("Importing feedback entries")
        result = ingest_feedback(Path(args.analysis_dir), Path(args.feedback_file))
        reporter.info(f"Feedback imported into: {result['analysis_dir']}")
        reporter.info(
            "Feedback summary: "
            f"{result['feedback_entries']} entries, "
            f"{result['accepted']} accepted, "
            f"{result['rejected']} rejected, "
            f"{result['needs_follow_up']} follow-up, "
            f"{result['fallback_uses']} fallback uses"
        )
        return 0
    if args.command == "run-llm":
        reporter.progress("Preparing LLM artifact execution")
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
        reporter.info(f"LLM run complete: {result.run_id}")
        reporter.info(
            "LLM summary: "
            f"{result.artifact_kind} {result.artifact_name}, "
            f"model={result.model}, "
            f"input_tokens~{result.request_tokens_estimate}, "
            f"context={len(result.included_context_paths)} file(s)"
        )
        reporter.detail(f"Included context paths: {result.included_context_paths}")
        reporter.info(f"Feedback template: {result.feedback_template_path}")
        return 0
    if args.command == "validate-provider":
        reporter.progress("Validating provider models endpoint")
        result = validate_openai_compatible_provider(
            provider_base_url=args.provider_base_url,
            model=args.model,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
            timeout_seconds=args.timeout_seconds,
            perform_completion=not args.skip_completion,
        )
        reporter.info(f"Provider base URL: {result['provider_base_url']}")
        reporter.info(f"Models endpoint: {result['models_endpoint']}")
        reporter.info(f"Chat endpoint: {result['chat_endpoint']}")
        reporter.info(f"Auth configured: {str(result['auth_configured']).lower()}")
        reporter.info(f"Models OK: {str(result['models_ok']).lower()}")
        reporter.info(f"Completion OK: {str(result['completion_ok']).lower()}")
        reporter.info(f"Selected model: {result.get('selected_model') or 'None'}")
        if result.get("listed_models"):
            reporter.info(f"Listed models: {', '.join(result['listed_models'])}")
        if result.get("response_preview"):
            reporter.info(f"Response preview: {result['response_preview']}")
        if result.get("debug"):
            reporter.info("Debug:")
            for item in result["debug"]:
                reporter.info(f"- {item}")
        if args.analysis_dir:
            analysis_dir = Path(args.analysis_dir).resolve()
            runtime_dir = analysis_dir / "runtime"
            save_provider_health(runtime_dir, result)
            reporter.info(f"Provider health saved: {runtime_dir / 'provider-health.json'}")
            bundle = load_runtime_bundle(analysis_dir)
            run_state = bundle["run_state"]
            if run_state is not None:
                output = rerun_analysis_from_runtime_state(analysis_dir)
                refresh_runtime_artifacts(
                    output,
                    target_model_profile=run_state.target_model_profile,
                    dispatch_mode=run_state.dispatch_mode,
                    analysis_config=run_state.analysis_config,
                    provider_config=run_state.provider_config,
                )
        if not result["ok"]:
            return 1
        return 0
    if args.command == "validate-response":
        reporter.progress("Validating task response")
        analysis_dir = Path(args.analysis_dir).resolve()
        task_dir = analysis_dir / "runtime" / "taskpacks" / args.task_id
        response_path = Path(args.response_file).resolve() if args.response_file else None
        result = validate_task_response(
            analysis_dir=analysis_dir,
            task_dir=task_dir,
            response_path=response_path,
            prompt_mode=args.prompt_mode,
        )
        reporter.info(f"Validation status: {result.status}")
        reporter.info(
            f"Schema valid: {str(result.schema_valid).lower()}, "
            f"evidence valid: {str(result.evidence_valid).lower()}, "
            f"supported={len(result.supported_claims)}, "
            f"unsupported={len(result.unsupported_claims)}, "
            f"missing={len(result.missing_evidence)}"
        )
        if result.repair_prompt:
            reporter.detail(f"Repair prompt available in runtime/taskpacks/{args.task_id}/retry-plan.md")
        return 0
    if args.command == "retry-plan":
        analysis_dir = Path(args.analysis_dir).resolve()
        task_dir = analysis_dir / "runtime" / "taskpacks" / args.task_id
        retry_plan_path = task_dir / "retry-plan.json"
        if not retry_plan_path.exists():
            raise ValueError(f"Retry plan does not exist under {task_dir}")
        reporter.info(retry_plan_path.read_text(encoding="utf-8"))
        return 0
    if args.command in {"run-loop", "resume-loop"}:
        reporter.progress("Running bounded agent loop")
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
        reporter.info(
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
            raise ValueError(f"Runtime state does not exist under {runtime_dir}")
        history = load_task_history(runtime_dir)
        attempts = load_task_attempts(runtime_dir)
        reporter.info(
            f"Loop: status={run_state.status}, phase={run_state.current_phase}, "
            f"iteration={run_state.loop_iteration}, blocking={run_state.blocking_task_id or 'None'}"
        )
        reporter.info(f"Task attempts tracked: {len(attempts)}")
        reporter.info(f"Task history entries: {len(history)}")
        for item in history[-5:]:
            reporter.info(
                f"- {item.get('task_id')}: "
                f"{item.get('validation_status') or item.get('status') or 'unknown'} "
                f"({item.get('prompt_mode') or 'n/a'})"
            )
        return 0
    if args.command == "benchmark-prompts":
        reporter.progress("Benchmarking prompt families")
        report = benchmark_prompts(Path(args.analysis_dir).resolve())
        reporter.info(
            f"Prompt benchmark complete: {len(report['prompt_benchmark'])} prompt rows, "
            f"{len(report['goal_summary'])} goals"
        )
        return 0
    if args.command == "run-cline-wrapper":
        reporter.progress("Running bundled Cline wrapper")
        result = run_cline_wrapper(
            analysis_dir=Path(args.analysis_dir),
            cline_cmd=args.cline_cmd,
            watch=args.watch,
            once=args.once,
            resume=args.resume,
            skip_accepted=not args.no_skip_accepted,
            streaming=args.streaming,
            sanitize_output=not args.no_sanitize_output,
            validate_after_run=not args.no_validate_after_run,
            retry_on_fail=not args.no_retry_on_fail,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        reporter.info(
            f"Cline wrapper complete: processed={result['processed']}, repaired={result['repaired']}, "
            f"last_task={result['last_task_id'] or 'None'}"
        )
        return 0
    if args.command == "review-task":
        reporter.progress("Recording task review")
        record = record_task_review(
            analysis_dir=Path(args.analysis_dir),
            task_id=args.task_id,
            decision=args.decision,
            notes=args.notes,
            reviewer=args.reviewer,
            response_file=Path(args.response_file) if args.response_file else None,
        )
        analysis_dir = Path(args.analysis_dir).resolve()
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is not None:
            output = rerun_analysis_from_runtime_state(analysis_dir)
            refresh_runtime_artifacts(
                output,
                target_model_profile=run_state.target_model_profile,
                dispatch_mode=run_state.dispatch_mode,
                analysis_config=run_state.analysis_config,
                provider_config=run_state.provider_config,
            )
        reporter.info(
            f"Review recorded: task={record['task_id']}, decision={record['decision']}, reviewer={record['reviewer'] or 'n/a'}"
        )
        return 0
    if args.command == "build-cheatsheet":
        reporter.progress("Regenerating Cline cheat sheets")
        analysis_dir = Path(args.analysis_dir).resolve()
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise ValueError(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
        output = rerun_analysis_from_runtime_state(analysis_dir)
        refresh_runtime_artifacts(
            output,
            target_model_profile=run_state.target_model_profile,
            dispatch_mode=run_state.dispatch_mode,
            analysis_config=run_state.analysis_config,
            provider_config=run_state.provider_config,
        )
        analysis_paths = write_analysis_cheat_sheet(output)
        runtime_paths = write_runtime_cheat_sheet(
            analysis_dir=analysis_dir,
            run_state=output.runtime_state,
            blockers=output.blocking_unknowns,
            completeness=output.artifact_completeness,
        )
        reporter.info(f"LLM cheat sheet: {analysis_paths['markdown_path']}")
        reporter.info(f"Runtime cheat sheet: {runtime_paths['markdown_path']}")
        return 0
    if args.command == "build-task-studio":
        reporter.progress("Regenerating task studio")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise ValueError(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
        refresh_runtime_artifacts(
            output,
            target_model_profile=run_state.target_model_profile,
            dispatch_mode=run_state.dispatch_mode,
            analysis_config=run_state.analysis_config,
            provider_config=run_state.provider_config,
        )
        studio = build_task_studio(
            analysis_dir=analysis_dir,
            runtime_dir=analysis_dir / "runtime",
            output=output,
        )
        reporter.info(f"Task studio generated: {studio['task_count']} tasks")
        reporter.info(f"Task studio file: {analysis_dir / 'runtime' / 'task-studio.json'}")
        return 0
    if args.command == "build-cline-session":
        reporter.progress("Regenerating Cline session prompt bundles")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise ValueError(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
        refresh_runtime_artifacts(
            output,
            target_model_profile=run_state.target_model_profile,
            dispatch_mode=run_state.dispatch_mode,
            analysis_config=run_state.analysis_config,
            provider_config=run_state.provider_config,
        )
        manifest = build_cline_session_manifest(
            analysis_dir=analysis_dir,
            runtime_dir=analysis_dir / "runtime",
            output=output,
            cline_cmd=args.cline_cmd,
        )
        reporter.info(f"Cline session bundles: {manifest['task_count']}")
        reporter.info(f"Session manifest: {analysis_dir / 'runtime' / 'cline-session' / 'session-manifest.json'}")
        return 0
    if args.command == "generate-code":
        reporter.progress("Generating React and Spring Boot skeletons")
        generated = generate_transition_code(
            Path(args.analysis_dir),
            output_dir=Path(args.output_dir) if args.output_dir else None,
            require_validated=not args.allow_unvalidated,
        )
        base_dir = Path(args.output_dir).resolve() if args.output_dir else (Path(args.analysis_dir).resolve() / "codegen")
        reporter.info(f"Generated code skeletons: {len(generated)}")
        reporter.info(f"Output directory: {base_dir}")
        return 0
    if args.command == "build-patch-packs":
        reporter.progress("Generating bounded code patch packs")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is not None:
            refresh_runtime_artifacts(
                output,
                target_model_profile=run_state.target_model_profile,
                dispatch_mode=run_state.dispatch_mode,
                analysis_config=run_state.analysis_config,
                provider_config=run_state.provider_config,
            )
        manifest = build_code_patch_packs(
            analysis_dir=analysis_dir,
            output=output,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Patch packs generated: {manifest['patch_count']}")
        reporter.info(f"Patch pack manifest: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'code-patch-packs') / 'manifest.json'}")
        return 0
    if args.command == "build-patch-apply":
        reporter.progress("Generating bounded patch-apply assistant bundles")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = build_patch_apply_assistant(
            analysis_dir,
            output=output,
            target_project_dir=Path(args.target_project_dir) if args.target_project_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Patch-apply entries: {manifest['entry_count']}")
        reporter.info(
            f"Patch-apply manifest: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'patch-apply-assistant') / 'manifest.json'}"
        )
        return 0
    if args.command in {"build-target-pack", "build-target-assistant"}:
        reporter.progress("Inspecting target React project and building integration pack")
        manifest = build_target_project_integration_pack(
            Path(args.analysis_dir),
            Path(args.target_project_dir),
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(
            f"Target integration pack complete: {len(manifest['entries'])} entries, "
            f"target={manifest['target_project_dir']}"
        )
        output_dir = Path(args.output_dir).resolve() if args.output_dir else (Path(args.analysis_dir).resolve() / "llm-pack" / "target-integration")
        reporter.info(f"Assistant manifest: {output_dir / 'target-integration-assistant-manifest.json'}")
        return 0
    if args.command == "compile-bff-sql":
        reporter.progress("Compiling Oracle 19c BFF SQL endpoint packs")
        manifest = compile_oracle_bff_sql(
            Path(args.analysis_dir),
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(
            f"Oracle BFF compiler complete: {manifest['summary']['entry_count']} entries, "
            f"read={manifest['summary']['read_endpoints']}, "
            f"command={manifest['summary']['command_endpoints']}"
        )
        return 0
    if args.command == "build-workspace-graph":
        reporter.progress("Building multi-root workspace graph")
        graph = build_workspace_graph(
            Path(args.analysis_dir),
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(
            f"Workspace graph complete: roots={graph['summary']['root_count']}, "
            f"nodes={graph['summary']['node_count']}, "
            f"cross_root_edges={graph['summary']['cross_root_edges']}"
        )
        return 0
    if args.command == "build-failure-replay":
        reporter.progress("Rebuilding failure replay lab")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is not None:
            refresh_runtime_artifacts(
                output,
                target_model_profile=run_state.target_model_profile,
                dispatch_mode=run_state.dispatch_mode,
                analysis_config=run_state.analysis_config,
                provider_config=run_state.provider_config,
            )
        manifest = build_failure_replay_lab(
            analysis_dir=analysis_dir,
            runtime_dir=analysis_dir / "runtime",
            output=output,
        )
        reporter.info(f"Failure replay entries: {manifest['entry_count']}")
        reporter.info(f"Failure replay manifest: {analysis_dir / 'runtime' / 'failure-replay' / 'manifest.json'}")
        return 0
    if args.command == "evaluate-golden-tasks":
        reporter.progress("Evaluating weak-model golden tasks")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is not None:
            refresh_runtime_artifacts(
                output,
                target_model_profile=run_state.target_model_profile,
                dispatch_mode=run_state.dispatch_mode,
                analysis_config=run_state.analysis_config,
                provider_config=run_state.provider_config,
            )
        report = evaluate_golden_tasks(
            analysis_dir=analysis_dir,
            runtime_dir=analysis_dir / "runtime",
            output=output,
        )
        reporter.info(f"Golden task types evaluated: {report['task_type_count']}")
        reporter.info(f"Golden task report: {analysis_dir / 'runtime' / 'golden-tasks' / 'golden-task-evaluation.json'}")
        return 0
    if args.command == "build-workspace-sync":
        reporter.progress("Building transition workspace sync report")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = build_transition_workspace_sync(
            analysis_dir,
            Path(args.target_project_dir),
            output=output,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Workspace sync entries: {manifest['entry_count']}")
        reporter.info(f"Workspace sync report: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'workspace-sync') / 'workspace-sync.json'}")
        return 0
    if args.command == "validate-patch-packs":
        reporter.progress("Validating bounded patch packs")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = validate_patch_packs(
            analysis_dir,
            output=output,
            target_project_dir=Path(args.target_project_dir) if args.target_project_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Patch validation entries: {manifest['entry_count']}")
        reporter.info(f"Patch validation report: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'patch-validation') / 'patch-validation.json'}")
        return 0
    if args.command == "build-repo-validation":
        reporter.progress("Validating bounded patch slices against the target repo layout")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = build_repo_validation_gate(
            analysis_dir,
            output=output,
            target_project_dir=Path(args.target_project_dir) if args.target_project_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Repo validation entries: {manifest['entry_count']}")
        reporter.info(
            f"Repo validation report: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'repo-validation-gate') / 'repo-validation.json'}"
        )
        return 0
    if args.command == "build-repair-tasks":
        reporter.progress("Building interactive repair tasks")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        runtime_dir = analysis_dir / "runtime"
        patch_validation_report = None
        repo_validation_report = None
        patch_validation_path = analysis_dir / "llm-pack" / "patch-validation" / "patch-validation.json"
        if patch_validation_path.exists():
            import json
            patch_validation_report = json.loads(patch_validation_path.read_text(encoding="utf-8"))
        repo_validation_path = analysis_dir / "llm-pack" / "repo-validation-gate" / "repo-validation.json"
        if repo_validation_path.exists():
            import json
            repo_validation_report = json.loads(repo_validation_path.read_text(encoding="utf-8"))
        manifest = build_repair_tasks(
            analysis_dir,
            runtime_dir=runtime_dir,
            runtime_error_summary=output.runtime_error_summary,
            patch_validation_report=patch_validation_report,
            repo_validation_report=repo_validation_report,
        )
        reporter.info(f"Repair tasks: {manifest['entry_count']}")
        reporter.info(f"Repair task manifest: {runtime_dir / 'repair-tasks' / 'repair-tasks.json'}")
        return 0
    if args.command == "build-progress-report":
        reporter.progress("Updating management progress report")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        report = update_progress_report(
            analysis_dir,
            runtime_dir=analysis_dir / "runtime",
            output=output,
        )
        reporter.info(f"Progress snapshots: {report['snapshot_count']}")
        reporter.info(f"Progress report: {analysis_dir / 'runtime' / 'progress' / 'progress-report.json'}")
        return 0
    if args.command == "build-handoff-packs":
        reporter.progress("Generating developer handoff packs")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = build_developer_handoff_packs(
            analysis_dir,
            output=output,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Handoff packs: {manifest['entry_count']}")
        reporter.info(f"Handoff manifest: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'delivery-handoff') / 'manifest.json'}")
        return 0
    if args.command == "build-transition-map":
        reporter.progress("Generating multi-repo transition map")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = build_multi_repo_transition_map(
            analysis_dir,
            output=output,
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )
        reporter.info(f"Transition roots: {manifest['root_count']}")
        reporter.info(f"Transition map: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'llm-pack' / 'multi-repo-transition-map') / 'multi-repo-transition-map.json'}")
        return 0
    if args.command == "run-controlled-delivery":
        reporter.progress("Running controlled delivery pipeline")
        analysis_dir = Path(args.analysis_dir).resolve()
        output = rerun_analysis_from_runtime_state(analysis_dir)
        manifest = run_controlled_delivery(
            analysis_dir,
            output=output,
            target_project_dir=Path(args.target_project_dir) if args.target_project_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            allow_unvalidated=args.allow_unvalidated,
        )
        reporter.info(f"Controlled delivery steps: {manifest['step_count']}")
        reporter.info(f"Controlled delivery manifest: {(Path(args.output_dir).resolve() if args.output_dir else analysis_dir / 'delivery-control') / 'controlled-delivery-manifest.json'}")
        return 0
    if args.command == "run-subagents":
        reporter.progress("Planning and dispatching bounded subagent batches")
        payload = run_subagent_batches(
            Path(args.analysis_dir),
            dispatch_mode=args.dispatch_mode,
            max_tasks=args.max_tasks,
            batch_size=args.batch_size,
            goal_filters=args.goals,
            wait_seconds=args.wait_seconds,
            poll_seconds=args.poll_seconds,
        )
        reporter.info(
            f"Subagent batches complete: {payload['batch_count']} batch(es), "
            f"dispatch={payload['dispatch_mode']}"
        )
        return 0
    if args.command == "deliver-slice":
        reporter.progress("Assembling slice delivery packages")
        manifest = deliver_slices(
            Path(args.analysis_dir),
            module_names=args.modules,
            target_project_dir=Path(args.target_project_dir) if args.target_project_dir else None,
            output_dir=Path(args.output_dir) if args.output_dir else None,
            allow_unvalidated=args.allow_unvalidated,
        )
        reporter.info(
            f"Slice delivery complete: {manifest['delivery_count']} module package(s), "
            f"target={manifest.get('target_project_dir') or 'None'}"
        )
        return 0
    if args.command == "phase-status":
        bundle = load_runtime_bundle(Path(args.analysis_dir))
        run_state = bundle["run_state"]
        phase_states = bundle["phase_states"]
        blockers = bundle["blocking_unknowns"]
        completeness = bundle["artifact_completeness"]
        if run_state is None:
            raise ValueError(f"Runtime state does not exist under {Path(args.analysis_dir) / 'runtime'}")
        reporter.info(f"Run ID: {run_state.run_id}")
        reporter.info(
            f"Runtime: status={run_state.status}, phase={run_state.current_phase}, "
            f"loop={run_state.loop_iteration}, model_profile={run_state.target_model_profile}"
        )
        if completeness is not None:
            reporter.info(
                f"Artifacts: {completeness.completed_count}/{completeness.required_count} required artifacts complete"
            )
        reporter.info(f"Blockers: {len(blockers)}")
        for phase_state in phase_states:
            reporter.info(
                f"- {phase_state.phase}: {phase_state.status}, completion={phase_state.completion_score}/100, "
                f"blockers={len(phase_state.blockers)}"
            )
        return 0
    if args.command == "build-taskpacks":
        reporter.progress("Regenerating task packs from runtime blockers")
        analysis_dir = Path(args.analysis_dir).resolve()
        bundle = load_runtime_bundle(analysis_dir)
        run_state = bundle["run_state"]
        if run_state is None:
            raise ValueError(f"Runtime state does not exist under {analysis_dir / 'runtime'}")
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
        written = write_taskpacks(taskpacks, analysis_dir / "runtime", include_compiled_context=True)
        reporter.info(f"Task packs generated: {len(written)}")
        for path in written[:10]:
            reporter.info(f"- {path}")
        return 0
    if args.command == "dispatch-task":
        analysis_dir = Path(args.analysis_dir).resolve()
        task_dir = analysis_dir / "runtime" / "taskpacks" / args.task_id
        taskpack = load_taskpack(task_dir)
        if taskpack is None:
            raise ValueError(f"Task pack does not exist or is invalid: {task_dir}")
        if args.mode == "manual":
            reporter.info(f"Task pack ready for manual execution: {task_dir}")
            return 0
        request_path = emit_cline_task(taskpack, task_dir, analysis_dir / "runtime")
        reporter.info(f"Cline request emitted: {request_path}")
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
        reporter.progress("Running full analysis and runtime phase orchestration")
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
        reporter.progress("Refreshing prompt benchmark outputs")
        benchmark_prompts(Path(output.output_dir))
    else:
        reporter.progress("Running analysis")
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
        reporter.progress("Refreshing runtime artifacts")
        refresh_runtime_artifacts(
            output,
            target_model_profile=args.model_profile,
            dispatch_mode="manual",
            analysis_config=analysis_config,
        )
        reporter.progress("Refreshing prompt benchmark outputs")
        benchmark_prompts(Path(output.output_dir))

    fatal_count = len([item for item in output.diagnostics if item.severity == "fatal"])
    error_count = len([item for item in output.diagnostics if item.severity == "error"])
    reporter.info(f"Analysis complete: {output.output_dir}")
    reporter.info(
        "Artifacts: "
        f"{len(output.manifest)} files, "
        f"{len(output.pascal_units)} Pascal units, "
        f"{len(output.forms)} forms, "
        f"{len(output.resolved_queries)} resolved queries"
    )
    reporter.info(
        "Workspace: "
        f"{len(output.inventory.scan_roots)} scan roots, "
        f"{len(output.inventory.external_roots)} external roots, "
        f"{len(output.inventory.missing_search_paths)} missing paths, "
        f"{len(output.inventory.unresolved_search_paths)} unresolved paths"
    )
    reporter.info(f"Diagnostics: {len(output.diagnostics)} total, {error_count} error, {fatal_count} fatal")
    report_path = Path(output.output_dir) / "report" / "index.html"
    if report_path.exists():
        reporter.info(f"Web report: {report_path}")
    runtime_path = Path(output.output_dir) / "runtime" / "run-state.json"
    if runtime_path.exists():
        reporter.info(f"Runtime state: {runtime_path}")
    return 1 if args.fail_on_fatal and fatal_count else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reporter = CliReporter(
        verbose=bool(getattr(args, "verbose", False)),
        progress_enabled=not bool(getattr(args, "no_progress", False)),
    )
    try:
        return _run_command(args, reporter)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            reporter.error(render_cli_exception(RuntimeError(exc.code), command=getattr(args, "command", None), verbose=reporter.verbose))
            return 2
        raise
    except Exception as exc:  # noqa: BLE001
        reporter.error(render_cli_exception(exc, command=getattr(args, "command", None), verbose=reporter.verbose))
        return 2


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
