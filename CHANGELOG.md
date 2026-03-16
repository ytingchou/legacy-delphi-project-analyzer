# Changelog

## v2.5.0

- Added Oracle 19c BFF SQL compiler packs with per-endpoint manifests and Markdown guides.
- Added operation-kind detection, DTO-to-bind mapping, select-field extraction, and pagination/sort heuristics.
- Added semantic checks for bind coverage, unresolved placeholders, DML terminators, and service-step complexity.
- Added `compile-bff-sql` and regression coverage for compiled Oracle BFF artifacts.

## v2.4.0

- Added target-project integration pack generation for existing React transition repositories.
- Added target project inspection for feature directories, route files, API clients, state files, and known routes.
- Added `build-target-pack` to emit target-aware integration manifests and per-page merge guides.
- Added regression coverage for target-project feature and route detection.

## v2.3.0

- Added prompt benchmarking and template-tuning reports under `runtime/prompt-benchmark.*`.
- Added prompt-family scoring from task-pack metadata, validation history, and feedback history.
- Added `benchmark-prompts` plus automatic benchmark refresh after `analyze`, `run-phases`, and loop runs.
- Added regression coverage for prompt benchmark generation and tuning recommendations.

## v2.2.0

- Added validator-driven retry planning with persisted `retry-plan.json` and `retry-plan.md` per task pack.
- Added explicit validation failure categories, repair prompts, and retry context trimming for weak-model follow-up attempts.
- Added compact-context support for prior validator feedback so later retries carry only the most useful correction hints.
- Added regression coverage for unsupported-claim and schema-error retry-plan generation.

## v2.1.0

- Added response validation for bounded task output, including schema checks, evidence checks, persisted validation records, and the `validate-response` CLI command.
- Added a bounded agent loop with `run-loop`, `resume-loop`, and `loop-status`, including task attempts, task history, loop summaries, provider dispatch, and file-based Cline waiting.
- Added auto-compact task context generation with compiled context artifacts, trusted-facts snapshots, and taskpack-compiled payloads tuned for weak `qwen3`-class models.
- Added file-based Cline loop orchestration through the bounded loop runtime, so task packs can be emitted, waited on, validated, and learned back into the analyzer.
- Added validated transition-to-code skeleton generation with `generate-code`, producing React and Spring Boot starter files under `codegen/`.
- Added regression coverage for validator acceptance/rejection, manual and Cline loop execution, and validated code skeleton generation.

## v2.0.0

- Added bounded loop support for file-based Cline dispatch and response waiting.
- Added loop state, task-attempt tracking, and task-history persistence under `runtime/`.
- Added CLI workflows for loop execution and loop inspection.

## v1.9.0

- Added auto-compact compiled task contexts and trusted-facts snapshots for weak-model execution.
- Added compiled taskpack payloads that narrow context to a single compact artifact before provider execution.

## v1.8.0

- Added bounded agent-loop execution that dispatches one task at a time, validates output, and learns accepted results back into the knowledge store.
- Added manual, provider, and Cline dispatch modes for loop execution.

## v1.7.0

- Added response validators for schema correctness and evidence grounding.
- Added persisted `validation-results.json` plus per-task validation records under `runtime/taskpacks/<task-id>/`.

## v1.5.0

- Added `llm-pack/backend-sql-manifest.json` and `llm-pack/backend-sql-guide.md` for qwen3-friendly backend load order and prompt-pack selection.
- Added `llm-pack/ui-handoff-manifest.json` and `llm-pack/ui-handoff-guide.md` for page-level pseudo UI, reference UI, and integration sequencing.
- Added per-entry recommended load order, prompt-pack names, and bundle token estimates so weak models can stay within practical context limits.
- Added regression coverage for the new compact handoff guides and manifests.

## v1.4.0

- Extended the boss summary, runtime handoff manifest, and static web dashboard with backend SQL and UI handoff visibility.
- Added completeness tracking for BFF SQL, UI pseudo, UI reference, and UI integration artifacts.
- Expanded phase definitions so transition and handoff phases now explicitly require weak-model-ready backend/UI artifact families.
- Added regression coverage for runtime completeness and dashboard rendering of the new handoff artifact families.

## v1.3.0

- Added compact BFF SQL logic artifacts under `llm-pack/bff-sql/` plus `intermediate/bff_sql_artifacts.json`.
- Added qwen3-friendly backend SQL prompt packs so weak 128k-token models can generate one Spring Boot Oracle 19c implementation slice at a time.
- Added specialized backend load bundles and load-plan sections for bounded BFF SQL generation.
- Added regression coverage for BFF SQL artifacts, bundles, and prompt-pack generation.

## v1.2.0

