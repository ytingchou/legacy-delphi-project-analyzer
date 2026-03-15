# Legacy Delphi Project Analyzer

`legacy-delphi-project-analyzer` scans a legacy Delphi project, parses large
`.pas`, `.dfm`, and custom SQL XML files, and emits layered artifacts designed
for a 128k-token LLM to continue a React + Spring Boot migration.

## Features

- Multi-phase CLI workflow: discovery, parsing, analysis, packaging, learning
- Heuristic Delphi and DFM analyzers for large legacy codebases
- Binary DFM heuristic support with actionable diagnostics when text export is needed
- SQL XML resolver that understands `main-query`, `sub-query`, internal/external
  references, and raw-copy semantics
- LLM-ready artifact packaging in JSON and Markdown
- Token-aware load bundles, business-flow dossiers, and leadership-facing complexity summaries
- Static HTML dashboard output plus `serve-report` for local report preview
- Diagnostics with prompt guidance for unresolved or low-confidence areas
- Rule-driven knowledge store for reusable overrides, learned patterns, and suggested overrides
- Prompt-pack generation and failure triage bundles tailored for low-capability 128k-token LLMs
- Delphi workspace resolution for external XE search paths and shared repos outside the main project root
- Prompt closure artifacts with verification prompts, acceptance checks, and minimal repro bundles
- Feedback learning plus prompt-effectiveness scoring for management reporting
- Direct OpenAI-compatible LLM execution with configurable token limits
- Module-level transition spec generation with React pages, Spring endpoints, DTOs, readiness scores, and first-slice recommendations
- Compact BFF SQL logic artifacts for Spring Boot + Oracle 19c implementation handoff
- Runtime orchestration outputs for multi-phase loops, blocker queues, and resumable handoff state
- Qwen3-oriented model profiles, task packs, and file-based Cline inbox/outbox integration

## Usage

```bash
python3 -m legacy_delphi_project_analyzer analyze /path/to/project
```

Or after installation:

```bash
legacy-delphi-analyzer analyze /path/to/project --output-dir artifacts
```

To generate runtime phase state for later LLM loops:

```bash
legacy-delphi-analyzer run-phases /path/to/project \
  --output-dir artifacts \
  --model-profile qwen3_128k_weak
```

Optional flags:

- `--phase discover --phase parse --phase analyze --phase package --phase learn`
- `--rules-dir rules`
- `--workspace-config workspace.json`
- `--search-path ../PDSS_Common --search-path ../PDSS_SQL`
- `--path-var PDSS_COMMON=../PDSS_Common --path-var PDSS_SQL=../PDSS_SQL`
- `--max-artifact-chars 40000`
- `--max-artifact-tokens 10000`
- `--target-model qwen3-128k`
- `--model-profile qwen3_128k_weak`
- `--fail-on-fatal`

Inspect the current runtime phase state:

```bash
legacy-delphi-analyzer phase-status /path/to/artifacts
```

Build blocker task packs for Cline or later loop execution:

```bash
legacy-delphi-analyzer build-taskpacks /path/to/artifacts --max-tasks 5
```

Dispatch one task pack into the file-based Cline inbox:

```bash
legacy-delphi-analyzer dispatch-task /path/to/artifacts task-query-orderlookup-placeholders
```

Serve the generated web report locally:

```bash
legacy-delphi-analyzer serve-report /path/to/artifacts/report
```

Import accepted or rejected LLM feedback back into the analyzer:

```bash
legacy-delphi-analyzer ingest-feedback /path/to/artifacts /path/to/feedback.json
```

Run a prompt pack or failure triage directly against an OpenAI-compatible provider:

```bash
legacy-delphi-analyzer run-llm /path/to/artifacts \
  --prompt-name OrderLookupClarify \
  --provider-base-url http://your-provider-host:8000/v1 \
  --model qwen3-32b \
  --api-key-env OPENAI_API_KEY \
  --token-limit 6000 \
  --output-token-limit 1200
```

This writes run outputs under `artifacts/llm-runs/`, including a feedback template JSON
that can be edited and passed to `ingest-feedback`.

## v1.0 Transition Specs

`analyze` now emits a formal transition spec for each inferred module under:

- `llm-pack/transition-specs/`
- `intermediate/transition_specs.json`

Each spec includes:

