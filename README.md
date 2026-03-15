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

## Usage

```bash
python3 -m legacy_delphi_project_analyzer analyze /path/to/project
```

Or after installation:

```bash
legacy-delphi-analyzer analyze /path/to/project --output-dir artifacts
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
- `--fail-on-fatal`

Serve the generated web report locally:

```bash
legacy-delphi-analyzer serve-report /path/to/artifacts/report
```

Import accepted or rejected LLM feedback back into the analyzer:

```bash
legacy-delphi-analyzer ingest-feedback /path/to/artifacts /path/to/feedback.json
```

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

## v0.9 Highlights

- Imported prompt feedback is now scored so you can see which prompts and goals actually work.
- Management outputs now include `prompt-effectiveness.json`, `prompt-effectiveness.md`,
  and web-report sections for prompt success and prompt failure hotspots.
- Boss summary and dashboard can now show whether the team is blocked by weak prompts
  or by real legacy-system unknowns.

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
  }
}
```
