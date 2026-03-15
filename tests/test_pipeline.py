from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.pipeline import run_analysis


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class PipelineTests(unittest.TestCase):
    def test_pipeline_generates_llm_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
                max_artifact_chars=8000,
            )
            output_root = Path(output.output_dir)
            self.assertTrue((output_root / "inventory" / "project_inventory.json").exists())
            self.assertTrue((output_root / "llm-pack" / "project-summary.md").exists())
            self.assertTrue((output_root / "llm-pack" / "modules" / "orderentry.md").exists())
            self.assertTrue((output_root / "llm-pack" / "queries" / "orderlookup.md").exists())
            self.assertTrue((output_root / "llm-pack" / "flows" / "orderentry-flow.md").exists())
            self.assertTrue(
                (output_root / "llm-pack" / "transition-specs" / "orderentry-transition-spec.md").exists()
            )
            self.assertTrue(
                (output_root / "llm-pack" / "bff-sql" / "orderentry-orderlookup-bff-sql.md").exists()
            )
            self.assertTrue(
                (output_root / "llm-pack" / "ui-pseudo" / "orderentry-orderentrypage-pseudo-ui.md").exists()
            )
            self.assertTrue(
                (output_root / "llm-pack" / "ui-reference" / "orderentry-orderentrypage-reference-ui.html").exists()
            )
            self.assertTrue(
                (output_root / "llm-pack" / "ui-integration" / "orderentry-orderentrypage-ui-integration.md").exists()
            )
            self.assertTrue((output_root / "llm-pack" / "bundles" / "orderentry.json").exists())
            self.assertTrue((output_root / "llm-pack" / "bundles" / "orderentrybffsql.json").exists())
            self.assertTrue((output_root / "llm-pack" / "bundles" / "orderentryui.json").exists())
            self.assertTrue((output_root / "llm-pack" / "bundles" / "orderentryuiintegration.json").exists())
            self.assertTrue((output_root / "llm-pack" / "backend-sql-manifest.json").exists())
            self.assertTrue((output_root / "llm-pack" / "backend-sql-guide.md").exists())
            self.assertTrue((output_root / "llm-pack" / "ui-handoff-manifest.json").exists())
            self.assertTrue((output_root / "llm-pack" / "ui-handoff-guide.md").exists())
            self.assertTrue((output_root / "llm-pack" / "load-plan.json").exists())
            self.assertTrue((output_root / "llm-pack" / "boss-summary.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentrytransition.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentryspecvalidate.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentryorderlookupbffsql.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentryorderentrypagepseudoui.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentryorderentrypagereferenceui.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentryorderentrypageuiintegration.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderlookupclarify.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderlookupintent.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "closure-summary.md").exists())
            self.assertTrue(
                (output_root / "prompt-pack" / "repro-bundles" / "orderentrytransition.json").exists()
            )
            self.assertTrue((output_root / "prompt-pack" / "unknowns.md").exists())
            self.assertTrue(
                (output_root / "failure-cases" / "orderlookup-unresolved-placeholders.md").exists()
            )
            self.assertTrue(
                (output_root / "failure-cases" / "repro-bundles" / "orderlookup-unresolved-placeholders.json").exists()
            )
            self.assertTrue((output_root / "errors" / "prompt-recipes.md").exists())
            self.assertTrue((output_root / "knowledge" / "learned_patterns.json").exists())
            self.assertTrue((output_root / "knowledge" / "suggested_overrides.json").exists())
            self.assertTrue((output_root / "knowledge" / "knowledge-insights.md").exists())
            self.assertTrue((output_root / "report" / "index.html").exists())
            self.assertTrue((output_root / "intermediate" / "transition_specs.json").exists())
            self.assertGreaterEqual(len(output.transition_mapping.modules), 1)
            self.assertGreaterEqual(len(output.business_flows), 1)
            self.assertGreaterEqual(len(output.transition_specs), 1)
            self.assertGreaterEqual(len(output.bff_sql_artifacts), 1)
            self.assertGreaterEqual(len(output.ui_pseudo_artifacts), 1)
            self.assertGreaterEqual(len(output.ui_reference_artifacts), 1)
            self.assertGreaterEqual(len(output.ui_integration_artifacts), 1)
            self.assertGreaterEqual(len(output.prompt_packs), 3)
            self.assertGreaterEqual(len(output.failure_triage), 1)
            self.assertGreaterEqual(len(output.manifest), 5)
            self.assertIsNotNone(output.complexity_report)
            self.assertTrue(any(entry.estimated_tokens > 0 for entry in output.manifest))
            self.assertTrue(any(entry.kind == "prompt-pack" for entry in output.manifest))
            self.assertTrue(any(entry.kind == "failure-triage" for entry in output.manifest))
            module_text = (output_root / "llm-pack" / "modules" / "orderentry.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("OrderLookup", module_text)
            flow_text = (output_root / "llm-pack" / "flows" / "orderentry-flow.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("btnSearchClick", flow_text)
            spec_text = (
                output_root / "llm-pack" / "transition-specs" / "orderentry-transition-spec.md"
            ).read_text(encoding="utf-8")
            self.assertIn("GET /api/order-entry/order-lookup", spec_text)
            self.assertIn("customerId", spec_text)
            self.assertIn("OrderEntryOrderLookupRequest", spec_text)
            bff_text = (output_root / "llm-pack" / "bff-sql" / "orderentry-orderlookup-bff-sql.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Oracle 19c Notes", bff_text)
            self.assertIn("Repository interface", bff_text)
            ui_pseudo_text = (
                output_root / "llm-pack" / "ui-pseudo" / "orderentry-orderentrypage-pseudo-ui.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Layout Sections", ui_pseudo_text)
            self.assertIn("Interaction Steps", ui_pseudo_text)
            ui_reference_html = (
                output_root / "llm-pack" / "ui-reference" / "orderentry-orderentrypage-reference-ui.html"
            ).read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", ui_reference_html.lower())
            self.assertIn("Route /order-entry", ui_reference_html)
            ui_integration_text = (
                output_root / "llm-pack" / "ui-integration" / "orderentry-orderentrypage-ui-integration.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Target Placement", ui_integration_text)
            self.assertIn("src/features/order-entry", ui_integration_text)
            backend_guide_text = (
                output_root / "llm-pack" / "backend-sql-guide.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Backend SQL Compact Guide", backend_guide_text)
            self.assertIn("OrderEntry / OrderEntryOrderLookupEndpoint", backend_guide_text)
            ui_guide_text = (
                output_root / "llm-pack" / "ui-handoff-guide.md"
            ).read_text(encoding="utf-8")
            self.assertIn("UI Compact Guide", ui_guide_text)
            self.assertIn("OrderEntry / OrderEntryPage", ui_guide_text)
            prompt_text = (output_root / "prompt-pack" / "orderentrytransition.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("React + Spring Boot", prompt_text)
            self.assertIn("Verification Prompt", prompt_text)
            spec_prompt_text = (output_root / "prompt-pack" / "orderentryspecvalidate.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("validate_transition_spec", spec_prompt_text)
            prompt_json = (output_root / "prompt-pack" / "orderlookupclarify.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("infer_placeholder_meaning", prompt_json)
            bff_prompt_json = (
                output_root / "prompt-pack" / "orderentryorderlookupbffsql.json"
            ).read_text(encoding="utf-8")
            self.assertIn("generate_bff_oracle_sql_logic", bff_prompt_json)
            ui_prompt_json = (
                output_root / "prompt-pack" / "orderentryorderentrypageuiintegration.json"
            ).read_text(encoding="utf-8")
            self.assertIn("integrate_react_transition_ui", ui_prompt_json)
            transition_specs_json = (
                output_root / "intermediate" / "transition_specs.json"
            ).read_text(encoding="utf-8")
            self.assertIn("\"readiness_level\": \"needs-clarification\"", transition_specs_json)
            bff_artifacts_json = (
                output_root / "intermediate" / "bff_sql_artifacts.json"
            ).read_text(encoding="utf-8")
            self.assertIn("\"query_name\": \"OrderLookup\"", bff_artifacts_json)
            report_text = (output_root / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Complexity Dashboard", report_text)
            self.assertIn("Transition Specs", report_text)
            self.assertIn("Backend SQL Handoff", report_text)
            self.assertIn("UI Integration Handoff", report_text)

    def test_pipeline_handles_binary_dfm_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "binary_project"
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "BinaryEntry.pas").write_text(
                """unit BinaryEntry;

interface

uses
  System.SysUtils, Vcl.Forms;

type
  TfrmBinaryEntry = class(TForm)
  published
    btnSave: TButton;
    procedure btnSaveClick(Sender: TObject);
  end;

implementation

procedure TfrmBinaryEntry.btnSaveClick(Sender: TObject);
begin
end;

end.
""",
                encoding="utf-8",
            )
            (project_root / "BinaryEntry.dfm").write_bytes(
                b"TPF0\x00"
                b"TfrmBinaryEntry\x00"
                b"frmBinaryEntry\x00"
                b"Caption\x00"
                b"Binary Entry\x00"
                b"TButton\x00"
                b"btnSave\x00"
                b"Caption\x00"
                b"Save\x00"
                b"OnClick\x00"
                b"btnSaveClick\x00"
            )

            output = run_analysis(
                project_root=project_root,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
                max_artifact_chars=8000,
            )
            self.assertTrue(any(item.code == "DFM_BINARY_HEURISTIC" for item in output.diagnostics))
            self.assertEqual(len(output.forms), 1)
            self.assertEqual(output.forms[0].root_name, "frmBinaryEntry")
            module_text = (
                Path(output.output_dir) / "llm-pack" / "modules" / "binaryentry.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Binary DFM was parsed heuristically", module_text)


if __name__ == "__main__":
    unittest.main()
