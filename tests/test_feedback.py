from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.feedback import ingest_feedback
from legacy_delphi_project_analyzer.pipeline import run_analysis


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class FeedbackLearningTests(unittest.TestCase):
    def test_ingest_feedback_creates_accepted_rules_for_query_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            feedback_path = Path(tmpdir) / "feedback.json"
            feedback_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "prompt_name": "OrderLookupClarify",
                                "status": "accepted",
                                "response": {
                                    "query_name": "OrderLookup",
                                    "business_intent": "Looks up orders before price-check approval.",
                                    "placeholder_meanings": {
                                        "fPriceCheckRule": "Injected by Delphi pricing rule logic."
                                    },
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = ingest_feedback(Path(output.output_dir), feedback_path)
            self.assertEqual(result["accepted"], 1)
            accepted_rules = json.loads(
                (Path(output.output_dir) / "knowledge" / "accepted_rules.json").read_text(
                    encoding="utf-8"
                )
            )
            prompt_effectiveness = json.loads(
                (Path(output.output_dir) / "knowledge" / "prompt-effectiveness.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("OrderLookup", accepted_rules["placeholder_notes"])
            self.assertIn("fPriceCheckRule", accepted_rules["placeholder_notes"]["OrderLookup"])
            self.assertEqual(
                accepted_rules["query_hints"]["OrderLookup"],
                "Looks up orders before price-check approval.",
            )
            self.assertEqual(prompt_effectiveness["accepted_entries"], 1)

            rerun = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            self.assertIsNotNone(rerun.prompt_effectiveness_report)
            assert rerun.prompt_effectiveness_report is not None
            self.assertEqual(rerun.prompt_effectiveness_report.accepted_entries, 1)
            report_html = (Path(rerun.output_dir) / "report" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Prompt Effectiveness", report_html)

    def test_feedback_learning_unblocks_workspace_resolution_on_next_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_root = root / "main_project"
            external_sql = root / "PDSS_SQL"
            project_root.mkdir(parents=True, exist_ok=True)
            external_sql.mkdir(parents=True, exist_ok=True)

            (project_root / "entry.pas").write_text(
                """unit entry;
interface
uses System.SysUtils;
implementation
end.
""",
                encoding="utf-8",
            )
            (project_root / "entry.xml").write_text(
                """<sql-mapping>
  <main-query name="UseShared">
    <ext-sql-refer-to xml="shared" sub-query="BaseWhere" />
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            (external_sql / "shared.xml").write_text(
                """<sql-mapping>
  <sub-query name="BaseWhere">
    <sql-body><![CDATA[
WHERE status = :status
    ]]></sql-body>
  </sub-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            workspace_config = project_root / "workspace.json"
            workspace_config.write_text(
                json.dumps(
                    {
                        "search_paths": ["$(PDSS_SQL)"],
                    }
                ),
                encoding="utf-8",
            )

            output_1 = run_analysis(
                project_root=project_root,
                output_dir=root / "artifacts",
                workspace_config_path=workspace_config,
                phases=["all"],
            )
            self.assertTrue(
                any(item.code == "PROJECT_SEARCH_PATH_UNRESOLVED" for item in output_1.diagnostics)
            )
            workspace_prompt = next(
                item for item in output_1.prompt_packs if item.goal == "resolve_search_path"
            )

            feedback_path = root / "workspace-feedback.json"
            feedback_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "prompt_name": workspace_prompt.name,
                                "status": "accepted",
                                "response": {
                                    "raw_path": "$(PDSS_SQL)",
                                    "resolved_path": external_sql.as_posix(),
                                    "path_variables": {
                                        "PDSS_SQL": external_sql.as_posix(),
                                    },
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ingest_feedback(Path(output_1.output_dir), feedback_path)

            output_2 = run_analysis(
                project_root=project_root,
                output_dir=root / "artifacts",
                workspace_config_path=workspace_config,
                phases=["all"],
            )
            self.assertFalse(
                any(item.code == "PROJECT_SEARCH_PATH_UNRESOLVED" for item in output_2.diagnostics)
            )
            self.assertIn(external_sql.resolve().as_posix(), output_2.inventory.external_roots)

    def test_transition_spec_feedback_updates_transition_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            feedback_path = Path(tmpdir) / "transition-spec-feedback.json"
            feedback_path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "prompt_name": "OrderEntrySpecValidate",
                                "status": "accepted",
                                "response": {
                                    "module_name": "OrderEntry",
                                    "revised_first_slice": "Implement OrderEntryPage plus GET /api/order-entry/order-lookup before any write path.",
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            ingest_feedback(Path(output.output_dir), feedback_path)
            accepted_rules = json.loads(
                (Path(output.output_dir) / "knowledge" / "accepted_rules.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                accepted_rules["transition_hints"]["OrderEntry"],
                "Implement OrderEntryPage plus GET /api/order-entry/order-lookup before any write path.",
            )


if __name__ == "__main__":
    unittest.main()