- Added model profiles for weak 128k-token LLMs, including `qwen3_128k_weak` and `qwen3_128k_validate`.
- Added blocker-to-task-pack conversion under `runtime/taskpacks/` with task pack indexes, run config, handoff templates, and LLM-compatible JSON payloads.
- Added file-based Cline inbox/outbox adapters plus `build-taskpacks` and `dispatch-task` CLI commands.
- Added regression coverage for qwen3 task-pack budgets, task-pack generation, and Cline dispatch round-trips.

## v1.1.0

- Added runtime orchestration artifacts including `run-state.json`, `blocking-unknowns.json`, `artifact-completeness.json`, and per-phase summaries under `runtime/phases/`.
- Added `run-phases` and `phase-status` CLI commands to turn a normal analysis run into a resumable multi-phase workflow.
- Added automatic handoff manifest generation under `llm-pack/handoff-manifest.json`.
- Added regression coverage for runtime orchestration outputs and CLI integration.

## v1.0.0

- Added formal module-level transition specs with readiness scores, migration strategies, recommended first slices, React page proposals, Spring endpoint proposals, and DTO suggestions.
- Added packaged transition spec artifacts under `llm-pack/transition-specs/` plus `intermediate/transition_specs.json`.
- Added `validate_transition_spec` prompt packs so low-capability LLMs can verify generated specs against recovered evidence before implementation.
- Extended management reporting and the static web dashboard with transition readiness and first-slice visibility.
- Added feedback learning for validated transition specs and regression coverage for transition spec generation.

## v0.10.0

- Added direct OpenAI-compatible LLM execution with the new `run-llm` CLI command.
- Added provider configuration for base URL, model, API key, prompt mode, and configurable input/output token limits.
- Added `llm-runs/` outputs containing raw run artifacts, rendered response markdown, and feedback templates ready for `ingest-feedback`.
- Added regression coverage for provider request formatting and context-token budgeting.

## v0.9.1

- Removed SQL XML parameter `data_type` validation so custom types no longer emit warnings.
- Relaxed SQL XML parameter name parsing so names with or without a leading `:` are accepted.
- Added regression coverage for mixed parameter-name styles and custom parameter types.

## v0.9.0

- Added prompt effectiveness scoring from imported feedback, including per-prompt and per-goal success metrics.
- Added `prompt-effectiveness.json` and `prompt-effectiveness.md` outputs for management reporting.
- Added prompt effectiveness sections to the boss summary and static web dashboard.
- Added regression coverage to ensure imported feedback appears in later analysis reports.

## v0.8.0

- Added `ingest-feedback` so accepted or rejected LLM answers can be imported back into the analyzer.
- Added automatic rule extraction for path variables, search paths, query hints, placeholder notes, and transition hints.
- Added persistent `accepted_rules.json`, `feedback-log.json`, `rejected_rules.json`, and `feedback-insights.md`.
- Added bootstrap rule loading so accepted workspace feedback can unblock the next analysis run automatically.

## v0.7.0

- Added prompt closure artifacts with goal-specific prompt packs, verification prompts, and acceptance checks.
- Added minimal repro bundle JSON outputs for prompt packs and failure triage cases.
- Added workspace-resolution and flow-summary prompt goals so weak internal LLMs can solve one bounded problem at a time.
- Added closure-summary packaging and regression coverage for prompt closure artifacts.

## v0.6.0

- Added Delphi workspace resolution for external search paths referenced outside the main repo.
- Added automatic `.dproj` and `.cfg` search-path parsing, plus `--workspace-config`, `--search-path`, and `--path-var`.
- Added workspace diagnostics, prompt hints, and report coverage for missing or unresolved external repositories such as shared `PDSS_Common` and `PDSS_SQL` roots.
- Added regression coverage for relative search paths and custom Delphi path variables.

## v0.5.0

- Added prompt-pack generation for module transition, query clarification, and unknown resolution.
- Added failure triage bundles with minimal context and fallback prompts.
- Added `--target-model` to specialize prompt output for weak or constrained internal LLMs.
- Extended packaged outputs and regression coverage for prompt and triage artifacts.

## v0.4.0

- Added validation for `overrides.json` with non-fatal diagnostics for bad keys or types.
- Added `knowledge-insights.md` and `suggested_overrides.json` generation.
- Added close-match XML alias suggestions and placeholder-oriented query hints.
- Included knowledge artifacts in packaged outputs and regression coverage.

## v0.3.0

- Added business-flow extraction from DFM event bindings to Pascal method heuristics.
- Added token-aware artifact chunking, load bundles, and a project-level load plan.
- Added boss-facing executive summaries, complexity scoring, and a static web dashboard.
- Added a `serve-report` CLI command for local report preview.

## v0.2.0

- Added heuristic binary DFM parsing with diagnostics and prompt hints.
- Hardened Pascal extraction for published members, component fields, and event handlers.
- Expanded SQL XML validation for duplicate queries, invalid copy targets, unknown parameter types, and cycle traces.
- Added broader regression coverage for binary DFM projects and SQL XML edge cases.
