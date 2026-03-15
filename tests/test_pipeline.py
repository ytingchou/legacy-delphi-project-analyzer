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
            self.assertTrue((output_root / "llm-pack" / "bundles" / "orderentry.json").exists())
            self.assertTrue((output_root / "llm-pack" / "load-plan.json").exists())
            self.assertTrue((output_root / "llm-pack" / "boss-summary.md").exists())
            self.assertTrue((output_root / "prompt-pack" / "orderentrytransition.md").exists())
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
            self.assertGreaterEqual(len(output.transition_mapping.modules), 1)
            self.assertGreaterEqual(len(output.business_flows), 1)
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
            prompt_text = (output_root / "prompt-pack" / "orderentrytransition.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("React + Spring Boot", prompt_text)
            self.assertIn("Verification Prompt", prompt_text)
            prompt_json = (output_root / "prompt-pack" / "orderlookupclarify.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("infer_placeholder_meaning", prompt_json)
            report_text = (output_root / "report" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Complexity Dashboard", report_text)

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
