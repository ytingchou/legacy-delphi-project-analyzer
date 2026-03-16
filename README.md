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
- Response validators, validator-driven retry plans, bounded agent-loop execution, auto-compact task contexts, and validated code skeleton generation

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

Validate one task response against its schema and the recovered legacy evidence:

```bash
legacy-delphi-analyzer validate-response /path/to/artifacts task-query-orderlookup-placeholders
legacy-delphi-analyzer retry-plan /path/to/artifacts task-query-orderlookup-placeholders
```

Run or resume the bounded orchestration loop:

```bash
legacy-delphi-analyzer run-loop /path/to/artifacts \
  --dispatch-mode cline \
  --max-loops 5

legacy-delphi-analyzer resume-loop /path/to/artifacts --max-loops 5
legacy-delphi-analyzer loop-status /path/to/artifacts
legacy-delphi-analyzer benchmark-prompts /path/to/artifacts
```

Dispatch one task pack into the file-based Cline inbox:

```bash
legacy-delphi-analyzer dispatch-task /path/to/artifacts task-query-orderlookup-placeholders
```

Build a target-project integration pack against an existing React transition repo:

```bash
legacy-delphi-analyzer build-target-pack /path/to/artifacts /path/to/react-project
```

Compile Oracle 19c BFF endpoint packs for Spring Boot implementation:

```bash
legacy-delphi-analyzer compile-bff-sql /path/to/artifacts
```

Build a multi-repo workspace graph:

```bash
legacy-delphi-analyzer build-workspace-graph /path/to/artifacts
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

## v2.2 Validator-Driven Retry

Each validated task now persists:

- `runtime/taskpacks/<task-id>/validation-record.json`
- `runtime/taskpacks/<task-id>/retry-plan.json`
- `runtime/taskpacks/<task-id>/retry-plan.md`

The retry plan classifies failures into bounded categories such as schema errors,
missing evidence, and unsupported claims. It also emits a repair prompt and a
smaller retry context set so weak `qwen3`-class models can retry with less noise.

## v2.3 Prompt Benchmarking

Prompt benchmarking now writes:

- `runtime/prompt-benchmark.json`
- `runtime/prompt-benchmark.md`
- `runtime/prompt-template-tuning.json`

These reports combine task-pack metadata, validation history, and feedback history
to show which prompt families are stable, which ones still overrun weak-model
capacity, and whether `primary`, `fallback`, or `verification` templates should
be preferred by task type.

## v2.4 Target Project Integration Packs

You can now point the analyzer at a separate target React project. It will inspect
existing feature directories, route files, API clients, and state files, then emit
target-aware handoff artifacts under:

- `llm-pack/target-integration/target-project-summary.json`
- `llm-pack/target-integration/target-integration-manifest.json`
- `llm-pack/target-integration/*.md`
- `llm-pack/target-integration/*.json`

These artifacts are intended for weak LLMs and Cline subagents that need to merge
the generated UI into an already-existing transition project without loading the
whole target repo into context.

## v2.5 Oracle BFF Compiler

The analyzer can now compile endpoint-sized Oracle BFF packs under:

- `llm-pack/bff-sql-compiler/oracle-bff-manifest.json`
- `llm-pack/bff-sql-compiler/oracle-bff-guide.md`
- `llm-pack/bff-sql-compiler/*.md`
- `llm-pack/bff-sql-compiler/*.json`

These packs add operation kind detection, DTO-to-bind mappings, select-field summaries,
pagination/sort heuristics, and semantic checks for unresolved placeholders and DML
terminator rules. They are designed to give weak LLMs a smaller contract than the
full transition spec plus full SQL artifact set.

## v2.6 Multi-Repo Workspace Graph

The analyzer can now emit a workspace knowledge graph under:

- `llm-pack/workspace-graph/workspace-graph.json`
- `llm-pack/workspace-graph/workspace-graph.dot`
- `llm-pack/workspace-graph/workspace-graph.md`

This graph tracks project roots, external roots, Pascal units, forms, SQL XML files,
queries, and transition modules. It is especially useful when your Delphi XE setup
depends on shared repos such as `PDSS_Common` or `PDSS_SQL`, because later loops can
use the graph instead of re-reading the entire workspace layout.

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

Runtime completeness now also tracks whether backend SQL, UI pseudo, UI reference,
and UI integration artifacts were generated, so the handoff state reflects more than
just core transition specs.

## v1.4 Reporting And Handoff

The static web dashboard and the handoff manifest now expose the weak-model delivery
artifacts directly:

- Backend SQL handoff counts and endpoint/query tables
- UI integration handoff counts and route/feature-dir tables
- Runtime completeness flags for backend SQL and frontend artifact families
- Handoff manifest entries that explicitly point downstream LLMs to:
  - `llm-pack/bff-sql/`
  - `llm-pack/ui-pseudo/`
  - `llm-pack/ui-reference/`
  - `llm-pack/ui-integration/`

## v1.5 Compact Guides For Weak Models

To keep `qwen3`-class models away from oversized contexts, `analyze` now emits two
additional compact guides:

- `llm-pack/backend-sql-manifest.json`
- `llm-pack/backend-sql-guide.md`
- `llm-pack/ui-handoff-manifest.json`
- `llm-pack/ui-handoff-guide.md`

These files are deliberately small. They tell the weak model:

- which bundle to load first
- which prompt pack to use
- which endpoint or page to stay focused on
- the estimated bundle token size
- the exact next artifact paths to read

## v1.6 Feedback And Handoff Loop

The analyzer can now learn from accepted backend SQL and frontend handoff prompt
results as well, not only from the older clarification prompts.

This matters for your internal weak models because:

- accepted `*BffSql` output can be folded back into transition hints
- accepted UI prompt output can be folded back into later migration runs
- `llm-pack/handoff-manifest.json` now points explicitly to the compact guides and
  the generated prompt-goal families, which makes later Cline/manual loops easier

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

## Response Validation And Agent Loop

The analyzer can now validate task output before learning from it. Validation combines:

- strict JSON/schema checks
- evidence checks against known modules, queries, pages, endpoints, DTOs, and feature dirs

Accepted or warning-level responses are written into `runtime/validation-results.json`, while
the bounded loop can keep iterating through blockers one task at a time.

The loop also writes:

- `runtime/loop-state.json`
- `runtime/loop-summary.md`
- `runtime/task-history.json`
- `runtime/task-attempts.json`
- `runtime/trusted-facts.json`

This keeps weak `qwen3`-class models scoped to one blocker, one prompt, and one compact context bundle at a time.

## Auto-Compact Task Contexts

Every loop task can now emit:

- `runtime/taskpacks/<task-id>/compiled-context.md`
- `runtime/taskpacks/<task-id>/compiled-context.json`
- `runtime/taskpacks/<task-id>/taskpack-compiled.json`

These compact files are meant for weak models and later Cline subagents. They reduce context to:

- task definition
- trusted facts
- evidence snippets
- one bounded prompt

## Transition To Code Skeletons

Validated transition artifacts can now be turned into starter code:

```bash
legacy-delphi-analyzer generate-code /path/to/artifacts
```

This writes generated skeletons under `artifacts/codegen/`, including:

- React pages, API helpers, and type files
- Spring Boot controllers, services, repositories, and DTO classes

By default, only modules with accepted validation results are converted. Use
`--allow-unvalidated` if you want skeletons even when validation is still missing.

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
