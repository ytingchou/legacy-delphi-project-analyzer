from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.agent_loop import validate_task_response
from legacy_delphi_project_analyzer.controlled_delivery import run_controlled_delivery
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.patch_validation import validate_patch_packs
from legacy_delphi_project_analyzer.repair_tasks import build_repair_tasks
from legacy_delphi_project_analyzer.runtime_errors import build_runtime_error_summary
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, write_taskpacks
from legacy_delphi_project_analyzer.workspace_sync import build_transition_workspace_sync


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"
TARGET_FIXTURE = Path(__file__).parent / "fixtures" / "react_target_project"


class V6FeatureTests(unittest.TestCase):
    def test_run_phases_generates_progress_handoff_and_transition_map_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            root = Path(output.output_dir)

            self.assertTrue((root / "runtime" / "progress" / "progress-report.json").exists())
            self.assertTrue((root / "delivery-handoff" / "manifest.json").exists())
            self.assertTrue((root / "llm-pack" / "multi-repo-transition-map" / "multi-repo-transition-map.json").exists())

            progress = json.loads((root / "runtime" / "progress" / "progress-report.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(progress["snapshot_count"], 1)
            self.assertIn("management_notes", progress)

            handoff = json.loads((root / "delivery-handoff" / "manifest.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(handoff["entry_count"], 1)

            transition_map = json.loads(
                (root / "llm-pack" / "multi-repo-transition-map" / "multi-repo-transition-map.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertGreaterEqual(transition_map["root_count"], 1)

            report_html = (root / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Progress Layer", report_html)
            self.assertIn("Developer Handoff Packs", report_html)
            self.assertIn("Multi-Repo Transition Map", report_html)

    def test_workspace_sync_patch_validation_and_repair_tasks_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            analysis_dir = Path(output.output_dir)
            runtime_dir = analysis_dir / "runtime"

            workspace_sync = build_transition_workspace_sync(
                analysis_dir,
                TARGET_FIXTURE,
                output=output,
            )
            self.assertGreaterEqual(workspace_sync["entry_count"], 1)
            self.assertGreaterEqual(workspace_sync["route_entry_count"], 1)

            patch_validation = validate_patch_packs(
                analysis_dir,
                output=output,
                target_project_dir=TARGET_FIXTURE,
            )
            self.assertGreaterEqual(patch_validation["entry_count"], 1)
            self.assertIn("counts_by_status", patch_validation)

            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
            task_dir = runtime_dir / "taskpacks" / taskpacks[0].task_id

            validate_task_response(
                analysis_dir=analysis_dir,
                task_dir=task_dir,
                response_payload={
                    "task_id": taskpacks[0].task_id,
                    "status": "completed",
                    "result": {"query_name": "UnknownQuery"},
                },
            )
            runtime_error_summary = build_runtime_error_summary(
                analysis_dir=analysis_dir,
                runtime_dir=runtime_dir,
                blockers=output.blocking_unknowns,
            )
            repair_manifest = build_repair_tasks(
                analysis_dir,
                runtime_dir=runtime_dir,
                runtime_error_summary=runtime_error_summary,
                patch_validation_report=patch_validation,
            )
            self.assertGreaterEqual(repair_manifest["entry_count"], 1)
            self.assertTrue((runtime_dir / "repair-tasks" / "repair-tasks.json").exists())

    def test_run_controlled_delivery_writes_manifest(self) -> None:
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

            self.assertGreaterEqual(manifest["step_count"], 7)
            self.assertGreaterEqual(manifest["summary"]["delivery_count"], 1)
            self.assertTrue((analysis_dir / "delivery-control" / "controlled-delivery-manifest.json").exists())
            self.assertTrue((analysis_dir / "llm-pack" / "workspace-sync" / "workspace-sync.json").exists())
            self.assertTrue((analysis_dir / "llm-pack" / "patch-validation" / "patch-validation.json").exists())
            self.assertTrue((analysis_dir / "delivery-handoff" / "manifest.json").exists())
            self.assertTrue((analysis_dir / "delivery-slices" / "delivery-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
