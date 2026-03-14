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
- `--fail-on-fatal`

## v0.2 Highlights

- Binary `.dfm` files no longer short-circuit analysis. They are parsed with a
  heuristic token scanner and surfaced as warnings rather than hard failures.
- Pascal parsing now extracts `published` fields, component declarations, and
  property names to improve module and form linkage.
- SQL XML diagnostics now cover duplicate query names, invalid `sql-copy`
  targets, ambiguous same-name resolution, and richer cycle traces.

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
