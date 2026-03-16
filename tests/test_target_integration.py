from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
TARGET_PROJECT = Path(__file__).parent / "fixtures" / "react_target_project"


class TargetIntegrationTests(unittest.TestCase):
    def test_build_target_project_integration_pack_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            manifest = build_target_project_integration_pack(Path(output.output_dir), TARGET_PROJECT)
            self.assertEqual(len(manifest["entries"]), 1)
            entry = manifest["entries"][0]
            self.assertTrue(entry["feature_exists"])
            self.assertTrue(entry["route_file_candidates"])
            self.assertTrue((Path(output.output_dir) / "llm-pack" / "target-integration" / "target-integration-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
