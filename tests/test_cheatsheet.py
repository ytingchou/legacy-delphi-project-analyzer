from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.cli import main
from legacy_delphi_project_analyzer.orchestrator import run_phases


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class CheatSheetTests(unittest.TestCase):
    def test_run_phases_writes_llm_and_runtime_cheat_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            analysis_dir = Path(output.output_dir)
            llm_sheet = analysis_dir / "llm-pack" / "cline-cheat-sheet.md"
            runtime_sheet = analysis_dir / "runtime" / "cline-cheat-sheet.md"
            llm_json = analysis_dir / "llm-pack" / "cline-cheat-sheet.json"
            runtime_json = analysis_dir / "runtime" / "cline-cheat-sheet.json"

            self.assertTrue(llm_sheet.exists())
            self.assertTrue(runtime_sheet.exists())
            self.assertTrue(llm_json.exists())
            self.assertTrue(runtime_json.exists())
            self.assertIn("Single-Task SOP", llm_sheet.read_text(encoding="utf-8"))
            self.assertIn("Current Top Blockers", runtime_sheet.read_text(encoding="utf-8"))

            payload = json.loads(runtime_json.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(payload["top_blockers"]), 1)
            self.assertTrue(payload["top_blockers"][0]["task_id"])

    def test_cli_build_cheatsheet_regenerates_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            analysis_dir = Path(tmpdir) / "artifacts"
            run_result = main(
                [
                    "run-phases",
                    FIXTURE_ROOT.as_posix(),
                    "--output-dir",
                    analysis_dir.as_posix(),
                    "--model-profile",
                    "qwen3_128k_weak",
                ]
            )
            self.assertEqual(run_result, 0)

            llm_sheet = analysis_dir / "llm-pack" / "cline-cheat-sheet.md"
            llm_sheet.write_text("stale", encoding="utf-8")

            result = main(["build-cheatsheet", analysis_dir.as_posix()])
            self.assertEqual(result, 0)
            refreshed = llm_sheet.read_text(encoding="utf-8")
            self.assertIn("Quick Start", refreshed)


if __name__ == "__main__":
    unittest.main()
