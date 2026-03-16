from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.cline import write_cline_response
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.subagents import run_subagent_batches


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class SubagentBatchTests(unittest.TestCase):
    def test_run_subagent_batches_manual_writes_batch_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            payload = run_subagent_batches(
                Path(output.output_dir),
                dispatch_mode="manual",
                max_tasks=2,
                batch_size=2,
                goal_filters=["classify_query_intent", "infer_placeholder_meaning"],
            )
            self.assertEqual(payload["batch_count"], 1)
            self.assertEqual(len(payload["results"][0]["results"]), 2)
            self.assertTrue((Path(output.output_dir) / "runtime" / "subagents" / "batch-plan.json").exists())

    def test_run_subagent_batches_cline_validates_multiple_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="cline",
            )
            runtime_dir = Path(output.output_dir) / "runtime"

            def responder() -> None:
                expected = {
                    "subagent-orderlookupintent": {
                        "task_id": "subagent-orderlookupintent",
                        "status": "completed",
                        "result": {
                            "query_name": "OrderLookup",
                            "business_intent": "Looks up order rows before approval.",
                            "read_or_write": "read",
                            "oracle_specifics": ["SQL XML composition"],
                            "likely_ui_trigger": "btnSearchClick",
                            "missing_evidence": [],
                        },
                    },
                    "subagent-orderlookupclarify": {
                        "task_id": "subagent-orderlookupclarify",
                        "status": "completed",
                        "result": {
                            "query_name": "OrderLookup",
                            "business_intent": "Looks up order rows before approval.",
                            "placeholder_meanings": {
                                "fPriceCheckRule": "Injected by Delphi pricing rule logic.",
                            },
                            "oracle_specifics": ["SQL XML composition"],
                            "missing_assumptions": ["Confirm source pricing rule service."],
                            "recommended_next_prompt": "Ask for the exact pricing rule source.",
                        },
                    },
                }
                deadline = time.time() + 5
                while time.time() < deadline and expected:
                    for task_id in list(expected.keys()):
                        request_path = runtime_dir / "cline-inbox" / task_id / "request.json"
                        if request_path.exists():
                            write_cline_response(task_id, runtime_dir, expected.pop(task_id))
                    time.sleep(0.1)

            thread = threading.Thread(target=responder, daemon=True)
            thread.start()
            payload = run_subagent_batches(
                Path(output.output_dir),
                dispatch_mode="cline",
                max_tasks=2,
                batch_size=2,
                goal_filters=["classify_query_intent", "infer_placeholder_meaning"],
                wait_seconds=5,
                poll_seconds=0.1,
            )
            statuses = [item["status"] for item in payload["results"][0]["results"]]
            self.assertEqual(statuses, ["accepted", "accepted"])
            validation_history = json.loads((runtime_dir / "validation-history.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(validation_history), 2)


if __name__ == "__main__":
    unittest.main()
