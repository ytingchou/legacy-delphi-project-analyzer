from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
        "--max-artifact-tokens",
        type=int,
        default=10000,
        help="Split Markdown artifacts once they exceed this approximate token budget.",
    )
    analyze_parser.add_argument(
        "--fail-on-fatal",
        action="store_true",
        help="Exit with a non-zero status if fatal diagnostics are present.",
    )

    report_parser = subparsers.add_parser(
        "serve-report", help="Serve a generated HTML report directory locally."
    )
    report_parser.add_argument("report_dir", help="Path to the generated report directory.")
    report_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    report_parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve-report":
        return serve_report(Path(args.report_dir), host=args.host, port=args.port)
    if args.command != "analyze":
        parser.error("Unsupported command")

    output = run_analysis(
        project_root=Path(args.project_root),
        output_dir=Path(args.output_dir),
        rules_dir=Path(args.rules_dir) if args.rules_dir else None,
        phases=args.phases,
        max_artifact_chars=args.max_artifact_chars,
        max_artifact_tokens=args.max_artifact_tokens,
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
    report_path = Path(output.output_dir) / "report" / "index.html"
    if report_path.exists():
        print(f"Web report: {report_path}")
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
