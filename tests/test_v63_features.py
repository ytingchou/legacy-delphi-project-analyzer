from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.controlled_delivery import run_controlled_delivery
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.patch_apply import build_patch_apply_assistant
from legacy_delphi_project_analyzer.repo_validation import build_repo_validation_gate


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
TARGET_FIXTURE = Path(__file__).parent / "fixtures" / "react_target_project"


class V63FeatureTests(unittest.TestCase):
    def test_run_phases_generates_patch_apply_repo_validation_and_task_studio_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            root = Path(output.output_dir)
            self.assertTrue((root / "llm-pack" / "patch-apply-assistant" / "manifest.json").exists())
            self.assertTrue((root / "llm-pack" / "repo-validation-gate" / "repo-validation.json").exists())
            self.assertTrue((root / "runtime" / "task-studio" / "workflow.md").exists())

            task_studio = json.loads((root / "runtime" / "task-studio.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(task_studio["task_count"], 1)
            first_task = task_studio["tasks"][0]
            self.assertIn("context_budget_tokens", first_task)
            self.assertIn("context_size_hint", first_task)
            self.assertIn("response_template_file", first_task)

            report_html = (root / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Task Studio 2.0", report_html)
            self.assertIn("Patch Apply Assistant", report_html)
            self.assertIn("Repo Validation Gate", report_html)

    def test_build_patch_apply_and_repo_validation_against_target_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            analysis_dir = Path(output.output_dir)
            patch_apply = build_patch_apply_assistant(
                analysis_dir,
                output=output,
                target_project_dir=TARGET_FIXTURE,
            )
            repo_validation = build_repo_validation_gate(
                analysis_dir,
                output=output,
                target_project_dir=TARGET_FIXTURE,
            )

            self.assertGreaterEqual(patch_apply["entry_count"], 1)
            self.assertTrue(any(item.get("allowed_files") for item in patch_apply["entries"]))
            self.assertTrue((analysis_dir / "llm-pack" / "patch-apply-assistant" / "manifest.json").exists())

            self.assertGreaterEqual(repo_validation["entry_count"], 1)
            self.assertTrue(any(item.get("patch_apply_prompt_file") for item in repo_validation["entries"]))
            self.assertTrue((analysis_dir / "llm-pack" / "repo-validation-gate" / "repo-validation.json").exists())

    def test_controlled_delivery_summary_includes_patch_apply_and_repo_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            analysis_dir = Path(output.output_dir)
            manifest = run_controlled_delivery(
                analysis_dir,
                output=output,
                target_project_dir=TARGET_FIXTURE,
                allow_unvalidated=True,
            )
            self.assertIn("patch_apply_count", manifest["summary"])
            self.assertIn("repo_validation_count", manifest["summary"])
            self.assertGreaterEqual(manifest["summary"]["patch_apply_count"], 1)
            self.assertGreaterEqual(manifest["summary"]["repo_validation_count"], 1)


if __name__ == "__main__":
    unittest.main()
