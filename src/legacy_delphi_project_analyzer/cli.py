from __future__ import annotations

import argparse
from pathlib import Path

from legacy_delphi_project_analyzer.pipeline import PHASE_ORDER, run_analysis


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy-delphi-analyzer",
        description="Analyze a legacy Delphi project and emit LLM-friendly migration artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Run the analyzer on a project root.")
    analyze_parser.add_argument("project_root", help="Path to the Delphi project root.")
    analyze_parser.add_argument(
        "--phase",
        dest="phases",
        action="append",
        choices=[*PHASE_ORDER, "all"],
        help="Limit execution to one or more phases. Defaults to all phases.",
    )
    analyze_parser.add_argument(
        "--output-dir",
        default="artifacts",
        help="Directory where analysis outputs will be written.",
    )
    analyze_parser.add_argument(
        "--rules-dir",
        default=None,
        help="Optional directory containing overrides.json.",
    )
    analyze_parser.add_argument(
        "--max-artifact-chars",
        type=int,
        default=40000,
        help="Split Markdown artifacts once they exceed this size.",
    )
    analyze_parser.add_argument(
        "--fail-on-fatal",
        action="store_true",
        help="Exit with a non-zero status if fatal diagnostics are present.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "analyze":
        parser.error("Unsupported command")

    output = run_analysis(
        project_root=Path(args.project_root),
        output_dir=Path(args.output_dir),
        rules_dir=Path(args.rules_dir) if args.rules_dir else None,
        phases=args.phases,
        max_artifact_chars=args.max_artifact_chars,
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
    print(f"Diagnostics: {len(output.diagnostics)} total, {error_count} error, {fatal_count} fatal")
    return 1 if args.fail_on_fatal and fatal_count else 0
