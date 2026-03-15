from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.cli import main
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.phase_state import (
    load_artifact_completeness,
    load_blocking_unknowns,
    load_phase_states,
    load_run_state,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class OrchestratorTests(unittest.TestCase):
    def test_run_phases_writes_runtime_state_and_handoff_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            run_state = load_run_state(runtime_dir)
            phase_states = load_phase_states(runtime_dir)
            blockers = load_blocking_unknowns(runtime_dir)
            completeness = load_artifact_completeness(runtime_dir)

            self.assertIsNotNone(run_state)
            assert run_state is not None
            self.assertEqual(run_state.current_phase, "clarify")
            self.assertEqual(run_state.target_model_profile, "qwen3_128k_weak")
            self.assertTrue((runtime_dir / "state-summary.md").exists())
            self.assertTrue((runtime_dir / "phase-delta.md").exists())
            self.assertTrue((Path(output.output_dir) / "llm-pack" / "handoff-manifest.json").exists())
            handoff_manifest = json.loads(
                (Path(output.output_dir) / "llm-pack" / "handoff-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertGreaterEqual(len(phase_states), 6)
            self.assertTrue(any(item.phase == "transition_validate" for item in phase_states))
            self.assertGreaterEqual(len(blockers), 2)
            self.assertIsNotNone(completeness)
            assert completeness is not None
            self.assertIn("transition_specs", completeness.items)
            self.assertIn("bff_sql_artifacts", completeness.items)
            self.assertIn("ui_pseudo_artifacts", completeness.items)
            self.assertIn("ui_reference_artifacts", completeness.items)
            self.assertIn("ui_integration_artifacts", completeness.items)
            self.assertTrue(completeness.items["bff_sql_artifacts"])
            self.assertTrue(completeness.items["ui_integration_artifacts"])
            self.assertFalse(completeness.items["validation_results"])
            self.assertIn("llm-pack/backend-sql-guide.md", handoff_manifest["compact_guides"])
            self.assertIn("generate_bff_oracle_sql_logic", handoff_manifest["prompt_pack_goals"])

    def test_analyze_cli_also_refreshes_runtime_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = main(
                [
                    "analyze",
                    FIXTURE_ROOT.as_posix(),
                    "--output-dir",
                    (Path(tmpdir) / "artifacts").as_posix(),
                    "--model-profile",
                    "qwen3_128k_weak",
                ]
            )
            self.assertEqual(result, 0)
            runtime_dir = Path(tmpdir) / "artifacts" / "runtime"
            self.assertTrue((runtime_dir / "run-state.json").exists())
            self.assertTrue((runtime_dir / "blocking-unknowns.json").exists())


if __name__ == "__main__":
    unittest.main()
