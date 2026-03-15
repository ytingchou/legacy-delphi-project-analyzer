from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.cli import main
from legacy_delphi_project_analyzer.cline import collect_cline_response, emit_cline_task, write_cline_response
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, load_taskpack, write_taskpacks


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class TaskPackTests(unittest.TestCase):
    def test_build_taskpacks_respects_qwen3_profile_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state)
            written = write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            self.assertGreaterEqual(len(taskpacks), 2)
            self.assertEqual(len(taskpacks), len(written))
            self.assertTrue(all(item.context_budget_tokens <= 12000 for item in taskpacks))
            index_path = Path(output.output_dir) / "runtime" / "taskpacks" / "taskpack-index.json"
            self.assertTrue(index_path.exists())
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertTrue(any(item["task_type"] == "infer_placeholder_meaning" for item in payload))

    def test_cline_adapter_round_trip_for_taskpack(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="cline",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            written = write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            task_dir = written[0]
            taskpack = load_taskpack(task_dir)
            assert taskpack is not None
            request_path = emit_cline_task(taskpack, task_dir, Path(output.output_dir) / "runtime")
            self.assertTrue(request_path.exists())
            write_cline_response(
                taskpack.task_id,
                Path(output.output_dir) / "runtime",
                {
                    "task_id": taskpack.task_id,
                    "status": "completed",
                    "result": {"ok": True},
                },
            )
            response = collect_cline_response(taskpack.task_id, Path(output.output_dir) / "runtime")
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response["status"], "completed")

    def test_cli_build_taskpacks_and_dispatch_task(self) -> None:
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
            build_result = main(["build-taskpacks", analysis_dir.as_posix(), "--max-tasks", "1"])
            self.assertEqual(build_result, 0)
            index_payload = json.loads(
                (analysis_dir / "runtime" / "taskpacks" / "taskpack-index.json").read_text(
                    encoding="utf-8"
                )
            )
            task_id = index_payload[0]["task_id"]
            dispatch_result = main(["dispatch-task", analysis_dir.as_posix(), task_id])
            self.assertEqual(dispatch_result, 0)
            self.assertTrue((analysis_dir / "runtime" / "cline-inbox" / task_id / "request.json").exists())


if __name__ == "__main__":
    unittest.main()
