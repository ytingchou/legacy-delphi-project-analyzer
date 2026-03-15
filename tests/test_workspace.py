from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.pipeline import run_analysis


class WorkspaceResolutionTests(unittest.TestCase):
    def test_dproj_relative_search_path_includes_external_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_root = root / "main_project"
            external_sql = root / "PDSS_SQL"
            project_root.mkdir(parents=True, exist_ok=True)
            external_sql.mkdir(parents=True, exist_ok=True)

            (project_root / "OrderEntry.pas").write_text(
                """unit OrderEntry;

interface

uses
  System.SysUtils, Vcl.Forms;

type
  TfrmOrderEntry = class(TForm)
  published
    procedure btnSearchClick(Sender: TObject);
  end;

implementation

procedure TfrmOrderEntry.btnSearchClick(Sender: TObject);
begin
end;

end.
""",
                encoding="utf-8",
            )
            (project_root / "OrderEntry.dfm").write_text(
                """object frmOrderEntry: TfrmOrderEntry
  Caption = 'Order Entry'
  object btnSearch: TButton
    OnClick = btnSearchClick
  end
end
""",
                encoding="utf-8",
            )
            (project_root / "order.xml").write_text(
                """<sql-mapping>
  <main-query name="OrderLookup">
    <ext-sql-refer-to xml="shared" sub-query="BaseWhere" />
    <sql-body><![CDATA[
SELECT * FROM orders
    ]]></sql-body>
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            (project_root / "main_project.dproj").write_text(
                """<Project>
  <PropertyGroup>
    <DCC_UnitSearchPath>..\\PDSS_SQL</DCC_UnitSearchPath>
  </PropertyGroup>
</Project>
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

            output = run_analysis(
                project_root=project_root,
                output_dir=root / "artifacts",
                phases=["all"],
            )

            self.assertIn(external_sql.resolve().as_posix(), output.inventory.external_roots)
            self.assertIn(external_sql.resolve().as_posix(), output.inventory.configured_search_paths)
            query = next(item for item in output.resolved_queries if item.name == "OrderLookup")
            self.assertIn("WHERE status = :status", query.expanded_sql)
            self.assertFalse(
                any(item.code == "PROJECT_SEARCH_PATH_MISSING" for item in output.diagnostics)
            )

    def test_workspace_config_path_variables_resolve_external_roots_and_surface_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_root = root / "main_project"
            common_root = root / "PDSS_Common"
            project_root.mkdir(parents=True, exist_ok=True)
            common_root.mkdir(parents=True, exist_ok=True)

            (project_root / "entry.pas").write_text(
                """unit entry;
interface
uses System.SysUtils;
implementation
end.
""",
                encoding="utf-8",
            )
            (common_root / "SharedTypes.pas").write_text(
                """unit SharedTypes;
interface
implementation
end.
""",
                encoding="utf-8",
            )
            workspace_config = project_root / "workspace.json"
            workspace_config.write_text(
                json.dumps(
                    {
                        "scan_roots": ["$(PDSS_COMMON)"],
                        "search_paths": ["$(PDSS_SQL)"],
                        "path_variables": {
                            "PDSS_COMMON": "../PDSS_Common",
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = run_analysis(
                project_root=project_root,
                output_dir=root / "artifacts",
                workspace_config_path=workspace_config,
                phases=["all"],
            )

            self.assertIn(common_root.resolve().as_posix(), output.inventory.external_roots)
            self.assertIn("$(PDSS_SQL)", output.inventory.unresolved_search_paths)
            self.assertTrue(
                any(item.code == "PROJECT_SEARCH_PATH_UNRESOLVED" for item in output.diagnostics)
            )
            prompt_pack = (Path(output.output_dir) / "prompt-pack" / "unknowns.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("PROJECT_SEARCH_PATH_UNRESOLVED", prompt_pack)
            workspace_pack = (
                Path(output.output_dir)
                / "prompt-pack"
                / "projectsearchpathunresolvedpdsssql.json"
            ).read_text(encoding="utf-8")
            self.assertIn("resolve_search_path", workspace_pack)


if __name__ == "__main__":
    unittest.main()
