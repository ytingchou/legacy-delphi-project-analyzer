from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.agent_loop import run_loop, validate_task_response
from legacy_delphi_project_analyzer.cline import write_cline_response
from legacy_delphi_project_analyzer.codegen import generate_transition_code
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, write_taskpacks


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class ValidatorLoopCodegenTests(unittest.TestCase):
    def test_validate_task_response_accepts_grounded_placeholder_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            task = taskpacks[0]
            task_dir = Path(output.output_dir) / "runtime" / "taskpacks" / task.task_id
            record = validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_payload={
                    "result": {
                        "query_name": "OrderLookup",
                        "business_intent": "Looks up order rows before approval.",
                        "placeholder_meanings": {
                            "fPriceCheckRule": "Injected by Delphi pricing rule logic.",
                        },
                        "oracle_specifics": ["SQL XML composition"],
                        "missing_assumptions": ["Confirm source pricing rule service."],
                        "recommended_next_prompt": "Ask for the exact pricing rule source.",
                    }
                },
            )
            self.assertEqual(record.status, "accepted")
            self.assertTrue(record.schema_valid)
            self.assertTrue(record.evidence_valid)

    def test_validate_task_response_rejects_unknown_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            task = taskpacks[0]
            task_dir = Path(output.output_dir) / "runtime" / "taskpacks" / task.task_id
            record = validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_payload={
                    "result": {
                        "query_name": "UnknownQuery",
                        "business_intent": "Unknown",
                        "placeholder_meanings": {
                            "fPriceCheckRule": "Unknown",
                        },
                        "oracle_specifics": [],
                        "missing_assumptions": [],
                        "recommended_next_prompt": "Ask again.",
                    }
                },
            )
            self.assertEqual(record.status, "rejected")
            self.assertIn("Unknown query_name: UnknownQuery", record.issues)
            self.assertEqual(record.rejection_category, "unsupported_claims")
            retry_plan = json.loads((task_dir / "retry-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(retry_plan["rejection_category"], "unsupported_claims")
            self.assertIn("Remove unsupported claims", retry_plan["repair_prompt"])

    def test_validate_task_response_writes_schema_retry_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            task = taskpacks[0]
            task_dir = Path(output.output_dir) / "runtime" / "taskpacks" / task.task_id
            record = validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_payload={"result": {"query_name": "OrderLookup"}},
            )
            self.assertEqual(record.rejection_category, "schema_error")
            retry_plan = json.loads((task_dir / "retry-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(retry_plan["next_prompt_mode"], "fallback")
            self.assertEqual(len(retry_plan["retry_context_paths"]), 1)

    def test_run_loop_manual_learns_from_agent_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            assert output.runtime_state is not None
            taskpacks = build_taskpacks(output, output.runtime_state, max_tasks=1)
            write_taskpacks(taskpacks, Path(output.output_dir) / "runtime")
            task = taskpacks[0]
            task_dir = Path(output.output_dir) / "runtime" / "taskpacks" / task.task_id
            (task_dir / "agent-response.json").write_text(
                json.dumps(
                    {
                        "task_id": task.task_id,
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
                    }
                ),
                encoding="utf-8",
            )
            run_loop(Path(output.output_dir), dispatch_mode="manual", max_loops=1)
            accepted_rules = json.loads(
                (Path(output.output_dir) / "knowledge" / "accepted_rules.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("OrderLookup", accepted_rules["placeholder_notes"])
            validation_results = json.loads(
                (Path(output.output_dir) / "runtime" / "validation-results.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(validation_results[0]["status"], "accepted")

    def test_run_loop_cline_waits_for_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="cline",
            )
            runtime_dir = Path(output.output_dir) / "runtime"

            def responder() -> None:
                request_path = runtime_dir / "cline-inbox" / "task-query-orderlookup-placeholders" / "request.json"
                deadline = time.time() + 5
                while time.time() < deadline:
                    if request_path.exists():
                        write_cline_response(
                            "task-query-orderlookup-placeholders",
                            runtime_dir,
                            {
                                "task_id": "task-query-orderlookup-placeholders",
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
                        )
                        return
                    time.sleep(0.1)

            thread = threading.Thread(target=responder, daemon=True)
            thread.start()
            run_loop(
                Path(output.output_dir),
                dispatch_mode="cline",
                max_loops=1,
                wait_seconds=5,
                poll_seconds=0.1,
            )
            validation_results = json.loads(
                (Path(output.output_dir) / "runtime" / "validation-results.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(validation_results[0]["status"], "accepted")

    def test_generate_transition_code_requires_validation_by_default(self) -> None:
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
            generated = generate_transition_code(Path(output.output_dir))
            self.assertGreaterEqual(len(generated), 3)
            codegen_dir = Path(output.output_dir) / "codegen"
            self.assertTrue((codegen_dir / "react" / "orderentry" / "OrderEntryPage.tsx").exists())
            self.assertTrue((codegen_dir / "spring" / "orderentry" / "OrderEntryController.java").exists())
            self.assertTrue((codegen_dir / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
