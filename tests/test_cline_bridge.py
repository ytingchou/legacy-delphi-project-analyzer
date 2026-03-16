from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.cline import emit_cline_task
from legacy_delphi_project_analyzer.cline_bridge import run_cline_wrapper
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, load_taskpack, write_taskpacks


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class ClineBridgeTests(unittest.TestCase):
    def test_run_cline_wrapper_processes_request_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="cline",
            )
            runtime_dir = Path(output.output_dir) / "runtime"
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, runtime_dir, include_compiled_context=True)
            task_dir = runtime_dir / "taskpacks" / taskpacks[0].task_id
            taskpack = load_taskpack(task_dir)
            assert taskpack is not None
            emit_cline_task(taskpack, task_dir, runtime_dir)

            script_path = Path(tmpdir) / "fake-cline.sh"
            script_path.write_text(
                "#!/bin/sh\n"
                "cat >/dev/null\n"
                "printf '%s' '{\"query_name\":\"OrderLookup\",\"business_intent\":\"Looks up order rows before approval.\",\"placeholder_meanings\":{\"fPriceCheckRule\":\"Injected by Delphi pricing rule logic.\"},\"oracle_specifics\":[\"SQL XML composition\"],\"missing_assumptions\":[\"Confirm source pricing rule service.\"],\"recommended_next_prompt\":\"Ask for the exact pricing rule source.\"}'\n",
                encoding="utf-8",
            )
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            result = run_cline_wrapper(
                analysis_dir=Path(output.output_dir),
                cline_cmd=[script_path.as_posix()],
                once=True,
            )
            self.assertEqual(result["processed"], 1)
            response_path = runtime_dir / "cline-outbox" / taskpack.task_id / "response.json"
            self.assertTrue(response_path.exists())
            validation_path = task_dir / "validation-result.json"
            self.assertTrue(validation_path.exists())
            payload = json.loads(validation_path.read_text(encoding="utf-8"))
            self.assertIn(payload["status"], {"accepted", "accepted_with_warnings"})
            self.assertTrue((runtime_dir / "cline-logs" / f"{taskpack.task_id}.log").exists())


if __name__ == "__main__":
    unittest.main()