- Readiness score and readiness level
- Recommended first migration slice
- React page proposals with routes, inputs, actions, and data dependencies
- Spring Boot endpoint proposals with HTTP methods and paths
- DTO suggestions derived from SQL parameters, select lists, and DFM inputs
- Assumptions, risks, and cross-cutting concerns

Prompt packs now also include `*SpecValidate` artifacts so your internal weak LLM can
check whether the generated transition spec is still grounded in the available evidence
before the team starts implementing React or Spring code.

## v1.3 Backend SQL Handoff

`analyze` now emits backend implementation handoff artifacts under:

- `llm-pack/bff-sql/`
- `intermediate/bff_sql_artifacts.json`

Each BFF SQL artifact is intentionally compact so a weak `qwen3`-class model can load
one endpoint at a time and stay well below the practical token budget. The artifacts
include:

- Oracle 19c notes and placeholder strategy
- Request/response field summaries
- Repository and service-layer implementation steps
- Endpoint-sized Spring Boot BFF contracts

`load-plan.json` also includes backend-specific bundle ordering, and prompt packs such
as `*BffSql` can be executed with `run-llm` to generate one bounded backend slice at a time.

## Runtime Orchestration

`run-phases` emits a resumable runtime workspace under `artifacts/runtime/`, including:

- `run-state.json`
- `blocking-unknowns.json`
- `artifact-completeness.json`
- `state-summary.md`
- `phase-delta.md`
- `phases/<phase>/phase-status.json`
- `phases/<phase>/phase-summary.md`

This is the foundation for the later agent loop, Cline subagent task packs, and auto-compact LLM orchestration workflow.

## Task Packs And Model Profiles

Task packs are emitted under `artifacts/runtime/taskpacks/` and include:

- `agent-task.md`
- `agent-context.json`
- `agent-run-config.json`
- `agent-expected-output-schema.json`
- `agent-handoff-template.json`
- `taskpack.json`

Built-in model profiles currently include:

- `qwen3_128k_weak`
- `qwen3_128k_validate`
- `strong_reasoning`

The file-based Cline adapter writes requests to `artifacts/runtime/cline-inbox/<task-id>/request.json`
and reads responses from `artifacts/runtime/cline-outbox/<task-id>/response.json`.

## External Delphi XE Search Paths

If the Delphi XE project references shared code outside the current repo, the analyzer can now
scan those roots too. This is the right way to handle layouts such as:

- `main_project/`
- `../PDSS_Common/`
- `../PDSS_SQL/`

You can provide external roots directly:

```bash
legacy-delphi-analyzer analyze /path/to/main_project \
  --search-path ../PDSS_Common \
  --search-path ../PDSS_SQL
```

Or define a workspace config with Delphi-style variables:

```json
{
  "scan_roots": ["$(PDSS_COMMON)", "$(PDSS_SQL)"],
  "search_paths": ["$(PDSS_COMMON)", "$(PDSS_SQL)"],
  "path_variables": {
    "PDSS_COMMON": "../PDSS_Common",
    "PDSS_SQL": "../PDSS_SQL"
  }
}
```

Then run:

```bash
legacy-delphi-analyzer analyze /path/to/main_project \
  --workspace-config /path/to/main_project/workspace.json
```

The analyzer will also read `.dproj` and `.cfg` search paths automatically. If a path is missing
or a Delphi variable like `$(PDSS_SQL)` is unresolved, the run emits diagnostics, failure triage
bundles, and prompt-ready hints so a weak internal LLM can still help close the gap.

## v0.10 Highlights

- Prompt packs and failure triage can now be executed directly against an OpenAI-compatible provider.
- You can control input context size with `--token-limit` and completion size with `--output-token-limit`.
- Each LLM run now produces a saved run artifact and a feedback template so model output can be learned back into the analyzer.

## Override File

If `--rules-dir` points to a folder containing `overrides.json`, the analyzer
will load it. Supported keys:

```json
{
  "ignore_globs": ["vendor/**"],
  "module_overrides": {
    "frmOrderEntry": "OrderManagement"
  },
  "xml_aliases": {
    "pricing": "pricing.xml"
  },
  "placeholder_notes": {
    "OrderLookup": "fPriceCheckRule is injected by Delphi business rules"
  },
  "query_hints": {
    "OrderLookup": "Used by price-check screen before submitting manual overrides"
  },
  "transition_hints": {
    "OrderEntry": "Implement the read-only search slice before any write path."
  }
}
```
