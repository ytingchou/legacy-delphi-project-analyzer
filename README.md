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
- Rule-driven knowledge store for reusable overrides and learned patterns

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
- `--max-artifact-chars 40000`
- `--max-artifact-tokens 10000`
- `--fail-on-fatal`

Serve the generated web report locally:

```bash
legacy-delphi-analyzer serve-report /path/to/artifacts/report
```

## v0.3 Highlights

- Artifacts are now packaged with an approximate token budget rather than only
  a character budget.
- Each inferred business module gets a business-flow dossier and an LLM load
  bundle that lists the minimum useful artifact set.
- The analyzer now emits a boss-facing complexity report, executive summary,
  and a static HTML dashboard under `report/index.html`.

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
  }
}
```
