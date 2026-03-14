from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.analyzers.dfm import analyze_dfm_file
from legacy_delphi_project_analyzer.analyzers.pascal import analyze_pascal_file


class PascalAndDfmAnalyzerTests(unittest.TestCase):
    def test_pascal_analyzer_extracts_published_members_and_event_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pas_path = Path(tmpdir) / "AdvancedOrderEntry.pas"
            pas_path.write_text(
                """unit AdvancedOrderEntry;

interface

uses
  System.SysUtils, Vcl.Forms, Vcl.DBGrids;

type
  TfrmAdvancedOrderEntry = class(TForm)
  private
    FSelectedCustomerId: string;
  published
    btnSave: TButton;
    grdOrders: TDBGrid;
    property SelectedCustomerId: string read FSelectedCustomerId write FSelectedCustomerId;
    procedure grdOrdersDblClick(Sender: TObject);
  end;

implementation

{$R *.dfm}

procedure TfrmAdvancedOrderEntry.grdOrdersDblClick(Sender: TObject);
var
  SqlText: string;
begin
  SqlText := LoadSql('pricing.xml', 'OrderLookup');
  SqlText := StringReplace(SqlText, ':status', 'ACTIVE', [rfReplaceAll]);
end;

end.
""",
                encoding="utf-8",
            )
            summary, diagnostics = analyze_pascal_file(pas_path)
            self.assertEqual(diagnostics, [])
            self.assertIn("btnSave", summary.published_fields)
            self.assertIn("grdOrders", summary.component_fields)
            self.assertIn("SelectedCustomerId", summary.published_properties)
            self.assertIn("pricing.xml", summary.xml_references)
            self.assertIn("OrderLookup", summary.referenced_query_names)
            self.assertIn("status", summary.replace_tokens)
            self.assertTrue(
                any(name.endswith("grdOrdersDblClick") for name in summary.event_handlers)
            )
            method_flows = {
                item.method_name.split(".")[-1]: item
                for item in summary.method_flows
            }
            self.assertIn("grdOrdersDblClick", method_flows)
            self.assertIn("OrderLookup", method_flows["grdOrdersDblClick"].query_names)
            self.assertIn("status", method_flows["grdOrdersDblClick"].replace_tokens)

    def test_binary_dfm_heuristic_extracts_components_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dfm_path = Path(tmpdir) / "BinaryEntry.dfm"
            dfm_path.write_bytes(
                b"TPF0\x00"
                b"TfrmBinaryEntry\x00"
                b"frmBinaryEntry\x00"
                b"Caption\x00"
                b"Binary Entry\x00"
                b"TEdit\x00"
                b"edtCustomer\x00"
                b"DataField\x00"
                b"CUSTOMER_ID\x00"
                b"TButton\x00"
                b"btnSave\x00"
                b"Caption\x00"
                b"Save\x00"
                b"OnClick\x00"
                b"btnSaveClick\x00"
            )
            summary, diagnostics = analyze_dfm_file(dfm_path)
            self.assertTrue(summary.is_binary)
            self.assertEqual(summary.parse_mode, "binary-heuristic")
            self.assertEqual(summary.root_name, "frmBinaryEntry")
            self.assertEqual(summary.root_type, "TfrmBinaryEntry")
            self.assertIn("Binary Entry", summary.captions)
            self.assertIn("CUSTOMER_ID", summary.datasets)
            self.assertIn("frmBinaryEntry/btnSave.OnClick", summary.event_bindings)
            self.assertGreaterEqual(len(summary.components), 3)
            self.assertTrue(any(item.code == "DFM_BINARY_HEURISTIC" for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
