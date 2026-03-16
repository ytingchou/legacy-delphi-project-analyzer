from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.delivery import deliver_slices
from legacy_delphi_project_analyzer.orchestrator import run_phases


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
TARGET_PROJECT = Path(__file__).parent / "fixtures" / "react_target_project"


class DeliveryPipelineTests(unittest.TestCase):
    def test_deliver_slices_assembles_module_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            (runtime_dir / "validation-results.json").write_text(
                json.dumps(
                    [
                        {
                            "task_id": "task-transition-orderentry-validate",
                            "task_type": "validate_transition_spec",
                            "status": "accepted",
                            "module_name": "OrderEntry",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            manifest = deliver_slices(
                Path(output.output_dir),
                target_project_dir=TARGET_PROJECT,
            )
            self.assertEqual(manifest["delivery_count"], 1)
            module_dir = Path(output.output_dir) / "delivery-slices" / "orderentry"
            self.assertTrue((module_dir / "slice-manifest.json").exists())
            self.assertTrue((module_dir / "slice-summary.md").exists())
            slice_manifest = json.loads((module_dir / "slice-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(slice_manifest["generated_code"])
            self.assertTrue(slice_manifest["bff_entries"])
            self.assertTrue(slice_manifest["target_integration_entries"])


if __name__ == "__main__":
    unittest.main()
