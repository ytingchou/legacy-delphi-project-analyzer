from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.target_integration import build_target_project_integration_pack


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
TARGET_FIXTURE = Path(__file__).parent / "fixtures" / "react_target_project"


class V4FeatureTests(unittest.TestCase):
    def test_run_phases_generates_task_studio_session_patch_and_eval_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            root = Path(output.output_dir)
            self.assertTrue((root / "runtime" / "task-studio.json").exists())
            self.assertTrue((root / "runtime" / "cline-session" / "session-manifest.json").exists())
            self.assertTrue((root / "llm-pack" / "code-patch-packs" / "manifest.json").exists())
            self.assertTrue((root / "runtime" / "failure-replay" / "manifest.json").exists())
            self.assertTrue((root / "runtime" / "golden-tasks" / "golden-task-evaluation.json").exists())

            task_studio = json.loads((root / "runtime" / "task-studio.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(task_studio["task_count"], 1)
            patch_manifest = json.loads((root / "llm-pack" / "code-patch-packs" / "manifest.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(patch_manifest["patch_count"], 1)
            report_html = (root / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Task Studio", report_html)
            self.assertIn("Cline Session Bridge", report_html)
            self.assertIn("Code Patch Packs", report_html)
            self.assertIn("Golden Task Evaluation", report_html)

    def test_build_target_pack_writes_assistant_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            manifest = build_target_project_integration_pack(
                Path(output.output_dir),
                TARGET_FIXTURE,
            )
            self.assertGreaterEqual(len(manifest["entries"]), 1)
            assistant_path = Path(output.output_dir) / "llm-pack" / "target-integration" / "target-integration-assistant-manifest.json"
            self.assertTrue(assistant_path.exists())
            assistant = json.loads(assistant_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(assistant["entry_count"], 1)
            entry = assistant["entries"][0]
            self.assertIn("route_alignment_score", entry)
            self.assertIn("suggested_api_adapter", entry)
            self.assertIn("assistant_prompt", entry)


if __name__ == "__main__":
    unittest.main()
