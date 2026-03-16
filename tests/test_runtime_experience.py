from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.agent_loop import validate_task_response
from legacy_delphi_project_analyzer.human_review import record_task_review
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.runtime_errors import save_provider_health
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, write_taskpacks


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class RuntimeExperienceTests(unittest.TestCase):
    def test_taskpacks_emit_vscode_helper_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
            task_dir = runtime_dir / "taskpacks" / taskpacks[0].task_id
            self.assertTrue((task_dir / "vscode-cline-quick-open.md").exists())
            self.assertTrue((task_dir / "vscode-cline-copy-prompt.txt").exists())
            self.assertTrue((task_dir / "vscode-cline-response-template.json").exists())
            self.assertTrue((task_dir / "primary-prompt.txt").exists())
            self.assertTrue((task_dir / "fallback-prompt.txt").exists())
            self.assertTrue((task_dir / "verification-prompt.txt").exists())

    def test_validation_failure_writes_runtime_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
            task_dir = runtime_dir / "taskpacks" / taskpacks[0].task_id

            validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_payload={
                    "task_id": taskpacks[0].task_id,
                    "status": "completed",
                    "result": {"query_name": "UnknownQuery"},
                },
            )

            error_summary_path = runtime_dir / "errors" / "error-summary.json"
            self.assertTrue(error_summary_path.exists())
            payload = json.loads(error_summary_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(payload["item_count"], 1)

    def test_review_task_writes_review_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
            task_dir = runtime_dir / "taskpacks" / taskpacks[0].task_id
            response_path = task_dir / "agent-response.json"
            response_path.write_text(
                json.dumps(
                    {
                        "task_id": taskpacks[0].task_id,
                        "status": "completed",
                        "result": {
                            "query_name": "OrderLookup",
                            "placeholder_meanings": {"fPriceCheckRule": "Runtime pricing rule placeholder"},
                            "missing_assumptions": [],
                            "confidence": "medium",
                        },
                    }
                ),
                encoding="utf-8",
            )
            validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_path=response_path,
            )
            record_task_review(
                analysis_dir=Path(output.output_dir),
                task_id=taskpacks[0].task_id,
                decision="accept",
                notes="Looks grounded.",
                reviewer="qa",
            )
            summary_path = runtime_dir / "reviews" / "review-summary.json"
            self.assertTrue(summary_path.exists())
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts_by_decision"]["accept"], 1)

    def test_report_contains_runtime_workbench_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            save_provider_health(
                Path(output.output_dir) / "runtime",
                {
                    "provider_base_url": "http://provider/v1",
                    "models_ok": True,
                    "completion_ok": True,
                    "selected_model": "qwen3",
                    "response_format": "sse",
                    "response_content_type": "text/event-stream; charset=utf-8",
                    "ok": True,
                },
            )
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            report_html = (Path(output.output_dir) / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Runtime Workbench", report_html)
            self.assertIn("Task Queue", report_html)
            self.assertIn("Provider Health", report_html)
            self.assertIn("Human Review", report_html)


if __name__ == "__main__":
    unittest.main()
