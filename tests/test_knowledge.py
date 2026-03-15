from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.pipeline import run_analysis


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class KnowledgeStoreTests(unittest.TestCase):
    def test_invalid_overrides_emit_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir) / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            (rules_dir / "overrides.json").write_text(
                json.dumps(
                    {
                        "ignore_globs": [123],
                        "xml_aliases": {"pricing": 1},
                        "unknown_key": {"x": "y"},
                    }
                ),
                encoding="utf-8",
            )
            output = run_analysis(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                rules_dir=rules_dir,
                phases=["all"],
            )
            codes = {item.code for item in output.diagnostics}
            self.assertIn("KNOWLEDGE_OVERRIDE_INVALID_LIST", codes)
            self.assertIn("KNOWLEDGE_OVERRIDE_INVALID_MAPPING", codes)
            self.assertIn("KNOWLEDGE_OVERRIDE_UNKNOWN_KEY", codes)

    def test_missing_external_xml_generates_suggested_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            (project_root / "entry.pas").write_text(
                """unit entry;
interface
uses System.SysUtils;
implementation
end.
""",
                encoding="utf-8",
            )
            (project_root / "shared_pricing.xml").write_text(
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
            (project_root / "entry.xml").write_text(
                """<sql-mapping>
  <main-query name="UseTypo">
    <ext-sql-refer-to xml="prcing.xml" sub-query="BaseWhere" />
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            output = run_analysis(
                project_root=project_root,
                output_dir=Path(tmpdir) / "artifacts",
                phases=["all"],
            )
            suggested = json.loads(
                (Path(output.output_dir) / "knowledge" / "suggested_overrides.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("prcing.xml", suggested["xml_aliases"])
            self.assertEqual(suggested["xml_aliases"]["prcing.xml"], "shared_pricing.xml")


if __name__ == "__main__":
    unittest.main()
