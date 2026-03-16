from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.benchmarking import benchmark_prompts
from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.taskpacks import build_taskpacks, write_taskpacks
from legacy_delphi_project_analyzer.agent_loop import validate_task_response


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class PromptBenchmarkTests(unittest.TestCase):
    def test_benchmark_prompts_writes_report_and_tuning(self) -> None:
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

            validate_task_response(
                analysis_dir=Path(output.output_dir),
                task_dir=task_dir,
                response_payload={
                    "result": {
                        "query_name": "UnknownQuery",
                        "business_intent": "Unknown",
                        "placeholder_meanings": {"fPriceCheckRule": "Unknown"},
                        "oracle_specifics": [],
                        "missing_assumptions": [],
                        "recommended_next_prompt": "Ask again.",
                    }
                },
            )
            validate_task_response(
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
                prompt_mode="fallback",
            )

            report = benchmark_prompts(Path(output.output_dir))
            self.assertEqual(len(report["prompt_benchmark"]), 1)
            row = report["prompt_benchmark"][0]
            self.assertGreaterEqual(row["attempts"], 2)
            self.assertIn("tighten_evidence_constraints", row["tuning_actions"])
            self.assertTrue((Path(output.output_dir) / "runtime" / "prompt-benchmark.json").exists())
            self.assertTrue((Path(output.output_dir) / "runtime" / "prompt-template-tuning.json").exists())


if __name__ == "__main__":
    unittest.main()
