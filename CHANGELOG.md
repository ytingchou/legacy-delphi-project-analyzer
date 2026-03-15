# Changelog

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
